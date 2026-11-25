"""Publish the approved regional Phase 5 projection, entirely offline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import tempfile
from threading import Event, Thread
from time import perf_counter, process_time

import duckdb

from design_static_graph import ROOT, SCHEMA, read, write, sha, verify_inputs, scalar
from profile_gtfs import peak_rss_mib
from validate_graph_ready import validate, validate_regional

VERSION = "1.0.0"
FORMAT = "transitkg-static-v1"
PHASE4 = ROOT / "reports/phase4"
CONTRACT = PHASE4 / "phase5_transformation_contract.md"
SCHEMA_FILE = SCHEMA / "static_graph_schema.json"
BUILD = ROOT / "data/normalized/build-5608c0d7d93b9c6200431b5c"
TYPES = {"STRING": "VARCHAR", "INTEGER": "BIGINT"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def definitions(schema):
    for kind, key, items in (("nodes", "label", schema["nodes"]),
                             ("relationships", "type", schema["relationships"])):
        for item in items:
            name = item[key]
            yield kind, name, item


def columns(kind, item):
    fields = {"start_id": "VARCHAR", "end_id": "VARCHAR"} if kind == "relationships" else {}
    fields.update({p: TYPES[spec["type"]] for p, spec in item["properties"].items()})
    return fields


def key_columns(kind, item):
    if kind == "nodes":
        return [item["identity"]]
    return ["start_id", "end_id", *item["identity"][3:]]


def pin_inputs(build, raw):
    manifest = verify_inputs(build, raw)
    schema = read(SCHEMA_FILE)
    previous = read(PHASE4 / "schema_validation_report.json")
    baseline = read(PHASE4 / "architecture_comparison.json")
    for evidence in (previous, baseline):
        for key in ("source_snapshot_id", "normalized_build_id"):
            if evidence[key] != manifest[key]:
                raise ValueError(f"Phase 4 {key} differs from normalized input")
    pins = previous["verified_schema_files"]
    for name, digest in pins.items():
        if sha(SCHEMA / name) != digest:
            raise ValueError(f"Approved schema artifact digest mismatch: {name}")
    if (schema["version"] != "1.0.0" or schema["canonical_contract_version"] != manifest["canonical_contract_version"]
            or schema["namespace"] != manifest["source_namespace"]):
        raise ValueError("Schema and canonical contract incompatibility")
    canonical = ROOT / "reports/phase3/canonical_identifier_contract.md"
    if f'Contract version **{manifest["canonical_contract_version"]}**' not in canonical.read_text(encoding="utf-8"):
        raise ValueError("Canonical contract document version mismatch")
    if "**Proposed version 1.0.0.**" not in CONTRACT.read_text(encoding="utf-8"):
        raise ValueError("Unsupported transformation contract version")
    return manifest, schema, baseline


def open_normalized(db, build, manifest):
    for table, artifact in manifest["artifacts"].items():
        relation = db.read_parquet(str(build / artifact["file"]))
        if dict(zip(relation.columns, map(str, relation.types))) != artifact["schema"]:
            raise ValueError(f"Normalized schema differs from its manifest: {table}")
        relation.create_view(table)
    db.execute((SCHEMA / "timetable.sql").read_text(encoding="utf-8"))


def projection(kind, item):
    source = item["source"]
    fields = []
    if kind == "nodes":
        entity = item["entity_type"]
        base = "canonical_ids AS ids"
        if source != "canonical_ids":
            base += f" JOIN {source} AS src ON ids.source_identifier=src.{entity}_id"
        where = f"ids.entity_type='{entity}'"
    else:
        first, last = item["start_column"], item["end_column"]
        base = f"""{source} AS src
           LEFT JOIN canonical_ids AS a ON a.entity_type='{item["from"].lower()}' AND a.source_identifier=src.{first}
           LEFT JOIN canonical_ids AS b ON b.entity_type='{item["to"].lower()}' AND b.source_identifier=src.{last}"""
        fields.extend(["a.canonical_id AS start_id", "b.canonical_id AS end_id"])
        where = f"src.{last} IS NOT NULL" if item["type"] == "PART_OF" else "TRUE"
    for field, spec in item["properties"].items():
        table, col = spec["source"].split(".")
        alias = "ids" if table == "canonical_ids" else "src"
        fields.append(f"CAST({alias}.{col} AS {TYPES[spec['type']]}) AS {field}")
    return f"SELECT {', '.join(fields)} FROM {base} WHERE {where} ORDER BY {', '.join(key_columns(kind, item))}"


def snapshot_projection(metadata):
    return "SELECT " + ", ".join(f"CAST(? AS VARCHAR) AS {key}" for key in metadata)


class TemporaryUsage:
    """Sample dedicated spill storage while the build connection is open."""
    def __init__(self, spill):
        self.spill = spill
        self.peak = 0
        self.done = Event()
        self.worker = Thread(target=self.sample, daemon=True)

    def sample(self):
        while not self.done.is_set():
            size = 0
            for path in self.spill.glob("**/*"):
                try:
                    if path.is_file():
                        size += path.stat().st_size
                except FileNotFoundError:
                    continue
            self.peak = max(self.peak,size)
            self.done.wait(0.025)

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, *_):
        self.done.set()
        self.worker.join()


def fingerprint(db, schema):
    """Hash sorted serialized UTF-8 records, fetching one bounded batch at a time."""
    whole = hashlib.sha256((FORMAT + "\n").encode("ascii"))
    per_artifact = {}
    for kind, name, item in sorted(definitions(schema), key=lambda entry: (entry[0], entry[1])):
        fields = list(columns(kind,item))
        cursor = db.execute(f"SELECT {', '.join(fields)} FROM graph_{name}")
        records = []
        while batch := cursor.fetchmany(4096):
            for values in batch:
                row = dict(zip(fields, values))
                if kind == "nodes":
                    identity = row[item["identity"]]
                    if name == "FeedSnapshot":
                        del row["static_fingerprint"]
                    record = ["N", name, identity, row]
                else:
                    endpoints = [row.pop("start_id"), row.pop("end_id")]
                    record = ["R", name, [name, *endpoints, *(row[p] for p in item["identity"][3:])], row]
                records.append(encode(record))
        artifact = hashlib.sha256((FORMAT + "\n").encode("ascii"))
        # All records begin with tag and ASCII type name, so sorted partitions concatenate in global order.
        for line in sorted(records):
            whole.update(line + b"\n")
            artifact.update(line + b"\n")
        per_artifact[name] = artifact.hexdigest()
    return whole.hexdigest(), per_artifact


def reopen(db, stage, schema):
    expected = {f"{kind}/{name}.parquet" for kind,name,_ in definitions(schema)}
    actual = {p.relative_to(stage).as_posix() for p in stage.rglob("*.parquet") if "spill" not in p.relative_to(stage).parts}
    if actual != expected:
        raise ValueError(f"Graph-ready output inventory mismatch: {actual ^ expected}")
    for kind,name,_ in definitions(schema):
        db.read_parquet(str(stage / kind / (name+".parquet"))).create_view("graph_"+name, replace=True)


def publish(stage, destination, manifest):
    if not destination.exists():
        try:
            stage.rename(destination)
            return False
        except OSError:
            if not destination.is_dir():
                raise
    existing = read(destination / "manifest.json")
    stable_fields = {"graph_ready_build_id", "expected_static_graph_fingerprint", "inputs",
                     "external", "counts", "implementation", "validation", "artifact_bytes"}
    # Physical encoding may differ for the same logical content.
    stable_fields.remove("artifact_bytes")
    if any(existing[key] != manifest[key] for key in stable_fields):
        raise ValueError("Existing graph-ready publication conflicts with staged build")
    previous = existing["artifacts"]
    if set(previous) != set(manifest["artifacts"]):
        raise ValueError("Existing graph-ready manifest inventory mismatch")
    if {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()} != (
            {"manifest.json"} | set(previous)):
        raise ValueError("Existing graph-ready file inventory mismatch")
    for path, info in previous.items():
        if (sha(destination / path) != info["sha256"]
                or info["logical_sha256"] != manifest["artifacts"][path]["logical_sha256"]
                or any(info[k] != manifest["artifacts"][path][k] for k in ("records","schema","lineage"))):
            raise ValueError(f"Existing graph-ready artifact failed verification: {path}")
    return True


def run(build, raw, output):
    start, cpu = perf_counter(), process_time()
    build, raw, output = (p.resolve() for p in (build, raw, output))
    if any(output == p or p in output.parents for p in (build,raw)):
        raise ValueError("Output must be outside the immutable inputs")
    normalized, schema, baseline = pin_inputs(build,raw)
    inputs = {
        "source_snapshot_id": normalized["source_snapshot_id"],
        "normalized_build_id": normalized["normalized_build_id"],
        "normalized_manifest_sha256": sha(build/"manifest.json"),
        "schema_version": schema["version"], "schema_sha256": sha(SCHEMA_FILE),
        "canonical_contract_version": normalized["canonical_contract_version"],
        "canonical_contract_sha256": sha(ROOT/"reports/phase3/canonical_identifier_contract.md"),
        "transformation_contract_version": "1.0.0", "transformation_contract_sha256": sha(CONTRACT),
        "reference_sha256": sha(PHASE4/"architecture_comparison.json"),
        "timetable_sql_sha256": sha(SCHEMA/"timetable.sql"),
    }
    implementation = {"version": VERSION, "sha256": {
        name:sha(ROOT/"scripts"/name) for name in ("transform_static_graph.py","validate_graph_ready.py","design_static_graph.py")}}
    output.mkdir(parents=True,exist_ok=True)
    timing = {"input_verification": perf_counter()-start}
    temporary = {"staged_files_peak_bytes": 0, "duckdb_spill_peak_bytes": 0}
    with tempfile.TemporaryDirectory(prefix=".phase5-",dir=output) as temp:
        scratch = Path(temp)
        stage = scratch/"publication"
        (stage/"nodes").mkdir(parents=True)
        (stage/"relationships").mkdir()
        config = {"threads":2,"memory_limit":"768MB","temp_directory":str(scratch/"spill")}
        with TemporaryUsage(scratch/"spill") as usage, duckdb.connect(config=config) as db:
            open_normalized(db,build,normalized)
            timing["nodes"] = timing["relationships"] = 0
            for kind,name,item in definitions(schema):
                if name == "FeedSnapshot":
                    continue
                at = perf_counter()
                db.execute(f"COPY ({projection(kind,item)}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                           [str(stage/kind/(name+".parquet"))])
                timing[kind] += perf_counter()-at
            metadata = {key: inputs[key] for key in ("normalized_build_id","source_snapshot_id","schema_version",
                                                       "canonical_contract_version")}
            metadata["construction_version"] = VERSION
            metadata["static_fingerprint"] = None
            metadata = {key:metadata[key] for key in next(n["properties"] for n in schema["nodes"] if n["label"]=="FeedSnapshot")}
            snap = stage/"nodes/FeedSnapshot.parquet"
            at = perf_counter()
            db.execute("CREATE TEMP TABLE snapshot_metadata AS " + snapshot_projection(metadata),list(metadata.values()))
            db.execute("COPY snapshot_metadata TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",[str(snap)])
            timing["nodes"] += perf_counter()-at
            reopen(db,stage,schema)
            at = perf_counter()
            digest, logical = fingerprint(db,schema)
            timing["fingerprint"] = perf_counter()-at
            metadata["static_fingerprint"] = digest
            at = perf_counter()
            db.execute("UPDATE snapshot_metadata SET static_fingerprint=?",[digest])
            db.execute("COPY snapshot_metadata TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",[str(snap)])
            timing["nodes"] += perf_counter()-at
            reopen(db,stage,schema)
            at = perf_counter()
            validation = validate(db,schema,definitions(schema),columns,key_columns,metadata)
            validation.update(validate_regional(db, baseline, normalized))
            timing["validation"] = perf_counter()-at
            at = perf_counter()
            if fingerprint(db,schema) != (digest,logical):
                raise ValueError("Final exported fingerprint differs from stage fingerprint")
            timing["fingerprint"] += perf_counter()-at
            artifacts = {}
            for kind,name,item in definitions(schema):
                path = stage/kind/(name+".parquet")
                artifacts[path.relative_to(stage).as_posix()] = {
                    "schema":columns(kind,item), "records":validation["counts"][kind][name],
                    "sha256":sha(path), "logical_sha256":logical[name], "bytes":path.stat().st_size,
                    "lineage":{"source":item["source"],"identity":item["identity"],
                               "schema_definition":name}}
            temporary["staged_files_peak_bytes"] = sum(a["bytes"] for a in artifacts.values())
        temporary["duckdb_spill_peak_bytes"] = usage.peak
        temporary["temporary_peak_upper_bound_bytes"] = temporary["staged_files_peak_bytes"] + usage.peak
        at = perf_counter()
        if verify_inputs(build,raw) != normalized:
            raise ValueError("Immutable inputs changed during transformation")
        timing["final_input_verification"] = perf_counter()-at
        build_id = "graph-ready-sha256-"+hashlib.sha256(encode([inputs,implementation,digest])).hexdigest()
        destination = output / ("build-"+build_id.removeprefix("graph-ready-sha256-")[:24])
        manifest = {
            "graph_ready_build_id":build_id,"expected_static_graph_fingerprint":digest,
            "fingerprint_format":FORMAT,"inputs":inputs,"implementation":implementation,
            "external":{"normalized_path":build.relative_to(ROOT).as_posix() if build.is_relative_to(ROOT) else str(build),
                        "join":"Typed canonical_ids mapping between canonical_id and exact source_identifier",
                        "artifacts":sorted(normalized["artifacts"])},
            "counts":validation["counts"],"artifacts":artifacts,"validation":validation,
            "artifact_bytes":temporary["staged_files_peak_bytes"],"created_utc":datetime.now(timezone.utc).isoformat(),
            "environment":{"python":platform.python_version(),"duckdb":duckdb.__version__,"platform":platform.platform(),
                           "threads":2,"duckdb_memory_limit":"768MB"},
            "execution":{"wall_seconds":round(perf_counter()-start,3),"cpu_seconds":round(process_time()-cpu,3),
                         "peak_rss_mib":peak_rss_mib(),"stages":{k:round(v,3) for k,v in timing.items()},**temporary}}
        write(stage/"manifest.json",manifest)
        reused = publish(stage,destination,manifest)
    return destination,manifest,reused


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build",type=Path,default=BUILD)
    parser.add_argument("--raw",type=Path,default=ROOT/"GTFSTransitData_RG")
    parser.add_argument("--output",type=Path,default=ROOT/"data/graph_ready")
    parser.add_argument("--repeat",action="store_true")
    args = parser.parse_args()
    out, manifest, reused = run(args.build,args.raw,args.output)
    result = {"path":str(out),"reused":reused,"build_id":manifest["graph_ready_build_id"],
              "fingerprint":manifest["expected_static_graph_fingerprint"],"execution":manifest["execution"]}
    if args.repeat:
        # A fresh process isolates the second run's peak process memory.
        import subprocess
        import sys
        command = [sys.executable,__file__,"--build",str(args.build),"--raw",str(args.raw),"--output",str(args.output)]
        repeat = json.loads(subprocess.check_output(command,text=True))
        if not repeat["reused"] or any(result[k] != repeat[k] for k in ("path","build_id","fingerprint")):
            raise ValueError("Repeated build differs")
        result["repeat"] = repeat
    print(json.dumps(result))


if __name__ == "__main__":
    main()

