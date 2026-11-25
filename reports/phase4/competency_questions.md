# TransitKG competency questions

G1–G14 refer to statements in file order in the parameterized Cypher file. All 14 compiled on the installed server and returned their expected answers on the isolated synthetic fixture. Regional reference answers below come from DuckDB over the published Parquet. The regional graph equivalent awaits Phase 6. The SQL macros below were installed and exercised on the regional build. [Cypher examples](../../schema/competency.cypher) · [DuckDB macros](../../schema/timetable.sql) · [Measured regional references](architecture_comparison.json) · [Live fixture results](neo4j_verification.json)

| Question and research use | Query path and required evidence | Reference or acceptance check |
|---|---|---|
| Which stop does AC/55660 identify? Ground a scoped selector. | G1, Agency.source_id → AGENCY_SERVES_STOP → Stop.source_id and canonical_id. Compare with agency_stops. | EXACT_MATCH, 511-sf-bay-regional:stop:55660. |
| Which same-name records could a text extractor confuse? | G2, Stop.stop_name and source_id. Compare the complete exact-name SQL result. | San Pablo Av & W Grand Av returns 52227 and 55660. |
| Which agencies serve a stop? Keep shared-stop scope. | G3, agency_stops(agency_id,stop_id). | 341028 returns CM and SM. Agency AC serves 55660. |
| Which route does an agency publish? Resolve exact RouteId. | G4, routes(agency_id,route_id), exact endpoint mappings. | AC and AC:72 resolve to 511-sf-bay-regional:route:AC%3A72. |
| Which scheduled trip corresponds to a TripId? | G5, canonical_ids(trip,source_identifier). | AC:10056070 resolves to 511-sf-bay-regional:trip:AC%3A10056070. |
| Which routes visit a stop within an operator? | G6, OPERATES and ROUTE_SERVES_STOP, verified with route_stops. | AC/55660 has AC:72, AC:72L, AC:72M and AC:802. |
| Which stops can this route serve? | G7, route_stops(route_id,stop_id). Compare distinct endpoints. | AC:72 has 152. |
| Which trips belong to that route? | G8, trips(route_id,trip_id). | AC:72 has 247 templates. |
| Which service applies to this trip? | G9, trips(trip_id,service_id). | AC:10056070 uses AC:83048. |
| Which locations belong to a station? | G10, stops(parent_station,stop_id). | Parent -325 has children 823111 and 823128. Preserve all IDs as strings. |
| Which transfer rules apply between two stops? | G11, transfers from/to stop plus route/trip restrictions, transfer_type and min_transfer_time. Retrieve rules, apply specificity for supplied travel context. | 241 → 241 has one type-1 rule. For partially supplied travel context return conditional rules with qualifiers. |
| Which stops are served by several operators? | G12, group distinct agencies by Stop. Compare with agency_stops. | 189 stops. |
| Which trips match route, type and direction? | G13, Route.route_type and Trip.direction_id on one operator–route–trip path. For a supplied stop also use scoped_visits. | AC:72/type 3/direction 0 visits 55660 on 126 templates. Same stop and direction 1 returns zero. |
| May this graph query use these external tables? | G14, exactly one FeedSnapshot. Compare snapshot, normalized build, schema and canonical contract against manifests. | Positive match and negative missing, multiple, stale-build and wrong-snapshot cases tested. |
| What is a trip’s ordered stop sequence? Preserve loops. | DuckDB, stop_times(trip_id, stop_sequence, stop_id), order numerically. | AC:10056070 has 77 visits, starting 55888, 56899, 55802. Across the build 4,221 trips repeat a stop. |
| Does a service operate on a supplied date? | DuckDB active_services over service mappings, inclusive calendar bounds, weekday flags and calendar_dates. | 125 services on 2026-11-25. AC:83048 is absent that day. |
| Which trips visit a stop on a date? Avoid overstating alert scope. | G1 resolves identity, then DuckDB scoped_visits joins active_services on service_id. | AC/55660 on 2026-11-25 gives 112 templates and 112 visits. |
| How do exceptions change weekly service? | DuckDB active_services gives the matching explicit exception priority, including services without a base row. | On 2022-05-30 PG:80068 is added and PG:80067 removed. Synthetic exception-only service operates only on its added date. |
| How do after-midnight visits retain their service day? | DuckDB, stop_times(original time, service-day seconds), operator agency_timezone. | Trip 3D:1175, sequence 2 is 24:02:48 or 86,568 seconds. |
| Which dated trip instance fits a descriptor? | DuckDB dated_trip_candidates uses service_day, optional trip_id, route_id, direction_id and first_departure_seconds. Check route/type/agency/stop scope on the same trip when provided. | AC:10155020, AC:72L, direction 0, 44,280 seconds on 2026-11-25 yields one. A different route or start time yields zero. |
| What scheduled trips might a scoped closure affect? | G1 and G6 for identity and routes, DuckDB for exact trip visits on those routes. | 345 AC trip templates at 55660 across the full snapshot. Neighbor 52227 has 339. These are scheduled candidates. |
| Can a route-and-stop selector cross routes or agencies? | G4 and G6 on the same Route, then scoped_visits for additional trip predicates. | AC/AC:72/55660/direction 0 yields 126. Changing the agency to BA yields zero. |
| Can a shared-stop closure stay within one agency? | Keep a single AffectedScope for the agency and stop. G1 establishes scheduled service. Filter G6 or scoped_visits by that agency before expanding routes/trips. | On 341028 use G3 as the two-operator control. Each operator’s result must contain only its own routes. |
| Which disruption events were known and active? | Future observation, event, scope, interval and evidence layer. Filter by observation/as-of time and applicable validity independently, then match every populated scope field. | Conceptual until the dynamic schema exists. Keep explicitly unknown bounds distinguishable from known ongoing or open-ended validity. |

