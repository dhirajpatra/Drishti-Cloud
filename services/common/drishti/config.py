"""Environment-driven settings. Every deployment knob lives here (12-factor)."""
from __future__ import annotations

import os


def _s(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _i(name: str, default: int) -> int:
    try:
        return int(_s(name, str(default)))
    except ValueError:
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(_s(name, str(default)))
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    v = _s(name, "")
    return default if v == "" else v.lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in _s(name, default).split(",") if x.strip()]


class Settings:
    def __init__(self) -> None:
        # --- infrastructure
        self.redis_uri = _s("REDIS_URI", "redis://localhost:6379/0")
        self.database_url = _s("DATABASE_URL", "postgresql://drishti:drishti123@localhost:5432/drishti")
        self.neo4j_uri = _s("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = _s("NEO4J_USER", "neo4j")
        self.neo4j_password = _s("NEO4J_PASSWORD", "drishti123")

        # --- Meta Responses API
        self.llama_api_key = _s("LLAMA_API_KEY")
        self.llama_api_url = _s("LLAMA_API_URL", "https://api.meta.ai/v1/responses")
        # Baseten Model APIs (OpenAI-compatible). Use a model as "baseten:<slug>", e.g. SPARK_MODEL=baseten:meta-models/muse-spark-open-1026
        self.baseten_api_key = _s("BASETEN_API_KEY")
        self.baseten_api_url = _s("BASETEN_API_URL", "https://inference.baseten.co/v1")
        self.max_baseten_calls_per_day = _i("MAX_BASETEN_CALLS_PER_DAY", 1500)
        self.sam_model = _s("SAM_MODEL", "sam-3.1")
        self.spark_model = _s("SPARK_MODEL", "muse-spark-1.3")
        self.wonder_model = _s("WONDER_MODEL", "muse-wonder-1.0")
        self.ocr_model = _s("OCR_MODEL", "muse-spark-1.3")
        self.max_llm_calls_per_day = _i("MAX_LLM_CALLS_PER_DAY", 1000)

        # MOCK_MODE=auto -> on whenever there is no API key, so a laptop demo "just works".
        mm = _s("MOCK_MODE", "auto").lower()
        self.mock_mode = (not (self.llama_api_key or self.baseten_api_key)) if mm == "auto" else mm in {"1", "true", "yes", "on"}

        # --- cost control (LOW_COST_MODE)
        self.allow_private_camera_hosts = _b("ALLOW_PRIVATE_CAMERA_HOSTS", True)   # false on a shared cloud server: LAN IPs are unreachable there anyway
        self.storm_limit = _i("STORM_LIMIT", 12)                  # more ALERTs than this per window = digest (CRITICAL always passes)
        self.storm_window_sec = _i("STORM_WINDOW_SEC", 300)
        self.low_cost_mode = _b("LOW_COST_MODE", True)
        self.event_images = _b("EVENT_IMAGES", True)               # keep a small annotated snapshot of every analysed frame
        self.event_image_ttl_sec = _i("EVENT_IMAGE_TTL_SEC", 21600)  # ...for this long (default 6 h)
        self.sample_interval_sec = _i("SAMPLE_INTERVAL_SEC", 30)
        self.hash_distance = _i("HASH_DISTANCE", 6)          # dhash bits that may differ = "same scene"
        self.force_refresh_sec = _i("FORCE_REFRESH_SEC", 900)  # periodically re-analyse an unchanged scene
        # idle detection: a frame is "idle" (never sent to a model) while < CHANGE_FRACTION of its 32x24 cells moved by more
        # than CELL_DELTA grey levels vs the last analysed frame (brightness-normalised, so lamps / auto-exposure don't count)
        self.change_fraction = _f("CHANGE_FRACTION", 0.04)
        self.cell_delta = _i("CELL_DELTA", 14)
        self.burst_fraction = _f("BURST_FRACTION", 0.15)             # a change this big (running, fight, fall) skips the long gap...
        self.burst_gap_sec = _i("BURST_GAP_SEC", 45)                 # ...but never analyses the same camera more often than this
        self.min_analysis_gap_sec = _i("MIN_ANALYSIS_GAP_SEC", 60)   # a changed scene is analysed at most this often (PIR/force bypass)

        # --- gateway
        self.auth_required = _b("AUTH_REQUIRED", False)
        self.admin_token = _s("ADMIN_TOKEN")
        self.cors_origins = _list("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
        self.max_frames_per_min = _i("MAX_FRAMES_PER_MIN", 60)
        self.burst_frames = _i("BURST_FRAMES", 4)                    # frames grabbed over ~3 s when a motion hook fires (1 = off)
        self.burst_spacing_sec = _f("BURST_SPACING_SEC", 0.8)
        self.storyboard = _b("STORYBOARD", True)                     # send Spark a before/burst/now sequence instead of one still
        self.secrets_key = _s("SECRETS_KEY")                          # Fernet key encrypting camera passwords stored in Postgres
        self.postgres_password = _s("POSTGRES_PASSWORD", "drishti123")
        self.site_cache_sec = _f("SITE_CONFIG_CACHE_SEC", 10)        # how long an agent may use a cached admin configuration
        self.camera_offline_sec = _i("CAMERA_OFFLINE_SEC", 1200)     # silent this long -> "camera offline" notification
        self.motion_hook_token = _s("MOTION_HOOK_TOKEN")            # secret in the motionEye web-hook URL (?token=...)
        self.motion_hook_cooldown_sec = _i("MOTION_HOOK_COOLDOWN_SEC", 60)   # one analysis per camera per this many seconds
        self.max_upload_mb = _i("MAX_UPLOAD_MB", 5)
        self.seed_demo = _b("SEED_DEMO", True)

        # --- pipeline
        self.frame_ttl_sec = _i("FRAME_TTL_SEC", 900)
        self.stream_maxlen = _i("STREAM_MAXLEN", 2000)
        self.max_attempts = _i("MAX_ATTEMPTS", 3)
        self.claim_idle_ms = _i("CLAIM_IDLE_MS", 45000)
        self.alert_cooldown_sec = _i("ALERT_COOLDOWN_SEC", 300)

        # --- rule thresholds
        self.unattended_bag_sec = _i("UNATTENDED_BAG_SEC", 120)
        self.no_motion_sec = _i("NO_MOTION_SEC", 7200)
        self.staff_absent_sec = _i("STAFF_ABSENT_SEC", 3600)
        self.crowd_threshold = _i("CROWD_THRESHOLD", 50)
        self.night_start_hour = _i("NIGHT_START_HOUR", 22)
        self.night_end_hour = _i("NIGHT_END_HOUR", 5)
        self.school_hours_start = _i("SCHOOL_HOURS_START", 9)
        self.school_hours_end = _i("SCHOOL_HOURS_END", 15)
        self.door_open_cm = _i("DOOR_OPEN_CM", 100)

        # --- agents
        self.news_api_key = _s("NEWS_API_KEY")
        self.news_interval_sec = _i("NEWS_INTERVAL_SEC", 1800)
        self.ocr_verticals = _list("OCR_VERTICALS", "icu_hospital,school_children,mall_security")
        self.notify_webhook_url = _s("NOTIFY_WEBHOOK_URL")
        # WhatsApp Business Cloud API (Meta). Alerts of every remote kind (whatsapp / call_family / notify_*) are sent as messages.
        self.whatsapp_token = _s("WHATSAPP_TOKEN")
        self.whatsapp_phone_id = _s("WHATSAPP_PHONE_ID")
        self.whatsapp_to = _list("WHATSAPP_TO", "")                    # comma separated, international format without '+': 919876543210
        self.whatsapp_template = _s("WHATSAPP_TEMPLATE")                # approved template with ONE body variable; empty = free text (24 h window)
        self.whatsapp_template_lang = _s("WHATSAPP_TEMPLATE_LANG", "en_US")
        self.whatsapp_api_version = _s("WHATSAPP_API_VERSION", "v21.0")
        self.graph_event_ttl_days = _i("GRAPH_EVENT_TTL_DAYS", 7)
        self.gateway_url = _s("GATEWAY_URL", "http://gateway:8000")


settings = Settings()
