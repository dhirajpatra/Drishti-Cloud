"""Meta Responses API request/response shaping (no network)."""
import copy
from drishti import llm
from drishti.config import settings


def test_image_part_matches_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "llama_api_url", "https://api.meta.ai/v1/responses")
    assert llm.image_part(b"x")["type"] == "input_image"
    monkeypatch.setattr(settings, "llama_api_url", "https://api.llama.com/compat/v1/")
    assert llm.image_part(b"x")["type"] == "input_image"


def test_messages_converted_for_responses_api(monkeypatch):
    monkeypatch.setattr(settings, "llama_api_url", "https://api.meta.ai/v1/responses")
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}, llm.image_part(b"x"),
                                         {"type": "image_url", "image_url": {"url": "data:u"}}]},
            {"role": "user", "content": "plain"}]
    out = llm.to_responses_input(msgs)
    assert [p["type"] for p in out[0]["content"]] == ["input_text", "input_image", "input_image"]
    assert out[0]["content"][2]["image_url"] == "data:u"
    assert out[1]["content"] == "plain"


def test_extract_text_skips_reasoning_item():
    data = {"output": [{"type": "reasoning", "summary": []},
                       {"type": "message", "content": [{"type": "output_text", "text": '{"a": 1}'}]}]}
    assert llm.parse_json(llm.extract_response_text(data)) == {"a": 1}
    assert llm.extract_response_text({"output_text": "{}"}) == "{}"


def test_extract_text_any_part_type_and_shape_dump():
    data = {"output": [{"type": "message", "content": [{"type": "weird_part", "text": '{"objects": []}'}]}]}
    assert llm.extract_response_text(data) == '{"objects": []}'
    empty = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "", "annotations": []}]}]}
    assert llm.extract_response_text(empty) == ""
    assert "annotations" in llm.describe_shape(empty)


def test_sam_falls_back_to_spark_when_no_text(monkeypatch):
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("sam_agent", pathlib.Path(__file__).parents[1] / "services/sam3-segmenter/main.py")
    sam = importlib.util.module_from_spec(spec); spec.loader.exec_module(sam)
    calls = []

    def fake(model, msgs, **kw):
        calls.append(model)
        if model == settings.sam_model:
            raise llm.NoOutputText("empty")
        return {"objects": [{"label": "bag", "bbox": [0.1, 0.1, 0.4, 0.4]}]}

    monkeypatch.setattr(sam.llm, "chat_json", fake)
    monkeypatch.setattr(settings, "sam_model", "sam-3.1"); monkeypatch.setattr(settings, "spark_model", "muse-spark-1.3")
    class R(dict):
        def get(self, k): return dict.get(self, k)
        def set(self, k, v, ex=None): self[k] = v
    rr = R()
    out = sam.sam_segment(b"x", "mall_security", rr)
    assert calls == ["sam-3.1", "muse-spark-1.3"] and out[0]["label"] == "bag"
    assert rr.get(sam.SAM_OFF_KEY)             # breaker tripped
    calls.clear(); sam.sam_segment(b"x", "mall_security", rr)
    assert calls == ["muse-spark-1.3"]         # SAM skipped while breaker is open


def test_reasoning_budget_and_retry(monkeypatch):
    monkeypatch.setattr(settings, "llama_api_url", "https://api.meta.ai/v1/responses")
    sent = []
    incomplete = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                  "output": [{"type": "reasoning", "summary": []}], "usage": {"output_tokens": 5}}
    ok = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"k": 1}'}]}]}

    def fake(model, messages, max_tokens, reasoning, r):
        sent.append((max_tokens, reasoning))
        return incomplete if len(sent) == 1 else ok

    monkeypatch.setattr(llm, "_responses_call", fake)
    monkeypatch.setattr(llm, "_record", lambda *a, **k: None)
    assert llm.chat_json("muse-spark-1.3", [], max_tokens=900, r=object()) == {"k": 1}
    assert sent == [(1412, True), (2824, True)]


