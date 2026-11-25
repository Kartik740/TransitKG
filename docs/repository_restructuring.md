# TransitKG repository restructuring

The original `scripts/` directory contained CLI commands, library modules, tests and resource files together. Several phases also record exact source checksums and paths, so moving all modules would affect build identity and checkout reproducibility. The migration separated the relocatable Python code and tests, grouped all implementation responsibilities under a namespaced API, and retained the attested source files in place.

## Baseline and migration plan

Inspected the repository inventory, Git status and file line endings, Python dependency/import graph, previous CLI commands and tests, schema/query resources, source/build manifests, report references, local dependencies, caches and environment variable names. The pre-migration baseline on Git commit `5515d36` passed all 56 tests, input preflight, exact graph readback and all requested AC scopes.

The migration proceeded in four steps. First, established the branch and inventoried SHA-256 dependencies. Second, moved the Phase 2 and Phase 3 report implementations and reran 17 focused tests. Third, moved the unpinned Neo4j probe and the benchmark renderer, verified their CLI paths and reran the import tests. Fourth, moved the tests with explicit discovery bridges and ran the complete suite using both discovery paths. A fresh isolated graph-ready repeat and read-only database audit followed.

| Responsibility | Dependencies and compatibility boundary |
|---|---|
| Profiling | Profile CLI uses checks, network analysis, a colocated GTFS field definition and the extracted report module. |
| Normalization | CLI uses pinned profiling readers, cleaning, canonical transformations, Phase 2 reports and the extracted Phase 3 report writer. Its existing subprocess still discovers tests under scripts/. |
| Graph construction | Phase 4 analysis loads schema and timetable contracts from the checkout. The Phase 5 transform hashes its own source and Phase 4 design/validation modules. The Phase 6 importer and verifier use the approved DDL and exact Parquet builds. |
| Validation | Graph-ready validation, Neo4j readback and the regional audit share the versioned contracts. The legacy Phase 4 probe now resolves resources through the shared checkout resolver. |
| Benchmarking | Benchmark CLI uses the audit, workload catalog, normalized timetable and Phase 6 evidence. The extracted renderer reads previously captured samples and writes only to the requested report directory. |
| Testing | The six real test modules live under tests/. A lightweight unittest load hook delegates from each previous path, and the direct launchers retain file-based CLI execution. |

## File mapping

Every old path below remains a compatibility entry point. There is one implementation of each relocated module or test.

| Previous path | Implementation location |
|---|---|
| `scripts/gtfs_report.py` | `src/transitkg/profiling/gtfs_report.py` |
| `scripts/gtfs_phase3_report.py` | `src/transitkg/normalization/gtfs_phase3_report.py` |
| `scripts/verify_phase4_neo4j.py` | `src/transitkg/validation/verify_phase4_neo4j.py` |
| `scripts/report_static_graph_benchmark.py` | `src/transitkg/benchmarking/report_static_graph_benchmark.py` |
| `scripts/test_profile_gtfs.py` | `tests/test_profile_gtfs.py` |
| `scripts/test_normalize_gtfs.py` | `tests/test_normalize_gtfs.py` |
| `scripts/test_static_graph.py` | `tests/test_static_graph.py` |
| `scripts/test_graph_ready.py` | `tests/test_graph_ready.py` |
| `scripts/test_static_graph_import.py` | `tests/test_static_graph_import.py` |
| `scripts/test_static_graph_benchmark.py` | `tests/test_static_graph_benchmark.py` |

The moved Phase 4 probe has a single path-resolution change. The other three implementations and the six test modules are byte-identical to their originals.

The **14 checksum-pinned Python modules and one field resource** remain at their original `scripts/` paths. Phase 3 pins `normalize_gtfs.py`, `gtfs_cleaning.py`, `gtfs_normalized.py`, `gtfs_checks.py`, `gtfs_network.py`, `profile_gtfs.py` and `gtfs_schedule_fields.json`. Phase 5 pins `design_static_graph.py`, `transform_static_graph.py` and `validate_graph_ready.py`. Phase 6 pins `import_static_graph.py` and `verify_imported_graph.py`. Phase 7 pins `audit_static_graph.py`, `benchmark_static_graph.py` and `static_graph_workloads.py`. Historical manifests and source hashes were preserved.

The package exposes those pinned sources through group-level imports such as `from transitkg.graph import transform_static_graph`. Legacy flat imports resolve the same modules. The unpinned reporting and probe modules use ordinary namespaced submodule imports. Compatibility path setup is localized to the script bridge and checkout resolver. Run `python -m unittest discover -s tests -t .` to import tests through the checkout's package setup. Standalone wheels cannot supply the repository-bound data and attested artifacts, so the supported package setup is editable.

## Configuration and dependencies

