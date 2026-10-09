# DIBBS Bid Copilot - common commands (Docker Compose).
# Without Docker/make (e.g. on Windows), use the equivalent `python dev.py <target>`.

COMPOSE ?= docker compose
BACKEND = $(COMPOSE) exec -T backend
FSC ?=
START ?= 2023-01-01
END ?= today
MODE ?=

.PHONY: help up down logs build migrate seed backfill ingest labels train fsc-report test test-backend \
        test-frontend test-e2e lint api-types shell

help:
	@echo "make up | down | logs | seed | backfill FSC=5305,5310 START=2023-01-01 END=today | ingest | labels"
	@echo "make train | fsc-report | test | lint | api-types"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

build:
	$(COMPOSE) build

migrate:
	$(BACKEND) python -m app.cli migrate

# Demo orgs, synthetic history (unless real backfilled data exists), today's RFQs, a trained model.
seed:
	$(BACKEND) python -m app.cli seed

# Resumable historical backfill. MODE=live to hit DIBBS (default: CONNECTOR_MODE from env, fixture in demo).
backfill:
	$(BACKEND) python -m app.cli backfill --fsc "$(FSC)" --start $(START) --end $(END) $(if $(MODE),--mode $(MODE),)

ingest:
	$(BACKEND) python -m app.cli ingest $(if $(MODE),--mode $(MODE),)

labels:
	$(BACKEND) python -m app.cli labels $(if $(MODE),--mode $(MODE),)

train:
	$(BACKEND) python -m app.cli train

fsc-report:
	$(BACKEND) python -m app.cli fsc-report

test: test-backend test-frontend

test-backend:
	$(BACKEND) python -m pytest

# Vitest units + Playwright end-to-end against the running stack (seeds first; seeding is idempotent).
test-frontend: seed
	$(COMPOSE) --profile test run --rm --build frontend-test

lint:
	$(BACKEND) ruff check app tests ../ml
	cd frontend && npm run lint && npm run typecheck

# Regenerate the typed API client from the backend's OpenAPI schema.
api-types:
	$(BACKEND) python -m app.cli export-openapi --out /app/data/openapi.json
	cp data/openapi.json frontend/openapi.json
	cd frontend && npm run gen:api

shell:
	$(BACKEND) bash
