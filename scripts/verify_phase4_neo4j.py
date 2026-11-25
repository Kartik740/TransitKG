"""Probe the selected database. --fixture additionally exercises a fresh private database."""
import argparse
import json
import logging
import os
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

import neo4j
from neo4j import GraphDatabase, READ_ACCESS

ROOT = Path(__file__).resolve().parents[1]
PARAMS = dict(agency="A", stop="S", target="T", route="R", trip="X", name="Twins",
              parent="P", direction=0, route_type=3)


def statements(filename):
    lines = (ROOT / "schema" / filename).read_text().splitlines()
    return [q.strip() for q in "\n".join(line for line in lines if not line.startswith("//")).split(";") if q.strip()]


def fixture(driver, result):
    db = "transitkgfixture" + uuid4().hex
    run = result["fixture"] = {"database": db, "status": "started", "removed": False}
    with driver.session(database="system") as admin:
        # CREATE without IF NOT EXISTS: a name collision must never enter the cleanup block.
        admin.run(f"CREATE DATABASE {db}").consume()
        try:
            admin.run(f"START DATABASE {db} WAIT 45 SECONDS").consume()
            with driver.session(database=db) as s:
                for q in statements("neo4j_schema.cypher"):
                    s.run(q).consume()
                s.run("CALL db.awaitIndexes(60)").consume()
                schema = json.loads((ROOT / "schema/static_graph_schema.json").read_text())
                constraints = {r["name"] for r in s.run("SHOW CONSTRAINTS YIELD name RETURN name")}
                expected = {"uq_"+n["label"] for n in schema["nodes"]}
                indexes = {r["name"] for r in s.run("SHOW INDEXES YIELD name,state WHERE state='ONLINE' RETURN name")}
                if constraints != expected or not (expected | {f"ix_{n}_{p}" for n,p in schema["indexes"]}) <= indexes:
                    raise ValueError("Fixture catalog differs from declared schema")
                run["constraints"] = len(constraints)
                run["indexes_online"] = len(indexes)
                s.run("""CREATE (a:Agency {source_id:'A',canonical_id:'a',agency_name:'A',agency_timezone:'UTC'}),
                    (b:Agency {source_id:'B',canonical_id:'b',agency_name:'B',agency_timezone:'UTC'}),
                    (r:Route {source_id:'R',canonical_id:'r',route_type:3}),
                    (t:Trip {source_id:'X',canonical_id:'x',direction_id:0}),
                    (v:Service {source_id:'V',canonical_id:'v'}),
                    (p:Stop {source_id:'P',canonical_id:'p',location_type:1}),
                    (s:Stop {source_id:'S',canonical_id:'s',stop_name:'Twins'}),
                    (u:Stop {source_id:'T',canonical_id:'u',stop_name:'Twins'}),
                    (:FeedSnapshot {normalized_build_id:'build',source_snapshot_id:'snapshot',
                        schema_version:'1',canonical_contract_version:'1',construction_version:'test',
                        static_fingerprint:'fixture'}),
                    (a)-[:OPERATES]->(r),(r)-[:HAS_TRIP]->(t),(t)-[:RUNS_ON]->(v),
                    (a)-[:AGENCY_SERVES_STOP {scheduled_visits:1, serving_routes:1}]->(s),
                    (b)-[:AGENCY_SERVES_STOP {scheduled_visits:1, serving_routes:1}]->(s),
                    (r)-[:ROUTE_SERVES_STOP {scheduled_visits:1, scheduled_trips:1}]->(s),
                    (s)-[:PART_OF]->(p),(s)-[:TRANSFER_RULE {transfer_type:2,min_transfer_time:75}]->(u)
                    """).consume()
                for n in schema["nodes"]:
                    if "entity_type" in n:
                        s.run(f"MATCH (n:{n['label']}) SET n.canonical_id=$prefix+n.source_id",
                              prefix=schema["namespace"]+":"+n["entity_type"]+":").consume()
                stop_key = schema["namespace"]+":stop:S"
                queries = statements("competency.cypher")
                expected = [[stop_key],["S","T"],["A","B"],[schema["namespace"]+":route:R"],
                            [schema["namespace"]+":trip:X"],["R"],["S"],["X"],["V"],
                            ["S"],[{"transfer_type":2,"min_transfer_time":75}],["S"],["X"],["snapshot"]]
                for query, wanted in zip(queries, expected, strict=True):
                    if [record[0] for record in s.run(query, PARAMS)] != wanted:
                        raise ValueError("Fixture answer differs from query contract")
                run["answered_queries"] = len(queries)
                times = []
                for _ in range(5):
                    start = perf_counter()
                    s.run(queries[0], PARAMS).consume()
                    times.append(round((perf_counter()-start)*1000, 3))
                run["warm_scoped_lookup_ms"] = times
                run["median_ms"] = median(times)
                try:
                    s.run("CREATE (:Stop {canonical_id:$id})", id=stop_key).consume()
                except neo4j.exceptions.ConstraintError:
                    run["duplicate_rejected"] = True
                else:
                    raise ValueError("Canonical uniqueness check failed")
                run["status"] = "passed"
        finally:
            admin.run(f"DROP DATABASE {db} WAIT 45 SECONDS").consume()
            run["removed"] = not admin.run("SHOW DATABASES YIELD name WHERE name=$name RETURN name", name=db).data()
            if not run["removed"]:
                raise RuntimeError("Fixture cleanup not confirmed")


def probe(use_fixture=False):
    result = {"driver": neo4j.__version__, "existing_database_writes": 0, "status": "blocked"}
    logging.getLogger("neo4j").setLevel(logging.CRITICAL)
    try:
        with GraphDatabase.driver(
            os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687"),
            auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
            connection_timeout=5, connection_acquisition_timeout=10, max_transaction_retry_time=0,
        ) as driver:
            driver.verify_connectivity()
            with driver.session(database=os.getenv("NEO4J_DATABASE") or None,
                                default_access_mode=READ_ACCESS) as session:
                result["components"] = session.run(
                    "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition"
                ).data()
                result["database"] = session.run("CALL db.info() YIELD name RETURN name").single()[0]
                result["read_query"] = session.run("RETURN 1 AS ok").single()[0]
                result["status"] = "connected"
                queries = statements("competency.cypher")
                for query in queries:
                    session.run("EXPLAIN " + query, PARAMS).consume()
                result["competency_query_plans"] = len(queries)
            if use_fixture:
                fixture(driver, result)
    except Exception as exc:
        result.update(status="blocked", error_type=type(exc).__name__, code=getattr(exc, "code", None))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", help="Create, verify and drop a new dedicated database")
    result = probe(parser.parse_args().fixture)
    out = ROOT / "reports/phase4/neo4j_verification.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    raise SystemExit(result["status"] != "connected")

