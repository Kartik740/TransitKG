-- Install on an in-memory DuckDB connection over the verified build's Parquet views.
CREATE MACRO active_services(on_date) AS TABLE
SELECT i.source_identifier AS service_id
FROM canonical_ids i
LEFT JOIN calendar c ON c.service_id=i.source_identifier
LEFT JOIN calendar_dates e ON e.service_id=i.source_identifier AND e.date=CAST(on_date AS DATE)
WHERE i.entity_type='service' AND CASE WHEN e.exception_type IS NOT NULL THEN e.exception_type=1
ELSE CAST(on_date AS DATE) BETWEEN c.start_date AND c.end_date AND
list_extract([c.monday,c.tuesday,c.wednesday,c.thursday,c.friday,c.saturday,c.sunday],
             isodow(CAST(on_date AS DATE)))=1 END;

-- Each supplied predicate applies to the same visit. Null arguments mean absent selector fields.
CREATE MACRO scoped_visits(a,r,rt,s,d,t) AS TABLE
SELECT x.agency_id,x.route_id,x.route_type,x.direction_id,x.service_id,x.canonical_trip_id,
       v.trip_id,v.stop_sequence,v.stop_id,v.arrival_time,v.departure_time,
       v.arrival_time_seconds,v.departure_time_seconds
FROM trip_selector_context x JOIN stop_times v USING(trip_id)
WHERE (a IS NULL OR x.agency_id=a) AND (r IS NULL OR x.route_id=r)
AND (rt IS NULL OR x.route_type=rt) AND (s IS NULL OR v.stop_id=s)
AND (d IS NULL OR x.direction_id=d) AND (t IS NULL OR v.trip_id=t);

-- Scheduled, non-frequency trip occurrences for a supplied service date.
-- Pass validated service-day seconds for start_sec. Validate descriptor presence rules before lookup.
CREATE MACRO dated_trip_candidates(service_day, t, r, d, start_sec) AS TABLE
SELECT x.* FROM trip_selector_context x JOIN active_services(service_day) USING(service_id)
WHERE NOT x.frequency_defined AND (t IS NULL OR x.trip_id=t)
AND (r IS NULL OR x.route_id=r) AND (d IS NULL OR x.direction_id=d)
AND (start_sec IS NULL OR x.first_departure_seconds=start_sec)
AND (t IS NOT NULL OR (r IS NOT NULL AND d IS NOT NULL AND start_sec IS NOT NULL));

