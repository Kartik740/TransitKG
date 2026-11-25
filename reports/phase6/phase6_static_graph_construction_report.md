# TransitKG Phase 6 · Static graph construction

**Status: validated.** The dedicated **transitkg-static** database on local Neo4j Enterprise **2026.10.2** contains **115,683 nodes** and **242,491 relationships**. An independent readback through driver **6.4.1** reproduced the exact expected fingerprint and every per-type digest. All 14 regional Cypher contracts passed against independent normalized SQL references. [Construction manifest](static_graph_manifest.json) · [Authenticated target preflight](live_preflight.json)

The user confirmed credential rotation and approved creation of the dedicated target. Authentication used the encrypted local handoff. The original create command succeeded, but the importer stopped when the command returned no status row. A separate authenticated check showed the target was online and empty. The importer now consumes the command response and verifies creation in the catalog. It loaded the existing dedicated target without a second create. [Creation attempt](database_creation_attempt.json)

## Inputs and expected graph

Inspected the Phase 3 canonical identifier contract and immutable publication, Phase 4 schema, approved DDL, fingerprint specification and competency queries, and the Phase 5 transformation report, import contract, checksums, manifest and validator. The offline preflight reopened every graph-ready dataset, checked schemas and types, compared source and output records, and reconciled route and agency associations against the normalized timetable. It checked all 27 raw files, 32 normalized files and 13 graph-ready files, and recomputed every logical artifact digest and the expected fingerprint. [Graph-ready manifest](../../data/graph_ready/build-f85cdfafe998da9e7eecb669/manifest.json) · [Import contract](../phase5/phase6_import_contract.md)

| Node label | Expected = actual | Relationship type | Expected = actual |
|---|---:|---|---:|
| Agency | 41 | OPERATES | 665 |
| Route | 665 | HAS_TRIP | 92,827 |
| Trip | 92,827 | RUNS_ON | 92,827 |
| Stop | 21,664 | ROUTE_SERVES_STOP | 33,985 |
| Service | 485 | AGENCY_SERVES_STOP | 19,904 |
| FeedSnapshot | 1 | PART_OF | 2,222 |
| | | TRANSFER_RULE | 61 |
| **Total** | **115,683** | **Total** | **242,491** |

Expected and imported SHA-256 both equal **675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66**. Readback found zero duplicate identities and zero incorrect endpoints. Every label, type, property representation, payload, canonical ID and directed endpoint matched the pinned build. The six uniqueness constraints, seven lookup indexes and six constraint-backed indexes matched the named definitions. All required indexes were online. The catalog also contained the two default token lookup indexes.

The immutable input sets and hashes passed again after construction. The graph and external build accounted for all 118,128 canonical mappings, including 2,446 external shape identities and 364 preserved stop suffix collision groups. External access to 3,185,115 visits, 1,340,496 shape points, extended-hour times and five calendar rows ending in 2099 remained verified. Complete record comparison preserved all 61 transfer rules, including 44 with both route qualifiers. The pinned quarantine ledger remains external. [Construction manifest and verification](static_graph_manifest.json)

## Regional competency and scope checks

All 14 approved Cypher queries ran in the regional target and matched normalized SQL results. The checks covered exact canonical lookup, ownership-independent agency service, route memberships, trip services, hierarchy, transfers, 189 shared stops, directional scope and the snapshot gate. The timetable lookup also returned the 77 ordered visits for the reference trip and 125 active services on the regression date.

AC/55660 resolved as **EXACT_MATCH** to **511-sf-bay-regional:stop:55660** using the imported agency, stop, route, trip and service paths with accepted timetable visits. It returned **345 trips** across AC:72, AC:72L, AC:72M and AC:802. **112** operate on the fixed service date **2026-11-25**. Stop 52227 resolved to its distinct canonical ID and returned **339** visiting trip templates. Same-name lookup returned both records. A contradictory agency and direction each returned zero matches. The results describe potentially affected scheduled service. [Regional query outcomes](static_graph_manifest.json)

## Import and safety

The importer uses the verified Phase 4 DDL, with a catalog comparison before any write and after all indexes come online. It loads the five entity labels first, all seven directed edge types next and FeedSnapshot after verifying the staged graph. The live reader checks all node labels, exact declared properties, typed endpoints, scoped transfer identities, unexpected content, global counts and per-type digests. It independently recomputes the approved fingerprint from Neo4j records, compares the exact hash, and runs the 14 regional Cypher contracts and the hybrid timetable regression. Graph and external canonical IDs stay linked to the approved normalized snapshot.

The initial configuration is sequential 2,000-row node and edge transactions, a 180-second transaction timeout, and at most two transient retries. Every batch resolves existing identities and compares exact payloads before creating missing rows. Typed endpoints and processed row counts are verified inside each transaction. The four transfer qualifiers retain null-safe identity equality, while absent optional properties are omitted and reconstructed as JSON null during readback. The build preserves the regional type and minimum-time payloads, including 44 route-scoped rules. Prohibited transfer type preservation was tested with a fixture. [Importer](../../scripts/import_static_graph.py) · [Independent readback](../../scripts/verify_imported_graph.py)

