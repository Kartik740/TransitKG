"""Phase 4 contracts evaluated on the published regional snapshot and small negative cases."""
import os
import sys
import platform
from time import perf_counter
import unittest
from pathlib import Path
from urllib.parse import quote

from design_static_graph import ROOT, SCHEMA, REPORT, open_build, read, scalar, check_snapshot, verify_inputs, sha, write


class StaticContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.getenv("TRANSITKG_PHASE3_BUILD")
        if path:
            cls.build = Path(path)
        else:
            [cls.build] = list((ROOT / "data/normalized").glob("build-*"))
        cls.manifest = read(cls.build / "manifest.json")
        cls.schema = read(SCHEMA / "static_graph_schema.json")
        cls.stats = read(REPORT / "architecture_comparison.json")
        cls.db = open_build(cls.build)
        cls.addClassCleanup(cls.db.close)

    def count(self, query, args=None):
        return scalar(self.db, query, args)

    def same_rows(self, actual, expected):
        for a,b in ((actual,expected),(expected,actual)):
            self.assertEqual(self.count(f"SELECT count(*) FROM (({a}) EXCEPT ALL ({b}))"), 0)

    def test_immutable_publication(self):
        self.assertEqual(verify_inputs(self.build, ROOT / "GTFSTransitData_RG"), self.manifest)

    def test_schema_types_endpoints_and_diagram(self):
        nodes = {n["label"]:n for n in self.schema["nodes"]}
        edges = self.schema["relationships"]
        self.assertEqual(len(nodes), len(self.schema["nodes"]))
        self.assertEqual(len(set(e["type"] for e in edges)), len(edges))
        diagram = (SCHEMA / "static_graph_schema.mmd").read_text()
        for e in edges:
            self.assertIn(e["from"], nodes)
            self.assertIn(e["to"], nodes)
            self.assertIn(f'{e["from"]} -->|{e["type"]}| {e["to"]}', diagram)
        for item in [*nodes.values(), *edges]:
            self.assertEqual(item["storage"], "graph")
            self.assertEqual(item["classification"], "static")
            for p, spec in item["properties"].items():
                self.assertIn(spec["type"], ("STRING","INTEGER"))
                self.assertIsInstance(spec["required"], bool)
                if "." in spec["source"]:
                    table,col = spec["source"].split(".")
                    if table not in self.manifest["artifacts"]:
                        continue
                    dtype = self.manifest["artifacts"][table]["schema"][col]
                    self.assertIn(dtype, {"STRING":["VARCHAR"],"INTEGER":["BIGINT","INTEGER"]}[spec["type"]])
                    if spec["required"]:
                        self.assertEqual(self.count(f"SELECT count(*) FROM {table} WHERE {col} IS NULL"), 0)
                    if "allowed" in spec:
                        self.assertEqual(self.count(f"SELECT count(*) FROM {table} WHERE {col} NOT IN (SELECT unnest(?))",
                                                    [spec["allowed"]]), 0)

    def test_phase3_mapping_contract_and_scope(self):
        m = self.manifest
        self.assertEqual(self.schema["canonical_contract_version"],m["canonical_contract_version"])
        self.assertEqual(self.schema["namespace"],m["source_namespace"])
        records = self.db.sql("SELECT entity_type,source_identifier,canonical_id,source_snapshot_id FROM canonical_ids").fetchall()
        self.assertEqual(len({r[2] for r in records}),len(records))
        for kind, source, cid, snapshot in records:
            self.assertEqual(cid, f'{m["source_namespace"]}:{kind}:{quote(source,safe="")}')
            self.assertEqual(snapshot, m["source_snapshot_id"])
        self.assertEqual(len(records),118128)
        self.assertEqual(self.count("""SELECT count(*) FROM (
          SELECT regexp_replace(source_identifier,'^[A-Za-z0-9]+[:_]','') AS suffix FROM canonical_ids
          WHERE entity_type='stop' GROUP BY 1 HAVING count(*)>1)"""),364)
        for n in self.schema["nodes"]:
            if n.get("entity_type") in ("agency","route","stop","trip"):
                kind = n["entity_type"]
                self.same_rows(f'SELECT {kind}_id FROM {n["source"]}',
                               f"SELECT source_identifier FROM canonical_ids WHERE entity_type='{kind}'")

    def test_endpoint_coverage_keys_and_cardinalities(self):
        for e in self.schema["relationships"]:
            a,b,table = e["start_column"],e["end_column"],e["source"]
            extra = e["identity"][3:]
            keys = ",".join([a,b,*extra])
            valid = f"{a} IS NOT NULL AND {b} IS NOT NULL"
            if e["type"] != "PART_OF":
                self.assertEqual(self.count(f"SELECT count(*) FROM {table} WHERE NOT ({valid})"),0)
            self.assertEqual(self.count(f"""SELECT count(*) FROM (SELECT {keys} FROM {table}
                              WHERE {valid} GROUP BY ALL HAVING count(*)>1)"""),0)
            for col,label in ((a,e["from"]),(b,e["to"])):
                self.assertEqual(self.count(f"""SELECT count(*) FROM {table} x WHERE {valid} AND NOT EXISTS
                  (SELECT 1 FROM canonical_ids c WHERE c.entity_type=? AND c.source_identifier=x.{col})""",
                                            [label.lower()]),0)
            self.assertEqual(self.count(f"SELECT count(*) FROM {table} WHERE {valid}"),self.stats["edges"][e["type"]])
        self.assertEqual(self.stats["alternatives"]["B_compact"],{"nodes":115683,"relationships":242491})
        self.assertEqual(sum(self.stats["nodes"].values()),115683)
        self.assertEqual(sum(self.stats["edges"].values()),242491)

    def test_route_stop_derivation(self):
        self.same_rows("SELECT route_id,stop_id,scheduled_visits,scheduled_trips FROM route_stops",
                       """SELECT route_id,stop_id,count(*),count(DISTINCT trip_id) FROM trips
                       JOIN stop_times USING(trip_id) GROUP BY ALL""")

    def test_agency_stop_derivation(self):
        self.same_rows("SELECT agency_id,stop_id,scheduled_visits,serving_routes FROM agency_stops",
                       """SELECT agency_id,stop_id,count(*),count(DISTINCT route_id) FROM routes JOIN trips USING(route_id)
                       JOIN stop_times USING(trip_id) GROUP BY ALL""")
        self.assertEqual(self.count("SELECT count(*) FROM (SELECT stop_id FROM agency_stops GROUP BY 1 HAVING count(*)>1)"),189)
        for agency in ("CM","SM"):
            self.same_rows(f"SELECT DISTINCT route_id FROM scoped_visits('{agency}',NULL,NULL,'341028',NULL,NULL)",
                           f"SELECT route_id FROM route_stops WHERE agency_id='{agency}' AND stop_id='341028'")
            self.assertGreater(self.count("SELECT count(*) FROM scoped_visits(?,NULL,NULL,'341028',NULL,NULL)",[agency]),0)

    def test_graph_parquet_bridge(self):
        for table, kinds in (("agency_stops",("agency","stop")),("route_stops",("route","stop","agency")),
                             ("trip_selector_context",("trip",))):
            for kind in kinds:
                self.assertEqual(self.count(f"""SELECT count(*) FROM {table} t ANTI JOIN canonical_ids i ON
                i.entity_type=? AND t.{kind}_id=i.source_identifier AND t.canonical_{kind}_id=i.canonical_id""",[kind]),0)

    def test_calendar_exceptions_override_and_weekday_bounds(self):
        q = "SELECT count(*) FROM active_services(?) WHERE service_id=?"
        self.assertEqual(self.count(q,["2022-05-30","PG:80068"]),1)
        self.assertEqual(self.count(q,["2022-05-30","PG:80067"]),0)
        self.assertEqual(self.count("SELECT count(*) FROM active_services(DATE '2100-01-01')"),0)
        self.assertEqual(self.count("SELECT count(*) FROM active_services(DATE '2026-11-25')"),125)
        self.assertEqual(self.count("""SELECT count(DISTINCT trip_id) FROM scoped_visits('AC',NULL,NULL,'55660',NULL,NULL)
                               JOIN active_services(DATE '2026-11-25') USING(service_id)"""),112)
        # An exception-only service must work without a calendar row, including the next day's boundary.
        with self.db.cursor() as fixture:
            fixture.execute("BEGIN")
            try:
                fixture.execute("CREATE OR REPLACE TEMP VIEW canonical_ids AS SELECT 'service' entity_type,'only' source_identifier")
                fixture.execute("CREATE OR REPLACE TEMP VIEW calendar_dates AS SELECT 'only' service_id,DATE '2026-05-04' date,1 exception_type")
                self.assertEqual(scalar(fixture,q,["2026-05-04","only"]),1)
                self.assertEqual(scalar(fixture,q,["2026-05-05","only"]),0)
            finally:
                fixture.execute("ROLLBACK")

    def test_extended_hours_and_occurrences(self):
        self.assertEqual(self.count("""SELECT arrival_time_seconds FROM stop_times WHERE trip_id='3D:1175'
           AND stop_sequence=2 AND arrival_time='24:02:48'"""),86568)
        self.assertEqual(self.count("""SELECT count(*) FROM (SELECT trip_id FROM stop_times GROUP BY 1
            HAVING count(*)>count(DISTINCT stop_id))"""),self.stats["patterns"]["trips_with_repeated_stops"])
        self.assertEqual(self.stats["patterns"]["reconstructed_visits"],self.stats["visits"])
        self.assertEqual(self.count("SELECT count(DISTINCT (trip_id,stop_sequence)) FROM stop_times"),self.stats["visits"])

    def test_dated_trip_and_optional_descriptor_fields(self):
        sql = "SELECT count(*) FROM dated_trip_candidates(DATE '2026-11-25',?,?,?,?)"
        for params,expected in [
            (["AC:10155020","AC:72L",0,44280],1),(["AC:10155020",None,None,None],1),
            (["AC:10155020","AC:72",0,44280],0),(["AC:10155020",None,None,5],0),
            ([None,None,None,None],0),(["unknown",None,None,None],0),
        ]:
            self.assertEqual(self.count(sql,params),expected)

    def test_quarantine_and_geometry(self):
        self.assertEqual(self.count("SELECT count(*) FROM directions WHERE route_id='WH:53' AND direction_id=0"),0)
        self.assertGreater(self.count("SELECT count(*) FROM trips WHERE route_id='WH:53' AND direction_id=0"),0)
        for file, expected in (("fare_transfer_rules",68),("directions",2)):
            self.assertEqual(self.count("SELECT count(*) FROM row_exception_ledger WHERE source_file=? AND disposition='QUARANTINED'",[file+".txt"]),expected)
            self.assertEqual(self.count(f"""SELECT count(*) FROM {file} JOIN row_exception_ledger USING(source_record)
                WHERE source_file=? AND disposition='QUARANTINED'""",[file+".txt"]),0)
        self.assertEqual(self.count("""SELECT count(*) FROM shapes JOIN row_exception_ledger USING (source_record)
            WHERE disposition='FLAGGED' AND source_file='shapes.txt'"""),21)

    def test_scoped_alert_and_names(self):
        for stop,total in (("55660",345),("52227",339)):
            self.assertEqual(self.count("SELECT count(DISTINCT trip_id) FROM scoped_visits('AC',NULL,NULL,?,NULL,NULL)",[stop]),total)
            self.assertEqual(self.count("SELECT canonical_stop_id FROM agency_stops WHERE agency_id='AC' AND stop_id=?",[stop]),
                             "511-sf-bay-regional:stop:"+stop)
        self.assertEqual(self.count("SELECT count(DISTINCT stop_name) FROM stops WHERE stop_id IN ('55660','52227')"),1)
        q = "SELECT count(DISTINCT trip_id) FROM scoped_visits(?, 'AC:72',3,'55660',?,NULL)"
        for a,d,n in (("AC",0,126),("AC",1,0),("BA",0,0)):
            self.assertEqual(self.count(q,[a,d]),n)

    def test_snapshot_gate_and_dynamic_boundary(self):
        graph = [(self.manifest["source_snapshot_id"],self.manifest["normalized_build_id"],self.schema["version"],
                  self.schema["canonical_contract_version"])]
        check_snapshot(graph,self.manifest,self.schema)
        for bad in ([],graph*2,[("other",*graph[0][1:])],[(graph[0][0],"stale",*graph[0][2:])]):
            with self.assertRaises(ValueError):
                check_snapshot(bad,self.manifest,self.schema)
        dyn = self.schema["provisional_dynamic"]
        self.assertTrue(set(dyn["nodes"]).isdisjoint(n["label"] for n in self.schema["nodes"]))
        self.assertTrue(set(dyn["relationships"]).isdisjoint(e["type"] for e in self.schema["relationships"]))


