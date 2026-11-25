"""Independent, streamed Neo4j graph verification for the Phase 6 importer."""
from collections import Counter
from hashlib import sha256
from time import perf_counter

from design_static_graph import check_snapshot, rows, scalar
from transform_static_graph import FORMAT, columns, definitions, encode, key_columns
from validate_graph_ready import require
from verify_phase4_neo4j import statements


def typed_properties(properties, specs):
    extra = properties.keys() - specs.keys()
    require(not extra, f"Undeclared properties: {sorted(extra)}")
    output = {}
    for name, spec in specs.items():
        value = properties.get(name)
        if value is None:
            require(not spec["required"], f"Missing required property {name}")
        elif spec["type"] == "STRING":
            require(type(value) is str, f"Invalid string property {name}")
        elif spec["type"] == "INTEGER":
            require(type(value) is int and -(2**63) <= value < 2**63, f"Invalid signed integer property {name}")
        else:
            raise ValueError(f"Unsupported property type {spec['type']}")
        if value is not None and "allowed" in spec:
            require(value in spec["allowed"], f"Invalid categorical property {name}")
        output[name] = value
    return output


def identity(kind, name, item, row):
    key = tuple(row[k] for k in key_columns(kind, item))
    return key[0] if kind == "nodes" else (name, *key)


def serialized(kind, name, item, row):
    props = {p:row[p] for p in item["properties"] if not (name=="FeedSnapshot" and p=="static_fingerprint")}
    return encode(["N" if kind=="nodes" else "R", name, identity(kind,name,item,row), props])


def partition_digest(records, whole=None):
    digest = sha256((FORMAT+"\n").encode("ascii"))
    for line in sorted(records):
        payload = line+b"\n"
        digest.update(payload)
        if whole is not None:
            whole.update(payload)
    return digest.hexdigest()


def records_from_parquet(db, kind, name, item, size):
    fields = list(columns(kind,item))
    cursor = db.execute(f"SELECT {', '.join(fields)} FROM graph_{name} ORDER BY {', '.join(key_columns(kind,item))}")
    while batch := cursor.fetchmany(size):
        yield [dict(zip(fields,row)) for row in batch]


def read_partition(tx, kind, name, item):
    if kind == "nodes":
        query = f"MATCH (n:{name}) RETURN labels(n) AS labels, properties(n) AS props"
    else:
        query = f"""MATCH (a)-[r:{name}]->(b)
             RETURN labels(a) AS start_labels,labels(b) AS end_labels,
               a.canonical_id AS start_id,b.canonical_id AS end_id,properties(r) AS props"""
    seen = set()
    for result in tx.run(query):
        if kind == "nodes":
            require(result["labels"] == [name], f"{name} has ambiguous labels")
            row = {}
        else:
            require(result["start_labels"] == [item["from"]] and result["end_labels"] == [item["to"]],
                    f"{name} has incorrectly typed endpoints")
            row = {field:result[field] for field in ("start_id","end_id")}
            require(all(type(v) is str for v in row.values()), f"{name} endpoint missing its canonical ID")
        row.update(typed_properties(result["props"],item["properties"]))
        key = identity(kind,name,item,row)
        require(key not in seen,f"Duplicate {name} identity {key!r}")
        seen.add(key)
        yield row


def compare_partition(tx, db, kind, name, item, manifest, whole, partial, include_snapshot):
    path = f"{kind}/{name}.parquet"
    exported = manifest["artifacts"][path]
    actual = {}
    for row in read_partition(tx,kind,name,item):
        if name == "FeedSnapshot":
            require(row["static_fingerprint"] == manifest["expected_static_graph_fingerprint"],
                    "FeedSnapshot fingerprint field differs from the pinned value")
        actual[identity(kind,name,item,row)] = serialized(kind,name,item,row)
    if name=="FeedSnapshot" and not include_snapshot:
        require(not actual,"Readiness metadata already exists")
        return 0,None
    digest = partition_digest(actual.values(),whole)
    if len(actual) == exported["records"] and digest == exported["logical_sha256"]:
        return len(actual),digest
    differences = {"missing":0,"extra":len(actual),"changed":0,"examples":[]}
    for batch in records_from_parquet(db,kind,name,item,4096):
        for row in batch:
            key = identity(kind,name,item,row)
            found = actual.pop(key,None)
            if found is None:
                differences["missing"] += 1
                continue
            differences["extra"] -= 1
            if found != serialized(kind,name,item,row):
                differences["changed"] += 1
                if len(differences["examples"]) < 3:
                    differences["examples"].append(key)
    differences["examples"].extend(list(actual)[:3])
    require(partial and not differences["extra"] and not differences["changed"],
            f"{name} database/Parquet mismatch: {differences}")
    return exported["records"]-differences["missing"],digest


