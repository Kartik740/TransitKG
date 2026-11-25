"""Service-day, network, identifier, and sample alert analysis."""

from datetime import datetime, timedelta, timezone
import re

from duckdb import Error

from gtfs_checks import clean, field, gtfs_date, gtfs_seconds, q, query, value

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
ALERT = {"AgencyId": "AC", "StopId": "55660", "timestamp": 1795543209,
         "name": "San Pablo Avenue at West Grand Avenue"}


def has(tables, name, *columns):
    return name in tables and set(columns) <= tables[name]["columns"].keys()


def rel(tables, name):
    return q(tables[name]["sql_table"])


def col(tables, name, column, prefix=""):
    return field(tables[name]["columns"], column, prefix)


def distribution(db, relation, expression, label):
    return query(db, f"SELECT {expression} AS {label}, count(*) AS records FROM {relation} GROUP BY 1 ORDER BY 1")


def summary(db, sql, field_name):
    return query(db, f"""SELECT count(*) AS groups, min({field_name}) AS minimum,
        round(avg({field_name}), 3) AS mean, median({field_name}) AS median,
        quantile_disc({field_name}, 0.95) AS p95, max({field_name}) AS maximum FROM ({sql})""")[0]


def conditional_checks(db, tables):
    findings = []

    def check(table, label, predicate, severity="MEDIUM", basis="GTFS Schedule"):
        relation = rel(tables, table)
        count = value(db, f"SELECT count(*) FROM {relation} s WHERE {predicate}")
        examples = query(db, f"SELECT * FROM {relation} s WHERE {predicate} ORDER BY ALL LIMIT 5") if count else []
        findings.append({"file": tables[table]["path"], "check": label, "basis": basis,
                         "affected_records": count, "severity_if_present": severity, "examples": examples})

    def f(table, name):
        return clean(col(tables, table, name, "s."))

    if "agency" in tables and tables["agency"]["records"] > 1:
        check("agency", "Multi-agency feed requires an agency ID for each agency",
              f"{f('agency', 'agency_id')} IS NULL", "HIGH")
    if "routes" in tables:
        check("routes", "Route requires a short or long name",
              f"{f('routes', 'route_short_name')} IS NULL AND {f('routes', 'route_long_name')} IS NULL")
        if {"networks", "route_networks"} & tables.keys():
            check("routes", "A route network ID is forbidden with a separate network definition file",
                  f"{f('routes', 'network_id')} IS NOT NULL")
    if "stops" in tables:
        check("stops", "Stops, stations and entrances require names",
              f"coalesce({f('stops', 'location_type')}, '0') IN ('0','1','2') AND {f('stops', 'stop_name')} IS NULL")
    if "pathways" in tables and "levels" not in tables:
        check("pathways", "Elevators require levels.txt", f"{f('pathways', 'pathway_mode')}='5'")
    if "translations" in tables and "feed_info" not in tables:
        check("translations", "Translation tables require feed_info.txt", "TRUE")
    if "timeframes" in tables:
        start, end = (f("timeframes", field) for field in ("start_time", "end_time"))
        check("timeframes", "Timeframe endpoints must be provided together",
              f"({start} IS NULL) <> ({end} IS NULL)")
        check("timeframes", "Populated timeframe start must precede its end",
              f"{gtfs_seconds(start)} >= {gtfs_seconds(end)}")
    if "attributions" in tables:
        for label, fields in (("Attribution scope must select at most one of agency, route and trip",
                               ("agency_id", "route_id", "trip_id")),
                              ("Attribution requires a producer, operator or authority role",
                               ("is_producer", "is_operator", "is_authority"))):
            if fields[0] == "agency_id":
                predicate = " + ".join(f"({f('attributions', name)} IS NOT NULL)::INTEGER" for name in fields) + " > 1"
            else:
                predicate = " AND ".join(f"{f('attributions', name)} IS DISTINCT FROM '1'" for name in fields)
            check("attributions", label, predicate)
    if "transfers" in tables:
        transfer_type = f("transfers", "transfer_type")
        check("transfers", "Type 2 transfers require minimum transfer time",
              f"{transfer_type}='2' AND {f('transfers', 'min_transfer_time')} IS NULL")
        if has(tables, "trips", "trip_id", "route_id"):
            for side in ("from", "to"):
                trip, route = (f("transfers", f"{side}_{name}_id") for name in ("trip", "route"))
                check("transfers", f"{side} trip and route must describe the same service",
                      f"{trip} IS NOT NULL AND {route} IS NOT NULL AND NOT EXISTS ("
                      f"SELECT 1 FROM {rel(tables, 'trips')} t WHERE t.trip_id={trip} AND t.route_id={route})", "HIGH")
    if "fare_transfer_rules" in tables:
        table = "fare_transfer_rules"
        start, end = (f(table, f"{side}_leg_group_id") for side in ("from", "to"))
        transfer_count, duration, duration_type = (f(table, name) for name in
                                                    ("transfer_count", "duration_limit", "duration_limit_type"))
        check(table, "Mutually specified leg groups constrain transfer_count",
              f"{start} IS NOT NULL AND {end} IS NOT NULL AND (({start}={end}) <> ({transfer_count} IS NOT NULL))")
        check(table, "Duration limit and duration type must be provided together",
              f"({duration} IS NULL) <> ({duration_type} IS NULL)")
    if has(tables, "directions", "route_id", "direction_id") and has(tables, "trips", "route_id", "direction_id"):
        check("directions", "Route and direction pair has no matching scheduled trip",
              f"NOT EXISTS (SELECT 1 FROM {rel(tables, 'trips')} t "
              "WHERE t.route_id=s.route_id AND t.direction_id=s.direction_id)", basis="511 Data Exchange")
    return findings


