"""Deterministic, paired static grounding workloads for the regional snapshot."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Workload:
    name: str
    purpose: str
    cases: list
    sql: str
    cypher: str = ""
    hybrid_sql: str = ""


# Independent SQL uses accepted trip/visit rows and the official service calendar macro.
SCOPED_DAY = """
SELECT DISTINCT x.canonical_trip_id, x.route_id
FROM trip_selector_context x JOIN stop_times v ON v.trip_id=x.trip_id
JOIN active_services($day) active ON active.service_id=x.service_id
WHERE x.agency_id=$agency AND v.stop_id=$stop
AND ($route IS NULL OR x.route_id=$route)
AND ($direction IS NULL OR x.direction_id=$direction)
ORDER BY 1,2
"""
GRAPH_CONTEXT = """
MATCH (a:Agency {source_id:$agency})-[:OPERATES]->(r:Route)
      -[:ROUTE_SERVES_STOP]->(s:Stop {source_id:$stop})
MATCH (a)-[:AGENCY_SERVES_STOP]->(s)
WHERE $route IS NULL OR r.source_id=$route
RETURN r.canonical_id,s.canonical_id ORDER BY 1
"""
HYBRID_DAY = """
SELECT DISTINCT x.canonical_trip_id,x.route_id
FROM canonical_ids r JOIN trip_selector_context x ON x.route_id=r.source_identifier
JOIN stop_times v ON v.trip_id=x.trip_id
JOIN canonical_ids s ON s.source_identifier=v.stop_id
JOIN active_services($day) active ON active.service_id=x.service_id
WHERE r.entity_type='route' AND r.canonical_id IN (SELECT unnest($route_ids::VARCHAR[]))
AND s.entity_type='stop' AND s.canonical_id=$canonical_stop
AND ($direction IS NULL OR x.direction_id=$direction)
ORDER BY 1,2
"""
TRIP_DAY = """
SELECT v.stop_sequence,v.stop_id,v.arrival_time_seconds,v.departure_time_seconds,
       active.service_id IS NOT NULL AS runs_on_date
FROM trip_selector_context x JOIN stop_times v ON v.trip_id=x.trip_id
LEFT JOIN active_services($day) active ON active.service_id=x.service_id
WHERE x.canonical_trip_id=$id ORDER BY 1
"""
GRAPH_TRIP = """
MATCH (t:Trip {canonical_id:$id})-[:RUNS_ON]->(s:Service)
RETURN t.canonical_id,s.source_id
"""
HYBRID_TRIP = """
SELECT v.stop_sequence,v.stop_id,v.arrival_time_seconds,v.departure_time_seconds,
       active.service_id IS NOT NULL AS runs_on_date