| Safety gate | Behavior |
|---|---|
| Target and approval | Require explicit local dedicated database name and write flag. The separate create flag permits only creating a missing, approved target. Reject default, system, remote or differently configured targets. |
| Existing data | Verify a valid completed build before a no-op. A partial graph requires a checkpoint bound to the database identity and exact subset validation before more writes. Reject unrelated, changed or unidentified content. |
| Interruption and retry | A local process lock serializes importer runs. Exact identity and payload reconciliation resolves uncertain commit outcomes. Store batch attempts, committed counts and sanitized failures in per-attempt evidence. |
| Reader isolation | Use a dedicated target and withhold client routing until a validated construction manifest exists. Coordinate exclusive write access while importing and verifying. Existing server access permissions still govern direct database connections. |
| Success evidence | Publish the construction manifest only after full live readback, competency queries and immutable input rechecks. Preserve an earlier validated manifest and keep separate attempt results. |

## Construction performance

| Measure | Observed |
|---|---:|
| End-to-end wall time | 240.146 s |
| Input verification | 5.739 s |
| Schema application and index readiness | 0.943 s |
| Main data load before metadata | 117.499 s |
| Node import, including metadata | 43.597 s and 2,653.46 nodes/s |
| Relationship import | 73.258 s and 3,310.10 relationships/s |
| Staged graph readback before metadata | 53.659 s |
| Full Neo4j fingerprint and record readback | 57.083 s |
| Regional competency checks | 4.034 s |
| Batch transactions | 187 committed, zero failures and zero retries |
| Client CPU and peak working set | 150.516 s, 470.80 MiB |
| Immediate local store and index files | 1,443,407 bytes including 614,400 index bytes |

The import ran sequentially with 2,000-row batches, a 180-second transaction timeout and at most two transient retries. Timings and working set are client-side measurements. Per-label, per-type and per-batch measurements are in the manifest. Store bytes are file lengths immediately after verification, excluding transaction logs. No forced checkpoint was performed, so the sample may precede a completed flush. Steady-state disk usage and server CPU and memory were not measured. [Construction manifest](static_graph_manifest.json) · [Neo4j driver transactions](https://neo4j.com/docs/python-manual/current/transactions/) · [Database catalog fields](https://neo4j.com/docs/operations-manual/current/database-administration/standard-databases/listing-databases/)

## Tests and reproduction

Phase 2 passed 7/7, Phase 3 passed 10/10, Phase 4 passed 13/13, Phase 5 passed 8/8 and Phase 6 passed 12/12 offline. Phase 6 fixtures cover targeting, input corruption, schema conflicts, type semantics, scoped transfer replay, missing endpoints, uncertain commit handling, unchanged payloads under replay, partial recovery, same-count content changes and failed-report publication. Live schema, import, full readback and competency checks passed. Planned live interruption was left out of the regional load. The synthetic recovery tests covered interruption and uncertain commit handling. [Test evidence](automated_test_report.json)

A rerun verified the exact existing target in **71.238 seconds**. It issued **zero write batches**, reproduced all counts and the fingerprint, and preserved the construction manifest byte for byte. [Replay evidence](last_database_attempt.json)

From the workspace root in PowerShell:

~~~powershell
$env:PYTHONPATH = '.phase2-deps'
$env:TRANSITKG_PHASE3_BUILD = (Resolve-Path 'data/normalized/build-5608c0d7d93b9c6200431b5c').Path
python scripts/import_static_graph.py --offline
python -m unittest discover -s scripts -p 'test_*.py'
~~~

With credentials available through the local secure mechanism, run read-only target verification. For a future import on a missing and explicitly approved target, use the create flag. For the verified target the write-approved path performs validation and exits without batches. The planned pause is reserved for an authorized test on an empty target.

~~~powershell
python scripts/import_static_graph.py --database transitkg-static --preflight
python scripts/import_static_graph.py --database transitkg-static --write-approved --create
python scripts/import_static_graph.py --database transitkg-static --verify
~~~

## Browser and Phase 7 handoff

Open Neo4j Desktop, select the local instance and open its query interface. Choose **transitkg-static** with the database selector or use `:use transitkg-static` in Neo4j Browser. Run one bounded statement at a time from the visualization file. Use the Graph view for paths and the Table view for transfer and snapshot properties. All ten statements ran on the regional graph and returned between 1 and 25 rows each. Route membership edges show scheduled associations. Ordered trip visits and dated services require the pinned DuckDB timetable query. [Visualization queries](visualization_queries.cypher) · [Visualization execution](visualization_validation.json) · [Phase 7 validation contract](phase7_validation_contract.md)

