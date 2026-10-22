.PHONY: up down logs seed pipeline check-sources test types graphs eval eval-record snapshot

BT = docker compose -f docker-compose.test.yml run --rm -T api-test
FT = docker compose -f docker-compose.test.yml run --rm web-test
EV = docker compose -f docker-compose.test.yml run --rm -T eval

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

seed:
	docker compose exec api python -m app.seed

pipeline:
	docker compose exec api python -m app.cli.run_ingestion

check-sources:
	docker compose exec api python -m app.cli.check_sources

test:
	$(BT) pytest
	$(FT) npx vitest run

types:
	$(BT) python -m app.cli.export_openapi > frontend/openapi.json
	$(FT) npm run gen:api

graphs:
	$(BT) python -m app.cli.export_graphs   # prints Mermaid; the README embeds it

# replay the committed recordings: no API key, no cost; fails unless it reproduces
# backend/evals/results/latest.json
eval:
	$(EV) python -m evals.run

# make the model calls the recordings lack (live, capped at $0.30) and rewrite latest.*
eval-record:
	$(EV) python -m evals.run --mode record --max-usd 0.30

# Rebuild backend/snapshot/ (the demo data a brand-new install starts from) from this
# stack's database: one read-only pg_dump, restored into a throwaway database of the test
# project, sanitised and scanned there (app/cli/build_snapshot.py). Rebuild the images after.
SNAP_DB = frogeye_snapshot
TDB = docker compose -f docker-compose.test.yml exec -T db-test

snapshot:
	docker compose -f docker-compose.test.yml up -d --wait db-test
	$(TDB) dropdb -U frogeye --if-exists $(SNAP_DB)
	$(TDB) createdb -U frogeye $(SNAP_DB)
	docker compose exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -Fc --no-owner --no-privileges' \
	  | $(TDB) pg_restore -U frogeye -d $(SNAP_DB) --no-owner --exit-on-error
	$(BT) python -m app.cli.build_snapshot sanitise --database $(SNAP_DB)
	$(TDB) pg_dump -U frogeye -d $(SNAP_DB) --no-owner --no-privileges --no-comments \
	  | $(BT) python -m app.cli.build_snapshot package
	$(TDB) dropdb -U frogeye $(SNAP_DB)
