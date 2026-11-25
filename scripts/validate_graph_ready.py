"""Validate reopened Parquet against source keys, source payloads and independent timetable joins."""
from design_static_graph import rows, scalar


def require(condition, message):
    if not condition:
        raise ValueError(message)


def equal_sets(db, actual, expected, label):
    for lhs, rhs in ((actual,expected),(expected,actual)):
        sample = db.sql(f"SELECT * FROM (({lhs}) EXCEPT ALL ({rhs})) LIMIT 3").fetchall()
        require(not sample, f"{label} differs, examples: {sample}")


def validate_columns(db, view, fields, properties, keys):
    relation = db.sql(f"SELECT * FROM {view}")
    require(list(zip(relation.columns,map(str,relation.types))) == list(fields.items()),
            f"{view} column or type mismatch")
    for field, spec in properties.items():
        if spec["required"]:
            require(not scalar(db,f"SELECT count(*) FROM {view} WHERE {field} IS NULL"),
                    f"{view}.{field} contains nulls")
        if "allowed" in spec:
            require(not scalar(db,f"SELECT count(*) FROM {view} WHERE {field} NOT IN (SELECT unnest(?))",
                               [spec["allowed"]]), f"{view}.{field} contains invalid values")
    if "start_id" in fields:
        require(not scalar(db,f"SELECT count(*) FROM {view} WHERE start_id IS NULL OR end_id IS NULL"),
                f"{view} has missing endpoint IDs")
    keys = ", ".join(keys)
    examples = db.sql(f"SELECT {keys},count(*) FROM {view} GROUP BY {keys} HAVING count(*)>1 LIMIT 5").fetchall()
    require(not examples,f"{view} has duplicate identities: {examples}")


def check_source_types(db, table, field_types):
    actual = dict((name,str(dtype)) for name,dtype,*_ in db.sql(f"SELECT * FROM {table}").description)
    for field, expected in field_types.items():
        compatible = {"VARCHAR"} if expected == "VARCHAR" else {"INTEGER","BIGINT"}
        require(actual.get(field) in compatible,f"{table}.{field} has an incompatible source type")


