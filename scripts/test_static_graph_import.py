"""Offline safety and recovery fixtures. Regional checks are run by the live importer."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import duckdb
from neo4j.exceptions import ServiceUnavailable
from design_static_graph import ROOT, read, sha
from import_static_graph import (READY, PINNED_HASH, ImportSafety, batch_queries, checkpoint, commit_batch, finished_report, payload,
                                 retryable, target_settings, verify_ready_files, write_batch)
from transform_static_graph import FORMAT, SCHEMA_FILE
from verify_imported_graph import typed_properties, verify_content, assert_catalog


SCHEMA = read(SCHEMA_FILE)
TRANSFER = next(r for r in SCHEMA["relationships"] if r["type"]=="TRANSFER_RULE")


class SafetyTests(unittest.TestCase):
    def test_explicit_target_and_no_default_or_remote_server(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(target_settings("transitkg-static"),"neo4j://127.0.0.1:7687")
            for forbidden in (None,"","system","neo4j","transitkg-x`db","transitkg-x.y"):
                with self.assertRaises(ImportSafety):
                    target_settings(forbidden)
            os.environ["NEO4J_DATABASE"]="some-other-target"
            with self.assertRaises(ImportSafety):
                target_settings("transitkg-static")
            os.environ["NEO4J_URI"]="neo4j://example.invalid:7687"
            with self.assertRaises(ImportSafety):
                target_settings("transitkg-static")

    def test_manifest_inventory_and_corrupt_artifact_rejected(self):
        verify_ready_files(READY,read(READY/"manifest.json"))
        with tempfile.TemporaryDirectory() as temp:
            decoy = Path(temp)
            (decoy/"manifest.json").write_text("{}")
            f = decoy/"test.parquet"
            f.write_bytes(b"one")
            manifest = {"artifacts":{"test.parquet":{"bytes":3,"sha256":sha(f)}}}
            verify_ready_files(decoy,manifest)
            f.write_bytes(b"two")
            with self.assertRaisesRegex(ImportSafety,"checksum"):
                verify_ready_files(decoy,manifest)
            (decoy/"unexpected").touch()
            with self.assertRaisesRegex(ImportSafety,"inventory"):
                verify_ready_files(decoy,manifest)

    def test_partial_target_requires_matching_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp)/"checkpoint"
            partial = {"nodes":1,"relationships":0}
            pin = {"build_id":"one","database_id":"target"}
            with self.assertRaisesRegex(ImportSafety,"Nonempty"):
                checkpoint(marker,pin,partial)
            self.assertFalse(marker.exists())
            self.assertFalse(checkpoint(marker,pin,{"nodes":0,"relationships":0}))
            self.assertTrue(checkpoint(marker,pin,partial))
            with self.assertRaisesRegex(ImportSafety,"incompatible"):
                checkpoint(marker,{**pin,"database_id":"different instance"},partial)
            self.assertEqual(read(marker),pin)

    def test_schema_name_property_and_index_state(self):
        schema = {"nodes":[{"label":"Stop","identity":"canonical_id"}],"indexes":[("Stop","source_id")]}
        constraint = {"name":"uq_Stop","type":"UNIQUENESS","entityType":"NODE",
                      "labelsOrTypes":["Stop"],"properties":["canonical_id"]}
        base = {"type":"RANGE","entityType":"NODE","labelsOrTypes":["Stop"],"state":"ONLINE"}
        indexes = [{**base,"name":"uq_Stop","properties":["canonical_id"],"owningConstraint":"uq_Stop"},
                   {**base,"name":"ix_Stop_source_id","properties":["source_id"],"owningConstraint":None}]
        assert_catalog([constraint],indexes,schema)
        with self.assertRaisesRegex(ValueError,"schema constraints"):
            assert_catalog([{**constraint,"properties":["source_id"]}],indexes,schema)
        with self.assertRaisesRegex(ValueError,"offline"):
            assert_catalog([constraint],[indexes[0],{**indexes[1],"state":"POPULATING"}],schema)
        with self.assertRaisesRegex(ValueError,"Missing"):
            assert_catalog([constraint],indexes[:1],schema)

    def test_failed_construction_cannot_publish_or_replace_success(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"reports/phase6") as temp:
            path = Path(temp)/"static_graph_manifest.json"
            with self.assertRaises(ImportSafety):
                finished_report({"status":"failed"},path)
            self.assertFalse(path.exists())
            report = {"status":"validated","verification":{"imported_fingerprint":PINNED_HASH},
                      "inputs":{"build":"pinned"},"target_identity":{"id":"id1"}}
            finished_report(report,path)
            finished_report({**report,"extra":"later attempt"},path)
            self.assertEqual(read(path),report)
            with self.assertRaisesRegex(ImportSafety,"another database"):
                finished_report({**report,"target_identity":{"id":"id2"}},path)
            self.assertEqual(read(path),report)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.values = {"start_id":"S","end_id":"T","from_route_id":"A","to_route_id":None,
                       "from_trip_id":None,"to_trip_id":None,"transfer_type":3,"min_transfer_time":None}

    def test_typed_payload_preserves_empty_strings_and_zero(self):
        item = {"properties":{"text":{"type":"STRING","required":True},
                              "time":{"type":"INTEGER","required":True},
                              "optional":{"type":"STRING","required":False}}}
        self.assertEqual(payload({"text":"","time":0,"optional":None},item),{"text":"","time":0})
        for num in (False,2**63,"0",None):
            with self.assertRaises(ValueError):
                payload({"text":"é","time":num,"optional":None},item)
        with self.assertRaisesRegex(ValueError,"Undeclared"):
            typed_properties({"unknown":"x"},item["properties"])

    def test_bounded_batch_counts_and_scoped_replay(self):
        lookup,create = batch_queries("relationships","TRANSFER_RULE",TRANSFER)
        self.assertIn("a:Stop",lookup)
        self.assertIn("b:Stop",create)
        for field in TRANSFER["identity"][3:]:
            self.assertIn(f"r.{field} IS NULL AND row.props.{field} IS NULL",lookup)
        tx = Mock()
        tx.run.side_effect = iter([[{"ordinal":0,"existing":[]}],Mock(**{"single.return_value":{"created":1}})])
        first = write_batch(tx,"relationships","TRANSFER_RULE",TRANSFER,[self.values])
        self.assertEqual(first,{"input":1,"created":1,"matched":0})
        self.assertEqual(tx.run.call_args.kwargs["rows"][0]["props"],{"from_route_id":"A","transfer_type":3})
        tx.reset_mock()
        tx.run.side_effect = [[{"ordinal":0,"existing":[{"from_route_id":"A","transfer_type":3}]}]]
        again = write_batch(tx,"relationships","TRANSFER_RULE",TRANSFER,[self.values])
        self.assertEqual(again,{"input":1,"created":0,"matched":1})
        self.assertEqual(tx.run.call_count,1)

    def test_missing_endpoint_duplicates_and_payload_conflict_abort_before_create(self):
        for existing,diagnostic in (([],"missing typed endpoints"),
              ([{"ordinal":0,"existing":[{},{}]}],"Duplicate relationship"),
              ([{"ordinal":0,"existing":[{"transfer_type":0}]}],"conflicting properties")):
            tx = Mock()
            tx.run.return_value = existing
            with self.assertRaisesRegex(ImportSafety,diagnostic):
                write_batch(tx,"relationships","TRANSFER_RULE",TRANSFER,[self.values])
            self.assertEqual(tx.run.call_count,1)

    def test_uncertain_commit_retries_once_and_nontransient_stops(self):
        self.assertTrue(retryable(ServiceUnavailable("simulated disconnect")))
        self.assertFalse(retryable(ImportSafety("simulated conflict")))
        session = Mock()
        from contextlib import nullcontext
        first,second = Mock(),Mock()
        first.commit.side_effect = ServiceUnavailable("simulated after committing")
        session.begin_transaction.side_effect = [nullcontext(first),nullcontext(second)]
        work = Mock(side_effect=[{"input":1,"created":1,"matched":0},{"input":1,"created":0,"matched":1}])
        stats = {"attempts":0,"committed":0,"retries":0,"failures":[]}
        with patch("import_static_graph.sleep"):
            result = commit_batch(session,work,5,1,stats,"Stop",1,1)
        self.assertEqual((result["created"],result["matched"],stats["retries"],stats["committed"]),(0,1,1,1))
        self.assertEqual(stats["failures"][0]["input"],1)
        self.assertEqual(session.begin_transaction.call_count,2)
        session.begin_transaction.side_effect = [nullcontext(second)]
        work.side_effect = ImportSafety("conflict")
        with self.assertRaisesRegex(ImportSafety,"conflict"):
            commit_batch(session,work,5,2,stats,"Stop",2,1)
        self.assertFalse(stats["failures"][-1]["retryable"])


def result(rows):
    response = Mock()
    response.__iter__ = Mock(return_value=iter(rows))
    response.single.return_value = rows[0] if rows else None
    return response


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        fields = {"canonical_id":{"type":"STRING","required":True},"name":{"type":"STRING","required":False}}
        self.schema = {"nodes":[{"label":"Stop","identity":"canonical_id","properties":fields}],
                       "relationships":[{"type":"LINK","from":"Stop","to":"Stop",
                           "identity":["type","start.canonical_id","end.canonical_id"],"properties":{}}]}
        self.node_rows = [{"labels":["Stop"],"props":{"canonical_id":"S","name":"Cafe é"}},
                          {"labels":["Stop"],"props":{"canonical_id":"T"}}]
        self.edge_rows = [{"start_labels":["Stop"],"end_labels":["Stop"],"start_id":"S","end_id":"T","props":{}}]
        records = [["N","Stop","S",{"canonical_id":"S","name":"Cafe é"}],
                   ["N","Stop","T",{"canonical_id":"T","name":None}],
                   ["R","LINK",["LINK","S","T"],{}]]
        def digest(items):
            records = sorted(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode() for r in items)
            return hashlib.sha256(FORMAT.encode()+b"\n"+b"\n".join(records)+b"\n").hexdigest()
        self.manifest = {"expected_static_graph_fingerprint":digest(records),"artifacts":{
            "nodes/Stop.parquet":{"records":2,"logical_sha256":digest(records[:2])},
            "relationships/LINK.parquet":{"records":1,"logical_sha256":digest(records[2:])}}}
        self.db=duckdb.connect()
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE graph_Stop AS SELECT 'S' canonical_id,'Cafe é' AS name UNION ALL SELECT 'T',NULL")
        self.db.execute("CREATE TABLE graph_LINK AS SELECT 'S' start_id,'T' end_id")

    def verify(self,partial=False,global_nodes=None):
        tx=Mock()
        tx.run.side_effect = lambda query: {
            "MATCH (n:Stop)":result(self.node_rows),"MATCH (a)-[r:LINK]->(b)":result(self.edge_rows),
            "MATCH (n)":result([{"n":len(self.node_rows) if global_nodes is None else global_nodes}]),
            "MATCH ()-[r]->()":result([{"n":len(self.edge_rows)}]),
        }[query.split(" RETURN",1)[0].strip()]
        return verify_content(tx,self.db,self.schema,self.manifest,partial=partial)

    def test_independent_reconstruction_and_unknown_records(self):
        self.assertEqual(self.verify()["imported_fingerprint"],self.manifest["expected_static_graph_fingerprint"])
        self.node_rows.reverse()
        self.assertEqual(self.verify()["imported_fingerprint"],self.manifest["expected_static_graph_fingerprint"])
        with self.assertRaisesRegex(ValueError,"Undeclared nodes"):
            self.verify(global_nodes=3)

    def test_same_count_property_and_endpoint_changes_are_detected(self):
        self.node_rows[0]["props"]["name"]="changed"
        with self.assertRaisesRegex(ValueError,"changed.*1"):
            self.verify()
        self.node_rows[0]["props"]["name"]="Cafe é"
        self.edge_rows[0]["end_id"]="S"
        with self.assertRaisesRegex(ValueError,"LINK database/Parquet mismatch"):
            self.verify()
        self.edge_rows[0]["end_labels"]=["Wrong"]
        with self.assertRaisesRegex(ValueError,"incorrectly typed endpoints"):
            self.verify()

    def test_recovery_verifies_exact_subset_without_claiming_readiness(self):
        self.node_rows.pop()
        self.edge_rows.clear()
        before = self.verify(partial=True)
        self.assertIsNone(before["imported_fingerprint"])
        self.assertEqual(before["counts"]["nodes"]["Stop"],1)
        self.node_rows[0]["props"]["name"]="unexpected"
        with self.assertRaisesRegex(ValueError,"changed.*1"):
            self.verify(partial=True)


if __name__=="__main__":
    unittest.main()

