// Scoped stop identity and its canonical key
MATCH (:Agency {source_id:$agency})-[:AGENCY_SERVES_STOP]->(s:Stop {source_id:$stop})
RETURN s.canonical_id AS id;

// Candidate search preserves distinct stop identities
MATCH (s:Stop {stop_name:$name}) RETURN s.source_id AS id ORDER BY id;

// All scheduled operators of one stop
MATCH (a:Agency)-[:AGENCY_SERVES_STOP]->(:Stop {source_id:$stop})
RETURN a.source_id AS id ORDER BY id;

// Route lookup in operator scope
MATCH (:Agency {source_id:$agency})-[:OPERATES]->(r:Route {source_id:$route})
RETURN r.canonical_id AS id;

// Exact trip lookup
MATCH (t:Trip {source_id:$trip}) RETURN t.canonical_id AS id;

// Routes at a stop scoped to the same agency
MATCH (:Agency {source_id:$agency})-[:OPERATES]->(r:Route)-[:ROUTE_SERVES_STOP]->(:Stop {source_id:$stop})
RETURN r.source_id AS id ORDER BY id;

// Stop membership on one route
MATCH (:Route {source_id:$route})-[:ROUTE_SERVES_STOP]->(s:Stop)
RETURN s.source_id AS id ORDER BY id;

// Trip templates on one route
MATCH (:Route {source_id:$route})-[:HAS_TRIP]->(t:Trip) RETURN t.source_id AS id ORDER BY id;

// Service used by one trip
MATCH (:Trip {source_id:$trip})-[:RUNS_ON]->(s:Service) RETURN s.source_id AS id;

// Children of a station
MATCH (c:Stop)-[:PART_OF]->(:Stop {source_id:$parent}) RETURN c.source_id AS id ORDER BY id;

// Transfer rules between the supplied stops, with all restrictions
MATCH (:Stop {source_id:$stop})-[r:TRANSFER_RULE]->(:Stop {source_id:$target})
RETURN properties(r) AS rule;

// Shared stops
MATCH (a:Agency)-[:AGENCY_SERVES_STOP]->(s:Stop)
WITH s, count(DISTINCT a) AS operators WHERE operators > 1
RETURN s.source_id AS id, operators ORDER BY id;

// Scoped route, type and direction candidates; stop on the same trip requires timetable lookup
MATCH (:Agency {source_id:$agency})-[:OPERATES]->(r:Route {source_id:$route,route_type:$route_type})-[:HAS_TRIP]->(t:Trip {direction_id:$direction})
RETURN t.source_id AS id ORDER BY id;

// Exact compatibility gate
MATCH (m:FeedSnapshot) RETURN m.source_snapshot_id AS snapshot, m.normalized_build_id AS build,
m.schema_version AS schema, m.canonical_contract_version AS contract;

