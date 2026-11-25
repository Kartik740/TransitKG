"""Inventory and profile an extracted or zipped GTFS Schedule snapshot."""

import argparse
import codecs
import csv
import hashlib
import json
import os
import platform
import shutil
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

import duckdb

from gtfs_checks import analyze, column_profile, key_profile, q as quote, recommendations
from gtfs_report import render

HERE = Path(__file__).resolve().parent
SCHEMA = json.loads((HERE / "gtfs_schedule_fields.json").read_text(encoding="utf-8"))
REFERENCE_DATE = "2026-11-25"
PROVIDER = {"calendar_attributes", "directions", "route_attributes", "mtc_feed_versions"}


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def inventory_bytes(path):
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")()
    size, lines = 0, 0
    last = b""
    encoding = "UTF-8"
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            if not size and block.startswith(codecs.BOM_UTF8):
                encoding = "UTF-8 with BOM"
            size += len(block)
            digest.update(block)
            lines += block.count(b"\n")
            last = block[-1:]
            if encoding is not None:
                try:
                    decoder.decode(block)
                except UnicodeDecodeError:
                    encoding = None
    if encoding is not None:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            encoding = None
    return {
        "bytes": size, "size": f"{size / 2**20:.3f} MiB" if size >= 2**20 else
        f"{size / 1024:.2f} KiB" if size >= 1024 else f"{size} B",
        "sha256": digest.hexdigest(), "encoding": encoding if size else None,
        "physical_lines": lines + int(bool(last) and last != b"\n"), "empty_file": size == 0,
    }


def files_under(directory):
    return {path.relative_to(directory).as_posix(): path for path in sorted(directory.rglob("*"))
            if path.is_file()}


def extract(source, target):
    # Generated filenames avoid traversal and preserve duplicate ZIP members in the inventory.
    paths = {}
    with zipfile.ZipFile(source) as archive:
        for index, member in enumerate(archive.infolist()):
            if member.is_dir():
                continue
            path = target / f"member-{index}"
            name = member.filename
            if name in paths:
                name = f"{name} [duplicate ZIP member {index}]"
            with archive.open(member) as incoming, path.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            paths[name] = path
    return dict(sorted(paths.items()))


def load_table(db, name, path, entry):
    if entry["empty_file"]:
        entry.update(status="empty", records=0, column_count=0, columns=[], malformed=False)
        return None
    if entry["encoding"] is None:
        raise ValueError("Input did not decode as UTF-8, the GTFS Schedule encoding.")
    if name == "locations.geojson":
        with path.open(encoding="utf-8-sig") as source:
            data = json.load(source)
        if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
            raise ValueError("Expected a GeoJSON FeatureCollection.")
        columns = ["id", "type", "geometry", "properties"]
        rows = [(str(row["id"]) if row.get("id") is not None else None, row.get("type"),
                 json.dumps(row.get("geometry")), json.dumps(row.get("properties")))
                for row in data["features"]]
        create_table(db, entry["sql_table"], columns)
        if rows:
            db.executemany(f'INSERT INTO {quote(entry["sql_table"])} VALUES (?, ?, ?, ?)', rows)
        entry.update(records=len(rows), rejected_records=0, malformed=False)
    else:
        delimiter = "\t" if name.endswith(".tsv") else ","
        with path.open(encoding="utf-8-sig", newline="") as source:
            columns = next(csv.reader(source, delimiter=delimiter, strict=True), [])
        if not columns or any(not c.strip() for c in columns) or len({c.casefold() for c in columns}) != len(columns):
            raise ValueError(f"Missing or duplicate CSV headers: {columns!r}")
        entry.update(columns=columns, column_count=len(columns))
        create_table(db, entry["sql_table"], columns)
        db.execute("DROP TABLE IF EXISTS reject_rows")
        db.execute("DROP TABLE IF EXISTS reject_scans")
        db.execute(f'INSERT INTO {quote(entry["sql_table"])} SELECT * FROM read_csv(?, '
                   "columns=?, header=true, auto_detect=false, delim=?, quote='\"', escape='\"', "
                   "strict_mode=true, null_padding=false, allow_quoted_nulls=false, parallel=false, "
                   "store_rejects=true, rejects_table='reject_rows', rejects_scan='reject_scans')",
                   [str(path), dict.fromkeys(columns, "VARCHAR"), delimiter])
        count, = db.execute(f'SELECT count(*) FROM {quote(entry["sql_table"])}').fetchone()
        rejected, = db.execute("SELECT count(DISTINCT line) FROM reject_rows").fetchone()
        examples = db.execute("SELECT line, column_name, error_type::VARCHAR, left(csv_line, 400), "
                              "left(error_message, 400) FROM reject_rows ORDER BY ALL LIMIT 8").fetchall()
        entry.update(records=count, rejected_records=rejected, rejected_examples=examples,
                     malformed=bool(rejected))
    entry.update(columns=columns, column_count=len(columns), empty_table=entry["records"] == 0,
                 status="profiled")
    return columns


