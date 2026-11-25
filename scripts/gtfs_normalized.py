"""Materialized identity, association, and integrity checks for the normalized snapshot."""

import hashlib
import json

from gtfs_checks import clean, gtfs_seconds, literal, q, query, references, service_domain, value
from gtfs_cleaning import field_specs
from gtfs_network import conditional_checks
from profile_gtfs import SCHEMA

CONTRACT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
ENTITY_TABLES = {"agency": ("agency", "agency_id"), "stop": ("stops", "stop_id"),
                 "route": ("routes", "route_id"), "trip": ("trips", "trip_id"), "shape": ("shapes", "shape_id")}


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def canonical_expression(namespace, entity, source_id):
    return f"{literal(f'{namespace}:{entity}:')} || url_encode({source_id})"


def compatible_views(db, tables, actual_schemas):
    compatible = {}
    for name, table in tables.items():
        schema = actual_schemas[name]
        expressions = []
        for column in table["columns"]:
            ref = q(column)
            if schema[column] == "DATE":
                ref = f"strftime({ref}, '%Y%m%d')"
            elif schema[column] != "VARCHAR":
                ref = (f"CASE WHEN list_contains(source_empty_typed_fields, {literal(column)}) THEN '' "
                       f"ELSE {ref}::VARCHAR END")
            expressions.append(f"{ref} AS {q(column)}")
        view = f"normalized_gtfs_{name}"
        db.execute(f"CREATE TEMP VIEW {q(view)} AS SELECT {', '.join(expressions)} FROM {q(name)}")
        compatible[name] = {**table, "sql_table": view}
    return compatible


def identity_domain(db, tables):
    pieces = []
    for entity, (name, field) in ENTITY_TABLES.items():
        if name in tables:
            pieces.append(f"""SELECT DISTINCT {literal(entity)} AS entity_type, {q(field)} AS source_identifier,
                [{literal(tables[name]['path'])}] AS source_tables FROM {q(name)}
                WHERE {clean(q(field))} IS NOT NULL""")
    service_sources = []
    for name in ("calendar", "calendar_dates"):
        if name in tables:
            where = " WHERE exception_type = 1 AND date IS NOT NULL" if name == "calendar_dates" else ""
            service_sources.append(f"SELECT service_id, {literal(tables[name]['path'])} AS source_table FROM {q(name)}{where}")
    if service_sources:
        pieces.append(f"""SELECT 'service' AS entity_type, service_id AS source_identifier,
            list(DISTINCT source_table ORDER BY source_table) AS source_tables
            FROM ({" UNION ALL ".join(service_sources)}) SEMI JOIN service_ids USING (service_id)
            GROUP BY service_id""")
    db.execute("CREATE TEMP VIEW identity_domain AS " + " UNION ALL ".join(pieces))


def mappings_query(namespace, snapshot):
    return f"""SELECT entity_type, {literal(namespace)} AS source_namespace, source_identifier,
        {literal(namespace + ':')} || entity_type || ':' || url_encode(source_identifier) AS canonical_id,
        source_tables, {literal(snapshot)} AS source_snapshot_id,
        'regional feed and entity type' AS identity_scope,
        'authoritative GTFS source ID' AS mapping_method, 'VERIFIED_STATIC' AS verification_status
        FROM identity_domain"""


def route_stop_query(namespace):
    return f"""SELECT r.agency_id, r.route_id, s.stop_id,
        {canonical_expression(namespace, 'agency', 'r.agency_id')} AS canonical_agency_id,
        {canonical_expression(namespace, 'route', 'r.route_id')} AS canonical_route_id,
        {canonical_expression(namespace, 'stop', 's.stop_id')} AS canonical_stop_id,
        count(*) AS scheduled_visits, count(DISTINCT t.trip_id) AS scheduled_trips
        FROM stop_times v JOIN trips t USING (trip_id) JOIN routes r USING (route_id)
        JOIN agency a ON r.agency_id = a.agency_id JOIN stops s ON v.stop_id = s.stop_id
        GROUP BY r.agency_id, r.route_id, s.stop_id"""


