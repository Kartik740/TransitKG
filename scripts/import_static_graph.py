"""Input preflight, isolated Neo4j import, recovery and independent readback."""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from time import perf_counter, process_time, sleep
from urllib.parse import urlsplit

import duckdb
import neo4j
from neo4j import GraphDatabase, READ_ACCESS
from neo4j import exceptions as neo_errors

from design_static_graph import ROOT, SCHEMA, read, sha, write
from profile_gtfs import peak_rss_mib
from transform_static_graph import (BUILD, SCHEMA_FILE, FORMAT, definitions, columns, encode, fingerprint,
                                    key_columns, open_normalized, pin_inputs, reopen)
from validate_graph_ready import require, validate, validate_regional
from verify_imported_graph import catalog, competencies, records_from_parquet, typed_properties, verify_content
from verify_phase4_neo4j import statements

READY = ROOT / "data/graph_ready/build-f85cdfafe998da9e7eecb669"
IMPORT_CONTRACT = ROOT / "reports/phase5/phase6_import_contract.md"
REPORT = ROOT / "reports/phase6"
VERSION = "1.0.0"
PINNED_ID = "graph-ready-sha256-f85cdfafe998da9e7eecb6697443ebd02e45f4d43e41462db7a8a5acb82dadae"
PINNED_HASH = "675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66"
DEFAULT_URI = "neo4j://127.0.0.1:7687"


class ImportSafety(RuntimeError):
    """A sanitized configuration, isolation or checkpoint failure."""


class PlannedPause(RuntimeError):
    """An explicitly requested stop after a committed first batch."""