def create_table(db, table, columns):
    db.execute(f"CREATE TABLE {quote(table)} (" + ", ".join(f"{quote(c)} VARCHAR" for c in columns) + ")")


def peak_rss_mib():
    if os.name != "nt":
        import resource
        divisor = 2**20 if platform.system() == "Darwin" else 1024
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / divisor, 2)
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = wintypes.HANDLE
    get_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    return round(counters.PeakWorkingSetSize / 2**20, 2) if get_info(process(), ctypes.byref(counters), counters.cb) else None


def run(source, output, memory_limit, threads):
    started, cpu = time.perf_counter(), time.process_time()
    source, output = source.resolve(), output.resolve()
    if source.is_dir() and (output == source or source in output.parents):
        raise ValueError("Reports must be outside the source dataset directory.")
    if source.is_file() and (output == source or source in output.parents):
        raise ValueError("Reports must be outside the source archive.")
    archive = source.is_file() and zipfile.is_zipfile(source)
    if not archive and not source.is_dir():
        raise ValueError("Dataset must be an extracted directory or a ZIP archive.")
    archive_hash = sha256(source) if archive else None
    result = {"inventory": [], "tables": {}, "keys": [], "failed_checks": []}
    with tempfile.TemporaryDirectory(prefix="transitkg-phase2-") as temporary:
        scratch = Path(temporary)
        paths = extract(source, scratch) if archive else files_under(source)
        if not paths:
            raise ValueError("No source files found.")
        with closing(duckdb.connect(str(scratch / "profile.duckdb"),
                                    config={"memory_limit": memory_limit, "threads": threads,
                                            "temp_directory": str(scratch / "spill"),
                                            "preserve_insertion_order": "true"})) as db:
            for index, (relative, path) in enumerate(paths.items()):
                filename = PurePosixPath(relative).name
                suffix = PurePosixPath(filename).suffix.lower()
                stem = PurePosixPath(filename).stem
                definition = SCHEMA["tables"].get(filename, {})
                entry = {"path": relative, "file_name": filename, **inventory_bytes(path),
                         "classification": "GTFS Schedule" if definition else
                         "511 extension" if stem in PROVIDER else
                         "unverified extension" if suffix in {".txt", ".csv", ".tsv"} else "ancillary",
                         "file_presence": definition.get("presence"), "records": None,
                         "column_count": None, "columns": [], "malformed": None, "status": "inventory only"}
                result["inventory"].append(entry)
                if suffix not in {".txt", ".csv", ".tsv"} and filename != "locations.geojson":
                    continue
                print(f"Inventory and profile {relative}", flush=True)
                entry["sql_table"] = f"source_{index}"
                try:
                    columns = load_table(db, filename, path, entry)
                except (duckdb.Error, csv.Error, KeyError, TypeError, ValueError) as error:
                    entry.update(status="unreadable", malformed=True, error=str(error))
                    continue
                if not columns:
                    continue
                field_specs = definition.get("fields", {}) or SCHEMA["provider_fields"].get(stem, {})
                table = {"path": relative, "sql_table": entry["sql_table"], "records": entry["records"],
                         "missing_columns": {c: value for c, value in field_specs.items() if c not in columns},
                         "columns": {}}
                result["tables"][relative] = table
                for col in columns:
                    try:
                        table["columns"][col] = column_profile(db, table, col, field_specs.get(col, {}))
                    except duckdb.Error as error:
                        result["failed_checks"].append({"check": f"{relative}.{col} profile", "error": str(error)})
                try:
                    result["keys"].extend(key_profile(db, table, columns, definition.get("key"), stem))
                except duckdb.Error as error:
                    result["failed_checks"].append({"check": f"{relative} keys", "error": str(error)})
            analyze(db, result, SCHEMA)
            db.execute("CHECKPOINT")
            database_bytes = (scratch / "profile.duckdb").stat().st_size
        after = files_under(source) if not archive else paths
        before_hashes = {item["path"]: item["sha256"] for item in result["inventory"]}
        after_hashes = {name: sha256(path) for name, path in after.items()}
        unchanged = before_hashes == after_hashes and (not archive or archive_hash == sha256(source))
    try:
        dataset_path = os.path.relpath(source, Path.cwd())
    except ValueError:
        dataset_path = str(source)
    result["source"] = {"dataset": dataset_path, "format": "ZIP" if archive else
                        "extracted directory", "archive_sha256": archive_hash, "verified_unchanged": unchanged,
                        "verification": "Complete file list and SHA-256 before and after profiling, plus archive SHA-256 if applicable."}
    present = {item["file_name"] for item in result["inventory"]}
    result["absent_standard_files"] = {name: spec["presence"] for name, spec in SCHEMA["tables"].items()
                                       if name not in present}
    result["specifications"] = {"accessed": REFERENCE_DATE, "schedule_revision": SCHEMA["revision"],
                                "reference_source_sha256": SCHEMA["source_sha256"],
                                "schedule": "https://gtfs.org/documentation/schedule/reference/",
                                "realtime": "https://gtfs.org/documentation/realtime/reference/",
                                "511_exchange": "https://511.org/media/6657/show"}
    recommendations(result)
    result["execution"] = {"wall_seconds": round(time.perf_counter() - started, 3),
                           "cpu_seconds": round(time.process_time() - cpu, 3),
                           "peak_process_rss_mib": peak_rss_mib(),
                           "scratch_database_bytes_at_checkpoint": database_bytes,
                           "python": platform.python_version(), "duckdb": duckdb.__version__,
                           "platform": platform.platform(), "duckdb_threads": threads,
                           "duckdb_memory_limit": memory_limit, "sample_limit": 5}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("dataset_inventory", {k: result[k] for k in ("inventory", "source", "specifications",
                              "absent_standard_files", "execution")}),
                        ("data_profile", result["tables"]),
                        ("data_quality_report", {k: result[k] for k in ("keys", "conditional_checks", "issues", "failed_checks")}),
                        ("referential_integrity_report", result["references"]),
                        ("identifier_analysis", result["identifiers"]),
                        ("realtime_compatibility_report", result["realtime"]),
                        ("temporal_network_graph", {k: result[k] for k in ("temporal", "network", "graph")})):
        (output / f"{name}.json").write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n",
                                             encoding="utf-8")
    (output / "phase2_dataset_exploration_report.md").write_text(render(result), encoding="utf-8")
    if not unchanged:
        raise RuntimeError("Source checksum verification failed. See dataset_inventory.json.")
    if result["failed_checks"] or any(item["status"] == "unreadable" for item in result["inventory"]):
        return 1
    return 0


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("dataset", type=Path)
    cli.add_argument("--output", type=Path, default=Path("reports/phase2"))
    cli.add_argument("--memory-limit", default="768MB")
    cli.add_argument("--threads", type=int, default=2)
    args = cli.parse_args()
    raise SystemExit(run(args.dataset, args.output, args.memory_limit, args.threads))