def service_views(db, tables):
    calendar = has(tables, "calendar", "service_id", "start_date", "end_date", *WEEKDAYS)
    exceptions = has(tables, "calendar_dates", "service_id", "date", "exception_type")
    empty = "SELECT NULL::VARCHAR AS service_id, NULL::DATE AS service_date WHERE false"
    base = empty
    if calendar:
        flags = ", ".join(WEEKDAYS)
        base = f"""SELECT DISTINCT service_id, day::DATE AS service_date
            FROM (SELECT service_id, {gtfs_date('start_date')} AS first_date,
                  {gtfs_date('end_date')} AS last_date, {flags} FROM {rel(tables, 'calendar')}) c,
                LATERAL generate_series(first_date, last_date, INTERVAL 1 DAY) days(day)
            WHERE {clean('service_id')} IS NOT NULL AND list_extract([{flags}], isodow(day)) = '1'"""
    if exceptions:
        db.execute(f"""CREATE OR REPLACE TEMP VIEW exception_days AS SELECT service_id,
            {gtfs_date('date')} AS service_date, exception_type FROM {rel(tables, 'calendar_dates')}
            WHERE {clean('service_id')} IS NOT NULL AND {gtfs_date('date')} IS NOT NULL""")
    else:
        db.execute(f"CREATE OR REPLACE TEMP VIEW exception_days AS SELECT *, NULL::VARCHAR AS exception_type FROM ({empty})")
    # A conflicting addition and removal is disclosed. The effective-date calculation gives removals precedence.
    db.execute(f"""CREATE OR REPLACE TEMP TABLE effective_service_dates AS
        WITH additions AS ({base} UNION SELECT service_id, service_date FROM exception_days WHERE exception_type = '1')
        SELECT * FROM additions EXCEPT SELECT service_id, service_date FROM exception_days WHERE exception_type = '2'""")
    return calendar, exceptions


