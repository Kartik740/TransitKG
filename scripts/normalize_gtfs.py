"""Build and verify a source-traceable Phase 3 Parquet snapshot."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time

import duckdb

from gtfs_checks import literal, q, query, service_domain, value
from gtfs_cleaning import PHASE2_ISSUES, project, source_exceptions
from gtfs_normalized import (
    AGENCY_STOP_QUERY, CONTRACT_VERSION, SCHEMA_VERSION, aliases_query, compatible_views, digest,
    identity_domain, mappings_query, realtime_report, route_stop_query, trip_context_query, validate,
)
from profile_gtfs import HERE, SCHEMA, files_under, inventory_bytes, load_table, peak_rss_mib, sha256

DEFAULT_POLICY = HERE.parent / "reports" / "phase3" / "cleaning_policy.json"
NAMESPACE = "511-sf-bay-regional"
REQUIRED_TABLES = {"agency", "routes", "stops", "trips", "stop_times"}
IMPLEMENTATION = ("normalize_gtfs.py", "gtfs_cleaning.py", "gtfs_normalized.py", "gtfs_checks.py",
                  "gtfs_network.py", "profile_gtfs.py", "gtfs_schedule_fields.json")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def phase2_evidence(directory):
    if directory is None:
        return {}
    report_files = sorted(path for path in directory.iterdir() if path.is_file())
    reports = {path.stem: read_json(path) for path in report_files if path.suffix == ".json"}
    return {"inventory": reports["dataset_inventory"], "quality": reports["data_quality_report"],
            "network": reports["temporal_network_graph"], "identifiers": reports["identifier_analysis"],
            "realtime": reports["realtime_compatibility_report"], "references": reports["referential_integrity_report"],
            "profiles": reports["data_profile"], "artifacts": {path.name: sha256(path) for path in report_files}}


def source_manifest(source, evidence):
    paths = files_under(source)
    inventory = [{"path": name, **inventory_bytes(path)} for name, path in sorted(paths.items())]
    if evidence:
        expected = {item["path"]: (item["bytes"], item["sha256"]) for item in evidence["inventory"]["inventory"]}
        actual = {item["path"]: (item["bytes"], item["sha256"]) for item in inventory}
        differences = sorted(name for name in actual.keys() | expected.keys() if actual.get(name) != expected.get(name))
        if differences:
            raise ValueError(f"Source snapshot differs from the supplied profiling inventory: {differences}")
    projection = [{"path": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]} for item in inventory]
    aggregate = digest(projection)
    return inventory, aggregate


def load_sources(db, source, inventory, evidence):
    tables = {}
    expected = {i["path"]: i for i in evidence.get("inventory", {}).get("inventory", [])}
    for index, item in enumerate(inventory):
        path = PurePosixPath(item["path"])
        if path.suffix.lower() not in {".txt", ".csv", ".tsv"} and path.name != "locations.geojson":
            item.update(disposition="ancillary retained in raw snapshot", records=None)
            continue
        name = path.stem
        if name in tables or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
            raise ValueError(f"Ambiguous or unsupported table name: {item['path']}")
        entry = dict(item, sql_table=f"raw_{index}")
        columns = load_table(db, path.name, source / item["path"], entry)
        if not columns or entry.get("malformed"):
            raise ValueError(f"Unpublishable source file {item['path']}: {entry.get('rejected_examples', entry['status'])}")
        baseline = expected.get(item["path"])
        if baseline and (entry["records"] != baseline["records"] or columns != baseline["columns"]):
            raise ValueError(f"Parsed schema or count differs from Phase 2: {item['path']}")
        tables[name] = {**entry, "columns": dict.fromkeys(columns)}
        item.update(disposition="tabular with per-record accounting", records=entry["records"], columns=columns)
    missing = REQUIRED_TABLES - tables.keys()
    if missing or not {"calendar", "calendar_dates"} & tables.keys():
        raise ValueError(f"Core schedule tables or service calendar absent: {sorted(missing)}")
    return tables


def rows_fingerprint(db, selection):
    """Order-independent logical fingerprint of a typed row multiset."""
    count, low, high = db.execute(f"""WITH serialized AS (
        SELECT to_json(r)::VARCHAR AS payload FROM ({selection}) r)
        SELECT count(*), coalesce(sum(md5_number_lower(payload)::HUGEINT),0),
               coalesce(sum(md5_number_upper(payload)::HUGEINT),0) FROM serialized""").fetchone()
    return count, str(low), str(high)


def parquet(db, stage, name, selection, artifacts, purpose):
    path = stage / f"{name}.parquet"
    db.execute(f"COPY ({selection}) TO {literal(str(path))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    db.execute(f"CREATE TEMP VIEW {q(name)} AS SELECT * FROM read_parquet({literal(str(path))})")
    select = f"SELECT * FROM {q(name)}"
    schema = {row[0]: row[1] for row in db.execute(f"DESCRIBE {select}").fetchall()}
    counts = rows_fingerprint(db, select)
    artifacts[name] = {"file": path.name, "role": purpose, "records": counts[0], "schema": schema,
                       "bytes": path.stat().st_size, "sha256": sha256(path),
                       "logical_sha256": digest({"schema": list(schema.items()), "row_multiset": counts})}
    return schema


def source_checks(db, name, projection, artifact, accounting):
    expected = rows_fingerprint(db, projection)
    schema = list(artifact["schema"].items())
    expected_digest = digest({"schema": schema, "row_multiset": expected})
    count = accounting["accepted_normalized"] + accounting["preserved_extension"]
    return {"table": name, "matches": expected_digest == artifact["logical_sha256"] and artifact["records"] == count,
            "verified_columns": list(artifact["schema"]), "expected_records": count,
            "reloaded_records": artifact["records"], "source_projection_logical_sha256": expected_digest}


def exception_accounting(db, tables, artifacts, snapshot):
    if "row_exception_ledger" not in artifacts:
        return {"rows": 0, "errors": 0}
    errors = value(db, f"""SELECT count(*) FROM row_exception_ledger WHERE
        source_snapshot_id IS DISTINCT FROM {literal(snapshot)} OR source_row IS NULL
        OR (disposition = 'DEDUPLICATED') <> (retained_source_record IS NOT NULL)""")
    errors += value(db, """SELECT count(*) FROM (SELECT source_file, source_record FROM row_exception_ledger
        GROUP BY ALL HAVING count(*) > 1)""")
    for name, table in tables.items():
        errors += value(db, f"""SELECT count(*) FROM row_exception_ledger e WHERE source_file={literal(table['path'])}
            AND (source_record NOT BETWEEN 1 AND {table['records']}
               OR (disposition = 'FLAGGED') <> EXISTS (
                    SELECT 1 FROM {q(name)} p WHERE p.source_record=e.source_record)
               OR (retained_source_record IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM {q(name)} p WHERE p.source_record=e.retained_source_record)))""")
    return {"rows": artifacts["row_exception_ledger"]["records"], "errors": errors}


def disk_monitor(scratch, stage, stop, peaks):
    while True:
        for name, root in (("scratch", scratch), ("staged_parquet", stage)):
            size = 0
            for path in root.iterdir():
                if path.is_dir() and name == "scratch":
                    children = path.glob("*")
                else:
                    children = (path,)
                for child in children:
                    try:
                        size += child.stat().st_size if child.is_file() else 0
                    except FileNotFoundError:
                        continue
            peaks[name] = max(peaks.get(name, 0), size)
        if stop.wait(0.15):
            break


def compare_baseline(integrity, accounting, realtime, evidence, observations):
    mismatches = []
    if evidence:
        network = evidence["network"]["network"]
        for key in ("route_stop_pairs", "agency_stop_pairs", "multi_agency_stops"):
            expected = network[key]["count"] if key == "multi_agency_stops" else network[key]
            if integrity["associations"][key] != expected:
                mismatches.append(key)
        profiled_times = evidence["network"]["temporal"]["stop_times"]
        for key in ("arrivals_above_24h", "departures_above_24h", "missing_arrivals", "missing_departures",
                    "records", "max_arrival_seconds", "max_departure_seconds"):
            if integrity["temporal"]["stop_times"][key] != profiled_times[key]:
                mismatches.append(key)
        expected_issues = {i["issue_id"]: i["affected_records"] for i in evidence["quality"]["issues"]}
        verified = {"P2-011": accounting["attributions"]["deduplicated"], "P2-012": accounting["directions"]["quarantined"],
                    "P2-013": observations["fare_transfer_analysis"]["conflicting_rows"],
                    "P2-014": observations["route_attribute_analysis"]["affected_records"],
                    "P2-016": accounting["shapes"]["flagged_retained"]}
        mismatches.extend(issue for issue, count in verified.items() if count != expected_issues[issue])
        if not realtime["ac_transit_55660"]["passed"]:
            mismatches.append("AC/55660 compatibility changed")
        if integrity["canonical_mappings"]["stop_suffix_collision_groups_preserved"] != evidence["identifiers"]["domains"]["stop_id"]["suffix_collisions"]:
            mismatches.append("stop prefix collision baseline")
    return mismatches


def publish(stage, output, manifest):
    # A short directory component keeps Windows temporary paths within legacy path limits.
    # The complete identity is retained and compared in the manifest.
    published = output / ("build-" + manifest["normalized_build_id"].removeprefix("normalized-sha256-")[:24])
    if published.exists():
        previous = read_json(published / "manifest.json")
        if previous["normalized_build_id"] != manifest["normalized_build_id"]:
            raise ValueError("Published directory prefix collides with a different build.")
        expected = manifest["artifacts"]
        existing = previous["artifacts"]
        if set(expected) != set(existing) or any(expected[name]["logical_sha256"] != a["logical_sha256"]
                                                 for name, a in existing.items()):
            raise ValueError("Existing build has different logical content.")
        for name, artifact in existing.items():
            if artifact["file"] != f"{name}.parquet" or sha256(published / artifact["file"]) != artifact["sha256"]:
                raise ValueError(f"Existing build file failed byte checksum verification: {name}")
        return published, True
    save_json(stage / "manifest.json", manifest)
    stage.rename(published)
    return published, False


def build(source, output, phase2=None, namespace=NAMESPACE, policy_path=DEFAULT_POLICY, memory_limit="768MB", threads=2):
    started, cpu = time.perf_counter(), time.process_time()
    source, output = source.resolve(), output.resolve()
    if not source.is_dir() or output == source or source in output.parents:
        raise ValueError("Choose an extracted source directory and an output outside the source.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", namespace):
        raise ValueError("Use a stable, lowercase feed namespace consisting of letters, digits and hyphens.")
    evidence = phase2_evidence(phase2)
    policy = read_json(policy_path)
    inventory, aggregate = source_manifest(source, evidence)
    snapshot = "gtfs-sha256-" + aggregate
    stages = {"source_verification_seconds": round(time.perf_counter() - started, 3)}
    code = {name: sha256(HERE / name) for name in IMPLEMENTATION}
    policy_checksum = sha256(policy_path)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".phase3-", dir=output) as temporary:
        work, stage = Path(temporary) / "work", Path(temporary) / "build"
        work.mkdir()
        stage.mkdir()
        stop = Event()
        peaks = {}
        monitor = Thread(target=disk_monitor, args=(work, stage, stop, peaks), daemon=True)
        monitor.start()
        try:
            with closing(duckdb.connect(str(work / "normalization.duckdb"), config={
                "threads": threads, "memory_limit": memory_limit, "temp_directory": str(work / "spill"),
                "preserve_insertion_order": "true"
            })) as db:
                stage_started = time.perf_counter()
                tables = load_sources(db, source, inventory, evidence)
                provider_feed = query(db, f"SELECT * FROM {q(tables['feed_info']['sql_table'])}") if "feed_info" in tables else []
                stages["source_load_seconds"] = round(time.perf_counter() - stage_started, 3)
                print("Loaded verified source tables. Applying the cleaning policy.", flush=True)
                stage_started = time.perf_counter()
                observations = source_exceptions(db, tables)
                artifacts, accounting, schemas, projections = {}, {}, {}, {}
                schema_checks, source_integrity = [], []
                for name, table in tables.items():
                    selection, expected_schema, accounting[name] = project(db, table)
                    projections[name] = selection
                    schemas[name] = parquet(db, stage, name, selection, artifacts, "source table")
                    schema_checks.append({"table": name, "matches": schemas[name] == expected_schema})
                if value(db, "SELECT count(*) FROM row_exceptions"):
                    parquet(db, stage, "row_exception_ledger", f"""SELECT {literal(snapshot)} AS source_snapshot_id, *
                        FROM row_exceptions""", artifacts, "quarantine, duplicate lineage and retained warnings")
                stages["normalization_seconds"] = round(time.perf_counter() - stage_started, 3)
                print("Exported normalized tables. Generating canonical IDs and scoped associations.", flush=True)
                stage_started = time.perf_counter()
                compatible = compatible_views(db, tables, schemas)
                service_domain(db, compatible)
                identity_domain(db, tables)
                parquet(db, stage, "canonical_ids", mappings_query(namespace, snapshot), artifacts, "source-to-canonical mappings")
                parquet(db, stage, "route_stops", route_stop_query(namespace), artifacts, "verified scheduled route-stop service")
                parquet(db, stage, "agency_stops", AGENCY_STOP_QUERY, artifacts, "verified scheduled agency-stop service")
                parquet(db, stage, "trip_selector_context", trip_context_query(tables, namespace), artifacts, "trip selector context")
                if "stop_code" in tables["stops"]["columns"]:
                    parquet(db, stage, "stop_alias_candidates", aliases_query(namespace), artifacts, "unverified external aliases")
                stages["mapping_and_associations_seconds"] = round(time.perf_counter() - stage_started, 3)
                print("Validating exported Parquet, Phase 2 baselines, and the AC selector.", flush=True)
                stage_started = time.perf_counter()
                for name in tables:
                    source_integrity.append(source_checks(db, name, projections[name], artifacts[name], accounting[name]))
                integrity = validate(db, tables, compatible, accounting, namespace, snapshot, evidence.get("references", []),
                                     schema_checks, source_integrity)
                realtime = realtime_report(db, evidence.get("realtime"))
                ledger = exception_accounting(db, tables, artifacts, snapshot)
                mismatches = compare_baseline(integrity, accounting, realtime, evidence, observations)
                if not integrity["passed"] or ledger["errors"] or mismatches:
                    failed = [x for x in integrity["keys"] if x["duplicate_key_groups"] or x["missing_required_records"]]
                    raise ValueError(f"Normalized validation failed. Keys: {failed}; join errors: "
                                     f"{integrity['reference_regressions']}; baseline: {mismatches}; "
                                     f"temporal: {integrity['temporal']['validation_errors']}; ledger: {ledger}; "
                                     f"structure: {[x for x in schema_checks + source_integrity if not x['matches']]}; "
                                     f"conditional: {[x for x in integrity['conditional_checks'] if x['affected_records']]}")
                integrity["phase2_baseline_mismatches"] = mismatches
                integrity["exception_ledger"] = ledger
                stages["validation_seconds"] = round(time.perf_counter() - stage_started, 3)
            stage_started = time.perf_counter()
            after = {name: sha256(path) for name, path in files_under(source).items()}
            if after != {item["path"]: item["sha256"] for item in inventory}:
                raise ValueError("Raw source changed during the build. Nothing was published.")
            stages["source_final_verification_seconds"] = round(time.perf_counter() - stage_started, 3)
        finally:
            stop.set()
            monitor.join()
        content = {name: item["logical_sha256"] for name, item in artifacts.items()}
        build_id = "normalized-sha256-" + digest({"snapshot": snapshot, "namespace": namespace,
            "schema_version": SCHEMA_VERSION, "canonical_contract_version": CONTRACT_VERSION,
            "policy_sha256": policy_checksum, "implementation": code, "logical_artifacts": content})
        published_at = datetime.now(timezone.utc).isoformat()
        metadata = {"source_snapshot_id": snapshot, "normalized_build_id": build_id, "source_namespace": namespace,
                    "aggregate_source_sha256": aggregate, "input_bytes": sum(i["bytes"] for i in inventory),
                    "input_files": inventory, "provider_feed_info": provider_feed, "acquired_at": None,
                    "processed_at_utc": published_at, "raw_checksums_unchanged": True,
                    "cleaning_policy_version": policy["version"], "cleaning_policy_sha256": policy_checksum,
                    "normalized_schema_version": SCHEMA_VERSION, "canonical_contract_version": CONTRACT_VERSION,
                    "implementation_sha256": digest(code), "implementation_files": code,
                    "phase2_report_sha256": evidence.get("artifacts", {}), "specification": policy["specification_basis"],
                    "python": platform.python_version(), "duckdb": duckdb.__version__, "platform": platform.platform(),
                    "artifacts": artifacts, "parquet_bytes": sum(a["bytes"] for a in artifacts.values())}
        execution = {"wall_seconds": round(time.perf_counter() - started, 3),
                     "cpu_seconds": round(time.process_time() - cpu, 3), "peak_process_rss_mib": peak_rss_mib(),
                     "sampled_temporary_working_bytes": peaks.get("scratch", 0),
                     "sampled_staging_parquet_bytes": peaks.get("staged_parquet", 0),
                     "stage_seconds": stages, "duckdb_threads": threads, "duckdb_memory_limit": memory_limit}
        metadata["execution"] = execution
        published, reused = publish(stage, output, metadata)
    if reused:
        metadata = read_json(published / "manifest.json")
    issue_index = {i["issue_id"]: i for i in evidence.get("quality", {}).get("issues", [])}
    findings = []
    for rule in policy["rules"]:
        if rule["id"] not in PHASE2_ISSUES:
            continue
        name = PurePosixPath(rule["table"]).stem
        if name in accounting:
            finding = {**rule, **accounting[name], "original_affected_records":
                       issue_index.get(PHASE2_ISSUES[rule["id"]], {}).get("affected_records")}
            findings.append(finding)
    totals = {column: sum(row[column] for row in accounting.values()) for column in
              ("input_records", "accepted_normalized", "preserved_extension", "quarantined",
               "deduplicated", "flagged_retained", "records_with_values_retyped", "source_values_rewritten")}
    totals["published_source_records"] = totals["accepted_normalized"] + totals["preserved_extension"]
    totals["unaccounted"] = totals["input_records"] - totals["published_source_records"] - totals["quarantined"] - totals["deduplicated"]
    if totals["unaccounted"]:
        raise AssertionError("Source record accounting does not reconcile.")
    summary = {"totals": totals, "source_tables": accounting, "medium_findings": findings,
               "source_evidence": observations, "derived_records": {name: a["records"] for name, a in artifacts.items()
                   if name not in tables and name != "row_exception_ledger"},
               "normalized_build_id": build_id, "source_snapshot_id": snapshot, "reused_existing_build": reused,
               "current_run": execution}
    return {"path": published, "manifest": metadata, "summary": summary, "integrity": integrity, "realtime": realtime}


def run_test_suites(published):
    tests = {}
    env = {**os.environ, "TRANSITKG_PHASE3_BUILD": str(published)}
    for phase, pattern in (("phase2", "test_profile_gtfs.py"), ("phase3", "test_normalize_gtfs.py")):
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(HERE), "-p", pattern, "-v"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, check=False)
        log = result.stdout + result.stderr
        print(log, end="", flush=True)
        count = re.search(r"Ran (\d+) tests?", log)
        tests[phase] = {"passed": result.returncode == 0 and count is not None and int(count[1]) > 0,
                        "tests": int(count[1]) if count else 0, "output": log}
    return tests


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("--phase2", type=Path, default=Path("reports/phase2"))
    cli.add_argument("--output", type=Path, default=Path("data/normalized"))
    cli.add_argument("--reports", type=Path, default=Path("reports/phase3"))
    cli.add_argument("--namespace", default=NAMESPACE)
    cli.add_argument("--memory-limit", default="768MB")
    cli.add_argument("--threads", type=int, default=2)
    cli.add_argument("--repeat", action="store_true", help="Run a second full build and verify logical equivalence.")
    cli.add_argument("--test", action="store_true", help="Run Phase 2 and Phase 3 test suites, including the dataset integration case.")
    args = cli.parse_args()
    reports = args.reports.resolve()
    if reports == args.source.resolve() or args.source.resolve() in reports.parents or reports == args.phase2.resolve():
        cli.error("Reports must be outside the source and distinct from the Phase 2 directory.")
    options = {"phase2": args.phase2, "namespace": args.namespace, "memory_limit": args.memory_limit, "threads": args.threads}
    results = build(args.source, args.output, **options)
    if args.repeat:
        repeat = build(args.source, args.output, **options)
        if repeat["path"] != results["path"] or not repeat["summary"]["reused_existing_build"]:
            raise ValueError("Repeated build did not reproduce the verified logical content.")
        results["summary"]["repeat_build"] = {"logically_equivalent": True, "execution": repeat["summary"]["current_run"]}
    tests = run_test_suites(results["path"]) if args.test else {}
    results["summary"]["tests"] = tests
    from gtfs_phase3_report import write_reports

    write_reports(reports, results, args)
    print(f"Build verified at {results['path']}", flush=True)
    return int(any(not result["passed"] for result in tests.values()))


if __name__ == "__main__":
    raise SystemExit(main())
