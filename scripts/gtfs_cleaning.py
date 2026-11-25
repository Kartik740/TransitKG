"""Phase 3 source-row decisions and typed projections."""

from pathlib import PurePosixPath

from gtfs_checks import clean, gtfs_seconds, literal, q, query, typed_expr, value
from profile_gtfs import PROVIDER, SCHEMA

CORE = {"agency", "routes", "trips", "stops", "calendar", "calendar_dates", "shapes", "stop_times"}
FARE_KEY = ("from_leg_group_id", "to_leg_group_id", "fare_product_id", "transfer_count", "duration_limit")
PHASE2_ISSUES = {
    "P3-ATTRIBUTION": "P2-011", "P3-DIRECTION": "P2-012", "P3-FARE-TRANSFER": "P2-013",
    "P3-ROUTE-ATTRIBUTES": "P2-014", "P3-SHAPE-DISTANCE": "P2-016",
}


def field_specs(table):
    name = PurePosixPath(table["path"]).name
    return SCHEMA["tables"].get(name, {}).get("fields", SCHEMA["provider_fields"].get(PurePosixPath(name).stem, {}))


def relation(tables, name):
    return q(tables[name]["sql_table"])


def disposition(db, table, rule, action, explanation, selection, retained="NULL::BIGINT", evidence="NULL::JSON"):
    db.execute(f"""
        INSERT INTO row_exceptions
        SELECT ?, r.rowid + 1, ?, ?, ?, 'MEDIUM', ?, e.retained, e.evidence, to_json(r)
        FROM (SELECT source_record, {retained} AS retained, {evidence} AS evidence FROM ({selection}) findings) e
        JOIN {q(table["sql_table"])} r ON r.rowid + 1 = e.source_record
    """, [table["path"], action, rule, PHASE2_ISSUES[rule], explanation])