def temporal_analysis(db, tables):
    calendar, exceptions = service_views(db, tables)
    result = {"effective_service_dates": query(db, """SELECT min(service_date) AS earliest,
        max(service_date) AS latest, count(*) AS service_date_pairs,
        count(DISTINCT service_id) AS services_with_active_dates FROM effective_service_dates""")[0],
        "service_id_domain": value(db, "SELECT count(*) FROM service_ids"),
        "notes": ["Weekly service expanded by weekday and date range, with calendar date additions and removals applied.",
                  "If the same service-date has conflicting exceptions, the effective-date summary gives removal precedence.",
                  "Service-day times are represented in seconds and include times after 24:00:00."],
        "unavailable": []}
    if has(tables, "trips", "service_id"):
        trip_services = f"SELECT DISTINCT service_id FROM {rel(tables, 'trips')} WHERE {clean('service_id')} IS NOT NULL"
        result["trip_backed_effective_service_dates"] = query(db, f"""SELECT min(service_date) AS earliest,
            max(service_date) AS latest, count(*) AS service_date_pairs, count(DISTINCT service_id) AS services_with_active_dates
            FROM effective_service_dates SEMI JOIN ({trip_services}) USING (service_id)""")[0]
        result["services_without_trips"] = query(db, f"""SELECT count(*) AS count, min(service_id, 5) AS examples
            FROM service_ids ANTI JOIN ({trip_services}) USING (service_id)""")[0]
        result["trip_service_ids_without_effective_dates"] = query(db, f"""SELECT count(*) AS count, min(service_id, 5) AS examples
            FROM ({trip_services}) ANTI JOIN effective_service_dates USING (service_id)""")[0]
        result["latest_service_date_examples"] = query(db, f"""SELECT service_id, service_date,
            service_id IN ({trip_services}) AS used_by_trips FROM effective_service_dates
            WHERE service_date=(SELECT max(service_date) FROM effective_service_dates) ORDER BY 1 LIMIT 5""")
    if calendar:
        c = rel(tables, "calendar")
        result["calendar"] = query(db, f"""SELECT min({gtfs_date('start_date')}) AS earliest_start,
            max({gtfs_date('start_date')}) AS latest_start, min({gtfs_date('end_date')}) AS earliest_end,
            max({gtfs_date('end_date')}) AS latest_end,
            count(*) FILTER (WHERE {gtfs_date('start_date')} > {gtfs_date('end_date')}) AS reversed_ranges
            FROM {c}""")[0]
        result["weekly_patterns"] = query(db, f"SELECT {', '.join(WEEKDAYS)}, count(*) AS services "
                                          f"FROM {c} GROUP BY ALL ORDER BY services DESC, {', '.join(WEEKDAYS)}")
        result["active_services_by_weekday_flag"] = {
            day: value(db, f"SELECT count(*) FROM {c} WHERE {day}='1'") for day in WEEKDAYS}
        result["exception_only_services"] = query(db, f"""SELECT count(*) AS count,
            min(service_id, 5) AS examples FROM service_ids WHERE service_id NOT IN
            (SELECT service_id FROM {c} WHERE service_id IS NOT NULL)""")[0]
    else:
        result["unavailable"].append("Base weekly calendar table or fields absent.")
    if exceptions:
        result["exceptions"] = query(db, """SELECT exception_type, count(*) AS records, min(service_date) AS first,
            max(service_date) AS last FROM exception_days GROUP BY 1 ORDER BY 1""")
    else:
        result["unavailable"].append("Calendar exceptions table or fields absent.")
    for name in ("agency", "stops"):
        column = "agency_timezone" if name == "agency" else "stop_timezone"
        if has(tables, name, column):
            result[f"{name}_timezones"] = distribution(db, rel(tables, name), column, "timezone")
    for name in ("feed_info", "mtc_feed_versions"):
        if name in tables:
            result[name] = query(db, f"SELECT * FROM {rel(tables, name)} ORDER BY ALL LIMIT 100")
    if not has(tables, "stop_times", "trip_id", "stop_sequence"):
        result["unavailable"].append("Stop time analysis requires trip_id and stop_sequence.")
        return result
    stop_times = rel(tables, "stop_times")
    getters = {f: clean(col(tables, "stop_times", f)) for f in (
        "arrival_time", "departure_time", "timepoint", "start_pickup_drop_off_window",
        "end_pickup_drop_off_window", "stop_id", "location_group_id", "location_id", "shape_dist_traveled")}
    db.execute(f"""CREATE OR REPLACE TEMP VIEW stop_time_values AS SELECT rowid AS source_order, trip_id,
        try_cast(stop_sequence AS BIGINT) AS seq, {gtfs_seconds(getters['arrival_time'])} AS arr,
        {gtfs_seconds(getters['departure_time'])} AS dep, {getters['arrival_time']} AS raw_arrival,
        {getters['departure_time']} AS raw_departure, {getters['timepoint']} AS timepoint,
        {getters['start_pickup_drop_off_window']} AS window_start, {getters['end_pickup_drop_off_window']} AS window_end,
        {getters['stop_id']} AS stop_id, {getters['location_group_id']} AS location_group_id,
        {getters['location_id']} AS location_id,
        try_cast({getters['shape_dist_traveled']} AS DOUBLE) AS distance FROM {stop_times}""")
    result["timepoints"] = distribution(db, "stop_time_values", "timepoint", "timepoint")
    result["stop_times"] = query(db, """WITH ordered AS (
        SELECT *, min(seq) OVER w AS first_seq, max(seq) OVER w AS last_seq,
        lag(dep IGNORE NULLS) OVER sequence AS prev_dep, lag(distance IGNORE NULLS) OVER sequence AS prev_distance,
        lag(seq) OVER physical_order AS prev_seq_in_file
        FROM stop_time_values
        WINDOW w AS (PARTITION BY trip_id), sequence AS (PARTITION BY trip_id ORDER BY seq, source_order),
               physical_order AS (PARTITION BY trip_id ORDER BY source_order)
    ), flagged AS (
        SELECT *, (window_start IS NOT NULL OR window_end IS NOT NULL) AS windowed,
        (stop_id IS NOT NULL)::INTEGER + (location_group_id IS NOT NULL)::INTEGER +
        (location_id IS NOT NULL)::INTEGER AS location_fields FROM ordered
    )
    SELECT count(*) AS records, count(DISTINCT trip_id) AS trips,
        count(*) FILTER (WHERE arr > 86400) AS arrivals_above_24h,
        count(*) FILTER (WHERE dep > 86400) AS departures_above_24h,
        min(raw_arrival, 5) FILTER (WHERE arr > 86400) AS after_midnight_examples,
        count(*) FILTER (WHERE arr = 86400) AS arrivals_at_24h,
        count(*) FILTER (WHERE dep = 86400) AS departures_at_24h,
        min(arr) AS min_arrival_seconds, max(arr) AS max_arrival_seconds,
        min(dep) AS min_departure_seconds, max(dep) AS max_departure_seconds,
        count(*) FILTER (WHERE raw_arrival IS NOT NULL AND arr IS NULL) AS invalid_arrivals,
        count(*) FILTER (WHERE raw_departure IS NOT NULL AND dep IS NULL) AS invalid_departures,
        count(*) FILTER (WHERE raw_arrival IS NULL) AS missing_arrivals,
        count(*) FILTER (WHERE raw_departure IS NULL) AS missing_departures,
        count(*) FILTER (WHERE NOT windowed AND raw_arrival IS NULL AND
            (seq IN (first_seq, last_seq) OR timepoint = '1')) AS missing_required_arrivals,
        count(*) FILTER (WHERE NOT windowed AND raw_departure IS NULL AND timepoint = '1') AS missing_required_departures,
        count(*) FILTER (WHERE windowed) AS windowed_records,
        count(*) FILTER (WHERE windowed AND (raw_arrival IS NOT NULL OR raw_departure IS NOT NULL)) AS mixed_times_and_windows,
        count(*) FILTER (WHERE (windowed OR location_group_id IS NOT NULL OR location_id IS NOT NULL)
             AND (window_start IS NULL OR window_end IS NULL)) AS missing_required_window_endpoint,
        count(*) FILTER (WHERE raw_arrival IS NOT NULL
            AND (length(split_part(raw_arrival, ':', 1)) = 1)) AS arrivals_one_digit_hour,
        count(*) FILTER (WHERE raw_departure IS NOT NULL
            AND (length(split_part(raw_departure, ':', 1)) = 1)) AS departures_one_digit_hour,
        count(*) FILTER (WHERE location_fields <> 1) AS invalid_location_selector_count,
        count(*) FILTER (WHERE dep < arr) AS departures_before_arrival,
        count(*) FILTER (WHERE arr < prev_dep) AS arrivals_before_previous_departure,
        count(*) FILTER (WHERE seq < prev_seq_in_file) AS rows_out_of_numeric_order_in_source,
        count(*) FILTER (WHERE distance < prev_distance) AS decreasing_stop_distances,
        min(trip_id || '/' || seq::VARCHAR, 5) FILTER (WHERE dep < arr OR arr < prev_dep) AS time_order_examples,
        min(trip_id || '/' || seq::VARCHAR, 5) FILTER (WHERE location_fields <> 1) AS location_selector_examples,
        min(trip_id || '/' || seq::VARCHAR, 5) FILTER (WHERE NOT windowed AND
            ((raw_arrival IS NULL AND (seq IN (first_seq, last_seq) OR timepoint = '1'))
                OR (raw_departure IS NULL AND timepoint = '1'))) AS missing_required_time_examples,
        min(trip_id || '/' || seq::VARCHAR, 5) FILTER (WHERE distance < prev_distance) AS distance_examples
    FROM flagged""")[0]
    return result


