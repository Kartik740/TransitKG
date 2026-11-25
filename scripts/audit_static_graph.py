"""Read-only regional audit, structure and storage measurements."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean, median

from design_static_graph import ROOT, read, sha
from import_static_graph import check, inventory
from verify_imported_graph import catalog, competencies, verify_content

TARGET = "transitkg-static"
PHASE6 = ROOT / "reports/phase6"


def read_result(result):
    data = [tuple(r.values()) for r in result]
    summary = result.consume()
    check(not (summary.counters.contains_updates or summary.counters.contains_system_updates),
          "Unexpected write counter in a read-only query")
    return data, summary


def verify_target(driver, session, baseline, db, ready, normalized, schema, *, full_semantics):
    environment = inventory(driver, TARGET)
    current = environment["database"]
    check(current["name"] == TARGET and current["databaseID"] == baseline["target_identity"]["id"],
          "Database identity differs from Phase 6")
    with session.begin_transaction(timeout=180) as tx:
        actual = tx.run("CALL db.info() YIELD id,name RETURN id,name").single(strict=True)
        check(actual["id"] == current["databaseID"] and actual["name"] == TARGET, "Session target differs")
        definitions = catalog(tx, schema)
        audit = verify_content(tx, db, schema, ready)
        check(audit["imported_fingerprint"] == baseline["verification"]["imported_fingerprint"],
              "Live fingerprint differs from Phase 6")
        check(audit["partition_digests"] == baseline["verification"]["partition_digests"]
              and audit["counts"] == ready["counts"],"Live counts or logical partitions differ")
        if full_semantics:
            audit["competencies"] = competencies(tx, db, ready, normalized, schema)
            audit["ac_paths"] = ac_paths(tx, db)
            audit["extra_competencies"] = extra_competencies(tx, db)
    return {"target":environment,"schema":definitions,"content":audit}


def account_access(system):
    summary = system.run("SHOW USER PRIVILEGES YIELD access,action,graph RETURN DISTINCT access,action,graph")
    rows,_ = read_result(summary)
    relevant = {"write","create_element","delete_element","set_property","set_label","remove_label",
                "all_graph_privileges","all_database_privileges","all_dbms_privileges","match","access"}
    return [dict(zip(("access","action","graph"),r)) for r in rows if str(r[1]).lower() in relevant]


def ac_paths(tx, db):
    agency, stop = "511-sf-bay-regional:agency:AC", "511-sf-bay-regional:stop:55660"
    query = """MATCH p=(a:Agency {canonical_id:$agency})-[:OPERATES]->(r:Route)
               -[:ROUTE_SERVES_STOP]->(s:Stop {canonical_id:$stop}) RETURN p"""
    paths,_ = read_result(tx.run(query,agency=agency,stop=stop))
    expected = db.execute("""SELECT canonical_route_id FROM route_stops
                             WHERE canonical_agency_id=? AND canonical_stop_id=? ORDER BY 1""",[agency,stop]).fetchall()
    routes, nodes, edges = [], set(), set()
    for (path,) in paths:
        start, middle, end = path.nodes
        check(start["canonical_id"]==agency and end["canonical_id"]==stop,"AC path endpoints differ")
        check(len(path.relationships)==2 and end["stop_name"]=="San Pablo Av & W Grand Av","AC path shape differs")
        routes.append(middle["canonical_id"])
        nodes.update(n.element_id for n in path.nodes)
        edges.update(r.element_id for r in path.relationships)
    check(sorted((r,) for r in routes)==expected and (len(paths),len(nodes),len(edges))==(4,6,8),
          "AC path count, node count, or relationship count differs")
    return {"paths":len(paths),"distinct_nodes":len(nodes),"distinct_relationships":len(edges),
            "canonical_agency":agency,"canonical_stop":stop,"canonical_routes":sorted(routes),"cypher":query}


def extra_competencies(tx, db):
    results = []
    for label,source in (("Agency","AC"),("Route","AC:72"),("Trip","AC:10056070"),("Stop","55660")):
        key = db.execute("SELECT canonical_id FROM canonical_ids WHERE entity_type=? AND source_identifier=?",
                         [label.lower(),source]).fetchone()[0]
        q = f"MATCH (n:{label} {{canonical_id:$key}}) RETURN n.source_id"
        actual,_ = read_result(tx.run(q,key=key))
        check(actual==[(source,)],f"{label} canonical lookup failed")
        results.append({"id":"canonical_"+label.lower(),"layer":"Neo4j","params":{"key":key},
                        "expected":[[source]],"actual":actual,"status":"passed"})
    q = """MATCH (:Agency {source_id:$agency})-[:OPERATES]->(r:Route {route_short_name:$name})
           RETURN r.source_id ORDER BY 1"""
    params = {"agency":"AC","name":"72"}
    actual,_=read_result(tx.run(q,params))
    expected=db.execute("SELECT route_id FROM routes WHERE agency_id=? AND route_short_name=? ORDER BY 1",
                        list(params.values())).fetchall()
    check(actual==expected,"Route short-name grounding differs")
    results.append({"id":"route_short_name","layer":"Neo4j","params":params,"expected":expected,"actual":actual,"status":"passed"})
    day="2026-11-25"
    row=db.execute("""SELECT trip_id,route_id,direction_id,first_departure_seconds,canonical_trip_id
                      FROM dated_trip_candidates(?,NULL,NULL,NULL,NULL) LIMIT 1""",[day]).fetchone()
    check(row is None,"Unspecified trip descriptor unexpectedly matched")
    q = """SELECT trip_id,route_id,direction_id,first_departure_seconds,canonical_trip_id
           FROM trip_selector_context x JOIN active_services(?) a USING(service_id)
           WHERE x.agency_id='AC' AND x.direction_id IS NOT NULL AND x.first_departure_seconds IS NOT NULL
           AND NOT x.frequency_defined ORDER BY trip_id LIMIT 1"""
    trip, route, direction, start, canonical = db.execute(q,[day]).fetchone()
    descriptor = db.execute("SELECT canonical_trip_id FROM dated_trip_candidates(?,?,?,?,?)",
                            [day,trip,route,direction,start]).fetchall()
    check(descriptor==[(canonical,)],"Dated trip descriptor resolution differs")
    graph,_=read_result(tx.run("MATCH (t:Trip {canonical_id:$id}) RETURN t.source_id,t.direction_id",id=canonical))
    check(graph==[(trip,direction)],"Dated trip descriptor conflicts with Neo4j")
    results.append({"id":"trip_descriptor","layer":"hybrid","status":"passed","expected":[[trip,direction]],
                    "actual":graph,"params":{"day":day,"trip":trip,"route":route,"direction":direction,"start_seconds":start}})
    check(db.sql("SELECT count(*) FROM trip_selector_context WHERE frequency_defined").fetchone()[0]==0,
          "Unexpected frequencies in the pinned baseline")
    results.extend([{"id":"frequency_schedule","layer":"unavailable for this feed","status":"not applicable"},
                    {"id":"active_disruptions","layer":"future dynamic graph","status":"not applicable"}])
    return results


def distribution(values):
    values = list(values)
    if not values:
        return {"count":0}
    return {"count":len(values),"min":min(values),"max":max(values),"mean":mean(values),
            "median":median(values),"histogram":dict(sorted(Counter(values).items()))}


def structure(tx, schema):
    nodes = []
    by_id = {}
    result = tx.run("""MATCH (n) RETURN head(labels(n)) AS label,
                    coalesce(n.canonical_id,n.normalized_build_id) AS key,n.source_id AS source_id,
                    n.location_type AS location_type""")
    for record in result:
        row = record.data()
        by_id[row["key"]] = len(nodes)
        nodes.append(row)
    summary = result.consume()
    check(not summary.counters.contains_updates,"Unexpected update while reading nodes")
    incoming = [0]*len(nodes)
    outgoing = incoming.copy()
    parent = list(range(len(nodes)))
    size = [1]*len(nodes)
    ancestry = {}
    degrees = defaultdict(Counter)
    pairs = Counter()
    endpoint_groups = {e["type"]:(e["from"],e["to"]) for e in schema["relationships"]}

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    result = tx.run("""MATCH (a)-[r]->(b) RETURN a.canonical_id AS source,
                       b.canonical_id AS target,type(r) AS kind""")
    for record in result:
        a,b,kind = by_id[record["source"]],by_id[record["target"]],record["kind"]
        outgoing[a] += 1
        incoming[b] += 1
        src_label,dst_label = endpoint_groups[kind]
        pairs[f"{src_label}|{kind}|{dst_label}"] += 1
        degrees[(kind,"out")][a] += 1
        degrees[(kind,"in")][b] += 1
        if kind=="PART_OF":
            ancestry[a] = b
        r1,r2 = root(a),root(b)
        if r1 != r2:
            if size[r1] < size[r2]:
                r1,r2=r2,r1
            parent[r2] = r1
            size[r1] += size[r2]
    check(not result.consume().counters.contains_updates,"Unexpected update while reading relationships")
    by_label = defaultdict(list)
    components = Counter()
    for i,node in enumerate(nodes):
        by_label[node["label"]].append(i)
        components[root(i)] += 1
    top_components = components.most_common(8)
    breakdown = defaultdict(Counter)
    for i,node in enumerate(nodes):
        component = root(i)
        if component in dict(top_components):
            breakdown[component][node["label"]] += 1
    depth = []
    for i in by_label["Stop"]:
        seen = set()
        while i in ancestry:
            check(i not in seen, "Stop hierarchy cycle")
            seen.add(i)
            i = ancestry[i]
        depth.append(len(seen))
    transfer,_ = read_result(tx.run("""MATCH ()-[r:TRANSFER_RULE]->()
        RETURN r.transfer_type,count(*),count(r.from_route_id),count(r.to_route_id),count(r.from_trip_id),count(r.to_trip_id)
        ORDER BY 1"""))
    def top(direction,indices):
        return [{"id":nodes[i]["source_id"],"canonical_id":nodes[i]["key"],"degree":direction[i]}
                for i in sorted(indices,key=lambda j:(-direction[j],nodes[j]["key"]))[:3] if direction[i]]
    return {
        "degree_by_label":{label:{"in":distribution(incoming[i] for i in indices),
            "out":distribution(outgoing[i] for i in indices),
            "isolated":sum(not (incoming[i] or outgoing[i]) for i in indices),
            "top_in":top(incoming,indices),"top_out":top(outgoing,indices)}
            for label,indices in sorted(by_label.items())},
        "label_type_label":dict(sorted(pairs.items())),
        "scoped_distributions":{f"{kind}:{direction}":distribution(degrees[(kind,direction)][i]
            for i in by_label[endpoint_groups[kind][direction=="in"]]) for kind,direction in
            (("OPERATES","out"),("HAS_TRIP","out"),("ROUTE_SERVES_STOP","out"),
             ("ROUTE_SERVES_STOP","in"),("AGENCY_SERVES_STOP","in"),("RUNS_ON","in"))},
        "hierarchy_depth":distribution(depth),
        "transfer_rules":[dict(zip(("type","count","from_route","to_route","from_trip","to_trip"),row)) for row in transfer],
        "isolated_stop_location_types":dict(Counter(str(nodes[i]["location_type"]) for i in by_label["Stop"]
                                             if not (incoming[i] or outgoing[i]))),
        "weak_components":{"count":len(components),"singleton_count":sum(n==1 for n in components.values()),
                           "size_histogram":dict(sorted(Counter(components.values()).items())),
                           "largest":[{"size":n,"labels":dict(breakdown[root_id])} for root_id,n in top_components]},
    }


def storage(session, normalized, ready, baseline):
    settings = session.run("""SHOW SETTINGS YIELD name,value
        WHERE name='server.directories.data' RETURN value""").single()
    raw_bytes = sum(x["bytes"] for x in normalized["input_files"])
    normalized_bytes = normalized["parquet_bytes"]
    output = {"measured_utc":datetime.now(timezone.utc).isoformat(),"target":TARGET,"server_online":True,
              "raw_bytes":raw_bytes,"normalized_parquet_bytes":normalized_bytes,"graph_ready_parquet_bytes":ready["artifact_bytes"],
              "phase6_immediate_store_bytes":baseline["storage"].get("store_including_indexes_bytes")}
    if not settings:
        return {**output,"status":"unavailable","reason":"Server did not expose the data-directory setting"}
    base = Path(settings["value"])
    directories = {kind:base/kind/TARGET for kind in ("databases","transactions")}
    if not base.is_absolute() or any(not p.is_dir() for p in directories.values()):
        return {**output,"status":"unavailable","reason":"Target storage paths unavailable"}
    try:
        files = {key:[{"file":p.relative_to(path).as_posix(),"logical_bytes":p.stat().st_size}
                      for p in sorted(path.rglob("*")) if p.is_file()] for key,path in directories.items()}
    except OSError:
        return {**output,"status":"unavailable","reason":"Local file inventory could not be read"}
    latest = None
    try:
        with (base.parent/"logs/debug.log").open(encoding="utf-8",errors="replace") as log:
            log.seek(0,2)
            log.seek(max(0,log.tell()-8*1024*1024))
            for line in log:
                if TARGET in line and "checkpoint completed" in line.lower():
                    try:
                        event=json.loads(line)
                    except ValueError:
                        continue
                    if event.get("databaseName")==TARGET:
                        latest=event["time"]
    except OSError:
        pass
    store_bytes = sum(x["logical_bytes"] for x in files["databases"])
    logs_bytes = sum(x["logical_bytes"] for x in files["transactions"])
    indexes = sum(x["logical_bytes"] for x in files["databases"] if x["file"].startswith("schema/index/"))
    return {**output,"status":"measured","method":"logical file lengths, no forced checkpoint or shutdown",
            "scheduled_checkpoint_seen_utc":latest,"store_including_indexes_bytes":store_bytes,"index_bytes":indexes,
            "transaction_and_checkpoint_log_bytes":logs_bytes,"dedicated_database_bytes":store_bytes+logs_bytes,
            "hybrid_store_and_normalized_bytes":store_bytes+normalized_bytes,
            "hybrid_including_transaction_logs_bytes":store_bytes+logs_bytes+normalized_bytes,
            "block_store_note":"Shared block files mix record categories. File inventory gives exact sizes without attributing them to pure node/edge/property storage.",
            "files":files}


def phase6_construction(baseline,summarize_values):
    partitions = baseline["partitions"]
    stages = {k:baseline[k] for k in ("schema_apply_seconds","schema_index_wait_verify_seconds","data_load_seconds")}
    return {"run_count":1,"end_to_end":baseline["performance"],"stages":stages,
            "readback_seconds":baseline["verification"]["seconds"],
            "pre_snapshot_readback_seconds":baseline["before_snapshot_verification"]["seconds"],
            "transactions":baseline["transactions"],
            "batch_latency_ms":summarize_values([1000*b["seconds"] for p in partitions.values() for b in p["batches"]])}

