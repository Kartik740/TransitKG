"""Focused synthetic regression tests for Phase 2."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import duckdb

from gtfs_checks import gtfs_seconds, key_profile, query, reference_profile, service_domain
from gtfs_network import classify_stop_match, service_views
from profile_gtfs import inventory_bytes, load_table, run


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)

    def test_identifier_text_quoted_empty_and_malformed_width(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trips.txt"
            path.write_text('trip_id,trip_short_name\n001,\n1,""\n002\n003,X\n', encoding="utf-8")
            entry = {"sql_table": "test_trips", **inventory_bytes(path)}
            self.assertEqual(load_table(self.db, "trips.txt", path, entry), ["trip_id", "trip_short_name"])
            self.assertEqual(entry["records"], 3)
            self.assertEqual(entry["rejected_records"], 1)
            self.assertEqual(self.db.execute("SELECT * FROM test_trips ORDER BY trip_id").fetchall(),
                             [("001", None), ("003", "X"), ("1", "")])

    def test_composite_key_exact_duplicate_conflict_and_missing(self):
        self.db.execute("""CREATE TABLE visits(trip_id VARCHAR, stop_sequence VARCHAR, stop_id VARCHAR);
            INSERT INTO visits VALUES ('001','1','A'), ('001','1','A'), ('001','01','B'),
                ('001','2','A'), ('1','1','A'), ('1',NULL,'X')""")
        table = {"sql_table": "visits", "path": "stop_times.txt", "records": 6}
        result, = key_profile(self.db, table, ["trip_id", "stop_sequence", "stop_id"],
                              ["trip_id", "stop_sequence"], "stop_times")
        self.assertEqual(result["unique_complete_keys"], 3)
        self.assertEqual(result["missing_required_key_records"], 1)
        self.assertEqual(result["duplicate_complete_key_groups"], 1)
        self.assertEqual(result["records_with_duplicate_complete_keys"], 3)
        self.assertEqual(result["exact_duplicate_excess_rows"], 1)
        self.assertEqual(result["conflicting_duplicate_groups"], 1)
        self.assertEqual(len(result["duplicate_examples"][0]["raw_row_examples"]), 2)

    def test_reference_integrity_does_not_multiply_duplicate_targets(self):
        self.db.execute("""CREATE TABLE visits(stop_id VARCHAR, location_id VARCHAR);
            INSERT INTO visits VALUES ('001',NULL), ('1',NULL), ('X',NULL), (NULL,NULL), (NULL,'zone');
            CREATE TABLE stops(stop_id VARCHAR);
            INSERT INTO stops VALUES ('001'), ('001'), ('1')""")
        report = reference_profile(self.db, {"sql_table": "visits", "path": "stop_times.txt",
            "columns": {"stop_id", "location_id"}}, "stop_id", "SELECT stop_id AS target_id FROM stops",
            ["stops.stop_id"], required="s.location_id IS NULL")
        self.assertEqual(report["valid_references"], 2)
        self.assertEqual(report["invalid_references"], 1)
        self.assertEqual(report["missing_required_references"], 1)
        self.assertEqual(report["blank_optional_references"], 1)
        self.assertEqual(report["integrity_percentage"], 50)
        self.assertEqual(report["invalid_examples"][0]["identifier"], "X")

    def test_service_domain_and_exception_only_services(self):
        self.db.execute("""CREATE TABLE exceptions(service_id VARCHAR, date VARCHAR, exception_type VARCHAR);
            INSERT INTO exceptions VALUES ('001','20261125','1'), ('002','20261126','2'),
            ('003','20260230','1'), ('004','20261127','1'), ('004','20261127','2')""")
        tables = {"calendar_dates": {"sql_table": "exceptions",
                                     "columns": dict.fromkeys(("service_id", "date", "exception_type"))}}
        service_domain(self.db, tables)
        self.assertEqual(query(self.db, "SELECT * FROM service_ids ORDER BY 1"), [{"service_id": "001"}, {"service_id": "004"}])
        self.assertEqual(service_views(self.db, tables), (False, True))
        self.assertEqual(self.db.execute("SELECT service_id, service_date::VARCHAR FROM effective_service_dates").fetchall(),
                         [("001", "2026-11-25")])

    def test_gtfs_service_time_after_midnight(self):
        cases = ["00:00:00", "9:08:07", "24:00:00", "25:15:30", "25:60:00", "2:3:04", "-1:00:00"]
        self.db.execute("CREATE TABLE sample_times (row_number INTEGER, v VARCHAR)")
        self.db.executemany("INSERT INTO sample_times VALUES (?, ?)", list(enumerate(cases)))
        results = [row[0] for row in self.db.execute(
            f"SELECT {gtfs_seconds('v')} FROM sample_times ORDER BY row_number").fetchall()]
        self.assertEqual(results, [0, 32887, 86400, 90930, None, None, None])

    def test_static_alert_classification_requires_agency_scope(self):
        exact = {"evidence": ["exact_stop_id"], "serving_agencies": ["AC"]}
        wrong = {"evidence": ["exact_stop_id"], "serving_agencies": ["SF"]}
        variant = {"evidence": ["delimited_suffix", "name_match"], "serving_agencies": ["AC"]}
        crosswalk = {"evidence": ["verified_crosswalk"], "serving_agencies": ["AC"]}
        self.assertEqual(classify_stop_match([exact, variant], "AC"), "EXACT_MATCH")
        self.assertEqual(classify_stop_match([wrong, variant], "AC"), "CANDIDATE_MATCH")
        self.assertEqual(classify_stop_match([wrong, crosswalk], "AC"), "VERIFIED_MAPPING")
        self.assertEqual(classify_stop_match([wrong, variant, variant], "AC"), "AMBIGUOUS_MATCH")
        self.assertEqual(classify_stop_match([wrong], "AC"), "NO_MATCH")

    def test_zip_integration_with_missing_optional_files(self):
        data = {
            "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nAC,AC Transit,https://example.org,America/Los_Angeles\n",
            "routes.txt": "route_id,agency_id,route_type,route_short_name\nAC:01,AC,3,1\n",
            "trips.txt": "trip_id,route_id,service_id,direction_id\n001,AC:01,S1,0\n",
            "calendar_dates.txt": "service_id,date,exception_type\nS1,20261124,1\n",
            "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n55660,San Pablo Av & W Grand Av,37.81,-122.27\n"
                         "055660,San Pablo Av & W Grand Av,37.82,-122.28\n",
            "stop_times.txt": "trip_id,stop_id,stop_sequence,arrival_time,departure_time\n"
                              "001,55660,1,24:59:00,25:00:00\n001,055660,2,25:03:00,25:03:00\n",
            "auxiliary/notes.md": "Synthetic input.\n"
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            archive = p / "feed.zip"
            with zipfile.ZipFile(archive, "w") as z:
                for name, body in data.items():
                    z.writestr(name, body)
            out = p / "report"
            with redirect_stdout(StringIO()):
                self.assertEqual(run(archive, out, "128MB", 1), 0)
            inv = json.loads((out / "dataset_inventory.json").read_text(encoding="utf-8"))
            self.assertTrue(inv["source"]["verified_unchanged"])
            self.assertEqual(len(inv["inventory"]), len(data))
            self.assertEqual(inv["absent_standard_files"]["frequencies.txt"], "Optional")
            self.assertEqual(inv["absent_standard_files"]["calendar.txt"], "Conditionally Required")
            rt = json.loads((out / "realtime_compatibility_report.json").read_text(encoding="utf-8"))
            self.assertEqual(rt["classification"], "EXACT_MATCH")
            self.assertEqual(rt["candidates"][1]["trip_examples"][0]["trip_id"], "001")
            network = json.loads((out / "temporal_network_graph.json").read_text(encoding="utf-8"))
            self.assertEqual(network["temporal"]["stop_times"]["departures_above_24h"], 2)


if __name__ == "__main__":
    unittest.main()
