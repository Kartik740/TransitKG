# TransitKG Phase 4 · Static knowledge graph architecture

**Decision for review, 25 November 2026.** Adopt a compact Neo4j property graph for canonical identity and scheduled connectivity. Keep the immutable Phase 3 Parquet build authoritative for timetable, service availability and geometry. The proposed regional baseline has **115,683 nodes and 242,491 relationships**. The AC/55660 selector resolves exactly. Its four routes contain 345 scheduled trip templates visiting that stop, of which 112 operate on the example service date 2026-11-25.

## Evidence and compatibility

Inspected the Phase 2 file inventory, profiles, key and reference reports, identifier and real-time findings, temporal/network analysis and summary. Inspected the Phase 3 cleaning policy, canonical contract, manifest, mapping checks, integrity checks, compatibility regression, normalization accounting and report. The exact paths and digests of all 16 artifacts are in the validation record. Rechecked the complete sets and SHA-256 of **27 raw files and 32 normalized Parquet files**, the published manifest against its report copy, and the eight Phase 2 evidence digests pinned in that manifest. The normalized publication contains 4,699,968 accepted source rows and occupies 61,576,978 Parquet bytes. [Validation record](schema_validation_report.json) · [Phase 3 manifest](../phase3/dataset_manifest.json)

| Pinned contract | Value |
|---|---|
| Raw snapshot | gtfs-sha256-d6279860e2fe203541a574ee04db5752ecad9e5e1a38b1759cc397b1b596dfa2 |
| Normalized build | normalized-sha256-5608c0d7d93b9c6200431b5c227da598d52cd0108e9e2f410e5b012b00d28052 |
| Canonical namespace and contract | 511-sf-bay-regional, version 1.0.0 |
| Proposed static schema | 1.0.0, one regional baseline per graph database |

**Evidence discrepancy.** The Phase 3 summary row says two calendars end on 2099-12-31. Its detailed service list names five. A fresh count of calendar rows whose end year is 2099 returned five and agrees with that list. Use their actual per-service bounds. The provider supplied no regional feed validity dates. [Phase 3 narrative](../phase3/phase3_data_cleaning_report.md) · [Measured cardinalities and references](architecture_comparison.json)

## Database and architecture decision