def run_suites():
    if not os.getenv("TRANSITKG_PHASE3_BUILD"):
        [path] = list((ROOT / "data/normalized").glob("build-*"))
        os.environ["TRANSITKG_PHASE3_BUILD"] = str(path)
    results = {}
    for phase, filename in [(2,"test_profile_gtfs.py"),(3,"test_normalize_gtfs.py"),(4,"test_static_graph.py")]:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "scripts"),pattern=filename)
        start = perf_counter()
        run = unittest.TextTestRunner(verbosity=2).run(suite)
        results[str(phase)] = {"total":run.testsRun, "passed":run.testsRun-len(run.failures)-len(run.errors)-len(run.skipped),
            "failures":[t.id() for t,_ in run.failures], "errors":[t.id() for t,_ in run.errors],
            "skipped":[(t.id(),reason) for t,reason in run.skipped], "seconds":round(perf_counter()-start,3)}
    m = read(Path(os.environ["TRANSITKG_PHASE3_BUILD"]) / "manifest.json")
    write(REPORT / "schema_validation_report.json",{
        "normalized_build_id":m["normalized_build_id"], "source_snapshot_id":m["source_snapshot_id"],
        "python":sys.version.split()[0], "platform":platform.platform(), "suites":results,
        "verified_schema_files":{p.name:sha(p) for p in sorted(SCHEMA.iterdir()) if p.is_file()},
        "inspected_evidence_sha256":{p.relative_to(ROOT).as_posix():sha(p)
            for phase in ("phase2","phase3") for p in sorted((ROOT / "reports" / phase).iterdir()) if p.is_file()},
        "server_verification":"neo4j_verification.json",
        "production_graph":"deferred to Phase 6; no production import, timing, store size or fingerprint"})
    return any(r["passed"]!=r["total"] for r in results.values())


if __name__ == "__main__":
    raise SystemExit(run_suites())