## Parameterized timetable examples

Open the verified build with design_static_graph.open_build. The connection creates only transient views and macros. Bind named parameters through DuckDB. Use only populated selector predicates. Validate selector syntax first, including the requirement for a route with an EntitySelector direction. Route-level membership does not establish that a particular trip on that route visits the stop.

~~~sql
SELECT stop_sequence, stop_id, arrival_time, arrival_time_seconds,
       departure_time, departure_time_seconds
FROM stop_times WHERE trip_id=$trip ORDER BY stop_sequence;

SELECT EXISTS(SELECT 1 FROM active_services($service_day) WHERE service_id=$service);

SELECT DISTINCT canonical_trip_id, route_id
FROM scoped_visits($agency,$route,$route_type,$stop,$direction,$trip)
JOIN active_services($service_day) USING(service_id) ORDER BY route_id, canonical_trip_id;

SELECT canonical_trip_id, route_id, service_id
FROM dated_trip_candidates($service_day,$trip,$route,$direction,$start_seconds);

SELECT p.shape_pt_sequence,p.shape_pt_lat,p.shape_pt_lon,p.shape_dist_traveled
FROM trips t JOIN shapes p USING(shape_id)
WHERE t.trip_id=$trip ORDER BY p.shape_pt_sequence;
~~~

The date-scoped answer uses the operator’s GTFS service date. An undated selector may match a template, but a dated occurrence requires a valid supplied or independently evidenced service date. For missing trip_id require the specification’s alternative identifying fields and exactly one matching scheduled instance. A frequency-based descriptor requires the frequency period, date and instance start semantics; the scheduled-only macro deliberately filters frequency-defined templates. Preserve unresolved descriptors, extra unknown fields and ambiguity as evidence. An unknown or contradictory supplied field must not be translated into a null wildcard.

## Future event query contract

For the future dynamic model, one observation can own multiple indexed scopes. Inside a scope, all supplied fields refer to one consistent scheduled path. To retrieve active events, find observations visible by the requested as-of time, apply update/supersession semantics, then select matching scopes and intervals. Return the supporting observation, original selector and temporal evidence. For an absent alert active_period, GTFS display follows the alert’s presence in the feed. Inferring actual disruption onset or cessation requires separate evidence. The precise history and supersession model is deferred to the alert phase. [GTFS-Realtime reference](https://gtfs.org/documentation/realtime/reference/) · [GTFS Service Alerts](https://gtfs.org/documentation/realtime/feed-entities/service-alerts/)

