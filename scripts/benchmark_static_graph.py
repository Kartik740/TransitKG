"""Reproduce the Phase 7 read-only audit and paired latency evaluation."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import random
from statistics import mean, stdev
import subprocess
from threading import Timer
from time import perf_counter, perf_counter_ns, process_time

import duckdb
import neo4j
from neo4j import Query, READ_ACCESS
from neo4j.exceptions import Neo4jError

from audit_static_graph import (PHASE6, TARGET, account_access, phase6_construction, read_result,
                                storage, structure, verify_target)
from design_static_graph import ROOT, read, sha, write
from import_static_graph import BUILD, READY, check, connect, pin_inputs, preflight, target_settings, verify_ready_files
from profile_gtfs import peak_rss_mib
from static_graph_workloads import workouts
from transform_static_graph import encode

SEED = 731511
SAME_PARAMETER = 10
MS = 1_000_000


def percentile(values, fraction):
    check(values and 0<=fraction<=1,"Percentile requires values and a fraction in [0,1]")
    ordered = sorted(values)
    k = (len(ordered)-1)*fraction
    i = int(k)
    return ordered[i] + (ordered[min(i+1,len(ordered)-1)]-ordered[i])*(k-i)


def summarize_values(values):
    if not values:
        return None
    return {"n":len(values),"min":min(values),"max":max(values),"mean":mean(values),
            "p50":percentile(values,.5),"p95":percentile(values,.95),
            "sample_sd":stdev(values) if len(values)>1 else None,
            "iqr":percentile(values,.75)-percentile(values,.25)}


def outputs_equal(actual, expected):
    return sorted(map(encode,actual)) == sorted(map(encode,expected))


def timed_sql(db, sql, params, timeout):
    watch = Timer(timeout,db.interrupt)
    watch.daemon = True
    watch.start()
    try:
        start = perf_counter_ns()
        records = db.execute(sql,params).fetchall()
        return records,(perf_counter_ns()-start)/MS
    finally:
        watch.cancel()
        watch.join()


def neo_query(session, query, params, timeout):
    start = perf_counter_ns()
    result = session.run(Query(query,timeout=timeout),params)
    submitted = perf_counter_ns()
    data,summary = read_result(result)
    end = perf_counter_ns()
    check(summary.query_type in ("r","s") or (summary.query_type is None and query.startswith("EXPLAIN ")),
          "Expected read-only Neo4j query summary")
    return data, {
        "neo4j_ms":(end-start)/MS,"neo4j_submit_ms":(submitted-start)/MS,
        "neo4j_consume_ms":(end-submitted)/MS,"neo4j_server_available_ms":summary.result_available_after,
        "neo4j_server_consumed_ms":summary.result_consumed_after,
    },summary


def execute(session, db, workload, backend, params, timeout):
    if backend == "duckdb":
        # DuckDB rejects unused named parameters. Every name is from this fixed workload.
        args = {k:v for k,v in params.items() if "$"+k in workload.sql}
        data,elapsed = timed_sql(db,workload.sql,args,timeout)
        return data,{"total_ms":elapsed,"duckdb_ms":elapsed}
    if backend == "neo4j":
        data,components,_ = neo_query(session,workload.cypher,params,timeout)
        return data,{**components,"total_ms":components["neo4j_ms"]}
    start = perf_counter_ns()
    context,components,_ = neo_query(session,workload.cypher,params,timeout)
    bridge = perf_counter_ns()
    if workload.name=="scoped_service_day":
        check(len({row[1] for row in context})<=1,"Scoped graph lookup returned different stops")
        args = {"route_ids":[r[0] for r in context],
                "canonical_stop":context[0][1] if context else None,
                "direction":params["direction"],"day":params["day"]}
    else:
        check(len(context)<=1,"Trip lookup returned multiple candidates")
        args = {"trip":context[0][0] if context else None,"service":context[0][1] if context else None,
                "day":params["day"]}
    transfer = perf_counter_ns()
    data,elapsed = timed_sql(db,workload.hybrid_sql,args,timeout)
    end = perf_counter_ns()
    return data,{**components,"total_ms":(end-start)/MS,"duckdb_ms":elapsed,
                 "bridge_ms":(transfer-bridge)/MS,"candidate_rows":len(context)}


def timeout_error(exc):
    code = getattr(exc,"code","") or ""
    return isinstance(exc, (duckdb.InterruptException, TimeoutError)) or "TimedOut" in code or "Timeout" in code


def variant_list(workloads):
    return [(w,backend) for w in workloads for backend in
            (("duckdb","hybrid") if w.hybrid_sql else ("duckdb","neo4j") if w.cypher else ("duckdb",))]


def execution_order(variants,rng,rounds):
    for i in range(rounds):
        current = list(variants)
        rng.shuffle(current)
        for workload,engine in current:
            yield i,workload,engine,i%len(workload.cases)


def benchmark(session, db, workloads, args, samples):
    variants = variant_list(workloads)
    expected = {}
    correct = []
    rng = random.Random(SEED)
    first = []

    def run(stage,iteration,workload,engine,case):
        params = workload.cases[case]
        row = {"case":case,"workload":workload.name,"engine":engine,"phase":stage,"iteration":iteration,
               "order":len(samples)+1,"params_json":encode(params).decode(),"status":"success"}
        start = perf_counter_ns()
        try:
            result,timing = execute(session,db,workload,engine,params,args.timeout)
            row.update(timing,rows=len(result))
        except (Neo4jError,duckdb.Error,TimeoutError,neo4j.exceptions.DriverError) as exc:
            row.update(total_ms=(perf_counter_ns()-start)/MS,status="timeout" if timeout_error(exc) else "error",
                       error_type=type(exc).__name__,error_code=getattr(exc,"code",None))
            result = None
        samples.append(row)
        return result,row

    def validate_result(workload,case,result,row):
        if result is None:
            return
        equivalent = outputs_equal(result,expected[(workload.name,case)])
        row["correct"] = equivalent
        if not equivalent:
            row["status"] = "incorrect"

    # First observed executions occur before preparing exact reference SQL results for these workload texts.
    for i,w,e,c in execution_order(variants,rng,1):
        result,row=run("initial",i,w,e,c)
        first.append((w,c,result,row))

    for w in workloads:
        for c,case in enumerate(w.cases):
            result,_ = timed_sql(db,w.sql,{k:v for k,v in case.items() if "$"+k in w.sql},args.timeout)
            expected[w.name,c] = result
            digest = hashlib.sha256(b"\n".join(sorted(map(encode,result)))).hexdigest()
            correct.append({"query":w.name,"purpose":w.purpose,"case":c,"params":case,
                "expected_rows":len(result),"expected_sha256":digest,"expected_sample":result[:5],
                "reference":"normalized DuckDB SQL","observed":{}})
    for w,c,data,row in first:
        validate_result(w,c,data,row)
    for stage,rounds in (("warmup",args.warmup),("warm_varying",args.iterations)):
        for i,w,e,c in execution_order(variants,rng,rounds):
            result,row=run(stage,i,w,e,c)
            validate_result(w,c,result,row)
    repeated=[(w,e) for w,e in variants if w.name in ("canonical_stop","scoped_service_day")]
    for i,w,e,_ in execution_order(repeated,rng,SAME_PARAMETER):
        result,row=run("warm_same_parameter",i,w,e,0)
        validate_result(w,0,result,row)

    by_key={(v["query"],v["case"]):v for v in correct}
    for row in samples:
        case = by_key[row["workload"],row["case"]]
        entry = case["observed"].setdefault(row["engine"],{"success":0,"failure":0})
        entry["success" if row["status"]=="success" else "failure"] += 1
        if row["status"]=="success":
            entry["rows"]=row["rows"]
            entry["matches_reference"]=row["correct"]
    return correct


def clock_summary(samples):
    groups=defaultdict(list)
    for row in samples:
        groups[row["workload"],row["engine"],row["phase"]].append(row)
    metrics=("total_ms","neo4j_ms","neo4j_submit_ms","neo4j_consume_ms","neo4j_server_available_ms",
             "neo4j_server_consumed_ms","duckdb_ms","bridge_ms","candidate_rows","rows")
    return [{**dict(zip(("workload","engine","phase"),key)),
        "executions":len(rows),"failure_count":sum(r["status"]!="success" for r in rows),
        "timeout_count":sum(r["status"]=="timeout" for r in rows),
        "failure_rate":sum(r["status"]!="success" for r in rows)/len(rows),
        "timeout_rate":sum(r["status"]=="timeout" for r in rows)/len(rows),
        "statistics":{attr:summarize_values([r[attr] for r in rows if r["status"]=="success" and r.get(attr) is not None])
                      for attr in metrics if any(r.get(attr) is not None for r in rows)}}
        for key,rows in sorted(groups.items())]


def plans(session,db,workloads,timeout):
    selected={"canonical_stop":0,"route_stops":1,"agency_stop":0,"stop_name":1,"transfer_rules":0,
              "scoped_service_day":0,"trip_service_visits":0}
    results=[]
    for w in workloads:
        if w.name not in selected:
            continue
        args=w.cases[min(selected[w.name],len(w.cases)-1)]
        _,_,explain=neo_query(session,"EXPLAIN "+w.cypher,args,timeout)
        _,_,profile=neo_query(session,"PROFILE "+w.cypher,args,timeout)
        sql_args={k:v for k,v in args.items() if "$"+k in w.sql}
        sql,_=timed_sql(db,"EXPLAIN ANALYZE "+w.sql,sql_args,timeout)
        results.append({"workload":w.name,"parameters":args,"neo4j_explain":explain.plan,
                        "neo4j_profile":profile.profile,"duckdb_explain_analyze":sql})
    return results


def baseline_comparison():
    architecture=read(ROOT/"reports/phase4/architecture_comparison.json")
    compact=architecture["alternatives"]["B_compact"]
    return {key:{**counts,"compact_node_reduction_pct":100*(1-compact["nodes"]/counts["nodes"]),
                          "compact_edge_reduction_pct":100*(1-compact["relationships"]/counts["relationships"]),
                          "performance_measured_in_phase7":key=="B_compact"}
            for key,counts in architecture["alternatives"].items()}


def protocol(workloads,args):
    return {
        "seed":SEED,"warmups_per_variant":args.warmup,"measured_varying_per_variant":args.iterations,
        "initial_per_variant":1,"same_parameter_per_selected_variant":SAME_PARAMETER,"worker_count":1,
        "timeout_seconds_per_engine":args.timeout,"query_retries":0,"duckdb_threads":2,"duckdb_memory_limit":"768MB",
        "sampling":"Deterministic observed extrema and fixed regression/negative cases. Round-robin cases; seeded variant order by round.",
        "routing":"Dedicated target, READ_ACCESS, one reused driver/session, one in-memory DuckDB view connection.",
        "cache":"Initial observed after integrity audit and case selection on a running server. Warm varying and warm same-case measured separately.",
        "timing":"perf_counter_ns. Fully materialized rows. Neo4j includes transport and decoding. DuckDB executes and fetches locally. Hybrid total includes both and conversion. Separate Bolt summary timings are retained.",
        "validation":"Complete sorted JSON row multisets compared outside timed regions against normalized Parquet SQL.",
        "statistics":"Linear interpolation over sorted rank (n-1)*p; sample standard deviation. No p99 at this sample size.",
        "errors":"Sanitized error type and code retained, no automatic retries. Incorrect or unsuccessful timed queries fail acceptance.",
        "plans":"EXPLAIN and PROFILE/EXPLAIN ANALYZE run after timings and stored separately.",
        "workloads":[{"name":w.name,"purpose":w.purpose,"cases":w.cases,"sql":w.sql,
                      "cypher":w.cypher or None,"hybrid_sql":w.hybrid_sql or None} for w in workloads]}


SAMPLE_COLUMNS = {"case":"INTEGER","workload":"VARCHAR","engine":"VARCHAR","phase":"VARCHAR",
    "iteration":"INTEGER","order":"INTEGER","params_json":"VARCHAR","status":"VARCHAR",
    "total_ms":"DOUBLE","neo4j_ms":"DOUBLE","neo4j_submit_ms":"DOUBLE","neo4j_consume_ms":"DOUBLE",
    "neo4j_server_available_ms":"DOUBLE","neo4j_server_consumed_ms":"DOUBLE","duckdb_ms":"DOUBLE",
    "bridge_ms":"DOUBLE","candidate_rows":"INTEGER","rows":"INTEGER","correct":"BOOLEAN",
    "error_type":"VARCHAR","error_code":"VARCHAR"}


def retain_samples(db,path,samples):
    fields=", ".join(f'"{key}" {kind}' for key,kind in SAMPLE_COLUMNS.items())
    db.execute(f"CREATE TEMP TABLE benchmark_samples ({fields})")
    if samples:
        db.executemany(f"INSERT INTO benchmark_samples VALUES ({','.join('?' for _ in SAMPLE_COLUMNS)})",
                       [tuple(row.get(k) for k in SAMPLE_COLUMNS) for row in samples])
    db.execute('COPY (SELECT * FROM benchmark_samples ORDER BY "order") TO ? (FORMAT PARQUET)',[str(path)])


def host_info():
    output={"os":platform.platform(),"python":platform.python_version(),"cpu":platform.processor(),
            "logical_cpus":os.cpu_count(),"cpu_available_to_process":os.process_cpu_count()}
    if os.name=="nt":
        import ctypes
        import winreg
        class Memory(ctypes.Structure):
            _fields_=[("length",ctypes.c_ulong),("load",ctypes.c_ulong),
                      *[(name,ctypes.c_ulonglong) for name in
                        ("total","available","total_page","available_page","total_virtual","available_virtual","extended")]]
        memory=Memory()
        memory.length=ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            output.update(total_ram_bytes=memory.total,available_ram_bytes=memory.available)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                output["cpu"]=winreg.QueryValueEx(key,"ProcessorNameString")[0].strip()
        except OSError:
            pass
    return output


def server_resources(pid):
    if os.name!="nt" or not pid:
        return {"status":"unavailable","reason":"Verified Neo4j OS process PID was not provided"}
    command=(f"$p=Get-Process -Id {int(pid)} -ErrorAction Stop; "
             "[pscustomobject]@{pid=$p.Id;process_name=$p.ProcessName;cpu_seconds=$p.CPU;"
             "working_set_bytes=$p.WorkingSet64;peak_working_set_bytes=$p.PeakWorkingSet64} | ConvertTo-Json -Compress")
    try:
        result=json.loads(subprocess.check_output(["powershell","-NoProfile","-Command",command],
                                                  text=True,timeout=5,stderr=subprocess.DEVNULL))
    except (OSError,subprocess.SubprocessError,ValueError):
        return {"status":"unavailable","reason":"Unable to observe the verified Neo4j server process"}
    return {"status":"measured",**result}


def spill_bytes(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def run(args):
    out=args.output.resolve()
    check(not out.exists(),"Use a new output directory for each audit and benchmark run")
    out.mkdir(parents=True)
    start,cpu=perf_counter(),process_time()
    began=datetime.now(timezone.utc).isoformat()
    samples=[]
    spill=out/"duckdb-spill"
    baseline_path=PHASE6/"static_graph_manifest.json"
    baseline_digest=sha(baseline_path)
    baseline=read(baseline_path)
    check(baseline["status"]=="validated","Phase 6 manifest is not validated")
    input_file=READY/"manifest.json"
    manifest_digest=sha(input_file)
    check(baseline["inputs"]["graph_ready_manifest_sha256"]==manifest_digest,"Phase 6 graph-ready manifest digest differs")
    uri=target_settings(TARGET)
    with duckdb.connect(config={"threads":2,"memory_limit":"768MB","temp_directory":str(spill)}) as db, connect(uri) as driver:
        ready,normalized,schema=preflight(db,READY,BUILD,ROOT/"GTFSTransitData_RG")
        for key in ("source_snapshot_id","normalized_build_id","schema_sha256","canonical_contract_version"):
            check(baseline["inputs"][key]==ready["inputs"][key],f"Phase 6 {key} differs")
        connection_at=perf_counter()
        driver.verify_connectivity()
        connect_seconds=perf_counter()-connection_at
        with driver.session(database="system",default_access_mode=READ_ACCESS) as system:
            access=account_access(system)
        with driver.session(database=TARGET,default_access_mode=READ_ACCESS) as session:
            before=verify_target(driver,session,baseline,db,ready,normalized,schema,full_semantics=True)
            write(out/"audit_before.json",before)
            with session.begin_transaction(timeout=180) as tx:
                network=structure(tx,schema)
            write(out/"structural_analysis.json",network)
            workloads=workouts(db)
            write(out/"benchmark_protocol.json",protocol(workloads,args))
            settings=session.run("""SHOW SETTINGS YIELD name,value WHERE name IN [
                'server.memory.heap.initial_size','server.memory.heap.max_size','server.memory.pagecache.size']
                RETURN name,value""").data()
            server_start=server_resources(args.server_pid)
            bench_at,bench_cpu=perf_counter(),process_time()
            error=None
            try:
                cases=benchmark(session,db,workloads,args,samples)
                write(out/"competency_cases.json",cases)
            except Exception as exc:
                error=exc
            bench_wall,bench_cpu=perf_counter()-bench_at,process_time()-bench_cpu
            server_end=server_resources(args.server_pid)
            retain_samples(db,out/"query_benchmark_samples.parquet",samples)
            write(out/"query_benchmark_results.json",clock_summary(samples))
            if not error:
                try:
                    write(out/"query_plan_analysis.json",plans(session,db,workloads,args.timeout))
                except Exception as exc:
                    error=exc
            write(out/"storage_measurement.json",storage(session,normalized,ready,baseline))
            after=verify_target(driver,session,baseline,db,ready,normalized,schema,full_semantics=False)
            write(out/"audit_after.json",after)
            verify_ready_files(READY,ready)
            normalized_after,_,_=pin_inputs(BUILD,ROOT/"GTFSTransitData_RG")
            check(normalized_after==normalized and sha(input_file)==manifest_digest and sha(baseline_path)==baseline_digest,
                  "An immutable manifest or dataset changed during benchmarking")
            fingerprints={"phase5_expected":ready["expected_static_graph_fingerprint"],
                "phase6_imported":baseline["verification"]["imported_fingerprint"],
                "phase7_before":before["content"]["imported_fingerprint"],
                "phase7_after":after["content"]["imported_fingerprint"]}
            check(len(set(fingerprints.values()))==1,"Four-way fingerprint comparison failed")
            check(before["schema"]["constraints"]==after["schema"]["constraints"],"Constraint catalog changed")
            # Counters such as lastRead and readCount are expected to move while definitions stay fixed.
            keep=("name","type","entityType","labelsOrTypes","properties","owningConstraint","state","indexProvider")
            indexes=lambda audit:sorted(tuple(str(row.get(k)) for k in keep) for row in audit["schema"]["indexes"])
            check(indexes(before)==indexes(after),"Index catalog changed")
            write(out/"static_invariance_report.json",{"status":"verified","fingerprints":fingerprints,
                "immutable_source_files":{"raw":len(normalized["input_files"]),"normalized":len(normalized["artifacts"]),
                                          "graph_ready":len(ready["artifacts"])},
                "constituent_digests":after["content"]["partition_digests"],"counts":after["content"]["counts"],
                "schema_unchanged":True,"phase6_manifest_sha256":baseline_digest,
                "verified_graph_database_id":after["target"]["database"]["databaseID"]})
            measured_failures=sum(row["status"]!="success" for row in samples)
            environment={"run_started_utc":began,"finished_utc":datetime.now(timezone.utc).isoformat(),
                "status":"validated" if not error and not measured_failures else "benchmark failed despite static invariance",
                "host":host_info(),"server":before["target"],"listed_account_grants":access,"server_settings":settings,
                "driver_version":neo4j.__version__,"duckdb_version":duckdb.__version__,"duckdb_threads":2,
                "duckdb_memory_limit":"768MB","connection_verification_seconds":connect_seconds,
                "benchmark_wall_seconds":bench_wall,"benchmark_client_cpu_seconds":bench_cpu,
                "audit_and_benchmark_wall_seconds":perf_counter()-start,"client_cpu_seconds":process_time()-cpu,
                "client_peak_rss_mib":peak_rss_mib(),"duckdb_spill_bytes_at_end_of_audit":spill_bytes(spill),
                "server_process_at_benchmark_start":server_start,"server_process_at_benchmark_end":server_end,
                "implementation_sha256":{name:sha(ROOT/"scripts"/name) for name in
                    ("benchmark_static_graph.py","static_graph_workloads.py","audit_static_graph.py")}}
            write(out/"benchmark_environment.json",environment)
            write(out/"architecture_and_construction.json",{"architecture":baseline_comparison(),
                                                           "phase6_construction":phase6_construction(baseline,summarize_values)})
            if error:
                raise error
            check(not measured_failures,"Benchmark has failed or incorrect executions; inspect retained samples")
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"reports/phase7")
    parser.add_argument("--warmup",type=int,default=5)
    parser.add_argument("--iterations",type=int,default=30)
    parser.add_argument("--timeout",type=float,default=15)
    parser.add_argument("--server-pid",type=int)
    args=parser.parse_args()
    if not (0<=args.warmup<=25 and 30<=args.iterations<=120 and 1<=args.timeout<=120):
        parser.error("Use 0–25 warmups, 30–120 measured rounds and a timeout of 1–120 seconds")
    try:
        out=run(args)
    except Exception as exc:
        from import_static_graph import ImportSafety
        detail=str(exc) if isinstance(exc,(ImportSafety,ValueError)) else None
        print(json.dumps({"status":"failed","type":type(exc).__name__,"code":getattr(exc,"code",None),"detail":detail}))
        return 1
    print(json.dumps({"status":"validated","reports":str(out)}))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

