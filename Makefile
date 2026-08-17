.DEFAULT_GOAL := help
.PHONY: help install dev up down reset logs ps test test-unit test-cov lint fmt typecheck check \
	db-upgrade db-downgrade db-revision db-check openapi openapi-check

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Crea el entorno con uv
	uv sync

dev: ## Arranca la API en local con recarga automática
	uv run uvicorn app.main:app --reload --port 8000

up: ## Levanta todo: postgres, redpanda, console, redis, api y el stack de observabilidad
	docker compose up -d --build

down: ## Para los servicios (conserva los datos)
	docker compose down

reset: ## Para los servicios y BORRA los volúmenes de datos
	docker compose down -v

logs: ## Sigue los logs de todos los servicios
	docker compose logs -f

ps: ## Estado de los servicios
	docker compose ps

test: ## Todos los tests
	uv run pytest

test-unit: ## Solo los tests que no necesitan servicios levantados
	uv run pytest -m "not integration and not e2e"

test-cov: ## Todos los tests con reporte de cobertura (falla por debajo del umbral de pyproject.toml)
	uv run pytest --cov

lint: ## Lint sin modificar archivos
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Formatea y arregla lo que se pueda automáticamente
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Comprobación estática de tipos
	uv run mypy

check: lint typecheck openapi-check db-check test-cov ## Todo lo que exige el CI

db-upgrade: ## Aplica las migraciones pendientes
	uv run alembic upgrade head

db-downgrade: ## Revierte la última migración
	uv run alembic downgrade -1

db-revision: ## Autogenera una migración a partir de los modelos (uso: make db-revision m="mensaje")
	uv run alembic revision --autogenerate -m "$(m)"

db-check: ## Falla si los modelos y las migraciones commiteadas divergieron
	uv run alembic check

openapi: ## Regenera contracts/openapi.json a partir del código actual
	uv run python scripts/export_openapi.py

openapi-check: ## Falla si contracts/openapi.json no coincide con el código actual
	uv run python scripts/export_openapi.py
	git diff --exit-code contracts/openapi.json
