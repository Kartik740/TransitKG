"""Focused fixture tests and bounded checks of the published regional build."""

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote

import duckdb

from gtfs_checks import literal, q
from gtfs_normalized import canonical_expression, scoped_stop
from normalize_gtfs import build, read_json, source_manifest
from profile_gtfs import sha256

FIXTURE = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                  "AC,AC Transit,https://example.org/ac,America/Los_Angeles\n"
                  "ZZ,Shared Stops Transit,https://example.org/zz,America/Los_Angeles\n",
    "stops.txt": "stop_id,stop_name,stop_code,stop_lat,stop_lon,location_type,platform_code\n"
                 "55660,San Pablo Av & W Grand Av,55660,37.81,-122.27,0,\"\"\n"
                 "52227,San Pablo Av & W Grand Av,52227,37.82,-122.28,\"\",\n"
                 "001,Zero test,,37.83,-122.29,,\nAC:001,Namé,,37.84,-122.30,0,\n",
    "routes.txt": "route_id,agency_id,route_type,route_short_name\n"
                  "AC:72,AC,3,72\nZZ:072,ZZ,3,072\nAC:%3A:é,AC,3,É\n",
    "trips.txt": "trip_id,route_id,service_id,direction_id,trip_headsign,shape_id\n"
                 "0007,AC:72,AC:exception,0,Grand Ave,AC:S\n"
                 "ZZ:7,ZZ:072,ZZ:S,1,Shared Stop,\n",
    "stop_times.txt": "trip_id,stop_id,stop_sequence,arrival_time,departure_time,timepoint\n"
                      "0007,55660,1,25:30:00,25:30:00,1\n"
                      "0007,52227,2,,\"\",0\n"
                      "0007,55660,3,25:40:00,25:40:00,1\n"
                      "ZZ:7,55660,1,01:00:00,01:00:00,1\n"
                      "ZZ:7,AC:001,2,01:04:00,01:04:00,1\n",
    "calendar_dates.txt": "service_id,date,exception_type\nAC:exception,20261124,1\nZZ:S,20261124,1\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled\n"
                  "AC:S,37,-122,1,0\nAC:S,37,-122,2,0\nAC:S,38,-122,3,1\n",
    "attributions.txt": "organization_name,is_producer\nPublisher,1\nPublisher,1\n",
    "directions.txt": "route_id,direction_id,direction\nAC:72,0,Inbound\nAC:72,0,Outbound\nZZ:072,1,East\n",
    "route_attributes.txt": "route_id,category,subcategory,running_way\n"
                            "AC:72,1,101,1\nAC:72,1,101,1\nZZ:072,1,101,1\nZZ:072,2,201,2\n",
    "fare_products.txt": "fare_product_id,amount,currency\nFA:01,0.10,USD\nFA:02,0.20,USD\nFA:03,0,USD\n",
    "fare_leg_rules.txt": "leg_group_id,fare_product_id\nG1,FA:01\nG2,FA:02\n",
    "fare_transfer_rules.txt": "from_leg_group_id,to_leg_group_id,fare_product_id,transfer_count,"
                               "duration_limit,duration_limit_type,fare_transfer_type,filter_fare_product_id\n"
                               "G1,G2,FA:03,,600,0,0,FA:01\nG1,G2,FA:03,,600,0,0,FA:02\n"
                               "G2,G1,FA:03,,600,0,0,\n",
    "changes.md": "Synthetic non-tabular provenance.\n",
}


def read_views(db, output, names):
    for name in names:
        db.execute(f"CREATE TEMP VIEW {q(name)} AS SELECT * FROM read_parquet({literal(str(output / (name + '.parquet')))})")


