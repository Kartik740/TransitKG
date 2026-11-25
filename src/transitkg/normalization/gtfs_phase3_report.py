"""Render Phase 3 reports from the verified build and executed checks."""

import os
from pathlib import Path
import tempfile

from normalize_gtfs import DEFAULT_POLICY, save_json


def canonical_contract(namespace):
    return f"""# TransitKG canonical identifier contract

Contract version **1.0.0**

## Entity keys

~~~text
<source_namespace>:<entity_type>:<percent-encoded source ID>
~~~

The namespace for this build is **{namespace}**. It identifies a source feed independently of its version, file layout and processing date. Supported entity types are agency, stop, route, trip, service, and shape. Use a different stable namespace for a separately issued source feed.

UTF-8 encode the exact source string and percent-escape every byte outside RFC 3986's unreserved ASCII set, with uppercase hexadecimal. Colons and literal percent signs in source IDs are escaped. Split on colons into exactly three components and decode the final component exactly once. Keep case, leading zeros, and original Unicode code points. A source value of %3A encodes as %253A.

| Entity | Source ID | Canonical ID |
|---|---|---|
| Agency | AC | {namespace}:agency:AC |
| Route | AC:72 | {namespace}:route:AC%3A72 |
| Stop | 55660 | {namespace}:stop:55660 |
| Stop | 52227 | {namespace}:stop:52227 |

Keep source identifiers as strings. Multiple agencies can provide scheduled service to the same regional stop. Keep agency and route scope in the association tables. Keep composite keys in separate typed columns, including (trip_id, stop_sequence) and (service_id, date).

## Provenance and lookup

The canonical_ids.parquet table records the original ID, canonical ID, source namespace, identity scope, source tables, snapshot ID, mapping method and static verification status. Shape points share their parent shape identity. A service may originate in a weekly calendar row, an added calendar exception, or both. Removal exceptions refer to an existing service.

The source_record column is the one-based parsed data-record ordinal in the source file, excluding its header. Combine it with the manifest's file path and snapshot checksum to recover source lexemes. The exception ledger gives the retained ordinal for exact duplicate rows.

Canonical identity, raw snapshot and normalized build each have a separate key. Canonical IDs are deterministic for the same feed, entity type and exact source spelling. Publishers may reuse IDs or change the ID assigned to an existing entity. Reconcile across versions with dated evidence before asserting real-world continuity.

Internal mappings establish the regional snapshot identity. An external crosswalk requires authoritative dated evidence scoped to both feeds. Stop-code candidates carry independent UNVERIFIED status. Evaluate selector combinations on the same scheduled service path, using a service date and start-time context for trip instances when required. [GTFS Schedule](https://gtfs.org/documentation/schedule/reference/) · [GTFS Realtime EntitySelector](https://gtfs.org/documentation/realtime/reference/#message-entityselector)
"""


