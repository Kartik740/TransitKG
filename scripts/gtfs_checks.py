"""Exact column, key, and relationship checks for the Phase 2 profiler."""

from pathlib import PurePosixPath

def q(name):
    return '"' + name.replace('"', '""') + '"'


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def query(db, sql, params=None):
    cursor = db.execute(sql, params or [])
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def value(db, sql):
    return db.execute(sql).fetchone()[0]


def pct(n, total):
    return round(100 * n / total, 6) if total else None


def clean(expr):
    return f"NULLIF({expr}, '')"


def field(columns, name, prefix=""):
    return prefix + q(name) if name in columns else "NULL::VARCHAR"


def matches(expr, pattern):
    return f"regexp_full_match({expr}, {literal(pattern)})"


def gtfs_seconds(expr):
    return (f"CASE WHEN {matches(expr, '[0-9]+:[0-5][0-9]:[0-5][0-9]')} THEN "
            f"try_cast(try_cast(split_part({expr}, ':', 1) AS BIGINT)::HUGEINT*3600 + "
            f"try_cast(split_part({expr}, ':', 2) AS BIGINT)*60 + "
            f"try_cast(split_part({expr}, ':', 3) AS BIGINT) AS BIGINT) END")


def gtfs_date(expr):
    return f"CASE WHEN {matches(expr, '[0-9]{8}')} THEN try_strptime({expr}, '%Y%m%d')::DATE END"


def typed_expr(expr, kind):
    kind = kind.lower()
    if kind in {"time", "local time"}:
        return gtfs_seconds(expr), "service seconds" if kind == "time" else "wall-clock seconds"
    if kind == "date":
        return gtfs_date(expr), "date"
    if "integer" in kind:
        return f"CASE WHEN {matches(expr, '[+-]?[0-9]+')} THEN try_cast({expr} AS HUGEINT) END", "integer"
    if "currency amount" == kind:
        return f"try_cast({expr} AS DECIMAL(30,12))", "decimal"
    if "float" in kind or kind in {"latitude", "longitude"}:
        return f"try_cast({expr} AS DOUBLE)", "decimal"
    return expr, None


