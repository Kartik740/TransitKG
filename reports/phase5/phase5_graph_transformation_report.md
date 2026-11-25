# TransitKG Phase 5 · Graph-ready transformation

The regional build published **six node datasets with 115,683 rows and seven relationship datasets with 242,491 rows**. Every exported count matches the approved Phase 4 baseline. Reopened exports have **zero duplicate identities and zero dangling or mistyped endpoints**. The immutable publication contains **1,406,381 Parquet bytes** (1.34 MiB) plus a 16,494-byte manifest. [Published manifest](../../data/graph_ready/build-f85cdfafe998da9e7eecb669/manifest.json) · [Cardinalities and file sizes](graph_cardinality_report.json)

## Inputs and traceability

Reviewed the Phase 2 evidence pinned by the normalized manifest, Phase 3 cleaning, integrity and mapping evidence, and Phase 4 architecture comparison, schema, competency queries, validation evidence, prior fixture results and transformation contract. The input check verified the Phase 3 report manifest, exact inventories and SHA-256 for 27 raw and 32 normalized files, all 32 normalized schemas, pinned Phase 4 schema artifacts, snapshot identities and canonical contract compatibility. The build has 4,699,968 accepted normalized source records in 61,576,978 Parquet bytes. Both transformations rechecked the immutable files before publication. [Phase 3 canonical contract](../phase3/canonical_identifier_contract.md) · [Approved Phase 4 design](../phase4/phase4_schema_design_report.md) · [Phase 5 transformation contract](../phase4/phase5_transformation_contract.md)

| Schema or contract responsibility | Implemented projection and independent check |
|---|---|
| Agency, Route, Trip and Stop | Typed canonical mapping join plus the approved source properties. Compare complete ID and payload multisets back to both input domains after export. |
| Service | Copy the complete service mapping. Reconcile against the union of weekly services and exception additions. |
| FeedSnapshot | One pinned manifest row with source/build/schema/contract versions and the expected digest. Compare all six exported fields and recompute the digest. |
| OPERATES, HAS_TRIP and RUNS_ON | Translate directed source references through canonical mappings. Decode exports through their exact endpoint labels and compare all pairs and mandatory multiplicities. |
| ROUTE_SERVES_STOP and AGENCY_SERVES_STOP | Use the approved Phase 3 associations for export. Independently aggregate the timetable by route and operator and reconcile complete pairs, visit counts and distinct trip/route counts. |
| PART_OF | Translate child→parent references. Reconcile exact source pairs, single-parent scope and location-type compatibility. |
| TRANSFER_RULE | Translate stop endpoints, preserving all approved properties. Reconcile the full source multiset and verify that route/trip qualifiers resolve. |
| Provenance, fingerprint and publication | Schema definitions and source keys recover raw source_record values through the normalized manifest. Record per-file SHA-256 and logical digests. Publish through an atomic staging-directory rename and verify artifacts before reusing an existing build. |

DuckDB performs joins, type projection and Parquet writes. A separate validation module reads reopened Parquet and translates canonical endpoints back to source IDs for full-set comparisons. SHA-256 serialization uses the approved JSON representation and unsigned UTF-8 ordering. Per-label/type sorting bounds Python storage to one artifact at a time, since the record tag and label/type precede the identity and payload. The digest includes the snapshot metadata except its self-referential fingerprint field. [Transformer](../../scripts/transform_static_graph.py) · [Independent validation](../../scripts/validate_graph_ready.py) · [Fingerprint record](graph_fingerprint_report.json)

## Identity, exclusions and timetable

The graph files account for **115,682** canonical entity IDs plus the metadata node. The other **2,446** identities are shapes in the pinned external build, accounting for all **118,128** mappings. Exact source identifiers and their canonical bytes are preserved, including **364 stop suffix collision groups** and **189 shared stops**. Stop coordinates remain accessible through the pinned stops table by source ID, as specified by the approved Stop property list.