AGENCY_STOP_QUERY = """SELECT agency_id, stop_id, canonical_agency_id, canonical_stop_id,
    count(*) AS serving_routes, sum(scheduled_visits)::BIGINT AS scheduled_visits
    FROM route_stops GROUP BY agency_id, stop_id, canonical_agency_id, canonical_stop_id"""


def trip_context_query(tables, namespace):
    directions = "t.direction_id" if "direction_id" in tables["trips"]["columns"] else "NULL::INTEGER"
    depart = "departure_time_seconds" if "departure_time" in tables["stop_times"]["columns"] else "NULL::BIGINT"
    frequency = "f.trip_id IS NOT NULL" if "frequencies" in tables else "FALSE"
    frequency_join = "LEFT JOIN (SELECT DISTINCT trip_id FROM frequencies) f ON f.trip_id = t.trip_id" if "frequencies" in tables else ""
    return f"""WITH starts AS (SELECT trip_id, first({depart} ORDER BY stop_sequence) AS first_departure_seconds
        FROM stop_times GROUP BY trip_id)
        SELECT t.trip_id, r.route_id, r.agency_id, t.service_id, r.route_type, {directions} AS direction_id,
        a.agency_timezone, s.first_departure_seconds, {frequency} AS frequency_defined,
        {canonical_expression(namespace, 'trip', 't.trip_id')} AS canonical_trip_id
        FROM trips t JOIN routes r USING (route_id) JOIN agency a ON r.agency_id = a.agency_id
        JOIN starts s ON s.trip_id = t.trip_id {frequency_join}"""


def aliases_query(namespace):
    return f"""SELECT stop_code AS alias_value, 'stop_code' AS alias_kind, stop_id AS source_stop_id,
        {canonical_expression(namespace, 'stop', 'stop_id')} AS canonical_stop_id,
        'UNVERIFIED' AS external_resolution_status, source_record
        FROM stops WHERE {clean('stop_code')} IS NOT NULL"""


def key_checks(db, tables):
    results = []
    for name, table in tables.items():
        specs = field_specs(table)
        table_ref = q(name)
        required = {c: s for c, s in specs.items() if s.get("presence") == "Required"}
        missing_columns = sorted(set(required) - table["columns"].keys())
        if missing_columns:
            raise ValueError(f"{table['path']} lacks required fields: {missing_columns}")
        conditions = [f"{clean(q(c) + '::VARCHAR')} IS NULL" for c, s in required.items() if not s.get("empty_default")]
        missing_required = value(db, f"SELECT count(*) FROM {table_ref} WHERE " + (" OR ".join(conditions) or "FALSE"))
        key = SCHEMA["tables"].get(table["path"].split("/")[-1], {}).get("key")
        key = key or {"calendar_attributes": ["service_id"], "directions": ["route_id", "direction_id"],
                      "route_attributes": ["route_id"], "mtc_feed_versions": ["agency_id"]}.get(name)
        origin = "GTFS Schedule" if table["path"].split("/")[-1] in SCHEMA["tables"] else "provider or Phase 2 candidate"
        if key == ["*"]:
            key = list(table["columns"])
        duplicates = None
        if key == ["none"]:
            duplicates = max(value(db, f"SELECT count(*) FROM {table_ref}") - 1, 0)
        elif key:
            parts = ", ".join(clean(q(c) + "::VARCHAR") if c in table["columns"] else "NULL::VARCHAR" for c in key)
            where = "WHERE attribution_id IS NOT NULL AND attribution_id <> ''" if name == "attributions" and "attribution_id" in table["columns"] else ""
            if name == "attributions" and "attribution_id" not in table["columns"]:
                where = "WHERE FALSE"
            duplicates = value(db, f"""SELECT count(*) FROM (SELECT {parts} FROM {table_ref} {where}
                GROUP BY {parts} HAVING count(*) > 1)""")
        results.append({"table": name, "key": key, "basis": origin, "duplicate_key_groups": duplicates,
                        "missing_required_records": missing_required})
    return results


