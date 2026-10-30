"""Remote delivery: WhatsApp Business Cloud API (Meta) first, Slack-style webhook as the fallback."""
import importlib.util
import pathlib

import pytest

from drishti.config import settings

spec = importlib.util.spec_from_file_location("action_agent", pathlib.Path(__file__).parents[1] / "services/action-agent/main.py")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)

PAYLOAD = {"message": "[CRITICAL] home_1: Possible physical abuse", "alert_id": "a1"}
P = {"tenant_id": "home_1", "level": "CRITICAL", "rule": "physical_abuse"}


class Resp:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "WT")
    monkeypatch.setattr(settings, "whatsapp_phone_id", "123456")
    monkeypatch.setattr(settings, "whatsapp_to", ["919876543210", "918888888888"])
    monkeypatch.setattr(settings, "whatsapp_template", "")
    monkeypatch.setattr(settings, "notify_webhook_url", "")


def test_free_text_and_template_bodies(monkeypatch):
    t = aa.whatsapp_body("91999", "line1\nline2")
    assert t["type"] == "text" and t["text"]["body"] == "line1 line2" and t["messaging_product"] == "whatsapp"
    monkeypatch.setattr(settings, "whatsapp_template", "drishti_alert")
    b = aa.whatsapp_body("91999", "hello")
    assert b["type"] == "template" and b["template"]["name"] == "drishti_alert"
    assert b["template"]["components"][0]["parameters"] == [{"type": "text", "text": "hello"}]


def test_sent_to_every_recipient_with_bearer_token(monkeypatch):
    calls = []
    monkeypatch.setattr(aa.httpx, "post", lambda url, **kw: calls.append((url, kw)) or Resp())
    assert aa.deliver_remote("whatsapp", PAYLOAD, P) == "sent"
    assert [c[1]["json"]["to"] for c in calls] == ["919876543210", "918888888888"]
    assert calls[0][0] == "https://graph.facebook.com/v21.0/123456/messages" and calls[0][1]["headers"]["Authorization"] == "Bearer WT"


def test_partial_and_failed_and_webhook_fallback(monkeypatch):
    replies = iter([Resp(), Resp(400)])
    monkeypatch.setattr(aa.httpx, "post", lambda url, **kw: next(replies))
    assert aa.deliver_remote("whatsapp", PAYLOAD, P) == "partial"
    monkeypatch.setattr(aa.httpx, "post", lambda url, **kw: Resp(401))
    assert aa.deliver_remote("whatsapp", PAYLOAD, P) == "failed"
    seen = []

    def post(url, **kw):
        seen.append(url)
        return Resp(401) if "facebook" in url else Resp()
    monkeypatch.setattr(settings, "notify_webhook_url", "https://hooks.example/x")
    monkeypatch.setattr(aa.httpx, "post", post)
    assert aa.deliver_remote("whatsapp", PAYLOAD, P) == "sent" and seen[-1] == "https://hooks.example/x"


def test_not_configured_just_logs(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "")
    assert not aa.whatsapp_enabled() and aa.deliver_remote("whatsapp", PAYLOAD, P) == "logged"
