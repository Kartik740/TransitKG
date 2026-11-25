"""Measure Phase 4 projections against an immutable Phase 3 publication."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema"
REPORT = ROOT / "reports/phase4"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_inputs(build, raw):
    m = read(build / "manifest.json")
    if m != read(ROOT / "reports/phase3/dataset_manifest.json"):
        raise ValueError("Build manifest differs from Phase 3 report")
    for base, entries in [(raw, {x["path"]: x["sha256"] for x in m["input_files"]}),
                          (build, {x["file"]: x["sha256"] for x in m["artifacts"].values()})]:
        actual = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}
        expected = set(entries) | ({"manifest.json"} if base == build else set())
        if actual != expected:
            raise ValueError(f"File set mismatch: {actual ^ expected}")
        for name, digest in entries.items():
            if sha(base / name) != digest:
                raise ValueError(f"Checksum mismatch: {name}")
    for name, digest in m["phase2_report_sha256"].items():
        if sha(ROOT / "reports/phase2" / name) != digest:
            raise ValueError(f"Phase 2 evidence changed: {name}")
    return m


def open_build(build):
    db = duckdb.connect(config={"threads": 2})
    for p in sorted(build.glob("*.parquet")):
        db.read_parquet(str(p)).create_view(p.stem)
    db.execute((SCHEMA / "timetable.sql").read_text())
    return db


def check_snapshot(graph, manifest, schema):
    wanted = (manifest["source_snapshot_id"], manifest["normalized_build_id"],
              schema["version"], manifest["canonical_contract_version"])
    if len(graph) != 1 or tuple(graph[0]) != wanted or wanted[-1] != schema["canonical_contract_version"]:
        raise ValueError("Static graph and tabular publication are incompatible")


def render(schema):
    ddl, diagram = [], ["flowchart LR"]
    for n in schema["nodes"]:
        label, key = n["label"], n["identity"]
        ddl.append(f"CREATE CONSTRAINT uq_{label} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE;")
        diagram.append(f'  {label}["{label}"]')
    for label, field in schema["indexes"]:
        ddl.append(f"CREATE INDEX ix_{label}_{field} IF NOT EXISTS FOR (n:{label}) ON (n.{field});")
    for e in schema["relationships"]:
        diagram.append(f'  {e["from"]} -->|{e["type"]}| {e["to"]}')
    (SCHEMA / "neo4j_schema.cypher").write_text("\n".join(ddl) + "\n")
    (SCHEMA / "static_graph_schema.mmd").write_text("\n".join(diagram) + "\n")


def scalar(db, sql, args=None):
    return db.execute(sql, args or []).fetchone()[0]


def rows(db, sql, args=None):
    cur = db.execute(sql, args or [])
    fields = [col[0] for col in cur.description]
    return [dict(zip(fields, row)) for row in cur.fetchall()]


def measure(db, schema, m):
    nodes = dict(db.sql("SELECT entity_type,count(*) FROM canonical_ids GROUP BY 1").fetchall())
    graph_nodes = {n["label"]: nodes[n["entity_type"]] if "entity_type" in n else 1 for n in schema["nodes"]}
    edges = {}
    for e in schema["relationships"]:
        table, start, end = e["source"], e["start_column"], e["end_column"]
        edges[e["type"]] = scalar(db, f"SELECT count(*) FROM {table} WHERE {start} IS NOT NULL AND {end} IS NOT NULL")
    pattern_at = perf_counter()
    patterns = rows(db, """
        WITH sequences AS (
          SELECT trip_id,list(stop_id ORDER BY stop_sequence) AS stops FROM stop_times GROUP BY trip_id
        ), patterns AS (
          SELECT route_id,direction_id,stops,count(*) AS n FROM sequences JOIN trips USING(trip_id) GROUP BY ALL
        )
        SELECT count(*) AS patterns,sum(len(stops)) AS occurrences,
          sum(n) FILTER (WHERE n>1) AS trips_in_shared_patterns,count(*) FILTER (WHERE n>1) AS shared_patterns,
          max(n) AS largest_trip_group,sum(n) AS trips,
          sum(n) FILTER (WHERE len(stops)>len(list_distinct(stops))) AS trips_with_repeated_stops,
          sum(len(stops)*n) AS reconstructed_visits FROM patterns
    """)[0]
    seconds = perf_counter() - pattern_at
    visits = scalar(db, "SELECT count(*) FROM stop_times")
    if patterns["reconstructed_visits"] != visits or patterns["trips"] != nodes["trip"]:
        raise ValueError("Pattern census does not cover every trip and visit")
    refs = {}
    for stop in ("55660", "52227"):
        refs[stop] = rows(db, """SELECT stop_id,stop_name,canonical_stop_id,route_id,count(DISTINCT trip_id) AS trips
            FROM scoped_visits('AC',NULL,NULL,?,NULL,NULL) JOIN stops USING(stop_id)
            JOIN agency_stops USING(agency_id,stop_id) GROUP BY ALL ORDER BY route_id""", [stop])
    facts = {
        "shared_stops": scalar(db, "SELECT count(*) FROM (SELECT stop_id FROM agency_stops GROUP BY 1 HAVING count(*)>1)"),
        "location_types": rows(db, "SELECT location_type,count(*) AS n FROM stops GROUP BY 1 ORDER BY 1"),
        "route_types": rows(db, "SELECT route_type,count(*) AS n FROM routes GROUP BY 1 ORDER BY 1"),
        "transfers": rows(db, """SELECT transfer_type,count(*) AS n,count(from_route_id) AS from_route,
            count(to_route_id) AS to_route,count(from_trip_id) AS from_trip,count(to_trip_id) AS to_trip
            FROM transfers GROUP BY 1 ORDER BY 1"""),
        "calendar_end_2099": scalar(db, "SELECT count(*) FROM calendar WHERE year(end_date)=2099"),
        "exceptions": scalar(db, "SELECT count(*) FROM calendar_dates"),
        "shape_points": scalar(db, "SELECT count(*) FROM shapes"),
        "trips_with_shape": scalar(db, "SELECT count(shape_id) FROM trips"),
        "extended_times": rows(db, """SELECT count(*) FILTER(WHERE arrival_time_seconds>86400) AS arrivals,
          count(*) FILTER(WHERE departure_time_seconds>86400) AS departures FROM stop_times""")[0],
        "scope_sample": refs,
        "dated_example": rows(db, """SELECT count(DISTINCT trip_id) AS trips,count(*) AS visits
           FROM scoped_visits('AC',NULL,NULL,'55660',NULL,NULL) JOIN active_services(DATE '2026-11-25') USING(service_id)""")[0],
        "direction_example": rows(db, """SELECT route_id,direction_id,count(DISTINCT trip_id) AS trips
            FROM scoped_visits('AC',NULL,NULL,'55660',NULL,NULL) GROUP BY ALL ORDER BY ALL"""),
        "application_examples": rows(db, """SELECT * FROM trip_selector_context
            WHERE trip_id IN (SELECT trip_id FROM scoped_visits('AC',NULL,NULL,'55660',NULL,NULL))
            ORDER BY trip_id LIMIT 1"""),
    }
    bench = {}
    for key, query in {
        "association":"SELECT canonical_stop_id FROM agency_stops WHERE agency_id='AC' AND stop_id='55660'",
        "scoped_trip_count":"SELECT count(DISTINCT trip_id) FROM scoped_visits('AC',NULL,NULL,'55660',NULL,NULL)",
        "date_scoped_count":"SELECT count(DISTINCT trip_id) FROM scoped_visits('AC',NULL,NULL,'55660',NULL,NULL) JOIN active_services(DATE '2026-11-25') USING(service_id)",
    }.items():
        times = []
        for _ in range(6):
            at = perf_counter()
            result = scalar(db, query)
            times.append(round((perf_counter()-at)*1000, 3))
        bench[key] = {"query":query, "result":result, "first_ms":times[0], "warm_ms":times[1:], "warm_median_ms":median(times[1:])}
    n, e = sum(graph_nodes.values()), sum(edges.values())
    return {"source_snapshot_id":m["source_snapshot_id"], "normalized_build_id":m["normalized_build_id"],
            "engine":duckdb.__version__, "threads":2, "nodes":graph_nodes, "edges":edges, "mapping_counts":nodes,
            "visits":visits, "patterns":patterns, "patterns_seconds":round(seconds,3), "reference":facts,
            "parquet_bytes":m["parquet_bytes"], "benchmark":bench,
            "alternatives":{"B_compact":{"nodes":n,"relationships":e},
                            "A_visit_edges":{"nodes":n,"relationships":e+visits},
                            "A_visit_nodes":{"nodes":n+visits,"relationships":e+2*visits},
                            "C_patterns":{"nodes":n+patterns["patterns"],"relationships":e+patterns["trips"]+patterns["occurrences"]}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("--raw", type=Path, default=ROOT / "GTFSTransitData_RG")
    args = parser.parse_args()
    manifest = verify_inputs(args.build, args.raw)
    schema = read(SCHEMA / "static_graph_schema.json")
    with open_build(args.build) as db:
        evidence = measure(db, schema, manifest)
    render(schema)
    REPORT.mkdir(parents=True, exist_ok=True)
    write(REPORT / "architecture_comparison.json", evidence)
    print(json.dumps(evidence["alternatives"]))


if __name__ == "__main__":
    main()