def validate(
    db, tables, compatible, accounting, namespace, snapshot, baseline_refs, schema_checks, observations
):
    keys = key_checks(db, tables)
    refs = references(db, compatible, SCHEMA)
    checks = conditional_checks(db, compatible)
    baseline = {(i["file"], i["field"]): i for i in baseline_refs}
    regressions = []
    for ref in refs:
        if ref.get("status") == "failed":
            regressions.append(ref)
            continue
        previous = baseline.get((ref["file"], ref["field"]), {})
        for measure in ("invalid_references", "missing_required_references"):
            if ref[measure] > previous.get(measure, 0):
                regressions.append({"file": ref["file"], "field": ref["field"],
                                    "measure": measure, "count": ref[measure], "baseline": previous.get(measure, 0)})
    actual_reference_fields = {(i["file"], i["field"]) for i in refs}
    absent_refs = sorted(set(baseline) - actual_reference_fields)
    if absent_refs:
        regressions.append({"checks_missing_from_phase2": absent_refs})
    unmatched, extra = db.execute("""SELECT
        (SELECT count(*) FROM (SELECT * FROM identity_domain EXCEPT SELECT entity_type, source_identifier, source_tables FROM canonical_ids)),
        (SELECT count(*) FROM (SELECT entity_type, source_identifier, source_tables FROM canonical_ids EXCEPT SELECT * FROM identity_domain))""").fetchone()
    mapping = query(db, f"""SELECT count(*) AS records, count(*) - count(DISTINCT canonical_id) AS collisions,
        count(*) - count(DISTINCT (entity_type, source_identifier)) AS duplicate_source_keys,
        count(*) FILTER (WHERE canonical_id IS DISTINCT FROM
          ({literal(namespace + ':')} || entity_type || ':' || url_encode(source_identifier))) AS noncanonical_encodings,
        count(*) FILTER (WHERE url_decode(split_part(canonical_id, ':', 3)) IS DISTINCT FROM source_identifier) AS roundtrip_failures,
        count(*) FILTER (WHERE source_snapshot_id IS DISTINCT FROM {literal(snapshot)}) AS snapshot_mismatches
        FROM canonical_ids""")[0]
    mapping.update(missing_source_mappings=unmatched, extra_mappings=extra)
    mapping["coverage"] = query(db, """SELECT entity_type, count(*) AS mappings,
        count(*) FILTER (WHERE regexp_matches(source_identifier, '^0[0-9]+$')) AS leading_zero_ids,
        count(*) FILTER (WHERE contains(source_identifier, ':')) AS ids_with_colons
        FROM canonical_ids GROUP BY 1 ORDER BY 1""")
    mapping["stop_suffix_collision_groups_preserved"] = value(db, """
        SELECT count(*) FROM (SELECT regexp_replace(source_identifier, '^[A-Za-z0-9]+[:_]', '') AS suffix
        FROM canonical_ids WHERE entity_type='stop' GROUP BY suffix HAVING count(*) > 1)""")
    association_keys = {"route_stops": ("canonical_agency_id", "canonical_route_id", "canonical_stop_id"),
                        "agency_stops": ("canonical_agency_id", "canonical_stop_id"),
                        "trip_selector_context": ("canonical_trip_id",)}
    if "stop_alias_candidates" in actual_names(db):
        association_keys["stop_alias_candidates"] = ("canonical_stop_id",)
    mapping["derived_reference_errors"] = {}
    for name, fields in association_keys.items():
        mapping["derived_reference_errors"][name] = value(db, f"SELECT count(*) FROM {q(name)} a WHERE " +
            " OR ".join(f"NOT EXISTS (SELECT 1 FROM canonical_ids m WHERE m.canonical_id = a.{q(field)})" for field in fields))
    associations = query(db, """SELECT (SELECT count(*) FROM route_stops) AS route_stop_pairs,
        (SELECT count(*) FROM agency_stops) AS agency_stop_pairs,
        (SELECT count(*) FROM (SELECT stop_id FROM agency_stops GROUP BY stop_id HAVING count(*) > 1)) AS multi_agency_stops,
        (SELECT coalesce(sum(scheduled_visits),0) FROM route_stops) AS scheduled_stop_visits,
        (SELECT count(*) FROM trip_selector_context) AS trip_selector_context_rows,
        (SELECT count(*) FROM stop_times WHERE NULLIF(stop_id,'') IS NOT NULL) AS timetable_stop_visits""")[0]
    associations["duplicate_route_stop_keys"] = value(db, """SELECT count(*) - count(DISTINCT (route_id, stop_id)) FROM route_stops""")
    associations["duplicate_agency_stop_keys"] = value(db, """SELECT count(*) - count(DISTINCT (agency_id, stop_id)) FROM agency_stops""")
    associations["duplicate_trip_context_keys"] = value(db, """SELECT count(*) - count(DISTINCT trip_id) FROM trip_selector_context""")
    temporal = temporal_checks(db, tables)
    orphaned = sum(mapping["derived_reference_errors"].values())
    failure = any(item["duplicate_key_groups"] or item["missing_required_records"] for item in keys) or regressions
    failure = failure or any(item["affected_records"] for item in checks)
    failure = failure or any(mapping[k] for k in ("collisions", "duplicate_source_keys", "noncanonical_encodings",
                                                  "roundtrip_failures", "snapshot_mismatches", "missing_source_mappings",
                                                  "extra_mappings"))
    failure = failure or orphaned or any(associations[k] for k in ("duplicate_route_stop_keys",
                                                                    "duplicate_agency_stop_keys", "duplicate_trip_context_keys"))
    failure = failure or associations["trip_selector_context_rows"] != accounting["trips"]["input_records"]
    failure = failure or associations["scheduled_stop_visits"] != associations["timetable_stop_visits"]
    failure = failure or any(temporal["validation_errors"].values()) or any(not item["matches"] for item in schema_checks)
    failure = failure or any(not item["matches"] for item in observations)
    return {"passed": not failure, "schema_checks": schema_checks, "keys": keys, "foreign_keys": refs,
            "reference_regressions": regressions, "conditional_checks": checks, "canonical_mappings": mapping,
            "associations": associations, "temporal": temporal, "source_projection_checks": observations}


