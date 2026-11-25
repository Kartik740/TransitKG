# Phase 5 transformation contract

**Proposed version 1.0.0.** Input is one published Phase 3 build. Output is a staged set of typed graph-ready Parquet files plus a manifest. Phase 5 ends at validated files. The graph installation and its measured fingerprint belong to Phase 6.

## Input and publication boundary

1. Resolve the explicitly selected build directory and compare its manifest to the Phase 3 publication. Verify the path set, byte SHA-256 digests, source snapshot ID, normalized build ID, namespace and canonical contract version. Pin the schema file SHA-256. Record the transformer release or source digest, configuration, dependency versions, start/end times and resource measurements. Abort on any incompatible or ambiguous build.
2. Open read-only Parquet views in a transient DuckDB connection. Read canonical_ids, agency, routes, trips, stops, route_stops, agency_stops and transfers for graph rows. Check calendar, calendar_dates, stop_times, trip_selector_context, shapes, row_exception_ledger and the manifest for consistency. Shapes, calendars, timetable and ancillary tables remain queryable in the same publication. Account for every table by its manifest role.
3. Write into a new staging directory. Produce one file per label and one per relationship type. Names are the exact label or type plus the Parquet extension under nodes/ or relationships/ in that future artifact. Keep deterministic row order by identity, a byte digest, schema, counts and logical fingerprint per output. Publish by an atomic directory rename after every validation passes. Reuse only a verified identical build. Reject an occupied path with different evidence.

## Row contract

The JSON schema is normative for labels, types, required properties, endpoint orientation, table names, column names and identity tuples. Source ID columns are exact strings. Required properties must be present and typed. Preserve explicit empty strings, zero and case. An absent optional property is a Parquet null and an omitted Neo4j property. Source columns have the meaning in the Phase 3 policy and GTFS reference. INTEGER maps to signed 64-bit Neo4j integer and STRING to UTF-8 text. [Property and relationship definitions](../../schema/static_graph_schema.json)

| Output | Projection | Reference rows |
|---|---|---:|
| Agency | Mapping type agency joined to agency.agency_id | 41 |
| Route | Mapping type route joined to routes.route_id | 665 |
| Trip | Mapping type trip joined to trips.trip_id | 92,827 |
| Stop | Mapping type stop joined to stops.stop_id, including unserved locations | 21,664 |
| Service | All mapping rows of type service, including exception-only services | 485 |
| FeedSnapshot | One manifest row with schema and construction versions and the expected static fingerprint | 1 |
| OPERATES | routes.agency_id → routes.route_id | 665 |
| HAS_TRIP | trips.route_id → trips.trip_id | 92,827 |
| RUNS_ON | trips.trip_id → trips.service_id | 92,827 |
| ROUTE_SERVES_STOP | route_stops.route_id → stop_id, with accepted scheduled_visits and scheduled_trips | 33,985 |
| AGENCY_SERVES_STOP | agency_stops.agency_id → stop_id, with scheduled_visits and serving_routes | 19,904 |
| PART_OF | stops.stop_id → non-null parent_station | 2,222 |
| TRANSFER_RULE | transfers.from_stop_id → to_stop_id, preserving route/trip restrictions, type and minimum seconds | 61 |

For nodes, copy each property from its declared projection. Populate source_id and canonical_id directly from the same regional mapping row. For the metadata node, take source_snapshot_id, normalized_build_id and canonical_contract_version from the input manifest, schema_version from this schema, and construction_version from the producing software version. The expected fingerprint is computed over staged records using the definition below. Treat this value as an expected digest until an independent Phase 6 readback agrees.

For each edge file include start_id and end_id as exact canonical strings, plus only the properties declared for its type. Its filename implies the endpoint domains. The build manifest supplies lineage. For direct edges require exactly one mapping per typed source endpoint. For served-stop associations, check their published canonical endpoints against the mapping as well as the source IDs. Independently regroup routes → trips → stop_times by exact identifiers to reconcile pairs and count properties. Sum visits without deduplicating repeated stops. Require one operator per route and one route and service per trip. No label-specific heuristic may fill a missing mapping.

Use (type, start_id, end_id) as the logical edge key except for TRANSFER_RULE. Append from_route_id, to_route_id, from_trip_id and to_trip_id, in that order, for transfers. Null is a distinct typed component. Preserve self-transfers, parallel rules and restrictions. A route/trip restriction is the exact regional source ID, verified against the corresponding mapping. Rule type and minimum time are payload. Check source and target specificity before describing a transfer as permitted. A prohibited matching rule must never become an allowed connection. The current feed has types 0, 1 and 2. A future trip-linked transfer with omitted stop endpoints needs an external rule representation and schema review. Fail the current projection on unsupported rows and identify them individually.

