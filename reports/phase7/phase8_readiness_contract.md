# Phase 8 static baseline readiness

| Boundary | Verified value |
|---|---|
| Database | transitkg-static, Neo4j Enterprise 2026.10.2, local Bolt port 7687 |
| Database identity | FEC14225142CED808735D9B68454D6C9ABE2B5CCBD95B8B5074B0331DA905445 |
| Source snapshot | gtfs-sha256-d6279860e2fe203541a574ee04db5752ecad9e5e1a38b1759cc397b1b596dfa2 |
| Normalized build | normalized-sha256-5608c0d7d93b9c6200431b5c227da598d52cd0108e9e2f410e5b012b00d28052 |
| Graph-ready build | graph-ready-sha256-f85cdfafe998da9e7eecb6697443ebd02e45f4d43e41462db7a8a5acb82dadae |
| Static SHA-256 | 675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66 |
| Cardinalities | 115,683 nodes and 242,491 relationships |
| Schema | Approved 1.0.0, six uniqueness constraints and seven explicit lookup indexes. Exact catalog recorded in the audit. |

Use the original GTFS Schedule snapshot and the normalized build as the static reference. Require the database ID, source snapshot, normalized build, schema and canonical contract to agree before resolving any hybrid scope. Preserve all accepted visit, calendar and geometry semantics externally, including extended hours and repeated visits. Use the 13 exact class and relationship digests as diagnostics and compare the full static fingerprint at version and deployment boundaries. Recheck Neo4j schema definitions and immutable file digests when publishing a new baseline.

Phase 7 ran under an admin account with a write grant. Set up a dedicated least-privilege read account for grounding and a separately scoped writer for any future event data, subject to explicit approval. Define a static projection that preserves the existing fingerprint contract while allowing future event content to be verified independently. Protect the authoritative labels, relationship types and property keys, keep operational state out of static hashing, and audit unexpected mutations before serving results. Keep timeless entity identity, source snapshot identity and temporal validity separate.

Use the approved competency set, AC path and date regression, raw latency samples and Phase 7 offline tests as baselines. For evaluation, distinguish graph-only, DuckDB-only and hybrid timing. Keep unresolved external operator crosswalks and unsupported scope claims outside authoritative facts. Future dynamic events and uncontrolled concurrent workloads still need separate validation. [Invariance](static_invariance_report.json) · [Benchmark baseline](query_benchmark_results.json) · [Competency checks](audit_before.json) · [Full Phase 7 report](phase7_validation_benchmark_report.md)
