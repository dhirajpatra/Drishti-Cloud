"""Show what freenewsapi.io REALLY answers, using the key and settings inside the news-context container.

    docker compose exec -T news-context python - < scripts/probe_news.py

Never prints the key (only its length and whether it has stray spaces/quotes).
"""
import os

import httpx

key = os.environ.get("NEWS_API_KEY", "")
url = os.environ.get("NEWS_API_URL", "").strip() or "https://api.freenewsapi.io/v1/news"
print(f"endpoint: {url}")
print(f"key: {len(key)} chars, stray whitespace/quotes: {key != key.strip() or any(c in key for c in chr(34) + chr(39) + ' ')}")
if not key:
    print("NEWS_API_KEY is EMPTY inside the container -> fix .env, then: docker compose up -d --force-recreate news-context")

tests = [
    ("no key (expect an auth message)", "language=en&country=IN", False),
    ("country only", "language=en&country=IN", True),
    ("country + in_title=Delhi", "language=en&country=IN&in_title=Delhi", True),
    ("country + q=Delhi", "language=en&country=IN&q=Delhi", True),
    ("no country, in_title=Delhi", "language=en&in_title=Delhi", True),
    ("language only", "language=en", True),
]
for label, query, with_key in tests:
    try:
        r = httpx.get(f"{url}?{query}", headers={"x-api-key": key} if with_key else {}, timeout=20, follow_redirects=False)
        body = r.text.replace("\n", " ")[:220]
        print(f"\n[{label}]  GET ?{query}\n  HTTP {r.status_code}  {body}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[{label}]  FAILED: {type(exc).__name__}: {exc}")
