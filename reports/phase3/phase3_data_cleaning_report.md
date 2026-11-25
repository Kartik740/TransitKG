# TransitKG Phase 3 data cleaning and normalization

## Executive summary

Published **4,699,968** of **4,700,080** source rows as typed Parquet. The exception ledger accounts for **42** exact copies and **70** unresolved rows. Post-cleaning checks passed. The normalized timetable retains **3,185,115 visits**.

## Phase 2 findings and raw snapshot verification

Inspected and fingerprinted all **8** Phase 2 artifacts and reused the CSV reader, specification metadata, service-day parsers and reference checks. All **27** source files matched their Phase 2 path, byte count and SHA-256. A second checksum pass preceded publication. The ancillary changelog remains in the raw snapshot.

The build sequence was source and evidence verification, policy-driven projection, canonical identity and association generation, then validation of the staged Parquet and full-build repeat verification.

| Phase 2 observation | Phase 3 decision |
|---|---|
| Core keys and relationships passed. | Keep core records and validate the exported Parquet against the same reference rules. |
| Five medium findings affect attribution, labels, fares, route attributes and shape distances. | Collapse proven equivalents, isolate unresolved optional values, and retain the geometry. |
| Prefix stripping creates 364 stop-ID collision groups. | Scope canonical identity to the regional feed and escape the exact ID. |
| Multiple agencies serve shared stops. | Publish verified many-to-many service associations. |
| AC/55660 resolves exactly while 52227 shares its name. | Preserve both stop identities and return an ambiguous name-only result. |
| 100,912 arrivals exceed 24 hours and some optional times are absent. | Keep original time text and use nullable service-day seconds. |
| Two source calendars end on 2099-12-31. Feed version and validity are blank. | Keep the source ranges and expose blank provider metadata as unknown. |

## Cleaning policy and outcomes

The typed projection preserves identifiers, display names, extension columns, original time text and explicit zeros. Dates become DATE. Documented numeric fields become nullable integers, doubles or exact decimals. Unquoted blanks become null. Quoted text empties stay empty. The source_empty_typed_fields list identifies quoted empties in converted fields. The original values remain recoverable from source record ordinals. [Cleaning policy](cleaning_policy.json)

| Phase 2 issue | Source | Originally affected | Deduplicated | Quarantined | Flagged and kept |
|---|---|---:|---:|---:|---:|
| P2-011 | attributions.txt | 40 | 40 | 0 | 0 |
| P2-012 | directions.txt | 2 | 0 | 2 | 0 |
| P2-013 | fare_transfer_rules.txt | 68 | 0 | 68 | 0 |
| P2-014 | route_attributes.txt | 4 | 2 | 0 | 0 |
| P2-016 | shapes.txt | 21 | 0 | 0 | 21 |

- **P2-011**. 41 identical rows with one producer role and no agency, route, trip or attribution identifier. Keep one exact dataset-wide row. Link each redundant source record to the retained ordinal.
- **P2-012**. WH:53 direction 0 has Inbound and Outbound. Scheduled headsigns do not establish a winning label. Isolate every row of a nonunique route-direction key. Retain valid trips, headsigns and numeric direction IDs.
- **P2-013**. 20 base-key groups contain 68 rows with distinct filter products and otherwise identical values within each group. The 511 reference lists filter_fare_product_id as a regional extension. Retain all variants as unresolved extended rules in the exception ledger. Exclude base-key conflicts and populated extension filters from the accepted base projection.
- **P2-014**. BA:Yellow-N and BA:Yellow-S each have two exact copies. Each pair agrees on category, subcategory and running way. Collapse identical copies for a nonempty route ID and link them to the retained ordinal. Quarantine any genuinely conflicting variants together.
- **P2-016**. 21 populated distances repeat along ordered shapes. Phase 2 found no decreasing distances. Preserve every point and flag repeated or decreasing distances in the ledger. Distance-based interpretation on affected shapes requires review.

The shape check found 21 equal distances across 13 shapes. Of the flagged points, 20 share the previous populated-distance point's coordinates. The distance and coordinate values remain available.

