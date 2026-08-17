.DEFAULT_GOAL := help
.PHONY: help install dev up down reset logs ps test test-unit test-cov lint fmt typecheck check \
	db-upgrade db-downgrade db-revision db-check openapi openapi-check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create the environment with uv
	uv sync

dev: ## Run the API locally with hot reload
	uv run uvicorn app.main:app --reload --port 8000

up: ## Bring up everything: postgres, redpanda, console, redis, api, and the observability stack
	docker compose up -d --build

down: ## Stop the services (keeps the data)
	docker compose down

reset: ## Stop the services and DELETE the data volumes
	docker compose down -v

logs: ## Follow the logs of every service
	docker compose logs -f

ps: ## Service status
	docker compose ps

test: ## All tests
	uv run pytest

test-unit: ## Only the tests that don't need services running
	uv run pytest -m "not integration and not e2e"

test-cov: ## All tests with a coverage report (fails below the threshold in pyproject.toml)
	uv run pytest --cov

lint: ## Lint without modifying files
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Format and auto-fix what can be auto-fixed
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Static type checking
	uv run mypy

check: lint typecheck openapi-check db-check test-cov ## Everything CI requires

db-upgrade: ## Apply pending migrations
	uv run alembic upgrade head

db-downgrade: ## Revert the last migration
	uv run alembic downgrade -1

db-revision: ## Autogenerate a migration from the models (usage: make db-revision m="message")
	uv run alembic revision --autogenerate -m "$(m)"

db-check: ## Fail if the models and the committed migrations have drifted apart
	uv run alembic check

openapi: ## Regenerate contracts/openapi.json from the current code
	uv run python scripts/export_openapi.py

openapi-check: ## Fail if contracts/openapi.json doesn't match the current code
	uv run python scripts/export_openapi.py
	git diff --exit-code contracts/openapi.json