def verify_content(tx, db, schema, manifest, *, partial=False, include_snapshot=True):
    """Read every node and edge. Per-partition counts are checked against global counts."""
    start = perf_counter()
    whole = sha256((FORMAT+"\n").encode("ascii"))
    counts = {"nodes":{},"relationships":{}}
    digests = {}
    for kind,name,item in sorted(definitions(schema),key=lambda d:(d[0],d[1])):
        count,digest = compare_partition(tx,db,kind,name,item,manifest,whole,partial,include_snapshot)
        counts[kind][name] = count
        digests[name] = digest
    for kind,query in (("nodes","MATCH (n) RETURN count(n) AS n"),
                       ("relationships","MATCH ()-[r]->() RETURN count(r) AS n")):
        require(tx.run(query).single(strict=True)["n"] == sum(counts[kind].values()),
                f"Undeclared {kind} or ambiguous labels in target")
    imported = whole.hexdigest() if include_snapshot and not partial else None
    if imported is not None:
        require(imported == manifest["expected_static_graph_fingerprint"],"Imported static fingerprint differs")
    return {"counts":counts,"imported_fingerprint":imported,"partition_digests":digests,
            "duplicate_identities":0,"incorrect_endpoints":0,"seconds":round(perf_counter()-start,3)}


def assert_catalog(constraints, indexes, schema, complete=True):
    expected_constraints = {f'uq_{n["label"]}':("UNIQUENESS","NODE",[n["label"]],[n["identity"]])
                            for n in schema["nodes"]}
    expected_indexes = {f"ix_{label}_{field}":("RANGE","NODE",[label],[field],None)
                        for label,field in schema["indexes"]}
    for name,(_,entity,labels,properties) in expected_constraints.items():
        expected_indexes[name] = ("RANGE",entity,labels,properties,name)
    found_constraints = {r["name"]:(r["type"],r["entityType"],r["labelsOrTypes"],r["properties"])
                         for r in constraints}
    found_indexes = {r["name"]:(r["type"],r["entityType"],r["labelsOrTypes"],r["properties"],r["owningConstraint"])
                     for r in indexes if r["type"]!="LOOKUP"}
    for actual,expected,kind in ((found_constraints,expected_constraints,"constraints"),(found_indexes,expected_indexes,"indexes")):
        require(all(expected.get(n)==v for n,v in actual.items()),f"Conflicting schema {kind}")
        if complete:
            require(actual==expected,f"Missing approved schema {kind}")
    if complete:
        require(all(r["state"]=="ONLINE" for r in indexes if r["type"]!="LOOKUP"),"A required index is offline")
    return {"constraints":constraints,"indexes":indexes}


def catalog(tx, schema, complete=True):
    return assert_catalog(tx.run("CYPHER 5 SHOW CONSTRAINTS").data(),tx.run("CYPHER 5 SHOW INDEXES").data(),schema,complete)


