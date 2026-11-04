import json
import logging
import os
import time
from typing import Dict, List, Optional
import httpx

from drishti import bus

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("whatsapp-connector")

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "").strip()
WHATSAPP_TO = [num.strip() for num in os.getenv("WHATSAPP_TO", "").split(",") if num.strip()]
WHATSAPP_TEMPLATE = os.getenv("WHATSAPP_TEMPLATE", "").strip()
API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0").strip()
STARTUP_NOTICE = os.getenv("WHATSAPP_STARTUP_NOTICE", "false").lower() in ("true", "1", "yes")  # off: a restart must not message people
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() in ("true", "1", "yes") or not WHATSAPP_TOKEN


def send_whatsapp_message(recipient: str, message_text: str) -> bool:
    if MOCK_MODE:
        logger.info(f"[WhatsApp-Connector] MOCK SEND to {recipient}:\n{message_text}")
        return True

    url = f"https://graph.facebook.com/{API_VERSION}/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    if WHATSAPP_TEMPLATE:
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": WHATSAPP_TEMPLATE,
                "language": {"code": os.getenv("WHATSAPP_TEMPLATE_LANG", "en_US")},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": message_text}]
                    }
                ]
            }
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": message_text}
        }

    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(url, headers=headers, json=payload)
            res.raise_for_status()
            logger.info(f"[WhatsApp-Connector] Successfully delivered alert to {recipient}")
            return True
    except Exception as err:
        logger.error(f"[WhatsApp-Connector] Failed to send WhatsApp message to {recipient}: {err}")
        return False


def dispatch_alert(alert_title: str, body_details: str, severity: str = "INFO"):
    icon = "🚨" if severity in ("CRITICAL", "HIGH") else "ℹ️"
    formatted_msg = (
        f"{icon} *DRISHTI MULTI-AGENT ALERT* [{severity}]\n"
        f"*Event:* {alert_title}\n"
        f"*Details:* {body_details}\n"
        f"_Time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}_"
    )

    for phone_number in WHATSAPP_TO:
        send_whatsapp_message(phone_number, formatted_msg)


def main():
    logger.info("Initializing WhatsApp Connector Service...")
    if MOCK_MODE:
        logger.info("[WhatsApp-Connector] Running in MOCK MODE (messages will be logged to console).")

    # Opt-in only (WHATSAPP_STARTUP_NOTICE=true): handy once to prove the credentials work
    if STARTUP_NOTICE:
        dispatch_alert(
            alert_title="System Startup",
            body_details="Multi-Agent Vision Pipeline online. Monitoring video streams.",
            severity="INFO"
        )

    while True:
        # Alerts are delivered by action-agent; this service is a stand-alone sender for testing the credentials
        try:
            bus.heartbeat("whatsapp-connector")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(30)


if __name__ == "__main__":
    main()