The new `pyproject.toml` records the validated Python 3.14 runtime and the existing DuckDB and Neo4j pins. Matplotlib 3.10.8 is an optional report-generation dependency. The existing phase requirements files and `.phase2-deps` workflow are supported. An editable install was tested in a disposable virtual environment with the existing pinned runtime dependencies and imports exercised from outside the checkout.

The new `.gitattributes` records the existing LF and CRLF representations for raw data, selected manifests, schema, legacy scripts and research evidence. Before this change, a fresh checkout could convert checksum-pinned text according to `core.autocrlf`. Tested `git cat-file --filters` SHA-256 against all 135 protected files and all 15 pinned source/resource files. All 150 filtered representations matched the validated worktree bytes. Git normalization for stored CRLF text differs from its required checkout representation.

The ignore rules cover virtual environments, generated metadata and local environment files. `.env.example` is trackable and contains only configuration keys and a blank password field. Neo4j checks used the existing encrypted local handoff in process memory. The account still has its previously verified write grant, so database verification used explicit read sessions and the same pinned database identity. Review provider distribution terms and the existing binary artifact sizes before publishing a public release.

## Regression checks

| Check | Before | After |
|---|---|---|
| Legacy unittest discovery | 56 passed, zero failures or skips | 56 passed, zero failures or skips |
| Namespaced test discovery | — | The same 56 unique cases passed |
| Immutable research paths | 135 SHA-256 values | All matched, including 27 raw, 32 normalized and 13 graph-ready data files |
| Pinned implementation paths | 15 recorded SHA-256 values | All matched |
| Neo4j content | 115,683 nodes, 242,491 edges, six uniqueness constraints, seven explicit indexes | Identical counts, catalog, database identity and all 13 logical digests |
| Phase 5 repeat | Published graph-ready manifest and digest | Isolated transformation reproduced the exact build ID, fingerprint and all 13 per-file logical digests |
| Legacy script compatibility | Documented arguments and test discovery | Nine CLI help checks retained their historical filenames, direct test invocation ran eight tests, and four Phase 7 report outputs regenerated byte-for-byte in a temporary directory |

Expected, pre-migration and post-migration fingerprint all equal `675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66`. The verifier compared the Neo4j records against the pinned build and ran the existing regional competency checks without publishing a new construction report.

| AC stop | Routes verified in both Neo4j and normalized Parquet |
|---|---|
| 55660 | AC:72, AC:72L, AC:72M, AC:802 |
| 50632 | AC:72, AC:72M, AC:802 |
| 53368 | AC:70, AC:71 |

Stop 52227 still has its distinct canonical ID and 339 visiting trip templates. Stop 55660 returned 345 templates, including 112 on the fixed regression date. [Before-and-after evidence and checksums](repository_restructuring_checks.json)

## Git and rollback

The migration is an uncommitted working-tree change on `codex/safe-repository-restructure`, based on `5515d36` and including the previously completed Phase 7 commit. The Git index stayed clean. To roll back the migration before it is committed, review `git status --short`, then stash only the migration paths below and switch to the original branch. The stash is a reversible copy of that code and documentation change. Keep unrelated later edits separate before doing this.

~~~powershell
git stash push --include-untracked -m 'TransitKG repository restructuring' -- .env.example .gitattributes .gitignore README.md pyproject.toml docs scripts src tests
git switch main
~~~

If the restructuring is committed later, `main` and `5515d36` still provide the original layout. The procedure uses existing Git history and requires no separate data copies.

For standard setup, read-only verification, and reproduction commands, see the [README](../README.md). For readback without writing a historical report, use `transitkg.validation.audit_static_graph.verify_target` in a `READ_ACCESS` session with `transitkg.graph.import_static_graph.preflight` to pin the artifacts. Use a fresh output directory for reproduction runs that generate files. Future real-time and LLM modules should use separate package areas only when implemented and retain the static validation boundary. [Phase 8 readiness](../reports/phase7/phase8_readiness_contract.md)

## Final tree

Datasets are collapsed and generated virtual environments, caches and package metadata are omitted.