def network_analysis(db, tables):
    result = {"unavailable": []}
    if has(tables, "stops", "stop_id"):
        s = rel(tables, "stops")
        latitude, longitude, name, location_type, parent = [
            col(tables, "stops", f) for f in ("stop_lat", "stop_lon", "stop_name", "location_type", "parent_station")]
        db.execute(f"""CREATE OR REPLACE TEMP VIEW stop_locations AS
            SELECT stop_id, {clean(name)} AS stop_name, {clean(parent)} AS parent_station,
            coalesce({clean(location_type)}, '0') AS location_type, {clean(latitude)} AS raw_lat,
            {clean(longitude)} AS raw_lon, try_cast({latitude} AS DOUBLE) AS lat,
            try_cast({longitude} AS DOUBLE) AS lon FROM {s}""")
        result["stop_location_types"] = distribution(db, "stop_locations", "location_type", "location_type")
        result["coordinates"] = query(db, """SELECT count(*) FILTER (WHERE raw_lat IS NULL OR raw_lon IS NULL) AS
            stops_missing_coordinate, count(*) FILTER (WHERE location_type IN ('0','1','2') AND
            (raw_lat IS NULL OR raw_lon IS NULL)) AS missing_required_coordinates,
            count(*) FILTER (WHERE (raw_lat IS NOT NULL AND (lat IS NULL OR NOT lat BETWEEN -90 AND 90))
                OR (raw_lon IS NOT NULL AND (lon IS NULL OR NOT lon BETWEEN -180 AND 180))) AS invalid_coordinate_records,
            min(lat) FILTER (WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180) AS min_lat,
            max(lat) FILTER (WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180) AS max_lat,
            min(lon) FILTER (WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180) AS min_lon,
            max(lon) FILTER (WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180) AS max_lon,
            min(stop_id, 5) FILTER (WHERE location_type IN ('0','1','2') AND (raw_lat IS NULL OR raw_lon IS NULL))
                AS missing_required_examples FROM stop_locations""")[0]
        result["parent_station"] = query(db, """WITH checked AS (SELECT child.*, EXISTS (SELECT 1 FROM stop_locations p
            WHERE p.stop_id = child.parent_station) AS parent_exists, EXISTS (SELECT 1 FROM stop_locations p
            WHERE p.stop_id = child.parent_station AND p.location_type = CASE WHEN child.location_type='4' THEN '0' ELSE '1' END)
            AS valid_type FROM stop_locations child)
            SELECT count(*) FILTER (WHERE parent_station IS NOT NULL) AS populated,
                count(*) FILTER (WHERE parent_station IS NULL AND location_type IN ('2','3','4')) AS missing_required,
                count(*) FILTER (WHERE parent_station IS NOT NULL AND NOT parent_exists) AS orphaned,
                count(*) FILTER (WHERE parent_exists AND NOT valid_type) AS wrong_parent_type,
                count(*) FILTER (WHERE location_type='1' AND parent_station IS NOT NULL) AS station_with_forbidden_parent,
                count(*) FILTER (WHERE parent_station = stop_id) AS self_parent,
                min(stop_id, 5) FILTER (WHERE (parent_exists AND NOT valid_type) OR parent_station = stop_id
                    OR (location_type='1' AND parent_station IS NOT NULL)) AS examples
            FROM checked""")[0]
        for label, grouping, where in (("repeated_names", "stop_name", "stop_name IS NOT NULL"),
                                       ("shared_coordinates", "lat, lon", "lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180")):
            sql = f"SELECT {grouping}, count(*) AS records, min(stop_id, 5) AS stop_examples FROM stop_locations " \
                  f"WHERE {where} GROUP BY {grouping} HAVING count(*) > 1"
            result[label] = {**query(db, f"SELECT count(*) AS groups, coalesce(sum(records),0) AS records FROM ({sql})")[0],
                             "examples": query(db, f"SELECT * FROM ({sql}) ORDER BY records DESC, {grouping} LIMIT 5")}
    if has(tables, "routes", "route_id", "route_type"):
        result["route_types"] = distribution(db, rel(tables, "routes"), "route_type", "route_type")
    if has(tables, "routes", "route_id"):
        r = rel(tables, "routes")
        agency = clean(col(tables, "routes", "agency_id", "r."))
        if has(tables, "agency", "agency_id") and tables["agency"]["records"] == 1:
            agency = f"coalesce({agency}, (SELECT agency_id FROM {rel(tables, 'agency')}))"
        db.execute(f"CREATE OR REPLACE TEMP VIEW route_agencies AS SELECT DISTINCT route_id, {agency} AS agency_id, "
                   f"{col(tables, 'routes', 'route_type', 'r.')} AS route_type FROM {r} r")
        result["routes_per_agency"] = query(db, """SELECT agency_id, count(DISTINCT route_id) AS routes
            FROM route_agencies GROUP BY 1 ORDER BY agency_id""")
    if has(tables, "trips", "trip_id", "route_id") and has(tables, "routes", "route_id"):
        t = rel(tables, "trips")
        db.execute(f"""CREATE OR REPLACE TEMP VIEW trip_agencies AS SELECT DISTINCT t.trip_id, a.route_id,
            a.agency_id, a.route_type, {col(tables, 'trips', 'direction_id', 't.')} AS direction_id,
            {col(tables, 'trips', 'service_id', 't.')} AS service_id, {col(tables, 'trips', 'shape_id', 't.')} AS shape_id
            FROM {t} t JOIN route_agencies a USING (route_id)""")
        result["trips_per_route"] = query(db, """SELECT a.route_id, a.agency_id, count(DISTINCT t.trip_id) AS trips
            FROM route_agencies a LEFT JOIN trip_agencies t USING (route_id, agency_id) GROUP BY 1, 2 ORDER BY 1, 2""")
        result["route_direction_pairs"] = value(db, """SELECT count(*) FROM (SELECT DISTINCT route_id, direction_id
            FROM trip_agencies WHERE direction_id IN ('0','1'))""")
        result["trips_missing_direction"] = value(db, "SELECT count(DISTINCT trip_id) FROM trip_agencies WHERE NULLIF(direction_id,'') IS NULL")
    if has(tables, "stop_times", "trip_id", "stop_id"):
        st = rel(tables, "stop_times")
        counts = f"SELECT trip_id, count(*) AS visits, count(DISTINCT NULLIF(stop_id,'')) AS stops FROM {st} GROUP BY 1"
        result["stop_records_per_trip"] = summary(db, counts, "visits")
        result["distinct_stops_per_trip"] = summary(db, counts, "stops")
        if has(tables, "trips", "trip_id"):
            result["trip_visit_coverage"] = query(db, f"""SELECT count(*) FILTER(WHERE c.visits IS NULL) AS without_visits,
                count(*) FILTER (WHERE c.visits = 1) AS with_one_visit,
                min(t.trip_id, 5) FILTER (WHERE coalesce(c.visits,0) < 2) AS examples
                FROM {rel(tables, 'trips')} t LEFT JOIN ({counts}) c USING (trip_id)""")[0]
    if all(has(tables, name, *cols) for name, cols in {
        "stop_times": ("trip_id", "stop_id", "stop_sequence"), "routes": ("route_id",),
        "trips": ("trip_id", "route_id"), "stops": ("stop_id",)}.items()):
        st = rel(tables, "stop_times")
        db.execute(f"""CREATE OR REPLACE TEMP TABLE trip_stop_counts AS SELECT DISTINCT
            st.trip_id, st.stop_id, st.stop_sequence FROM {st} st
            SEMI JOIN stop_locations s ON s.stop_id=st.stop_id AND s.location_type='0'
            SEMI JOIN trip_agencies t ON t.trip_id=st.trip_id
            WHERE NULLIF(st.stop_id,'') IS NOT NULL AND NULLIF(st.stop_sequence,'') IS NOT NULL""")
        db.execute("""CREATE OR REPLACE TEMP TABLE route_stops AS SELECT DISTINCT t.agency_id,t.route_id,st.stop_id
            FROM trip_stop_counts st JOIN trip_agencies t USING (trip_id)""")
        db.execute("""CREATE OR REPLACE TEMP TABLE agency_stops AS SELECT DISTINCT agency_id, stop_id
            FROM route_stops WHERE agency_id IS NOT NULL""")
        result["verified_platform_visit_keys"] = value(db, "SELECT count(*) FROM trip_stop_counts")
        result["route_stop_pairs"] = value(db, "SELECT count(*) FROM (SELECT DISTINCT route_id, stop_id FROM route_stops)")
        result["agency_stop_pairs"] = value(db, "SELECT count(*) FROM agency_stops")
        result["stops_per_agency"] = distribution(db, "agency_stops", "agency_id", "agency_id")
        shared = "SELECT stop_id, count(*) AS agencies, list(agency_id ORDER BY agency_id) AS agency_ids FROM agency_stops GROUP BY 1 HAVING count(*) > 1"
        result["multi_agency_stops"] = {"count": value(db, f"SELECT count(*) FROM ({shared})"),
            "examples": query(db, f"SELECT * FROM ({shared}) ORDER BY agencies DESC, stop_id LIMIT 10")}
        adjacency = f"""WITH sequenced AS (
            SELECT st.trip_id, st.stop_id, try_cast(st.stop_sequence AS BIGINT) AS seq,
              lead(st.stop_id) OVER w AS next_stop
            FROM {st} st WINDOW w AS (PARTITION BY trip_id ORDER BY try_cast(stop_sequence AS BIGINT), rowid)
        ) SELECT stop_id, next_stop FROM sequenced x
            SEMI JOIN stop_locations a ON a.stop_id=x.stop_id AND a.location_type='0'
            SEMI JOIN stop_locations b ON b.stop_id=x.next_stop AND b.location_type='0'"""
        result["next_stop_pairs"] = query(db, f"""SELECT count(*) AS occurrences, count(DISTINCT (stop_id,next_stop))
            AS distinct_directed_pairs FROM ({adjacency})""")[0]
    else:
        result["unavailable"].append("Agency-stop graph requires stops, stop times, trips, and routes with join fields.")
    if has(tables, "shapes", "shape_id", "shape_pt_sequence"):
        sh = rel(tables, "shapes")
        result["points_per_shape"] = summary(db, f"SELECT shape_id, count(*) AS points FROM {sh} GROUP BY 1", "points")
        distance = col(tables, "shapes", "shape_dist_traveled")
        result["shape_consistency"] = query(db, f"""WITH points AS (
            SELECT shape_id, try_cast(shape_pt_sequence AS BIGINT) AS seq,
                try_cast({distance} AS DOUBLE) AS dist, rowid AS source_order FROM {sh}
        ), edits AS (
            SELECT *, lag(dist IGNORE NULLS) OVER (PARTITION BY shape_id ORDER BY seq, source_order) AS prev_dist,
                lag(seq) OVER (PARTITION BY shape_id ORDER BY source_order) AS prev_seq FROM points
        ) SELECT count(*) FILTER (WHERE dist < prev_dist) AS decreasing_distances,
            count(*) FILTER (WHERE dist = prev_dist) AS equal_distances,
            count(*) FILTER (WHERE seq < prev_seq) AS out_of_order_rows,
            min(shape_id || '/' || seq::VARCHAR, 5) FILTER (WHERE dist < prev_dist) AS examples,
            min(shape_id || '/' || seq::VARCHAR, 5) FILTER (WHERE dist = prev_dist) AS equal_examples FROM edits""")[0]
        if has(tables, "trips", "shape_id"):
            tr = rel(tables, "trips")
            result["shape_coverage"] = query(db, f"""WITH shapes AS (SELECT DISTINCT shape_id FROM {sh}),
                referenced AS (SELECT DISTINCT shape_id FROM {tr} WHERE {clean('shape_id')} IS NOT NULL)
                SELECT (SELECT count(*) FROM shapes) AS distinct_shapes,
                    (SELECT count(*) FROM shapes SEMI JOIN referenced USING (shape_id)) AS shapes_used_by_trips,
                    (SELECT count(*) FROM shapes ANTI JOIN referenced USING (shape_id)) AS shapes_unused_by_trips,
                    count(*) FILTER (WHERE {clean('shape_id')} IS NOT NULL) AS trips_with_shape_id,
                    count(*) FILTER (WHERE {clean('shape_id')} IS NULL) AS trips_without_shape_id
                FROM {tr}""")[0]
    return result