def check(condition,message):
    if not condition:
        raise ImportSafety(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def verify_ready_files(ready, manifest):
    actual = {p.relative_to(ready).as_posix() for p in ready.rglob("*") if p.is_file()}
    check(actual == {"manifest.json", *manifest["artifacts"]}, "Graph-ready file inventory differs")
    for path, info in manifest["artifacts"].items():
        file = ready/path
        check(file.stat().st_size == info["bytes"] and sha(file) == info["sha256"],
              f"Graph-ready checksum or size differs for {path}")


def preflight(db, ready, normalized_path, raw):
    m = read(ready / "manifest.json")
    normalized,schema,baseline = pin_inputs(normalized_path,raw)
    inputs = m["inputs"]
    recomputed_id = "graph-ready-sha256-"+hashlib.sha256(encode(
        [inputs,m["implementation"],m["expected_static_graph_fingerprint"]])).hexdigest()
    check(m["graph_ready_build_id"] == recomputed_id == PINNED_ID and
          m["expected_static_graph_fingerprint"] == PINNED_HASH and m["fingerprint_format"] == FORMAT,
          "Graph-ready manifest does not identify the approved build")
    for key in ("source_snapshot_id","normalized_build_id","canonical_contract_version"):
        check(inputs[key]==normalized[key],f"Normalized compatibility failure for {key}")
    check(inputs["schema_version"]==schema["version"] and inputs["schema_sha256"]==sha(SCHEMA_FILE)
          and inputs["normalized_manifest_sha256"]==sha(normalized_path/"manifest.json"),
          "Schema or normalized manifest digest differs")
    contract = IMPORT_CONTRACT.read_text(encoding="utf-8")
    check(all(v in contract for v in (PINNED_ID,PINNED_HASH,inputs["source_snapshot_id"],
                                       inputs["normalized_build_id"],ready.name)),
          "Import contract does not identify the exact input build")
    for key,path in (
        ("transformation_contract_sha256",ROOT/"reports/phase4/phase5_transformation_contract.md"),
        ("canonical_contract_sha256",ROOT/"reports/phase3/canonical_identifier_contract.md"),
        ("timetable_sql_sha256",SCHEMA/"timetable.sql"),
    ):
        check(inputs[key]==sha(path),f"Pinned {key} differs")
    expected = {f"{kind}/{name}.parquet" for kind,name,_ in definitions(schema)}
    check(set(m["artifacts"])==expected,"Unexpected graph-ready labels or relationship types")
    verify_ready_files(ready,m)
    open_normalized(db,normalized_path,normalized)
    reopen(db,ready,schema)
    meta = db.sql("SELECT * FROM graph_FeedSnapshot").fetchone()
    keys = tuple(n["properties"] for n in schema["nodes"] if n["label"]=="FeedSnapshot")[0]
    metadata = dict(zip(keys,meta,strict=True))
    check(all(metadata[k]==inputs[k] for k in ("source_snapshot_id","normalized_build_id",
              "schema_version","canonical_contract_version")) and
          metadata["construction_version"]==m["implementation"]["version"] and
          metadata["static_fingerprint"]==PINNED_HASH,"Incompatible FeedSnapshot metadata")
    evidence = validate(db,schema,definitions(schema),columns,key_columns,metadata)
    evidence.update(validate_regional(db,baseline,normalized))
    check(evidence==m["validation"] and evidence["counts"]==m["counts"],"Prior graph-ready validation differs")
    actual, logical = fingerprint(db,schema)
    check(actual==PINNED_HASH,"Recomputed Parquet digest differs")
    for kind,name,item in definitions(schema):
        info = m["artifacts"][f"{kind}/{name}.parquet"]
        check(info["logical_sha256"]==logical[name] and info["records"]==m["counts"][kind][name]
              and info["schema"]==columns(kind,item),f"Artifact manifest does not match reopened {name}")
    return m,normalized,schema


def target_settings(database):
    uri = os.getenv("NEO4J_URI",DEFAULT_URI)
    try:
        address = urlsplit(uri)
        port = address.port
    except ValueError:
        raise ImportSafety("Invalid local Neo4j connection URI") from None
    check(address.scheme in ("neo4j","bolt") and address.hostname in ("127.0.0.1","localhost","::1")
          and port == 7687 and not address.username and not address.password
          and address.path in ("","/") and not address.query and not address.fragment,
          "URI must identify the approved local Neo4j instance")
    check(database and re.fullmatch(r"transitkg-[a-z0-9-]{1,39}",database),
          "Specify an approved dedicated transitkg-* database")
    configured = os.getenv("NEO4J_DATABASE")
    check(not configured or configured==database,"Configured database differs from explicitly named target")
    return uri


def connect(uri):
    password = os.getenv("NEO4J_PASSWORD")
    check(password,"Uncompromised credentials must be injected locally")
    logging.getLogger("neo4j").setLevel(logging.CRITICAL)
    return GraphDatabase.driver(uri,auth=(os.getenv("NEO4J_USERNAME","neo4j"),password),
        connection_timeout=10,connection_acquisition_timeout=15,max_transaction_retry_time=0,
        max_connection_pool_size=3,fetch_size=2000)


def inventory(driver, database):
    with driver.session(database="system",default_access_mode=READ_ACCESS) as session:
        components = session.run("CALL dbms.components() YIELD name,versions,edition RETURN name,versions,edition").data()
        user = session.run("SHOW CURRENT USER").single(strict=True)
        accounts = {"roles":user["roles"],"password_change_required":user["passwordChangeRequired"]}
        databases = session.run("SHOW DATABASES YIELD name,type,currentStatus,databaseID,access,default "
                                "WHERE name=$name RETURN *",name=database).data()
    check(not accounts["password_change_required"], "Neo4j requires the account password to be changed")
    check(len(databases)<=1,"Target resolved to more than one database copy")
    state = databases[0] if databases else None
    if state is not None:
        check(state["type"]=="standard" and not state["default"] and state["currentStatus"]=="online"
              and state["databaseID"],"Target must be an online dedicated and non-default standard database")
    return {"components":components,"account":accounts,"database":state}


def inspect_target(session, database):
    info = session.run("CALL db.info() YIELD id,name,creationDate RETURN id,name,creationDate").single(strict=True).data()
    check(info["name"]==database,"Driver resolved a different database")
    data = session.run("""CALL { MATCH (n) RETURN count(n) AS nodes }
                          CALL { MATCH ()-[r]->() RETURN count(r) AS relationships }
                          CALL { MATCH (m:FeedSnapshot) RETURN collect(m.normalized_build_id) AS snapshots }
                          RETURN nodes,relationships,snapshots""").single(strict=True).data()
    return info,data


@contextmanager
def local_lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a+b") as stream:
        if os.name=="nt":
            import msvcrt
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                raise ImportSafety("Another local import holds the target lock") from None
        else:
            import fcntl
            try:
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                raise ImportSafety("Another local import holds the target lock") from None
        yield


def checkpoint(path, pin, state):
    if path.exists():
        check(read(path)==pin,"Checkpoint belongs to an incompatible database or build")
        return True
    check(not state["nodes"] and not state["relationships"],"Nonempty target has no matching import checkpoint")
    with path.open("x",encoding="utf-8") as stream:
        stream.write(json.dumps(pin,sort_keys=True)+"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return False


def payload(row, item):
    approved = {name:row[name] for name in item["properties"]}
    values = typed_properties(approved,item["properties"])
    return {k:v for k,v in values.items() if v is not None}


def batch_queries(kind,name,item):
    if kind=="nodes":
        key = item["identity"]
        match = f"OPTIONAL MATCH (n:{name} {{{key}: row.key}})"
        lookup = f"UNWIND $rows AS row {match} RETURN row.ordinal AS ordinal,collect(properties(n)) AS existing"
        insert = f"UNWIND $rows AS row CREATE (n:{name}) SET n=row.props RETURN count(n) AS created"
    else:
        endpoints = (f"MATCH (a:{item['from']} {{canonical_id:row.start_id}}) "
                     f"MATCH (b:{item['to']} {{canonical_id:row.end_id}})")
        scope = " AND ".join(f"(r.{p}=row.props.{p} OR (r.{p} IS NULL AND row.props.{p} IS NULL))"
                             for p in item["identity"][3:])
        where = f" WHERE {scope}" if scope else ""
        lookup = f"""UNWIND $rows AS row {endpoints} OPTIONAL MATCH (a)-[r:{name}]->(b){where}
                     RETURN row.ordinal AS ordinal,collect(properties(r)) AS existing"""
        insert = f"""UNWIND $rows AS row {endpoints} CREATE (a)-[r:{name}]->(b)
                     SET r=row.props RETURN count(r) AS created"""
    return lookup,insert


def write_batch(tx, kind, name, item, records):
    batch = []
    for ordinal,row in enumerate(records):
        envelope = ({"key":row[item["identity"]]} if kind=="nodes" else
                    {"start_id":row["start_id"],"end_id":row["end_id"]})
        batch.append(dict(envelope,ordinal=ordinal,props=payload(row,item)))
    lookup,insert = batch_queries(kind,name,item)
    missing = []
    seen = set()
    for record in tx.run(lookup,rows=batch):
        ordinal = record["ordinal"]
        check(ordinal not in seen,"Nonunique typed endpoint")
        seen.add(ordinal)
        existing = record["existing"]
        check(len(existing)<=1,"Duplicate relationship or node identity already in target")
        entry = batch[ordinal]
        if existing:
            check(encode(existing[0])==encode(entry["props"]),"Matched identity has conflicting properties")
        else:
            missing.append(entry)
    check(len(seen)==len(batch),"Batch lookup lost rows through missing typed endpoints")
    created = tx.run(insert,rows=missing).single(strict=True)["created"] if missing else 0
    check(created == len(missing),"Batch insert did not process every resolved row")
    return {"input":len(batch),"created":created,"matched":len(batch)-created}


def retryable(exc):
    return isinstance(exc,(neo_errors.TransientError,neo_errors.ServiceUnavailable,neo_errors.SessionExpired)) and exc.is_retryable()


def commit_batch(session, work, timeout, retries, stats, name, number, intended):
    start = perf_counter()
    for attempt in range(retries+1):
        stats["attempts"] += 1
        try:
            with session.begin_transaction(timeout=timeout) as tx:
                result = work(tx)
                tx.commit()
            stats["committed"] += 1
            return {**result,"attempts":attempt+1,"seconds":round(perf_counter()-start,5)}
        except Exception as exc:
            transient = retryable(exc)
            stats["failures"].append({"label_or_type":name,"batch":number,"attempt":attempt+1,"input":intended,
                        "type":type(exc).__name__,"code":getattr(exc,"code",None),"retryable":transient})
            if not transient or attempt==retries:
                raise
            stats["retries"] += 1
            sleep(min(0.25*2**attempt,2.0))


def import_partition(session,db,kind,name,item,args,report):
    start = perf_counter()
    size = args.node_batch_size if kind=="nodes" else args.relationship_batch_size
    partition = report["partitions"][name] = {"batch_size":size,"batches":[],"status":"in-progress"}
    batches = partition["batches"]
    for number,values in enumerate(records_from_parquet(db,kind,name,item,size),1):
        batches.append(commit_batch(session,lambda tx:write_batch(tx,kind,name,item,values),
                                    args.timeout,args.retries,report["transactions"],name,number,len(values)))
        if args.pause_after_first_batch:
            raise PlannedPause()
    seconds = perf_counter()-start
    total = {key:sum(batch[key] for batch in batches) for key in ("input","created","matched")}
    require(session.run(f"MATCH {'(n:'+name+')' if kind=='nodes' else '()-[n:'+name+']->()'} "
                        "RETURN count(n) AS n").single(strict=True)["n"]==total["input"],f"{name} count mismatch")
    partition.update(total,status="complete",seconds=round(seconds,3),
                     rows_per_second=round(total["input"]/seconds,2))


def local_store_sizes(session, database):
    """Measure file payloads under this server's own data directory if accessible."""
    try:
        setting = session.run("""SHOW SETTINGS YIELD name,value
            WHERE name='server.directories.data' RETURN value""").single()
        if not setting:
            return {"status":"unavailable","reason":"Server data directory setting is not visible"}
        root = Path(setting["value"])
        if not root.is_absolute():
            return {"status":"unavailable","reason":"Server data directory is not absolute"}
        store = root/"databases"/database
        if not store.is_dir():
            return {"status":"unavailable","reason":"Local store directory is inaccessible"}
        index = store/"schema"/"index"
        sizes = {"store_including_indexes_bytes":0,"schema_index_bytes":0}
        for p in store.rglob("*"):
            if p.is_file():
                length = p.stat().st_size
                sizes["store_including_indexes_bytes"] += length
                if index in p.parents:
                    sizes["schema_index_bytes"] += length
        return {"status":"measured","method":"read-only local file lengths, excludes transaction log directory",**sizes}
    except (OSError,neo_errors.Neo4jError):
        return {"status":"unavailable","reason":"Catalog or filesystem access unavailable"}


def verify_target(session,db,schema,m,normalized,timeout,**options):
    with session.begin_transaction(timeout=timeout) as tx:
        evidence = verify_content(tx,db,schema,m,**options)
        if evidence["imported_fingerprint"]:
            start = perf_counter()
            evidence["competencies"] = competencies(tx,db,m,normalized,schema)
            evidence["competency_seconds"] = round(perf_counter()-start,3)
        tx.commit()
    return evidence


def load_target(driver,db,m,n,schema,args,report,uri):
    database = args.database
    environment = inventory(driver,database)
    report["neo4j"] = environment
    if args.preflight:
        if environment["database"]:
            with driver.session(database=database,default_access_mode=READ_ACCESS) as session:
                report["target_contents"] = inspect_target(session,database)[1]
                report["schema_preflight"] = catalog(session,schema,complete=False)
        report["status"]="preflight-passed"
        return
    previous_path = REPORT/"static_graph_manifest.json"
    previous = read(previous_path) if previous_path.exists() else None
    if previous:
        check(environment["database"] and previous["inputs"]==report["inputs"],
              "A previous verified target is missing or refers to a different build")
    if environment["database"] is None:
        check(args.create,"Explicit database creation approval is required")
        with driver.session(database="system") as system:
            system.run("CREATE DATABASE $database WAIT 45 SECONDS",database=database).consume()
        # The database catalog is authoritative even if WAIT returns no status row.
        environment = inventory(driver,database)
        report["neo4j"] = environment
        check(environment["database"] is not None,"Approved dedicated database was not found after creation")
        report["created_database"] = True
    with driver.session(database=database) as session:
        info,state = inspect_target(session,database)
        report["target_contents_before"] = state
        report["target_identity"] = info
        if previous:
            check(previous["target_identity"]==info,"A previous verified report refers to another database")
        pin = {"uri":uri,"target":database,"database_id":info["id"],"creation_date":info["creationDate"],
               "build_id":PINNED_ID,"schema_sha256":m["inputs"]["schema_sha256"]}
        if args.verify or state["snapshots"]:
            check(not args.pause_after_first_batch,"A planned interruption requires an empty target")
            report["mode"] = "read-only verification" if args.verify else "validated matching build / no-op"
            report["schema"] = catalog(session,schema)
            report["verification"] = verify_target(session,db,schema,m,n,args.timeout)
            report["storage"] = local_store_sizes(session,database)
            return
        catalog(session,schema,complete=False)
        check(environment["database"]["access"]=="read-write","Dedicated database is not writable")
        if args.pause_after_first_batch:
            check(not state["nodes"] and not state["relationships"],"A planned interruption requires an empty target")
        # Marker binds recovery to this database identity. Every pre-existing row is then
        # independently verified as a subset before any replay can write.
        marker = args.state_dir/(database+".checkpoint.json")
        resumed = checkpoint(marker,pin,state)
        if resumed:
            report["recovery_preflight"] = verify_target(session,db,schema,m,n,args.timeout,
                                                          partial=True,include_snapshot=False)
        report["mode"] = "resume" if resumed else "initial"
        at = perf_counter()
        for query in statements("neo4j_schema.cypher"):
            session.run(neo4j.Query(query,timeout=args.timeout)).consume()
        report["schema_apply_seconds"] = round(perf_counter()-at,3)
        at = perf_counter()
        session.run(neo4j.Query("CALL db.awaitIndexes(60)",timeout=max(65,args.timeout))).consume()
        report["schema"] = catalog(session,schema)
        report["schema_index_wait_verify_seconds"] = round(perf_counter()-at,3)
        at = perf_counter()
        for kind,name,item in definitions(schema):
            if name=="FeedSnapshot":
                snapshot = (kind,name,item)
            else:
                import_partition(session,db,kind,name,item,args,report)
        report["data_load_seconds"] = round(perf_counter()-at,3)
        report["before_snapshot_verification"] = verify_target(session,db,schema,m,n,args.timeout,include_snapshot=False)
        kind,name,item = snapshot
        import_partition(session,db,kind,name,item,args,report)
        report["verification"] = verify_target(session,db,schema,m,n,args.timeout)
        report["storage"] = local_store_sizes(session,database)


def finished_report(report,path):
    # Immutable report for the first passing construction and separate per-attempt evidence.
    check(report["status"]=="validated" and report["verification"]["imported_fingerprint"]==PINNED_HASH,
          "Only a verified matching imported fingerprint can be published")
    if path.exists():
        previous=read(path)
        check(previous["status"]=="validated" and previous["inputs"]==report["inputs"]
              and previous["target_identity"]==report["target_identity"],
              "An existing verified report refers to another database or build")
        return
    temporary = path.with_suffix(".publishing")
    write(temporary,report)
    temporary.rename(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready",type=Path,default=READY)
    parser.add_argument("--normalized",type=Path,default=BUILD)
    parser.add_argument("--raw",type=Path,default=ROOT/"GTFSTransitData_RG")
    parser.add_argument("--database")
    parser.add_argument("--offline",action="store_true")
    parser.add_argument("--preflight",action="store_true")
    parser.add_argument("--verify",action="store_true")
    parser.add_argument("--write-approved",action="store_true")
    parser.add_argument("--create",action="store_true")
    parser.add_argument("--pause-after-first-batch",action="store_true")
    parser.add_argument("--node-batch-size",type=int,default=2000)
    parser.add_argument("--relationship-batch-size",type=int,default=2000)
    parser.add_argument("--retries",type=int,default=2)
    parser.add_argument("--timeout",type=float,default=180)
    parser.add_argument("--state-dir",type=Path,default=REPORT/"state")
    args = parser.parse_args()
    if (sum((args.offline,args.preflight,args.verify))>1
            or ((args.offline or args.preflight or args.verify) and
                (args.create or args.write_approved or args.pause_after_first_batch))
            or (not (args.offline or args.preflight or args.verify) and not args.write_approved)):
        parser.error("Select an offline/read-only mode or explicitly authorize writes")
    if not (1<=args.node_batch_size<=10000 and 1<=args.relationship_batch_size<=10000
            and 0<=args.retries<=8 and 1<=args.timeout<=1800):
        parser.error("Batch sizes, retries or timeout outside bounded range")
    start,cpu = perf_counter(),process_time()
    report = {"status":"failed","started_utc":now(),"phase":"input preflight",
              "driver":neo4j.__version__,"duckdb":duckdb.__version__,
              "implementation":{"version":VERSION,"files":{name:sha(ROOT/"scripts"/name)
                for name in ("import_static_graph.py","verify_imported_graph.py")}},
              "configuration":{"database":args.database,"node_batch_size":args.node_batch_size,
                               "relationship_batch_size":args.relationship_batch_size,"max_retries":args.retries,
                               "transaction_timeout_seconds":args.timeout},
              "transactions":{"attempts":0,"committed":0,"retries":0,"failures":[]},"partitions":{}}
    report["test_evidence"] = "automated_test_report.json"
    code = 0
    try:
        with duckdb.connect(config={"threads":2}) as db:
            m,n,schema = preflight(db,args.ready,args.normalized,args.raw)
            before = sha(args.ready/"manifest.json")
            report["inputs"] = {**m["inputs"],"graph_ready_build_id":PINNED_ID,
                                "graph_ready_manifest_sha256":before,"import_contract_sha256":sha(IMPORT_CONTRACT)}
            report["input_counts"] = m["counts"]
            report["expected_static_graph_fingerprint"] = PINNED_HASH
            report["offline_validation"] = {
                **{key:m["validation"][key] for key in ("raw_files_verified","normalized_files_verified",
                    "duplicate_identities","dangling_endpoints","shared_stops","transfers","ac_selector")},
                "graph_ready_files_verified":len(m["artifacts"]),
                "parquet_fingerprint":PINNED_HASH,
            }
            report["external"] = {**m["external"],"verification":{k:m["validation"][k] for k in
              ("timetable","external_temporal_and_shapes","canonical_mappings","suffix_collision_groups","exception_ledger")}}
            report["input_seconds"] = round(perf_counter()-start,3)
            if args.offline:
                report["status"]="offline-inputs-verified"
            else:
                report["phase"]="Neo4j target and import"
                uri = target_settings(args.database)
                report["configuration"]["uri"] = uri
                report["reader_isolation"] = "Dedicated database and unpublished manifest until passing. Reader access is operationally withheld; server permissions are unchanged."
                with local_lock(args.state_dir/(args.database+".lock")), connect(uri) as driver:
                    driver.verify_connectivity()
                    load_target(driver,db,m,n,schema,args,report,uri)
                verify_ready_files(args.ready,m)
                normalized_after,_,_ = pin_inputs(args.normalized,args.raw)
                check(normalized_after==n and sha(args.ready/"manifest.json")==before,
                      "An immutable input changed during construction")
                report["input_immutability_rechecked"] = True
                if report.get("verification",{}).get("imported_fingerprint")==PINNED_HASH:
                    report["status"]="validated"
    except PlannedPause:
        report["status"] = "paused-after-committed-batch"
    except Exception as exc:
        from neo4j.exceptions import Neo4jError
        code = 1
        report["status"]="failed"
        report["error"]={"type":type(exc).__name__}
        if isinstance(exc,(ImportSafety,ValueError)):
            report["error"]["detail"]=str(exc)
        if isinstance(exc,Neo4jError):
            report["error"]["code"]=exc.code
    report["ended_utc"] = now()
    report["performance"] = {"wall_seconds":round(perf_counter()-start,3),
                             "client_cpu_seconds":round(process_time()-cpu,3),"client_peak_rss_mib":peak_rss_mib()}
    if report["partitions"]:
        node_names = set(report["input_counts"]["nodes"])
        for kind in ("nodes","relationships"):
            grouped = [part for name,part in report["partitions"].items() if (name in node_names)==(kind=="nodes")
                       and part["status"]=="complete"]
            totals = Counter()
            for partition in grouped:
                for key in ("created","matched","seconds"):
                    totals[key] += partition[key]
            if totals["seconds"]:
                report["performance"][kind] = {**totals,
                    "created_per_second":round(totals["created"]/totals["seconds"],2)}
    REPORT.mkdir(parents=True,exist_ok=True)
    if args.offline:
        filename = "offline_preflight.json"
    elif args.preflight:
        filename = "live_preflight.json"
    elif report["status"]=="paused-after-committed-batch":
        filename = "recovery_checkpoint_test.json"
    else:
        filename = "last_database_attempt.json"
    path = REPORT/filename
    if report["status"]=="validated":
        try:
            finished_report(report,REPORT/"static_graph_manifest.json")
        except (ImportSafety,OSError) as exc:
            code = 1
            report["status"]="failed"
            report["error"]={"type":type(exc).__name__,"detail":"Verified manifest could not be published"}
    write(path,report)
    print(json.dumps({"status":report["status"],"report":str(path),"error":report.get("error")}))
    return code


if __name__=="__main__":
    raise SystemExit(main())