def source_exceptions(db, tables):
    db.execute("""CREATE TEMP TABLE row_exceptions (source_file VARCHAR, source_record BIGINT, disposition VARCHAR,
        cleaning_rule VARCHAR, phase2_issue VARCHAR, severity VARCHAR, explanation VARCHAR,
        retained_source_record BIGINT, evidence JSON, source_row JSON)""")
    evidence = {}
    if "attributions" in tables:
        table = tables["attributions"]
        scope_cols = ("attribution_id", "agency_id", "route_id", "trip_id")
        scope = " AND ".join(f"{clean(q(c))} IS NULL" for c in scope_cols if c in table["columns"]) or "TRUE"
        source = relation(tables, "attributions")
        grouping = ", ".join(map(q, table["columns"]))
        disposition(db, table, "P3-ATTRIBUTION", "DEDUPLICATED", "Equivalent dataset-wide attribution.",
                    f"""SELECT source_record, survivor FROM (
                        SELECT rowid + 1 AS source_record, min(rowid + 1) OVER (PARTITION BY {grouping}) AS survivor
                        FROM {source} WHERE {scope}) WHERE source_record <> survivor""", retained="survivor")
    if "route_attributes" in tables:
        table = tables["route_attributes"]
        source = relation(tables, "route_attributes")
        fields = ", ".join(map(q, table["columns"]))
        groups = f"""SELECT route_id, count(*) AS records, count(DISTINCT row({fields})) AS variants, min(rowid + 1) AS survivor
            FROM {source} WHERE {clean('route_id')} IS NOT NULL GROUP BY route_id HAVING count(*) > 1"""
        evidence["route_attribute_analysis"] = query(db, f"""SELECT count(*) AS groups,
            coalesce(sum(records), 0) AS affected_records,
            count(*) FILTER (WHERE variants > 1) AS conflicting_groups FROM ({groups})""")[0]
        marked = f"SELECT r.rowid + 1 AS source_record, g.* FROM {source} r JOIN ({groups}) g USING (route_id)"
        disposition(db, table, "P3-ROUTE-ATTRIBUTES", "DEDUPLICATED", "Exact copy of the retained route attributes.",
                    f"SELECT * FROM ({marked}) WHERE variants = 1 AND survivor <> source_record", retained="survivor")
        disposition(db, table, "P3-ROUTE-ATTRIBUTES", "QUARANTINED", "Multiple variants of the same route attributes.",
                    f"SELECT * FROM ({marked}) WHERE variants > 1")
    if "directions" in tables:
        table = tables["directions"]
        source = relation(tables, "directions")
        disposition(db, table, "P3-DIRECTION", "QUARANTINED", "Direction label key has multiple source rows.",
                    f"""SELECT rowid + 1 AS source_record FROM {source}
                        QUALIFY count(*) OVER (PARTITION BY route_id, direction_id) > 1""")
        if "trips" in tables and {"direction_id", "trip_headsign"} <= tables["trips"]["columns"].keys():
            evidence["conflicting_direction_trip_headsigns"] = query(db, f"""
                SELECT route_id, direction_id, trip_headsign, count(*) AS trips
                FROM {relation(tables, 'trips')} WHERE (route_id, direction_id) IN (
                    SELECT DISTINCT route_id, direction_id FROM {source} r JOIN row_exceptions e
                    ON e.source_record = r.rowid + 1 WHERE e.cleaning_rule = 'P3-DIRECTION')
                GROUP BY ALL ORDER BY ALL
            """)
    if "fare_transfer_rules" in tables:
        table = tables["fare_transfer_rules"]
        source = relation(tables, "fare_transfer_rules")
        key = ", ".join(map(q, FARE_KEY))
        extended = "filter_fare_product_id" in table["columns"]
        condition = f"OR {clean('filter_fare_product_id')} IS NOT NULL" if extended else ""
        disposition(db, table, "P3-FARE-TRANSFER", "QUARANTINED", "Base-key conflict or 511 extension filter requires fare interpretation.",
                    f"""SELECT rowid + 1 AS source_record FROM {source}
                        QUALIFY count(*) OVER (PARTITION BY {key}) > 1 {condition}""")
        variant_cols = ", ".join(q(c) for c in table["columns"] if c != "filter_fare_product_id")
        evidence["fare_transfer_analysis"] = query(db, f"""WITH keyed AS (
            SELECT count(*) AS records, count(DISTINCT row({variant_cols})) AS variants_without_filter
            FROM {source} GROUP BY {key} HAVING count(*) > 1)
            SELECT count(*) AS conflicting_base_keys, coalesce(sum(records),0) AS conflicting_rows,
                   count(*) FILTER (WHERE variants_without_filter > 1) AS groups_differing_beyond_filter
            FROM keyed""")[0]
        if extended:
            evidence["fare_transfer_analysis"]["distinct_keys_with_filter"] = value(
                db, f"SELECT count(DISTINCT row({key}, filter_fare_product_id)) FROM {source}")
            evidence["fare_transfer_analysis"]["referenced_filter_products_missing"] = value(db, f"""
                SELECT count(*) FROM {source} f WHERE {clean('f.filter_fare_product_id')} IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM {relation(tables, 'fare_products')} p
                     WHERE p.fare_product_id = f.filter_fare_product_id)""") if "fare_products" in tables else None
    if "shapes" in tables and "shape_dist_traveled" in tables["shapes"]["columns"]:
        table = tables["shapes"]
        source = relation(tables, "shapes")
        disposition(db, table, "P3-SHAPE-DISTANCE", "FLAGGED",
                    "Optional distance is non-increasing relative to the previous populated distance. Geometry retained.",
                    f"""SELECT * FROM (SELECT rowid + 1 AS source_record,
                       shape_dist_traveled::DOUBLE AS distance, shape_pt_lat, shape_pt_lon,
                       lag(rowid + 1) OVER w AS previous_source_record,
                       lag(shape_dist_traveled::DOUBLE) OVER w AS previous_distance,
                       lag(shape_pt_lat) OVER w AS previous_lat,
                       lag(shape_pt_lon) OVER w AS previous_lon
                       FROM {source} WHERE {clean('shape_dist_traveled')} IS NOT NULL
                       WINDOW w AS (PARTITION BY shape_id ORDER BY shape_pt_sequence::BIGINT))
                       WHERE distance <= previous_distance""",
                    evidence="""to_json(struct_pack(previous_source_record := previous_source_record,
                        previous_distance := previous_distance, distance := distance,
                        equal_distance := distance = previous_distance,
                        equal_coordinates := (shape_pt_lat::DOUBLE = previous_lat::DOUBLE
                                              AND shape_pt_lon::DOUBLE = previous_lon::DOUBLE)))""")
        evidence["shape_distance_analysis"] = query(db, """SELECT count(*) AS flagged_points,
            count(*) FILTER (WHERE evidence->>'equal_distance' = 'true') AS equal_distances,
            count(*) FILTER (WHERE evidence->>'equal_coordinates' = 'true') AS same_coordinates,
            count(DISTINCT source_row->>'shape_id') AS affected_shapes
            FROM row_exceptions WHERE cleaning_rule = 'P3-SHAPE-DISTANCE'""")[0]
    return evidence