FROM canonical_ids t JOIN stop_times v ON v.trip_id=t.source_identifier
LEFT JOIN active_services($day) active ON active.service_id=$service
WHERE t.entity_type='trip' AND t.canonical_id=$trip ORDER BY 1
"""


def workouts(db):
    def col(query):
        return [row[0] for row in db.sql(query).fetchall()]

    # Stable ordering and lexicographic tie-breaks throughout selection.
    routes = col("""WITH d AS (SELECT route_id,count(*) n FROM route_stops GROUP BY 1)
       SELECT route_id FROM (
          (SELECT route_id,1 ord FROM d ORDER BY n,route_id LIMIT 1)
          UNION ALL (SELECT route_id,2 ord FROM d ORDER BY n DESC,route_id LIMIT 1)
          UNION ALL SELECT 'AC:72',3) ORDER BY ord""")
    counts = """SELECT stop_id,count(*) n FROM route_stops GROUP BY 1"""
    low = col(f"SELECT stop_id FROM ({counts}) ORDER BY n,stop_id LIMIT 1")[0]
    high = col(f"SELECT stop_id FROM ({counts}) ORDER BY n DESC,stop_id LIMIT 1")[0]
    stops = list(dict.fromkeys([low,high,"341028","55660"]))
    agencies = col("""WITH d AS (SELECT agency_id,count(*) n FROM routes GROUP BY 1)
       SELECT agency_id FROM (
          (SELECT agency_id,1 ord FROM d ORDER BY n,agency_id LIMIT 1)
          UNION ALL (SELECT agency_id,2 ord FROM d ORDER BY n DESC,agency_id LIMIT 1)
          UNION ALL SELECT 'AC',3) ORDER BY ord""")
    agencies = list(dict.fromkeys([*agencies,"BA","CM"]))
    ambig = col("""SELECT stop_name FROM stops WHERE nullif(stop_name,'') IS NOT NULL
                  GROUP BY 1 HAVING count(*)>1 ORDER BY count(*) DESC,stop_name LIMIT 1""")[0]
    trips = col("""SELECT canonical_trip_id FROM trip_selector_context WHERE trip_id='AC:10056070'
      UNION ALL SELECT canonical_trip_id FROM trip_selector_context WHERE trip_id='3D:1175'""")
    # The first two query families include representative exact keys and a distinct absent key.
    def canonical(entity, source):
        return db.execute("SELECT canonical_id FROM canonical_ids WHERE entity_type=? AND source_identifier=?",
                          [entity,source]).fetchone()[0]

    cases = [{"id":canonical("stop",s)} for s in stops]
    cases.append({"id":"511-sf-bay-regional:stop:PHASE7-ABSENT"})
    pairs = [{"agency":"AC","stop":"55660"},{"agency":"BA","stop":"55660"},
             {"agency":"CM","stop":"341028"},{"agency":"SM","stop":"341028"}]
    pairs += [dict(agency=a,stop=s) for a,s in db.execute(
        "SELECT agency_id,stop_id FROM agency_stops WHERE stop_id IN (?,?) ORDER BY 1,2",
        [low,high]).fetchall()[:2]]
    transfer = [dict(src=a,dst=b) for a,b in db.sql("""SELECT DISTINCT from_stop_id,to_stop_id
        FROM transfers WHERE from_route_id IS NOT NULL ORDER BY 1,2 LIMIT 3""").fetchall()]
    transfer += [{"src":"241","dst":"241"}]
    scoped = [
        dict(agency="AC",stop="55660",day="2026-11-25",route=None,direction=None),
        dict(agency="AC",stop="55660",day="2026-11-25",route="AC:72",direction=0),
        dict(agency="AC",stop="55660",day="2026-11-25",route="AC:72",direction=1),
        dict(agency="BA",stop="55660",day="2026-11-25",route=None,direction=None),
        dict(agency="CM",stop="341028",day="2026-11-24",route=None,direction=None),
        dict(agency="SM",stop="341028",day="2026-11-29",route=None,direction=None),
    ]
    services = [{"service":sid,"day":str(day)} for sid,day in db.sql("""SELECT service_id,date FROM (
       SELECT *,row_number() OVER(PARTITION BY exception_type ORDER BY date,service_id) pos
       FROM calendar_dates WHERE date BETWEEN DATE '2026-11-01' AND DATE '2026-12-01'
       ) WHERE pos=1 ORDER BY exception_type""").fetchall()]
    services += [{"service":"AC:83048","day":"2026-11-25"}]
    return [
        Workload("canonical_stop","Exact canonical stop lookup including absent ID",cases,
          "SELECT source_identifier AS id FROM canonical_ids WHERE entity_type='stop' AND canonical_id=$id",
          "MATCH (s:Stop {canonical_id:$id}) RETURN s.source_id AS id"),
        Workload("agency_routes","Routes operated by smaller and larger agencies",[{"agency":a} for a in agencies],
          "SELECT route_id FROM routes WHERE agency_id=$agency ORDER BY 1",
          "MATCH (:Agency {source_id:$agency})-[:OPERATES]->(r:Route) RETURN r.source_id ORDER BY 1"),
        Workload("route_stops","Served stops on small and large routes",[{"route":r} for r in routes],
          "SELECT stop_id FROM route_stops WHERE route_id=$route ORDER BY 1",
          "MATCH (:Route {source_id:$route})-[:ROUTE_SERVES_STOP]->(s:Stop) RETURN s.source_id ORDER BY 1"),
        Workload("stop_routes","Routes at low-degree, high-degree and shared stops",[{"stop":s} for s in stops],
          "SELECT route_id FROM route_stops WHERE stop_id=$stop ORDER BY 1",
          "MATCH (r:Route)-[:ROUTE_SERVES_STOP]->(:Stop {source_id:$stop}) RETURN r.source_id ORDER BY 1"),
        Workload("agency_stop","Agency-scoped stop identity including contradictory agency",pairs,
          "SELECT canonical_stop_id FROM agency_stops WHERE agency_id=$agency AND stop_id=$stop",
          """MATCH (:Agency {source_id:$agency})-[:AGENCY_SERVES_STOP]->(s:Stop {source_id:$stop})
             RETURN s.canonical_id"""),
        Workload("stop_name","Ambiguous stop names and nonexistent name",
          [{"name":x} for x in dict.fromkeys(["San Pablo Av & W Grand Av",ambig,"PHASE7 UNKNOWN STOP"])],
          "SELECT stop_id FROM stops WHERE stop_name=$name ORDER BY 1",
          "MATCH (s:Stop {stop_name:$name}) RETURN s.source_id ORDER BY 1"),
        Workload("transfer_rules","Transfer identities and restriction payload",transfer,
          """SELECT from_route_id,to_route_id,from_trip_id,to_trip_id,transfer_type,min_transfer_time
             FROM transfers WHERE from_stop_id=$src AND to_stop_id=$dst""",
          """MATCH (:Stop {source_id:$src})-[r:TRANSFER_RULE]->(:Stop {source_id:$dst})
             RETURN r.from_route_id,r.to_route_id,r.from_trip_id,r.to_trip_id,r.transfer_type,r.min_transfer_time"""),
        Workload("scoped_service_day","Agency/stop/date, route/direction and shared-stop scope",scoped,
          SCOPED_DAY,GRAPH_CONTEXT,HYBRID_DAY),
        Workload("trip_service_visits","Ordered visits and service-day predicate including extended hours",
          [{"id":i,"day":day} for i in trips for day in ("2026-11-25","2026-11-29")],
          TRIP_DAY,GRAPH_TRIP,HYBRID_TRIP),
        Workload("calendar_exceptions","Calendar service on dates with additions and removals",services,
          "SELECT EXISTS(SELECT 1 FROM active_services($day) WHERE service_id=$service)"),
    ]

