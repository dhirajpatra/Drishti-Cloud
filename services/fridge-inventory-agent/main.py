import base64
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional
import httpx

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fridge-inventory-agent")

# Configuration Environment Variables
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() in ("true", "1", "yes")
BASETEN_API_KEY = os.getenv("BASETEN_API_KEY", "").strip()
BASETEN_API_URL = os.getenv("BASETEN_API_URL", "https://inference.baseten.co/v1").rstrip("/")
LLAMA_API_KEY = os.getenv("LLAMA_API_KEY", "").strip()
LLAMA_API_URL = os.getenv("LLAMA_API_URL", "https://api.meta.ai/v1/responses").rstrip("/")

SPARK_MODEL = os.getenv(
    "SPARK_MODEL",
    "baseten:meta-models/muse-spark-open-1026|muse-spark-1.3"
)
REASONING_EFFORT_FAST = os.getenv("REASONING_EFFORT_FAST", "minimal")
FAST_HEADROOM_TOKENS = int(os.getenv("FAST_HEADROOM_TOKENS", "512"))
SAMPLE_INTERVAL_SEC = int(os.getenv("SAMPLE_INTERVAL_SEC", "120"))

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")


def resolve_model_and_credentials() -> tuple[str, str, str]:
    """Resolves whether to use Baseten or Meta API endpoint and model slug based on .env configuration."""
    models = [m.strip() for m in SPARK_MODEL.split("|")]
    
    # Try Baseten first if key is present
    if BASETEN_API_KEY:
        for m in models:
            if m.startswith("baseten:"):
                model_name = m.replace("baseten:", "")
                return BASETEN_API_URL, BASETEN_API_KEY, model_name
    
    # Fallback to Meta API if key is present
    if LLAMA_API_KEY:
        for m in models:
            clean_m = m.replace("baseten:", "")
            return LLAMA_API_URL, LLAMA_API_KEY, clean_m

    return "", "", ""


def encode_image_to_base64(image_path: str) -> str:
    """Reads a local image file and converts it to a base64 data URI."""
    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded_string}"


def analyze_fridge_image_with_muse(image_data_uri: str) -> List[Dict[str, Any]]:
    """Calls the Muse Spark VLM model to analyze fridge/freezer contents and detect items and quantities."""
    api_url, api_key, model_id = resolve_model_and_credentials()

    if MOCK_MODE or not api_key:
        logger.info("Running in MOCK_MODE or API keys missing. Generating mock vision analysis.")
        return [
            {"item_name": "milk", "kind": "fridge", "count": 1, "min_stock": 2, "expiry_risk": False},
            {"item_name": "frozen_pizza", "kind": "freezer", "count": 0, "min_stock": 1, "expiry_risk": False},
            {"item_name": "insulin_vial", "kind": "medicine", "count": 3, "min_stock": 5, "expiry_risk": True},
        ]

    prompt = (
        "You are an advanced inventory computer vision system inspecting the inside of a fridge, freezer, or storage cabinet.\n"
        "Analyze the provided image and extract a list of all visible items, estimated quantities, storage type, and condition.\n\n"
        "Return ONLY a JSON array of objects with the following schema:\n"
        "[\n"
        "  {\n"
        "    \"item_name\": \"string (e.g. milk, ice_cream, vaccine_vial)\",\n"
        "    \"kind\": \"fridge | freezer | medicine | general\",\n"
        "    \"count\": integer,\n"
        "    \"min_stock\": integer (suggested threshold),\n"
        "    \"expiry_risk\": boolean\n"
        "  }\n"
        "]\n"
        "Do not include markdown code block formatting or explanations."
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

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
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            res_json = response.json()
            
            raw_text = res_json["choices"][0]["message"]["content"].strip()
            # Clean markdown code blocks if the model included them
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            
            return json.loads(raw_text.strip())

    except Exception as e:
        logger.error(f"Error invoking Muse Spark API: {e}. Falling back to default baseline.")
        return []


def process_inventory_and_order(tenant_id: str, detected_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compares detected inventory counts against minimum thresholds and constructs mock restock orders."""
    order_items = []

    for item in detected_items:
        name = item.get("item_name", "unknown")
        count = item.get("count", 0)
        min_stock = item.get("min_stock", 1)

        # Sync inventory state with gateway
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    f"{GATEWAY_URL}/api/tenants/{tenant_id}/inventory",
                    json={
                        "name": name,
                        "kind": item.get("kind", "fridge"),
                        "min_stock": min_stock,
                        "count": count,
                        "camera_id": "fridge_cam_01"
                    }
                )
        except Exception as err:
            logger.warning(f"Could not update central inventory gateway: {err}")

        # Check if reorder is needed
        if count < min_stock:
            reorder_qty = (min_stock - count) + 2  # Order buffer
            order_items.append({
                "item_name": name,
                "current_count": count,
                "min_stock": min_stock,
                "order_quantity": reorder_qty,
                "unit": "units"
            })

    # Generate Mock Purchase Order payload if items need replenishment
    mock_order = None
    if order_items:
        mock_order = {
            "order_id": f"ORD-{tenant_id[:8].upper()}-{int(time.time())}",
            "tenant_id": tenant_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "MOCK_ORDER_CREATED",
            "supplier": "Drishti Auto-Replenishment Hub",
            "items": order_items,
            "total_items_ordered": sum(i["order_quantity"] for i in order_items)
        }
        logger.info(f"Generated Restock Order: {json.dumps(mock_order, indent=2)}")

    return {
        "detected_items": detected_items,
        "mock_order": mock_order
    }


def main():
    logger.info("Initializing Fridge Inventory Agent...")
    tenant_id = os.getenv("TENANT_ID", "home_1_bangalore")
    
    logger.info(f"Agent up and running for tenant '{tenant_id}'. Check interval: {SAMPLE_INTERVAL_SEC}s")

    while True:
        try:
            sample_image_path = "/tmp/fridge_latest.jpg"
            
            if os.path.exists(sample_image_path):
                data_uri = encode_image_to_base64(sample_image_path)
            else:
                # Placeholder image URI for demo/mock execution
                data_uri = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBD-placeholder"

            logger.info("Analyzing fridge visual frame...")
            detected = analyze_fridge_image_with_muse(data_uri)
            result = process_inventory_and_order(tenant_id, detected)

            if result["mock_order"]:
                logger.info(f"Triggered order for {len(result['mock_order']['items'])} low-stock item(s).")
            else:
                logger.info("All fridge inventory levels are within acceptable thresholds.")

        except Exception as e:
            logger.error(f"Error during execution loop: {e}")

        time.sleep(SAMPLE_INTERVAL_SEC)


if __name__ == "__main__":
    main()