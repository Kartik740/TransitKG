# Phase 7 validation contract

Use the validated Phase 6 construction manifest to pin the imported baseline. Repeat the database readback before running benchmarks. The regional import, fingerprint and competency checks passed. [Phase 6 construction manifest](static_graph_manifest.json)

| Input | Required value or location |
|---|---|
| Verified target | Local Neo4j Enterprise 2026.10.2 at 127.0.0.1:7687, dedicated transitkg-static database |
| Graph-ready build | graph-ready-sha256-f85cdfafe998da9e7eecb6697443ebd02e45f4d43e41462db7a8a5acb82dadae |
| Expected static fingerprint | 675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66 |
| Imported fingerprint | 675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66 |
| Cardinalities | 115,683 nodes, 242,491 relationships, with per-label/type counts from the graph-ready manifest |
| Normalized timetable | normalized-sha256-5608c0d7d93b9c6200431b5c227da598d52cd0108e9e2f410e5b012b00d28052 |
| Approved schema | Static schema 1.0.0 and canonical ID contract 1.0.0 |

Run the full static verifier before and after read-only benchmarks. Check global counts, full typed record digests, nullable transfer qualifiers, exact source identifiers, absence of quarantined facts, expected indexes and the static snapshot gate. Reuse the 14 Cypher competency contracts and the regional hybrid regression. Test shared-stop scope, both same-name stops, rejected agency/direction combinations, service availability and extended hours. Keep ordered visits and date-aware service queries in the pinned timetable. Record query plan shape, result cardinality, warm-up method, measured repetitions, server/driver versions, cache conditions and resource limits. Run separate, repeatable measurements for graph-only lookup, DuckDB-only timetable query and end-to-end hybrid scope. Measure server-side and client-side resources separately.

Use the Phase 6 manifest's per-type timing and batch evidence as the construction baseline. Phase 6 took 240.146 seconds wall time and committed 187 batches with zero retries. The store size was an immediate file-length sample excluding transaction logs. Record stable post-checkpoint storage and server resource metrics if those measurements are available. Use isolated data for future write benchmarks with explicit approval. A readback mismatch or changed snapshot blocks the benchmark. [Phase 4 competency questions](../phase4/competency_questions.md) · [Cypher](../../schema/competency.cypher) · [Timetable SQL](../../schema/timetable.sql) · [Graph-ready manifest](../../data/graph_ready/build-f85cdfafe998da9e7eecb669/manifest.json)

