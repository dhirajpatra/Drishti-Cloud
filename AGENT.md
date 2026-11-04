# AGENT.md - how Drishti's agents work (read this before adding or changing one)

Drishti is a set of small, stateless agents that pass a frame down a pipeline of Redis streams. Each agent does one job, can be scaled with
`docker compose up -d --scale <name>=3`, and degrades to deterministic rules when a model is unavailable.

## The pipeline

```
camera ──> feed-ingestor ─┐                         edge camera-agent (remote site, pushes over HTTPS) ─┐
                          └─> gateway POST /ingest/{tenant}  (idle gate, quality checks, storyboard) <──┘
raw_frames ─> sam3-segmenter ─> segmented ─> ocr-reader ─> perceived ─> spark-multimodal ─> assessed
assessed ─> wonder-reasoner ─> decided ─> image-alert ─> proofed ─> action-agent ─> Postgres / Neo4j / speaker / WhatsApp / dashboard bell
```

| Agent | Consumes -> publishes | Job | Models |
|---|---|---|---|
| gateway | HTTP -> `raw_frames` | auth, rate limit, idle gate (unchanged scenes never reach a model), quality, API for the dashboard | none |
| feed-ingestor | cameras -> gateway | samples RTSP / snapshot / MP4 per camera, motion hook, staged connection test, back-off on dead links | none |
| edge camera-agent | camera -> gateway | same, but runs **at the site** and pushes outbound (see docs/REMOTE_CAMERAS.md) | none |
| sam3-segmenter | `raw_frames` -> `segmented` | labelled objects + boxes (also the fridge staples) | SAM 3.1, falls back to Muse Spark vision |
| ocr-reader | `segmented` -> `perceived` | reads signs, monitors, labels (zoomed crop) | Muse Spark |
| spark-multimodal | `perceived` -> `assessed` | rule engine first, then visual judgement vs. the vertical's SOP | Muse Spark |
| wonder-reasoner | `assessed` -> `decided` | habits, learned feedback, local news; may lower an ALERT or raise it to CRITICAL | Muse Wonder |
| image-alert | `decided` -> `proofed` | draws the proof picture (Pillow, never generated) | none |
| action-agent | `proofed` -> world | alert rows, cooldown, storm breaker, speaker / OLED / WhatsApp / webhook, dashboard bell, camera watchdog | none |
| fridge-inventory-agent | scan requests -> stock | counts staples, "Need to buy" | Muse Spark |
| news-context | timer -> graph | top national + local headlines per tenant | news API |
| grocery-price-agent, whatsapp-connector | optional, profile `extras` | rough shelf prices; WhatsApp credential test sender | Muse Spark / none |

## Rules every agent follows

1. **Rules before models.** Anything computable from sensors, labels and OCR lives in `common/drishti/rules.py` and works with no API key.
2. **Never a generated picture as evidence.** Proofs are drawn from what the camera saw. Muse Image is not used on evidence.
3. **No face recognition, no identity claims.** Drishti describes events, it does not name or judge people.
4. **CRITICAL is never suppressed** by habits, learning or the model. Learning and habits can only turn an ALERT down.
5. **Degrade, don't drop.** A model failure falls back to the rule engine; a database hiccup must not block an alert; the graph is memory, not the alert path.
6. **Every model call goes through `drishti.llm.chat_json`** (daily caps, provider chain, retries, reasoning budget). No agent calls an API directly.
7. **Tenant isolation:** Postgres row-level security through `db._conn(tenant)`, `tenant_id` on every graph node, one Redis stream per tenant per stage.

## Guardrails (`common/drishti/guardrails.py`)

| | Where | What |
|---|---|---|
| G1 | spark | `validate_llm`: level allow-list, rule ids, list/text limits; a bad reply becomes NORMAL, not a crash |
| G2 | spark, wonder | `clean_speech`: nothing spoken or messaged may accuse a person, give a medical order or name someone from their looks |
| G3 | spark | `evidence_gate`: a model-only CRITICAL on a blurry / dim / blocked picture is held at ALERT, with the reason shown |
| G4 | wonder | learned habits need evidence (see below); protected rules are never muted |
| G5 | action | `StormBreaker`: more than `STORM_LIMIT` ALERTs in `STORM_WINDOW_SEC` -> one digest notice; CRITICAL always passes |
| G6 | spark, wonder | `scrub_untrusted`: OCR text, speech transcripts, owner notes and news headlines are data, never instructions to the model |

## Learning from the operator (`common/drishti/learning.py`)

Alert cards have **ACKNOWLEDGE / CONFIRMED / FALSE ALARM**. Each verdict is stored in the `feedback` table. For each (tenant, rule, hour +-1) Drishti keeps
decayed counts (half-life 14 days) and the posterior false-alarm probability `(1+F)/(2+F+C)`. An ALERT is quieted only when **F >= 2** and **P >= 0.75**,
never for protected rules (abuse, fire, weapons, falls, help calls, monitor alarms...), and `GET /tenants/{id}/learning` plus the dashboard panel show the numbers.
It is a contextual bandit with a hard safety floor, not an opaque RL loop: a single mis-click changes nothing, old feedback fades, and every decision is explainable.

## Adding an agent

1. `services/<name>/main.py` + `requirements.txt` (runtime deps come from `services/common`). Consume with `bus.consume(stage, group, handler)` and
   `bus.publish(next_stage, tenant, payload)`; the handler must be idempotent (a message is acked only after it returns; it is retried, then dead-lettered).
2. A compose service with the `*agent` anchor and a healthcheck (`python -m drishti.healthcheck <name>`); loops without `bus.consume` call `bus.heartbeat`.
3. Use a **unique consumer group name**. Two services reading the same stage with the same group split the frames between them (this happened once with a stale copy).
4. Tests in `tests/` (real Redis + Postgres, in-memory graph, `MOCK_MODE`): `python -m pytest -q`.
5. Update README.md, this file and SKILL.md.

## Connecting cameras

`docs/REMOTE_CAMERAS.md`: edge agent (recommended), VPN, tunnel, and what the staged TEST CONNECTION result means.
