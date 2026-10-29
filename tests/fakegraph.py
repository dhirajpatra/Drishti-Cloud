"""In-memory stand-in for drishti.graph so tests need no Neo4j. Same public functions, same return shapes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class FakeGraph:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.tenants: dict[str, dict] = {}
        self.cameras: dict[str, set] = {}
        self.people: dict[str, dict] = {}
        self._habits: dict[str, list] = {}
        self.events: dict[str, list] = {}
        self._news: dict[str, list] = {}

    # --- schema / health
    def ping(self) -> bool: return True
    def ensure_schema(self, wait_sec: int = 90) -> None: pass
    def prune(self, event_days: int, news_hours: int = 48) -> None: pass

    # --- writes
    def upsert_tenant(self, tid, ttype, location, vertical, sop_text):
        self.tenants[tid] = {"type": ttype, "location": location, "vertical": vertical, "sop": sop_text}
        self.cameras.setdefault(tid, set()); self.people.setdefault(tid, {}); self._habits.setdefault(tid, [])
        self.events.setdefault(tid, []); self._news.setdefault(tid, [])

    def add_camera(self, tid, camera_id, rtsp_url=None): self.cameras[tid].add(camera_id)
    def add_person(self, tid, name, role): self.people[tid][name] = role

    def add_habit(self, tid, person, desc, rule=None, start_hour=None, end_hour=None, learned=False, **_):
        if not any(h["desc"] == desc for h in self._habits[tid]):
            self._habits[tid].append({"desc": desc, "rule": rule, "start_hour": start_hour, "end_hour": end_hour,
                                      "learned": learned, "frequency": 1})

    def record_event(self, tid, frame_id, camera_id, ts_iso, level, rule, objects, ocr_texts, mask_count, alert=None):
        self.events[tid].append({"type": rule or "observation", "level": level, "camera": camera_id, "ts": ts_iso,
                                 "frame_id": frame_id, "objects": objects, "alert": alert})

    def add_news(self, tid, headline, source, url, ts_iso):
        self._news[tid].append({"headline": headline, "source": source, "ts": ts_iso})

    # --- reads
    def habits(self, tid): return list(self._habits.get(tid, []))
    def recent_events(self, tid, hours=24, limit=15):
        return [{k: e[k] for k in ("type", "level", "camera", "ts")} for e in reversed(self.events.get(tid, []))][:limit]
    def news(self, tid, hours=24): return list(reversed(self._news.get(tid, [])))[:10]

    def subgraph(self, tid):
        if tid not in self.tenants:
            return {"nodes": [], "edges": []}
        nodes = [{"id": f"t:{tid}", "labels": ["Tenant"], "props": {"id": tid}}]
        edges = []
        for c in self.cameras[tid]:
            nodes.append({"id": f"c:{c}", "labels": ["Camera"], "props": {"id": c}})
            edges.append({"source": f"t:{tid}", "target": f"c:{c}", "type": "HAS_CAMERA"})
        for i, e in enumerate(self.events[tid][-25:]):
            nodes.append({"id": f"e:{i}", "labels": ["Event"], "props": {"type": e["type"], "level": e["level"]}})
            edges.append({"source": f"t:{tid}", "target": f"e:{i}", "type": "HAS_EVENT"})
        return {"nodes": nodes, "edges": edges}
