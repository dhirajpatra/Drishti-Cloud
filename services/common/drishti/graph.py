"""Neo4j knowledge graph. Tenant isolation: every node carries tenant_id, every query is anchored on
(:Tenant {id:$tid}) (or filters on tenant_id), and node uids are namespaced "<tenant>:<kind>:<key>"
so two tenants can never MERGE into the same node.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from neo4j import GraphDatabase, RoutingControl

from .config import settings
from .log import get_logger

log = get_logger("graph")
_driver = None

NODE_LABELS = ["Camera", "Person", "Object", "Event", "Alert", "Habit", "SOPDoc", "NewsContext"]


def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
                                       max_connection_lifetime=1800)
    return _driver


def _q(query: str, **params: Any) -> list[dict]:
    recs, _, _ = driver().execute_query(query, parameters_=params, routing_=RoutingControl.WRITE)
    return [r.data() for r in recs]


def _read(query: str, **params: Any) -> list[dict]:
    recs, _, _ = driver().execute_query(query, parameters_=params, routing_=RoutingControl.READ)
    return [r.data() for r in recs]


def ping() -> bool:
    try:
        driver().verify_connectivity()
        return True
    except Exception:  # noqa: BLE001
        return False


def _h(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def ensure_schema(wait_sec: int = 90) -> None:
    deadline = time.time() + wait_sec
    while True:
        try:
            driver().verify_connectivity()
            break
        except Exception as exc:  # noqa: BLE001
            if time.time() > deadline:
                raise
            log.warning("neo4j not ready (%s) - retrying", exc)
            time.sleep(3)
    _q("CREATE CONSTRAINT tenant_id IF NOT EXISTS FOR (t:Tenant) REQUIRE t.id IS UNIQUE")
    for label in NODE_LABELS:
        _q(f"CREATE CONSTRAINT {label.lower()}_uid IF NOT EXISTS FOR (n:{label}) REQUIRE n.uid IS UNIQUE")
    _q("CREATE INDEX event_ts IF NOT EXISTS FOR (e:Event) ON (e.timestamp)")
    _q("CREATE INDEX news_ts IF NOT EXISTS FOR (n:NewsContext) ON (n.timestamp)")


# ------------------------------------------------------------------ writes
def upsert_tenant(tid: str, ttype: str, location: str, vertical: str, sop_text: str) -> None:
    _q(
        """
        MERGE (t:Tenant {id:$tid})
          SET t.tenant_id=$tid, t.type=$ttype, t.location=$loc, t.vertical=$vertical, t.name=$tid
        MERGE (s:SOPDoc {uid:$suid})
          ON CREATE SET s.text=$sop
          SET s.tenant_id=$tid, s.vertical=$vertical, s.name=$sname
        MERGE (t)-[:HAS_SOP]->(s)
        """,
        tid=tid, ttype=ttype, loc=location or "", vertical=vertical, suid=f"{tid}:sop", sop=sop_text,
        sname=f"{vertical} SOP",
    )


def add_camera(tid: str, camera_id: str, rtsp_url: str | None = None) -> None:
    _q(
        """
        MATCH (t:Tenant {id:$tid})
        MERGE (c:Camera {uid:$uid})
          SET c.id=$cam, c.name=$cam, c.tenant_id=$tid, c.rtsp_url=$url
        MERGE (t)-[:HAS_CAMERA]->(c)
        """,
        tid=tid, uid=f"{tid}:cam:{camera_id}", cam=camera_id, url=rtsp_url,
    )


def add_person(tid: str, name: str, role: str) -> None:
    _q(
        """
        MATCH (t:Tenant {id:$tid})
        MERGE (p:Person {uid:$uid})
          SET p.id=$uid, p.tenant_id=$tid, p.name=$name, p.role=$role
        MERGE (t)-[:HAS_PERSON]->(p)
        """,
        tid=tid, uid=f"{tid}:person:{name.lower()}", name=name, role=role,
    )


def add_habit(tid: str, person: str | None, desc: str, rule: str | None = None, start_hour: int | None = None,
              end_hour: int | None = None, learned: bool = False) -> None:
    """A habit explains a rule firing in an hour window (start_hour <= hour < end_hour)."""
    _q(
        """
        MATCH (t:Tenant {id:$tid})
        MERGE (h:Habit {uid:$uid})
          ON CREATE SET h.frequency = 1
          ON MATCH SET h.frequency = coalesce(h.frequency, 0) + 1
        SET h.tenant_id=$tid, h.desc=$desc, h.name=$rule, h.rule=$rule,
            h.start_hour=$start, h.end_hour=$end, h.learned=$learned
        WITH t, h
        OPTIONAL MATCH (p:Person {uid:$puid})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [1] ELSE [] END | MERGE (t)-[:HAS_HABIT]->(h))
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:HAS_HABIT]->(h))
        """,
        tid=tid, uid=f"{tid}:habit:{_h(desc)}", puid=f"{tid}:person:{person.lower()}" if person else None,
        desc=desc, rule=rule, start=start_hour, end=end_hour, learned=learned,
    )


def record_event(tid: str, frame_id: str, camera_id: str, ts_iso: str, level: str, rule: str, objects: list[str],
                 ocr_texts: list[str], mask_count: int, alert: dict | None = None) -> None:
    """Event + DETECTED_OBJECT (+ TRIGGERED Alert), written in one transaction."""
    euid = f"{tid}:event:{frame_id}"

    def work(tx):  # noqa: ANN001
        tx.run(
            """
            MATCH (t:Tenant {id:$tid})
            MERGE (e:Event {uid:$euid})
              SET e.id=$fid, e.tenant_id=$tid, e.timestamp=datetime($ts), e.type=$rule, e.kind=$rule,
                  e.level=$level, e.camera_id=$cam, e.ocr_texts=$ocr, e.mask_count=$masks
            MERGE (t)-[:HAS_EVENT]->(e)
            """,
            tid=tid, euid=euid, fid=frame_id, ts=ts_iso, rule=rule or "normal", level=level, cam=camera_id,
            ocr=[str(x)[:120] for x in ocr_texts[:10]], masks=mask_count,
        )
        if objects:
            tx.run(
                """
                MATCH (e:Event {uid:$euid})
                UNWIND $objects AS lbl
                MERGE (o:Object {uid: $tid + ':obj:' + lbl})
                  SET o.label=lbl, o.name=lbl, o.tenant_id=$tid, o.last_seen=datetime($ts)
                MERGE (e)-[:DETECTED_OBJECT]->(o)
                """,
                euid=euid, tid=tid, objects=sorted(set(objects)), ts=ts_iso,
            )
        if alert:
            tx.run(
                """
                MATCH (e:Event {uid:$euid})
                MERGE (a:Alert {uid:$auid})
                  SET a.id=$aid, a.tenant_id=$tid, a.level=$level, a.rule=$rule, a.reason=$reason,
                      a.speak_text=$speak, a.image_url=$img
                MERGE (e)-[:TRIGGERED]->(a)
                """,
                euid=euid, auid=f"{tid}:alert:{alert['id']}", aid=alert["id"], tid=tid, level=level, rule=rule,
                reason=alert.get("reason", ""), speak=alert.get("speak_text", ""),
                img=f"/alerts/{alert['id']}/image",
            )

    with driver().session() as s:
        s.execute_write(work)


def add_news(tid: str, headline: str, source: str, url: str | None, ts_iso: str) -> None:
    _q(
        """
        MATCH (t:Tenant {id:$tid})
        MERGE (n:NewsContext {uid:$uid})
          SET n.tenant_id=$tid, n.headline=$headline, n.name=$headline, n.source=$source, n.url=$url,
              n.timestamp=datetime($ts)
        MERGE (t)-[:IN_CONTEXT]->(n)
        """,
        tid=tid, uid=f"{tid}:news:{_h(headline)}", headline=headline[:240], source=source, url=url, ts=ts_iso,
    )


def prune(event_days: int, news_hours: int = 48) -> None:
    _q(
        """
        MATCH (e:Event) WHERE e.timestamp < datetime() - duration({days:$d})
        WITH e LIMIT 2000
        OPTIONAL MATCH (e)-[:TRIGGERED]->(a:Alert)
        DETACH DELETE a, e
        """,
        d=event_days,
    )
    _q("MATCH (n:NewsContext) WHERE n.timestamp < datetime() - duration({hours:$h}) DETACH DELETE n", h=news_hours)


# ------------------------------------------------------------------ reads
def habits(tid: str) -> list[dict]:
    return _read(
        """
        MATCH (t:Tenant {id:$tid})-[:HAS_HABIT]->(h:Habit)
        RETURN h.desc AS desc, h.rule AS rule, h.start_hour AS start_hour, h.end_hour AS end_hour,
               coalesce(h.learned,false) AS learned, coalesce(h.frequency,1) AS frequency
        UNION
        MATCH (t:Tenant {id:$tid})-[:HAS_PERSON]->(:Person)-[:HAS_HABIT]->(h:Habit)
        RETURN h.desc AS desc, h.rule AS rule, h.start_hour AS start_hour, h.end_hour AS end_hour,
               coalesce(h.learned,false) AS learned, coalesce(h.frequency,1) AS frequency
        """,
        tid=tid,
    )


def recent_events(tid: str, hours: int = 24, limit: int = 15) -> list[dict]:
    return _read(
        """
        MATCH (t:Tenant {id:$tid})-[:HAS_EVENT]->(e:Event)
        WHERE e.timestamp > datetime() - duration({hours:$h})
        RETURN e.type AS type, e.level AS level, e.camera_id AS camera, toString(e.timestamp) AS ts
        ORDER BY e.timestamp DESC LIMIT $limit
        """,
        tid=tid, h=hours, limit=limit,
    )


def news(tid: str, hours: int = 24) -> list[dict]:
    return _read(
        """
        MATCH (t:Tenant {id:$tid})-[:IN_CONTEXT]->(n:NewsContext)
        WHERE n.timestamp > datetime() - duration({hours:$h})
        RETURN n.headline AS headline, n.source AS source, toString(n.timestamp) AS ts
        ORDER BY n.timestamp DESC LIMIT 10
        """,
        tid=tid, h=hours,
    )


def _clean(v: Any) -> Any:
    if hasattr(v, "iso_format"):
        return v.iso_format()
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, str) and len(v) > 240:
        return v[:237] + "..."
    return v


def _node(n) -> dict:  # noqa: ANN001
    props = {k: _clean(v) for k, v in dict(n).items() if k not in {"uid", "tenant_id"}}
    return {"id": n.element_id, "labels": sorted(n.labels), "props": props}


def subgraph(tid: str) -> dict:
    """Tenant, its cameras/people/habits/SOP/news, and its 25 latest events with objects and alerts."""
    nodes: dict[str, dict] = {}
    for rec in driver().execute_query(
        """
        MATCH (t:Tenant {id:$tid})
        OPTIONAL MATCH (t)-[:HAS_CAMERA|HAS_PERSON|HAS_SOP|IN_CONTEXT|HAS_HABIT]->(n)
        OPTIONAL MATCH (t)-[:HAS_PERSON]->(:Person)-[:HAS_HABIT]->(h:Habit)
        RETURN t, collect(DISTINCT n)[..100] AS direct, collect(DISTINCT h)[..50] AS habits
        """,
        parameters_={"tid": tid}, routing_=RoutingControl.READ,
    )[0]:
        for item in ([rec["t"]] if rec["t"] is not None else []) + list(rec["direct"]) + list(rec["habits"]):
            nodes[item.element_id] = _node(item)
    if not nodes:
        return {"nodes": [], "edges": []}
    recs, _, _ = driver().execute_query(
        """
        MATCH (t:Tenant {id:$tid})-[:HAS_EVENT]->(e:Event)
        WITH e ORDER BY e.timestamp DESC LIMIT 25
        OPTIONAL MATCH (e)-[:TRIGGERED]->(a:Alert)
        OPTIONAL MATCH (e)-[:DETECTED_OBJECT]->(o:Object)
        RETURN e, collect(DISTINCT a) AS alerts, collect(DISTINCT o) AS objs
        """,
        parameters_={"tid": tid}, routing_=RoutingControl.READ,
    )
    for rec in recs:
        for item in [rec["e"], *rec["alerts"], *rec["objs"]]:
            nodes[item.element_id] = _node(item)
    edge_recs, _, _ = driver().execute_query(
        """
        MATCH (a)-[r]->(b)
        WHERE elementId(a) IN $ids AND elementId(b) IN $ids
        RETURN elementId(a) AS s, elementId(b) AS t, type(r) AS type
        """,
        parameters_={"ids": list(nodes)}, routing_=RoutingControl.READ,
    )
    edges = [{"source": r["s"], "target": r["t"], "type": r["type"]} for r in edge_recs]
    return {"nodes": list(nodes.values()), "edges": edges}
