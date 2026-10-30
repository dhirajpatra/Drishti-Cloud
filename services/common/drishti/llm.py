# services/common/drishti/llm.py
"""Model client: Meta Model API (Responses API) and Baseten Model APIs (OpenAI-compatible chat completions).

A model setting is one or more specs separated by "|", tried in order (credentials / daily budget / errors move on to the next):
    SPARK_MODEL=muse-spark-1.3                                          Meta only
    SPARK_MODEL=baseten:meta-models/muse-spark-open-1026|muse-spark-1.3 Baseten first (hackathon credits), Meta as the fallback
"""

from __future__ import annotations

import base64
import json
import os
import re
import types
from datetime import date
from typing import Any

import redis
import requests

from .bus import redis_client
from .config import settings
from .log import get_logger

log = get_logger("llm")

class NoOutputText(ValueError):
    """The model answered 200 but with no text (e.g. a segmentation model returning masks)."""


class CallBudgetExceeded(RuntimeError):
    """The daily Meta API call budget has been exhausted."""


def is_responses_api() -> bool:
    """True when LLAMA_API_URL points at the new api.meta.ai Responses endpoint."""
    return "api.meta.ai" in (settings.llama_api_url or "")


def image_part(jpeg: bytes) -> dict:
    b64 = base64.b64encode(jpeg).decode()
    url = f"data:image/jpeg;base64,{b64}"
    return {"type": "input_image", "image_url": url}


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


def _today_key(scope: str = "meta") -> str:
    return f"usage:calls:{date.today().isoformat()}" if scope == "meta" else f"usage:calls:{scope}:{date.today().isoformat()}"


def _limit(scope: str) -> int:
    return settings.max_llm_calls_per_day if scope == "meta" else settings.max_baseten_calls_per_day


def _reserve_call(r: redis.Redis, scope: str = "meta") -> None:
    reserved = r.eval(
        "local count=tonumber(redis.call('GET', KEYS[1]) or '0') "
        "if count >= tonumber(ARGV[1]) then return 0 end "
        "local next=redis.call('INCR', KEYS[1]) "
        "redis.call('EXPIRE', KEYS[1], 259200) "
        "return next",
        1,
        _today_key(scope),
        _limit(scope),
    )
    if not reserved:
        raise CallBudgetExceeded(f"daily {scope} API call budget exhausted")


def split_models(spec: str) -> list[str]:
    return [m.strip() for m in (spec or "").split("|") if m.strip()]


def provider_of(spec: str) -> tuple[str, str]:
    """('baseten', slug) for "baseten:<slug>", otherwise ('meta', model)."""
    if spec.lower().startswith("baseten:"):
        return "baseten", spec.split(":", 1)[1].strip()
    return "meta", spec


def _scope_ok(scope: str, r: redis.Redis) -> bool:
    if scope == "baseten" and not settings.baseten_api_key:
        return False
    return int(r.get(_today_key(scope)) or 0) < _limit(scope)


def budget_ok(r: redis.Redis | None = None, model: str | None = None) -> bool:
    """True while at least one provider that could serve `model` (all providers when omitted) has calls left today."""
    r = r or redis_client()
    scopes = {provider_of(m)[0] for m in split_models(model)} if model else {"meta", "baseten"}
    return any(_scope_ok(sc, r) for sc in scopes)


def _record(model: str, usage: Any, r: redis.Redis) -> None:  # `model` is the full spec, so Baseten usage is listed separately
    day = date.today().isoformat()
    pipe = r.pipeline()
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

def _usage(data: dict):
    u = data.get("usage") or {}
    return types.SimpleNamespace(prompt_tokens=u.get("input_tokens", 0), completion_tokens=u.get("output_tokens", 0))


def is_thinking_model(model: str) -> bool:
    """muse-* models think before answering and, unless told otherwise, default to effort 'high'."""
    return model.lower().startswith("muse")


def thinking_effort(model: str, reasoning: bool) -> str | None:
    """Effort sent with the request. reasoning=False is the FAST path (short extraction/classification calls):
    still sent explicitly ('minimal'), because omitting the field would give the model's expensive default."""
    if not is_thinking_model(model):
        return None
    name, default = ("REASONING_EFFORT", "low") if reasoning else ("REASONING_EFFORT_FAST", "minimal")
    value = os.getenv(name, default).strip().lower()
    return None if value in ("", "default") else value


def thinking_headroom(model: str, reasoning: bool) -> int:
    """Extra output tokens on top of the answer budget, for the model's own thinking."""
    if not is_thinking_model(model):
        return 0
    return int(os.getenv("REASONING_HEADROOM_TOKENS", "512")) if reasoning else int(os.getenv("FAST_HEADROOM_TOKENS", "256"))


