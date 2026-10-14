# Grailed Liquidity Analyzer - macOS developer commands. `make help` lists them.
PY := .venv/bin/python
BACKEND := cd backend &&
FRONTEND := cd frontend &&

.DEFAULT_GOAL := help
.PHONY: help setup start dev migrate test cov lint typecheck backend-check \
	fe-install fe-lint fe-typecheck fe-test build frontend-check check

help: ## Show this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Create venv, install deps, migrate, build UI (idempotent)
	scripts/setup.sh

start: ## Serve the built app on http://127.0.0.1:8000
	scripts/start.sh run

dev: ## Backend :8000 + Next.js dev server :3000
	scripts/start.sh dev

migrate: ## alembic upgrade head
	$(BACKEND) $(PY) -m alembic upgrade head

test: ## Backend tests (pass ARGS="tests/test_x.py -k name" to filter)
	$(BACKEND) $(PY) -m pytest $(ARGS)

cov: ## Backend tests with the coverage floor, as CI runs them
	$(BACKEND) $(PY) -m pytest --cov

lint: ## ruff check
	$(BACKEND) $(PY) -m ruff check app tests

typecheck: ## mypy (strict)
	$(BACKEND) $(PY) -m mypy

backend-check: lint typecheck cov ## All backend CI checks

fe-install: ## pnpm install from the lockfile
	$(FRONTEND) CI=true pnpm install --frozen-lockfile

fe-lint: ## eslint
	$(FRONTEND) pnpm run lint

fe-typecheck: ## tsc --noEmit
	$(FRONTEND) pnpm run typecheck

fe-test: ## vitest
	$(FRONTEND) pnpm run test

build: ## Static export to frontend/out/
	$(FRONTEND) pnpm run build

frontend-check: fe-lint fe-typecheck fe-test build ## All frontend CI checks

check: backend-check frontend-check ## Everything CI runs
