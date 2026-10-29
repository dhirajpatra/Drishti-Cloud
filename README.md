# Drishti Cloud - Multi-Agent Multi-Tenant Multi-Modal Security Platform

> **Meta Global AI Developer Hackathon 2026** | Indie Track | $1M Prize Pool
> **Theme:** Build for Your World + Push the Frontier

> One brain that watches everything: **IMAGES + VIDEO + TEXT + DOCS → Muse Spark**

[Meta](https://img.shields.io/badge/Meta-Model%20API-blue) [Muse Spark](https://img.shields.io/badge/Muse%20Spark-Multimodal-blue) [Muse Wonder](https://img.shields.io/badge/Muse%20Wonder-Reasoning-purple) [SAM 3.1](https://img.shields.io/badge/SAM%203.1-Segmentation-green) [Neo4j](https://img.shields.io/badge/Neo4j-Graph-black)

Drishti is a **cloud-native, multi-agent, multi-tenant security platform** that automatically processes multiple video feeds to secure **Malls, Home Seniors, School Children, and ICU/Hospital Patient Rooms** - finding everything from image segmentation to text on monitors and informing instantly.

Built entirely with **Muse Spark, Wonder, Image, SAM 3.1 via Meta Model API**, open-weights models, and Muse Code.

### Live Demos
- [Multi-Feed Multi-Vertical Architecture](./artifacts/drishti_multi_feed_multi)
- [Multi-Agent Cloud Architecture](./artifacts/drishti_cloud_multi_agent)
- [Pi Home Sentinel](./artifacts/drishti_pi_home_sentinel)
- [Drishti Cloud dashboard](./docs/images/drishti-dashboard1.png)

---

## 🌟 Why Drishti?

**India Context:** 1 guard watches 20 CCTV screens - misses 95% events. Senior at home alone, child at school gate, patient in ICU - no one watching continuously.

**Solution:** One Pi 5 16GB per location + Cloud brain that understands:
- **Mall:** Unattended bag >2 min, crowd >50, fight, restricted entry
- **Home Seniors:** Fall detected, no movement 2 hours, stove ON, door open night
- **School:** Child alone corridor, stranger without badge, gate open during class
- **ICU/Hospital:** Patient out of bed, patient on floor, monitor says "ALARM", nurse not visited 1 hour

All with **voice-first** alerts via 2" 5W woofer + PAM8403 + ISD1820 loud module + 0.96" OLED eyes.

---

## 🧠 Core Innovation: Multimodality for Developers

As per Meta's vision:

```
IMAGES ──┐
VIDEO  ──┼──> Muse Spark (api.meta.ai/v1/responses) -> Alert JSON
TEXT   ──┤
DOCS   ──┘
       + Muse Wonder (habits + Neo4j + News)
       + Muse Image (visual proof)
       + SAM 3.1 (segmentation)
```

- **IMAGES:** Camera frame + SAM 3.1 masks (stove, keys, person, bag, bed, child)
- **VIDEO:** 10 sec clip + PIR HC-SR501 motion + 2x HC-SR04 ultrasonic distance + IR Obstacle + APDS-9960 gesture
- **TEXT:** OCR from ICU vitals board "HR 120 ALARM", Whisper STT from USB Mini Mic "Help me", WhatsApp query
- **DOCS:** Safety SOP per vertical (Mall SOP, Hospital Protocol, School Policy) - Spark checks violation against docs

---

## 🏗️ Architecture: 12 Services in Docker Compose

### Quick View
```
Edge (Multiple Tenants)          Cloud (Docker Compose)
------------------------         -----------------------------
Pi 5 16GB x N                    gateway (FastAPI :8000)
- Camera Module 3 x2             feed-ingestor (multi-RTSP)
- USB Mini Mic                   sam3-segmenter (SAM 3.1)
- PAM8403 + 2" 5W Woofer         ocr-reader (ICU boards)
- ISD1820 Voice Module           spark-multimodal (Muse Spark)
- OLED SSD1306 0.96              wonder-reasoner (Muse Wonder + Neo4j)
- HC-SR04 x2 + 1k/2k divider     image-alert (Muse Image)
- PIR, IR, DHT11, APDS-9960      news-context (latest news)
                                 neo4j, redis, postgres, frontend
```

### Services

| Service | Tech | Input | Output |
|---------|------|-------|--------|
| **gateway** | FastAPI | HTTP ingest from Pi | Validates tenant_id, pushes to Redis |
| **feed-ingestor** | Python + OpenCV | RTSP feeds: mall_1_cam_1, senior_home_kitchen, school_gate, icu_bed_5 | Samples every 30 sec LOW_COST_MODE, scene hash dedup |
| **sam3-segmenter** | Meta API SAM 3.1 | frame.jpg | {masks: [{label: "stove", bbox}, {label:"person"}]} |
| **ocr-reader** | PaddleOCR + Llama 3.2 Vision | Frame crop | {texts: [{text:"HR 120 ALARM"}]} |
| **spark-multimodal** | Muse Spark via Meta Responses API | IMAGES+VIDEO+TEXT+DOCS | {alert_level: NORMAL/ALERT/CRITICAL, reason} |
| **wonder-vertical-reasoner** | Muse Wonder + Neo4j | Spark output + habit graph + news | {final_decision, habit_match, speak_text} |
| **image-alert** | Muse Image | Frame + masks | Visual proof jpg with red circles |
| **news-context-agent** | Wonder + News API | Location | {headline:"Bangalore power cut", context} |
| **neo4j** | Neo4j 5.21 | Graph writes | Habit, Event, Person graph |
| **redis** | Redis 7 | Queue | queue:raw_frames:{tenant_id} |
| **postgres** | Postgres 15 | Tenant, SOP docs | RLS multi-tenant |
| **pi-edge-simulator** | Python | Simulates Pi sensors | For testing without hardware |

---

## 🗄️ Neo4j Knowledge Graph

```cypher
// Nodes
(:Tenant {id, type: mall|home_senior|school|icu_hospital, location})
(:Camera {id, tenant_id, rtsp_url})
(:Person {id, tenant_id, name, role: senior|child|patient|staff})
(:Object {label: stove|keys|bag|bed, last_seen})
(:Event {id, timestamp, type, camera_id, sam_masks, ocr_texts})
(:Alert {id, level, reason, image_url, speak_text})
(:Habit {desc: "Dad forgets keys 7:30am", frequency})
(:SOPDoc {vertical, text})
(:NewsContext {headline, timestamp, source})

// Relationships
(Tenant)-[:HAS_CAMERA]->(Camera)
(Tenant)-[:HAS_PERSON]->(Person)
(Tenant)-[:HAS_SOP]->(SOPDoc)
(Tenant)-[:IN_CONTEXT]->(NewsContext)
(Person)-[:HAS_HABIT]->(Habit)
(Event)-[:DETECTED_OBJECT]->(Object)
(Event)-[:TRIGGERED]->(Alert)
(Event)-[:INVOLVES]->(Person)
```

**Example Query - ICU:**
```cypher
MATCH (t:Tenant {id:"icu_ward_1"})-[:HAS_EVENT]->(e:Event)
WHERE e.timestamp > datetime() - duration({hours:1})
MATCH (e)-[:DETECTED_OBJECT]->(o:Object {label:"patient_on_floor"})
RETURN e, o
```

---

## 🏫 Multi-Vertical Detection Matrix

| Vertical | SAM 3.1 + Sensors | TEXT | DOCS SOP | Alert Action |
|----------|-------------------|------|----------|--------------|
| **mall_security** | Crowd count >50, bag unattended >2min, person restricted | OCR gate sign | "Unattended bag SOP" | PAM8403 speaker + WhatsApp security |
| **home_seniors** | Fall pose, no PIR 2h, door HC-SR04 120cm night, stove ON | Whisper "Help" | "Senior fall protocol" | ISD1820 loud + OLED "FALL!" + call family |
| **school_children** | Child alone corridor, stranger no badge, gate open class time, bullying cluster | OCR school notice | "Child safety policy" | School admin + speaker |
| **icu_hospital** | Patient out of bed, on floor, staff absent 1h | OCR monitor "ALARM" HR 120 | "ICU monitoring protocol" | Nurse station + Image proof |

---

## 🔧 Hardware Mapping (Per Tenant Pi 5 16GB)

**You Have:**
- Pi 5 16GB + fan + 32GB SD + power cable + jumper cables
- HC-SR501 PIR, HC-SR04, DHT11, IR Obstacle, IR Receiver, cartoon brackets

**Ordered/To Order:**
- 0.96" OLED I2C SSD1306 (eyes)
- HC-SR04 x2 extra, IR Obstacle
- APDS-9960 gesture sensor
- ISD1820 Voice Module with mic + 0.5W speaker (loud alert)
- USB 2.0 Mini Mic (main mic - chosen over ReSpeaker HAT)
- 2 Inch 4Ω 5W Metal Woofer speaker
- PAM8403 amplifier with knob (solder: speaker to L+/L-, VCC to Pi 5V Pin2, GND to Pin6, audio from Pi 3.5mm jack to PAM8403 L/R/GND)
- Resistors 1kΩ + 2kΩ mandatory for HC-SR04 Echo 5V->3.3V divider
- PCA9685 16-ch servo HAT (future moving robot)
- Reserve GPIO 12,13 pan-tilt SG90, 18,19 wheels

**Wiring Note:** HC-SR04 Echo through 1k+2k divider to Pi GPIO - direct 5V will fry Pi!

---

## 💰 Cost Optimization (User Requirement: ₹140/month)

- **LOW_COST_MODE=true:** Sample frames, reuse analysis for unchanged scenes for up to 15 minutes, wake on scene changes/PIR, and avoid Wonder calls for CRITICAL alerts
- **API call cap:** 1,000 Meta requests/day across all agents, including SAM and OCR; after the cap, cached perception and deterministic rules continue
- **Target:** ₹60/month Meta API (using $150 free credits + $225 partner credits first) + ₹80 electricity = ₹140/month
- **Billing:** Set an API spend limit in your Meta Model API account dashboard

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- LLAMA_API_KEY from the Meta Model API (Muse Spark, Wonder, Image, SAM 3.1)
- NEWS_API_KEY (optional)

### 1. Clone & Env
```bash
git clone https://github.com/dhirajpatra/drishti-cloud.git
cd drishti-cloud
cp .env.example .env
# Edit .env (everything is optional: with no LLAMA_API_KEY the stack runs offline in MOCK_MODE):
# LLAMA_API_KEY=your_key
# LLAMA_API_URL=https://api.meta.ai/v1/responses
# MOCK_MODE=false
# LOW_COST_MODE=true
# SAMPLE_INTERVAL_SEC=300                  # one frame per camera every 5 min (each frame = a few paid model calls)
# MAX_LLM_CALLS_PER_DAY=1000               # hard daily cap; the rule engine keeps working after it
# COMPOSE_PROFILES=sim                     # demo simulator - only with MOCK_MODE=true (it sends fake frames)
# RTSP_FEEDS=home_1_bangalore:home_seniors:kitchen=http://192.168.1.4:8081/   # tenant:vertical:camera=url
```

### 2. Run All Services
```bash
docker compose up -d
docker compose logs -f spark-multimodal
```

Services:
- Gateway: http://localhost:8000
- Frontend: http://localhost:3000
- Neo4j Browser: http://localhost:7474 (neo4j/drishti123)
- Redis: localhost:6379

### 3. Test Multi-Feed Ingest
Use any JPEG (a phone photo is fine). With a real API key this costs a few model calls per frame.
```bash
curl -X POST http://localhost:8000/ingest/mall_1 \
  -F "file=@my_photo.jpg" -F "camera_id=door_cam" -F "force=true" \
  -F 'sensors={"pir":true,"us_front":75,"us_door":120,"temp":32}'
# -> {"status":"queued","frame_id":"...","static":false}   (static=true means the scene was unchanged and skipped)
```
Real cameras: put them in `RTSP_FEEDS` (`tenant:vertical:camera=url`; motionEye's *Streaming URL* on port 8081, RTSP
streams and MP4 files under `./feeds` all work), then `docker compose up -d feed-ingestor`. The newest frame of every
camera shows up in the dashboard's **Live cameras** panel and each analysed frame in the **Event timeline**.

### 4. Explore the Knowledge Graph in the Neo4j Browser

**Open it:** http://localhost:7474 -> *Connect URL* `neo4j://localhost:7687` (or `bolt://localhost:7687`), user `neo4j`,
password = `NEO4J_PASSWORD` from `.env` (default `drishti123`). The ports are bound to `127.0.0.1`; on a cloud VM use an SSH
tunnel (`ssh -L 7474:localhost:7474 -L 7687:localhost:7687 user@vm`) rather than opening them.
> The password is only applied the first time the `neo4j_data` volume is created. If you change it later, either run
> `docker compose down -v` (deletes ALL data) or `ALTER CURRENT USER SET PASSWORD FROM 'old' TO 'new'`.

**Run a query:** paste it into the top bar and press **Ctrl+Enter**. Pick a tenant once, then reuse it as `$tid`:
```cypher
:param tid => 'home_1_bangalore'
```
Demo tenants: `mall_1`, `home_1_bangalore`, `school_1`, `icu_ward_1`. In the graph view click a node type at the bottom
right and choose caption `name` to label nodes; double-click a node to expand its neighbours.

**Schema as built** (every node carries `tenant_id`, so always filter by tenant):

| Node | Useful properties |
|------|-------------------|
| `Tenant` | `id`, `name`, `type`, `vertical`, `location` |
| `Camera` | `id`, `rtsp_url` |
| `Person` | `name`, `role` |
| `Habit` | `desc`, `rule`, `start_hour`, `end_hour`, `frequency`, `learned` |
| `Event` | `id` (= frame id), `timestamp`, `level`, `type`, `camera_id`, `ocr_texts`, `mask_count` |
| `Object` | `label`, `last_seen` |
| `Alert` | `level`, `rule`, `reason`, `speak_text`, `image_url` |
| `SOPDoc`, `NewsContext` | `text` / `headline`, `source`, `timestamp` |

`(Tenant)-[:HAS_CAMERA|HAS_PERSON|HAS_SOP|HAS_EVENT|IN_CONTEXT|HAS_HABIT]->(...)`, `(Person)-[:HAS_HABIT]->(Habit)`,
`(Event)-[:DETECTED_OBJECT]->(Object)`, `(Event)-[:TRIGGERED]->(Alert)`.
Only *non-trivial* frames become `Event` nodes (an unchanged NORMAL scene is skipped), so Neo4j has fewer events than the
dashboard's timeline, which is read from Postgres.

**Starter queries**
```cypher
// 1. See the whole picture for one tenant (graph view)
MATCH (t:Tenant {id:$tid})-[r]->(n) RETURN t, r, n LIMIT 100

// 2. What is in the database?
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC

// 3. Latest events (table view)
MATCH (:Tenant {id:$tid})-[:HAS_EVENT]->(e:Event)
RETURN e.timestamp AS time, e.camera_id AS camera, e.level AS level, e.type AS rule, e.ocr_texts AS text
ORDER BY e.timestamp DESC LIMIT 20

// 4. Last hour as a graph: events with the objects they saw and the alerts they raised
MATCH (:Tenant {id:$tid})-[:HAS_EVENT]->(e:Event)
WHERE e.timestamp > datetime() - duration({hours: 1})
OPTIONAL MATCH (e)-[:DETECTED_OBJECT]->(o:Object)
OPTIONAL MATCH (e)-[:TRIGGERED]->(a:Alert)
RETURN e, o, a

// 5. Every CRITICAL alert, all tenants
MATCH (t:Tenant)-[:HAS_EVENT]->(e:Event)-[:TRIGGERED]->(a:Alert)
WHERE a.level = 'CRITICAL'
RETURN t.id AS tenant, e.timestamp AS time, a.rule AS rule, a.reason AS reason ORDER BY time DESC

// 6. Which habits does the system know (these suppress false alarms)?
MATCH (t:Tenant {id:$tid})-[:HAS_PERSON|HAS_HABIT*1..2]->(h:Habit)
RETURN DISTINCT h.desc AS habit, h.rule AS explains_rule, h.start_hour AS from_hour, h.end_hour AS to_hour,
       h.frequency AS seen, h.learned AS learned_from_feedback

// 7. What does this camera see most often?
MATCH (:Tenant {id:$tid})-[:HAS_EVENT]->(:Event)-[:DETECTED_OBJECT]->(o:Object)
RETURN o.label AS object, count(*) AS times_seen ORDER BY times_seen DESC LIMIT 15

// 8. Activity by hour of day (India time)
MATCH (:Tenant {id:$tid})-[:HAS_EVENT]->(e:Event)
RETURN datetime({datetime: e.timestamp, timezone: 'Asia/Kolkata'}).hour AS hour, count(*) AS events ORDER BY hour

// 9. Local news the reasoner may use to escalate an alert
MATCH (:Tenant {id:$tid})-[:IN_CONTEXT]->(n:NewsContext)
RETURN n.headline, n.source, n.timestamp ORDER BY n.timestamp DESC LIMIT 10
```
Teach a habit by hand (same shape the agents write; the Wonder agent then marks that rule NORMAL inside the window):
```cypher
MATCH (t:Tenant {id:$tid})
MERGE (h:Habit {uid: $tid + ':habit:evening-tea'})
SET h.tenant_id=$tid, h.desc='Evening tea in the kitchen 17:00-18:00', h.name='no_motion_2h', h.rule='no_motion_2h',
    h.start_hour=17, h.end_hour=18, h.learned=false, h.frequency=1
MERGE (t)-[:HAS_HABIT]->(h)
```
Clean up one tenant's events (keeps habits, cameras and people): `MATCH (e:Event {tenant_id:$tid}) DETACH DELETE e`

Same queries without a browser: `docker compose exec neo4j cypher-shell -u neo4j -p drishti123 "MATCH (n) RETURN labels(n)[0], count(*)"`.
A harmless `IN_CONTEXT ... not available` warning in the gateway log just means no news items have been stored yet.

**Local news:** `news-context` reads each tenant's `location` (where its cameras are), fetches the newest headlines that
mention that city (freenewsapi.io; "Bengaluru" also searches "Bangalore"), keeps the top 5 (`NEWS_MAX_ITEMS`) from the last
48 h, and fills gaps with the country's top headlines marked `(national)`. Check it with query 9 above, or
`docker compose logs news-context --tail=20` (look for `news for home_1_bangalore (Bengaluru): 5 item(s)`). The Wonder
agent escalates an ALERT to CRITICAL when a headline matches that rule's hazard words (e.g. bomb, riot, flood).

### 5. Edge Pi Setup (Optional)
```bash
# On Pi 5
cd edge/
pip install -r requirements.txt
python pi_edge.py --tenant_id home_1_bangalore --vertical home_seniors --gateway http://YOUR_CLOUD_IP:8000
```

### 6. Tests (no Docker, no API key, no Neo4j needed)
```bash
pip install ./services/common fastapi "uvicorn[standard]" python-multipart pytest
make test        # starts a throw-away redis-server + postgres, runs the whole 6-agent pipeline in MOCK_MODE
make smoke       # against a running stack: one hazard per vertical must raise the right alert
```

### 7. Laptop -> cloud
The same `docker-compose.yml` runs anywhere Docker runs (VM, ECS/Compose, Azure/GCP VM). Only `.env` changes:
`BIND_ADDR=0.0.0.0`, `AUTH_REQUIRED=true`, a long random `ADMIN_TOKEN`, new `NEO4J_PASSWORD`/`POSTGRES_PASSWORD`,
remove `COMPOSE_PROFILES=sim`, add your `LLAMA_API_KEY`, and put TLS (Caddy / nginx / ALB) in front of ports 8000 and 3000.
Data lives in the `neo4j_data`, `pg_data`, `redis_data` volumes - swap them for managed Postgres/Redis/Neo4j Aura by
changing `DATABASE_URL`, `REDIS_URI`, `NEO4J_URI`. Scale any agent: `docker compose up -d --scale spark-multimodal=3`.

### 8. Handy commands
```bash
# --- run
make up                                    # = docker compose up -d --build
docker compose ps                          # health of every service
docker compose logs -f --tail=50 spark-multimodal feed-ingestor     # follow chosen agents
docker compose up -d --force-recreate feed-ingestor   # apply a .env change ("restart" does NOT re-read .env)
docker compose up -d --scale spark-multimodal=3       # scale a stateless agent
docker compose down                        # stop, keep data     |  make reset  = stop AND delete all data

# --- what is happening (replace tenant / camera as needed)
curl -s localhost:8000/health
curl -s localhost:8000/usage                                         # model calls + tokens today, per model
curl -s localhost:8000/tenants/home_1_bangalore/cameras              # cameras that sent a frame
curl -so latest.jpg localhost:8000/tenants/home_1_bangalore/cameras/kitchen/latest   # newest frame
curl -s "localhost:8000/tenants/home_1_bangalore/alerts?limit=5"
curl -s "localhost:8000/tenants/home_1_bangalore/events?limit=5"

# --- cost watch (daily cap counter, and frames that failed 4 times and were dead-lettered)
docker compose exec redis redis-cli get usage:calls:$(date +%F)
for s in raw_frames segmented perceived assessed decided proofed; do echo "$s: $(docker compose exec -T redis redis-cli xlen dead:$s)"; done

# --- Postgres (row-level security is forced, so act as admin for the session)
docker compose exec postgres psql -U drishti -d drishti -c \
  "SET app.admin='on'; SELECT created_at, tenant_id, level, decision, reason FROM events ORDER BY created_at DESC LIMIT 10;"

# --- Meta API: print the raw reply of one model for a tiny test image
docker compose exec -T gateway python - muse-spark-1.3 < scripts/probe_meta_api.py

# --- verify
make test        # whole pipeline in MOCK_MODE, no key / Docker needed
make smoke       # against the running stack: one hazard per vertical must raise the right alert
```
Cost knobs in `.env`: `SAMPLE_INTERVAL_SEC`, `FORCE_REFRESH_SEC`, `MAX_LLM_CALLS_PER_DAY`, `REASONING_EFFORT`,
`REASONING_HEADROOM_TOKENS`, `SAM_MODE`. Compare `/usage` with the usage page of the Meta dashboard now and then.

---

## 📡 API Endpoints

- `POST /ingest/{tenant_id}` - Upload frame + sensors JSON (`camera_id`, `force` optional)
- `GET /tenants` / `POST /tenants` - List / create tenants (mall, school, icu, home)
- `GET /tenants/{tenant_id}/cameras` and `/cameras/{camera_id}/latest` - Live view: newest frame of each camera
- `GET /tenants/{tenant_id}/alerts` - List alerts; `GET /alerts/{id}/image` annotated proof; `POST /alerts/{id}/ack`
- `GET /tenants/{tenant_id}/events` and `/events/{frame_id}/image` - Event timeline and the picture behind each event
- `GET /tenants/{tenant_id}/graph` - Neo4j subgraph
- `GET /verticals/{vertical}/stats` - Crowd count, fall count etc
- `GET /usage` - Model calls and tokens today; `GET /health`
- `GET /edge/{tenant_id}/actuate` - Pi polls this for speak / OLED / buzzer commands

---

## 🧩 Meta Model API Usage

The Python services call Meta's Responses API directly with `requests`; they do not require the OpenAI SDK.

```python
import os
import requests

response = requests.post(
    os.getenv("LLAMA_API_URL", "https://api.meta.ai/v1/responses"),
    headers={"Authorization": f"Bearer {os.environ['LLAMA_API_KEY']}"},
    json={
        "model": os.getenv("SPARK_MODEL", "muse-spark-1.3"),
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Assess this scene."}]}],
        "max_output_tokens": 512,
    },
    timeout=90,
)
response.raise_for_status()
print(response.json())
```

---

## 🛠️ Muse Code Skill

This repo includes `SKILL.md` for Muse Code.

When you say:
- "Add gate detection for school" -> Updates `services/spark-multimodal/prompts/school_children.txt` + Neo4j habit + restarts service
- "Add new vertical gym_security" -> Creates SOP doc, prompts, Cypher

Skill file: `./SKILL.md`

---

## 📹 Pi Edge to Cloud Flow

1. Pi captures frame every 30 sec + sensor JSON (PIR, 2x US, DHT, IR, gesture)
2. Edge compresses to 640x480 jpg + scene hash check
3. If PIR true + hash changed -> POST to `gateway /ingest/{tenant_id}`
4. feed-ingestor -> Redis queue per tenant
5. sam3-segmenter + ocr-reader -> spark-multimodal (Muse Spark with IMAGES+VIDEO+TEXT+DOCS)
6. wonder-vertical-reasoner queries Neo4j + news-context -> final decision
7. image-alert generates proof -> Action: speak via PAM8403 + 2" woofer, ISD1820 loud, OLED, WhatsApp

---

## 🏆 Hackathon Requirements Met

- [x] Muse Spark via Meta Model API
- [x] Muse Wonder via Meta Model API
- [x] Muse Image via Meta Model API
- [x] SAM 3.1 via Meta Model API
- [x] Open-weights via Baseten/Modal/Fireworks (Llama 3.2 Vision, Whisper, PaddleOCR)
- [x] Muse Code (SKILL.md + this README)
- [x] Docker Compose multi-agent
- [x] Neo4j graph DB
- [x] Multi-tenant (100 feeds), Multi-modal (IMAGES, VIDEO, TEXT, DOCS), Multi-vertical (mall, home seniors, school, ICU)
- [x] Edge: Pi 5 with USB Mic + PAM8403 + 5W woofer + ISD1820 + OLED

---

## 🔮 Future Roadmap

- **Robot Mode:** PCA9685 + SG90 pan-tilt + 2x continuous servos + L298N + LiPo + buck + UPS HAT, GPIO 12,13,18,19, robot_mode flag
- **5 Person Enroll:** enroll_5_people.py with Llama 3.2 Vision embeddings locally
- **Reminders:** reminders.db "scooter key at 7:30am for college"
- **RLHF:** Voice feedback +1 thank you, -1 wrong alert
- **Mobile App:** React Native for guard to see alerts

---

## 👨‍💻 Author

**Dhiraj Patra** - Bangalore, India
- Pi 5 16GB Builder
- Meta Global AI Hackathon 2026 - Indie Track
- Cost-focused: Rs 140/month target

---

## 📄 License

MIT - Built for learning and community safety.

---

## 🙏 Credits

- Meta Model API: Muse Spark, Wonder, Image, SAM 3.1
- Partner Credits: Baseten ($100), Modal ($100), Fireworks ($25)
- Hardware: Pi 5 community, HC-SR04 divider trick
- Neo4j, Redis, Postgres open-source

> "One Pi watches home, 100 Pis watch Bharat" - Drishti Vision