def actual_names(db):
    return {row[0] for row in db.execute("SELECT table_name FROM information_schema.tables").fetchall()}


def temporal_checks(db, tables):
    times = query(db, f"""SELECT count(*) AS records,
            count(*) FILTER (WHERE arrival_time_seconds > 86400) AS arrivals_above_24h,
            count(*) FILTER (WHERE departure_time_seconds > 86400) AS departures_above_24h,
            count(*) FILTER (WHERE {clean('arrival_time')} IS NULL) AS missing_arrivals,
            count(*) FILTER (WHERE {clean('departure_time')} IS NULL) AS missing_departures,
            max(arrival_time_seconds) AS max_arrival_seconds, max(departure_time_seconds) AS max_departure_seconds,
            count(*) FILTER (WHERE arrival_time_seconds IS DISTINCT FROM {gtfs_seconds('arrival_time')}
                            OR departure_time_seconds IS DISTINCT FROM {gtfs_seconds('departure_time')}) AS conversion_mismatches
        FROM stop_times""")[0]
    # Validation uses service-day order and the same conditional presence rules as Phase 2.
    def optional(column, data_type):
        return q(column) if column in tables["stop_times"]["columns"] else f"NULL::{data_type}"

    start, end = (optional(f"{k}_pickup_drop_off_window", "VARCHAR") for k in ("start", "end"))
    group, location = (optional(k, "VARCHAR") for k in ("location_group_id", "location_id"))
    timepoint = optional("timepoint", "INTEGER")
    order_checks = query(db, f"""WITH sequence AS (
        SELECT *, lag(departure_time_seconds IGNORE NULLS) OVER w AS previous_departure,
            min(stop_sequence) OVER (PARTITION BY trip_id) AS first_sequence,
            max(stop_sequence) OVER (PARTITION BY trip_id) AS last_sequence,
            {clean(start)} IS NOT NULL OR {clean(end)} IS NOT NULL AS windowed,
            ({clean('stop_id')} IS NOT NULL)::INTEGER + ({clean(group)} IS NOT NULL)::INTEGER +
            ({clean(location)} IS NOT NULL)::INTEGER AS location_count
        FROM stop_times WINDOW w AS (PARTITION BY trip_id ORDER BY stop_sequence))
        SELECT count(*) FILTER (WHERE NOT windowed AND arrival_time_seconds IS NULL AND
            (stop_sequence IN (first_sequence, last_sequence) OR {timepoint}=1)) AS missing_required_arrivals,
            count(*) FILTER (WHERE NOT windowed AND departure_time_seconds IS NULL AND {timepoint}=1) AS missing_required_departures,
            count(*) FILTER (WHERE departure_time_seconds < arrival_time_seconds
                OR arrival_time_seconds < previous_departure) AS time_order_violations,
            count(*) FILTER (WHERE location_count <> 1) AS invalid_location_selectors,
            count(*) FILTER (WHERE windowed AND
                (arrival_time_seconds IS NOT NULL OR departure_time_seconds IS NOT NULL)) AS mixed_times_and_windows,
            count(*) FILTER (WHERE (windowed OR {clean(group)} IS NOT NULL OR {clean(location)} IS NOT NULL)
                AND ({clean(start)} IS NULL OR {clean(end)} IS NULL)) AS missing_window_endpoints
        FROM sequence""")[0]
    result = {"stop_times": times, "validation_errors": {**order_checks, "time_conversion_mismatches": times["conversion_mismatches"]},
              "service_ids": value(db, "SELECT count(*) FROM service_ids"),
              "calendar_exception_rows": value(db, "SELECT count(*) FROM calendar_dates") if "calendar_dates" in tables else 0,
              "service_date_expansion": False,
              "agency_timezones": query(db, """SELECT agency_timezone, count(*) AS agencies FROM agency GROUP BY 1 ORDER BY 1""")}
    if "calendar" in tables:
        result["calendar"] = query(db, """SELECT min(start_date) AS earliest_base_start, max(end_date) AS latest_base_end,
            count(*) AS rows, count(*) FILTER (WHERE start_date > end_date) AS reversed_ranges FROM calendar""")[0]
        result["validation_errors"]["reversed_calendar_ranges"] = result["calendar"]["reversed_ranges"]
        result["calendar_end_2099"] = query(db, """SELECT c.service_id, c.start_date, c.end_date,
            count(t.trip_id) AS trip_templates FROM calendar c LEFT JOIN trips t USING (service_id)
            WHERE c.end_date = DATE '2099-12-31' GROUP BY ALL ORDER BY 1""")
    return result