def competencies(tx, db, manifest, normalized, schema):
    """Check the 14 approved Cypher contracts against normalized SQL, plus hybrid scope."""
    params = dict(agency="AC",stop="55660",route="AC:72",trip="AC:10056070",
                  name="San Pablo Av & W Grand Av", parent="-325",target="241",direction=0,route_type=3)
    reference_sql = [
        ("SELECT canonical_stop_id FROM agency_stops WHERE agency_id=? AND stop_id=?", ["AC","55660"]),
        ("SELECT stop_id FROM stops WHERE stop_name=? ORDER BY 1", [params["name"]]),
        ("SELECT agency_id FROM agency_stops WHERE stop_id=? ORDER BY 1", ["55660"]),
        ("""SELECT i.canonical_id FROM routes r JOIN canonical_ids i ON i.entity_type='route'
            AND i.source_identifier=r.route_id WHERE r.agency_id=? AND r.route_id=?""", ["AC","AC:72"]),
        ("SELECT canonical_id FROM canonical_ids WHERE entity_type='trip' AND source_identifier=?", [params["trip"]]),
        ("SELECT route_id FROM route_stops WHERE agency_id=? AND stop_id=? ORDER BY 1", ["AC","55660"]),
        ("SELECT stop_id FROM route_stops WHERE route_id=? ORDER BY 1", ["AC:72"]),
        ("SELECT trip_id FROM trips WHERE route_id=? ORDER BY 1", ["AC:72"]),
        ("SELECT service_id FROM trips WHERE trip_id=?", [params["trip"]]),
        ("SELECT stop_id FROM stops WHERE parent_station=? ORDER BY 1", [params["parent"]]),
        ("""SELECT from_route_id,to_route_id,from_trip_id,to_trip_id,transfer_type,min_transfer_time
            FROM transfers WHERE from_stop_id=? AND to_stop_id=?""", ["241","241"]),
        ("SELECT stop_id,count(DISTINCT agency_id) FROM agency_stops GROUP BY 1 HAVING count(DISTINCT agency_id)>1 ORDER BY 1", []),
        ("""SELECT trip_id FROM trips JOIN routes USING(route_id)
            WHERE agency_id=? AND route_id=? AND route_type=? AND direction_id=? ORDER BY 1""", ["AC","AC:72",3,0]),
    ]
    queries = statements("competency.cypher")
    checked = {}
    for number,(query,(sql,args)) in enumerate(zip(queries[:-1],reference_sql,strict=True),1):
        given = {**params,"stop":"241"} if number==11 else params
        actual = [tuple(record.values()) for record in tx.run(query,given)]
        expected = db.execute(sql,args).fetchall()
        if number==11:
            expected = [(dict((k,v) for k,v in record.items() if v is not None),) for record in rows(db,sql,args)]
            require(Counter(map(encode,actual))==Counter(map(encode,expected)),f"G{number} differs from SQL")
        else:
            require(actual==expected,f"G{number} differs from SQL")
        checked[f"G{number}"] = len(actual)
    check_snapshot([r.values() for r in tx.run(queries[-1])],normalized,schema)
    checked["G14"] = 1

    scoped_query = """MATCH (a:Agency {source_id:$agency})-[:AGENCY_SERVES_STOP]->(s:Stop {source_id:$stop})
        MATCH (a)-[:OPERATES]->(r:Route)-[:ROUTE_SERVES_STOP]->(s)
        MATCH (r)-[:HAS_TRIP]->(t:Trip)-[:RUNS_ON]->(v:Service)
        WHERE $direction IS NULL OR t.direction_id=$direction
        RETURN t.canonical_id AS canonical_trip_id, t.source_id AS trip_id, r.source_id AS route_id,
               v.source_id AS service_id,s.source_id AS stop_id"""
    def hybrid(agency, stop, direction):
        graph_rows = tx.run(scoped_query,agency=agency,stop=stop,direction=direction).data()
        db.execute("""CREATE OR REPLACE TEMP TABLE phase6_candidates(canonical_trip_id VARCHAR,trip_id VARCHAR,
                       route_id VARCHAR,service_id VARCHAR,stop_id VARCHAR)""")
        if graph_rows:
            db.execute("""INSERT INTO phase6_candidates BY NAME
                       SELECT x.* FROM (SELECT unnest($rows) AS x)
                       """,{"rows":graph_rows})
        return db.execute("""SELECT c.route_id,count(DISTINCT c.trip_id),
            count(DISTINCT c.trip_id) FILTER(WHERE active.service_id IS NOT NULL)
            FROM phase6_candidates c JOIN stop_times s USING(trip_id,stop_id)
            JOIN trip_selector_context x ON x.canonical_trip_id=c.canonical_trip_id
            LEFT JOIN active_services(DATE '2026-11-25') active ON active.service_id=c.service_id
            GROUP BY 1 ORDER BY 1""").fetchall()

    # Rows from Neo4j constrain the external visit query by graph trip, stop and service IDs.
    ac = hybrid("AC","55660",None)
    neighbor = hybrid("AC","52227",None)
    evidence = manifest["validation"]["ac_selector"]
    require([(r,n) for r,n,_ in ac]==[(r["route_id"],r["trips"]) for r in evidence["55660"]],"AC/55660 scope mismatch")
    require([(r,n) for r,n,_ in neighbor]==[(r["route_id"],r["trips"]) for r in evidence["52227"]],"AC/52227 scope mismatch")
    require(sum(r[2] for r in ac)==evidence["dated_trips"],"AC dated scope mismatch")
    require(not hybrid("BA","55660",None) and not hybrid("AC","55660",1),"Contradictory selector matched")
    names = [r["id"] for r in tx.run(queries[1],params)]
    require(names==["52227","55660"],"Same-name stop ambiguity changed")
    identities = {stop:tx.run(queries[0],{**params,"stop":stop}).single(strict=True)["id"] for stop in names}
    require(all(identities[stop]==evidence[stop][0]["canonical_id"] for stop in names),"AC canonical identity changed")
    sequence = db.execute("SELECT stop_sequence,stop_id FROM stop_times WHERE trip_id=? ORDER BY 1",[params["trip"]]).fetchall()
    require(len(sequence)==77 and [r[1] for r in sequence[:3]]==["55888","56899","55802"],"Ordered trip example differs")
    services = scalar(db,"SELECT count(*) FROM active_services(DATE '2026-11-25')")
    require(services==125,"Service availability reference differs")
    return {"cypher":checked,"shared_stops":checked["G12"],"sequence_visits":len(sequence),"active_services":services,
            "ac_selector":{"status":"EXACT_MATCH","stop_ids":identities,"routes":ac,
                           "visiting_trips":sum(r[1] for r in ac),"dated_visiting_trips":sum(r[2] for r in ac),
                           "neighbor_visiting_trips":sum(r[1] for r in neighbor),"service_date":"2026-11-25",
                           "wrong_agency_matches":0,"wrong_direction_matches":0,"name_only":"AMBIGUOUS"}}