def render(result, args):
    manifest, summary, integrity, rt = (result[k] for k in ("manifest", "summary", "integrity", "realtime"))
    totals, mapping, network = summary["totals"], integrity["canonical_mappings"], integrity["associations"]
    temporal, metrics = integrity["temporal"], summary["current_run"]
    times = temporal["stop_times"]
    report = [
        "# TransitKG Phase 3 data cleaning and normalization", "",
        "## Executive summary", "",
        f"Published **{totals['published_source_records']:,}** of **{totals['input_records']:,}** source rows as typed Parquet. "
        f"The exception ledger accounts for **{totals['deduplicated']}** exact copies and **{totals['quarantined']}** unresolved "
        f"rows. Post-cleaning checks passed. The normalized timetable retains **{times['records']:,} visits**.",
        "",
        "## Phase 2 findings and raw snapshot verification", "",
        f"Inspected and fingerprinted all **{len(manifest['phase2_report_sha256'])}** Phase 2 artifacts and reused the CSV reader, "
        "specification metadata, service-day parsers and reference checks. "
        f"All **{len(manifest['input_files'])}** source files matched their Phase 2 path, byte count and SHA-256. "
        "A second checksum pass preceded publication. The ancillary changelog remains in the raw snapshot.", "",
        "The build sequence was source and evidence verification, policy-driven projection, canonical identity and association "
        "generation, then validation of the staged Parquet and full-build repeat verification.", "",
        "| Phase 2 observation | Phase 3 decision |", "|---|---|",
        "| Core keys and relationships passed. | Keep core records and validate the exported Parquet against the same reference rules. |",
        "| Five medium findings affect attribution, labels, fares, route attributes and shape distances. | Collapse proven equivalents, isolate unresolved optional values, and retain the geometry. |",
        "| Prefix stripping creates 364 stop-ID collision groups. | Scope canonical identity to the regional feed and escape the exact ID. |",
        "| Multiple agencies serve shared stops. | Publish verified many-to-many service associations. |",
        "| AC/55660 resolves exactly while 52227 shares its name. | Preserve both stop identities and return an ambiguous name-only result. |",
        "| 100,912 arrivals exceed 24 hours and some optional times are absent. | Keep original time text and use nullable service-day seconds. |",
        "| Two source calendars end on 2099-12-31. Feed version and validity are blank. | Keep the source ranges and expose blank provider metadata as unknown. |",
        "",
        "## Cleaning policy and outcomes", "",
        "The typed projection preserves identifiers, display names, extension columns, original time text and explicit zeros. "
        "Dates become DATE. Documented numeric fields become nullable integers, doubles or exact decimals. Unquoted blanks "
        "become null. Quoted text empties stay empty. The source_empty_typed_fields list identifies quoted empties in converted "
        "fields. The original values remain recoverable from source record ordinals. [Cleaning policy](cleaning_policy.json)",
        "",
        "| Phase 2 issue | Source | Originally affected | Deduplicated | Quarantined | Flagged and kept |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in summary["medium_findings"]:
        report.append(f"| {row['phase2_issue']} | {row['source_file']} | {row['original_affected_records']} | "
                      f"{row['deduplicated']} | {row['quarantined']} | {row['flagged_retained']} |")
    report += [""]
    for finding in summary["medium_findings"]:
        report.append(f"- **{finding['phase2_issue']}**. {finding['evidence']} {finding['transformation']}")
    shape = summary["source_evidence"].get("shape_distance_analysis", {})
    fare = summary["source_evidence"].get("fare_transfer_analysis", {})
    report += ["",
        f"The shape check found {shape.get('equal_distances', 0)} equal distances across "
        f"{shape.get('affected_shapes', 0)} shapes. Of the flagged points, {shape.get('same_coordinates', 0)} "
        "share the previous populated-distance point's coordinates. The distance and coordinate values remain available.", "",
        f"The fare check found {fare.get('distinct_keys_with_filter', 0)} distinct extended keys and "
        f"{fare.get('referenced_filter_products_missing', 0)} populated filter references without a matching product. "
        "The Phase 2 type metadata leaves the field undocumented under base GTFS. The 511 specification confirms it as "
        "a provider extension. Confirm its fare applicability and precedence before using "
        "the quarantined rows. [GTFS fare-transfer keys](https://gtfs.org/documentation/schedule/reference/#fare_transfer_rulestxt) "
        "· [511 Data Exchange appendix E.3](https://511.org/media/6657/show)", "",
        "## Table-by-table accounting", "",
        "| Source file | Handling | Input | Accepted | Preserved extension | Deduplicated | Quarantined | Flagged | Retyped |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    fields = ("input_records", "accepted_normalized", "preserved_extension", "deduplicated",
              "quarantined", "flagged_retained", "records_with_values_retyped")
    for row in summary["source_tables"].values():
        report.append(f"| {row['source_file']} | {row['handling']} | " + " | ".join(f"{row[k]:,}" for k in fields) + " |")
    report += ["", f"Every source row has one disposition. The build retyped values on "
        f"**{totals['records_with_values_retyped']:,} rows**. It preserved the source spellings and marked retained warnings "
        "independently of source-row disposition. Derived records are counted separately. [Normalization summary](normalization_summary.json)",
        "",
        "## Canonical identity and scheduled associations", "",
        f"Verified **{mapping['records']:,} mappings**, including exact source-ID round trips and zero canonical collisions. "
        f"The **{mapping['stop_suffix_collision_groups_preserved']}** stop-ID suffix groups still contain distinct entities. "
        "[Canonical ID contract](canonical_identifier_contract.md) · [Mapping validation](mapping_validation.json)", "",
        "| Entity | Mappings |", "|---|---:|",
    ]
    for row in mapping["coverage"]:
        report.append(f"| {row['entity_type']} | {row['mappings']:,} |")
    report += ["",
        f"The exported tables retain **{network['route_stop_pairs']:,} route–stop pairs**, **{network['agency_stop_pairs']:,} "
        f"agency–stop pairs**, and **{network['multi_agency_stops']} multi-agency stops**. Scheduled visits establish service. "
        "Physical ownership requires independent evidence.", "",
        "## Realtime compatibility and AC Transit regression", "",
    ]
    ac = rt.get("ac_transit_55660", {})
    if ac:
        matched = ac["exact"]["candidates"][0]
        routes = ", ".join(route["route_id"] for route in matched["routes"])
        report += [
            f"AgencyId AC and StopId 55660 resolve together as **{ac['exact']['status']}** to "
            f"**{matched['canonical_stop_id']}**. **{matched['distinct_trips']}** distinct AC trips visit the stop on {routes}. "
            f"Stop 52227 has a separate canonical ID. The scoped name-only lookup is **{ac['name_only']['status']}**.", "",
        ]
    report += [
        "Use trip context, timetable, weekly service and exceptions to evaluate compound selectors. External operator IDs require "
        "dated authority. Stop-code aliases remain unverified. [Realtime compatibility](realtime_compatibility_report.json) "
        "· [GTFS Realtime EntitySelector](https://gtfs.org/documentation/realtime/reference/#message-entityselector)", "",
        "## Temporal integrity", "",
        f"Verified **{times['arrivals_above_24h']:,} arrivals** and **{times['departures_above_24h']:,} departures** beyond "
        f"24:00:00. The timetable retains **{times['missing_arrivals']:,} missing arrivals** and "
        f"**{times['missing_departures']:,} missing departures** with the original conditional semantics.",
        f"Retained all **{temporal['calendar_exception_rows']:,} calendar exceptions** and all agency timezones. "
        "Weekly rules and exceptions remain compact. The source inventory contains no frequencies.txt.", "",
    ]
    for row in temporal.get("calendar_end_2099", []):
        report.append(f"- Service {row['service_id']} has source bounds {row['start_date']} through {row['end_date']} "
                      f"and {row['trip_templates']} trip templates.")
    report += ["", "The 2099 date comes from source calendar rows. The regional publisher's version and validity remain unknown.", "",
        "## Quarantine, provenance and post-cleaning validation", "",
        "The ledger records duplicate lineage, optional representations awaiting review and retained shape warnings. "
        "Each entry names the snapshot, source file and record, rule, issue, explanation, and recoverable values.", "",
        f"- **Source snapshot** {manifest['source_snapshot_id']}",
        f"- **Normalized build** {manifest['normalized_build_id']}",
        f"- **Output directory** {os.path.relpath(result['path'], Path.cwd())}",
        f"- **Versions** policy {manifest['cleaning_policy_version']}, schema {manifest['normalized_schema_version']}, "
        f"canonical contract {manifest['canonical_contract_version']}.", "",
        "The snapshot hash uses a sorted path, byte-count and SHA-256 manifest. The build hash incorporates the snapshot, "
        "implementation, policy, namespace, contract versions and logical table fingerprints. Logical fingerprints combine "
        "schema and row count with two order-independent sums of per-row MD5 halves, wrapped in SHA-256. File SHA-256 checks "
        "attest to bytes. Logically equivalent Parquet files can use different physical encodings.", "",
        f"Re-ran **{len(integrity['foreign_keys'])}** relationship checks against reopened Parquet. "
        "Verified schema, required fields, keys, source projections, derived references, timetable order, ledger accounting "
        "and the Phase 2 baselines. [Data integrity report](data_integrity_report.json) · [Dataset manifest](dataset_manifest.json)", "",
        "## Performance and automated tests", "",
        f"Python {manifest['python']} and DuckDB {manifest['duckdb']} on {manifest['platform']}. "
        f"The run took **{metrics['wall_seconds']:.2f} s wall time** and **{metrics['cpu_seconds']:.2f} s process CPU time**. "
        f"Peak process RSS was **{metrics['peak_process_rss_mib']} MiB**. "
        f"Sampled scratch database and spill peaked at **{metrics['sampled_temporary_working_bytes']:,} bytes**. "
        f"Raw input was **{manifest['input_bytes']:,} bytes** and the Parquet output occupies **{manifest['parquet_bytes']:,} bytes**.", "",
        "Peak RSS is the Windows process peak working set or the OS process maximum on other supported systems. "
        "Scratch usage is sampled every 150 ms. Build timings run through validation and checksum verification. "
        "The repeat build and test suites run separately.", "",
        "| Stage | Seconds |", "|---|---:|",
    ]
    for stage, duration in metrics["stage_seconds"].items():
        report.append(f"| {stage.removesuffix('_seconds').replace('_', ' ')} | {duration:.3f} |")
    if summary.get("repeat_build", {}).get("logically_equivalent"):
        report += ["", "A second build reproduced the canonical mappings, logical fingerprints and build ID. "
                   "The existing published Parquet file checksums also passed."]
    report += [""]
    if summary.get("tests"):
        for phase, test in summary["tests"].items():
            report.append(f"- Phase {phase[-1]} suite ran **{test['tests']} tests** and **{'passed' if test['passed'] else 'failed'}**.")
    else:
        report.append("Use the reproduction command with --test to execute and record the test suites.")
    report += ["", "## Reproduction", "", "From the workspace root in PowerShell.", "", "~~~powershell",
               "python -m venv .venv",
               ".venv/Scripts/python.exe -m pip install -r scripts/requirements-phase2.txt",
               f'.venv/Scripts/python.exe scripts/normalize_gtfs.py "{args.source}" --phase2 "{args.phase2}" '
               f'--output "{args.output}" --reports "{args.reports}" --namespace {args.namespace} '
               f'--memory-limit {args.memory_limit} --threads {args.threads} --repeat --test',
               "~~~", "",
               "The publisher stages and validates output before renaming it into a build directory. A repeat with equivalent "
               "logical content and intact file checksums reuses the published artifact.", "",
               "## Unresolved decisions and recommendations for Phase 4", "",
               "Preserve snapshot-specific evidence for incremental loads. Benchmark graph construction using the compact "
               "associations and external timetable. Require dated operator evidence for external identifiers. Confirm fare "
               "filter precedence, the WH:53 direction label and affected shape distances before using them semantically. "
               "Apply compound selector predicates to the same scheduled service path.", ""]
    return "\n".join(report)


def write_reports(directory, result, args):
    directory.mkdir(parents=True, exist_ok=True)
    if directory / "cleaning_policy.json" != DEFAULT_POLICY.resolve():
        (directory / "cleaning_policy.json").write_text(DEFAULT_POLICY.read_text(encoding="utf-8"), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix=".report-", dir=directory) as temporary:
        staging = Path(temporary)
        files = {"dataset_manifest.json": result["manifest"], "normalization_summary.json": result["summary"],
                 "data_integrity_report.json": result["integrity"],
                 "mapping_validation.json": result["integrity"]["canonical_mappings"],
                 "realtime_compatibility_report.json": result["realtime"]}
        for name, content in files.items():
            save_json(staging / name, content)
        (staging / "canonical_identifier_contract.md").write_text(
            canonical_contract(result["manifest"]["source_namespace"]), encoding="utf-8")
        (staging / "phase3_data_cleaning_report.md").write_text(render(result, args), encoding="utf-8")
        for path in sorted(staging.iterdir(), key=lambda p: p.name == "phase3_data_cleaning_report.md"):
            path.replace(directory / path.name)