def scoped_stop(db, agency_id, stop_id=None, stop_name=None):
    """Resolve a regional selector against exact scheduled service in the exported tables."""
    if (stop_id is None) == (stop_name is None):
        raise ValueError("Provide exactly one source stop ID or display name.")
    column, selector = ("stop_id", stop_id) if stop_id is not None else ("stop_name", stop_name)
    scoped = query(db, f"""SELECT a.agency_id, a.stop_id, s.stop_name, a.canonical_agency_id, a.canonical_stop_id
        FROM agency_stops a JOIN stops s USING (stop_id) WHERE a.agency_id=? AND s.{column}=? ORDER BY a.stop_id""",
        [agency_id, selector])
    status = ("NO_MATCH" if not scoped else "AMBIGUOUS_MATCH" if len(scoped) > 1 else
              "EXACT_MATCH" if stop_id is not None else "CANDIDATE_MATCH")
    for row in scoped:
        params = [agency_id, row["stop_id"]]
        row["routes"] = query(db, """SELECT route_id, canonical_route_id, scheduled_trips, scheduled_visits
            FROM route_stops WHERE agency_id=? AND stop_id=? ORDER BY route_id""", params)
        row["distinct_trips"] = db.execute("""SELECT count(DISTINCT t.trip_id) FROM stop_times st
            JOIN trip_selector_context t USING (trip_id) WHERE t.agency_id=? AND st.stop_id=?""", params).fetchone()[0]
    return {"selector": {"AgencyId": agency_id, "StopId" if stop_id is not None else "StopName": selector},
            "status": status, "candidates": scoped}