def column_profile(db, table, col, spec):
    raw = q(col)
    rel = q(table["sql_table"])
    kind = spec.get("type", "")
    identifier = "ID" in kind.split() or col.endswith(("_id", "_code")) or col in {"parent_station", "id"}
    coerced, units = typed_expr("raw", kind)
    enum = spec.get("enum")
    invalid = "FALSE"
    if units:
        invalid = "v IS NULL"
        if units == "decimal":
            invalid += " OR NOT isfinite(v::DOUBLE)"
        operators = {"non-negative": "v < 0", "positive": "v <= 0", "non-zero": "v = 0"}
        for sign, check in operators.items():
            if kind.lower().startswith(sign):
                invalid += " OR " + check
        bounds = {"Latitude": 90, "Longitude": 180, "Local time": 86400}
        if kind in bounds:
            invalid += f" OR abs(v) > {bounds[kind]}"
    elif enum:
        invalid = "raw NOT IN (" + ", ".join(map(literal, enum)) + ")"
    elif kind in {"Color", "Currency code", "URL", "Email", "Language code", "Timezone"}:
        pattern = {"Color": "[0-9a-fA-F]{6}", "Currency code": "[A-Z]{3}",
                   "URL": "https?://[^/[:space:]]+[^[:space:]]*",
                   "Email": "[^@[:space:]]+@[^@[:space:]]+",
                   "Language code": "[a-zA-Z0-9]{1,8}(-[a-zA-Z0-9]{1,8})*",
                   "Timezone": "[A-Za-z0-9_+.-]+(/[A-Za-z0-9_+.-]+)*"}[kind]
        invalid = "NOT " + matches("raw", pattern)
    padding = "regexp_matches(raw, '^[[:space:]\\p{Z}]|[[:space:]\\p{Z}]$')"
    invalid_present = f"raw IS NOT NULL AND (({invalid}) OR "
    invalid_present += "regexp_full_match(raw, '[[:space:]\\p{Z}]*'))"
    range_sql = ", min(v) FILTER (WHERE NOT bad) AS minimum, max(v) FILTER (WHERE NOT bad) AS maximum" if units else ""
    row = query(db, f"""
        WITH vals AS (
            SELECT {raw} AS original, {clean(raw)} AS raw FROM {rel}
        ), typed AS (
            SELECT *, {coerced} AS v FROM vals
        ), flags AS (
            SELECT *, ({invalid_present}) AS bad FROM typed
        )
        SELECT count(*) FILTER (WHERE original IS NULL) AS blank_or_null_fields,
               count(*) FILTER (WHERE original = '') AS empty_strings,
               count(*) FILTER (WHERE raw IS NULL) AS missing_values,
               count(DISTINCT raw) AS distinct_nonempty_values,
               count(*) FILTER (WHERE {padding}) AS leading_or_trailing_whitespace,
               count(*) FILTER (WHERE bad) AS invalid_or_unexpected_nonempty_values,
               min(raw, 5) FILTER (WHERE bad) AS invalid_examples,
               min(raw, 5) FILTER (WHERE {padding}) AS whitespace_examples,
               count(*) FILTER (WHERE {matches("raw", '[+-]?[0-9]+')}) AS integer_like,
               count(*) FILTER (WHERE {matches("raw", '[+-]?([0-9]+[.][0-9]*|[.][0-9]+)([eE][+-]?[0-9]+)?|[+-]?[0-9]+[eE][+-]?[0-9]+')}) AS other_numeric_like
               {range_sql}
        FROM flags
    """)[0]
    populated = table["records"] - row["missing_values"]
    observed = "no populated values" if not populated else "string identifiers" if identifier else (
        units if units and not row["invalid_or_unexpected_nonempty_values"] else
        "integer-looking strings" if row["integer_like"] == populated else
        "numeric-looking strings" if row["integer_like"] + row["other_numeric_like"] == populated else "strings")
    row["sample_values"] = [item[0] for item in db.execute(
        f"SELECT DISTINCT {raw} FROM {rel} WHERE {clean(raw)} IS NOT NULL ORDER BY 1 LIMIT 5").fetchall()] if populated else []
    row.update(expected_type=kind or None, specification_presence=spec.get("presence"),
               stored_type="VARCHAR", observed_type=observed,
               missing_percentage=pct(row["missing_values"], table["records"]),
               identifier_field=identifier, potential_relationships=spec.get("references", []),
               invalid_required_missing=(row["missing_values"] if not spec.get("empty_default") else 0)
               if spec.get("presence") == "Required" else None)
    return row


