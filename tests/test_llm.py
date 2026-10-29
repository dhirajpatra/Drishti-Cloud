"""Meta Responses API request/response shaping (no network)."""
from drishti import llm
from drishti.config import settings


def test_image_part_matches_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "llama_api_url", "https://api.meta.ai/v1/responses")
    assert llm.image_part(b"x")["type"] == "input_image"
    monkeypatch.setattr(settings, "llama_api_url", "https://api.llama.com/compat/v1/")
    assert llm.image_part(b"x")["type"] == "image_url"


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
