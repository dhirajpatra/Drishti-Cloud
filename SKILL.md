# Drishti Cloud - Multi-Agent Multi-Tenant Multi-Modal Skill

> For Muse Code: Use this skill to build, run, and extend Drishti Cloud Multi-Agent Security Platform using Meta Model APIs.

This skill defines how to work with the Docker Compose based multi-agentic application that powers Drishti - Universal Security for Mall, Home Seniors, School Children, ICU/Hospital.

## 1. Overview

Drishti Cloud is Multi-Agent, Multi-Tenant, Multi-Modal:

- Multi-Modal Input: IMAGES + VIDEO + TEXT + DOCS -> Muse Spark (as per Meta's Multimodality for developers image you shared)
- Multi-Agent: 7 specialized agents via Redis queues
- Multi-Tenant: Simultaneous processing of 100+ feeds (mall, home, school, ICU) with isolated Neo4j subgraphs
- Multi-Vertical: mall_security, home_seniors, school_children, icu_hospital

All services run via `docker compose up` with Neo4j graph database.

## 2. Core Philosophy

### Multimodality for Developers (Your Image)
```
IMAGES ──┐
VIDEO  ──┼──> Muse Spark (api.meta.ai/v1/responses) -> Decision JSON
TEXT   ──┤
DOCS   ──┘
       +
Muse Wonder (long reasoning + Neo4j memory + News)
Muse Image (visual alert)
SAM 3.1 (segmentation)
```

- IMAGES: Frame + SAM 3.1 masks
- VIDEO: 10 sec clip + PIR HC-SR501 + 2x HC-SR04 ultrasonic + IR Obstacle + APDS-9960 gesture + DHT11
- TEXT: OCR (ICU vitals), Whisper STT from USB Mini Mic, WhatsApp query
- DOCS: Safety SOP docs per vertical loaded into Spark context

## 3. Docker Compose Services (12 Services)

### File: docker-compose.yml

```yaml
version: '3.9'
services:
  neo4j:
    image: neo4j:5.21
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/drishti123
      NEO4J_PLUGINS: '["apoc"]'
    volumes: ["neo4j_data:/data"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: drishti
      POSTGRES_USER: drishti
      POSTGRES_PASSWORD: drishti123
    ports: ["5432:5432"]
    volumes: ["pg_data:/var/lib/postgresql/data"]

  gateway:
    build: ./services/gateway
    ports: ["8000:8000"]
    environment:
      - LLAMA_API_KEY=${LLAMA_API_KEY}
      - NEO4J_URI=bolt://neo4j:7687
      - REDIS_URI=redis://redis:6379
      - DATABASE_URL=postgresql://drishti:drishti123@postgres:5432/drishti
    depends_on: [neo4j, redis, postgres]

  feed-ingestor:
    build: ./services/feed-ingestor
    environment:
      - REDIS_URI=redis://redis:6379
      - RTSP_FEEDS=${RTSP_FEEDS}
    volumes: ["./feeds:/app/feeds"]
    # Handles multiple RTSP: mall_1_cam_1, senior_home_kitchen, school_gate, icu_bed_5
    # Samples every 30s (LOW_COST_MODE)

  sam3-segmenter:
    build: ./services/sam3-segmenter
    environment:
      - LLAMA_API_KEY=${LLAMA_API_KEY}
      - LLAMA_API_URL=https://api.meta.ai/v1/responses
    # Calls SAM 3.1 via Meta Model API

  ocr-reader:
    build: ./services/ocr-reader
    # For ICU vitals board, mall sign, school notice
    # Uses PaddleOCR + Llama 3.2 Vision

  spark-multimodal:
    build: ./services/spark-multimodal
    environment:
      - LLAMA_API_KEY=${LLAMA_API_KEY}
      - SPARK_MODEL=muse-spark-1.3
    # IMAGES+VIDEO+TEXT+DOCS -> Spark

  wonder-vertical-reasoner:
    build: ./services/wonder-reasoner
    environment:
      - LLAMA_API_KEY=${LLAMA_API_KEY}
      - WONDER_MODEL=muse-wonder-1.0
      - NEO4J_URI=bolt://neo4j:7687

  image-alert:
    build: ./services/image-alert
    environment:
      - LLAMA_API_KEY=${LLAMA_API_KEY}
      - LLAMA_MODEL=Meta-Llama-Image

  news-context-agent:
    build: ./services/news-context
    environment:
      - LLAMA_API_KEY=${LLAMA_API_KEY}
      - NEWS_API_KEY=${NEWS_API_KEY}

  pi-edge-simulator:
    build: ./services/pi-edge-simulator
    # Simulates Pi 5 edge: USB Mini Mic, PAM8403 + 2 inch 5W woofer, ISD1820 Voice Module, OLED SSD1306 0.96, HC-SR04 x2 with 1k/2k divider, PIR HC-SR501, IR Obstacle, DHT11, APDS-9960

  frontend:
    build: ./services/frontend
    ports: ["3000:3000"]
    environment:
      - REACT_APP_API_URL=http://localhost:8000

volumes:
  neo4j_data:
  pg_data:
```

## 4. Service Details

### 4.1 feed-ingestor
- Ingest multiple RTSP/MP4/phone camera feeds simultaneously
- Multi-tenancy: Each feed tagged tenant_id:vertical:camera_id e.g., mall_1:mall_security:cam_1
- Sampling: 30 sec per user low-cost request
- Scene hash: Avoid duplicate API calls if frame unchanged
- Output -> Redis: queue:raw_frames:{tenant_id}

### 4.2 sam3-segmenter (SAM 3.1 via Meta Responses API)
```python
import os
import requests

response = requests.post(
  os.getenv("LLAMA_API_URL", "https://api.meta.ai/v1/responses"),
  headers={"Authorization": f"Bearer {os.environ['LLAMA_API_KEY']}"},
  json={"model": "sam-3.1", "input": [{"role": "user", "content": [
    {"type": "input_text", "text": "Segment visible stove, keys, person, bag, bed, door, child, patient"},
    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
  ]}], "max_output_tokens": 600},
  timeout=90,
)
response.raise_for_status()
```

### 4.3 spark-multimodal (Muse Spark)
Core multimodal prompt:
```
You are Drishti Guard for vertical {vertical} for tenant {tenant_id}.
IMAGES: {sam3_masks}
VIDEO: motion={pir}, distance_front={us_front}cm, gesture={gesture}
TEXT: OCR={ocr_texts}, Audio Transcript={whisper_text}
DOCS: Safety SOP: {sop_doc_for_vertical}
Decide: NORMAL, ALERT, CRITICAL. Explain reason. List objects. Return JSON only.
```

### 4.4 wonder-vertical-reasoner (Muse Wonder + Neo4j)
Queries Neo4j habit graph:
```
MATCH (t:Tenant {id:$tid})-[:HAS_PERSON]->(p:Person)-[:HAS_HABIT]->(h:Habit)
MATCH (t)-[:HAS_EVENT]->(e:Event) WHERE e.timestamp > now() - 24h
MATCH (t)-[:IN_CONTEXT]->(n:NewsContext)
RETURN p, h, e, n
```

### 4.5 Neo4j Graph Schema
Nodes:
- Tenant {id, type: mall|home_senior|school|icu_hospital, location}
- Camera {id, tenant_id, rtsp_url}
- Person {id, tenant_id, name, role}
- Object {label: stove|keys|bag|bed, last_seen}
- Event {id, timestamp, type, camera_id}
- Alert {id, level, reason, image_url, speak_text}
- Habit {desc: Dad forgets keys 7:30am}
- SOPDoc {vertical, text}
- NewsContext {headline, timestamp}

Relationships:
- Tenant -HAS_CAMERA-> Camera
- Tenant -HAS_PERSON-> Person
- Tenant -HAS_SOP-> SOPDoc
- Tenant -IN_CONTEXT-> NewsContext
- Person -HAS_HABIT-> Habit
- Event -DETECTED_OBJECT-> Object
- Event -TRIGGERED-> Alert
- Event -INVOLVES-> Person

## 5. Multi-Tenancy & Simultaneous Processing

- Tenant isolation: Postgres RLS + Neo4j tenant_id filtering + Redis queue per tenant
- Scaling: Each agent stateless, scales via docker compose up --scale spark-multimodal=3
- Cost: LOW_COST_MODE - only call Meta API if scene_hash changes + PIR triggered + 30 sec interval
- Target: Rs 60/month API + Rs 80 electricity = Rs 140/month per home

## 6. Vertical-Specific Logic

| Vertical | Key Detections | SOP Example | Alert |
|----------|----------------|-------------|-------|
| mall_security | Crowd >50, unattended bag >2min, restricted area | Unattended bag SOP | PAM8403 speaker + security WhatsApp |
| home_seniors | Fall, no motion 2h, door open night, stove ON | Senior fall SOP | ISD1820 loud + OLED FALL + call |
| school_children | Child alone corridor, stranger no badge, gate open | Child safety policy | School admin alert |
| icu_hospital | Patient out of bed, floor, monitor ALARM, staff absent | ICU protocol | Nurse station + Image proof |

## 7. Edge Hardware Mapping (Pi 5 16GB)

Each tenant Pi:
- Input: USB 2.0 Mini Mic (main mic), 2x HC-SR04 with 1k/2k divider (Echo 5V->3.3V), PIR HC-SR501, IR Obstacle, DHT11, APDS-9960 gesture, ISD1820 Voice Module
- Output: PAM8403 amp (VCC Pin2 5V, GND Pin6, audio from Pi 3.5mm jack to L/R/GND), 2 inch 4Ohm 5W woofer speaker (L+/L-), SSD1306 OLED 0.96 I2C

## 7b. Remote cameras, guardrails and learning (added during the hackathon)

- **Private RTSP from anywhere**: `edge/camera-agent` runs at the site and pushes frames to `POST /ingest/{tenant}` with a per-site token (`X-Edge-Token`, created with
  `POST /tenants/{id}/edge-tokens`, hashed, one tenant, revocable). VPN (Tailscale / WireGuard) and ngrok TCP templates are in `deploy/remote/`. Guide: `docs/REMOTE_CAMERAS.md`.
- `common/drishti/camurl.py`: `tcp://` -> `rtsp://`, credential redaction, forbidden camera hosts (loopback, metadata, Drishti's own services) checked at save time and after DNS,
  staged `dns -> tcp -> rtsp -> frame` diagnosis returned by the Admin TEST CONNECTION. `ALLOW_PRIVATE_CAMERA_HOSTS=false` on a shared cloud server.
- Camera-only sites: a changing picture counts as motion, so `no_motion_2h` does not fire without a PIR sensor.
- `common/drishti/guardrails.py` (G1-G6) and `learning.py` (operator feedback with a safety floor): see AGENT.md.
- Agent contributor guide: **AGENT.md**.

## 8. Environment Variables

```
LLAMA_API_KEY=xxx # Meta Model API key
LLAMA_API_URL=https://api.meta.ai/v1/responses
NEO4J_URI=bolt://neo4j:7687
REDIS_URI=redis://redis:6379
LOW_COST_MODE=true # 30 sec sampling
RTSP_FEEDS=rtsp://mall_cam_1,rtsp://senior_home_kitchen
NEWS_API_KEY=xxx
ALLOW_PRIVATE_CAMERA_HOSTS=true   # false on a shared cloud server
STORM_LIMIT=12 STORM_WINDOW_SEC=300   # alert-storm breaker (CRITICAL always passes)
```

## 9. Commands for Muse Code

```bash
docker compose up -d
docker compose logs -f spark-multimodal
curl -X POST http://localhost:8000/ingest/mall_1 -F "file=@test_frames/mall_bag.jpg" -F "sensors={\"pir\":true,\"us_front\":75}"
# Query Neo4j: MATCH (t:Tenant {id:"icu_ward_1"})-[:HAS_EVENT]->(e:Event) RETURN e LIMIT 10
```

## 10. Hackathon Checklist

- [x] Muse Spark via Meta Model API
- [x] Muse Wonder via Meta Model API
- [x] Muse Image via Meta Model API
- [x] SAM 3.1 via Meta Model API
- [x] Open-weights via Baseten/Modal
- [x] Muse Code (this SKILL.md)
- [x] Docker Compose multi-agent
- [x] Neo4j graph DB
- [x] Multi-tenant, multi-modal (IMAGES, VIDEO, TEXT, DOCS)
- [x] Multi-vertical: mall, home seniors, school children, ICU hospital
- [x] Edge: Pi 5 with USB Mic + PAM8403 + 5W woofer + ISD1820 + OLED

## 11. Muse Code Instructions

When user says "Add school child gate detection":
1. Read services/spark-multimodal/prompts/school_children.txt
2. Update SAM prompt to include gate, fence, badge
3. Add Cypher habit for gate in Neo4j
4. Test with docker compose restart spark-multimodal

Always use LOW_COST_MODE flag to keep API cost Rs 60/month.

Built for Meta Global AI Hackathon Oct 26 - Nov 4, Indie Track.
