"""Dynamic, admin-editable configuration (Admin page) - what to watch for, how loudly, how often, and how to reach each camera.

Layers, later wins:  built-in defaults (code + .env)  <  site overrides (camera_id '')  <  camera overrides.
Only OVERRIDES are stored (sparse JSON in Postgres `site_config`), so changing a default in .env still flows through to every
site that has not overridden it, and the Admin page can show "default" vs "customised" per field.
Agents call `effective()`; results are cached for SITE_CONFIG_CACHE_SEC (default 10 s), so a saved change reaches every agent
within seconds, without a restart and without editing .env.
"""
from __future__ import annotations

import copy
import re
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from . import camurl, db, vault
from .config import settings
from .log import get_logger
from .verticals import ACTION_KINDS, RULE_INFO, THRESHOLD_INFO, VERTICALS

log = get_logger("siteconfig")
LEVELS = ("ALERT", "CRITICAL")
SECTIONS = ("rules", "thresholds", "sampling", "notify", "custom_rules", "label_rules", "instructions")


# --------------------------------------------------------------------------- defaults and merge
def defaults_for(vertical: str) -> dict:
    v = VERTICALS[vertical]
    return {
        "rules": {rid: {"enabled": True, "level": None, "cooldown_sec": None, "hours": None} for rid in v["rules"]},
        "thresholds": {k: getattr(settings, k) for k, info in THRESHOLD_INFO.items() if vertical in info[4]},
        "sampling": {"sample_interval_sec": settings.sample_interval_sec, "change_fraction": settings.change_fraction,
                     "min_analysis_gap_sec": settings.min_analysis_gap_sec, "burst_frames": settings.burst_frames,
                     "storyboard": settings.storyboard},
        "notify": {"policy": {lvl: list(v["policy"][lvl]) for lvl in LEVELS}, "whatsapp_to": [], "webhook_url": "",
                   "news_hazards": True, "camera_health": True, "extra_hazard_words": []},
        "custom_rules": [],
        "label_rules": [],
        "instructions": "",
    }


