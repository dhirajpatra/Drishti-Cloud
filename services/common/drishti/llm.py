"""Meta Model API client (OpenAI-compatible endpoint) with usage accounting and a daily call budget.

LOW_COST_MODE guard rails live here too: `budget_ok()` stops model calls once MAX_LLM_CALLS_PER_DAY is
reached; callers then fall back to the deterministic rules.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import date
from typing import Any

import redis
from openai import OpenAI

from .bus import redis_client
from .config import settings
from .log import get_logger

log = get_logger("llm")
_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.llama_api_key:
            raise RuntimeError("LLAMA_API_KEY is not set (or enable MOCK_MODE=true)")
        _client = OpenAI(base_url=settings.llama_api_url, api_key=settings.llama_api_key, timeout=60, max_retries=2)
    return _client


def image_part(jpeg: bytes) -> dict:
    b64 = base64.b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def _today_key() -> str:
    return f"usage:calls:{date.today().isoformat()}"


def budget_ok(r: redis.Redis | None = None) -> bool:
    r = r or redis_client()
    return int(r.get(_today_key()) or 0) < settings.max_llm_calls_per_day


def _record(model: str, usage: Any, r: redis.Redis) -> None:
    day = date.today().isoformat()
    pipe = r.pipeline()
    pipe.incr(_today_key())
    pipe.expire(_today_key(), 3 * 86400)
    key = f"usage:{day}:{model}"
    pipe.hincrby(key, "calls", 1)
    if usage is not None:
        pipe.hincrby(key, "prompt_tokens", int(getattr(usage, "prompt_tokens", 0) or 0))
        pipe.hincrby(key, "completion_tokens", int(getattr(usage, "completion_tokens", 0) or 0))
    pipe.expire(key, 14 * 86400)
    pipe.execute()


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json(text: str) -> dict:
    """Tolerant JSON extraction: strips code fences and surrounding prose."""
    text = _FENCE.sub("", (text or "").strip())
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            raise ValueError(f"model returned no JSON: {text[:120]!r}") from None
        out = json.loads(text[a : b + 1])
    if not isinstance(out, dict):
        raise ValueError("model JSON is not an object")
    return out


def chat_json(model: str, messages: list[dict], *, max_tokens: int = 700, temperature: float = 0.1,
              r: redis.Redis | None = None) -> dict:
    """One chat completion that must return a JSON object. Raises on API or parse failure."""
    r = r or redis_client()
    resp = client().chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
    _record(model, getattr(resp, "usage", None), r)
    return parse_json(resp.choices[0].message.content or "")
