# TransitKG Phase 2 dataset exploration

## Executive summary

The extracted directory contains **27 files** and **26 profiled tables**. The run recorded **0 critical or high-severity findings** and **0 failed checks**. Source checksum verification passed. The AC/55660 selector outcome is **EXACT_MATCH**.

Core source counts include 41 agency records, 665 routes, 92,827 trips, 21,664 stop and station records, 3,185,115 stop-time rows.

## Dataset overview and complete file inventory

Source input is `GTFSTransitData_RG`. Inventoried files total **296,177,172 bytes**. GTFS Schedule revision **April 27, 2026** and GTFS Realtime were consulted on **2026-11-25**. [GTFS Schedule reference](https://gtfs.org/documentation/schedule/reference/) [GTFS Realtime reference](https://gtfs.org/documentation/realtime/reference/) [511 Data Exchange specification](https://511.org/media/6657/show)

| Source path | Size and bytes | Records | Columns | Encoding | Classification and file presence | Status |
|---|---:|---:|---:|---|---|---|
| agency.txt | 4.87 KiB (4,990) | 41 | 9 | UTF-8 | GTFS Schedule / Required | profiled |
| areas.txt | 19.75 KiB (20,227) | 678 | 2 | UTF-8 | GTFS Schedule / Optional | profiled |
| attributions.txt | 2.79 KiB (2,855) | 41 | 11 | UTF-8 | GTFS Schedule / Optional | profiled |
| calendar.txt | 19.52 KiB (19,989) | 485 | 10 | UTF-8 | GTFS Schedule / Conditionally Required | profiled |
| calendar_attributes.txt | 10.07 KiB (10,307) | 449 | 2 | UTF-8 | 511 extension / outside standard | profiled |
| calendar_dates.txt | 111.48 KiB (114,151) | 5,706 | 3 | UTF-8 | GTFS Schedule / Conditionally Required | profiled |
| changes-toc.md | 54.87 KiB (56,190) | — | — | UTF-8 | ancillary / outside standard | inventory only |
| directions.txt | 17.88 KiB (18,314) | 1,184 | 3 | UTF-8 | 511 extension / outside standard | profiled |
| fare_leg_join_rules.txt | 94 B (94) | 5 | 4 | UTF-8 | GTFS Schedule / Optional | profiled |
| fare_leg_rules.txt | 234.19 KiB (239,811) | 3,610 | 9 | UTF-8 | GTFS Schedule / Optional | profiled |
| fare_media.txt | 207 B (207) | 7 | 3 | UTF-8 | GTFS Schedule / Optional | profiled |
| fare_products.txt | 833.79 KiB (853,798) | 11,545 | 10 | UTF-8 | GTFS Schedule / Optional | profiled |
| fare_transfer_rules.txt | 11.82 KiB (12,100) | 228 | 8 | UTF-8 | GTFS Schedule / Optional | profiled |
| feed_info.txt | 195 B (195) | 1 | 9 | UTF-8 | GTFS Schedule / Conditionally Required | profiled |
| levels.txt | 4.39 KiB (4,495) | 92 | 3 | UTF-8 | GTFS Schedule / Conditionally Required | profiled |
| mtc_feed_versions.txt | 2.90 KiB (2,969) | 41 | 5 | UTF-8 | 511 extension / outside standard | profiled |
| pathways.txt | 133.09 KiB (136,287) | 1,632 | 12 | UTF-8 | GTFS Schedule / Optional | profiled |
| rider_categories.txt | 645 B (645) | 11 | 6 | UTF-8 | GTFS Schedule / Optional | profiled |
| route_attributes.txt | 5.07 KiB (5,188) | 329 | 4 | UTF-8 | 511 extension / outside standard | profiled |
| routes.txt | 70.58 KiB (72,273) | 665 | 15 | UTF-8 | GTFS Schedule / Required | profiled |
| shapes.txt | 54.685 MiB (57,341,525) | 1,340,496 | 5 | UTF-8 | GTFS Schedule / Conditionally Required | profiled |
| stop_areas.txt | 424.06 KiB (434,242) | 33,164 | 2 | UTF-8 | GTFS Schedule / Optional | profiled |
| stop_times.txt | 216.474 MiB (226,989,221) | 3,185,115 | 22 | UTF-8 | GTFS Schedule / Required | profiled |
| stops.txt | 2.379 MiB (2,494,891) | 21,664 | 16 | UTF-8 | GTFS Schedule / Conditionally Required | profiled |
| timeframes.txt | 210 B (210) | 3 | 4 | UTF-8 | GTFS Schedule / Optional | profiled |
| transfers.txt | 2.27 KiB (2,321) | 61 | 8 | UTF-8 | GTFS Schedule / Optional | profiled |
| trips.txt | 7.000 MiB (7,339,677) | 92,827 | 13 | UTF-8 | GTFS Schedule / Required | profiled |

Full headers, SHA-256 values, line counts, rejection counts and absent standard files are in [dataset_inventory.json](dataset_inventory.json). Ancillary documents are inventoried as files. Their physical lines are available in the inventory.

## Table and column statistics

The profiler produced column statistics for **198 fields**. Raw fields are VARCHAR. Typed date, time and numeric views supply the reported ranges. Representative values are selected deterministically, with populated-value samples ordered lexically.

| Table | Empty columns | Columns with missing values | Columns with invalid or unexpected nonempty values |
|---|---:|---:|---:|
| agency.txt | 1 | 4 | 0 |
| areas.txt | 0 | 1 | 0 |
| attributions.txt | 7 | 7 | 0 |
| calendar.txt | 0 | 0 | 0 |
| calendar_attributes.txt | 0 | 0 | 0 |
| calendar_dates.txt | 0 | 0 | 0 |
| directions.txt | 0 | 0 | 0 |
| fare_leg_join_rules.txt | 2 | 2 | 0 |
| fare_leg_rules.txt | 3 | 4 | 0 |
| fare_media.txt | 0 | 0 | 0 |
| fare_products.txt | 1 | 6 | 0 |
| fare_transfer_rules.txt | 0 | 4 | 0 |
| feed_info.txt | 6 | 6 | 0 |
| levels.txt | 0 | 0 | 0 |
| mtc_feed_versions.txt | 0 | 0 | 0 |
| pathways.txt | 5 | 6 | 0 |
| rider_categories.txt | 2 | 3 | 0 |
| route_attributes.txt | 0 | 0 | 0 |
| routes.txt | 4 | 10 | 0 |
| shapes.txt | 0 | 1 | 0 |
| stop_areas.txt | 0 | 0 | 0 |
| stop_times.txt | 12 | 16 | 0 |
| stops.txt | 2 | 11 | 0 |
| timeframes.txt | 0 | 2 | 0 |
| transfers.txt | 2 | 5 | 0 |
| trips.txt | 3 | 9 | 0 |

## Missing-value analysis

Unquoted blanks and quoted empty strings are counted separately in [data_profile.json](data_profile.json). Short or malformed CSV rows are rejected and counted in the inventory. Missing columns have their own entries. Required fields, defaulted empties and optional fields follow the recorded field presence. Conditional checks are documented with the corresponding relationship or temporal results.

## Duplicate and key analysis

| Source | Key | Complete distinct keys | Missing required key | Duplicate groups | Exact excess rows | Conflicting groups |
|---|---|---:|---:|---:|---:|---:|
| agency.txt | agency_id | 41 | 0 | 0 | 0 | 0 |
| areas.txt | area_id | 678 | 0 | 0 | 0 | 0 |
| attributions.txt | attribution_id | 0 | 0 | 0 | 40 | 0 |
| calendar.txt | service_id | 485 | 0 | 0 | 0 | 0 |
| calendar_attributes.txt | service_id | 449 | 0 | 0 | 0 | 0 |
| calendar_dates.txt | service_id + date | 5706 | 0 | 0 | 0 | 0 |
| directions.txt | route_id + direction_id | 1183 | 0 | 1 | 0 | 1 |
| fare_leg_join_rules.txt | from_network_id + to_network_id + from_stop_id + to_stop_id | 5 | 0 | 0 | 0 | 0 |
| fare_leg_rules.txt | network_id + from_area_id + to_area_id + from_timeframe_group_id + to_timeframe_group_id + fare_product_id | 3610 | 0 | 0 | 0 | 0 |
| fare_media.txt | fare_media_id | 7 | 0 | 0 | 0 | 0 |
| fare_products.txt | fare_product_id + rider_category_id + fare_media_id | 11545 | 0 | 0 | 0 | 0 |
| fare_transfer_rules.txt | from_leg_group_id + to_leg_group_id + fare_product_id + transfer_count + duration_limit | 180 | 0 | 20 | 0 | 20 |
| feed_info.txt | single-row constraint | 1 | 0 | 0 | 0 | 0 |
| levels.txt | level_id | 92 | 0 | 0 | 0 | 0 |
| mtc_feed_versions.txt | agency_id | 41 | 0 | 0 | 0 | 0 |
| pathways.txt | pathway_id | 1632 | 0 | 0 | 0 | 0 |
| rider_categories.txt | rider_category_id | 11 | 0 | 0 | 0 | 0 |
| route_attributes.txt | route_id | 327 | 0 | 2 | 2 | 0 |
| routes.txt | route_id | 665 | 0 | 0 | 0 | 0 |
| shapes.txt | shape_id + shape_pt_sequence | 1340496 | 0 | 0 | 0 | 0 |
| stop_areas.txt | area_id + stop_id | 33164 | 0 | 0 | 0 | 0 |
| stop_times.txt | trip_id + stop_sequence | 3185115 | 0 | 0 | 0 | 0 |
| stops.txt | stop_id | 21664 | 0 | 0 | 0 | 0 |
| timeframes.txt | timeframe_group_id + start_time + end_time + service_id | 3 | 0 | 0 | 0 | 0 |
| transfers.txt | from_stop_id + to_stop_id + from_trip_id + to_trip_id + from_route_id + to_route_id | 61 | 0 | 0 | 0 | 0 |
| trips.txt | trip_id | 92827 | 0 | 0 | 0 | 0 |

Duplicate groups count repeated keys. Exact excess rows count redundant copies of the same parsed row. A conflicting group has multiple distinct row variants under the same key. [Key details and examples](data_quality_report.json)

## Referential integrity

Checked **46 relationships**. Integrity uses valid populated references divided by populated references plus missing required references. Blank optional references are visible in the detailed results. The service ID domain combines base calendar IDs and valid exception additions.

| Reference | Applicable | Valid | Invalid | Missing required | Integrity % |
|---|---:|---:|---:|---:|---:|
| attributions.txt.agency_id → agency.agency_id | 0 | 0 | 0 | 0 | — |
| attributions.txt.route_id → routes.route_id | 0 | 0 | 0 | 0 | — |
| attributions.txt.trip_id → trips.trip_id | 0 | 0 | 0 | 0 | — |
| calendar_attributes.txt.service_id → calendar.service_id | 449 | 449 | 0 | 0 | 100.0 |
| calendar_dates.txt.service_id → service_ids.service_id | 5,706 | 5,706 | 0 | 0 | 100.0 |
| directions.txt.route_id → routes.route_id | 1,184 | 1,184 | 0 | 0 | 100.0 |
| fare_leg_join_rules.txt.from_network_id → routes.network_id or networks.network_id | 5 | 5 | 0 | 0 | 100.0 |
| fare_leg_join_rules.txt.to_network_id → routes.network_id or networks.network_id | 5 | 5 | 0 | 0 | 100.0 |
| fare_leg_join_rules.txt.from_stop_id → stops.stop_id | 0 | 0 | 0 | 0 | — |
| fare_leg_join_rules.txt.to_stop_id → stops.stop_id | 0 | 0 | 0 | 0 | — |
| fare_leg_rules.txt.network_id → routes.network_id or networks.network_id | 3,610 | 3,610 | 0 | 0 | 100.0 |
| fare_leg_rules.txt.from_area_id → areas.area_id | 3,610 | 3,610 | 0 | 0 | 100.0 |
| fare_leg_rules.txt.to_area_id → areas.area_id | 3,610 | 3,610 | 0 | 0 | 100.0 |
| fare_leg_rules.txt.from_timeframe_group_id → timeframes.timeframe_group_id | 2 | 2 | 0 | 0 | 100.0 |
| fare_leg_rules.txt.to_timeframe_group_id → timeframes.timeframe_group_id | 0 | 0 | 0 | 0 | — |
| fare_leg_rules.txt.fare_product_id → fare_products.fare_product_id | 3,610 | 3,610 | 0 | 0 | 100.0 |
| fare_products.txt.rider_category_id → rider_categories.rider_category_id | 11,540 | 11,540 | 0 | 0 | 100.0 |
| fare_products.txt.fare_media_id → fare_media.fare_media_id | 11,542 | 11,542 | 0 | 0 | 100.0 |
| fare_transfer_rules.txt.from_leg_group_id → fare_leg_rules.leg_group_id | 228 | 228 | 0 | 0 | 100.0 |
| fare_transfer_rules.txt.to_leg_group_id → fare_leg_rules.leg_group_id | 228 | 228 | 0 | 0 | 100.0 |
| fare_transfer_rules.txt.fare_product_id → fare_products.fare_product_id | 228 | 228 | 0 | 0 | 100.0 |
| mtc_feed_versions.txt.agency_id → agency.agency_id | 41 | 41 | 0 | 0 | 100.0 |
| pathways.txt.from_stop_id → stops.stop_id | 1,632 | 1,632 | 0 | 0 | 100.0 |
| pathways.txt.to_stop_id → stops.stop_id | 1,632 | 1,632 | 0 | 0 | 100.0 |
| route_attributes.txt.route_id → routes.route_id | 329 | 329 | 0 | 0 | 100.0 |
| routes.txt.agency_id → agency.agency_id | 665 | 665 | 0 | 0 | 100.0 |
| stop_areas.txt.area_id → areas.area_id | 33,164 | 33,164 | 0 | 0 | 100.0 |
| stop_areas.txt.stop_id → stops.stop_id | 33,164 | 33,164 | 0 | 0 | 100.0 |
| stop_times.txt.trip_id → trips.trip_id | 3,185,115 | 3,185,115 | 0 | 0 | 100.0 |
| stop_times.txt.stop_id → stops.stop_id | 3,185,115 | 3,185,115 | 0 | 0 | 100.0 |
| stop_times.txt.location_group_id → location_groups.location_group_id | 0 | 0 | 0 | 0 | — |
| stop_times.txt.location_id → locations.id | 0 | 0 | 0 | 0 | — |
| stop_times.txt.pickup_booking_rule_id → booking_rules.booking_rule_id | 0 | 0 | 0 | 0 | — |
| stop_times.txt.drop_off_booking_rule_id → booking_rules.booking_rule_id | 0 | 0 | 0 | 0 | — |
| stops.txt.parent_station → stops.stop_id | 2,222 | 2,222 | 0 | 0 | 100.0 |
| stops.txt.level_id → levels.level_id | 1,426 | 1,426 | 0 | 0 | 100.0 |
| timeframes.txt.service_id → service_ids.service_id | 3 | 3 | 0 | 0 | 100.0 |
| transfers.txt.from_stop_id → stops.stop_id | 61 | 61 | 0 | 0 | 100.0 |
| transfers.txt.to_stop_id → stops.stop_id | 61 | 61 | 0 | 0 | 100.0 |
| transfers.txt.from_route_id → routes.route_id | 44 | 44 | 0 | 0 | 100.0 |
| transfers.txt.to_route_id → routes.route_id | 44 | 44 | 0 | 0 | 100.0 |
| transfers.txt.from_trip_id → trips.trip_id | 0 | 0 | 0 | 0 | — |
| transfers.txt.to_trip_id → trips.trip_id | 0 | 0 | 0 | 0 | — |
| trips.txt.route_id → routes.route_id | 92,827 | 92,827 | 0 | 0 | 100.0 |
| trips.txt.service_id → service_ids.service_id | 92,827 | 92,827 | 0 | 0 | 100.0 |
| trips.txt.shape_id → shapes.shape_id | 91,296 | 91,296 | 0 | 0 | 100.0 |

[Referential integrity counts and examples](referential_integrity_report.json)

## Agency and identifier analysis

| Domain | Observed formats and distinct IDs | Collisions after stripping a leading prefix |
|---|---|---:|
| agency_id | alphabetic (40), alphanumeric (1) | 0 |
| stop_id | numeric string (19,355), colon prefix (1,914), underscore prefix (244), alphabetic (110), alphanumeric (23), other (18) | 364 |
| stop_code | numeric string (19,768), colon prefix (315), underscore prefix (245), alphabetic (113), alphanumeric (25), other (17) | 333 |
| route_id | colon prefix (665) | 101 |
| trip_id | colon prefix (92,827) | 478 |
| service_id | colon prefix (485) | 0 |
| shape_id | colon prefix (2,446) | 72 |
| fare_id | field or table absent | — |
| fare_product_id | colon prefix (2,927), other (2), alphabetic (1) | 24 |

Agency–stop associations are derived through routes, trips and validated platform visits. The identifier report includes each agency, per-agency prefix distributions, stop-code collisions, and samples of colliding suffixes. [Identifier analysis](identifier_analysis.json)

## Realtime alert compatibility

Outcome for the combined `AgencyId=AC` and `StopId=55660` selector is **EXACT_MATCH**. Feed generation in the example is **2026-11-24T18:00:09+00:00**. Separate timestamps are needed for observation, disruption start, disruption end and ingestion.

| Stop | Name | Evidence | Serving agencies | Routes | AC trips |
|---|---|---|---|---:|---:|
| 52227 | San Pablo Av & W Grand Av | name_match | AC | 4 | 339 |
| 55660 | San Pablo Av & W Grand Av | exact_stop_id, stop_code_match, stop_url_match, name_match | AC | 4 | 345 |

AC service reaches stop `55660` via AC:72, AC:72L, AC:72M, AC:802. Scheduled AC trip templates visiting the stop and running on the adjacent service dates total 112 on 2026-11-23, 112 on 2026-11-24. Those dates are computed from the example feed timestamp and the agency timezone.

Route and trip evidence, checked identifier variants and support for AgencyId, RouteId, RouteType, StopId, DirectionId and TripDescriptor are in [realtime_compatibility_report.json](realtime_compatibility_report.json). Use conjunctive matching within each selector. Match direction with route and resolve trip templates to a service date and start time where required. Verify the paired operator feed before generalizing this example to other identifiers.

## Temporal analysis

Across the complete calendar domain, effective service spans **2021-06-22 to 2099-12-31** across **84002 service-date pairs**. Trip-backed service spans **2021-06-22 to 2099-12-31**. Calendar-exception-only service IDs total **0**.

Stop times include **100912** arrivals past 24:00:00 and **100948** departures past 24:00:00. Missing arrivals and departures total **15109** and **15109**, with **0** and **0** classified as required by the implemented endpoint and timepoint checks.

Agency time zones and their row counts are America/Los_Angeles (41). Inspect effective service dates, weekly flags, calendar exceptions, time bounds and raw feed validity fields in [temporal_network_graph.json](temporal_network_graph.json). Service-day context can constrain an alert's trip instance. The sample supplies no explicit active period.

Service IDs on the latest date include `PE:72837`, `PG:80067`. The regional feed version and both feed validity fields are blank. The calendar range describes scheduled service records. Verify the snapshot's publication and active dates separately.

## Geographic and network analysis

Valid stop coordinates cover latitude **36.97492 to 39.09981** and longitude **-123.05377 to -120.95269**. **189 stops** are served by multiple agencies. Repeated stop-name and shared-coordinate groups total **5270** and **381** respectively. These groups are candidates for investigation.

The joins produced **33985 route–stop pairs** and **19904 agency–stop pairs**. Median unique stops per trip is **30.0**. The JSON includes route and location types, routes per agency, trips per route, stops per agency, parent station checks and shape-point consistency.

Shape IDs are populated on **91296** trips. **2434** distinct shapes are referenced by trips.

## Preliminary knowledge graph assessment

| Entity | Sources | Identifier | Estimated nodes |
|---|---|---|---:|
| Agency | agency.txt | agency_id | 41 |
| Route | routes.txt | route_id | 665 |
| Trip | trips.txt | trip_id | 92827 |
| Stop | stops.txt | stop_id | 21664 |
| Service | calendar.txt and calendar_dates.txt | service_id | 485 |
| ServiceException | calendar_dates.txt | service_id + date | 5706 |
| Shape | shapes.txt | shape_id | 2446 |
| FeedVersion | source manifest and feed_info.txt | version provenance and source checksum manifest | 1 |

| Relationship | Cardinality | Origin | Estimated records or pairs |
|---|---|---|---:|
| Agency OPERATES Route | 1:N | direct | 665 |
| Route HAS_TRIP Trip | 1:N | direct | 92827 |
| Trip RUNS_ON Service | N:1 | direct | 92827 |
| Trip VISITS_STOP Stop | N:M | direct | 3185115 |
| Route SERVES_STOP Stop | N:M | derived | 33985 |
| Agency SERVES_STOP Stop | N:M | derived | 19904 |
| Stop PART_OF Stop | N:1 | direct | 2222 |
| Stop NEXT_STOP Stop | N:M | derived | 22705 |
| Stop TRANSFERS_TO Stop | N:M | direct policy | 61 |
| Service HAS_EXCEPTION ServiceException | 1:N | direct | 5706 |
| Trip FOLLOWS Shape | N:1 | direct | 91296 |

Estimates are from source records or validated joins as described in [the detailed graph assessment](temporal_network_graph.json). Counts can change after issue review.

- **Visit nodes**. Up to 3,185,115 visit nodes and 6,370,230 linking edges before validity filtering. Supports visit-level provenance and traversals, with higher graph write, index and working-set costs.
- **Visit relationship properties**. Up to 3,185,115 sequence-aware Trip–Stop visit edges. Direct trip traversal with times on the edge. A multiedge representation must preserve loops and visit order.
- **External DuckDB or Parquet timetable**. 3,185,115 timetable rows plus graph connectivity projections. Column pruning and batch filtering support timetable queries with fewer graph objects. Cross-store lookups and provenance alignment add complexity for per-visit queries.

Benchmark an external timetable and graph connectivity as the initial design. Compare bulk load time, storage, p50/p95 selector and timetable latency, and incremental update cost against visit properties and visit nodes using the same records and queries.

## Identified data-quality issues

Issue totals are CRITICAL 0, HIGH 0, MEDIUM 5, LOW 0, INFORMATIONAL 12. An issue can cover records also counted in another check.

| Issue | Severity | File and field | Records | Graph impact |
|---|---|---|---:|---|
| P2-011 | MEDIUM | attributions.txt / attribution_id | 40 | A graph load could conflate records or create repeated edges. |
| P2-012 | MEDIUM | directions.txt / route_id + direction_id | 2 | A graph load could conflate records or create repeated edges. |
| P2-013 | MEDIUM | fare_transfer_rules.txt / from_leg_group_id + to_leg_group_id + fare_product_id + transfer_count + duration_limit | 68 | A graph load could conflate records or create repeated edges. |
| P2-014 | MEDIUM | route_attributes.txt / route_id | 4 | A graph load could conflate records or create repeated edges. |
| P2-016 | MEDIUM | shapes.txt / shape_dist_traveled | 21 | Consecutive populated shape distances are equal. |

[All issues, evidence and suggested handling](data_quality_report.json)

## Unresolved questions and check coverage

- Provider-only and undocumented columns are profiled as raw text. Verify their semantics and the meaning and versioning of `mtc_feed_versions.txt` with 511.
- Operator-feed IDs, regional transformation guarantees and identifier stability across snapshots require comparison with matching dated sources. The supplied sample can only validate the static regional evidence it matches.
- Value checks cover targeted syntax, ranges, enumerations, keys and join rules. Full GTFS conformance, all optional fare/booking conditional logic, geospatial shape-to-stop distances, geometry topology, authoritative IANA/ISO registry membership and transfer routing behavior are outside the executed checks.
- Shape distance values retain source units. A single coordinate range summarizes the bounding box.
- Deterministic samples are bounded and can emphasize lexically early IDs. Record and distinct counts use full tables.

Fields with unresolved documented types include the following.

- `fare_leg_rules.txt` contains `transfer_only`.
- `fare_products.txt` contains `duration_start`, `duration_amount`, `duration_unit`, `duration_type`.
- `fare_transfer_rules.txt` contains `filter_fare_product_id`.
- `mtc_feed_versions.txt` contains `agency_id`, `feed_version`, `sha1`, `feed_start_date`, `feed_end_date`.
- `rider_categories.txt` contains `min_age`, `max_age`.
- `routes.txt` contains `as_route`.
- `stop_times.txt` contains `safe_duration_factor`, `safe_duration_offset`, `mean_duration_factor`, `mean_duration_offset`.

## Recommended data-cleaning strategies

- Preserve source ID text, leading zeros, feed scope and checksum provenance. Audit any normalization against suffix and stop-code collisions.
- Resolve conflicting key groups with dated provider evidence. Track invalid references and coordinate or time anomalies without silently dropping records.
- Use typed service-day seconds and validated service-date pairs for time analysis. Preserve source time text for traceability.

## Recommendations for Phase 3

Prioritize the critical and high findings, obtain same-version operator and regional evidence for identifier crosswalks, and design an explicit policy for shared stops and unresolved compound selectors. Compare the timetable storage options on the measured feed before committing to a graph model.

## Execution time and resource consumption

Python **3.14.0**, DuckDB **1.5.0**, Windows-11-10.0.26300-SP0. The run used **31.329 s wall time**, **44.562 s process CPU time**, **908.98 MiB peak process RSS**, and **100,937,728 bytes** for the temporary database at checkpoint. DuckDB used **2 threads** with a **768MB** memory setting. Peak RSS includes Python and DuckDB allocations. The checkpoint size excludes transient spill peaks.

## Reproducibility

From the workspace root in PowerShell, install the pinned dependency and run the profiler and fixture tests.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r scripts/requirements-phase2.txt
.venv/Scripts/python.exe scripts/profile_gtfs.py "GTFSTransitData_RG" --output reports/phase2
.venv/Scripts/python.exe -m unittest discover -s scripts -p 'test_profile_gtfs.py'
```

The profiler accepts any extracted feed directory or ZIP archive. Source files are opened read-only. Temporary tables are removed after the run. Report directories must be outside the source. The inventory records the input checksums, specification revision and execution settings.