def merge(base: dict, over: dict) -> dict:
    """Deep merge of dicts; lists and scalars in `over` replace."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def prune(over: Any, base: Any) -> Any:
    """Drop everything in `over` that equals the inherited value, so only real customisations are stored."""
    if isinstance(over, dict) and isinstance(base, dict):
        out = {}
        for k, v in over.items():
            p = prune(v, base.get(k))
            if p not in ({}, None) or (v is None and base.get(k) is not None):
                out[k] = p
        return out
    return None if over == base else over


# --------------------------------------------------------------------------- validation
class ConfigError(ValueError):
    pass


def _int(v: Any, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= v <= hi:
        raise ConfigError(f"{what} must be a whole number from {lo} to {hi}")
    return int(v)


def _bool(v: Any, what: str) -> bool:
    if not isinstance(v, bool):
        raise ConfigError(f"{what} must be true or false")
    return v


def _level(v: Any, what: str) -> str:
    if v not in LEVELS:
        raise ConfigError(f"{what} must be ALERT or CRITICAL")
    return v


def _hours(v: Any, what: str) -> list[int] | None:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ConfigError(f"{what} must be [start_hour, end_hour] or empty")
    a, b = _int(v[0], 0, 23, what), _int(v[1], 0, 23, what)
    if a == b:
        raise ConfigError(f"{what}: start and end hour must differ (leave it empty for 'always')")
    return [a, b]


def _text(v: Any, lo: int, hi: int, what: str) -> str:
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
        raise ConfigError(f"{what} must be {lo}-{hi} characters")
    return v.strip()


def _slug(v: Any, what: str, n: int = 24) -> str:
    if not isinstance(v, str) or not re.fullmatch(r"[a-z0-9_]{1,%d}" % n, v):
        raise ConfigError(f"{what} must be 1-{n} characters of a-z, 0-9, _")
    return v


def validate(vertical: str, data: dict) -> dict:
    """Strict whitelist: returns a cleaned copy or raises ConfigError with a message the Admin page can show."""
    if not isinstance(data, dict):
        raise ConfigError("configuration must be an object")
    unknown = set(data) - set(SECTIONS)
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(sorted(unknown))}")
    v, out = VERTICALS[vertical], {}

    if "rules" in data:
        out["rules"] = {}
        for rid, rc in (data["rules"] or {}).items():
            if rid not in v["rules"]:
                raise ConfigError(f"'{rid}' is not an alarm of {v['label']}")
            if not isinstance(rc, dict) or set(rc) - {"enabled", "level", "cooldown_sec", "hours"}:
                raise ConfigError(f"alarm '{rid}': allowed fields are enabled, level, cooldown_sec, hours")
            c: dict = {}
            if "enabled" in rc:
                c["enabled"] = _bool(rc["enabled"], f"{rid}.enabled")
            if "level" in rc:
                c["level"] = None if rc["level"] is None else _level(rc["level"], f"{rid}.level")
            if "cooldown_sec" in rc:
                c["cooldown_sec"] = None if rc["cooldown_sec"] is None else _int(rc["cooldown_sec"], 0, 86400, f"{rid}.cooldown_sec")
            if "hours" in rc:
                c["hours"] = _hours(rc["hours"], f"{rid}.hours")
            out["rules"][rid] = c

    if "thresholds" in data:
        out["thresholds"] = {}
        for k, val in (data["thresholds"] or {}).items():
            info = THRESHOLD_INFO.get(k)
            if not info or vertical not in info[4]:
                raise ConfigError(f"'{k}' is not a threshold of {v['label']}")
            out["thresholds"][k] = _int(val, info[2], info[3], info[0])

    if "sampling" in data:
        s, out["sampling"] = data["sampling"] or {}, {}
        for k, val in s.items():
            if k == "sample_interval_sec":
                out["sampling"][k] = _int(val, 5, 86400, "sample interval")
            elif k == "change_fraction":
                if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0.005 <= val <= 0.5:
                    raise ConfigError("change sensitivity must be between 0.5% and 50%")
                out["sampling"][k] = float(val)
            elif k == "min_analysis_gap_sec":
                out["sampling"][k] = _int(val, 0, 3600, "minimum gap")
            elif k == "burst_frames":
                out["sampling"][k] = _int(val, 1, 8, "burst frames")
            elif k == "storyboard":
                out["sampling"][k] = _bool(val, "storyboard")
            else:
                raise ConfigError(f"unknown sampling field '{k}'")

    if "notify" in data:
        n, out["notify"] = data["notify"] or {}, {}
        for k, val in n.items():
            if k == "policy":
                out["notify"]["policy"] = {}
                for lvl, kinds in (val or {}).items():
                    _level(lvl, "policy level")
                    if not isinstance(kinds, list) or any(x not in ACTION_KINDS for x in kinds):
                        raise ConfigError(f"actions for {lvl} must be a list of: {', '.join(ACTION_KINDS)}")
                    out["notify"]["policy"][lvl] = list(dict.fromkeys(kinds))
            elif k == "whatsapp_to":
                nums = [re.sub(r"[\s+\-()]", "", str(x)) for x in (val or [])]
                if len(nums) > 10 or any(not re.fullmatch(r"\d{8,15}", x) for x in nums):
                    raise ConfigError("WhatsApp numbers: international format with country code, digits only (8-15), at most 10")
                out["notify"][k] = nums
            elif k == "webhook_url":
                if val and not re.fullmatch(r"https?://\S{4,500}", str(val)):
                    raise ConfigError("webhook URL must start with http:// or https://")
                out["notify"][k] = str(val or "")
            elif k in ("news_hazards", "camera_health"):
                out["notify"][k] = _bool(val, k)
            elif k == "extra_hazard_words":
                words = [str(w).strip().lower() for w in (val or []) if str(w).strip()]
                if len(words) > 30 or any(not 2 <= len(w) <= 40 for w in words):
                    raise ConfigError("extra hazard words: up to 30 words of 2-40 characters")
                out["notify"][k] = list(dict.fromkeys(words))
            else:
                raise ConfigError(f"unknown notify field '{k}'")

    if "custom_rules" in data:
        rows, seen = data["custom_rules"] or [], set()
        if not isinstance(rows, list) or len(rows) > 20:
            raise ConfigError("custom rules: a list of at most 20")
        out["custom_rules"] = []
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                raise ConfigError("custom rule must be an object")
            rid = _slug(r.get("id") or f"c{i + 1}", "custom rule id")
            if rid in seen:
                raise ConfigError(f"duplicate custom rule id '{rid}'")
            seen.add(rid)
            out["custom_rules"].append({"id": rid, "text": _text(r.get("text"), 5, 300, "custom rule text"),
                                        "level": _level(r.get("level", "ALERT"), "custom rule level"),
                                        "enabled": _bool(r.get("enabled", True), "custom rule enabled"),
                                        "hours": _hours(r.get("hours"), "custom rule hours")})

    if "label_rules" in data:
        rows, seen = data["label_rules"] or [], set()
        if not isinstance(rows, list) or len(rows) > 20:
            raise ConfigError("object rules: a list of at most 20")
        out["label_rules"] = []
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                raise ConfigError("object rule must be an object")
            rid = _slug(r.get("id") or f"o{i + 1}", "object rule id")
            if rid in seen:
                raise ConfigError(f"duplicate object rule id '{rid}'")
            seen.add(rid)
            op = r.get("op", "present")
            if op not in ("present", "absent", "count_gte"):
                raise ConfigError("object rule condition must be present, absent or count_gte")
            label = re.sub(r"[\s\-]+", "_", str(r.get("label", "")).strip().lower())
            if not re.fullmatch(r"[a-z0-9_]{1,40}", label):
                raise ConfigError("object rule label: letters, digits and _ only (for example person, bag, stove)")
            out["label_rules"].append({
                "id": rid, "label": label, "op": op, "value": _int(r.get("value", 1), 1, 1000, "object rule count"),
                "for_sec": _int(r.get("for_sec", 0), 0, 86400, "object rule duration"),
                "level": _level(r.get("level", "ALERT"), "object rule level"),
                "message": _text(r.get("message") or f"{label} {op}", 1, 200, "object rule message"),
                "enabled": _bool(r.get("enabled", True), "object rule enabled"), "hours": _hours(r.get("hours"), "object rule hours")})

    if "instructions" in data:
        ins = data["instructions"] or ""
        if not isinstance(ins, str) or len(ins) > 2000:
            raise ConfigError("notes must be text of at most 2000 characters")
        out["instructions"] = ins.strip()
    return out


# --------------------------------------------------------------------------- load / save / cache
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def invalidate() -> None:
    _cache.clear()


def inherited(tenant_id: str, camera_id: str, vertical: str) -> dict:
    """What a scope inherits: defaults for a site; defaults + site overrides for a camera."""
    base = defaults_for(vertical)
    return merge(base, db.get_site_config(tenant_id, "")) if camera_id else base


def effective(tenant_id: str, camera_id: str, vertical: str) -> dict:
    """The configuration an agent should act on right now. Never raises: on a database hiccup the last good value, else defaults."""
    key = (tenant_id, camera_id or "")
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < settings.site_cache_sec:
        return hit[1]
    try:
        cfg = merge(defaults_for(vertical), db.get_site_config(tenant_id, ""))
        if camera_id:
            cfg = merge(cfg, db.get_site_config(tenant_id, camera_id))
    except Exception:  # noqa: BLE001
        log.exception("site configuration unavailable - using %s", "the last good copy" if hit else "defaults")
        return hit[1] if hit else defaults_for(vertical)
    _cache[key] = (now, cfg)
    return cfg


def save(tenant_id: str, camera_id: str, vertical: str, data: dict) -> dict:
    """Validate, keep only real customisations, store, drop caches. Returns the new effective configuration."""
    cleaned = prune(validate(vertical, data), inherited(tenant_id, camera_id, vertical))
    # an emptied section must still be able to CLEAR a stored override -> compare with the stored doc below
    if cleaned:
        db.set_site_config(tenant_id, camera_id, cleaned)
    else:
        db.delete_site_config(tenant_id, camera_id)
    invalidate()
    return effective(tenant_id, camera_id, vertical)


def in_hours(hours: list[int] | None, hour: int) -> bool:
    if not hours:
        return True
    a, b = hours
    return a <= hour < b if a < b else (hour >= a or hour < b)


# --------------------------------------------------------------------------- camera connection
SCHEMES = ("http", "https", "rtsp", "rtsps", "file")
_HOST = re.compile(r"^[A-Za-z0-9_.\-:\[\]]{1,253}$")


def validate_source(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ConfigError("camera settings must be an object")
    scheme = str(data.get("scheme", "http")).lower()
    scheme = "rtsp" if scheme == "tcp" else scheme                # ngrok shows an RTSP tunnel as tcp://host:port
    if scheme not in SCHEMES:
        raise ConfigError(f"protocol must be one of: {', '.join(SCHEMES)}")
    path = str(data.get("path") or "/").strip()
    if len(path) > 400 or re.search(r"[\s\x00-\x1f]", path) or ".." in path:
        raise ConfigError("path must be at most 400 characters, with no spaces or '..'")
    enabled = _bool(data.get("enabled", True), "enabled")
    if scheme == "file":
        if not re.fullmatch(r"/app/feeds/[A-Za-z0-9_\-./]{1,200}", path):
            raise ConfigError("a video file must live under /app/feeds/ (the ./feeds folder)")
        return {"scheme": "file", "host": "", "port": None, "path": path, "username": "", "enabled": enabled}
    host = str(data.get("host", "")).strip()
    if not _HOST.match(host):
        raise ConfigError("host must be an IP address or a name (no spaces, no http://)")
    try:
        camurl.check_host(host, settings.allow_private_camera_hosts)
    except camurl.HostNotAllowed as exc:
        raise ConfigError(str(exc)) from None
    port = data.get("port")
    port = None if port in (None, "") else _int(port, 1, 65535, "port")
    user = str(data.get("username") or "")
    if len(user) > 100 or re.search(r"[\x00-\x1f]", user):
        raise ConfigError("username is too long or has control characters")
    return {"scheme": scheme, "host": host, "port": port, "path": path if path.startswith("/") else "/" + path,
            "username": user, "enabled": enabled}


def build_url(src: dict, password: str = "") -> str:
    if src["scheme"] == "file":
        return src["path"]
    host = src["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    auth = ""
    if src.get("username"):
        auth = quote(src["username"], safe="") + (":" + quote(password, safe="") if password else "") + "@"
    return f"{src['scheme']}://{auth}{host}{':' + str(src['port']) if src.get('port') else ''}{src.get('path') or '/'}"


def parse_url(url: str) -> dict:
    """Split a camera URL (from RTSP_FEEDS) into the editable parts."""
    if not re.match(r"^[a-z]+://", url, re.I):
        return {"scheme": "file", "host": "", "port": None, "path": url, "username": "", "password": ""}
    u = urlsplit(url)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return {"scheme": u.scheme.lower(), "host": u.hostname or "", "port": u.port, "path": path,
            "username": unquote(u.username or ""), "password": unquote(u.password or "")}


def mask_url(url: str) -> str:
    return re.sub(r"//[^/@\s]+@", "//***@", url)


def public_source(row: dict) -> dict:
    """What the API may show: everything except the password."""
    return {"camera_id": row["camera_id"], "scheme": row["scheme"], "host": row["host"], "port": row["port"], "path": row["path"],
            "username": row["username"], "has_password": bool(row.get("password_enc")), "enabled": row["enabled"]}


def save_source(tenant_id: str, camera_id: str, data: dict) -> None:
    """Password handling: key absent -> keep the stored one; '' or clear_password -> remove; text -> encrypt and store."""
    clean = validate_source(data)
    pw = data.get("password")
    if data.get("clear_password") is True:
        enc: str | None = ""
    elif pw in (None, ""):
        enc = None                                                # empty box = unchanged
    else:
        pw = str(pw)
        if len(pw) > 200 or re.search(r"[\x00-\x1f]", pw):
            raise ConfigError("password is too long or has control characters")
        enc = vault.encrypt(pw)
    db.upsert_camera_source(tenant_id, camera_id, clean["scheme"], clean["host"], clean["port"], clean["path"],
                            clean["username"], enc, clean["enabled"])


def parse_feeds(spec: str) -> list[dict]:
    """RTSP_FEEDS="tenant:vertical:camera=url,..." -> [{tenant, vertical, camera, url}]"""
    feeds = []
    for item in [x.strip() for x in (spec or "").split(",") if x.strip()]:
        tag, sep, url = item.partition("=")
        parts = tag.split(":")
        if not sep or len(parts) != 3 or parts[1] not in VERTICALS or not url:
            log.error("ignoring malformed feed %r (want tenant:vertical:camera=url)", item.split("=")[0])
            continue
        feeds.append({"tenant": parts[0], "vertical": parts[1], "camera": parts[2], "url": camurl.normalize_url(url)})
    return feeds


def all_feeds(env_spec: str) -> list[dict]:
    """Every camera to sample: .env feeds, overridden or hidden (enabled=false) by what the Admin page stored."""
    merged: dict[tuple[str, str], dict] = {}
    for f in parse_feeds(env_spec):
        merged[(f["tenant"], f["camera"])] = {**f, "source": "env"}
    for row in db.list_camera_sources():
        key = (row["tenant_id"], row["camera_id"])
        if not row["enabled"]:
            merged.pop(key, None)
            continue
        merged[key] = {"tenant": row["tenant_id"], "vertical": row["vertical"], "camera": row["camera_id"], "source": "db",
                       "url": build_url(row, vault.decrypt(row["password_enc"]) if row["password_enc"] else "")}
    for f in merged.values():
        s = effective(f["tenant"], f["camera"], f["vertical"])["sampling"]
        f["interval"], f["burst"] = s["sample_interval_sec"], s["burst_frames"]
    return sorted(merged.values(), key=lambda f: (f["tenant"], f["camera"]))