def validate(db, schema, definitions, columns, key_columns, metadata):
    counts = {"nodes":{},"relationships":{}}
    for kind,name,item in definitions:
        view = "graph_"+name
        props, table = item["properties"], item["source"]
        fields = columns(kind,item)
        validate_columns(db,view,fields,props,key_columns(kind,item))
        counts[kind][name] = scalar(db,f"SELECT count(*) FROM {view}")
        if name == "FeedSnapshot":
            require(db.sql(f"SELECT * FROM {view}").fetchall() == [tuple(metadata.values())],
                    "FeedSnapshot differs from pinned metadata")
            continue
        mappings = {p:spec["source"].split(".")[-1] for p,spec in props.items()}
        if kind == "nodes":
            entity = item["entity_type"]
            equal_sets(db, f"SELECT source_id,canonical_id FROM {view}",
                       f"SELECT source_identifier,canonical_id FROM canonical_ids WHERE entity_type='{entity}'",
                       name+" mapping")
            if table != "canonical_ids":
                payload = [p for p in props if p not in ("source_id","canonical_id")]
                check_source_types(db, table, {mappings[p]:fields[p] for p in payload} | {entity+"_id":"VARCHAR"})
                equal_sets(db, f"SELECT source_id, {', '.join(payload)} FROM {view}",
                           f"SELECT {entity}_id, {', '.join(mappings[p] for p in payload)} FROM {table}",
                           name+" source payload")
            continue
        first,last = item["start_column"],item["end_column"]
        check_source_types(db,table,{mappings[p]:fields[p] for p in props} | {first:"VARCHAR",last:"VARCHAR"})
        if name != "PART_OF":
            source_record = "source_record, " if name == "TRANSFER_RULE" else ""
            missing = db.sql(f"SELECT {source_record}{first}, {last} FROM {table} WHERE {first} IS NULL OR {last} IS NULL LIMIT 10").fetchall()
            require(not missing,f"{name} source rows have missing endpoints: {missing}")
        decoded = f"""SELECT a.source_id AS first_id,b.source_id AS last_id
                     {''.join(', g.'+p for p in props)}
                     FROM {view} g JOIN graph_{item["from"]} a ON a.canonical_id=g.start_id
                     JOIN graph_{item["to"]} b ON b.canonical_id=g.end_id"""
        require(scalar(db,f"SELECT count(*) FROM ({decoded})") == counts[kind][name], f"{name} has dangling or mistyped endpoints")
        normalized = f"SELECT {first}, {last}{''.join(', '+mappings[p] for p in props)} FROM {table} WHERE {last} IS NOT NULL"
        equal_sets(db,decoded,normalized,name+" source correspondence")
        if name == "TRANSFER_RULE":
            for prop in item["identity"][3:]:
                label = "Route" if prop.endswith("route_id") else "Trip"
                require(not scalar(db,f"""SELECT count(*) FROM {view} g ANTI JOIN graph_{label} n
                            ON g.{prop}=n.source_id WHERE g.{prop} IS NOT NULL"""),f"Invalid transfer restriction {prop}")
            require(not scalar(db,f"SELECT count(*) FROM {view} WHERE min_transfer_time<0 OR (transfer_type=2 AND min_transfer_time IS NULL)"),
                    "Invalid minimum transfer time")
    for name, label, edge in (("OPERATES","Route","end_id"),("HAS_TRIP","Trip","end_id"),("RUNS_ON","Trip","start_id")):
        equal_sets(db,f"SELECT {edge} FROM graph_{name}",f"SELECT canonical_id FROM graph_{label}",name+" mandatory multiplicity")
    require(not scalar(db,"""SELECT count(*) FROM graph_PART_OF g
        JOIN graph_Stop a ON a.canonical_id=g.start_id JOIN graph_Stop b ON b.canonical_id=g.end_id
        WHERE g.start_id=g.end_id OR coalesce(b.location_type,0) <>
          CASE WHEN a.location_type=4 THEN 0 ELSE 1 END OR a.location_type=1"""),
        "Invalid stop hierarchy levels")
    require(not scalar(db,"SELECT count(*) FROM (SELECT start_id FROM graph_PART_OF GROUP BY 1 HAVING count(*)>1)"),
            "Stop has multiple parents")
    return {"status":"passed","counts":counts,"duplicate_identities":0,"dangling_endpoints":0}


def scoped(db, agency, stop, direction=None, date=None):
    restriction = " AND t.direction_id=?" if direction is not None else ""
    args = [agency,stop]
    if direction is not None:
        args.append(direction)
    service = "JOIN active_services(?) active ON active.service_id=s.source_id" if date else ""
    if date:
        args.insert(0,date)
    return rows(db, f"""SELECT p.source_id AS stop_id,p.canonical_id,p.stop_name,r.source_id AS route_id,
        count(DISTINCT t.canonical_id) AS trips
        FROM graph_Agency a JOIN graph_AGENCY_SERVES_STOP ao ON ao.start_id=a.canonical_id
        JOIN graph_Stop p ON p.canonical_id=ao.end_id
        JOIN graph_OPERATES ar ON ar.start_id=a.canonical_id
        JOIN graph_Route r ON r.canonical_id=ar.end_id
        JOIN graph_ROUTE_SERVES_STOP rs ON rs.start_id=r.canonical_id AND rs.end_id=p.canonical_id
        JOIN graph_HAS_TRIP rt ON rt.start_id=r.canonical_id
        JOIN graph_Trip t ON t.canonical_id=rt.end_id
        JOIN graph_RUNS_ON ts ON ts.start_id=t.canonical_id
        JOIN graph_Service s ON s.canonical_id=ts.end_id
        {service}
        JOIN stop_times v ON v.trip_id=t.source_id AND v.stop_id=p.source_id
        WHERE a.source_id=? AND p.source_id=? {restriction} GROUP BY ALL ORDER BY r.source_id""",args)