def key_profile(db, table, columns, spec_key, stem):
    key = spec_key or {"directions": ["route_id", "direction_id"], "route_attributes": ["route_id"],
                       "calendar_attributes": ["service_id"], "mtc_feed_versions": ["agency_id"]}.get(stem)
    if not key:
        key = ["*"]
    basis = "GTFS Schedule" if spec_key else "candidate from 511 documentation" if stem == "directions" else "candidate"
    actual = list(columns) if key == ["*"] else [] if key == ["none"] else key
    optional = {"agency": {"agency_id"}, "attributions": {"attribution_id"},
                "fare_products": {"fare_media_id", "rider_category_id"},
                "fare_leg_rules": set(actual) - {"fare_product_id"},
                "fare_leg_join_rules": {"from_stop_id", "to_stop_id"},
                "fare_transfer_rules": set(actual), "transfers": set(actual), "translations": set(actual) - {
                    "table_name", "field_name", "language"}, "timeframes": {"start_time", "end_time"},
                "fare_rules": set(actual) - {"fare_id"}}.get(stem, set())
    base = {"file": table["path"], "key_columns": actual, "basis": basis,
            "singleton_constraint": key == ["none"], "total_records": table["records"],
            "optional_key_components": sorted(optional)}
    absent_required = sorted(set(actual) - set(columns) - optional)
    if absent_required:
        return [{**base, "status": "missing key columns", "missing_columns": absent_required,
                 "missing_required_key_records": table["records"]}]
    exprs = []
    for col in actual:
        expr = clean(field(columns, col))
        if col in {"stop_sequence", "shape_pt_sequence", "direction_id", "transfer_count", "duration_limit"}:
            parsed, _ = typed_expr(expr, "Integer")
            expr = f"coalesce(({parsed})::VARCHAR, {expr})"
        if col in {"start_time", "end_time"}:
            expr = f"coalesce(({gtfs_seconds(expr)})::VARCHAR, {expr})"
        exprs.append(expr)
    aliases = [f"k{i}" for i in range(len(exprs))]
    missing = " OR ".join(f"{a} IS NULL" for a, c in zip(aliases, actual) if c not in optional) or "FALSE"
    blanks = " OR ".join(f"{a} IS NULL" for a in aliases) or "FALSE"
    comparable = f"NOT ({blanks})" if stem == "attributions" else f"NOT ({missing})"
    keys = ", ".join(aliases) or "k0"
    select_keys = ", ".join(f"{e} AS {a}" for e, a in zip(exprs, aliases)) or "1 AS k0"
    rel = q(table["sql_table"])
    grouped = f"SELECT {keys}, count(*) AS n FROM (SELECT {select_keys} FROM {rel}) GROUP BY {keys}"
    metrics = query(db, f"""
        SELECT count(*) AS observed_key_tuples_including_missing,
               count(*) FILTER (WHERE {comparable}) AS unique_complete_keys,
               coalesce(sum(n) FILTER (WHERE {missing}), 0) AS missing_required_key_records,
               coalesce(sum(n) FILTER (WHERE {blanks}), 0) AS records_with_any_blank_key_component,
               count(*) FILTER (WHERE n > 1 AND {comparable}) AS duplicate_complete_key_groups,
               coalesce(sum(n) FILTER (WHERE n > 1 AND {comparable}), 0) AS records_with_duplicate_complete_keys,
               coalesce(sum(n-1) FILTER (WHERE n > 1 AND {comparable}), 0) AS excess_duplicate_complete_key_records,
               count(*) FILTER (WHERE n > 1) AS groups_to_inspect
        FROM ({grouped})
    """)[0]
    exact, conflicts, conflict_records, examples = 0, 0, 0, []
    if metrics.pop("groups_to_inspect"):
        join = " AND ".join(f"a.{alias} IS NOT DISTINCT FROM b.{alias}" for alias in keys.split(", "))
        packed = "row(" + ", ".join(q(c) for c in columns) + ")"
        variants = (f"WITH dup AS (SELECT * FROM ({grouped}) WHERE n > 1), "
                    f"src AS (SELECT {select_keys}, {packed} AS raw_record FROM {rel}) "
                    f"SELECT {', '.join('a.' + a for a in keys.split(', '))}, "
                    f"count(*) AS n, count(DISTINCT raw_record) AS variants FROM src a JOIN dup b ON {join} "
                    f"GROUP BY {', '.join('a.' + a for a in keys.split(', '))}")
        exact, conflicts, conflict_records = db.execute(f"""
            SELECT coalesce(sum(n-variants), 0), count(*) FILTER (WHERE variants > 1 AND {comparable}),
                   coalesce(sum(n) FILTER (WHERE variants > 1 AND {comparable}), 0)
            FROM ({variants})
        """).fetchone()
        examples = query(db, f"SELECT * FROM ({variants}) ORDER BY variants DESC, n DESC, {keys} LIMIT 5")
        for example in examples:
            params = [example[f"k{i}"] for i in range(len(actual))] or [1]
            predicates = " AND ".join(f"{item} IS NOT DISTINCT FROM ?" for item in keys.split(", "))
            example["raw_row_examples"] = query(db, f"SELECT DISTINCT {', '.join(map(q, columns))} "
                f"FROM (SELECT *, {select_keys} FROM {rel}) WHERE {predicates} ORDER BY ALL LIMIT 2", params)
    return [{**base, **metrics, "status": "checked", "exact_duplicate_excess_rows": exact,
             "conflicting_duplicate_groups": conflicts, "records_in_conflicting_groups": conflict_records,
             "duplicate_examples": examples}]


