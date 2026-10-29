# services/common/drishti/llm.py
"""Meta Model API client - supports the new api.meta.ai /v1/responses endpoint and the old OpenAI-compatible /compat/v1."""

from __future__ import annotations

import base64
import json
import re
import types
from datetime import date
from typing import Any

import redis
import requests
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

class NoOutputText(ValueError):
    """The model answered 200 but with no text (e.g. a segmentation model returning masks)."""


def is_responses_api() -> bool:
    """True when LLAMA_API_URL points at the new api.meta.ai Responses endpoint."""
    return "api.meta.ai" in (settings.llama_api_url or "")


def image_part(jpeg: bytes) -> dict:
    b64 = base64.b64encode(jpeg).decode()
    url = f"data:image/jpeg;base64,{b64}"
    if is_responses_api():
        return {"type": "input_image", "image_url": url}          # Responses API
    return {"type": "image_url", "image_url": {"url": url}}        # old compat/v1


def to_responses_input(messages: list[dict]) -> list[dict]:
    """Convert chat-style messages (text / image_url parts) to Responses API input parts."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            text_type = "output_text" if m.get("role") == "assistant" else "input_text"
            parts = []
            for part in content:
                t = part.get("type")
                if t == "text":
                    parts.append({"type": text_type, "text": part.get("text", "")})
                elif t == "image_url":
                    iu = part.get("image_url")
                    parts.append({"type": "input_image", "image_url": iu.get("url") if isinstance(iu, dict) else iu})
                else:                                               # already input_text / input_image
                    parts.append(part)
            content = parts
        out.append({"role": m.get("role", "user"), "content": content})
    return out


def extract_response_text(data: dict) -> str:
    """Pull the assistant text out of a Responses API reply (skips reasoning items).

    Tolerant: any content part carrying a string `text` counts, whatever its `type`.
    """
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"]
    chunks = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type", "message") in ("reasoning", "function_call"):
            continue
        c = item.get("content")
        if isinstance(c, str):
            chunks.append(c)
        for part in c if isinstance(c, list) else []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks).strip()


def describe_shape(obj: Any, limit: int = 3000, _depth: int = 0) -> str:
    """Compact structural dump of an API reply for logs (long strings and base64 are clipped)."""
    if isinstance(obj, dict):
        body = ", ".join(f"{k}: {describe_shape(v, limit, _depth + 1)}" for k, v in obj.items())
        out = "{" + body + "}"
    elif isinstance(obj, list):
        out = "[" + ", ".join(describe_shape(v, limit, _depth + 1) for v in obj[:8]) + (", ..." if len(obj) > 8 else "") + "]"
    elif isinstance(obj, str):
        out = repr(obj[:160] + ("..." if len(obj) > 160 else ""))
    else:
        out = repr(obj)
    return out[:limit] if _depth == 0 else out


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
    r = r or redis_client()
    
    # NEW API: https://api.meta.ai/v1/responses
    if is_responses_api():
        url = settings.llama_api_url.rstrip("/")
        if not url.endswith("/responses"):
            url += "/responses"
        headers = {"Authorization": f"Bearer {settings.llama_api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "input": to_responses_input(messages), "max_output_tokens": max_tokens}
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            log.error("Meta API Error %s: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        data = resp.json()
        text = extract_response_text(data)
        if not text:
            log.error("Meta API returned no text (model=%s) shape: %s", model, describe_shape(data))
            raise NoOutputText(f"{model} returned no output text")
        usage = data.get("usage") or {}
        _record(model, types.SimpleNamespace(prompt_tokens=usage.get("input_tokens", 0),
                                             completion_tokens=usage.get("output_tokens", 0)), r)
        return parse_json(text)

    # OLD API: compat/v1 - use OpenAI client
    resp = client().chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
    _record(model, getattr(resp, "usage", None), r)
    return parse_json(resp.choices[0].message.content or "")