~~~text
TransitKG/
├── .env.example
├── .gitattributes
├── .gitignore
├── data/
│   ├── graph_ready/
│   │   └── build-f85cdfafe998da9e7eecb669/
│   │       └── [13 Parquet files and manifest]
│   └── normalized/
│       └── build-5608c0d7d93b9c6200431b5c/
│           └── [32 Parquet files and manifest]
├── docs/
│   ├── repository_restructuring_checks.json
│   └── repository_restructuring.md
├── GTFSTransitData_RG/
│   └── [27 raw files]
├── pyproject.toml
├── README.md
├── reports/
│   ├── phase2/
│   │   ├── data_profile.json
│   │   ├── data_quality_report.json
│   │   ├── dataset_inventory.json
│   │   ├── identifier_analysis.json
│   │   ├── phase2_dataset_exploration_report.md
│   │   ├── realtime_compatibility_report.json
│   │   ├── referential_integrity_report.json
│   │   └── temporal_network_graph.json
│   ├── phase3/
│   │   ├── canonical_identifier_contract.md
│   │   ├── cleaning_policy.json
│   │   ├── data_integrity_report.json
│   │   ├── dataset_manifest.json
│   │   ├── mapping_validation.json
│   │   ├── normalization_summary.json
│   │   ├── phase3_data_cleaning_report.md
│   │   └── realtime_compatibility_report.json
│   ├── phase4/
│   │   ├── architecture_comparison.json
│   │   ├── competency_questions.md
│   │   ├── neo4j_verification.json
│   │   ├── phase4_schema_design_report.md
│   │   ├── phase5_transformation_contract.md
│   │   └── schema_validation_report.json
│   ├── phase5/
│   │   ├── graph_cardinality_report.json
│   │   ├── graph_fingerprint_report.json
│   │   ├── graph_integrity_report.json
│   │   ├── phase5_graph_transformation_report.md
│   │   └── phase6_import_contract.md
│   ├── phase6/
│   │   ├── automated_test_report.json
│   │   ├── database_creation_attempt.json
│   │   ├── last_database_attempt.json
│   │   ├── live_preflight.json
│   │   ├── offline_preflight.json
│   │   ├── phase6_static_graph_construction_report.md
│   │   ├── phase7_validation_contract.md
│   │   ├── state/
│   │   │   ├── transitkg-static.checkpoint.json
│   │   │   └── transitkg-static.lock
│   │   ├── static_graph_manifest.json
│   │   ├── visualization_queries.cypher
│   │   └── visualization_validation.json
│   └── phase7/
│       ├── architecture_and_construction.json
│       ├── audit_after.json
│       ├── audit_before.json
│       ├── automated_test_report.json
│       ├── benchmark_comparisons.json
│       ├── benchmark_environment.json
│       ├── benchmark_latency.png
│       ├── benchmark_protocol.json
│       ├── competency_cases.json
│       ├── phase7_validation_benchmark_report.md
│       ├── phase8_readiness_contract.md
│       ├── query_benchmark_results.json
│       ├── query_benchmark_samples.parquet
│       ├── query_plan_analysis.json
│       ├── static_invariance_report.json
│       ├── storage_measurement.json
│       └── structural_analysis.json
├── schema/
│   ├── competency.cypher
│   ├── neo4j_schema.cypher
│   ├── static_graph_schema.json
│   ├── static_graph_schema.mmd
│   └── timetable.sql
├── scripts/
│   ├── _transitkg_compat.py
│   ├── audit_static_graph.py
│   ├── benchmark_static_graph.py
│   ├── design_static_graph.py
│   ├── gtfs_checks.py
│   ├── gtfs_cleaning.py
│   ├── gtfs_network.py
│   ├── gtfs_normalized.py
│   ├── gtfs_phase3_report.py
│   ├── gtfs_report.py
│   ├── gtfs_schedule_fields.json
│   ├── import_static_graph.py
│   ├── normalize_gtfs.py
│   ├── profile_gtfs.py
│   ├── report_static_graph_benchmark.py
│   ├── requirements-phase2.txt
│   ├── requirements-phase4.txt
│   ├── static_graph_workloads.py
│   ├── test_graph_ready.py
│   ├── test_normalize_gtfs.py
│   ├── test_profile_gtfs.py
│   ├── test_static_graph_benchmark.py
│   ├── test_static_graph_import.py
│   ├── test_static_graph.py
│   ├── transform_static_graph.py
│   ├── validate_graph_ready.py
│   ├── verify_imported_graph.py
│   └── verify_phase4_neo4j.py
├── src/
│   └── transitkg/
│       ├── __init__.py
│       ├── _workspace.py
│       ├── benchmarking/
│       │   ├── __init__.py
│       │   └── report_static_graph_benchmark.py
│       ├── graph/
│       │   └── __init__.py
│       ├── normalization/
│       │   ├── __init__.py
│       │   └── gtfs_phase3_report.py
│       ├── profiling/
│       │   ├── __init__.py
│       │   └── gtfs_report.py
│       └── validation/
│           ├── __init__.py
│           └── verify_phase4_neo4j.py
└── tests/
    ├── __init__.py
    ├── test_graph_ready.py
    ├── test_normalize_gtfs.py
    ├── test_profile_gtfs.py
    ├── test_static_graph_benchmark.py
    ├── test_static_graph_import.py
    └── test_static_graph.py
~~~