def service_domain(db, tables):
    pieces = []
    for name in ("calendar", "calendar_dates"):
        table = tables.get(name)
        if not table or "service_id" not in table["columns"]:
            continue
        where = "NULLIF(service_id, '') IS NOT NULL"
        if name == "calendar_dates":
            if not {"exception_type", "date"} <= table["columns"].keys():
                continue
            where += f" AND exception_type = '1' AND {gtfs_date('date')} IS NOT NULL"
        pieces.append(f'SELECT service_id FROM {q(table["sql_table"])} WHERE {where}')
    db.execute("CREATE TEMP VIEW service_ids AS " + (" UNION ".join(pieces) or "SELECT NULL::VARCHAR AS service_id WHERE false"))


def reference_profile(db, table, column, domain, targets, required="FALSE", target_filter="TRUE"):
    name = q(table["sql_table"])
    expr = clean(field(table["columns"], column, "s."))
    metrics = query(db, f"""
        WITH source AS (SELECT {expr} AS identifier, ({required}) IS TRUE AS required FROM {name} s),
        target AS ({domain}),
        checked AS (SELECT identifier, required, EXISTS (SELECT 1 FROM target t
                   WHERE target_id = identifier AND ({target_filter})) AS matched FROM source)
        SELECT count(*) AS source_records,
               count(identifier) AS populated_references,
               count(*) FILTER (WHERE identifier IS NOT NULL AND matched) AS valid_references,
               count(*) FILTER (WHERE identifier IS NOT NULL AND NOT matched) AS invalid_references,
               count(*) FILTER (WHERE identifier IS NULL AND required) AS missing_required_references,
               count(*) FILTER (WHERE identifier IS NULL AND NOT required) AS blank_optional_references
        FROM checked
    """)[0]
    examples = []
    if metrics["invalid_references"]:
        examples = query(db, f"""SELECT {expr} AS identifier, count(*) AS records
            FROM {name} s WHERE {expr} IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM ({domain}) t WHERE target_id = {expr} AND ({target_filter}))
            GROUP BY 1 ORDER BY records DESC, identifier LIMIT 5""")
    if metrics["missing_required_references"]:
        metrics["missing_required_examples"] = query(db, f"SELECT * FROM {name} s WHERE {expr} IS NULL "
                                                     f"AND ({required}) IS TRUE ORDER BY ALL LIMIT 5")
    applicable = metrics["populated_references"] + metrics["missing_required_references"]
    return {"file": table["path"], "field": column, "target": targets, **metrics,
            "source_column_present": column in table["columns"], "applicable_references": applicable,
            "orphan_records": metrics["invalid_references"],
            "integrity_percentage": pct(metrics["valid_references"], applicable), "invalid_examples": examples}


