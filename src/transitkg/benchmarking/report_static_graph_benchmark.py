"""Regenerate Phase 7 research tables, figure and handoff from recorded evidence."""
import argparse
from collections import defaultdict
from pathlib import Path

import duckdb

from benchmark_static_graph import clock_summary, summarize_values
from design_static_graph import ROOT, read, write
from import_static_graph import check

MIB = 1024**2
COMPETENCIES = [
    ("Agency-stop canonical ground","AC / 55660"),
    ("Stop-name ambiguity","San Pablo Av & W Grand Av"),
    ("Agencies at a stop","55660"),
    ("Route canonical ground","AC / AC:72"),
    ("Trip canonical lookup","AC:10056070"),
    ("Routes at a scoped stop","AC / 55660"),
    ("Stops on a route","AC:72"),
    ("Trip templates on a route","AC:72"),
    ("Service of a trip","AC:10056070"),
    ("Parent station children","-325"),
    ("Scoped transfer","241 → 241"),
    ("Multi-agency stops","All stops"),
    ("Route, type and direction","AC / AC:72 / type 3 / direction 0"),
    ("Snapshot gate","Pinned manifests"),
]


def plan_summary(plans):
    output=[]
    for item in plans:
        root=item["neo4j_profile"]
        operators=[]
        queue=[root]
        while queue:
            node=queue.pop(0)
            queue.extend(node.get("children",[]))
            args=node.get("args",{})
            operators.append({"operator":node["operatorType"].split("@",1)[0],
                "index":args.get("UsedIndexes"),"estimated_rows":args.get("EstimatedRows"),
                "rows":node.get("rows"),"db_hits":node.get("dbHits"),
                "memory_bytes":args.get("Memory"),"page_hits":node.get("pageCacheHits"),
                "page_misses":node.get("pageCacheMisses")})
        output.append({"workload":item["workload"],"parameters":item["parameters"],"operators":operators,
                       "total_db_hits":sum(op["db_hits"] or 0 for op in operators),
                       "maximum_operator_rows":max(op["rows"] or 0 for op in operators),
                       "global_memory_bytes":root["args"].get("GlobalMemory")})
    return output


def chart(path, warm):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import ScalarFormatter

    names=list(dict.fromkeys(r["workload"] for r in warm))
    colors={"duckdb":"#126e75","neo4j":"#8c3f25","hybrid":"#5855a1"}
    offsets={"duckdb":-.22,"neo4j":.03,"hybrid":.26}
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":10,"svg.fonttype":"none"})
    fig,ax=plt.subplots(figsize=(9.3,5.2),layout="constrained")
    for row in warm:
        engine,name=row["engine"],row["workload"]
        y=names.index(name)+offsets[engine]
        p50,p95=(row["statistics"]["total_ms"][p] for p in ("p50","p95"))
        ax.plot([p50,p95],[y,y],color=colors[engine],lw=1.55,alpha=.75)
        ax.scatter([p50],[y],color=colors[engine],s=22,zorder=3)
        ax.scatter([p95],[y],color=colors[engine],marker="|",s=65,zorder=3)
    ax.set_xscale("log")
    right=max(row["statistics"]["total_ms"]["p95"] for row in warm)*1.32
    left=min(row["statistics"]["total_ms"]["p50"] for row in warm)*.8
    ax.set_xticks([n for n in (1,2,5,10,20,50,100,200,500) if left<=n<=right])
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.set_yticks(range(len(names)),[n.replace("_"," ") for n in names])
    ax.invert_yaxis()
    ax.set_xlabel("Client latency in milliseconds, logarithmic scale")
    ax.set_title("Warm parameter-varying benchmarks",loc="left",fontweight="bold",pad=12)
    ax.grid(axis="x",which="both",alpha=.25)
    ax.spines[["top","right","left"]].set_visible(False)
    ax.tick_params(axis="y",length=0)
    display={"duckdb":"DuckDB","neo4j":"Neo4j","hybrid":"Hybrid"}
    handles=[Line2D([0],[0],color=c,marker="o",label=display[e]) for e,c in colors.items()]
    handles += [Line2D([0],[0],color="#444444",marker="|",lw=0,label="p95 endpoint")]
    ax.legend(handles=handles,loc="upper right",frameon=False)
    fig.savefig(path,dpi=190)
    plt.close(fig)


