import base64
import json
import logging
import os
import time
from typing import Any, Dict, List
import httpx

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("grocery-price-agent")

MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() in ("true", "1", "yes")
BASETEN_API_KEY = os.getenv("BASETEN_API_KEY", "").strip()
BASETEN_API_URL = os.getenv("BASETEN_API_URL", "https://inference.baseten.co/v1").rstrip("/")
LLAMA_API_KEY = os.getenv("LLAMA_API_KEY", "").strip()
LLAMA_API_URL = os.getenv("LLAMA_API_URL", "https://api.meta.ai/v1/responses").rstrip("/")

SPARK_MODEL = os.getenv("SPARK_MODEL", "baseten:meta-models/muse-spark-open-1026|muse-spark-1.3")
REASONING_EFFORT_FAST = os.getenv("REASONING_EFFORT_FAST", "minimal")
FAST_HEADROOM_TOKENS = int(os.getenv("FAST_HEADROOM_TOKENS", "512"))
SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "120"))
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")


def resolve_credentials() -> tuple[str, str, str]:
    models = [m.strip() for m in SPARK_MODEL.split("|")]
    if BASETEN_API_KEY:
        for m in models:
            if m.startswith("baseten:"):
                return BASETEN_API_URL, BASETEN_API_KEY, m.replace("baseten:", "")
    if LLAMA_API_KEY:
        for m in models:
            return LLAMA_API_URL, LLAMA_API_KEY, m.replace("baseten:", "")
    return "", "", ""


def analyze_grocery_prices_with_muse(image_data_uri: str) -> List[Dict[str, Any]]:
    api_url, api_key, model_id = resolve_credentials()

    if MOCK_MODE or not api_key:
        logger.info("[Grocery-Price] MOCK_MODE active or key missing. Returning simulated price intelligence.")
        return [
            {"item_name": "organic_milk_1l", "brand": "Amul", "est_price_inr": 68, "discount_detected": True, "condition": "fresh"},
            {"item_name": "whole_wheat_bread", "brand": "Britannia", "est_price_inr": 50, "discount_detected": False, "condition": "fresh"},
        ]

    prompt = (
        "You are an intelligent retail grocery vision agent. Analyze the provided camera frame showing grocery items, packages, or shelf stock.\n"
        "Identify all consumer goods, estimated local retail prices (in INR), visible branding, and discount badges if present.\n\n"
        "Return ONLY a JSON list of objects matching this schema:\n"
        "[\n"
        "  {\n"
        "    \"item_name\": \"string\",\n"
        "    \"brand\": \"string or unknown\",\n"
        "    \"est_price_inr\": number,\n"
        "    \"discount_detected\": boolean,\n"
        "    \"condition\": \"fresh | packaging_damaged | near_expiry\"\n"
        "  }\n"
        "]\n"
        "Do not include markdown tags or conversational filler."
    )

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_uri}}
                ]
            }
        ],
        "max_tokens": FAST_HEADROOM_TOKENS,
        "reasoning_effort": REASONING_EFFORT_FAST,
        "temperature": 0.1
    }

    try:
        endpoint = f"{api_url}/chat/completions" if not api_url.endswith("/completions") else api_url
        with httpx.Client(timeout=30.0) as client:
            res = client.post(endpoint, headers=headers, json=payload)
            res.raise_for_status()
            text = res.json()["choices"][0]["message"]["content"].strip()
            if text.startswith("```"):
                text = text.split("```")[1].replace("json", "")
            return json.loads(text.strip())
    except Exception as e:
        logger.error(f"[Grocery-Price] Muse Spark API error: {e}")
        return []


def process_grocery_intelligence(tenant_id: str, items: List[Dict[str, Any]]):
    if not items:
        return

    total_est_value = sum(i.get("est_price_inr", 0) for i in items)
    discounted_items = [i for i in items if i.get("discount_detected")]

    payload = {
        "tenant_id": tenant_id,
        "agent": "grocery-price-agent",
        "detected_item_count": len(items),
        "total_estimated_value_inr": total_est_value,
        "discounted_items_count": len(discounted_items),
        "items": items,
        "timestamp": time.time()
    }

    logger.info(f"[Grocery-Price] Syncing pricing matrix for {tenant_id}: Total ~₹{total_est_value}")
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(f"{GATEWAY_URL}/api/events/grocery-price", json=payload)
    except Exception as e:
        logger.warning(f"[Grocery-Price] Gateway notify skipped: {e}")


def main():
    logger.info("Starting Grocery Price Intelligence Agent...")
    tenant_id = os.getenv("TENANT_ID", "home_1_bangalore")

    while True:
        try:
            # Placeholder or latest frame path
            sample_path = "/tmp/grocery_latest.jpg"
            if os.path.exists(sample_path):
                with open(sample_path, "rb") as f:
                    data_uri = f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode('utf-8')}"
            else:
                data_uri = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBD-placeholder"

            logger.info("[Grocery-Price] Processing camera frame...")
            detected = analyze_grocery_prices_with_muse(data_uri)
            process_grocery_intelligence(tenant_id, detected)

        except Exception as e:
            logger.error(f"[Grocery-Price] Loop error: {e}")

        time.sleep(SAMPLE_INTERVAL_SEC)


if __name__ == "__main__":
    main()