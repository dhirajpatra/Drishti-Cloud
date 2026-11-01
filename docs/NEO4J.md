# Exploring the Drishti knowledge graph (Neo4j)

**Open it:** http://localhost:7474 -> *Connect URL* `neo4j://localhost:7687` (or `bolt://localhost:7687`), user `neo4j`,
password = `NEO4J_PASSWORD` from `.env` (default `drishti123`). The ports are bound to `127.0.0.1`; on a cloud VM use an SSH
tunnel (`ssh -L 7474:localhost:7474 -L 7687:localhost:7687 user@vm`) rather than opening them.
> The password is only applied the first time the `neo4j_data` volume is created. If you change it later, either run
> `docker compose down -v` (deletes ALL data) or `ALTER CURRENT USER SET PASSWORD FROM 'old' TO 'new'`.

**Run a query:** paste it into the top bar and press **Ctrl+Enter**. Pick a tenant once, then reuse it as `$tid`:
```cypher
:param tid => 'home_1_bangalore'
```
Demo tenants: `mall_1`, `home_1_bangalore`, `school_1`, `icu_ward_1`. In the graph view click a node type at the bottom
right and choose caption `name` to label nodes; double-click a node to expand its neighbours.

**Schema as built** (every node carries `tenant_id`, so always filter by tenant):

| Node | Useful properties |
|------|-------------------|
| `Tenant` | `id`, `name`, `type`, `vertical`, `location` |
| `Camera` | `id`, `rtsp_url` |
| `Person` | `name`, `role` |
| `Habit` | `desc`, `rule`, `start_hour`, `end_hour`, `frequency`, `learned` |
| `Event` | `id` (= frame id), `timestamp`, `level`, `type`, `camera_id`, `ocr_texts`, `mask_count` |
| `Object` | `label`, `last_seen` |
| `Alert` | `level`, `rule`, `reason`, `speak_text`, `image_url` |
| `SOPDoc`, `NewsContext` | `text` / `headline`, `source`, `timestamp` |

`(Tenant)-[:HAS_CAMERA|HAS_PERSON|HAS_SOP|HAS_EVENT|IN_CONTEXT|HAS_HABIT]->(...)`, `(Person)-[:HAS_HABIT]->(Habit)`,
`(Event)-[:DETECTED_OBJECT]->(Object)`, `(Event)-[:TRIGGERED]->(Alert)`.
Only *non-trivial* frames become `Event` nodes (an unchanged NORMAL scene is skipped), so Neo4j has fewer events than the
dashboard's timeline, which is read from Postgres.

## Starter queries (Cypher)

