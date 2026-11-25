"""Phase 5 fault, serialization, and reopened publication tests."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import duckdb

from design_static_graph import ROOT, read, sha
from transform_static_graph import (BUILD, FORMAT, SCHEMA_FILE, columns, definitions, fingerprint,
                                    key_columns, open_normalized, pin_inputs, publish, reopen)
from validate_graph_ready import scoped, validate, validate_columns


class FixtureTests(unittest.TestCase):
    def test_required_types_and_duplicate_identities(self):
        with duckdb.connect() as db:
            fields = {"id":"VARCHAR","value":"BIGINT"}
            props = {"id":{"required":True},"value":{"required":False}}
            cases = [
                ("SELECT 'x' id, NULL::BIGINT AS value",None),
                ("SELECT NULL::VARCHAR id, 3::BIGINT AS value","nulls"),
                ("SELECT 'x' id, '3' AS value","type mismatch"),
                ("SELECT 'x' id, 3::BIGINT AS value UNION ALL SELECT 'x',4","duplicate"),
            ]
            for sql,error in cases:
                with self.subTest(sql=sql):
                    db.execute("CREATE OR REPLACE VIEW node AS "+sql)
                    if error:
                        with self.assertRaisesRegex(ValueError,error):
                            validate_columns(db,"node",fields,props,["id"])
                    else:
                        validate_columns(db,"node",fields,props,["id"])

    def test_transfer_identity_keeps_scope_null_empty_and_prohibited_types(self):
        edge = next(e for e in read(SCHEMA_FILE)["relationships"] if e["type"]=="TRANSFER_RULE")
        with duckdb.connect() as db:
            db.execute("""CREATE TABLE graph_TRANSFER_RULE AS SELECT 'S' start_id,'T' end_id,
                from_route AS from_route_id, NULL::VARCHAR to_route_id,NULL::VARCHAR from_trip_id,
                NULL::VARCHAR to_trip_id,type::BIGINT transfer_type,NULL::BIGINT min_transfer_time
                FROM (VALUES (NULL,3),('',0),('R',3)) v(from_route,type)""")
            validate_columns(db,"graph_TRANSFER_RULE",columns("relationships",edge),edge["properties"],
                             key_columns("relationships",edge))
            self.assertEqual(db.sql("SELECT count(*) FROM graph_TRANSFER_RULE WHERE transfer_type=3").fetchone()[0],2)
            db.execute("INSERT INTO graph_TRANSFER_RULE SELECT * FROM graph_TRANSFER_RULE WHERE from_route_id IS NULL")
            with self.assertRaisesRegex(ValueError,"duplicate"):
                validate_columns(db,"graph_TRANSFER_RULE",columns("relationships",edge),edge["properties"],
                                 key_columns("relationships",edge))

    def test_static_utf8_fingerprint_and_composite_key_contract(self):
        schema = {"nodes":[{"label":"Stop","identity":"canonical_id","properties":{
                "canonical_id":{"type":"STRING"},"name":{"type":"STRING"}}}],
                  "relationships":[{"type":"RULE","identity":["type","start.canonical_id","end.canonical_id","scope"],
                                    "properties":{"scope":{"type":"STRING"}}}]}
        with duckdb.connect() as db:
            db.execute("CREATE TABLE graph_Stop(canonical_id VARCHAR,name VARCHAR)")
            db.executemany("INSERT INTO graph_Stop VALUES (?,?)",[("S",None),("T",'é,\n"')])
            db.execute("CREATE TABLE graph_RULE(start_id VARCHAR,end_id VARCHAR,scope VARCHAR)")
            db.executemany("INSERT INTO graph_RULE VALUES (?,?,?)",[("S","T",None),("S","T","")])
            records = [
                ["N","Stop","S",{"canonical_id":"S","name":None}],
                ["N","Stop","T",{"canonical_id":"T","name":'é,\n"'}],
                ["R","RULE",["RULE","S","T",None],{"scope":None}],
                ["R","RULE",["RULE","S","T",""],{"scope":""}],
            ]
            strings = sorted(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode() for r in records)
            expected = hashlib.sha256(FORMAT.encode()+b"\n"+b"\n".join(strings)+b"\n").hexdigest()
            actual,_ = fingerprint(db,schema)
            self.assertEqual(actual,expected)
            db.execute("CREATE OR REPLACE TABLE graph_Stop AS SELECT * FROM graph_Stop ORDER BY canonical_id DESC")
            self.assertEqual(fingerprint(db,schema)[0],expected)
            db.execute("UPDATE graph_RULE SET scope='other' WHERE scope IS NULL")
            self.assertNotEqual(fingerprint(db,schema)[0],expected)

    def test_incomplete_existing_build_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            existing = Path(temp)/"existing"
            staged = Path(temp)/"staged"
            existing.mkdir()
            staged.mkdir()
            marker = existing/"unrelated"
            marker.write_text("keep")
            with self.assertRaises(FileNotFoundError):
                publish(staged,existing,{})
            self.assertEqual(marker.read_text(),"keep")
            self.assertTrue(staged.is_dir())

    def test_relationship_with_wrong_endpoint_label_is_rejected(self):
        schema = read(SCHEMA_FILE)
        edge = next(e for e in schema["relationships"] if e["type"]=="OPERATES")
        with duckdb.connect() as db:
            db.execute("CREATE VIEW routes AS SELECT 'agency' agency_id,'route' route_id")
            db.execute("CREATE VIEW graph_Agency AS SELECT 'A' canonical_id,'agency' source_id")
            db.execute("CREATE VIEW graph_Route AS SELECT 'R' canonical_id,'route' source_id")
            db.execute("CREATE VIEW graph_OPERATES AS SELECT 'R' start_id,'A' end_id")
            with self.assertRaisesRegex(ValueError,"dangling or mistyped endpoints"):
                validate(db,schema,[("relationships","OPERATES",edge)],columns,key_columns,{})

    def test_input_manifest_and_corrupt_raw_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p/"manifest.json").write_text("{}")
            with self.assertRaisesRegex(ValueError,"Manifest|manifest"):
                pin_inputs(p,p)
            raw = json.loads((BUILD/"manifest.json").read_text())["input_files"]
            for entry in raw:
                path = p/entry["path"]
                path.parent.mkdir(parents=True,exist_ok=True)
                path.touch()
            p.joinpath("manifest.json").unlink()
            with self.assertRaisesRegex(ValueError,"Checksum mismatch"):
                pin_inputs(BUILD,p)


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output = ROOT/"data/graph_ready"
        candidates = sorted(output.glob("build-*/manifest.json"))
        cls.assert_build(candidates)
        cls.path = candidates[0].parent
        cls.manifest = read(candidates[0])
        cls.normalized, cls.schema, cls.baseline = pin_inputs(BUILD,ROOT/"GTFSTransitData_RG")
        cls.db = duckdb.connect(config={"threads":2})
        cls.addClassCleanup(cls.db.close)
        open_normalized(cls.db,BUILD,cls.normalized)
        reopen(cls.db,cls.path,cls.schema)

    @staticmethod
    def assert_build(candidates):
        if len(candidates) != 1:
            raise AssertionError("Expected the single Phase 5 regional publication")

    def test_manifest_export_inventory_cardinality_and_fingerprint(self):
        m = self.manifest
        self.assertEqual(m["inputs"]["normalized_build_id"],self.normalized["normalized_build_id"])
        self.assertEqual(m["inputs"]["schema_sha256"],sha(SCHEMA_FILE))
        digest, logical = fingerprint(self.db,self.schema)
        self.assertEqual(digest,m["expected_static_graph_fingerprint"])
        self.assertEqual(self.db.sql("SELECT static_fingerprint FROM graph_FeedSnapshot").fetchone()[0],digest)
        for kind,name,item in definitions(self.schema):
            key = f"{kind}/{name}.parquet"
            artifact = m["artifacts"][key]
            self.assertEqual(artifact["logical_sha256"],logical[name])
            self.assertEqual(artifact["sha256"],sha(self.path/key))
            self.assertEqual(artifact["records"],self.baseline["nodes" if kind=="nodes" else "edges"][name])
            validate_columns(self.db,"graph_"+name,columns(kind,item),item["properties"],key_columns(kind,item))

    def test_scoped_visits_temporal_and_external_identity(self):
        self.assertEqual(sum(r["trips"] for r in scoped(self.db,"AC","55660")),345)
        self.assertEqual(sum(r["trips"] for r in scoped(self.db,"AC","52227")),339)
        self.assertEqual(sum(r["trips"] for r in scoped(self.db,"AC","55660",date="2026-11-25")),112)
        self.assertEqual(scoped(self.db,"BA","55660"),[])
        self.assertEqual(scoped(self.db,"AC","55660",1),[])
        evidence = self.manifest["validation"]
        self.assertEqual(evidence["shared_stops"],189)
        self.assertEqual(evidence["suffix_collision_groups"],364)
        self.assertEqual(evidence["canonical_mappings"]["shape"],2446)
        self.assertEqual(evidence["retained_flagged_points"],21)
        self.assertEqual(evidence["duplicate_identities"],0)
        self.assertEqual(evidence["dangling_endpoints"],0)


if __name__ == "__main__":
    unittest.main()