def _responses_call(model: str, messages: list[dict], max_tokens: int, reasoning: bool, r: redis.Redis) -> dict:
    """One POST to /v1/responses (counts against the daily call budget)."""
    url = settings.llama_api_url.rstrip("/")
    if not url.endswith("/responses"):
        url += "/responses"
    headers = {"Authorization": f"Bearer {settings.llama_api_key}", "Content-Type": "application/json"}
    payload: dict = {"model": model, "input": to_responses_input(messages), "max_output_tokens": max_tokens}
    effort = thinking_effort(model, reasoning)
    if effort:
        payload["reasoning"] = {"effort": effort}

    def send() -> requests.Response:
        _reserve_call(r)
        return requests.post(url, headers=headers, json=payload, timeout=90)

    resp = send()
    fallbacks = [e for e in ("low",) if e != effort]
    while resp.status_code == 400 and "reasoning" in payload and any(w in resp.text.lower() for w in ("reasoning", "effort")):
        if fallbacks:                                   # e.g. 'minimal' not accepted -> try 'low', then drop the field
            nxt = fallbacks.pop(0)
            log.warning("model %s rejected reasoning.effort=%s - retrying with %s", model, payload["reasoning"]["effort"], nxt)
            payload["reasoning"] = {"effort": nxt}
        else:
            log.warning("model %s rejected reasoning.effort - retrying without it", model)
            payload.pop("reasoning")
        resp = send()
    if resp.status_code != 200:
        log.error("Meta API Error %s: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
    return resp.json()


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """Chat messages (text / image_url / input_text / input_image parts) -> OpenAI chat-completions messages."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                t = part.get("type")
                if t in ("text", "input_text", "output_text"):
                    parts.append({"type": "text", "text": part.get("text", "")})
                elif t in ("image_url", "input_image"):
                    iu = part.get("image_url")
                    parts.append({"type": "image_url", "image_url": {"url": iu.get("url") if isinstance(iu, dict) else iu}})
            content = parts
        out.append({"role": m.get("role", "user"), "content": content})
    return out


def extract_chat_text(data: dict) -> tuple[str, str | None]:
    """(assistant text, finish_reason) from a chat-completions reply."""
    choice = ((data.get("choices") or [{}])[0]) or {}
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return (content or "").strip(), choice.get("finish_reason")


def _baseten_call(slug: str, messages: list[dict], max_tokens: int, r: redis.Redis) -> dict:
    """One POST to Baseten /chat/completions (counts against MAX_BASETEN_CALLS_PER_DAY)."""
    _reserve_call(r, "baseten")
    payload: dict = {"model": slug, "messages": to_openai_messages(messages), "max_tokens": max_tokens}
    effort = os.getenv("BASETEN_REASONING_EFFORT", "").strip()
    if effort:
        payload["reasoning_effort"] = effort
    resp = requests.post(settings.baseten_api_url.rstrip("/") + "/chat/completions", timeout=90, json=payload,
                         headers={"Authorization": f"Bearer {settings.baseten_api_key}", "Content-Type": "application/json"})
    if resp.status_code != 200:
        log.error("Baseten API Error %s: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
    return resp.json()


def _chat_json_baseten(spec: str, slug: str, messages: list[dict], max_tokens: int, r: redis.Redis, reasoning: bool) -> dict:
    budget = max_tokens + thinking_headroom(slug.split("/")[-1], reasoning)   # Muse reasons before answering here too
    data = _baseten_call(slug, messages, budget, r)
    text, finish = extract_chat_text(data)
    if not text and finish == "length":
        log.warning("%s ran out of tokens (%d) - retrying once with %d", slug, budget, budget * 2)
        _record(spec, _chat_usage(data), r)
        data = _baseten_call(slug, messages, budget * 2, r)
        text, finish = extract_chat_text(data)
    if not text:
        log.error("Baseten returned no text (model=%s) shape: %s", slug, describe_shape(data))
        raise NoOutputText(f"{slug} returned no output text")
    _record(spec, _chat_usage(data), r)
    return parse_json(text)                       # Muse Spark Open has no JSON mode: the prompt asks for JSON, we parse tolerantly


def _chat_usage(data: dict):
    u = data.get("usage") or {}
    return types.SimpleNamespace(prompt_tokens=u.get("prompt_tokens", 0), completion_tokens=u.get("completion_tokens", 0))


def _chat_json_meta(model: str, messages: list[dict], max_tokens: int, r: redis.Redis, reasoning: bool) -> dict:
    if not is_responses_api():
        raise RuntimeError("LLAMA_API_URL must point to the Meta Responses API")
    budget = max_tokens + thinking_headroom(model, reasoning)
    data = _responses_call(model, messages, budget, reasoning, r)
    text = extract_response_text(data)
    reason = (data.get("incomplete_details") or {}).get("reason")
    if not text and data.get("status") == "incomplete" and reason == "max_output_tokens":
        log.warning("%s ran out of tokens while reasoning (%d) - retrying once with %d", model, budget, budget * 2)
        _record(model, _usage(data), r)
        data = _responses_call(model, messages, budget * 2, reasoning, r)
        text = extract_response_text(data)
    if not text:
        log.error("Meta API returned no text (model=%s) shape: %s", model, describe_shape(data))
        raise NoOutputText(f"{model} returned no output text")
    _record(model, _usage(data), r)
    return parse_json(text)


def chat_json(model: str, messages: list[dict], *, max_tokens: int = 700,
              r: redis.Redis | None = None, reasoning: bool = True) -> dict:
    """Try each model spec in `model` ("a|b|c") until one answers; the last error is raised when none does."""
    r = r or redis_client()
    last: Exception | None = None
    for spec in split_models(model):
        prov, name = provider_of(spec)
        if prov == "baseten" and not settings.baseten_api_key:
            last = last or RuntimeError("BASETEN_API_KEY is not set")
            continue
        try:
            if prov == "baseten":
                return _chat_json_baseten(spec, name, messages, max_tokens, r, reasoning)
            return _chat_json_meta(name, messages, max_tokens, r, reasoning)
        except Exception as exc:  # noqa: BLE001 - budget, HTTP error, empty answer: hand over to the next provider
            last = exc
            log.warning("model %s failed (%s: %s)%s", spec, type(exc).__name__, str(exc)[:160],
                        " - trying the next one" if spec != split_models(model)[-1] else "")
    raise last or RuntimeError("no model configured")