Neo4j speaks Cypher, not SQL. Paste into the Neo4j Browser (http://localhost:7474).

```cypher
// 1. See the whole picture for one tenant (graph view)
MATCH (t:Tenant {id:$tid})-[r]->(n) RETURN t, r, n LIMIT 100

// 2. What is in the database?
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC

// 3. Latest events (table view)
MATCH (:Tenant {id:$tid})-[:HAS_EVENT]->(e:Event)
RETURN e.timestamp AS time, e.camera_id AS camera, e.level AS level, e.type AS rule, e.ocr_texts AS text
ORDER BY e.timestamp DESC LIMIT 20

// 4. Last hour as a graph: events with the objects they saw and the alerts they raised
MATCH (:Tenant {id:$tid})-[:HAS_EVENT]->(e:Event)
WHERE e.timestamp > datetime() - duration({hours: 1})
OPTIONAL MATCH (e)-[:DETECTED_OBJECT]->(o:Object)
OPTIONAL MATCH (e)-[:TRIGGERED]->(a:Alert)
RETURN e, o, a

// 5. Every CRITICAL alert, all tenants
MATCH (t:Tenant)-[:HAS_EVENT]->(e:Event)-[:TRIGGERED]->(a:Alert)
WHERE a.level = 'CRITICAL'
RETURN t.id AS tenant, e.timestamp AS time, a.rule AS rule, a.reason AS reason ORDER BY time DESC

// 6. Which habits does the system know (these suppress false alarms)?
MATCH (t:Tenant {id:$tid})-[:HAS_PERSON|HAS_HABIT*1..2]->(h:Habit)
RETURN DISTINCT h.desc AS habit, h.rule AS explains_rule, h.start_hour AS from_hour, h.end_hour AS to_hour,
       h.frequency AS seen, h.learned AS learned_from_feedback

// 7. What does this camera see most often?
MATCH (:Tenant {id:$tid})-[:HAS_EVENT]->(:Event)-[:DETECTED_OBJECT]->(o:Object)
RETURN o.label AS object, count(*) AS times_seen ORDER BY times_seen DESC LIMIT 15

// 8. Activity by hour of day (India time)
MATCH (:Tenant {id:$tid})-[:HAS_EVENT]->(e:Event)
RETURN datetime({datetime: e.timestamp, timezone: 'Asia/Kolkata'}).hour AS hour, count(*) AS events ORDER BY hour

// 9. Local news the reasoner may use to escalate an alert
MATCH (:Tenant {id:$tid})-[:IN_CONTEXT]->(n:NewsContext)
RETURN n.headline, n.source, n.timestamp ORDER BY n.timestamp DESC LIMIT 10
```
Teach a habit by hand (same shape the agents write; the Wonder agent then marks that rule NORMAL inside the window):
```cypher
MATCH (t:Tenant {id:$tid})
MERGE (h:Habit {uid: $tid + ':habit:evening-tea'})
SET h.tenant_id=$tid, h.desc='Evening tea in the kitchen 17:00-18:00', h.name='no_motion_2h', h.rule='no_motion_2h',
    h.start_hour=17, h.end_hour=18, h.learned=false, h.frequency=1
MERGE (t)-[:HAS_HABIT]->(h)
```
Clean up one tenant's events (keeps habits, cameras and people): `MATCH (e:Event {tenant_id:$tid}) DETACH DELETE e`

Same queries without a browser: `docker compose exec neo4j cypher-shell -u neo4j -p drishti123 "MATCH (n) RETURN labels(n)[0], count(*)"`.
A harmless `IN_CONTEXT ... not available` warning in the gateway log just means no news items have been stored yet.


## More queries
```cypher
// every physical-abuse alert, all tenants
MATCH (t:Tenant)-[:HAS_EVENT]->(e:Event)-[:TRIGGERED]->(a:Alert {rule:'physical_abuse'})
RETURN t.id AS tenant, e.timestamp AS time, a.reason AS reason ORDER BY time DESC

// alerts that were escalated or explained by local news (the reasoner's rationale mentions the headline)
MATCH (t:Tenant)-[:IN_CONTEXT]->(n:NewsContext) RETURN t.id, n.headline, n.source, n.timestamp ORDER BY n.timestamp DESC LIMIT 20

// cameras per tenant
MATCH (t:Tenant)-[:HAS_CAMERA]->(c:Camera) RETURN t.id AS tenant, collect(c.id) AS cameras
```
Dashboard notifications and live frames are kept in Redis (short-lived), not in Neo4j.

## Show everything for one site (what the dashboard's graph shows, but big)
The dashboard's **NEO4J BROWSER** link now opens this query already typed in for the selected site: click it, log in
(`neo4j` / your `NEO4J_PASSWORD`), press **Ctrl+Enter**. The dashboard's **EXPAND** button shows the same graph full screen.
```cypher
MATCH p=(t:Tenant {id:'home_1_bangalore'})-[*1..2]-(n) RETURN p LIMIT 300
```
Using a parameter instead: `:param tenant_id => 'home_1_bangalore'` (or `:params {tenant_id: 'home_1_bangalore'}`), then use `$tenant_id`.
If the graph looks crowded, click a node type in the bottom bar and choose caption `label`, or lower the `LIMIT`.

## "My query returns nothing": it used relationships Drishti does not have
A query like `(Tenant)-[:KNOWS]->(Person)-[:TRIGGERED]->(Event)-[:LOCATED_AT]->(Object)`, `e.at`, `e.kind`, `RELATED_TO_NEWS`
matches no data, because the real graph is (see *Schema as built* above):
`(Tenant)-[:HAS_PERSON]->(Person)`, `(Tenant)-[:HAS_EVENT]->(Event)-[:DETECTED_OBJECT]->(Object)`, `(Event)-[:TRIGGERED]->(Alert)`,
`(Tenant)-[:IN_CONTEXT]->(NewsContext)`; events carry `timestamp` (not `at`) and `type`/`kind`; persons are not linked to events.
The working version of "what happened in the last hour":
```cypher
MATCH (:Tenant {id: $tenant_id})-[:HAS_EVENT]->(e:Event)
WHERE e.timestamp > datetime() - duration('PT1H')
OPTIONAL MATCH (e)-[:DETECTED_OBJECT]->(o:Object)
OPTIONAL MATCH (e)-[:TRIGGERED]->(a:Alert)
RETURN e.timestamp AS time, e.camera_id AS camera, e.type AS rule, e.level AS level,
       collect(DISTINCT o.name) AS objects, a.reason AS alert
ORDER BY time DESC
```
And the news the site is currently aware of:
```cypher
MATCH (:Tenant {id: $tenant_id})-[:IN_CONTEXT]->(n:NewsContext)
RETURN n.timestamp AS time, n.headline AS headline, n.source AS source ORDER BY time DESC LIMIT 20
```
Nothing in the last hour is normal when the scene is idle: only changed or alerting frames become `Event` nodes. Widen the window
(`PT24H`) to check.