class EncodingTests(unittest.TestCase):
    def test_exact_string_encoding_and_namespace_type_separation(self):
        raw = ["AC:001", "001", "1", "ac:001", "AC%3A001", "é", "e\u0301", " Stop / 5 "]
        with duckdb.connect() as db:
            expr = canonical_expression("fixture", "stop", "source_id")
            ids = [db.execute(f"SELECT {expr} FROM (SELECT ? AS source_id)", [source]).fetchone()[0] for source in raw]
            self.assertEqual(len(ids), len(set(ids)))
            for source, encoded in zip(raw, ids):
                self.assertEqual(len(encoded.split(":")), 3)
                self.assertEqual(unquote(encoded.split(":")[2]), source)
            self.assertEqual(ids[0], "fixture:stop:AC%3A001")
            self.assertEqual(ids[1], "fixture:stop:001")
            self.assertEqual(ids[4], "fixture:stop:AC%253A001")
            self.assertNotEqual(ids[2], db.execute("SELECT " + canonical_expression("fixture", "route", "'1'")).fetchone()[0])

    def test_manifest_is_order_independent_and_content_sensitive(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            a, b = directory / "a", directory / "b"
            a.mkdir()
            b.mkdir()
            for name, content in reversed(list(FIXTURE.items())):
                (a / name).write_text(content, encoding="utf-8")
            for name, content in FIXTURE.items():
                (b / name).write_text(content, encoding="utf-8")
            self.assertEqual(source_manifest(a, {})[1], source_manifest(b, {})[1])
            (b / "changes.md").write_text("One altered file.", encoding="utf-8")
            self.assertNotEqual(source_manifest(a, {})[1], source_manifest(b, {})[1])


class FixtureBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        cls.source, cls.output = root / "raw", root / "normalized"
        cls.source.mkdir()
        for name, content in FIXTURE.items():
            (cls.source / name).write_text(content, encoding="utf-8")
        cls.before = {name: sha256(cls.source / name) for name in FIXTURE}
        with redirect_stdout(StringIO()):
            cls.result = build(cls.source, cls.output, namespace="fixture", threads=1, memory_limit="256MB")
        cls.published = cls.result["path"]

    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)
        read_views(self.db, self.published, self.result["manifest"]["artifacts"])

    def test_raw_identifiers_names_and_unknown_values_survive(self):
        self.assertEqual(self.db.execute("SELECT stop_id FROM stops ORDER BY stop_id").fetchall(),
                         [("001",), ("52227",), ("55660",), ("AC:001",)])
        self.assertEqual(self.db.execute("SELECT stop_id, stop_name FROM stops WHERE stop_id='AC:001'").fetchone(),
                         ("AC:001", "Namé"))
        self.assertEqual(self.db.execute("SELECT platform_code, location_type FROM stops WHERE stop_id='55660'").fetchone(),
                         ("", 0))
        self.assertEqual(self.db.execute("SELECT platform_code, location_type, source_empty_typed_fields FROM stops "
                                         "WHERE stop_id='52227'").fetchone(), (None, None, ["location_type"]))
        ids = self.db.execute("SELECT canonical_id FROM canonical_ids WHERE entity_type='stop' ORDER BY source_identifier").fetchall()
        self.assertIn(("fixture:stop:001",), ids)
        self.assertIn(("fixture:stop:AC%3A001",), ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.result["integrity"]["canonical_mappings"]["stop_suffix_collision_groups_preserved"], 1)

    def test_repeated_visits_shared_agencies_and_scoped_selectors(self):
        agency_stops = self.db.execute("SELECT agency_id FROM agency_stops WHERE stop_id='55660' ORDER BY 1").fetchall()
        self.assertEqual(agency_stops, [("AC",), ("ZZ",)])
        self.assertEqual(self.db.execute("SELECT stop_sequence FROM stop_times WHERE trip_id='0007' AND stop_id='55660' "
                                         "ORDER BY 1").fetchall(), [(1,), (3,)])
        self.assertEqual(self.db.execute("SELECT scheduled_visits, scheduled_trips FROM route_stops "
                                         "WHERE route_id='AC:72' AND stop_id='55660'").fetchone(), (2, 1))
        exact = scoped_stop(self.db, "AC", stop_id="55660")
        neighbor = scoped_stop(self.db, "AC", stop_id="52227")
        self.assertEqual(exact["status"], "EXACT_MATCH")
        self.assertNotEqual(exact["candidates"][0]["canonical_stop_id"], neighbor["candidates"][0]["canonical_stop_id"])
        self.assertEqual(scoped_stop(self.db, "AC", stop_name="San Pablo Av & W Grand Av")["status"], "AMBIGUOUS_MATCH")
        self.assertEqual(scoped_stop(self.db, "ZZ", stop_id="52227")["status"], "NO_MATCH")
        self.assertEqual(scoped_stop(self.db, "ZZ", stop_name="San Pablo Av & W Grand Av")["status"], "CANDIDATE_MATCH")

    def test_service_exception_dates_and_extended_time(self):
        self.assertEqual(self.db.execute("SELECT source_tables FROM canonical_ids WHERE canonical_id='fixture:service:AC%3Aexception'").fetchone(),
                         (["calendar_dates.txt"],))
        self.assertEqual(self.db.execute("SELECT typeof(date) FROM calendar_dates LIMIT 1").fetchone(), ("DATE",))
        self.assertEqual(self.db.execute("SELECT arrival_time, arrival_time_seconds FROM stop_times WHERE trip_id='0007' "
                                         "AND stop_sequence=1").fetchone(), ("25:30:00", 91800))
        self.assertEqual(self.db.execute("SELECT arrival_time, departure_time, arrival_time_seconds, departure_time_seconds "
                                         "FROM stop_times WHERE trip_id='0007' AND stop_sequence=2").fetchone(), (None, "", None, None))
        self.assertEqual(self.db.execute("SELECT first_departure_seconds, frequency_defined FROM trip_selector_context "
                                         "WHERE trip_id='0007'").fetchone(), (91800, False))

    def test_semantic_quarantine_exact_duplicate_lineage_and_geometry(self):
        ledger = self.db.execute("""SELECT source_file, disposition, count(*) FROM row_exception_ledger
            GROUP BY ALL ORDER BY ALL""").fetchall()
        self.assertEqual(ledger, [("attributions.txt", "DEDUPLICATED", 1), ("directions.txt", "QUARANTINED", 2),
                                  ("fare_transfer_rules.txt", "QUARANTINED", 2),
                                  ("route_attributes.txt", "DEDUPLICATED", 1), ("route_attributes.txt", "QUARANTINED", 2),
                                  ("shapes.txt", "FLAGGED", 1)])
        evidence = self.db.execute("""SELECT source_snapshot_id, source_record, retained_source_record, source_row
            FROM row_exception_ledger WHERE source_file='attributions.txt'""").fetchone()
        self.assertEqual(evidence[:3], (self.result["manifest"]["source_snapshot_id"], 2, 1))
        self.assertEqual(json.loads(evidence[3])["organization_name"], "Publisher")
        self.assertEqual(self.db.execute("SELECT count(*) FROM shapes").fetchone(), (3,))
        self.assertEqual(self.db.execute("SELECT count(*) FROM fare_transfer_rules").fetchone(), (1,))
        self.assertEqual(self.db.execute("SELECT direction_id FROM trips WHERE trip_id='0007'").fetchone(), (0,))
        self.assertEqual(self.result["summary"]["totals"]["unaccounted"], 0)
        self.assertEqual(self.result["integrity"]["exception_ledger"]["errors"], 0)

    def test_repeat_builds_keep_canonical_mappings_and_raw_checksums(self):
        with redirect_stdout(StringIO()):
            again = build(self.source, self.output, namespace="fixture", threads=2, memory_limit="256MB")
        self.assertEqual(self.published, again["path"])
        self.assertTrue(again["summary"]["reused_existing_build"])
        self.assertEqual(self.result["integrity"]["canonical_mappings"], again["integrity"]["canonical_mappings"])
        self.assertEqual(self.before, {name: sha256(self.source / name) for name in FIXTURE})
        self.assertTrue(again["manifest"]["raw_checksums_unchanged"])
        self.assertTrue(again["integrity"]["passed"])

    def test_reordering_source_rows_changes_snapshot_but_preserves_entity_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "raw"
            source.mkdir()
            for name, content in FIXTURE.items():
                lines = content.splitlines(keepends=True)
                (source / name).write_text("".join([lines[0], *reversed(lines[1:])]), encoding="utf-8")
            with redirect_stdout(StringIO()):
                reordered = build(source, Path(temporary) / "output", namespace="fixture", threads=1, memory_limit="256MB")
            self.assertNotEqual(reordered["manifest"]["source_snapshot_id"], self.result["manifest"]["source_snapshot_id"])
            path = literal(str(reordered["path"] / "canonical_ids.parquet"))
            expected = self.db.execute("SELECT entity_type, source_identifier, canonical_id FROM canonical_ids ORDER BY ALL").fetchall()
            actual = self.db.execute(
                f"SELECT entity_type, source_identifier, canonical_id FROM read_parquet({path}) ORDER BY ALL").fetchall()
            self.assertEqual(expected, actual)

    def test_failed_references_leave_prior_build_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "raw"
            source.mkdir()
            for name, content in FIXTURE.items():
                if name == "trips.txt":
                    content = content.replace("0007,AC:72,", "0007,AC:orphan,")
                (source / name).write_text(content, encoding="utf-8")
            before = set(self.output.iterdir())
            with self.assertRaisesRegex(ValueError, "invalid_references"), redirect_stdout(StringIO()):
                build(source, self.output, namespace="fixture", threads=1, memory_limit="256MB")
            self.assertEqual(before, set(self.output.iterdir()))
            self.assertTrue((self.published / "manifest.json").exists())


class RegionalIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("TRANSITKG_PHASE3_BUILD"), "Published regional build path is required.")
    def test_published_ac_selector_and_distinct_neighbor(self):
        output = Path(os.environ["TRANSITKG_PHASE3_BUILD"])
        manifest = read_json(output / "manifest.json")
        self.assertTrue(manifest["raw_checksums_unchanged"])
        self.assertEqual(manifest["artifacts"]["stop_times"]["records"], 3_185_115)
        with duckdb.connect() as db:
            read_views(db, output, ["stops", "trip_selector_context", "stop_times", "route_stops", "agency_stops"])
            exact = scoped_stop(db, "AC", stop_id="55660")
            neighbor = scoped_stop(db, "AC", stop_id="52227")
            self.assertEqual(exact["status"], "EXACT_MATCH")
            self.assertEqual(len(exact["candidates"]), 1)
            stop = exact["candidates"][0]
            self.assertEqual(stop["stop_id"], "55660")
            self.assertEqual(stop["canonical_stop_id"], manifest["source_namespace"] + ":stop:55660")
            self.assertNotEqual(stop["canonical_stop_id"], neighbor["candidates"][0]["canonical_stop_id"])
            self.assertEqual([r["route_id"] for r in stop["routes"]], ["AC:72", "AC:72L", "AC:72M", "AC:802"])
            self.assertEqual(stop["distinct_trips"], 345)
            self.assertEqual(scoped_stop(db, "AC", stop_name=stop["stop_name"])["status"], "AMBIGUOUS_MATCH")
