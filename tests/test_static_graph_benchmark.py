"""Focused Phase 7 measurements, scheduling, and fail-closed artifact tests."""
from argparse import ArgumentParser, Namespace
from pathlib import Path
import tempfile
from time import perf_counter
import unittest

import duckdb

from benchmark_static_graph import (SEED, clock_summary, execution_order, outputs_equal, percentile, retain_samples,
                                    run, summarize_values, timed_sql, timeout_error, variant_list)
from design_static_graph import ROOT, read, write
from import_static_graph import ImportSafety
from static_graph_workloads import Workload


class BenchmarkTests(unittest.TestCase):
    def test_percentiles_and_sample_deviation(self):
        data=[0,10,20,30,40]
        summary=summarize_values(data)
        self.assertEqual(summary["p50"],20)
        self.assertAlmostEqual(summary["p95"],38)
        self.assertEqual(summary["iqr"],20)
        self.assertAlmostEqual(summary["sample_sd"],15.8113883008)
        self.assertIsNone(summarize_values([1])["sample_sd"])
        self.assertIsNone(summarize_values([]))
        with self.assertRaises(ImportSafety):
            percentile(data,1.1)

    def test_schedule_is_seeded_and_cycles_through_cases(self):
        from random import Random
        tasks=[Workload("a","",[{},{},{}],"SELECT 1","RETURN 1"),
               Workload("b","",[{}],"SELECT 1","RETURN 1","SELECT 1")]
        variants=variant_list(tasks)
        trace=list(execution_order(variants,Random(SEED),9))
        self.assertEqual(trace,list(execution_order(variants,Random(SEED),9)))
        self.assertEqual([c for _,w,e,c in trace if w.name=="a" and e=="neo4j"],[0,1,2]*3)
        self.assertEqual({e for w,e in variants if w.name=="b"},{"duckdb","hybrid"})

    def test_multiset_comparison_detects_type_and_duplicate_changes(self):
        self.assertTrue(outputs_equal([(1,"é"),(2,None)],[(2,None),(1,"é")]))
        self.assertFalse(outputs_equal([(True,"é")],[(1,"é")]))
        self.assertFalse(outputs_equal([(1,),(1,)],[(1,),(2,)]))
        self.assertFalse(outputs_equal([(1,None)],[(1,"")]))

    def test_duckdb_timeout_and_safe_reuse(self):
        with duckdb.connect(config={"threads":2}) as db:
            with self.assertRaises(duckdb.InterruptException) as raised:
                timed_sql(db,"SELECT sum(sin(i)) FROM range(1000000000) AS n(i)",{},.08)
            self.assertTrue(timeout_error(raised.exception))
            self.assertEqual(timed_sql(db,"SELECT 1",{},5)[0],[(1,)])
            self.assertFalse(timeout_error(ValueError("non-timeout")))

    def test_raw_samples_and_rate_include_failed_executions(self):
        base={"workload":"fixture","engine":"duckdb","phase":"warm_varying"}
        records=[{**base,"order":1,"status":"success","rows":2,"total_ms":12},
                 {**base,"order":2,"status":"timeout","total_ms":100},
                 {**base,"order":3,"status":"incorrect","rows":2,"total_ms":20}]
        stats=clock_summary(records)[0]
        self.assertEqual(stats["failure_rate"],2/3)
        self.assertEqual(stats["timeout_rate"],1/3)
        self.assertEqual(stats["statistics"]["total_ms"]["p95"],12)
        with tempfile.TemporaryDirectory(dir=ROOT/"reports") as td, duckdb.connect() as db:
            path=Path(td)/"samples.parquet"
            retain_samples(db,path,records)
            self.assertEqual(db.read_parquet(str(path)).select("status,total_ms").fetchall(),
                             [("success",12),("timeout",100),("incorrect",20)])

    def test_existing_output_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"reports") as td:
            path=Path(td)
            marker=path/"static_invariance_report.json"
            marker.write_bytes(b"previous validation")
            with self.assertRaisesRegex(ImportSafety,"new output"):
                run(Namespace(output=path))
            self.assertEqual(marker.read_bytes(),b"previous validation")


def report_suite(directory):
    sources = ["test_profile_gtfs.py", "test_normalize_gtfs.py", "test_static_graph.py",
               "test_graph_ready.py", "test_static_graph_import.py", "test_static_graph_benchmark.py"]
    suites = {}
    for phase,filename in enumerate(sources,2):
        start = perf_counter()
        loader = unittest.TestLoader()
        result = unittest.TextTestRunner(verbosity=1).run(loader.discover(str(ROOT/"scripts"),pattern=filename))
        failed, errors, skipped = len(result.failures),len(result.errors),len(result.skipped)
        suites[str(phase)] = {"ran":result.testsRun,"passed":result.testsRun-failed-errors-skipped,
                             "failed":failed,"errors":errors,"skipped":skipped,"seconds":round(perf_counter()-start,3)}
    if read(directory/"static_invariance_report.json")["status"] != "verified":
        raise ValueError("Live invariance evidence is missing")
    if read(directory/"benchmark_environment.json")["status"] != "validated":
        raise ValueError("Live benchmark did not pass acceptance")
    regional = {"passed":["full typed pre and post audit, graph and input invariance",
                          "14 approved regional Cypher contracts","additional canonical, dated trip and route-name cases",
                          "mandatory AC path and hybrid regression","all paired benchmark case outputs and selected plans"],
                "failed":[],"skipped":["controlled cold-cache restart","optional concurrency",
                                       "optional alternative architecture writes","optional live interruption recovery"],
                "blocked":[]}
    write(directory/"automated_test_report.json",{"offline_suites":suites,"regional_live":regional})
    return any(r["failed"] or r["errors"] or r["skipped"] for r in suites.values())


if __name__=="__main__":
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path,help="Run all offline suites and write a report beside live evidence")
    args=parser.parse_args()
    if args.report:
        raise SystemExit(report_suite(args.report))
    unittest.main(argv=[__file__])