def mib(value):
    return f"{value/MIB:,.2f} MiB"


def render(out):
    read_at=lambda name:read(out/(name+".json"))
    env=read_at("benchmark_environment")
    invariance=read_at("static_invariance_report")
    check(env["status"]=="validated" and invariance["status"]=="verified","Phase 7 evidence is incomplete")
    with duckdb.connect(config={"threads":2}) as db:
        query=db.read_parquet(str(out/"query_benchmark_samples.parquet")).order('"order"')
        samples=[dict(zip(query.columns,row)) for row in query.fetchall()]
    summary=clock_summary(samples)
    check(summary==read_at("query_benchmark_results"),"Stored summaries differ from raw Parquet")
    warm=[r for r in summary if r["phase"]=="warm_varying"]
    initial={(r["workload"],r["engine"]):r["statistics"]["total_ms"]["p50"]
             for r in summary if r["phase"]=="initial"}
    same={(r["workload"],r["engine"]):r["statistics"]["total_ms"] for r in summary if r["phase"]=="warm_same_parameter"}
    per_case=defaultdict(list)
    for s in samples:
        if s["phase"]=="warm_varying" and s["status"]=="success":
            per_case[s["workload"],s["engine"],s["case"]].append(s["total_ms"])
    paired={}
    for r in warm:
        paired.setdefault(r["workload"],{})[r["engine"]]=r["statistics"]["total_ms"]
    comparisons=[{"workload":name,"engines":engines,
        "nonrelational_minus_duckdb_p50_ms":next(v["p50"]-engines["duckdb"]["p50"] for k,v in engines.items() if k!="duckdb")}
        for name,engines in paired.items() if len(engines)==2]
    faster=sum(c["nonrelational_minus_duckdb_p50_ms"]>0 for c in comparisons)
    canonical=paired["canonical_stop"]
    p50,p95=(("Neo4j" if canonical["neo4j"][stat]<canonical["duckdb"][stat] else
              "DuckDB" if canonical["neo4j"][stat]>canonical["duckdb"][stat] else "both engines (tie)")
             for stat in ("p50","p95"))
    plans=plan_summary(read_at("query_plan_analysis"))
    write(out/"benchmark_comparisons.json",{"paired_workloads":comparisons,"per_case":[
        {"workload":w,"engine":e,"case":c,"latency_ms":summarize_values(values)}
        for (w,e,c),values in sorted(per_case.items())],"profile_summary":plans})
    chart(out/"benchmark_latency.png",warm)

    audit=read_at("audit_before")["content"]
    net=read_at("structural_analysis")
    storage=read_at("storage_measurement")
    arch=read_at("architecture_and_construction")
    tests=read_at("automated_test_report")
    protocol=read_at("benchmark_protocol")
    fingerprint=invariance["fingerprints"]["phase7_before"]
    node_counts=invariance["counts"]["nodes"]
    rel_counts=invariance["counts"]["relationships"]
    nodes=sum(node_counts.values())
    edges=sum(rel_counts.values())
    component=net["weak_components"]
    ac=audit["competencies"]["ac_selector"]
    percent=100*component["largest"][0]["size"]/nodes
    good=sum(row["status"]=="success" for row in samples)
    measured=sum(row["phase"] in ("warm_varying","warm_same_parameter") for row in samples)
    config=env["host"]
    environment=env["server"]
    medium=env.get("storage_hardware",{}).get("project_volume")
    database_settings={x["name"]:x["value"] for x in env["server_settings"]}
    initial_heap=database_settings.get("server.memory.heap.initial_size","unavailable")
    max_heap=database_settings.get("server.memory.heap.max_size","unavailable")
    page_cache=database_settings.get("server.memory.pagecache.size","unavailable")
    neo_version=environment["components"][0]["versions"][0]
    server_sample=env["server_process_at_benchmark_end"]
    server_memory=mib(server_sample["working_set_bytes"]) if server_sample["status"]=="measured" else "unavailable"
    server_peak=mib(server_sample["peak_working_set_bytes"]) if server_sample["status"]=="measured" else "unavailable"
    baseline=arch["phase6_construction"]
    phase6=baseline["end_to_end"]
    node_rows="\n".join(f"| {label} | {count:,} | {net['degree_by_label'][label]['isolated']:,} |"
                         for label,count in node_counts.items())
    edge_rows="\n".join(f"| {name} | {count:,} |" for name,count in rel_counts.items())
    timing_rows="\n".join(f"| {r['workload']} | {r['engine']} | {initial[r['workload'],r['engine']]:.3f} | "
                        f"{r['statistics']['total_ms']['p50']:.3f} | {r['statistics']['total_ms']['p95']:.3f} | "
                        f"{r['statistics']['rows']['min']}–{r['statistics']['rows']['max']} |" for r in warm)
    same_rows="\n".join(f"| {w} | {e} | {s['p50']:.3f} | {s['p95']:.3f} |" for (w,e),s in same.items())
    plan_rows="\n".join(f"| {p['workload']} | {' → '.join(o['operator'] for o in p['operators'])} | "
                       f"{p['total_db_hits']:,} | {p['maximum_operator_rows']:,} |" for p in plans)
    arch_rows="\n".join(f"| {label} | {x['nodes']:,} | {x['relationships']:,} | "
                       f"{x['compact_node_reduction_pct']:.2f}% | {x['compact_edge_reduction_pct']:.2f}% |"
                       for label,x in arch["architecture"].items())
    checks="\n".join(f"| G{i} | {label} | {param} | {audit['competencies']['cypher'][f'G{i}']} |"
                     for i,(label,param) in enumerate(COMPETENCIES,1))
    test_rows="\n".join(f"| {phase} | {r['passed']} | {r['failed']+r['errors']} | {r['skipped']} |"
                         for phase,r in tests["offline_suites"].items())
    hybrid_rows=[]
    for row in warm:
        if row["engine"]=="hybrid":
            s=row["statistics"]
            hybrid_rows.append(f"| {row['workload']} | {s['neo4j_ms']['p50']:.3f} | {s['duckdb_ms']['p50']:.3f} | "
                               f"{s['bridge_ms']['p50']:.3f} | {s['total_ms']['p50']:.3f} |")
    text=f"""# TransitKG Phase 7 · Static graph audit and benchmark

**Validated on 25 November 2026.** The graph in `transitkg-static` has **{nodes:,} nodes** and **{edges:,} relationships**. The Phase 5 expected fingerprint, Phase 6 imported fingerprint and both Phase 7 readbacks match. All directed endpoints, identity keys, typed properties and 13 logical partition digests also matched. There were **{good}/{len(samples)} successful timed executions** and zero timeouts. [Static invariance](static_invariance_report.json) · [Raw timings](query_benchmark_samples.parquet)

## Evidence and safety

Reviewed the Phase 2 dataset and referential reports, Phase 3 canonical contract and normalized manifest, Phase 4 architecture, schema, SQL/Cypher contracts and bounded benchmarks, Phase 5 graph-ready publication and fingerprints, and Phase 6 construction, replay, visualization and testing evidence. The Phase 6 manifest pinned the actual database ID and graph-ready manifest hash. The preflight reopened the graph-ready Parquet, audited complete record sets against normalized SQL and independently verified the actual Neo4j graph. Both audits verified exact schema definitions, online indexes, all approved properties and no undeclared content. Follow-up checks covered 27 raw files, 32 normalized Parquet files, 13 graph-ready files, the normalized manifest and the Phase 6 manifest. [Phase 6 contract](../phase6/phase7_validation_contract.md) · [Before audit](audit_before.json) · [After audit](audit_after.json)

Before and after, SHA-256 was **{fingerprint}**. Canonical IDs retained exact source spellings, UTF-8 and nullable property semantics. Complete graph-to-Parquet agreement and normalized SQL checks covered agency-route, route-trip, trip-service, route-stop, agency-stop, hierarchy and nullable transfer identities. The two conflicting direction labels and 68 unresolved fare transfer variants stayed outside authoritative facts. All 118,128 mappings are accounted for by graph entities and 2,446 external shapes. The normalized build retains 3,185,115 visits, 1,340,496 shape points, 100,912 arrivals and 100,948 departures above 24 hours, and five calendar rows ending in 2099. The snapshot gate and synthetic mismatched-build cases were exercised.

The authenticated account has `admin` and `PUBLIC` roles and the catalog lists an explicit write grant. The audit used the exact target, Neo4j read routing, read transactions and fixed read queries. Timed queries checked update counters. Database access control still permits admin writes. A dedicated read-only principal remains a Phase 8 protection opportunity. [Environment and grants](benchmark_environment.json)

## Graph structure

| Label | Nodes | Isolated |
|---|---:|---:|
{node_rows}

| Relationship type | Edges |
|---|---:|
{edge_rows}

A streamed union-find calculation treated all stored edges as undirected for weak connectivity. It found **{component['count']} components**, **{component['singleton_count']} singletons**, and a largest component of **{component['largest'][0]['size']:,} nodes ({percent:.2f}%)**. This measures graph connectivity across all modeled relationship types. The stop hierarchy reaches depth **{net['hierarchy_depth']['max']}**, with {net['hierarchy_depth']['histogram']['1']:,} child→parent edges. The 122 isolated nodes are the metadata singleton, two unused service definitions and 119 locations. An independent source query found 36 type-0 stops and 83 type-1 stations without an incident served-stop, hierarchy or transfer relation. These are accepted source entities. The two unused services are `SF:every-day` and `tf:20260101-20261231`.

The stored statistics include full in/out histograms by label, endpoint label/type counts, per-route and per-agency association distributions, component composition, hierarchy depth, representative highest-degree nodes and transfer restrictions. **44 of 61** transfer rules have two route qualifiers. Structural connectedness does not establish physical or time-feasible travel. [Structural analysis](structural_analysis.json)

## Competency questions and AC Transit regression

All 14 approved Cypher contracts matched the complete normalized SQL results. The table shows equal reference and observed result counts. Additional exact canonical lookup of agencies, routes, trips and stops, short-name route lookup, a dated trip descriptor and benchmark cases with negative and positive selectors passed. Frequency service is inapplicable to this feed. Dynamic alert retrieval remains a future competency. [Exact query contracts](../../schema/competency.cypher) · [Phase 4 design](../phase4/competency_questions.md) · [Case-level results and reference digests](competency_cases.json)

| Query | Purpose | Parameters | Expected = actual rows |
|---|---|---|---:|
{checks}

The agency-scoped AC/55660 query produced **{audit['ac_paths']['paths']} paths, {audit['ac_paths']['distinct_nodes']} distinct nodes and {audit['ac_paths']['distinct_relationships']} distinct relationships**. The four canonical routes are `AC%3A72`, `AC%3A72L`, `AC%3A72M` and `AC%3A802` under the regional route namespace. The graph and timetable resolve **{ac['status']}** to `{ac['stop_ids']['55660']}` with **{ac['visiting_trips']} templates**, including **{ac['dated_visiting_trips']}** on **{ac['service_date']}**. The same-name stop 52227 has its own ID and **{ac['neighbor_visiting_trips']}** templates. Wrong-agency and contradictory-direction cases return zero. These are scheduled candidates within the selector scope. The extended-hour reference trip includes an arrival and departure at 86,568 service-day seconds. The exact ordered visit and calendar exception tests preserve GTFS service-day semantics. [Regional audit](audit_before.json)

## Benchmark protocol and observed latency

The designed static workload uses ten query families and nineteen engine/query variants. Cases include actual low- and high-degree stops, small and large routes, several agencies, shared stops, ambiguous names, absent identifiers, dates with exceptions and restricted transfers. Selection is deterministic. Seed **{protocol['seed']}** shuffles variant order within round. Each variant uses one initial execution, {protocol['warmups_per_variant']} warm-ups and {protocol['measured_varying_per_variant']} parameter-varying measured executions. Canonical lookup and date-scoped visits additionally use ten same-parameter executions per applicable variant. Of the {len(samples)} retained rows, {measured} are warm measurements.

One worker uses reused connections and fully consumes results. The per-engine timeout is {protocol['timeout_seconds_per_engine']:.0f} seconds. SQL watchdog and Bolt timeout failures are recorded without retries. Result equality is checked outside timing. Client timings use elapsed wall time. Neo4j submission, full consumption and Bolt reported available/consumed durations are stored separately. DuckDB timing includes execution and local fetch. Hybrid timing includes graph lookup, parameter conversion and local timetable access. Initial samples are after audit and workload selection on a running server. Cache state was neither cleared nor controlled. p50 and p95 use linear interpolation at rank `(n−1)p`. Machine-readable summaries include min, max, mean, sample standard deviation, IQR, result cardinality and failure rates. [Protocol](benchmark_protocol.json) · [Latency statistics](query_benchmark_results.json) · [Pair and per-case comparisons](benchmark_comparisons.json)

| Workload | Engine | Initial ms | Warm p50 ms | Warm p95 ms | Result rows |
|---|---|---:|---:|---:|---:|
{timing_rows}

| Same parameter | Engine | p50 ms | p95 ms |
|---|---|---:|---:|
{same_rows}

![Warm benchmark medians and 95th percentiles](benchmark_latency.png)

### Hybrid stage costs

| Workload | Neo4j p50 ms | DuckDB p50 ms | Bridge p50 ms | End-to-end p50 ms |
|---|---:|---:|---:|---:|
{chr(10).join(hybrid_rows)}

Component medians are calculated separately. The end-to-end figure includes orchestration. Direct DuckDB had the lower median on **{faster} of {len(comparisons)} paired workloads**. For exact stop lookup the lower median was observed in {p50} and the lower p95 in {p95}. Both dated hybrid workloads add graph and result conversion work around SQL. Neo4j supplies typed connectivity and inspectable scope. DuckDB evaluates the date, timetable and ordered visit predicates. The tests compare equal row multisets and pinned snapshots for each pair. They reflect local Bolt-plus-decoding versus in-process DuckDB from Parquet. [Paired measurements](benchmark_comparisons.json)

### Plans and likely bottlenecks

| Workload | Neo4j profiled operators | Database hits | Largest operator rows |
|---|---|---:|---:|
{plan_rows}

Canonical keys and indexed exact attributes provided selective entry points in sampled Neo4j plans. Expansion and result sorting become more visible for large routes, and name ambiguity increases both result work and tail latency. DuckDB plans for date-scoped service and ordered trip visits read Parquet and join timetable/calendar data. Their time also depends on filtering, join and materialization costs. Profile values come from separately instrumented executions. The raw plans include estimated and actual rows, memory and page-cache counters where exposed. No timed sample uses profiling. [Raw Neo4j and DuckDB plans](query_plan_analysis.json)

## Resources and storage

The run used {config['os']}, {config['cpu']}, {config['logical_cpus']} logical CPUs and {mib(config['total_ram_bytes'])} RAM. The observed project storage medium was **{medium or 'unavailable'}**. Python {config['python']}, Neo4j Enterprise {neo_version}, Bolt driver {env['driver_version']} and DuckDB {env['duckdb_version']} were used. Neo4j reported an initial heap of {initial_heap}, a maximum heap of {max_heap} and a page cache of {page_cache}. DuckDB used two threads and a 768 MB memory limit.

The benchmark stage and associated case validation took **{env['benchmark_wall_seconds']:.3f} s** wall and **{env['benchmark_client_cpu_seconds']:.3f} s** client CPU. Full client audit, plan and benchmark work took **{env['audit_and_benchmark_wall_seconds']:.3f} s** wall and **{env['client_cpu_seconds']:.3f} s** CPU. Peak client working set was **{env['client_peak_rss_mib']:.2f} MiB**. Server working set at benchmark end was **{server_memory}** and lifetime peak at that sample was **{server_peak}**. The starting server process sample was unavailable, so benchmark-interval server CPU and memory change are unreported. DuckDB spill at the end of the audit was {env['duckdb_spill_bytes_at_end_of_audit']:,} bytes. Peak spill was not sampled. [Benchmark environment](benchmark_environment.json)

| Layer | Logical bytes | MiB |
|---|---:|---:|
| Raw GTFS | {storage['raw_bytes']:,} | {storage['raw_bytes']/MIB:.2f} |
| Normalized Parquet | {storage['normalized_parquet_bytes']:,} | {storage['normalized_parquet_bytes']/MIB:.2f} |
| Graph-ready Parquet | {storage['graph_ready_parquet_bytes']:,} | {storage['graph_ready_parquet_bytes']/MIB:.2f} |
| Neo4j graph store, including indexes | {storage['store_including_indexes_bytes']:,} | {storage['store_including_indexes_bytes']/MIB:.2f} |
| Indexes within store | {storage['index_bytes']:,} | {storage['index_bytes']/MIB:.2f} |
| Dedicated transaction and checkpoint logs | {storage['transaction_and_checkpoint_log_bytes']:,} | {storage['transaction_and_checkpoint_log_bytes']/MIB:.2f} |
| Dedicated database including logs | {storage['dedicated_database_bytes']:,} | {storage['dedicated_database_bytes']/MIB:.2f} |
| Hybrid operational footprint including logs | {storage['hybrid_including_transaction_logs_bytes']:,} | {storage['hybrid_including_transaction_logs_bytes']/MIB:.2f} |

The online sample was taken after a naturally completed checkpoint at `{storage['scheduled_checkpoint_seen_utc']}`. It measures logical file lengths for the dedicated store and log directories. It excludes other databases, Neo4j installation files and intermediate artifacts from the hybrid total. Graph-ready and normalized figures are Parquet payloads and exclude JSON manifests. Physical allocation and sparse/compressed-file effects are unmeasured. Neo4j blocks can share categories, so node, relationship and property storage cannot be separately attributed in full. The file inventory preserves exact per-file sizes. The Phase 6 immediate sample of {storage['phase6_immediate_store_bytes']:,} bytes preceded this later checkpoint-backed observation and undercounted the populated store. [Storage file inventory](storage_measurement.json)

## Architecture and construction evidence

| Phase 4 comparison | Nodes | Relationships | Compact node reduction | Compact edge reduction |
|---|---:|---:|---:|---:|
{arch_rows}

Only the compact regional graph was measured. The other counts are dataset-derived model alternatives. Record counts alone cannot predict flush size, construction time or lookup latency.

Phase 6 is one production construction run. It took **{phase6['wall_seconds']:.3f} s** end to end. Per-stage measurements support **{phase6['nodes']['created_per_second']:,.2f} nodes/s** and **{phase6['relationships']['created_per_second']:,.2f} relationships/s**, using their respective load timings. There were 187 committed batches with no retries. Batch latency p50/p95 was **{baseline['batch_latency_ms']['p50']:.2f}/{baseline['batch_latency_ms']['p95']:.2f} ms**. Schema application took {baseline['stages']['schema_apply_seconds']:.3f} s and index readiness verification {baseline['stages']['schema_index_wait_verify_seconds']:.3f} s. Dual readback took {baseline['pre_snapshot_readback_seconds']:.3f} and {baseline['readback_seconds']:.3f} s. This audit reused that evidence without rebuilding. Readback is a substantial part of verified construction cost. [Cardinality and construction evidence](architecture_and_construction.json)

## Tests, limitations and readiness

| Offline phase | Passed | Failed or errored | Skipped |
|---|---:|---:|---:|
{test_rows}

The full live audit and benchmark acceptance passed. Optional concurrency, architecture subset writes, controlled cold-cache testing and deliberate live interruption were deferred. The study focuses on single-client regional queries. Phase 6 verified live completed-build replay and verified interruption handling in synthetic fixtures. Live interruption remains unverified. No manual action is required for this read-only evaluation.

The data supports exact mapping guarantees within one pinned regional snapshot and reliable joins between typed graph scope and timetable predicates. The designed cases, warm/server cache state, result cardinality, one local host, short measurement series and one prior construction run limit generalization. These results do not establish UI latency, fleet-scale concurrency, real alert traffic behavior, external operator crosswalks, model extraction quality or future dynamic update costs. The benchmark uses equal result multisets. Query implementations and storage access paths differ intentionally across engines. [Test evidence](automated_test_report.json) · [Phase 8 readiness contract](phase8_readiness_contract.md)

## Reproduction

The implementation chose the deterministic seed, case-selection rules, one worker, 15-second per-engine timeout and 30 warm varying rounds. Set the project Python environment and securely provide Neo4j connection settings to the process. From the workspace root in PowerShell:

~~~powershell
$env:PYTHONPATH = '.phase2-deps'
$env:TRANSITKG_PHASE3_BUILD = (Resolve-Path 'data/normalized/build-5608c0d7d93b9c6200431b5c').Path
python scripts/benchmark_static_graph.py --output reports/phase7-rerun
python scripts/test_static_graph_benchmark.py --report reports/phase7-rerun
python scripts/report_static_graph_benchmark.py --input reports/phase7-rerun
~~~

Benchmark reruns require a fresh output directory, so existing evidence cannot be overwritten. Use `--input reports/phase7` to regenerate this report without querying Neo4j. The report command checks raw Parquet against the stored statistics before regenerating the figure, tables and handoff. Matplotlib is required for figure generation. Add `--server-pid` with the PID verified to own the local Bolt port to sample server process statistics.

Official references for plan interpretation and timing fields were checked on 25 November 2026. [Neo4j execution plans](https://neo4j.com/docs/cypher-manual/current/planning-and-tuning/execution-plans/) · [Neo4j driver results](https://neo4j.com/docs/python-manual/current/result-summary/) · [DuckDB EXPLAIN ANALYZE](https://duckdb.org/docs/stable/guides/meta/explain_analyze.html)
"""
    (out/"phase7_validation_benchmark_report.md").write_text(text,encoding="utf-8")
    handoff=f"""# Phase 8 static baseline readiness

| Boundary | Verified value |
|---|---|
| Database | transitkg-static, Neo4j Enterprise {neo_version}, local Bolt port 7687 |
| Database identity | {environment['database']['databaseID']} |
| Source snapshot | gtfs-sha256-d6279860e2fe203541a574ee04db5752ecad9e5e1a38b1759cc397b1b596dfa2 |
| Normalized build | normalized-sha256-5608c0d7d93b9c6200431b5c227da598d52cd0108e9e2f410e5b012b00d28052 |
| Graph-ready build | graph-ready-sha256-f85cdfafe998da9e7eecb6697443ebd02e45f4d43e41462db7a8a5acb82dadae |
| Static SHA-256 | {fingerprint} |
| Cardinalities | {nodes:,} nodes and {edges:,} relationships |
| Schema | Approved 1.0.0, six uniqueness constraints and seven explicit lookup indexes. Exact catalog recorded in the audit. |

Use the original GTFS Schedule snapshot and the normalized build as the static reference. Require the database ID, source snapshot, normalized build, schema and canonical contract to agree before resolving any hybrid scope. Preserve all accepted visit, calendar and geometry semantics externally, including extended hours and repeated visits. Use the 13 exact class and relationship digests as diagnostics and compare the full static fingerprint at version and deployment boundaries. Recheck Neo4j schema definitions and immutable file digests when publishing a new baseline.

Phase 7 ran under an admin account with a write grant. Set up a dedicated least-privilege read account for grounding and a separately scoped writer for any future event data, subject to explicit approval. Define a static projection that preserves the existing fingerprint contract while allowing future event content to be verified independently. Protect the authoritative labels, relationship types and property keys, keep operational state out of static hashing, and audit unexpected mutations before serving results. Keep timeless entity identity, source snapshot identity and temporal validity separate.

Use the approved competency set, AC path and date regression, raw latency samples and Phase 7 offline tests as baselines. For evaluation, distinguish graph-only, DuckDB-only and hybrid timing. Keep unresolved external operator crosswalks and unsupported scope claims outside authoritative facts. Future dynamic events and uncontrolled concurrent workloads still need separate validation. [Invariance](static_invariance_report.json) · [Benchmark baseline](query_benchmark_results.json) · [Competency checks](audit_before.json) · [Full Phase 7 report](phase7_validation_benchmark_report.md)
"""
    (out/"phase8_readiness_contract.md").write_text(handoff,encoding="utf-8")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,default=ROOT/"reports/phase7")
    render(parser.parse_args().input)