The fare check found 228 distinct extended keys and 0 populated filter references without a matching product. The Phase 2 type metadata leaves the field undocumented under base GTFS. The 511 specification confirms it as a provider extension. Confirm its fare applicability and precedence before using the quarantined rows. [GTFS fare-transfer keys](https://gtfs.org/documentation/schedule/reference/#fare_transfer_rulestxt) · [511 Data Exchange appendix E.3](https://511.org/media/6657/show)

## Table-by-table accounting

| Source file | Handling | Input | Accepted | Preserved extension | Deduplicated | Quarantined | Flagged | Retyped |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| agency.txt | core canonical table | 41 | 41 | 0 | 0 | 0 | 0 | 0 |
| areas.txt | supporting normalized table | 678 | 678 | 0 | 0 | 0 | 0 | 0 |
| attributions.txt | supporting normalized table | 41 | 1 | 0 | 40 | 0 | 0 | 1 |
| calendar.txt | core canonical table | 485 | 485 | 0 | 0 | 0 | 0 | 485 |
| calendar_attributes.txt | preserved extension | 449 | 0 | 449 | 0 | 0 | 0 | 0 |
| calendar_dates.txt | core canonical table | 5,706 | 5,706 | 0 | 0 | 0 | 0 | 5,706 |
| directions.txt | preserved extension | 1,184 | 0 | 1,182 | 0 | 2 | 0 | 1,182 |
| fare_leg_join_rules.txt | supporting normalized table | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| fare_leg_rules.txt | supporting normalized table | 3,610 | 3,610 | 0 | 0 | 0 | 0 | 0 |
| fare_media.txt | supporting normalized table | 7 | 7 | 0 | 0 | 0 | 0 | 7 |
| fare_products.txt | supporting normalized table | 11,545 | 11,545 | 0 | 0 | 0 | 0 | 11,545 |
| fare_transfer_rules.txt | supporting normalized table | 228 | 160 | 0 | 0 | 68 | 0 | 160 |
| feed_info.txt | supporting normalized table | 1 | 1 | 0 | 0 | 0 | 0 | 0 |
| levels.txt | supporting normalized table | 92 | 92 | 0 | 0 | 0 | 0 | 92 |
| mtc_feed_versions.txt | preserved extension | 41 | 0 | 41 | 0 | 0 | 0 | 0 |
| pathways.txt | supporting normalized table | 1,632 | 1,632 | 0 | 0 | 0 | 0 | 1,632 |
| rider_categories.txt | supporting normalized table | 11 | 11 | 0 | 0 | 0 | 0 | 11 |
| route_attributes.txt | preserved extension | 329 | 0 | 327 | 2 | 0 | 0 | 327 |
| routes.txt | core canonical table | 665 | 665 | 0 | 0 | 0 | 0 | 665 |
| shapes.txt | core canonical table | 1,340,496 | 1,340,496 | 0 | 0 | 0 | 21 | 1,340,496 |
| stop_areas.txt | supporting normalized table | 33,164 | 33,164 | 0 | 0 | 0 | 0 | 0 |
| stop_times.txt | core canonical table | 3,185,115 | 3,185,115 | 0 | 0 | 0 | 0 | 3,185,115 |
| stops.txt | core canonical table | 21,664 | 21,664 | 0 | 0 | 0 | 0 | 21,664 |
| timeframes.txt | supporting normalized table | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| transfers.txt | supporting normalized table | 61 | 61 | 0 | 0 | 0 | 0 | 61 |
| trips.txt | core canonical table | 92,827 | 92,827 | 0 | 0 | 0 | 0 | 92,827 |

Every source row has one disposition. The build retyped values on **4,661,976 rows**. It preserved the source spellings and marked retained warnings independently of source-row disposition. Derived records are counted separately. [Normalization summary](normalization_summary.json)

## Canonical identity and scheduled associations

Verified **118,128 mappings**, including exact source-ID round trips and zero canonical collisions. The **364** stop-ID suffix groups still contain distinct entities. [Canonical ID contract](canonical_identifier_contract.md) · [Mapping validation](mapping_validation.json)

| Entity | Mappings |
|---|---:|
| agency | 41 |
| route | 665 |
| service | 485 |
| shape | 2,446 |
| stop | 21,664 |
| trip | 92,827 |

The exported tables retain **33,985 route–stop pairs**, **19,904 agency–stop pairs**, and **189 multi-agency stops**. Scheduled visits establish service. Physical ownership requires independent evidence.

## Realtime compatibility and AC Transit regression

AgencyId AC and StopId 55660 resolve together as **EXACT_MATCH** to **511-sf-bay-regional:stop:55660**. **345** distinct AC trips visit the stop on AC:72, AC:72L, AC:72M, AC:802. Stop 52227 has a separate canonical ID. The scoped name-only lookup is **AMBIGUOUS_MATCH**.

Use trip context, timetable, weekly service and exceptions to evaluate compound selectors. External operator IDs require dated authority. Stop-code aliases remain unverified. [Realtime compatibility](realtime_compatibility_report.json) · [GTFS Realtime EntitySelector](https://gtfs.org/documentation/realtime/reference/#message-entityselector)

## Temporal integrity

Verified **100,912 arrivals** and **100,948 departures** beyond 24:00:00. The timetable retains **15,109 missing arrivals** and **15,109 missing departures** with the original conditional semantics.
Retained all **5,706 calendar exceptions** and all agency timezones. Weekly rules and exceptions remain compact. The source inventory contains no frequencies.txt.

- Service PE:72836 has source bounds 2026-08-10 through 2099-12-31 and 238 trip templates.
- Service PE:72837 has source bounds 2026-08-10 through 2099-12-31 and 239 trip templates.
- Service PE:78571 has source bounds 2026-08-10 through 2099-12-31 and 241 trip templates.
- Service PG:80067 has source bounds 2026-06-27 through 2099-12-31 and 56 trip templates.
- Service PG:80068 has source bounds 2022-05-30 through 2099-12-31 and 32 trip templates.

The 2099 date comes from source calendar rows. The regional publisher's version and validity remain unknown.

## Quarantine, provenance and post-cleaning validation

The ledger records duplicate lineage, optional representations awaiting review and retained shape warnings. Each entry names the snapshot, source file and record, rule, issue, explanation, and recoverable values.

- **Source snapshot** gtfs-sha256-d6279860e2fe203541a574ee04db5752ecad9e5e1a38b1759cc397b1b596dfa2
- **Normalized build** normalized-sha256-5608c0d7d93b9c6200431b5c227da598d52cd0108e9e2f410e5b012b00d28052
- **Output directory** data\normalized\build-5608c0d7d93b9c6200431b5c
- **Versions** policy 1.0.0, schema 1.0.0, canonical contract 1.0.0.

The snapshot hash uses a sorted path, byte-count and SHA-256 manifest. The build hash incorporates the snapshot, implementation, policy, namespace, contract versions and logical table fingerprints. Logical fingerprints combine schema and row count with two order-independent sums of per-row MD5 halves, wrapped in SHA-256. File SHA-256 checks attest to bytes. Logically equivalent Parquet files can use different physical encodings.

Re-ran **46** relationship checks against reopened Parquet. Verified schema, required fields, keys, source projections, derived references, timetable order, ledger accounting and the Phase 2 baselines. [Data integrity report](data_integrity_report.json) · [Dataset manifest](dataset_manifest.json)

## Performance and automated tests

Python 3.14.0 and DuckDB 1.5.0 on Windows-11-10.0.26300-SP0. The run took **104.67 s wall time** and **151.52 s process CPU time**. Peak process RSS was **902.56 MiB**. Sampled scratch database and spill peaked at **535,736,320 bytes**. Raw input was **296,177,172 bytes** and the Parquet output occupies **61,576,978 bytes**.

Peak RSS is the Windows process peak working set or the OS process maximum on other supported systems. Scratch usage is sampled every 150 ms. Build timings run through validation and checksum verification. The repeat build and test suites run separately.

| Stage | Seconds |
|---|---:|
| source verification | 0.540 |
| source load | 26.856 |
| normalization | 44.376 |
| mapping and associations | 4.838 |
| validation | 27.703 |
| source final verification | 0.270 |

A second build reproduced the canonical mappings, logical fingerprints and build ID. The existing published Parquet file checksums also passed.

- Phase 2 suite ran **7 tests** and **passed**.
- Phase 3 suite ran **10 tests** and **passed**.

## Reproduction

From the workspace root in PowerShell.

~~~powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r scripts/requirements-phase2.txt
.venv/Scripts/python.exe scripts/normalize_gtfs.py "GTFSTransitData_RG" --phase2 "reports\phase2" --output "data\normalized" --reports "reports\phase3" --namespace 511-sf-bay-regional --memory-limit 768MB --threads 2 --repeat --test
~~~

The publisher stages and validates output before renaming it into a build directory. A repeat with equivalent logical content and intact file checksums reuses the published artifact.

## Unresolved decisions and recommendations for Phase 4

Preserve snapshot-specific evidence for incremental loads. Benchmark graph construction using the compact associations and external timetable. Require dated operator evidence for external identifiers. Confirm fare filter precedence, the WH:53 direction label and affected shape distances before using them semantically. Apply compound selector predicates to the same scheduled service path.
