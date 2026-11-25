# TransitKG

**TransitKG: LLM-Based Temporally Grounded Knowledge Graph Construction from GTFS-Realtime Service Alerts** is a research project on grounding transit alerts in an auditable schedule graph. The completed static pipeline profiles and normalizes regional GTFS Schedule data, assigns canonical identifiers, constructs a graph in Neo4j, and verifies it against the published timetable. GTFS-Realtime ingestion, LLM extraction and dynamic updates are future research stages.

The validated snapshot contains **115,683 nodes**, **242,491 relationships** and **3,185,115 timetable visits**. Neo4j holds canonical transit entities and scoped connectivity. DuckDB queries the pinned Parquet for individual visits, ordered stop sequences, service dates, exceptions and shape geometry. [511 SF Bay Open Data](https://511.org/open-data/transit) · [Architecture](reports/phase4/phase4_schema_design_report.md) · [Validation and benchmark](reports/phase7/phase7_validation_benchmark_report.md)

## Quick start on Windows

Follow these steps from a fresh clone to a queryable local graph. Run the PowerShell commands from the repository root after cloning. If the repository requires authentication, use a GitHub account with access to both the repository and its Git LFS files.

### 1. Install prerequisites

- **Git and Git LFS** to download the full dataset. [Git LFS installation](https://docs.github.com/en/repositories/working-with-files/managing-large-files/installing-git-large-file-storage)
- **Python 3.14**. The editable install selects DuckDB 1.5.0 and Neo4j Python driver 6.4.1.
- **Neo4j Desktop 2.x** with a local Neo4j Enterprise instance that supports multiple databases. The reference deployment used **Neo4j Enterprise 2026.10.2**. The importer expects a local Bolt listener on port **7687**. [Neo4j Desktop instance management](https://neo4j.com/docs/desktop/current/operations/instance-management/)

### 2. Clone the repository and download the data

~~~powershell
git lfs install
git clone https://github.com/Kartik740/TransitKG.git
Set-Location TransitKG
git lfs pull
git lfs ls-files
~~~

Git LFS retrieves the full raw `stop_times.txt` and `shapes.txt` files. The clone also contains the pinned normalized and graph-ready Parquet builds. Use the supplied builds for this walkthrough. [Normalized manifest](data/normalized/build-5608c0d7d93b9c6200431b5c/manifest.json) · [Graph-ready manifest](data/graph_ready/build-f85cdfafe998da9e7eecb669/manifest.json)

### 3. Install Python dependencies and verify the input files

~~~powershell
python --version
python -m venv .venv
$TransitKGPython = (Resolve-Path '.venv/Scripts/python.exe').Path
& $TransitKGPython -m pip install -e .

$TransitKGImport = 'from transitkg.graph import import_static_graph as job; job.REPORT = job.ROOT / ".venv" / "transitkg-local"; raise SystemExit(job.main())'
& $TransitKGPython -c $TransitKGImport --offline
~~~

Expect `"status": "offline-inputs-verified"`. This preflight verifies **27 raw files, 32 normalized Parquet files and 13 graph-ready Parquet files**, checks the schema and manifests, and recomputes the graph-ready digest. Keep this PowerShell window open for the next steps.

`$TransitKGImport` invokes the existing importer with **`.venv/transitkg-local/`** as your local reports and recovery directory. The committed Phase 6 report and checkpoint refer to the original Neo4j database ID. Your database gets its own ID and local state. [Import contract](reports/phase5/phase6_import_contract.md)

### 4. Create and start a local Neo4j instance

1. Open **Neo4j Desktop**. Choose **Create** in a project, or **Get started** on a first launch, and create a **local instance**.
2. Name the instance **TransitKG** or another name you recognize. Choose Neo4j **2026.10.2** if available and set a password for the local `neo4j` user.
3. Start the instance. Keep the default database as supplied by Neo4j. The importer creates a separate database named `transitkg-static`.
4. Confirm that Desktop shows `neo4j://127.0.0.1:7687` or the equivalent `localhost` address. The examples use `127.0.0.1`.

Use the initial administrative account on your own local instance for the import. It needs permission to create a database, install schema and load graph records. [Neo4j Desktop setup](https://neo4j.com/docs/desktop/current/operations/instance-management/)

### 5. Preflight, import and independently verify the graph

Run this block in the same PowerShell window. Enter the password you set in Desktop at the masked prompt.

~~~powershell
$env:NEO4J_URI = 'neo4j://127.0.0.1:7687'
$env:NEO4J_USERNAME = 'neo4j'
$env:NEO4J_DATABASE = 'transitkg-static'
$neo4jSecret = Read-Host 'Local Neo4j password' -AsSecureString
$env:NEO4J_PASSWORD = [System.Net.NetworkCredential]::new('', $neo4jSecret).Password

try {
    & $TransitKGPython -c $TransitKGImport --database transitkg-static --preflight
    if ($LASTEXITCODE -ne 0) { throw 'Neo4j preflight failed' }

    & $TransitKGPython -c $TransitKGImport --database transitkg-static --write-approved --create
    if ($LASTEXITCODE -ne 0) { throw 'TransitKG import failed' }

    & $TransitKGPython -c $TransitKGImport --database transitkg-static --verify
    if ($LASTEXITCODE -ne 0) { throw 'TransitKG verification failed' }
} finally {
    Remove-Item Env:NEO4J_PASSWORD -ErrorAction SilentlyContinue
    Remove-Variable neo4jSecret -ErrorAction SilentlyContinue
}
~~~

Preflight should return `preflight-passed`. For a newly created local instance, `.venv/transitkg-local/live_preflight.json` should show a null target database. A pre-existing target must be dedicated to TransitKG and empty, a recognized interrupted import, or a matching completed graph.

The import can take several minutes. It applies the approved constraints and indexes, loads the graph, and checks the complete Neo4j readback. Expect `"status": "validated"`, **115,683 nodes**, **242,491 relationships** and the fingerprint below. The separate `--verify` command repeats the readback without database writes.

~~~text
675d71bd4e8bd82ffc1ec0d8951ca527928ca4376c681e40193d14f451892e66
~~~

Look for `static_graph_manifest.json` and `last_database_attempt.json` under `.venv/transitkg-local/`. If the import is interrupted, keep the local `state/` directory and rerun against the same database and local reports directory. The importer validates the checkpoint and existing records before resuming. [.env.example](.env.example) lists the shell environment variables used for the connection.

## Query the knowledge graph in Neo4j

In Neo4j Desktop, open **Query** from the running instance’s **Connect** menu or the sidebar. Sign in with your local account and choose **transitkg-static** in the database dropdown. Paste one Cypher block at a time. Use **Table** view for rows and properties and **Graph** view for queries that return paths. [Neo4j Desktop Query guide](https://neo4j.com/docs/desktop/current/query/)

### Confirm the snapshot and counts

~~~cypher
MATCH (f:FeedSnapshot)
RETURN f.source_snapshot_id AS source_snapshot,
       f.normalized_build_id AS normalized_build,
       f.static_fingerprint AS fingerprint;
~~~

~~~cypher
CALL () {
  MATCH (n)
  RETURN count(n) AS nodes
}
CALL () {
  MATCH ()-[r]->()
  RETURN count(r) AS relationships
}
RETURN nodes, relationships;
~~~

Expect one `FeedSnapshot` row and counts of **115,683** and **242,491**. The snapshot query displays the stored fingerprint. The importer’s `--verify` command independently recomputes it from the database.

### Visualize AC Transit stop 55660

~~~cypher
MATCH scope = (a:Agency {source_id: 'AC'})
              -[:AGENCY_SERVES_STOP]->(s:Stop {source_id: '55660'})
MATCH path = (a)-[:OPERATES]->(r:Route)-[:ROUTE_SERVES_STOP]->(s)
RETURN scope, path
ORDER BY r.source_id
LIMIT 10;
~~~

Switch to **Graph** view. The four expected routes are **AC:72, AC:72L, AC:72M and AC:802**. These paths represent operator scope and scheduled connectivity in the pinned GTFS snapshot.

### Inspect route, trip and service connections

~~~cypher
MATCH path = (r:Route {source_id: 'AC:72'})
             -[:HAS_TRIP]->(t:Trip)-[:RUNS_ON]->(s:Service)
RETURN path, t.source_id AS trip_id, t.direction_id AS direction,
       s.source_id AS service_id
ORDER BY trip_id
LIMIT 10;
~~~

### Distinguish stops that share a name

~~~cypher
MATCH (s:Stop {stop_name: 'San Pablo Av & W Grand Av'})
RETURN s.source_id AS stop_id, s.canonical_id AS canonical_id
ORDER BY stop_id;
~~~

Stops **52227** and **55660** have separate canonical IDs. Use the ID for exact lookup. The regional canonical namespace is `511-sf-bay-regional`. For example, source route `AC:72` has canonical ID `511-sf-bay-regional:route:AC%3A72`. [Canonical identity contract](reports/phase3/canonical_identifier_contract.md) · [More Browser queries](reports/phase6/visualization_queries.cypher) · [Parameterized Cypher examples](schema/competency.cypher)

## Query dated timetable visits with DuckDB

Use the normalized Parquet for individual visits, service dates and ordered trip sequences. The example below counts distinct scheduled trip templates at AC stop 55660 on a fixed regression date.

~~~powershell
@'
from transitkg.graph import design_static_graph, transform_static_graph

with design_static_graph.open_build(transform_static_graph.BUILD) as db:
    result = db.execute("""
        SELECT count(DISTINCT v.canonical_trip_id)
        FROM scoped_visits(?, NULL, NULL, ?, NULL, NULL) AS v
        JOIN active_services(DATE '2026-11-25') AS active USING (service_id)
    """, ["AC", "55660"]).fetchone()
    print(f"Scheduled trip templates at AC stop 55660 on 2026-11-25: {result[0]}")
'@ | & $TransitKGPython -
~~~

Expect **112** on that service date. The full snapshot has **345** distinct AC trip templates visiting stop 55660. The timetable retains GTFS service-day times beyond `24:00:00`. [Timetable SQL](schema/timetable.sql) · [Regional competency checks](reports/phase7/audit_before.json)

## Troubleshooting

| Symptom | What to check |
|---|---|
| Offline preflight reports a raw-file checksum mismatch | Run `git lfs pull` and repeat preflight. Confirm Git LFS downloaded the two large files and the checkout retains the line-ending rules in `.gitattributes`. |
| Neo4j connection or authentication fails | Start the local instance, confirm Bolt port `7687` and enter the password again. Set `NEO4J_USERNAME` if your account uses another name. |
| The importer reports a different previous database identity | Use `$TransitKGImport` as shown in step 3. For a newly recreated empty database, choose a fresh local report directory in that variable’s Python command. |
| The importer refuses a target with unrelated data | Use a separate empty database with a `transitkg-*` name and a fresh local report directory. Set `NEO4J_DATABASE` and `--database` to that name. |
| Query runs and returns zero rows | Select `transitkg-static`, run the snapshot and count queries, and verify the exact case and spelling of `source_id` or `canonical_id`. |

## Repository layout and graph schema

| Path | Purpose |
|---|---|
| `GTFSTransitData_RG/` | Original 511 regional GTFS Schedule snapshot. |
| `data/normalized/` | Immutable Parquet timetable, associations, canonical mappings and lineage. |
| `data/graph_ready/` | Immutable typed node and relationship files consumed by the importer. |
| `schema/` | Graph schema, Neo4j DDL, Cypher queries, Mermaid diagram and timetable SQL. |
| `src/transitkg/` | Namespaced Python modules for profiling, normalization, graph operations, validation and benchmarking. |
| `scripts/` | Historical CLI entry points, dependency pins and checksum-pinned source files. |
| `tests/` | Offline regression suite. |
| `reports/` and `docs/` | Published research evidence, reproduction guides and migration documentation. |

| Graph objects | Types |
|---|---|
| Nodes | `Agency`, `Route`, `Trip`, `Stop`, `Service`, `FeedSnapshot` |
| Relationships | `OPERATES`, `HAS_TRIP`, `RUNS_ON`, `ROUTE_SERVES_STOP`, `AGENCY_SERVES_STOP`, `PART_OF`, `TRANSFER_RULE` |

[Machine-readable schema](schema/static_graph_schema.json) · [Repository migration and file mapping](docs/repository_restructuring.md)

## Development and research reproduction

Run the offline suite from the checkout root. The explicit normalized build path enables the regional regression.

~~~powershell
$env:TRANSITKG_PHASE3_BUILD = (Resolve-Path 'data/normalized/build-5608c0d7d93b9c6200431b5c').Path
& $TransitKGPython -m unittest discover -s tests -t . -p 'test_*.py'
~~~

The historical `-s scripts` test discovery command is supported. The original phase requirements remain available in `scripts/`. Install the optional plotting dependency with `pip install -e '.[plots]'` to regenerate the benchmark figure.

| Phase | Report and reproduction |
|---|---|
| 2 | [Dataset profiling](reports/phase2/phase2_dataset_exploration_report.md) |
| 3 | [GTFS normalization and identity](reports/phase3/phase3_data_cleaning_report.md) |
| 4 | [Graph schema and architecture](reports/phase4/phase4_schema_design_report.md) |
| 5 | [Graph-ready transformation](reports/phase5/phase5_graph_transformation_report.md) |
| 6 | [Neo4j construction and readback](reports/phase6/phase6_static_graph_construction_report.md) |
| 7 | [Static audit and benchmark](reports/phase7/phase7_validation_benchmark_report.md) |

The published evidence covers one regional schedule snapshot and local single-client static workloads. External operator ID crosswalks, live alert coverage and LLM accuracy require separate evaluation. When citing a result, include the pinned snapshot and its phase report. [Static baseline readiness](reports/phase7/phase8_readiness_contract.md)