def validate_regional(db, baseline, manifest):
    counts = {k:scalar(db,f"SELECT count(*) FROM graph_{k}") for k in [*baseline["nodes"],*baseline["edges"]]}
    require(counts == baseline["nodes"] | baseline["edges"],"Export counts differ from approved regional baseline")
    reference = baseline["reference"]
    for e, table, entity, measure, prefix in (
        ("ROUTE_SERVES_STOP", "graph_Route", "route_id", "scheduled_trips", ""),
        ("AGENCY_SERVES_STOP", "graph_Agency", "agency_id", "serving_routes", "JOIN routes USING(route_id)")
    ):
        count_distinct = "trip_id" if not prefix else "route_id"
        equal_sets(db, f"""SELECT a.source_id,b.source_id,g.scheduled_visits,g.{measure} FROM graph_{e} g
                 JOIN {table} a ON a.canonical_id=g.start_id JOIN graph_Stop b ON b.canonical_id=g.end_id""",
                   f"""SELECT {entity},stop_id,count(*),count(DISTINCT {count_distinct})
                   FROM stop_times JOIN trips USING(trip_id) {prefix} GROUP BY ALL""", e+" independent timetable grouping")
    for table,entity in (("route_stops","route"),("agency_stops","agency")):
        equal_sets(db,f"SELECT canonical_{entity}_id,canonical_stop_id FROM {table}",
                   f"SELECT start_id,end_id FROM graph_{entity.upper()}_SERVES_STOP",table+" canonical references")
    equal_sets(db, "SELECT source_id FROM graph_Service",
               "SELECT service_id FROM calendar UNION SELECT service_id FROM calendar_dates WHERE exception_type=1",
               "service domain")
    equal_sets(db, "SELECT source_identifier FROM canonical_ids WHERE entity_type='shape'",
               "SELECT DISTINCT shape_id FROM shapes","external shape domain")
    require(not scalar(db,"""SELECT count(*) FROM graph_Trip t ANTI JOIN canonical_ids c
         ON c.entity_type='shape' AND c.source_identifier=t.shape_id WHERE t.shape_id IS NOT NULL"""),
         "Trip contains a missing shape")
    inventory = {r["entity_type"]:r["n"] for r in rows(db,"SELECT entity_type,count(*) n FROM canonical_ids GROUP BY 1")}
    require(inventory==baseline["mapping_counts"],"Canonical domain inventory differs")
    require(not scalar(db,"""SELECT count(*)-count(DISTINCT canonical_id) FROM canonical_ids""")
            and not scalar(db,"""SELECT count(*) FROM canonical_ids WHERE
                source_snapshot_id IS DISTINCT FROM ? OR source_namespace IS DISTINCT FROM ?
                OR verification_status IS DISTINCT FROM 'VERIFIED_STATIC' OR source_identifier IS NULL""",
                           [manifest["source_snapshot_id"],manifest["source_namespace"]]),"Invalid canonical mappings")
    actual = rows(db,"""SELECT count(*) AS visits, count(*) FILTER(WHERE arrival_time_seconds>86400) arrivals,
              count(*) FILTER(WHERE departure_time_seconds>86400) departures,
              count(*) FILTER(WHERE stop_id IS NULL OR location_id IS NOT NULL OR location_group_id IS NOT NULL) flex
              FROM stop_times""")[0]
    require(actual == {"visits":baseline["visits"], **reference["extended_times"],"flex":0},"Timetable baseline differs")
    require(not scalar(db,"SELECT count(*)-count(DISTINCT (trip_id,stop_sequence)) FROM stop_times"),"Visit key duplication")
    repeated = scalar(db,"SELECT count(*) FROM (SELECT trip_id FROM stop_times GROUP BY 1 HAVING count(*)>count(DISTINCT stop_id))")
    require(repeated == baseline["patterns"]["trips_with_repeated_stops"],"Repeated stop occurrence count differs")
    shared = scalar(db,"SELECT count(*) FROM (SELECT end_id FROM graph_AGENCY_SERVES_STOP GROUP BY 1 HAVING count(*)>1)")
    suffix = scalar(db,"""SELECT count(*) FROM (SELECT regexp_replace(source_id,'^[A-Za-z0-9]+[:_]','') AS suffix
                    FROM graph_Stop GROUP BY 1 HAVING count(*)>1)""")
    suffix_expected = 364  # Regional acceptance baseline, never used for generation.
    require((shared,suffix)==(reference["shared_stops"],suffix_expected),"Shared stop or suffix count differs")
    temporal = {"calendar_end_2099":scalar(db,"SELECT count(*) FROM calendar WHERE year(end_date)=2099"),
                "exceptions":scalar(db,"SELECT count(*) FROM calendar_dates"),"shape_points":scalar(db,"SELECT count(*) FROM shapes")}
    require(all(temporal[k]==reference[k] for k in temporal),"Calendar/shape reference differs")
    for table in ("directions","fare_transfer_rules"):
        require(not scalar(db,f"""SELECT count(*) FROM {table} JOIN row_exception_ledger USING(source_record)
              WHERE source_file=? AND disposition='QUARANTINED'""",[table+".txt"]),"Quarantine leaked into accepted inputs")
    ledger = rows(db,"SELECT source_file,disposition,count(*) AS records FROM row_exception_ledger GROUP BY ALL ORDER BY ALL")
    flagged = scalar(db,"""SELECT count(*) FROM shapes JOIN row_exception_ledger USING(source_record)
               WHERE source_file='shapes.txt' AND disposition='FLAGGED'""")
    require(flagged == 21 and sum(r["records"] for r in ledger if r["disposition"]=="QUARANTINED")==70,"Missing ledger records")
    numeric = scalar(db,"SELECT count(*) FROM graph_Trip WHERE direction_id=0 AND source_id IN (SELECT trip_id FROM trips WHERE route_id='WH:53')")
    require(numeric > 0,"WH:53 numeric trip directions were lost")
    ac = scoped(db,"AC","55660")
    neighbor = scoped(db,"AC","52227")
    for stop, result in (("55660",ac),("52227",neighbor)):
        expected = [{("canonical_id" if k=="canonical_stop_id" else k):v for k,v in row.items()}
                    for row in reference["scope_sample"][stop]]
        require(result==expected, f"AC/{stop} differs from Phase 4 reference")
    dated = sum(r["trips"] for r in scoped(db,"AC","55660",date="2026-11-25"))
    require(dated == reference["dated_example"]["trips"],"Date-scoped trip count differs")
    require(not scoped(db,"BA","55660") and not scoped(db,"AC","55660",1),"Contradictory selector matched")
    twins = db.sql("SELECT source_id FROM graph_Stop WHERE stop_name='San Pablo Av & W Grand Av' ORDER BY 1").fetchall()
    require(twins == [("52227",),("55660",)],"Name-only ambiguity changed")
    transfers = rows(db,"""SELECT transfer_type,count(*) AS n,count(from_route_id) AS from_route,
      count(to_route_id) AS to_route,count(from_trip_id) AS from_trip,count(to_trip_id) AS to_trip
      FROM graph_TRANSFER_RULE GROUP BY 1 ORDER BY 1""")
    require(transfers==reference["transfers"],"Transfer restrictions differ")
    return {"canonical_mappings":inventory,"shared_stops":shared,"suffix_collision_groups":suffix,
            "normalized_source_records":sum(a["records"] for a in manifest["artifacts"].values() if a["role"]=="source table"),
            "normalized_artifact_bytes":manifest["parquet_bytes"],"raw_files_verified":len(manifest["input_files"]),
            "normalized_files_verified":len(manifest["artifacts"]),"timetable":actual,"repeated_stop_trips":repeated,
            "external_temporal_and_shapes":temporal,"exception_ledger":ledger,"retained_flagged_points":flagged,
            "wh53_direction_zero_trips":numeric,"transfers":transfers,
            "ac_selector":{"status":"EXACT_MATCH","55660":ac,"52227":neighbor,"dated_service_day":"2026-11-25",
                          "dated_trips":dated,"name_only":"AMBIGUOUS","wrong_agency":0,"wrong_direction":0}}

