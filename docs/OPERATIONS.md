# Operating Drishti

## Laptop to cloud

The same `docker-compose.yml` runs anywhere Docker runs (VM, ECS/Compose, Azure/GCP VM). Only `.env` changes:
`BIND_ADDR=0.0.0.0`, `AUTH_REQUIRED=true`, a long random `ADMIN_TOKEN`, new `NEO4J_PASSWORD`/`POSTGRES_PASSWORD`,
remove `COMPOSE_PROFILES=sim`, add your `LLAMA_API_KEY`, and put TLS (Caddy / nginx / ALB) in front of ports 8000 and 3000.
Data lives in the `neo4j_data`, `pg_data`, `redis_data` volumes - swap them for managed Postgres/Redis/Neo4j Aura by
changing `DATABASE_URL`, `REDIS_URI`, `NEO4J_URI`. Scale any agent: `docker compose up -d --scale spark-multimodal=3`.

## Handy commands

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