## External timetable and exclusion contract

Use canonical_ids(entity_type, source_identifier) in the pinned build to bridge graph IDs to external keys. For trip visits join on trip_id and return one row per (trip_id, stop_sequence), ordered numerically. Keep original timing text, nullable seconds and flexible-location fields. For shape queries join Trip.shape_id to the shape mapping, then order shapes by shape_pt_sequence. All 2,446 shape mappings remain accessible. Resolve Service.source_id in calendar and calendar_dates. Route and agency membership have separate compact graph edges. Keep frequencies external when present. This build has no frequency file. If another build introduces location selectors, scoped transfers, extended route types or frequency behavior outside the validated projection, report the unsupported scope and re-evaluate before publishing.

Treat ledger dispositions separately. Exclude both WH:53 direction-label conflicts, preserve numeric trip directions, and leave the 68 fare variants external to accepted graph facts. Keep all 21 flagged shape points with their ledger references. The 42 redundant source rows have their retained-row lineage in Phase 3. External stop codes, names and operator candidates retain their Phase 3 status. To recover a direct fact, select its source row by the file and key in this contract, obtain source_record and the immutable source file checksum. For a derived edge, replay its join against the pinned input or inspect the accepted association and underlying trip/visit evidence.

## Fingerprint and acceptance

Use fingerprint format **transitkg-static-v1** in Phase 5 and independently in Phase 6. Serialize each record as a JSON array. A node is ["N",label,identity,properties]. A relationship is ["R",type,identity_tuple,properties], with the exact identity tuple from the schema, including type and endpoints. Properties include all declared fields in a UTF-8 lexicographically sorted object, with null for absent optionals. For the singleton FeedSnapshot omit only static_fingerprint from its properties to avoid self-reference. Include its other metadata. Use UTF-8, no BOM or Unicode normalization, no ASCII escaping of non-ASCII, JSON escaping as in Python json.dumps(ensure_ascii=False, sort_keys=True, separators=(',',':'), allow_nan=False), decimal integer tokens and no floats under this schema. Reject undeclared properties on the static projection, ambiguous labels, duplicate identities and invalid endpoint types.

Sort complete serialized records by unsigned UTF-8 bytes. Hash ASCII transitkg-static-v1 followed by LF, then every record followed by LF with SHA-256. The digest is lowercase hexadecimal. Hash only the declared static node labels and relationship types over static endpoints. Future event edges incident on static nodes are excluded. Counts, Neo4j internal IDs, load order, dynamic labels, operational timestamps and filesystem paths never substitute for the digest. Use external sorting if needed. Keep expected and readback digests separate in build history.

Fail publication on counts inconsistent with input, missing required properties, illegal categorical values, canonical collisions, unmapped endpoints, scope errors, transfer-key duplicates or broken provenance. Check classification, shape and service mappings, hierarchy, zero dangling references, 189 shared stops, all visit and extended-hour totals, AC/55660 exact resolution and same-name stop separation for this reference snapshot. Derive counts afresh on a new build and explain changes. Validate that the declared shape IDs in trips resolve. Re-run the phase suites and competency reference queries. The published Phase 3 and Phase 4 artifacts are inputs to this acceptance record.

## Phase 6 import interface

Favor driver transactions with parameterized UNWIND batches for a dedicated empty target. Stage nodes before relationships. Resolve endpoints by constrained canonical keys. Group direct edge writes by type and use endpoint-pair MERGE. For scoped transfer rules use one writer per endpoint pair and match all four restriction fields with null-safe equality before creating a missing rule. Reject a second match. Use a single writer per logical key, bounded transactions and a resumable batch checkpoint committed with its batch or safely replayed. Validate payload equality on retry. Publish the metadata and enable readers only after complete readback and fingerprint verification. Keep checkpoints and import metrics outside the static fingerprint.

Bulk neo4j-admin database import full is an alternative for a new offline database. It requires import-compatible ID groups and typed node/relationship headers or supported equivalent input, planned constraint setup and operational access. It is attractive for a large first load. Driver batches fit local Desktop, typed nullable properties and later small event transactions. Measure wall time, peak memory, store and index bytes and query behavior on equivalent builds before choosing a faster import path. Phase 5 should keep semantic Parquet outputs independent of either transport. [Neo4j bulk import](https://neo4j.com/docs/operations-manual/current/import/) · [Neo4j Python driver](https://neo4j.com/docs/python-manual/current/)

