// 1. Small network around AC:72 with operator, route and stops.
MATCH (a:Agency {source_id:'AC'})-[o:OPERATES]->(r:Route {source_id:'AC:72'})
CALL (r) {
  MATCH (r)-[e:ROUTE_SERVES_STOP]->(s:Stop)
  RETURN e,s ORDER BY s.source_id LIMIT 12
}
RETURN a,o,r,e,s;

// 2. Agency and routes.
MATCH path=(a:Agency {source_id:'AC'})-[:OPERATES]->(r:Route)
RETURN path ORDER BY r.source_id LIMIT 20;

// 3. Route and served stops.
MATCH path=(r:Route {source_id:'AC:72'})-[:ROUTE_SERVES_STOP]->(s:Stop)
RETURN path ORDER BY s.source_id LIMIT 25;

// 4. Route, a few trip templates and their services.
MATCH path=(r:Route {source_id:'AC:72'})-[:HAS_TRIP]->(t:Trip)-[:RUNS_ON]->(:Service)
RETURN path ORDER BY t.source_id LIMIT 12;

// 5. AC Transit stop 55660 with agency service and the scoped route associations.
MATCH scope=(a:Agency {source_id:'AC'})-[:AGENCY_SERVES_STOP]->(s:Stop {source_id:'55660'})
MATCH routes=(a)-[:OPERATES]->(r:Route)-[:ROUTE_SERVES_STOP]->(s)
RETURN scope,routes ORDER BY r.source_id LIMIT 10;

// 6. Shared stop 341028 and its operators.
MATCH path=(a:Agency)-[:AGENCY_SERVES_STOP]->(:Stop {source_id:'341028'})
RETURN path ORDER BY a.source_id LIMIT 15;

// 7. Child locations pointing to their parent.
MATCH path=(s:Stop)-[:PART_OF]->(:Stop {source_id:'-325'})
RETURN path ORDER BY s.source_id LIMIT 20;

// 8. Transfer rules and their properties. Conditions remain on each rule.
MATCH path=(a:Stop)-[rule:TRANSFER_RULE]->(b:Stop)
WHERE rule.from_route_id IS NOT NULL OR a.source_id='241'
RETURN path, properties(rule) AS restrictions ORDER BY a.source_id,b.source_id LIMIT 20;

// 9. Connected subgraph for AC with both stop membership and scheduled services.
MATCH path=(a:Agency {source_id:'AC'})-[:OPERATES]->(r:Route {source_id:'AC:72'})
           -[:HAS_TRIP]->(t:Trip)-[:RUNS_ON]->(:Service)
WITH r,path,t ORDER BY t.source_id LIMIT 5
MATCH membership=(r)-[:ROUTE_SERVES_STOP]->(:Stop {source_id:'55660'})
RETURN path,membership;

// 10. In-graph snapshot metadata.
MATCH (snapshot:FeedSnapshot) RETURN snapshot, properties(snapshot) AS metadata LIMIT 1;

