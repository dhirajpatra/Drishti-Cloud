"""Admin API: everything the dashboard's Admin tab reads and writes.

Settings live in Postgres (siteconfig / camera_sources); agents re-read them every few seconds, so a save takes effect without
editing .env or restarting anything. Camera passwords are encrypted at rest and are WRITE-ONLY: no endpoint ever returns one.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from drishti import bus, db, graph, siteconfig, vault
from drishti.config import settings
from drishti.verticals import ACTION_KINDS, RULE_INFO, THRESHOLD_INFO, VERTICALS

class SiteIn(BaseModel):          # module level: FastAPI cannot resolve a model defined inside a function
    name: str | None = Field(default=None, min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=120)


CAM_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


def build_router(require_admin, get_tenant) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    def _guard(request: Request, tenant_id: str | None = None) -> dict | None:
        require_admin(request)
        return get_tenant(tenant_id) if tenant_id else None

    def _cam(camera: str) -> str:
        if camera and not CAM_RE.match(camera):
            raise HTTPException(422, "camera id must be 1-40 chars of [A-Za-z0-9_-]")
        return camera

    def _env_feeds(tenant_id: str) -> dict[str, dict]:
        return {f["camera"]: f for f in siteconfig.parse_feeds(os.environ.get("RTSP_FEEDS", "")) if f["tenant"] == tenant_id}

    def catalog(vertical: str) -> dict:
        v = VERTICALS[vertical]
        return {
            "vertical": vertical, "vertical_label": v["label"], "levels": list(siteconfig.LEVELS), "action_kinds": ACTION_KINDS,
            "rules": [{"id": rid, "label": RULE_INFO[rid][0], "description": RULE_INFO[rid][2], "default_level": RULE_INFO[rid][1]}
                      for rid in v["rules"]],
            "thresholds": [{"key": k, "label": i[0], "unit": i[1], "min": i[2], "max": i[3]}
                           for k, i in THRESHOLD_INFO.items() if vertical in i[4]],
            "schemes": list(siteconfig.SCHEMES),
        }

    # ------------------------------------------------------------------ overview
    @router.get("/overview")
    def overview(request: Request):
        require_admin(request)
        r = bus.redis_client()
        out = []
        for t in db.all_tenants():
            tid = t["id"]
            env, rows = _env_feeds(tid), {c["camera_id"]: c for c in db.list_camera_sources(tid)}
            live = {c["camera_id"]: c for c in bus.list_cameras(tid)}
            with_cfg = set(db.list_site_config_scopes(tid))
            cams = []
            for cid in sorted(set(env) | set(rows) | set(live)):
                st = r.hgetall(f"feedstat:{tid}:{cid}")
                cams.append({"camera_id": cid,
                             "source": "database" if cid in rows else ("env" if cid in env else "edge"),
                             "enabled": rows[cid]["enabled"] if cid in rows else True,
                             "last_frame": (live.get(cid) or {}).get("updated_at"),
                             "level": (live.get(cid) or {}).get("level"),
                             "ok": (st.get("ok") == "1") if st else None, "error": st.get("error", "") if st else "",
                             "customised": cid in with_cfg})
            out.append({"id": tid, "name": t["name"], "location": t.get("location") or "", "vertical": t["vertical"],
                        "vertical_label": VERTICALS[t["vertical"]]["label"], "customised": "" in with_cfg, "cameras": cams})
        return {"tenants": out, "auth_required": settings.auth_required, "secrets_key_custom": vault.key_is_custom(),
                "whatsapp_configured": bool(settings.whatsapp_token and settings.whatsapp_phone_id),
                "default_whatsapp_numbers": len(settings.whatsapp_to), "default_webhook": bool(settings.notify_webhook_url)}

    # ------------------------------------------------------------------ alarm / notification / sampling configuration
    @router.get("/tenants/{tenant_id}/config")
    def get_config(tenant_id: str, request: Request, camera: str = ""):
        t = _guard(request, tenant_id)
        camera = _cam(camera)
        inh = siteconfig.inherited(tenant_id, camera, t["vertical"])
        over = db.get_site_config(tenant_id, camera)
        return {"scope": camera or "site", "camera": camera, "defaults": inh, "overrides": over,
                "effective": siteconfig.merge(inh, over), "catalog": catalog(t["vertical"]),
                "instructions_default": "", "sop": db.get_sop(t["vertical"]) or VERTICALS[t["vertical"]]["sop"]}

    @router.put("/tenants/{tenant_id}/config")
    def put_config(tenant_id: str, request: Request, camera: str = "", body: dict = Body(...)):
        t = _guard(request, tenant_id)
        camera = _cam(camera)
        try:
            siteconfig.save(tenant_id, camera, t["vertical"], body.get("config", body))
        except siteconfig.ConfigError as exc:
            raise HTTPException(422, str(exc)) from None
        return get_config(tenant_id, request, camera)

    @router.delete("/tenants/{tenant_id}/config")
    def reset_config(tenant_id: str, request: Request, camera: str = ""):
        _guard(request, tenant_id)
        db.delete_site_config(tenant_id, _cam(camera))
        siteconfig.invalidate()
        return {"ok": True}

    # ------------------------------------------------------------------ site details + SOP
    @router.put("/tenants/{tenant_id}/site")
    def put_site(tenant_id: str, body: SiteIn, request: Request):
        _guard(request, tenant_id)
        t = db.update_tenant(tenant_id, body.name, body.location)
        try:
            graph.upsert_tenant(tenant_id, VERTICALS[t["vertical"]]["tenant_type"], t.get("location") or "", t["vertical"],
                                db.get_sop(t["vertical"]) or VERTICALS[t["vertical"]]["sop"])
        except Exception:  # noqa: BLE001 - the graph is memory; Postgres is the source of truth
            pass
        return {"id": tenant_id, "name": t["name"], "location": t.get("location") or ""}

    @router.put("/tenants/{tenant_id}/sop")
    def put_sop(tenant_id: str, request: Request, body: dict = Body(...)):
        t = _guard(request, tenant_id)
        text = str(body.get("text", "")).strip()
        if not 20 <= len(text) <= 8000:
            raise HTTPException(422, "SOP must be 20-8000 characters")
        db.set_sop(t["vertical"], text)
        return {"ok": True, "note": f"shared by every {VERTICALS[t['vertical']]['label']} site"}

    # ------------------------------------------------------------------ cameras
    @router.get("/tenants/{tenant_id}/cameras/{camera}")
    def get_camera(tenant_id: str, camera: str, request: Request):
        _guard(request, tenant_id)
        row = db.get_camera_source(tenant_id, _cam(camera))
        if row:
            return {**siteconfig.public_source(row), "source": "database"}
        env = _env_feeds(tenant_id).get(camera)
        if env:
            p = siteconfig.parse_url(env["url"])
            return {"camera_id": camera, "scheme": p["scheme"], "host": p["host"], "port": p["port"], "path": p["path"],
                    "username": p["username"], "has_password": bool(p["password"]), "enabled": True, "source": "env"}
        return {"camera_id": camera, "scheme": "http", "host": "", "port": None, "path": "/", "username": "",
                "has_password": False, "enabled": True, "source": "edge"}

    @router.put("/tenants/{tenant_id}/cameras/{camera}")
    def put_camera(tenant_id: str, camera: str, request: Request, body: dict = Body(...)):
        _guard(request, tenant_id)
        camera = _cam(camera)
        env = _env_feeds(tenant_id).get(camera)
        if env and not db.get_camera_source(tenant_id, camera) and body.get("password") in (None, ""):
            body = {**body, "password": siteconfig.parse_url(env["url"])["password"]}   # first save of an env camera keeps its password
        try:
            siteconfig.save_source(tenant_id, camera, body)
        except siteconfig.ConfigError as exc:
            raise HTTPException(422, str(exc)) from None
        return get_camera(tenant_id, camera, request)

    @router.delete("/tenants/{tenant_id}/cameras/{camera}")
    def delete_camera(tenant_id: str, camera: str, request: Request):
        _guard(request, tenant_id)
        n = db.delete_camera_source(tenant_id, _cam(camera))
        if not n:
            raise HTTPException(404, "no saved connection for this camera (cameras from RTSP_FEEDS are managed in .env)")
        return {"ok": True}

    @router.post("/tenants/{tenant_id}/cameras/{camera}/test")
    def test_camera(tenant_id: str, camera: str, request: Request, body: dict = Body(default={})):
        """Grab ONE frame with the form's current values (or the saved ones) through the feed-ingestor, which is the process
        that can reach the camera network. The password is used for this call only and never echoed."""
        _guard(request, tenant_id)
        camera = _cam(camera)
        try:
            if body.get("host") or body.get("scheme") == "file":
                src = siteconfig.validate_source({**body, "enabled": True})
                pw = body.get("password")
                if pw in (None, ""):
                    row = db.get_camera_source(tenant_id, camera)
                    pw = vault.decrypt(row["password_enc"]) if row and row["password_enc"] and row["username"] == src["username"] else ""
                    if not pw:
                        env = _env_feeds(tenant_id).get(camera)
                        pw = siteconfig.parse_url(env["url"])["password"] if env else ""
                url = siteconfig.build_url(src, pw)
            else:
                f = {x["camera"]: x for x in siteconfig.all_feeds(os.environ.get("RTSP_FEEDS", ""))
                     if x["tenant"] == tenant_id}.get(camera)
                if not f:
                    raise HTTPException(404, "no saved connection to test; fill in the form first")
                url = f["url"]
        except siteconfig.ConfigError as exc:
            raise HTTPException(422, str(exc)) from None
        rid, r = uuid.uuid4().hex, bus.redis_client()
        r.rpush("camtest:req", json.dumps({"id": rid, "tenant": tenant_id, "url": url}))
        r.expire("camtest:req", 120)
        deadline = time.time() + 20
        while time.time() < deadline:
            raw = r.get(f"camtest:res:{rid}")
            if raw:
                res = json.loads(raw)
                img = bus.redis_bin().get(f"camtest:img:{rid}")
                if img:
                    res["preview"] = "data:image/jpeg;base64," + base64.b64encode(img).decode()
                return res
            time.sleep(0.4)
        return {"ok": False, "bytes": 0, "error": "no answer from the feed-ingestor within 20 s (is it running?)"}

    return router