The research workload begins with exact entity grounding, then follows relationships between a selector, an operator, routes, stops and eventually evidence-bearing events. Neo4j provides directed typed edges with properties, indexed equality lookups, transactional writes, Cypher and a local Python driver. DuckDB already answers detailed temporal, grouping and analytical questions over the normalized files. A purely relational system could answer all current static questions with joins and would minimize operational complexity. The property graph adds an explicit event-to-scope-to-entity traversal and a surface for testing future graph updates. RDF would offer portable predicates and linked-data interchange, but representing scoped transfers, ordered visits, event evidence and snapshot context would require additional modeling without a current interchange or reasoning requirement. [Neo4j Cypher constraints](https://neo4j.com/docs/cypher-manual/current/schema/constraints/) · [DuckDB Parquet access](https://duckdb.org/docs/stable/data/parquet/overview)

**Live verification.** The supplied local Desktop instance authenticated at neo4j://127.0.0.1:7687. The driver read database neo4j, executed RETURN 1 and inspected the server components. The server reported Neo4j Enterprise 2026.10.2 and Cypher 5 and 25. Driver version was 6.4.1. The static schema targets community-compatible property uniqueness and range indexes. Enterprise multi-database support was used only for the authorized isolated verification. Community deployment would need a separate disposable instance for the same write test. Licensing and production deployment remain deployment choices. [Sanitized live results](neo4j_verification.json) · [Neo4j Python driver](https://neo4j.com/docs/python-manual/current/)

### Same-snapshot alternatives

Counts include identical core entities, metadata and compact associations in all alternatives so each figure shows the incremental cost. A and C below are evaluated designs. Only the compact schema was exercised as DDL on the fixture.

| Physical design | Nodes | Relationships | Native graph operation | Additional cost and remaining external work |
|---|---:|---:|---|---|
| A with one trip→stop edge per occurrence | 115,683 | 3,427,606 | Retrieve a trip’s visits, sort by sequence and read time properties | 3,185,115 more edge identities and temporal payloads. Calendar logic still needed. |
| A with one node per occurrence | 3,300,798 | 6,612,721 | Address a visit directly through Trip→Visit→Stop | Two edges per occurrence. Additional writes, index/store overhead and duplicate timetable payload to reconcile. |
| **B compact hybrid** | **115,683** | **242,491** | Resolve identities, route/agency membership, trip templates, parents and scoped transfer rules | Exact trip-stop and date predicates cross to the pinned Parquet layer. |
| C with route patterns | 117,608 | 388,486 | Trip→Pattern→Stop, ordered by occurrence ordinal | Adds 1,925 patterns, 92,827 trip links and 53,168 ordered occurrence edges. Times and boarding restrictions still require visit rows. |

DuckDB grouped exact ordered stop-ID lists within (route_id, direction_id), retaining null direction as a separate group. Every trip has stop visits in this build. Pattern identity for this comparison describes topology only. Ordered repeated IDs preserve loops, short turns and variants. There are **1,713 patterns shared by 92,615 trips**, 212 singleton patterns, and up to 586 trips per pattern. Expanding occurrences by trip multiplicity recovers 3,185,115 visits. The aggregation took 22.288 seconds with two DuckDB threads. The original numeric sequence values, timing, pickup and drop-off rules and shape remain trip-specific. A future pattern projection would use ordinal edges, preserve trip identity and continue to resolve dates on individual trips. [Architecture experiment](architecture_comparison.json)

**Inference.** B satisfies the grounding workload at the smallest measured projection. C would become attractive if ordered native traversal dominates real queries. Neither C nor A establishes which dated trips are operating without service evaluation. Extra writes and duplicated payload imply construction and maintenance costs for A and C. Their production load times, store bytes and speedups have not been measured. Preserving a separate event layer lets dynamic updates append scoped assertions independently of the static topology in all three designs.

## Static ontology and physical dictionary

| Label | Identity, meaning and graph properties | Count |
|---|---|---:|
| Agency | Exact operator mapping; name and IANA timezone | 41 |
| Route | Exact route mapping; numeric type and optional short and long names | 665 |
| Trip | Exact scheduled trip definition; optional numeric direction, headsign and exact source shape ID | 92,827 |
| Stop | Exact GTFS location; optional name, code, platform code and numeric location type | 21,664 |
| Service | Exact service-domain mapping; links a trip template to its weekly/exception rules | 485 |
| FeedSnapshot | Unique normalized build ID; source snapshot, schema, canonical contract, construction version and static digest | 1 |

One Stop label covers 19,795 stops/platforms (type 0), 613 stations (1), 250 entrances/exits (2) and 1,006 generic nodes (3). A type-4 boarding area in a compatible build would use the same label. The dataset contains route types 0 through 5. A graph Stop can exist without a scheduled visit. Names and stop codes can supply search candidates. They never assign canonical identity.

| Relationship | Meaning and multiplicity | Count |
|---|---|---:|
| Agency OPERATES Route | Each route has one published operator | 665 |
| Route HAS_TRIP Trip | Each trip belongs to one route | 92,827 |
| Trip RUNS_ON Service | Each template has one service definition | 92,827 |
| Route ROUTE_SERVES_STOP Stop | Distinct route/stop membership, with visits and distinct trips | 33,985 |
| Agency AGENCY_SERVES_STOP Stop | Distinct operator/stop membership, with visits and distinct routes | 19,904 |
| Stop PART_OF Stop | Child to published parent, zero or one outgoing parent | 2,222 |
| Stop TRANSFER_RULE Stop | Directed transfer with its full applicability and minimum time | 61 |

Route and agency memberships are derived. All other edges project explicit source relationships. There are 189 shared stops. An agency association records scheduled service only. Of 61 rules, 3 have type 0, 10 type 1 and 48 type 2. Forty-four include both route restrictions. The schema retains both trip restrictions when populated. Endpoint pairs alone lose transfer semantics. Adjacency is available in ordered external visit rows. The schema has no global stop-order edge. [Machine-readable dictionary](../../schema/static_graph_schema.json) · [Generated conceptual diagram](../../schema/static_graph_schema.mmd) · [Exact joins, keys, provenance and validation](phase5_transformation_contract.md)

### External allocation and canonical identity

Copy the exact Phase 3 mappings, including case, leading zeros, literal percent signs and provider prefixes. For example, AC:72 maps to 511-sf-bay-regional:route:AC%3A72. All **118,128** mappings round-trip. The five graph entity families account for 115,682 keys. The 2,446 shape keys stay in Parquet. Metadata adds one graph node. Prefix stripping would conflate 364 stop-suffix groups. Names alone would conflate distinct stops. An external operator crosswalk requires dated authoritative evidence. [Canonical ID contract](../phase3/canonical_identifier_contract.md) · [Mapping evidence](../phase3/mapping_validation.json)

| External fact | Access and reason |
|---|---|
| 3,185,115 timetable visits | Graph canonical trip → exact source ID → ordered (trip_id, stop_sequence). Repeated stops and different times remain separate. |
| 1,340,496 shape points | Trip.shape_id → shape mapping → shapes by point sequence. Optional Shape nodes and FOLLOWS_SHAPE would add 2,446 nodes and 91,296 edges. Geometry remains external either way. |
| 485 weekly rows and 5,706 exceptions | Service.source_id → calendar/calendar_dates. One ServiceException per row would add 5,706 nodes and 5,706 service edges. Date filtering is already a tabular operation. |
| Frequency service | No frequency file in this snapshot. In a supporting build use trip, time window, headway and exact_times from normalized frequencies. Resolve only when instance semantics are supported. |
| Fares, pathways, levels, provider attributes and alias candidates | Available through their accepted tables with lineage. Limited relevance to current selectors; no independent graph labels proposed. Direction-label conflicts and fare-filter interpretations remain unresolved in the ledger. |

Graph reads start with the single FeedSnapshot. Compare graph source snapshot, normalized build, graph schema and canonical contract to the selected Parquet/schema manifests. Pin the immutable graph baseline and tabular build through a hybrid query. Reject a missing or extra metadata row, mismatch or unverified mapping. Use transient DuckDB views for detail. Recover raw evidence through manifest file checksums and record ordinals. Archive old baseline metadata and binding history when later adopting a new snapshot. Future incremental static changes should diff stable typed identities and payloads in staged snapshots, validate changes, and explicitly re-evaluate outstanding event bindings before switching reader versions. Dynamic ingestion always records the baseline used.

## Service time and future alerts

Keep original H:MM:SS/HH:MM:SS text and nullable service-day seconds. A trip template can operate on multiple dates. Evaluate a supplied service date using its explicit exception first, otherwise its inclusive weekly bounds and weekday flag. This also handles services defined only by additions. Keep the agency timezone. GTFS defines a service-day time origin relative to local noon minus 12 hours, which matters across daylight-saving transitions. For instant conversion anchor the agency-local service date accordingly, then apply elapsed service-day seconds. Retain the original service date even when the wall clock crosses midnight. The dataset contains 100,912 arrivals and 100,948 departures above 86,400 seconds. Two separate counts of 15,109 missing arrivals and departures retain their source semantics. Date ranges through 2099 stay compact. [GTFS Schedule reference](https://gtfs.org/documentation/schedule/reference/) · [Timetable query definitions](../../schema/timetable.sql)

Future event design is provisional. One AlertObservation captures feed generation time, receipt/observation time, source alert identity, payload and evidence. Link it to a ServiceAlert and its described disruption, and retain one AffectedScope per informed_entity ordinal. Record original fields and bind resolved canonical targets with the verified snapshot. Separate selectors express independently specified scopes. Inside each selector, all predicates must hold on the same compatible operator/route/trip/stop path. Direction requires a route. RouteType and direction constrain the same route and trip as StopId. Check dated trip start semantics against calendars, first departure and frequency rules. Unsupported real-time-only trips, conflicting selectors and uncertain external aliases retain an unresolved status. Event-owned links permit incremental supersession without altering scheduled nodes, edges or timetable payloads. [GTFS-Realtime reference](https://gtfs.org/documentation/realtime/reference/)

**Sample closure.** The AC/55660 selector binds to 511-sf-bay-regional:stop:55660. The source routes are AC:72, AC:72L, AC:72M and AC:802. Stop 52227 has the same displayed name and four route IDs, a distinct canonical key, and 339 templates. Preserve cause 9, effect 1, header, stop-optimization explanation and “immediately until further notice” text as observation evidence. The supplied sample has no explicit ActivePeriod. Display follows feed presence; actual disruption endpoints remain unknown. Feed generation, observation, actual start/end and graph ingestion are separate timestamps. The routes and templates are potentially within the closure’s scope. A cancellation would require separate evidence. [Competency questions and expected answers](competency_questions.md) · [Service Alerts specification](https://gtfs.org/documentation/realtime/feed-entities/service-alerts/)

## Constraints, provenance and verification

Install six property uniqueness constraints, one per selected label’s identity. Seven additional range indexes cover the five source_id lookups plus exact Stop.stop_name and Route.route_short_name. The fixture reported 15 online indexes including the six constraint-backed and two default token lookup indexes. Single-build typed source IDs resolve uniquely in the Phase 3 mapping. Global canonical uniqueness, required fields, property types, endpoint labels, transfer-key null semantics, degree and edge uniqueness are also checked in application logic. Separate relationship-property uniqueness would require copying a surrogate key onto every edge. A composite agency/stop index cannot enforce an association across nodes. Use the typed indexed starting node and the actual edge. Route type/direction have low selectivity on this data. A text index can be evaluated later if fuzzy candidate search becomes a measured requirement. [Generated DDL](../../schema/neo4j_schema.cypher) · [Neo4j constraints](https://neo4j.com/docs/cypher-manual/current/schema/constraints/)

The Phase 5 contract defines the static digest over sorted, typed node/edge records, direction, qualifying fields, properties and metadata, with explicit exclusions to avoid a self-referential digest or contamination from event updates. The future loader must independently compare the imported graph to the staged digest. This phase measured cardinalities and verified projections; there is no production graph fingerprint yet. [Fingerprint and publication contract](phase5_transformation_contract.md)

| Verification | Observed result |
|---|---|
| Phase 2, 3 and 4 suites | 7/7, 10/10 and 13/13 passed respectively. Zero failures, errors or skips. Includes full-build association reconciliation, negative snapshot cases and dated selectors. |
| Neo4j isolation | User authorized a unique disposable database. Applied the DDL, checked schema catalog names and online status, rejected duplicate canonical identity, executed 14 query contracts, removed the database and confirmed absence. Existing database reads included 14 query plans. |
| DuckDB full-build lookups | Five warm samples, median 1.884 ms for AC/55660 association, 26.236 ms for its distinct trip count and 27.634 ms for the date-scoped count. One process, two threads, direct Parquet. |
| Neo4j synthetic fixture | Five warm local driver round trips for the scoped lookup, median 11.929 ms. Includes client/driver cost. Different data and query workloads preclude a speed comparison with regional DuckDB. |
| Deferred measurements | Regional Neo4j import cost, store and index bytes, production query timings, dynamic update behavior and readback fingerprint require later data and construction phases. No live connection or permission blocker remains. |

[Full timing samples](architecture_comparison.json) · [Server and cleanup evidence](neo4j_verification.json) · [Test report](schema_validation_report.json)

## Decision traceability

| Evidence | Interpretation | Proposed action |
|---|---|---|
| Exact schedule mappings, 364 suffix collisions, two identical names | Text transformations can collapse true identities | Require Phase 3 keys and maintain a separate candidate stage. |
| 33,985 route pairs, 19,904 agency pairs, 189 shared stops | Membership is compact and many-to-many | Materialize both associations and preserve a single Stop per mapping. |
| 3.19M visits, 4,221 trips repeating stops, measured scoped SQL | Timetable detail is authoritative and affordable to query externally | Adopt B; reconsider C if ordered graph traversal becomes a workload bottleneck. |
| 44 route-scoped transfers, 61 total | Endpoint deduplication loses restrictions | Retain the full directed rule key and property payload. |
| Extended hours, 5,706 exceptions and five very long bounds | Ordinary clock arithmetic and date expansion misrepresent service | Store service identities and evaluate the verified calendar and timetable externally. |
| 2 quarantined direction labels, 68 fare variants, 21 retained flagged shape points on 13 shapes | Schedule and optional attribute quality differ | Retain numeric directions and geometry; keep unresolved labels/fares auditable in the Phase 3 ledger. |
| Four-field version gate and future scoped observations | Matching identifiers alone cannot prove snapshot compatibility | Pin both layers, preserve observation history, and use a static-only fingerprint. |
| Successful DDL, uniqueness rejection and fixture queries on the installed server | The proposed features work in this environment | Use the verified property-graph schema and Python driver; benchmark production performance later. |

## Reproduction and handoff

From the workspace root, use the installed project dependencies. On a fresh checkout create a virtual environment, install scripts/requirements-phase4.txt, and run the scripts with that environment’s Python. The verification suite reuses the Phase 3 build on disk. The regional regression tests are pinned to this reference dataset.

~~~powershell
$env:PYTHONPATH = '.phase2-deps'
$build = 'data/normalized/build-5608c0d7d93b9c6200431b5c'
$env:TRANSITKG_PHASE3_BUILD = (Resolve-Path $build).Path
python scripts/design_static_graph.py $build --raw GTFSTransitData_RG
python scripts/test_static_graph.py
~~~

For the optional server checks, set NEO4J_URI, NEO4J_USERNAME, NEO4J_DATABASE and NEO4J_PASSWORD via a local credential mechanism. The probe defaults to the local Desktop URI, username neo4j and the user’s home database. In PowerShell the following prompt keeps the password out of command history and terminal output. Run the second probe mode only for explicitly authorized disposable-database testing.

~~~powershell
$secret = Read-Host 'Neo4j password' -AsSecureString
$env:NEO4J_PASSWORD = [System.Net.NetworkCredential]::new('', $secret).Password
python scripts/verify_phase4_neo4j.py --fixture
Remove-Item Env:NEO4J_PASSWORD
~~~

Omit --fixture for a read-only connection and plan check. Each run records the verification mode it actually executed. Generated diagrams and DDL derive from the machine-readable schema. Access date for the linked official documents is **2026-11-25**. Phase 3 records its Schedule field basis as the 2026-04-27 revision. Use the pinned Phase 3 projection when interpreting the measured build.

**Phase 5 review decision.** Approve or revise the compact projection, single-build publication boundary, directed transfer key, fingerprint format and typed Parquet handoff. Then stage and validate graph-ready datasets under the contract. No manual assistance is pending. Open research questions concern authoritative operator crosswalks, fare-filter precedence, WH:53 labels, flagged shape distances and the production workload that would justify route patterns. [Phase 5 transformation contract](phase5_transformation_contract.md)

