# TransitKG canonical identifier contract

Contract version **1.0.0**

## Entity keys

~~~text
<source_namespace>:<entity_type>:<percent-encoded source ID>
~~~

The namespace for this build is **511-sf-bay-regional**. It identifies a source feed independently of its version, file layout and processing date. Supported entity types are agency, stop, route, trip, service, and shape. Use a different stable namespace for a separately issued source feed.

UTF-8 encode the exact source string and percent-escape every byte outside RFC 3986's unreserved ASCII set, with uppercase hexadecimal. Colons and literal percent signs in source IDs are escaped. Split on colons into exactly three components and decode the final component exactly once. Keep case, leading zeros, and original Unicode code points. A source value of %3A encodes as %253A.

| Entity | Source ID | Canonical ID |
|---|---|---|
| Agency | AC | 511-sf-bay-regional:agency:AC |
| Route | AC:72 | 511-sf-bay-regional:route:AC%3A72 |
| Stop | 55660 | 511-sf-bay-regional:stop:55660 |
| Stop | 52227 | 511-sf-bay-regional:stop:52227 |

Keep source identifiers as strings. Multiple agencies can provide scheduled service to the same regional stop. Keep agency and route scope in the association tables. Keep composite keys in separate typed columns, including (trip_id, stop_sequence) and (service_id, date).

## Provenance and lookup

The canonical_ids.parquet table records the original ID, canonical ID, source namespace, identity scope, source tables, snapshot ID, mapping method and static verification status. Shape points share their parent shape identity. A service may originate in a weekly calendar row, an added calendar exception, or both. Removal exceptions refer to an existing service.

The source_record column is the one-based parsed data-record ordinal in the source file, excluding its header. Combine it with the manifest's file path and snapshot checksum to recover source lexemes. The exception ledger gives the retained ordinal for exact duplicate rows.

Canonical identity, raw snapshot and normalized build each have a separate key. Canonical IDs are deterministic for the same feed, entity type and exact source spelling. Publishers may reuse IDs or change the ID assigned to an existing entity. Reconcile across versions with dated evidence before asserting real-world continuity.

Internal mappings establish the regional snapshot identity. An external crosswalk requires authoritative dated evidence scoped to both feeds. Stop-code candidates carry independent UNVERIFIED status. Evaluate selector combinations on the same scheduled service path, using a service date and start-time context for trip instances when required. [GTFS Schedule](https://gtfs.org/documentation/schedule/reference/) · [GTFS Realtime EntitySelector](https://gtfs.org/documentation/realtime/reference/#message-entityselector)
