# TransitKG

TransitKG is a research project on temporally grounded knowledge graph construction from GTFS-Realtime service alerts. The completed static pipeline profiles and normalizes GTFS Schedule, assigns canonical identifiers, constructs an approved compact graph in Neo4j and validates it against the normalized timetable. Real-time ingestion, LLM-based extraction, entity linking across operator feeds and dynamic graph updates are future research stages.

## Architecture and results

The project uses Python 3.14, DuckDB 1.5.0, Neo4j Python driver 6.4.1, typed Parquet and Neo4j Enterprise 2026.10.2 in the validated local deployment. The source is the 511 SF Bay regional GTFS Schedule feed. The graph contains **115,683 nodes and 242,491 relationships**. Neo4j provides canonical entities and scoped scheduled connectivity. DuckDB queries the immutable normalized Parquet for ordered visits, service dates and exceptions, and geometry. [511 SF Bay Open Data](https://511.org/open-data/transit) · [Phase 4 architecture](reports/phase4/phase4_schema_design_report.md) · [Phase 7 validation and benchmark](reports/phase7/phase7_validation_benchmark_report.md)

| Neo4j nodes | Neo4j relationships |
|---|---|
| Agency, Route, Trip, Stop, Service and FeedSnapshot | OPERATES, HAS_TRIP, RUNS_ON, ROUTE_SERVES_STOP, AGENCY_SERVES_STOP, PART_OF and TRANSFER_RULE |

The canonical identity contract preserves exact GTFS identifiers, including agency scope. The static fingerprint is `675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66`. The tested AC/55660 selector resolves in the pinned regional snapshot. Current measurements cover local single-client static workloads. Cross-operator alias coverage, cold-cache behavior and dynamic workloads remain separate research questions. [Canonical ID contract](reports/phase3/canonical_identifier_contract.md) · [Schema](schema/static_graph_schema.json) · [Invariance evidence](reports/phase7/static_invariance_report.json)

## Repository

| Directory | Purpose |
|---|---|
| `src/transitkg/` | Organized Python API for profiling, normalization, graph operations, validation and benchmarking. |
| `scripts/` | Historical CLI paths, compatibility launchers, dependency pins and byte-pinned research implementations. |
| `tests/` | The six existing unittest modules. Legacy discovery in `scripts/` delegates to these tests. |
| `GTFSTransitData_RG/` | Raw regional GTFS source snapshot. |
| `data/normalized/` and `data/graph_ready/` | Validated immutable Parquet builds and their manifests. |
| `schema/` | Approved graph schema, DDL, timetable SQL, competency Cypher and diagram. |
| `reports/` | Research reports and evidence from Phases 2 through 7. |
| `docs/` | Repository migration, exact path mapping and rollback instructions. |

Selected pinned entry points remain under `scripts/` because the builds and reports attest to their exact source bytes. Use package-level exports for these modules, for example `from transitkg.graph import transform_static_graph` and `from transitkg.validation import audit_static_graph`. The reporting modules are ordinary package submodules, for example `from transitkg.benchmarking.report_static_graph_benchmark import render`. [Repository migration](docs/repository_restructuring.md)

## Environment

Run PowerShell commands from the checkout root. The existing dependency directory is supported.

~~~powershell
$env:PYTHONPATH = '.phase2-deps'
~~~

For a clean checkout environment, create a Python 3.14 virtual environment. The phase-specific requirements files remain available. The editable project install exposes the namespaced Python API. The optional plots dependency is used to render the Phase 7 figure.

~~~powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[plots]'
~~~

For the historical script-only stack, install `scripts/requirements-phase4.txt` in the virtual environment. For profiling and normalization alone, `scripts/requirements-phase2.txt` is sufficient. These files retain their original versions and relationship. The editable package uses the repository's schemas, data and pinned code, so keep the checkout available.

### Tests

Set the pinned regional path to exercise the regional integration test. Both discovery commands execute the same suite.

~~~powershell
$env:TRANSITKG_PHASE3_BUILD = (Resolve-Path 'data/normalized/build-5608c0d7d93b9c6200431b5c').Path
python -m unittest discover -s tests -t . -p 'test_*.py'
python -m unittest discover -s scripts -p 'test_*.py'
~~~

### Secure Neo4j verification

Set the process environment variables shown in `.env.example`. Use your local secret store or enter the password via a masked prompt. Use a dedicated read-only principal when available. The validation target is explicitly `transitkg-static`.

~~~powershell
$env:NEO4J_URI = 'neo4j://127.0.0.1:7687'
$env:NEO4J_USERNAME = 'neo4j'
$env:NEO4J_DATABASE = 'transitkg-static'
$neo4jSecret = Read-Host 'Neo4j password' -AsSecureString
$env:NEO4J_PASSWORD = [System.Net.NetworkCredential]::new('', $neo4jSecret).Password
try {
    python scripts/import_static_graph.py --database transitkg-static --verify
} finally {
    Remove-Item Env:NEO4J_PASSWORD -ErrorAction SilentlyContinue
}
~~~

The verify command reads graph content and writes a local verification report. To keep historical report files byte-for-byte, use the independent `verify_content` and `audit_static_graph.verify_target` APIs as shown in the migration report.

### Reproduction

Historical commands and arguments remain in the phase reports. For local reruns, choose fresh output paths so the published evidence remains available.

| Phase | Artifacts and reproduction |
|---|---|
| 2 | [Dataset profiling](reports/phase2/phase2_dataset_exploration_report.md) |
| 3 | [GTFS normalization](reports/phase3/phase3_data_cleaning_report.md) |
| 4 | [Schema and architecture](reports/phase4/phase4_schema_design_report.md) |
| 5 | [Graph-ready transformation](reports/phase5/phase5_graph_transformation_report.md) |
| 6 | [Static construction and Neo4j readback](reports/phase6/phase6_static_graph_construction_report.md) |
| 7 | [Read-only graph audit and benchmark](reports/phase7/phase7_validation_benchmark_report.md) |

~~~powershell
python scripts/profile_gtfs.py --help
python scripts/normalize_gtfs.py --help
python scripts/transform_static_graph.py --repeat --output data/graph_ready-rerun
python scripts/benchmark_static_graph.py --output reports/phase7-rerun
~~~

The next research stage can build against the static snapshot and behavior specified in the [Phase 8 readiness contract](reports/phase7/phase8_readiness_contract.md). Add real-time and LLM modules as separate implementations when those stages begin, with independent credentials, test fixtures and static fingerprint checks.