def typed_projections(table):
    """Keep unverified fields as text and record the inputs to all typed projections."""
    specs = field_specs(table)
    projections, casts = [], {}
    for column in table["columns"]:
        name = q(column)
        spec = specs.get(column, {})
        kind = spec.get("type", "")
        expr, units = typed_expr(clean(name), kind)
        enums = spec.get("enum", [])
        numeric_enum = enums and all(item.lstrip("-").isdigit() for item in enums)
        validity = []
        target = "VARCHAR"
        if numeric_enum:
            expr, target = f"try_cast({name} AS INTEGER)", "INTEGER"
            validity = [f"{name} IN ({', '.join(map(literal, enums))})"]
        elif units == "integer":
            expr, target = f"try_cast(({expr}) AS BIGINT)", "BIGINT"
        elif units == "date":
            target = "DATE"
        elif units == "decimal":
            target = "DECIMAL(30,12)" if kind == "Currency amount" else "DOUBLE"
            validity = [f"isfinite(({expr})::DOUBLE)"]
            if kind == "Currency amount":
                validity.append(f"regexp_full_match({name}, '[+-]?([0-9]+([.][0-9]{{0,12}})?|[.][0-9]{{1,12}})')")
        elif units in {"service seconds", "wall-clock seconds"}:
            projections.append((column, name, "VARCHAR"))
            column = f"{column}_seconds"
            target = "BIGINT"
            if units == "wall-clock seconds":
                validity = [f"({expr}) <= 86400"]
        if target != "VARCHAR":
            kind_lower = kind.lower()
            for prefix, operator in (("non-negative", ">="), ("positive", ">"), ("non-zero", "<>")):
                if kind_lower.startswith(prefix):
                    validity.append(f"({expr}) {operator} 0")
            if kind in {"Latitude", "Longitude"}:
                validity.append(f"abs({expr}) <= {90 if kind == 'Latitude' else 180}")
            casts[column] = {"source": name, "expression": expr, "type": target,
                             "valid": " AND ".join([f"({expr}) IS NOT NULL", *validity])}
        else:
            expr = name
        projections.append((column, expr, target))
    return projections, casts


def project(db, table):
    projections, casts = typed_projections(table)
    reserved = ["source_record", "source_empty_typed_fields", *(column for column, _, _ in projections)]
    if len({item.casefold() for item in reserved}) != len(reserved):
        raise ValueError(f"{table['path']} contains a column collision with the normalized schema.")
    raw_table = q(table["sql_table"])
    source_file = literal(table["path"])
    accepted_from = f"""{raw_table} r WHERE NOT EXISTS (
        SELECT 1 FROM row_exceptions e WHERE e.source_file = {source_file} AND e.source_record = r.rowid + 1
        AND e.disposition IN ('DEDUPLICATED', 'QUARANTINED'))"""
    if casts:
        bad = query(db, "SELECT " + ", ".join(
            f"count(*) FILTER (WHERE {clean(c['source'])} IS NOT NULL AND NOT ({c['valid']})) AS {q(field)}"
            for field, c in casts.items()) + f" FROM {accepted_from}")[0]
        bad = {field: count for field, count in bad.items() if count}
        if bad:
            raise ValueError(f"{table['path']} contains values unsupported by the typed policy: {bad}")
    dedup_or_quarantine = query(db, """SELECT disposition, count(*) AS records FROM row_exceptions
                                      WHERE source_file = ? GROUP BY 1""", [table["path"]])
    decisions = {row["disposition"]: row["records"] for row in dedup_or_quarantine}
    empty_sources = sorted({c["source"] for field, c in casts.items() if not field.endswith("_seconds")})
    empties = ("list_filter([" + ", ".join(f"CASE WHEN {src} = '' THEN {literal(src[1:-1])} END"
                for src in empty_sources) + "], x -> x IS NOT NULL)") if empty_sources else "[]::VARCHAR[]"
    select = ", ".join(f"{expr} AS {q(column)}" for column, expr, _ in projections)
    sql = f"SELECT r.rowid + 1 AS source_record, {select}, {empties} AS source_empty_typed_fields FROM {accepted_from}"
    name = PurePosixPath(table["path"]).stem
    category = ("core canonical table" if name in CORE else "preserved extension" if name in PROVIDER or
                PurePosixPath(table["path"]).name not in SCHEMA["tables"] else "supporting normalized table")
    input_rows = table["records"]
    removed, quarantined = decisions.get("DEDUPLICATED", 0), decisions.get("QUARANTINED", 0)
    accounted = input_rows - removed - quarantined
    # Casts change the representation, while the original values and identifiers remain recoverable.
    modification_predicate = " OR ".join(f"{clean(src)} IS NOT NULL" for src in empty_sources) or "FALSE"
    modified = value(db, f"SELECT count(*) FROM {accepted_from} AND ({modification_predicate})")
    accounting = {"source_file": table["path"], "handling": category, "input_records": input_rows,
                  "accepted_normalized": accounted if category != "preserved extension" else 0,
                  "preserved_extension": accounted if category == "preserved extension" else 0,
                  "quarantined": quarantined, "deduplicated": removed, "flagged_retained": decisions.get("FLAGGED", 0),
                  "records_with_values_retyped": modified, "source_values_rewritten": 0,
                  "preserved_untyped_extension_fields": sorted(set(table["columns"]) - field_specs(table).keys()),
                  "typed_fields": {field: item["type"] for field, item in casts.items()}}
    schema = {"source_record": "BIGINT", **{column: kind for column, _, kind in projections},
              "source_empty_typed_fields": "VARCHAR[]"}
    return sql, schema, accounting