All 61 transfers match their accepted source rows. Types 0, 1 and 2 have 3, 10 and 48 rules respectively. Forty-four have both route qualifiers. The regional build has zero trip-scoped and zero prohibited transfers. A synthetic test verified that a prohibited type and distinct nullable qualifiers survive validation. The two quarantined direction labels and 68 quarantined fare-transfer variants remain excluded. Twenty-four WH:53 trip templates retain numeric direction 0. All 21 flagged shape points remain externally accessible. The ledger also accounts for 42 deduplicated rows. [Integrity evidence](graph_integrity_report.json)

The pinned build retains **3,185,115** ordered visits, including repeated stops on **4,221** trips, **100,912** arrivals and **100,948** departures beyond 24:00:00, plus **1,340,496** shape points. Five calendar rows end in 2099 and 5,706 date exceptions remain available. The regional build has no frequency table. Source timing text, service-day seconds, calendar precedence and agency timezones come from the same compatible snapshot. Exact trip/stop and date predicates are evaluated through those external tables.

## AC Transit integration

The exported agency→stop edge and the agency→route→trip path joined to accepted visits establish **EXACT_MATCH** for AC/55660, canonical ID **511-sf-bay-regional:stop:55660**. Visiting trip templates total 345 over the snapshot, with 126 on AC:72, 81 on AC:72L, 123 on AC:72M and 15 on AC:802. The same query with service-date qualification gives **112** on 2026-11-25.

Stop 52227 retains a separate canonical ID and 339 visiting templates. Both have the name “San Pablo Av & W Grand Av,” so name-only lookup is ambiguous. The wrong agency and a contradictory direction returned zero. The regression measures scheduled scope for the future selector. [Scoped query and validation](../../scripts/validate_graph_ready.py)

## Fingerprint, reproducibility and resources

- Build ID **graph-ready-sha256-f85cdfafe998da9e7eecb6697443ebd02e45f4d43e41462db7a8a5acb82dadae**.
- Expected static SHA-256 **675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66**.
- A fresh-process repeat re-exported, reopened and validated all 13 datasets. It matched the build ID, static digest and per-artifact logical digests before reusing the verified publication. Physical file hashes attest to the specific published Parquet bytes.
- Python 3.14.0 and DuckDB 1.5.0 on Windows, with two DuckDB threads and a 768 MB DuckDB memory budget. First run took **11.747 s wall and 14.766 s CPU**, with **806.62 MiB** peak process working set. The repeat took **10.191 s wall and 13.125 s CPU**, with **806.60 MiB** peak working set.
- On the first run node export took **1.014 s**, relationship export **0.308 s**, reopened validation **5.832 s**, and the two fingerprint passes **3.548 s**. The sampled DuckDB spill peak was **283,705,344 bytes** at 25 ms intervals. Adding staged Parquet gives a sampled temporary-payload upper bound of **285,111,725 bytes**. The repeat bound was **317,191,597 bytes**. Wall and CPU timings are measured through manifest preparation before final publication/reuse checks. [Published execution metrics](../../data/graph_ready/build-f85cdfafe998da9e7eecb669/manifest.json) · [Repeat evidence](graph_fingerprint_report.json)

## Acceptance and Phase 6

Phase 2 passed 7/7, Phase 3 passed 10/10, Phase 4 passed 13/13 and Phase 5 passed 8/8. The offline run had zero failures, errors or skips. Neo4j connectivity and import playback were outside this offline run. The prior live server and fixture evidence remains in Phase 4. Imported fingerprint, server storage and regional load performance are pending Phase 6 construction.

Implementation choices included Zstandard Parquet compression, BIGINT for signed graph integers, two DuckDB threads, artifact-partitioned fingerprint sorting and a 24-hex directory prefix backed by full build-identity verification. The Phase 6 handoff recommends bounded driver transactions on a dedicated, empty, explicitly designated database. No user action is needed for Phase 5 delivery. [Phase 6 import contract](phase6_import_contract.md)

From the workspace root in PowerShell, use the existing dependency folder or a virtual environment with scripts/requirements-phase2.txt installed.

~~~powershell
$env:PYTHONPATH = '.phase2-deps'
$env:TRANSITKG_PHASE3_BUILD = (Resolve-Path 'data/normalized/build-5608c0d7d93b9c6200431b5c').Path
python scripts/transform_static_graph.py --repeat
python -m unittest discover -s scripts -p 'test_*.py'
~~~