def test_chat_json_rejects_non_meta_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "llama_api_url", "https://api.llama.com/compat/v1/")
    try:
        llm.chat_json("model", [], r=object())
    except RuntimeError as exc:
        assert "Meta Responses API" in str(exc)
    else:
        raise AssertionError("chat_json should reject non-Meta endpoints")


def test_call_budget_is_reserved_atomically(monkeypatch):
    class BudgetRedis:
        count = 0

        def eval(self, _script, _keys, _key, limit):
            if self.count >= limit:
                return 0
            self.count += 1
            return self.count

        def get(self, _key):
            return self.count

    r = BudgetRedis()
    monkeypatch.setattr(settings, "max_llm_calls_per_day", 2)
    llm._reserve_call(r)
    llm._reserve_call(r)
    assert not llm.budget_ok(r)
    try:
        llm._reserve_call(r)
    except llm.CallBudgetExceeded:
        pass
    else:
        raise AssertionError("call budget should reject requests at the cap")


def _capture(monkeypatch, replies):
    """Fake requests.post: records payloads, answers from `replies` (status, body) in order."""
    sent = []
    monkeypatch.setattr(llm, "_reserve_call", lambda r: None)
    monkeypatch.setattr(settings, "llama_api_url", "https://api.meta.ai/v1/responses")

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._b, self.text = status, body, str(body)

        def json(self):
            return self._b

        def raise_for_status(self):
            raise RuntimeError(self.status_code)

    def post(url, headers=None, json=None, timeout=None):
        sent.append(copy.deepcopy(json))
        return Resp(*replies[min(len(sent) - 1, len(replies) - 1)])

    monkeypatch.setattr(llm.requests, "post", post)
    return sent


OK = (200, {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]})


def test_fast_call_on_muse_model_pins_minimal_effort_with_small_headroom(monkeypatch):
    for k in ("REASONING_EFFORT", "REASONING_EFFORT_FAST", "FAST_HEADROOM_TOKENS", "REASONING_HEADROOM_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    sent = _capture(monkeypatch, [OK])
    monkeypatch.setattr(llm, "_record", lambda *a, **k: None)
    llm.chat_json("muse-spark-1.3", [], max_tokens=350, r=object(), reasoning=False)
    assert sent[0]["reasoning"] == {"effort": "minimal"}          # NOT omitted (that would mean the default, 'high')
    assert sent[0]["max_output_tokens"] == 350 + 256


def test_decision_call_uses_low_effort_and_bigger_headroom(monkeypatch):
    for k in ("REASONING_EFFORT", "REASONING_HEADROOM_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    sent = _capture(monkeypatch, [OK])
    monkeypatch.setattr(llm, "_record", lambda *a, **k: None)
    llm.chat_json("muse-wonder-1.0", [], max_tokens=250, r=object())
    assert sent[0]["reasoning"] == {"effort": "low"} and sent[0]["max_output_tokens"] == 250 + 512


def test_segmentation_model_gets_no_thinking_controls(monkeypatch):
    sent = _capture(monkeypatch, [OK])
    monkeypatch.setattr(llm, "_record", lambda *a, **k: None)
    llm.chat_json("sam-3.1", [], max_tokens=900, r=object(), reasoning=False)
    assert "reasoning" not in sent[0] and sent[0]["max_output_tokens"] == 900


def test_rejected_effort_steps_down_then_drops(monkeypatch):
    monkeypatch.delenv("REASONING_EFFORT_FAST", raising=False)
    bad = (400, {"error": "unsupported value for reasoning.effort"})
    sent = _capture(monkeypatch, [bad, bad, OK])
    monkeypatch.setattr(llm, "_record", lambda *a, **k: None)
    llm.chat_json("muse-spark-1.3", [], max_tokens=100, r=object(), reasoning=False)
    assert [p.get("reasoning") for p in sent] == [{"effort": "minimal"}, {"effort": "low"}, None]