def references(db, tables, schema):
    results = []
    def src_field(name, column):
        return clean(field(tables[name]["columns"], column, "s."))

    agency_count = tables.get("agency", {}).get("records", 0)
    for name, table in tables.items():
        specs = schema["tables"].get(PurePosixPath(table["path"]).name, {}).get("fields", {})
        specs = specs or schema["provider_fields"].get(name, {})
        relations = {col: spec["references"] for col, spec in specs.items() if spec.get("references")}
        if name in {"calendar_dates", "mtc_feed_versions"}:
            relations.update({"service_id": ["service_ids.service_id"]} if name == "calendar_dates" else
                             {"agency_id": ["agency.agency_id"]})
        for column, targets in relations.items():
            if column == "service_id" and name in {"trips", "timeframes"}:
                targets = ["service_ids.service_id"]
            if targets == ["locations.geojson"]:
                targets = ["locations.id"]
            required = "TRUE" if specs.get(column, {}).get("presence") == "Required" or name == "mtc_feed_versions" else "FALSE"
            if name in {"routes", "fare_attributes"} and column == "agency_id":
                required = str(agency_count > 1).upper()
            if name == "calendar_dates":
                # Type-1 dates can create a service. A removal-only ID needs an existing service.
                required = "TRUE"
            if name == "stop_times" and column == "stop_id":
                required = f"{src_field(name, 'location_group_id')} IS NULL AND {src_field(name, 'location_id')} IS NULL"
            if name == "stops" and column == "parent_station":
                required = f"{src_field(name, 'location_type')} IN ('2','3','4')"
            if name == "transfers":
                if column.endswith("_stop_id"):
                    required = f"coalesce({src_field(name, 'transfer_type')}, '0') IN ('0','1','2','3')"
                if column.endswith("_trip_id"):
                    required = f"{src_field(name, 'transfer_type')} IN ('4','5')"
            if name == "fare_leg_join_rules" and column.endswith("_stop_id"):
                other = "from_stop_id" if column == "to_stop_id" else "to_stop_id"
                required = f"{src_field(name, other)} IS NOT NULL"
            if name == "trips" and column == "shape_id":
                tests = []
                for related, join_column in (("routes", "route_id"), ("stop_times", "trip_id")):
                    if related not in tables or join_column not in tables[related]["columns"]:
                        continue
                    controls = [field(tables[related]["columns"], f"continuous_{c}", "r.") for c in ("pickup", "drop_off")]
                    tests.append(f"EXISTS (SELECT 1 FROM {q(tables[related]['sql_table'])} r WHERE "
                                 f"r.{join_column} = {src_field(name, join_column)} AND "
                                 f"({controls[0]} IN ('0','2','3') OR {controls[1]} IN ('0','2','3')))")
                required = " OR ".join(tests) or "FALSE"
            target_sql = []
            for target in targets:
                target_name, target_col = target.split(".")
                if target_name == "service_ids":
                    target_sql.append("SELECT service_id AS target_id, NULL::VARCHAR AS location_type FROM service_ids")
                elif target_name in tables and target_col in tables[target_name]["columns"]:
                    location_type = field(tables[target_name]["columns"], "location_type")
                    target_sql.append(f"SELECT {clean(q(target_col))} AS target_id, {location_type} AS location_type "
                                      f"FROM {q(tables[target_name]['sql_table'])}")
            if not target_sql:
                target_sql = ["SELECT NULL::VARCHAR AS target_id, NULL::VARCHAR AS location_type WHERE FALSE"]
            valid_type = "TRUE"
            if name == "stop_times" and column == "stop_id":
                valid_type = "coalesce(NULLIF(t.location_type, ''), '0') = '0'"
            if name == "pathways" and column in {"from_stop_id", "to_stop_id"}:
                valid_type = "coalesce(NULLIF(t.location_type, ''), '0') IN ('0','2','3','4')"
            if name in {"transfers", "fare_leg_join_rules"} and column in {"from_stop_id", "to_stop_id"}:
                valid_type = "coalesce(NULLIF(t.location_type, ''), '0') IN ('0','1')"
            try:
                results.append(reference_profile(db, table, column, " UNION ALL ".join(target_sql),
                                                 targets, required, valid_type))
            except Exception as error:
                results.append({"file": table["path"], "field": column, "target": targets,
                                "status": "failed", "error": str(error)})
    return results


def analyze(db, result, schema):
    from gtfs_network import conditional_checks, identifier_analysis, network_analysis, realtime_analysis, temporal_analysis

    tables = {}
    for path, table in result["tables"].items():
        name = PurePosixPath(path).stem
        if name in tables:
            result["failed_checks"].append({"check": f"cross-table analysis for {path}",
                                            "error": f"Ambiguous table basename. Using {tables[name]['path']}."})
            continue
        tables[name] = table
    service_domain(db, tables)
    result["references"] = references(db, tables, schema)
    result["failed_checks"].extend({"check": f"{r['file']}.{r['field']} reference", "error": r["error"]}
                                   for r in result["references"] if r.get("status") == "failed")
    for label, function in (("conditional_checks", conditional_checks),
                            ("network", network_analysis), ("identifiers", identifier_analysis),
                            ("temporal", temporal_analysis), ("realtime", realtime_analysis)):
        print(f"Run {label} analysis", flush=True)
        try:
            result[label] = function(db, tables)
        except Exception as error:
            result[label] = {"status": "failed", "reason": str(error)}
            result["failed_checks"].append({"check": label, "error": str(error)})


def recommendations(result):
    from gtfs_report import graph_assessment, issues

    result["graph"] = graph_assessment(result)
    result["issues"] = issues(result)
