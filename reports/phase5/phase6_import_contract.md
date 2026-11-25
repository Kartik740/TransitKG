# Phase 6 import contract

## Pinned inputs

| Field | Value |
|---|---|
| Publication | [data/graph_ready/build-f85cdfafe998da9e7eecb669](../../data/graph_ready/build-f85cdfafe998da9e7eecb669/manifest.json) |
| Graph-ready build ID | graph-ready-sha256-f85cdfafe998da9e7eecb6697443ebd02e45f4d43e41462db7a8a5acb82dadae |
| Source snapshot | gtfs-sha256-d6279860e2fe203541a574ee04db5752ecad9e5e1a38b1759cc397b1b596dfa2 |
| Normalized publication | normalized-sha256-5608c0d7d93b9c6200431b5c227da598d52cd0108e9e2f410e5b012b00d28052 |
| Schema and canonical ID contract | 1.0.0 and 1.0.0 |
| Expected static fingerprint | 675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66 |
| Expected regional shape | 115,683 nodes and 242,491 relationships |

## Preflight and target

Validate the input manifest, schema and transformation contract digests, normalized build ID, all 13 Parquet hashes and the expected digest recomputed from reopened exports. The publication manifest specifies exact columns, types, counts and lineage. Use the approved DDL to install six uniqueness constraints and seven explicit lookup indexes, and wait for the schema to be online. Confirm authentication, availability and target name through securely injected connection settings. Establish a new, dedicated empty database for the first import and keep readers out until verification. The earlier authorization to create a disposable Phase 4 fixture was for that fixture. Phase 6 needs an explicitly designated target and write authorization. If a target contains data, require a matching import checkpoint to resume, or escalate a separately scoped update plan. [Schema definitions](../../schema/static_graph_schema.json) · [Neo4j DDL](../../schema/neo4j_schema.cypher)

## Transactional load order

1. Create Agency (41), Route (665), Trip (92,827), Stop (21,664) and Service (485) records from the exact-label filenames in nodes/. Use canonical_id as the typed lookup key. Copy every declared property under its existing name.
2. Create OPERATES (665), HAS_TRIP (92,827), RUNS_ON (92,827), ROUTE_SERVES_STOP (33,985), AGENCY_SERVES_STOP (19,904), PART_OF (2,222) and TRANSFER_RULE (61) from the exact uppercase type filenames in relationships/. Resolve start_id and end_id against canonical_id on the specific labels declared in the schema. Keep start_id and end_id in the import envelope. Copy declared edge properties verbatim. Transfer qualifier IDs are exact source IDs and stay strings. The transfer identity includes direction and all four route/trip qualifiers, with null-safe equality. Preserve self-edges, parallel qualifiers and prohibitions.
3. Validate the staged graph against the Phase 5 manifest. Create the one FeedSnapshot keyed by normalized_build_id in the isolated database. Copy its full six-field payload. Keep routing to the graph disabled until its own readback passes.

Read Parquet in bounded chunks and use parameterized driver transactions with UNWIND. Choose and benchmark batch size on the designated target. Serialize writes per logical key. Require exactly one typed start and end match and a row-for-row accounted batch. Validate an existing record's properties before accepting a retry. For transfers, match both endpoints and all four qualifier fields with explicit null-safe comparisons and reject a second match before inserting. Commit the checkpoint with the transaction or prove batches can replay safely. Record failed batch identity, failure category and unresolved counts without connection secrets. Keep checkpoint and operational metadata outside the static fingerprint. Offline bulk import is an alternative for a proven empty target with approved admin access and a typed conversion adapter. Use the same semantic payload and fingerprint check for either transport. [Phase 4 import design](../phase4/phase5_transformation_contract.md)

## Type and fingerprint mapping

Parquet VARCHAR becomes a Neo4j string. Parquet BIGINT becomes a signed 64-bit integer. Keep explicit zero and empty strings. Omit optional null graph properties and reconstruct null for each absent declared optional field on readback. Reject a missing required property, unknown property, ambiguous static label or wrongly typed endpoint. This schema has no Date, list or floating-point conversion. External timetable dates and times retain the normalized representations.

Independently serialize the readback with transitkg-static-v1. A node record contains its label, identity and typed properties. An edge record contains its type, identity tuple with both canonical endpoints, and only the declared edge properties. Reconstruct absent optional fields as JSON null. Omit the FeedSnapshot static_fingerprint field from its digest payload and verify that field independently. Sort UTF-8 serialized records by bytes and hash the format line and LF-terminated records. Verify exact equality with the expected fingerprint before enabling readers. Keep expected and imported digests as separate results. [Serialization contract](../phase4/phase5_transformation_contract.md#fingerprint-and-acceptance)

Check the full count matrix, schema catalog, typed endpoints, transfer scopes, shared stops, required properties, snapshot gate and AC/55660 competency queries against the pinned external build. Record import counts, failures and retries, resource usage, store and index bytes, batch settings and server/driver versions. A digest or source mismatch blocks publication. Production measurements and readback belong to Phase 6.