def relation_exists(db, name):
    return bool(db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [name]).fetchone()[0])


def identifier_analysis(db, tables):
    result = {"domains": {}, "scoped_patterns": {}, "possible_crosswalk_columns": [
        "stops.stop_code", "stops.stop_url", "routes.route_short_name", "trips.trip_short_name",
        "Agency route, trip, and stop-visit joins", "Observed delimited identifier suffixes"],
        "interpretation": "Prefixes, suffixes, stop codes and URL paths are evidence to investigate. "
        "No agency-source crosswalk or cross-version stability is assumed."}
    if has(tables, "agency", "agency_id", "agency_name"):
        result["agency_names"] = query(db, f"SELECT agency_id, agency_name FROM {rel(tables, 'agency')} ORDER BY 1, 2")
    domains = {"agency_id": ("agency", "agency_id"), "stop_id": ("stops", "stop_id"), "stop_code": ("stops", "stop_code"),
               "route_id": ("routes", "route_id"), "trip_id": ("trips", "trip_id"), "service_id": (None, "service_id"),
               "shape_id": ("shapes", "shape_id"), "fare_id": ("fare_attributes", "fare_id"),
               "fare_product_id": ("fare_products", "fare_product_id")}
    shape = """CASE WHEN regexp_full_match(id,'[0-9]+') THEN 'numeric string'
        WHEN regexp_full_match(id,'[A-Za-z0-9]+:.+') THEN 'colon prefix'
        WHEN regexp_full_match(id,'[A-Za-z0-9]+_.+') THEN 'underscore prefix'
        WHEN regexp_full_match(id,'[A-Za-z]+') THEN 'alphabetic'
        WHEN regexp_full_match(id,'[A-Za-z0-9]+') THEN 'alphanumeric'
        ELSE 'other' END"""
    for label, (table, column) in domains.items():
        if table and not has(tables, table, column):
            result["domains"][label] = {"status": "field or table absent"}
            continue
        source = rel(tables, table) if table else "service_ids"
        ids = f"SELECT DISTINCT {column} AS id FROM {source} WHERE {clean(column)} IS NOT NULL"
        patterns = query(db, f"""SELECT {shape} AS format, count(*) AS distinct_ids, min(id,5) AS examples
            FROM ({ids}) GROUP BY 1 ORDER BY distinct_ids DESC, format""")
        prefixes = query(db, f"""SELECT regexp_extract(id, '^([A-Za-z0-9]+)[:_]',1) AS prefix,
            count(*) AS distinct_ids, min(id,3) AS examples FROM ({ids})
            WHERE regexp_matches(id,'^[A-Za-z0-9]+[:_]') GROUP BY 1 ORDER BY distinct_ids DESC, prefix""")
        suffixes = f"""SELECT regexp_replace(id, '^[A-Za-z0-9]+[:_]', '') AS suffix, count(*) AS variants,
            min(id,5) AS examples FROM ({ids}) GROUP BY 1 HAVING count(*) > 1"""
        result["domains"][label] = {"formats": patterns, "prefixes": prefixes,
            "leading_zero_ids": value(db, f"SELECT count(*) FROM ({ids}) WHERE regexp_full_match(id,'0[0-9]+')"),
            "suffix_collisions": value(db, f"SELECT count(*) FROM ({suffixes})"),
            "suffix_collision_examples": query(db, f"SELECT * FROM ({suffixes}) ORDER BY variants DESC, suffix LIMIT 5")}
    if relation_exists(db, "trip_agencies"):
        for column in ("route_id", "trip_id", "service_id", "shape_id"):
            result["scoped_patterns"][column] = query(db, f"""SELECT agency_id,
                count(DISTINCT {column}) AS distinct_ids,
                count(DISTINCT {column}) FILTER (WHERE starts_with({column}, agency_id || ':')) AS agency_colon,
                count(DISTINCT {column}) FILTER (WHERE starts_with({column}, agency_id || '_')) AS agency_underscore,
                count(DISTINCT {column}) FILTER (WHERE regexp_full_match({column},'[0-9]+')) AS numeric,
                min({column},3) AS examples FROM (SELECT DISTINCT agency_id, {column} FROM trip_agencies
                WHERE {clean(column)} IS NOT NULL) GROUP BY 1 ORDER BY 1""")
    if relation_exists(db, "agency_stops"):
        result["scoped_patterns"]["stop_id"] = query(db, """SELECT agency_id, count(*) AS distinct_ids,
            count(*) FILTER (WHERE regexp_full_match(stop_id,'[0-9]+')) AS numeric,
            count(*) FILTER (WHERE starts_with(stop_id, agency_id || ':')) AS agency_colon,
            count(*) FILTER (WHERE starts_with(stop_id, agency_id || '_')) AS agency_underscore,
            min(stop_id,5) AS examples FROM agency_stops GROUP BY 1 ORDER BY 1""")
        if has(tables, "stops", "stop_code"):
            collisions = f"""SELECT a.agency_id, s.stop_code, count(DISTINCT s.stop_id) AS stops,
                min(s.stop_id,5) AS stop_examples FROM agency_stops a JOIN {rel(tables, 'stops')} s USING (stop_id)
                WHERE {clean('s.stop_code')} IS NOT NULL GROUP BY 1,2 HAVING count(DISTINCT s.stop_id)>1"""
            result["within_agency_stop_code_collisions"] = {"groups": value(db, f"SELECT count(*) FROM ({collisions})"),
                "examples": query(db, f"SELECT * FROM ({collisions}) ORDER BY stops DESC, agency_id, stop_code LIMIT 8")}
    return result


