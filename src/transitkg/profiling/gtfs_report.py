"""Issue classification and generated Phase 2 research summary."""

from collections import Counter


def issues(result):
    findings = []

    def add(severity, file, field, count, examples, graph, grounding, handling):
        findings.append({"issue_id": f"P2-{len(findings) + 1:03}", "severity": severity,
                         "file": file, "field_or_relationship": field, "affected_records": count,
                         "examples": examples or [], "graph_impact": graph, "grounding_impact": grounding,
                         "phase3_handling": handling})

    core = {"agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "calendar.txt", "calendar_dates.txt"}
    for item in result["inventory"]:
        file = item["path"]
        if item["malformed"] or item["empty_file"]:
            add("CRITICAL" if item["file_name"] in core and item["status"] != "profiled" else "MEDIUM",
                file, "CSV structure or encoding", item.get("rejected_records"),
                item.get("rejected_examples") or [item.get("error", item["status"])],
                "Incomplete source records.", "Potentially incomplete identity or schedule coverage.",
                "Obtain a corrected source. Preserve rejected records and source provenance.")
    for file, presence in result["absent_standard_files"].items():
        if presence == "Required":
            add("CRITICAL", file, "file presence", None, [], "A mandatory source table is absent.",
                "Identity or corresponding associations cannot be completed.", "Obtain the missing table.")
    present = {item["file_name"] for item in result["inventory"]}
    if not present.intersection({"calendar.txt", "calendar_dates.txt"}):
        add("CRITICAL", "calendar.txt / calendar_dates.txt", "service dates", None, [],
            "No source for service-date relationships.", "Trip dates cannot be verified.", "Obtain a service calendar.")
    if not present.intersection({"stops.txt", "locations.geojson"}):
        add("CRITICAL", "stops.txt / locations.geojson", "location file presence", None, [],
            "The required static service-location domain is absent.", "Stop selectors lack a static domain.",
            "Obtain the service-location source.")
    for name, table in result["tables"].items():
        severity = "HIGH" if name in core else "MEDIUM"
        for field, spec in table["missing_columns"].items():
            if spec["presence"] == "Required":
                add(severity, name, field, table["records"], [], "Required column missing.",
                    "Associated static evidence is unavailable.", "Confirm the publishing schema and retrieve the field.")
        for field, col in table["columns"].items():
            missing = col["invalid_required_missing"]
            invalid = col["invalid_or_unexpected_nonempty_values"]
            if missing or invalid:
                add(severity if field.endswith("_id") or col["specification_presence"] == "Required" else "MEDIUM",
                    name, field, missing + invalid if missing else invalid, col["invalid_examples"],
                    "Source values fail configured syntax, range, domain or required-presence checks.",
                    "Selectors or schedules using the affected values need review.",
                    "Check the applicable specification and provider conventions before defining a typed projection.")
            if col["leading_or_trailing_whitespace"]:
                add("HIGH" if col["identifier_field"] else "LOW", name, field,
                    col["leading_or_trailing_whitespace"], col["whitespace_examples"],
                    "Whitespace can change identifier equality or presentation.",
                    "ID whitespace can prevent exact resolution.",
                    "Keep raw values and audit downstream normalization for collisions before applying it.")
            if col["expected_type"] is None and table["records"] > col["missing_values"]:
                known_extensions = {"directions.txt", "route_attributes.txt", "calendar_attributes.txt"}
                if name not in known_extensions:
                    add("INFORMATIONAL", name, field, table["records"] - col["missing_values"], col["sample_values"],
                        "Field semantics or type are unverified in the selected references.",
                        "No verified selector crosswalk has been established from this field.",
                        "Preserve as opaque source data pending provider documentation.")
    for key in result["keys"]:
        duplicates = key.get("records_with_duplicate_complete_keys", 0)
        missing = key.get("missing_required_key_records", 0)
        exact = key.get("exact_duplicate_excess_rows", 0)
        if duplicates or missing or exact:
            add("HIGH" if key["file"] in core else "MEDIUM", key["file"],
                " + ".join(key["key_columns"]) or "single row", duplicates + missing or exact,
                key.get("duplicate_examples"), "A graph load could conflate records or create repeated edges.",
                "A selector can resolve to multiple differing records for a shared key.",
                "Investigate conflicting variants and document an auditable duplicate policy before graph loads.")
    for ref in result["references"]:
        if ref.get("invalid_references", 0) or ref.get("missing_required_references", 0):
            add("HIGH" if ref["file"] in core else "MEDIUM", ref["file"], ref["field"] + " → " + " or ".join(ref["target"]),
                ref["invalid_references"] + ref["missing_required_references"],
                ref["invalid_examples"] + ref.get("missing_required_examples", []),
                "A required or populated association cannot be joined to a valid target.",
                "A compound selector can lose a path through this relationship.",
                "Investigate source records and preserve unresolved associations for explicit review.")
    for check in result["conditional_checks"] if isinstance(result["conditional_checks"], list) else []:
        if check["affected_records"]:
            add(check["severity_if_present"], check["file"], check["check"], check["affected_records"],
                check["examples"], "A field or scoped combination fails a documented condition.",
                "Related relationships or selector context may be incomplete.",
                "Check the producer's source and repair the relationship only with supporting evidence.")
    temporal = result["temporal"].get("stop_times", {})
    for metric, example_key, severity, description in (
        ("missing_required_arrivals", "missing_required_time_examples", "HIGH", "Required arrival is blank."),
        ("missing_required_departures", "missing_required_time_examples", "HIGH", "Required departure is blank."),
        ("departures_before_arrival", "time_order_examples", "HIGH", "Departure precedes arrival."),
        ("arrivals_before_previous_departure", "time_order_examples", "HIGH", "Arrival precedes the previous scheduled departure."),
        ("decreasing_stop_distances", "distance_examples", "MEDIUM", "Stop-time distance decreases in sequence order."),
        ("invalid_location_selector_count", "location_selector_examples", "HIGH", "A visit requires exactly one location representation."),
        ("mixed_times_and_windows", "", "HIGH", "A visit includes scheduled times and pickup/drop-off windows."),
        ("missing_required_window_endpoint", "", "HIGH", "A required pickup/drop-off window endpoint is blank."),
        ("rows_out_of_numeric_order_in_source", "", "INFORMATIONAL", "Numeric order differs from physical row order."),
        ("arrivals_above_24h", "after_midnight_examples", "INFORMATIONAL", "Service-day arrivals extend past 24:00:00."),
    ):
        if temporal.get(metric):
            add(severity, "stop_times.txt", metric, temporal[metric], temporal.get(example_key),
                description, "Schedule evidence depends on service day and numeric stop sequence.",
                "Preserve service-day seconds and review ordering or presence exceptions.")
    network = result["network"]
    for area, metric, file, severity, impact in (
        ("parent_station", "wrong_parent_type", "stops.txt", "HIGH", "Wrong parent location type for the child."),
        ("parent_station", "station_with_forbidden_parent", "stops.txt", "HIGH", "A station has a parent."),
        ("parent_station", "self_parent", "stops.txt", "HIGH", "Self-referencing station hierarchy."),
        ("coordinates", "missing_required_coordinates", "stops.txt", "MEDIUM", "Required coordinates are incomplete."),
        ("shape_consistency", "decreasing_distances", "shapes.txt", "MEDIUM", "Distance decreases in shape sequence order."),
        ("trip_visit_coverage", "without_visits", "trips.txt", "HIGH", "Trip has no stop-time records."),
        ("trip_visit_coverage", "with_one_visit", "trips.txt", "HIGH", "Trip has only one stop-time record."),
    ):
        details = network.get(area, {})
        if details.get(metric):
            add(severity, file, metric, details[metric], details.get("examples") or details.get("missing_required_examples"),
                impact, "Hierarchy, coverage or location evidence can be incomplete.",
                "Review source relationships and retain valid independent identity evidence.")
    if result["temporal"].get("calendar", {}).get("reversed_ranges"):
        add("HIGH", "calendar.txt", "start_date, end_date", result["temporal"]["calendar"]["reversed_ranges"], [],
            "Weekly service has a reversed date range.", "Trip date groundings can be lost.",
            "Verify the source date range before expanding service dates.")
    shape = network.get("shape_consistency", {})
    if shape.get("equal_distances"):
        add("MEDIUM", "shapes.txt", "shape_dist_traveled", shape["equal_distances"], shape["equal_examples"],
            "Consecutive populated shape distances are equal.",
            "Distance-based mapping can have more than one candidate point.",
            "Inspect repeated points and distance precision before using shape offsets.")
    if network.get("multi_agency_stops", {}).get("count"):
        shared = network["multi_agency_stops"]
        add("INFORMATIONAL", "stop_times.txt", "agency-stop service", shared["count"], shared["examples"][:5],
            "A stop may be linked to several agencies.", "A stop and agency selector must share a valid service path.",
            "Keep a many-to-many service association.")
    for check in result["failed_checks"]:
        add("HIGH", None, check["check"], None, [check["error"]], "This check was incomplete.",
            "Confidence in the affected claim is limited.", "Rerun the failed check after investigating the error.")
    return findings


def graph_assessment(result):
    table = {item["file_name"]: item for item in result["inventory"] if item["status"] == "profiled"}
    network, time = result["network"], result["temporal"]

    def rows(file):
        return table.get(file, {}).get("records")

    def distinct(file, column):
        return result["tables"].get(table.get(file, {}).get("path"), {}).get("columns", {}).get(
            column, {}).get("distinct_nonempty_values")

    def valid(file, column):
        return next((item.get("valid_references") for item in result["references"]
                     if item["file"] == table.get(file, {}).get("path") and item["field"] == column), None)

    entities = [
        ("Agency", "agency.txt", "agency_id", distinct("agency.txt", "agency_id"), "Agency selector scope and schedule time zone."),
        ("Route", "routes.txt", "route_id", distinct("routes.txt", "route_id"), "Route, type and agency scope."),
        ("Trip", "trips.txt", "trip_id", distinct("trips.txt", "trip_id"), "Scheduled trip template for trip descriptors."),
        ("Stop", "stops.txt", "stop_id", distinct("stops.txt", "stop_id"), "Stop selectors and station hierarchy."),
        ("Service", "calendar.txt and calendar_dates.txt", "service_id", time.get("service_id_domain"),
         "Service-day grounding via base and exception schedules."),
        ("ServiceException", "calendar_dates.txt", "service_id + date", rows("calendar_dates.txt"), "Added/removed service per date."),
        ("Shape", "shapes.txt", "shape_id", distinct("shapes.txt", "shape_id"), "Optional spatial support for route and trip context."),
        ("FeedVersion", "source manifest and feed_info.txt", "version provenance and source checksum manifest", 1,
         "Identity provenance and selection of a valid static snapshot.") if table else
        ("FeedVersion", "source manifest", "checksum manifest", 1, "Static snapshot provenance."),
    ]
    paths = [
        ("Agency OPERATES Route", "routes.txt + agency.txt", "routes.agency_id = agency.agency_id", "1:N", "direct",
         valid("routes.txt", "agency_id"), "Preserve agency scope for route selectors.", "Single-agency omission may require inference."),
        ("Route HAS_TRIP Trip", "trips.txt + routes.txt", "trips.route_id = routes.route_id", "1:N", "direct",
         valid("trips.txt", "route_id"), "Route and direction scope.", "Inspect orphan routes and trip key conflicts."),
        ("Trip RUNS_ON Service", "trips.txt + calendar.txt + calendar_dates.txt", "trips.service_id = service domain",
         "N:1", "direct", valid("trips.txt", "service_id"), "Resolve trip instances on a service date.", "Combine weekly dates with exceptions."),
        ("Trip VISITS_STOP Stop", "stop_times.txt + trips.txt + stops.txt", "trip_id and stop_id, with stop_sequence per visit",
         "N:M", "direct", network.get("verified_platform_visit_keys"), "Scope affected stops to trips and preserve repeated visits.",
         "Check duplicate sequence keys and Flex alternatives. This count is distinct raw visit tuples with joinable conventional stops."),
        ("Route SERVES_STOP Stop", "routes.txt + trips.txt + stop_times.txt + stops.txt", "route_id → trip_id → stop_id",
         "N:M", "derived", network.get("route_stop_pairs"), "Fast route-stop selector intersection.", "Keep time validity and original visits as provenance."),
        ("Agency SERVES_STOP Stop", "agency.txt + routes.txt + trips.txt + stop_times.txt + stops.txt",
         "agency_id → route_id → trip_id → stop_id", "N:M", "derived", network.get("agency_stop_pairs"),
         "Agency-scoped stop selectors.", "Service association does not express agency ownership."),
        ("Stop PART_OF Stop", "stops.txt", "stops.parent_station = parent.stop_id", "N:1", "direct",
         valid("stops.txt", "parent_station"), "Station versus platform selector scope.", "Membership type matters. Count joins by ID before checking parent type."),
        ("Stop NEXT_STOP Stop", "stop_times.txt + stops.txt", "next numeric stop_sequence within trip_id", "N:M",
         "derived", network.get("next_stop_pairs", {}).get("distinct_directed_pairs"),
         "Direction and segment context.", "Preserve route, trip, direction and occurrence context for loops."),
        ("Stop TRANSFERS_TO Stop", "transfers.txt + stops.txt", "from_stop_id and to_stop_id = stops.stop_id",
         "N:M", "direct policy", rows("transfers.txt"), "Context for station disruptions.",
         "Raw rule count is an upper bound for stop-to-stop edges. Rules can be forbidden or trip/route-scoped."),
        ("Service HAS_EXCEPTION ServiceException", "calendar_dates.txt", "service_id with date as exception key",
         "1:N", "direct", valid("calendar_dates.txt", "service_id"), "Account for additions and removed service.",
         "Review duplicate service dates and removal-only IDs."),
        ("Trip FOLLOWS Shape", "trips.txt + shapes.txt", "trips.shape_id = shapes.shape_id", "N:1", "direct",
         valid("trips.txt", "shape_id"), "Spatial context for disruptions affecting route segments.",
         "Assess shape coverage and sequence/distance consistency."),
    ]
    visits = rows("stop_times.txt") or 0
    return {
        "entities": [dict(zip(("entity", "sources", "identifiers", "estimate", "grounding"), item),
                          cardinality="one node per identifier in the scoped snapshot",
                          representation="derived provenance" if item[0] == "FeedVersion" else
                          "combined domain" if item[0] == "Service" else "direct",
                          limitations={"Agency": "Agency IDs are scoped to their feed.",
                                       "Route": "A short name can repeat across agencies and across route variants.",
                                       "Trip": "A template can recur on many service dates.",
                                       "Stop": "Shared names and coordinates do not establish identity.",
                                       "Service": "Inspect IDs with no effective service date.",
                                       "ServiceException": "Raw record count includes any unresolved key conflicts.",
                                       "Shape": "One shape has many ordered source points.",
                                       "FeedVersion": "Regional version dates may be missing."}[item[0]])
                     for item in entities],
        "relationships": [dict(zip(("relationship", "sources", "join", "cardinality", "representation",
                                    "estimate", "grounding", "limitations"), item)) for item in paths],
        "stop_time_options": [
            {"option": "Visit nodes", "size": f"Up to {visits:,} visit nodes and {2 * visits:,} linking edges before validity filtering.",
             "tradeoff": "Supports visit-level provenance and traversals, with higher graph write, index and working-set costs."},
            {"option": "Visit relationship properties", "size": f"Up to {visits:,} sequence-aware Trip–Stop visit edges.",
             "tradeoff": "Direct trip traversal with times on the edge. A multiedge representation must preserve loops and visit order."},
            {"option": "External DuckDB or Parquet timetable", "size": f"{visits:,} timetable rows plus graph connectivity projections.",
             "tradeoff": "Column pruning and batch filtering support timetable queries with fewer graph objects. "
                         "Cross-store lookups and provenance alignment add complexity for per-visit queries."}],
        "recommendation": "Benchmark an external timetable and graph connectivity as the initial design. "
                          "Compare bulk load time, storage, p50/p95 selector and timetable latency, and incremental update cost "
                          "against visit properties and visit nodes using the same records and queries.",
    }


def render(r):
    inventory, source, execution = r["inventory"], r["source"], r["execution"]
    network, temporal, rt = r["network"], r["temporal"], r["realtime"]
    files = {row["file_name"]: row for row in inventory}
    severe = [item for item in r["issues"] if item["severity"] in {"CRITICAL", "HIGH"}]
    review = [item for item in r["issues"] if item["severity"] != "INFORMATIONAL"]
    severity = Counter(item["severity"] for item in r["issues"])
    counts = [f"{files[name]['records']:,} {label}" for name, label in
              (("agency.txt", "agency records"), ("routes.txt", "routes"), ("trips.txt", "trips"),
               ("stops.txt", "stop and station records"), ("stop_times.txt", "stop-time rows"))
              if name in files and files[name]["records"] is not None]
    line = ["# TransitKG Phase 2 dataset exploration", "", "## Executive summary", "",
            f"The {source['format']} contains **{len(inventory):,} files** and **{len(r['tables']):,} profiled tables**. "
            f"The run recorded **{len(severe)} critical or high-severity findings** and **{len(r['failed_checks'])} failed checks**. "
            f"Source checksum verification {'passed' if source['verified_unchanged'] else 'failed'}. "
            f"The AC/55660 selector outcome is **{rt.get('classification') or 'unavailable'}**.",
            "", "Core source counts include " + ", ".join(counts) + "." if counts else "",
            "", "## Dataset overview and complete file inventory", "",
            f"Source input is `{source['dataset']}`. Inventoried files total **{sum(row['bytes'] for row in inventory):,} bytes**. "
            f"GTFS Schedule revision **{r['specifications']['schedule_revision']}** and GTFS Realtime were consulted on "
            f"**{r['specifications']['accessed']}**. [GTFS Schedule reference]({r['specifications']['schedule']}) "
            f"[GTFS Realtime reference]({r['specifications']['realtime']}) [511 Data Exchange specification]({r['specifications']['511_exchange']})",
            "", "| Source path | Size and bytes | Records | Columns | Encoding | Classification and file presence | Status |",
            "|---|---:|---:|---:|---|---|---|"]
    for entry in inventory:
        name, nrows, ncols = entry["path"], entry["records"], entry["column_count"]
        state = "malformed" if entry["malformed"] else "empty" if entry["empty_file"] or entry.get("empty_table") else entry["status"]
        line.append(f"| {name} | {entry['size']} ({entry['bytes']:,}) | "
                    f"{format(nrows, ',') if nrows is not None else '—'} | {ncols if ncols is not None else '—'} | "
                    f"{entry['encoding'] or 'undetermined'} | {entry['classification']} / {entry['file_presence'] or 'outside standard'} | {state} |")
    line += ["", "Full headers, SHA-256 values, line counts, rejection counts and absent standard files are in "
             "[dataset_inventory.json](dataset_inventory.json). Ancillary documents are inventoried as files. Their physical "
             "lines are available in the inventory.", "",
             "## Table and column statistics", "",
             f"The profiler produced column statistics for **{sum(len(t['columns']) for t in r['tables'].values())} fields**. "
             "Raw fields are VARCHAR. Typed date, time and numeric views supply the reported ranges. Representative values "
             "are selected deterministically, with populated-value samples ordered lexically.",
             "", "| Table | Empty columns | Columns with missing values | Columns with invalid or unexpected nonempty values |",
             "|---|---:|---:|---:|"]
    for name, table in r["tables"].items():
        columns = table["columns"].values()
        line.append(f"| {name} | {sum(t['missing_values'] == table['records'] for t in columns)} | "
                    f"{sum(bool(t['missing_values']) for t in columns)} | "
                    f"{sum(bool(t['invalid_or_unexpected_nonempty_values']) for t in columns)} |")
    line += ["", "## Missing-value analysis", "",
             "Unquoted blanks and quoted empty strings are counted separately in "
             "[data_profile.json](data_profile.json). Short or malformed CSV rows are rejected and counted in the inventory. "
             "Missing columns have their own entries. Required fields, defaulted empties and optional fields follow the "
             "recorded field presence. Conditional checks are documented with the corresponding relationship or temporal results.",
             "", "## Duplicate and key analysis", "",
             "| Source | Key | Complete distinct keys | Missing required key | Duplicate groups | Exact excess rows | Conflicting groups |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for k in r["keys"]:
        line.append(f"| {k['file']} | {' + '.join(k['key_columns']) or 'single-row constraint'} | "
                    f"{k.get('unique_complete_keys', '—')} | {k.get('missing_required_key_records', '—')} | "
                    f"{k.get('duplicate_complete_key_groups', '—')} | {k.get('exact_duplicate_excess_rows', '—')} | "
                    f"{k.get('conflicting_duplicate_groups', '—')} |")
    refs = [item for item in r["references"] if "valid_references" in item]
    line += ["", "Duplicate groups count repeated keys. Exact excess rows count redundant copies of the same parsed row. "
             "A conflicting group has multiple distinct row variants under the same key. "
             "[Key details and examples](data_quality_report.json)", "",
             "## Referential integrity", "",
             f"Checked **{len(refs)} relationships**. Integrity uses valid populated references divided by populated references "
             "plus missing required references. Blank optional references are visible in the detailed results. The service ID "
             "domain combines base calendar IDs and valid exception additions.",
             "", "| Reference | Applicable | Valid | Invalid | Missing required | Integrity % |",
             "|---|---:|---:|---:|---:|---:|"]
    for item in refs:
        line.append(f"| {item['file']}.{item['field']} → {' or '.join(item['target'])} | {item['applicable_references']:,} | "
                    f"{item['valid_references']:,} | {item['invalid_references']:,} | {item['missing_required_references']:,} | "
                    f"{item['integrity_percentage'] if item['integrity_percentage'] is not None else '—'} |")
    line += ["", "[Referential integrity counts and examples](referential_integrity_report.json)", "",
             "## Agency and identifier analysis", "",
             "| Domain | Observed formats and distinct IDs | Collisions after stripping a leading prefix |",
             "|---|---|---:|"]
    for domain, item in r["identifiers"].get("domains", {}).items():
        formats = ", ".join(f"{x['format']} ({x['distinct_ids']:,})" for x in item.get("formats", []))
        line.append(f"| {domain} | {formats or item.get('status', 'empty')} | {item.get('suffix_collisions', '—')} |")
    line += ["", "Agency–stop associations are derived through routes, trips and validated platform visits. The identifier "
             "report includes each agency, per-agency prefix distributions, stop-code collisions, and samples of colliding suffixes. "
             "[Identifier analysis](identifier_analysis.json)", "", "## Realtime alert compatibility", "",
             f"Outcome for the combined `AgencyId=AC` and `StopId=55660` selector is **{rt.get('classification') or 'unavailable'}**. "
             f"Feed generation in the example is **{rt.get('feed_generation_utc', 'unavailable')}**. "
             "Separate timestamps are needed for observation, disruption start, disruption end and ingestion.", "",
             "| Stop | Name | Evidence | Serving agencies | Routes | AC trips |", "|---|---|---|---|---:|---:|"]
    for row in rt.get("candidates", []):
        ac_trips = sum(x["distinct_trips"] for x in row.get("visit_counts_by_agency", []) if x["agency_id"] == "AC")
        line.append(f"| {row['stop_id']} | {row.get('stop_name', '—')} | {', '.join(row['evidence'])} | "
                    f"{', '.join(row['serving_agencies'])} | {len(row['routes'])} | {ac_trips:,} |")
    exact = [row for row in rt.get("candidates", []) if "exact_stop_id" in row["evidence"] and "AC" in row["serving_agencies"]]
    for row in exact:
        routes = ", ".join(route["route_id"] for route in row["routes"] if route["agency_id"] == "AC")
        dated = ", ".join(f"{row['trips']} on {row['service_date']}" for row in row.get("agency_trips_by_service_date", []))
        line += ["", f"AC service reaches stop `{row['stop_id']}` via {routes or 'no identified routes'}. " +
                 (f"Scheduled AC trip templates visiting the stop and running on the adjacent service dates total {dated}. "
                  "Those dates are computed from the example feed timestamp and the agency timezone." if dated else "")]
    line += ["", "Route and trip evidence, checked identifier variants and support for AgencyId, RouteId, RouteType, "
             "StopId, DirectionId and TripDescriptor are in [realtime_compatibility_report.json](realtime_compatibility_report.json). "
             "Use conjunctive matching within each selector. Match direction with route and resolve trip templates to a service date "
             "and start time where required. Verify the paired operator feed before generalizing this example to other identifiers.",
             "", "## Temporal analysis", ""]
    dates, st = temporal.get("effective_service_dates", {}), temporal.get("stop_times", {})
    trip_dates = temporal.get("trip_backed_effective_service_dates", {})
    line += [f"Across the complete calendar domain, effective service spans **{dates.get('earliest', '—')} to {dates.get('latest', '—')}** "
             f"across **{dates.get('service_date_pairs', '—')} service-date pairs**. "
             f"Trip-backed service spans **{trip_dates.get('earliest', '—')} to {trip_dates.get('latest', '—')}**. "
             f"Calendar-exception-only service IDs total **{temporal.get('exception_only_services', {}).get('count', '—')}**.", "",
             f"Stop times include **{st.get('arrivals_above_24h', '—')}** arrivals past 24:00:00 and "
             f"**{st.get('departures_above_24h', '—')}** departures past 24:00:00. "
             f"Missing arrivals and departures total **{st.get('missing_arrivals', '—')}** and **{st.get('missing_departures', '—')}**, "
             f"with **{st.get('missing_required_arrivals', '—')}** and **{st.get('missing_required_departures', '—')}** "
             "classified as required by the implemented endpoint and timepoint checks.", "",
             f"Agency time zones and their row counts are " +
             (", ".join(f"{row['timezone']} ({row['records']:,})" for row in temporal.get("agency_timezones", [])) or "unavailable") + ". "
             "Inspect effective service dates, weekly flags, calendar exceptions, time bounds and raw feed validity fields in "
             "[temporal_network_graph.json](temporal_network_graph.json). Service-day context can constrain an alert's trip "
             "instance. The sample supplies no explicit active period.", ""]
    latest = temporal.get("latest_service_date_examples", [])
    feed_rows = temporal.get("feed_info", [])
    if latest or feed_rows:
        date_details = []
        if latest:
            date_details.append("Service IDs on the latest date include " +
                                ", ".join(f"`{item['service_id']}`" for item in latest) + ".")
        if len(feed_rows) == 1 and all(not feed_rows[0].get(key) for key in
                                       ("feed_version", "feed_start_date", "feed_end_date")):
            date_details.append("The regional feed version and both feed validity fields are blank. "
                                "The calendar range describes scheduled service records. Verify the snapshot's "
                                "publication and active dates separately.")
        if date_details:
            line += [" ".join(date_details), ""]
    line += ["## Geographic and network analysis", ""]
    coordinates = network.get("coordinates", {})
    line += [f"Valid stop coordinates cover latitude **{coordinates.get('min_lat', '—')} to {coordinates.get('max_lat', '—')}** "
             f"and longitude **{coordinates.get('min_lon', '—')} to {coordinates.get('max_lon', '—')}**. "
             f"**{network.get('multi_agency_stops', {}).get('count', '—')} stops** are served by multiple agencies. "
             f"Repeated stop-name and shared-coordinate groups total **{network.get('repeated_names', {}).get('groups', '—')}** and "
             f"**{network.get('shared_coordinates', {}).get('groups', '—')}** respectively. These groups are candidates for investigation.",
             "", f"The joins produced **{network.get('route_stop_pairs', '—')} route–stop pairs** and "
             f"**{network.get('agency_stop_pairs', '—')} agency–stop pairs**. "
             f"Median unique stops per trip is **{network.get('distinct_stops_per_trip', {}).get('median', '—')}**. "
             "The JSON includes route and location types, routes per agency, trips per route, stops per agency, "
             "parent station checks and shape-point consistency.",
             "", f"Shape IDs are populated on **{network.get('shape_coverage', {}).get('trips_with_shape_id', '—')}** trips. "
             f"**{network.get('shape_coverage', {}).get('shapes_used_by_trips', '—')}** distinct shapes are referenced by trips.", "",
             "## Preliminary knowledge graph assessment", "",
             "| Entity | Sources | Identifier | Estimated nodes |", "|---|---|---|---:|"]
    for node in r["graph"]["entities"]:
        line.append(f"| {node['entity']} | {node['sources']} | {node['identifiers']} | {node['estimate']} |")
    line += ["", "| Relationship | Cardinality | Origin | Estimated records or pairs |", "|---|---|---|---:|"]
    for edge in r["graph"]["relationships"]:
        line.append(f"| {edge['relationship']} | {edge['cardinality']} | {edge['representation']} | {edge['estimate']} |")
    line += ["", "Estimates are from source records or validated joins as described in "
             "[the detailed graph assessment](temporal_network_graph.json). Counts can change after issue review.", ""]
    for option in r["graph"]["stop_time_options"]:
        line.append(f"- **{option['option']}**. {option['size']} {option['tradeoff']}")
    line += ["", r["graph"]["recommendation"], "", "## Identified data-quality issues", "",
             "Issue totals are " + ", ".join(f"{name} {severity[name]}" for name in
                 ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")) + ". "
             "An issue can cover records also counted in another check.", "",
             "| Issue | Severity | File and field | Records | Graph impact |", "|---|---|---|---:|---|"]
    for item in review:
        line.append(f"| {item['issue_id']} | {item['severity']} | {item['file']} / {item['field_or_relationship']} | "
                    f"{item['affected_records'] if item['affected_records'] is not None else '—'} | {item['graph_impact']} |")
    line += ["", "[All issues, evidence and suggested handling](data_quality_report.json)", "",
             "## Unresolved questions and check coverage", "",
             "- Provider-only and undocumented columns are profiled as raw text. Verify their semantics and the meaning and "
             "versioning of `mtc_feed_versions.txt` with 511.",
             "- Operator-feed IDs, regional transformation guarantees and identifier stability across snapshots require comparison "
             "with matching dated sources. The supplied sample can only validate the static regional evidence it matches.",
             "- Value checks cover targeted syntax, ranges, enumerations, keys and join rules. Full GTFS conformance, all optional "
             "fare/booking conditional logic, geospatial shape-to-stop distances, geometry topology, authoritative IANA/ISO "
             "registry membership and transfer routing behavior are outside the executed checks.",
             "- Shape distance values retain source units. A single coordinate range summarizes the bounding box.",
             "- Deterministic samples are bounded and can emphasize lexically early IDs. Record and distinct counts use full tables."]
    unknown = [(name, [column for column, profile in table["columns"].items() if profile["expected_type"] is None])
               for name, table in r["tables"].items()]
    if any(columns for _, columns in unknown):
        line += ["", "Fields with unresolved documented types include the following.", ""]
        for name, columns in unknown:
            if columns:
                line.append(f"- `{name}` contains " + ", ".join(f"`{column}`" for column in columns) + ".")
    for detail in network.get("unavailable", []) + temporal.get("unavailable", []):
        line.append(f"- {detail}")
    for check in r["failed_checks"]:
        line.append(f"- Failed check **{check['check']}**. See the diagnostic in the quality report.")
    line += ["", "## Recommended data-cleaning strategies", "",
             "- Preserve source ID text, leading zeros, feed scope and checksum provenance. Audit any normalization against "
             "suffix and stop-code collisions.",
             "- Resolve conflicting key groups with dated provider evidence. Track invalid references and coordinate or time anomalies "
             "without silently dropping records.",
             "- Use typed service-day seconds and validated service-date pairs for time analysis. Preserve source time text for traceability.",
             "", "## Recommendations for Phase 3", "",
             "Prioritize the critical and high findings, obtain same-version operator and regional evidence for identifier "
             "crosswalks, and design an explicit policy for shared stops and unresolved compound selectors. Compare the timetable "
             "storage options on the measured feed before committing to a graph model.",
             "", "## Execution time and resource consumption", "",
             f"Python **{execution['python']}**, DuckDB **{execution['duckdb']}**, {execution['platform']}. "
             f"The run used **{execution['wall_seconds']} s wall time**, **{execution['cpu_seconds']} s process CPU time**, "
             f"**{execution['peak_process_rss_mib']} MiB peak process RSS**, and "
             f"**{execution['scratch_database_bytes_at_checkpoint']:,} bytes** for the temporary database at checkpoint. "
             f"DuckDB used **{execution['duckdb_threads']} threads** with a **{execution['duckdb_memory_limit']}** memory setting. "
             "Peak RSS includes Python and DuckDB allocations. The checkpoint size excludes transient spill peaks.",
             "", "## Reproducibility", "",
             "From the workspace root in PowerShell, install the pinned dependency and run the profiler and fixture tests.", "",
             "```powershell", "python -m venv .venv",
             ".venv/Scripts/python.exe -m pip install -r scripts/requirements-phase2.txt",
             f'.venv/Scripts/python.exe scripts/profile_gtfs.py "{source["dataset"]}" --output reports/phase2',
             ".venv/Scripts/python.exe -m unittest discover -s scripts -p 'test_profile_gtfs.py'", "```", "",
             "The profiler accepts any extracted feed directory or ZIP archive. Source files are opened read-only. "
             "Temporary tables are removed after the run. Report directories must be outside the source. "
             "The inventory records the input checksums, specification revision and execution settings.", ""]
    return "\n".join(line)
