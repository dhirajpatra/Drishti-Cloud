.PHONY: up down logs ps smoke test reset
up:      ## build + start everything
	docker compose up -d --build
down:
	docker compose down
logs:    ## tail every agent:  make logs S=spark-multimodal
	docker compose logs -f --tail=100 $(S)
ps:
	docker compose ps
smoke:   ## inject one hazard per vertical and verify alerts (run after `make up`, MOCK_MODE)
	docker compose exec -T gateway python - < scripts/smoke.py
test:    ## unit + pipeline tests (needs redis-server and postgres binaries; uses an in-memory graph)
	python -m pytest tests -q
reset:   ## DESTROYS all data volumes
	docker compose down -v