def classify_stop_match(candidates, agency):
    scoped = [item for item in candidates if agency in item["serving_agencies"]]
    exact = [item for item in scoped if "exact_stop_id" in item["evidence"]]
    if exact:
        return "EXACT_MATCH" if len(exact) == 1 else "AMBIGUOUS_MATCH"
    verified = [item for item in scoped if "verified_crosswalk" in item["evidence"]]
    if verified:
        return "VERIFIED_MAPPING" if len(verified) == 1 else "AMBIGUOUS_MATCH"
    return "AMBIGUOUS_MATCH" if len(scoped) > 1 else "CANDIDATE_MATCH" if scoped else "NO_MATCH"


def realtime_analysis(db, tables):
    agency, stop = ALERT["AgencyId"], ALERT["StopId"]
    timestamp = datetime.fromtimestamp(ALERT["timestamp"], timezone.utc)
    result = {"input": ALERT, "feed_generation_utc": timestamp.isoformat(), "candidates": [],
              "variants_checked": [stop, f"{agency}:{stop}", f"{agency}_{stop}", f"{agency}-{stop}"],
              "scope_join_available": relation_exists(db, "agency_stops"),
              "provenance": "User-supplied alert JSON. No live request or independently supplied agency feed.",
              "selector_support": [
                  {"selector": "AgencyId", "source": "agency.agency_id", "available": has(tables, "agency", "agency_id")},
                  {"selector": "RouteId and RouteType", "source": "routes.route_id, routes.route_type",
                   "available": has(tables, "routes", "route_id", "route_type")},
                  {"selector": "StopId", "source": "stops.stop_id", "available": has(tables, "stops", "stop_id")},
                  {"selector": "DirectionId with RouteId", "source": "trips.route_id and trips.direction_id",
                   "available": has(tables, "trips", "route_id", "direction_id")},
                  {"selector": "TripDescriptor", "source": "trips, first departure, service dates, frequencies when available",
                   "available": has(tables, "trips", "trip_id", "service_id") and has(tables, "stop_times", "departure_time"),
                   "constraint": "Trip instance resolution requires date/start time where needed. EntitySelector ignores the descriptor's schedule_relationship."},
                  {"selector": "Combinations", "source": "Agency, route, trip and stop visits",
                   "available": relation_exists(db, "agency_stops"),
                   "constraint": "All fields of one selector must match the same scoped service. DirectionId requires RouteId."}],
              "operator_feed_comparison": "Unavailable. Regional and operator feed crosswalks were not supplied."}
    result["agency_rows"] = query(db, f"SELECT * FROM {rel(tables, 'agency')} WHERE agency_id = ?", [agency]) if has(tables, "agency", "agency_id") else []
    if not has(tables, "stops", "stop_id"):
        return {**result, "status": "unavailable", "reason": "stops.stop_id is absent."}
    s = rel(tables, "stops")
    stop_name, stop_code, stop_url = [col(tables, "stops", c) for c in ("stop_name", "stop_code", "stop_url")]
    suffix = f"(^|[:_/-]){re.escape(stop)}$"
    name_checks = f"regexp_matches(lower({stop_name}), 'san[ .-]+pablo') AND regexp_matches(lower({stop_name}), '(west|w)[ .-]+grand')"
    candidates = query(db, f"""SELECT *, stop_id = ? AS exact_stop_id,
        regexp_matches(stop_id, ?) AND stop_id <> ? AS delimited_suffix,
        coalesce({stop_code} = ?, false) AS stop_code_match,
        coalesce(regexp_matches({stop_url}, ?), false) AS stop_url_match,
        coalesce({name_checks}, false) AS name_match FROM {s}
        WHERE regexp_matches(stop_id, ?) OR {stop_code} = ? OR regexp_matches({stop_url}, ?) OR ({name_checks})
        ORDER BY stop_id""", [stop, suffix, stop, stop, f"/{stop}/?(\\?.*)?$", suffix, stop, f"/{stop}/?(\\?.*)?$"])
    for row in candidates:
        sid = row["stop_id"]
        evidence = [key for key in ("exact_stop_id", "delimited_suffix", "stop_code_match", "stop_url_match", "name_match")
                    if row.pop(key)]
        row["evidence"] = evidence
        row["routes"], row["serving_agencies"], row["trip_examples"] = [], [], []
        if not result["scope_join_available"]:
            continue
        route_name = col(tables, "routes", "route_short_name", "r.")
        row["routes"] = query(db, f"""SELECT DISTINCT rs.agency_id, rs.route_id, {route_name} AS route_short_name
            FROM route_stops rs JOIN {rel(tables, 'routes')} r USING (route_id)
            WHERE rs.stop_id=? ORDER BY 1,2,3""", [sid])
        row["serving_agencies"] = sorted({route["agency_id"] for route in row["routes"] if route["agency_id"] is not None})
        trip_sql = """FROM trip_stop_counts s JOIN trip_agencies t USING(trip_id) WHERE s.stop_id=?"""
        row["visit_counts_by_agency"] = query(db, f"""SELECT agency_id, count(DISTINCT trip_id) AS distinct_trips,
            count(*) AS visits {trip_sql} GROUP BY 1 ORDER BY 1""", [sid])
        row["trip_examples"] = query(db, f"""SELECT DISTINCT agency_id, route_id, trip_id, direction_id, service_id
            {trip_sql} ORDER BY 1, 2, 3 LIMIT 10""", [sid])
        zones = {item.get("agency_timezone") for item in result["agency_rows"]} - {None, ""}
        if relation_exists(db, "effective_service_dates") and len(zones) == 1:
            zone = next(iter(zones))
            try:
                local_time = db.execute("SELECT timezone(?, to_timestamp(?))", [zone, ALERT["timestamp"]]).fetchone()[0]
            except Error as error:
                row["temporal_context_unavailable"] = str(error)
                continue
            row["alert_agency_local_datetime"] = local_time.isoformat()
            row["alert_local_calendar_date"] = local_time.date()
            row["agency_trips_by_service_date"] = []
            for service_date in (local_time.date() - timedelta(days=1), local_time.date()):
                active = db.execute(f"""SELECT count(DISTINCT trip_id) {trip_sql} AND agency_id=?
                    AND service_id IN (SELECT service_id FROM effective_service_dates WHERE service_date=?)""",
                    [sid, agency, service_date]).fetchone()[0]
                row["agency_trips_by_service_date"].append({"service_date": service_date, "trips": active})
    result["candidates"] = candidates
    result["classification"] = classify_stop_match(candidates, agency) if result["scope_join_available"] and result["agency_rows"] else (
        "NO_MATCH" if result["scope_join_available"] and has(tables, "agency", "agency_id") else None)
    result["status"] = "checked" if result["classification"] else "unavailable"
    result["known_exclusions"] = ["No explicit alert active period in the sample. Feed generation is distinct from observation, disruption start/end and ingestion.",
        "Service-day activity on the alert's local calendar date is context. An overnight trip can use the prior service date.",
        "Similar stop names and numeric suffixes supply candidate evidence only."]
    return result