def realtime_report(db, baseline=None):
    result = {
        "identity_domains": {"internal_source_to_canonical": "canonical_ids.parquet",
                             "external_verified_crosswalks": [],
                             "unverified_stop_code_aliases": "stop_alias_candidates.parquet"
                             if "stop_alias_candidates" in actual_names(db) else None},
        "external_scope_limitation": "The verified input is the regional snapshot. No matching dated operator feed or "
                                     "authoritative operator crosswalk was supplied.",
        "statuses": {"EXACT_MATCH": "Unique exact source ID in the supplied regional snapshot and applicable selector scope.",
                     "VERIFIED_MAPPING": "An authoritative dated crosswalk or operator feed establishes equivalence to a regional entity.",
                     "CANDIDATE_MATCH": "One plausible candidate from supporting labels or aliases.",
                     "AMBIGUOUS_MATCH": "Multiple plausible scoped candidates.",
                     "NO_MATCH": "No entity matches the requested regional ID and selector scope.",
                     "UNVERIFIED": "Potential external alias has insufficient authoritative evidence."},
        "selector_lookups": {
            "AgencyId": "agency and canonical_ids",
            "RouteId and RouteType": "routes and trip_selector_context",
            "DirectionId": "trip_selector_context with required route scope",
            "StopId with AgencyId or RouteId": "agency_stops and route_stops for candidate scope",
            "TripDescriptor": "trip_selector_context with timetable, service calendar and exceptions. "
                              "Apply frequencies when present and identify the service date and start time as needed.",
            "Combinations": "Evaluate all fields conjunctively on the same trip and applicable stop visit. "
                            "Independent connectivity existence checks can overmatch."
        }
    }
    if not baseline:
        return result
    exact = scoped_stop(db, "AC", stop_id="55660")
    other = scoped_stop(db, "AC", stop_id="52227")
    expected = next(c for c in baseline["candidates"] if c["stop_id"] == "55660")
    expected_routes = sorted(r["route_id"] for r in expected["routes"] if r["agency_id"] == "AC")
    expected_trips = next(row["distinct_trips"] for row in expected["visit_counts_by_agency"] if row["agency_id"] == "AC")
    match = exact["candidates"][0] if exact["candidates"] else {}
    name = scoped_stop(db, "AC", stop_name=expected["stop_name"])
    checks = {
        "agency_present": value(db, "SELECT count(*) FROM agency WHERE agency_id='AC'") == 1,
        "one_exact_stop_match": exact["status"] == "EXACT_MATCH" and len(exact["candidates"]) == 1,
        "source_id_preserved": match.get("stop_id") == "55660",
        "distinct_neighbor": other["status"] == "EXACT_MATCH" and match.get("canonical_stop_id") !=
        other["candidates"][0]["canonical_stop_id"],
        "matched_display_name": match.get("stop_name") == expected["stop_name"],
        "expected_routes_preserved": [r["route_id"] for r in match.get("routes", [])] == expected_routes,
        "expected_trip_count_preserved": match.get("distinct_trips") == expected_trips,
        "name_only_is_ambiguous": name["status"] == "AMBIGUOUS_MATCH" and
        {"55660", "52227"} <= {candidate["stop_id"] for candidate in name["candidates"]},
    }
    result["ac_transit_55660"] = {"exact": exact, "same_name_neighbor": other, "name_only": name,
                                  "checks": checks, "passed": all(checks.values())}
    return result
