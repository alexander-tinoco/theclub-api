<h1 align="center">🎰 theclub-api</h1>
<p align="center"><b>Provably-fair roulette engine — real-time WebSocket, Kafka event streaming, and a full observability stack.</b></p>

<p align="center">
  <a href="https://github.com/alexander-tinoco/theclub-api/actions/workflows/ci.yml"><img src="https://github.com/alexander-tinoco/theclub-api/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.14-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.14">
  <img src="https://img.shields.io/badge/FastAPI-async-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/coverage-99%25-brightgreen?style=flat-square" alt="Coverage 99%">
  <img src="https://img.shields.io/badge/docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker ready">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT License"></a>
</p>

The Club's game engine: resolves European roulette rounds, moves money with ledger-grade
integrity, and streams every event to Kafka. A portfolio project built to production
standards — not a CRUD demo.

- 🎲 **Provably fair** — HMAC-SHA256 commit-reveal, verifiable by any player after the fact
- 💰 **Money as integer minor units**, ledger-backed balance, tested under real concurrency
- 📨 **Outbox pattern** — no dual-writes; a Kafka outage never loses or duplicates an event
- 🔌 **Real-time WebSocket** — round results and balance updates pushed live
- 🔐 **JWT auth** with refresh rotation + reuse detection, and zero-downtime secret rotation
- 📊 **Full observability** — Prometheus, Grafana, Loki, Alertmanager, all provisioned as code
- ✅ **220 tests, 99% coverage**, CI on every push (lint, types, contracts, Docker build)

## Screenshots

<table>
  <tr>
    <td width="33%"><img src="docs/screenshots/grafana-dashboard.png" alt="Grafana dashboard"><br><sub>Grafana — live metrics dashboard</sub></td>
    <td width="33%"><img src="docs/screenshots/api-docs.png" alt="API docs"><br><sub>Interactive API docs (Swagger UI)</sub></td>
    <td width="33%"><img src="docs/screenshots/redpanda-console.png" alt="Redpanda console"><br><sub>Redpanda — Kafka event topics</sub></td>
  </tr>
</table>

## Architecture

```mermaid
flowchart LR
    Client(["Client / Browser"])
    API["FastAPI<br/>REST + WebSocket"]
    Domain["domain/<br/>pure fairness + roulette engine"]
    PG[("PostgreSQL<br/>ledger · outbox · idempotency")]
    Redis[("Redis<br/>rate limiting")]
    Relay["outbox relay"]
    Kafka[("Kafka / Redpanda<br/>bets · rounds · wallet events")]
    Obs["Prometheus · Grafana<br/>Loki · Alertmanager"]

    Client -- HTTP / WS --> API
    API --> Domain
    API --> PG
    API --> Redis
    PG -. outbox rows .-> Relay
    Relay --> Kafka
    API -. metrics · logs .-> Obs
```

`app/domain/` has zero external dependencies — no FastAPI, no SQLAlchemy, no IO. It's pure
math over money, fairness, and the roulette table, which is what lets 1M simulated spins run
in ~4s without Docker.

<details>
<summary><b>Provably fair, in one diagram</b></summary>

```mermaid
sequenceDiagram
    participant P as Player
    participant A as theclub-api

    Note over A: generates server_seed (32 bytes)
    A->>P: sha256(server_seed)
    Note over P,A: the hash is published before any bet exists

    loop each spin: nonce = 1, 2, 3...
        P->>A: bet + client_seed
        A->>A: HMAC(server_seed, client_seed:nonce) mod 37
        A->>P: outcome
    end

    Note over A: on seed rotation
    A->>P: reveals server_seed
    P->>P: sha256(server_seed) == published hash?
    P->>P: recomputes every spin with the public algorithm
```
</details>

## Engineering highlights

Patterns and decisions worth a second look:

- **Money never touches a float.** Every amount is a `BIGINT` in minor units. Debits are a
  single `UPDATE wallets SET balance = balance - :stake WHERE balance >= :stake RETURNING
  balance` — no read-then-write, no race window. Verified with a test that fires 10 truly
  concurrent requests (`asyncio.gather`, not a loop) at the same idempotency key and checks
  the database ends up debited exactly once.
- **Outbox pattern, not dual-writes.** Every domain event is inserted into an `outbox` table
  in the *same transaction* as the business write. A background relay (`FOR UPDATE SKIP
  LOCKED`, exponential backoff) publishes to Kafka afterward — a broker outage delays
  events, it never loses or duplicates money.
- **Idempotency as a first-class concern**, not a decorator: `Idempotency-Key` is required
  on every state-changing endpoint, backed by a 3-transaction claim/execute/reconcile
  mechanism that handles genuinely concurrent duplicate requests, not just sequential
  retries.
- **Provably fair RNG.** HMAC-SHA256 commit-reveal with rejection sampling (never `hash %
  37`, which is measurably biased) — any player can recompute a spin once the server seed
  is revealed.
- **JWT auth done properly**: short-lived access tokens, opaque (non-JWT) refresh tokens
  with rotation and reuse detection that revokes the entire token family on theft, and
  zero-downtime secret rotation (`JWT_PREVIOUS_SECRETS`) so rotating the signing key doesn't
  log everyone out.
- **Structured, correlated logging.** One canonical JSON line per request/connection
  (`request_id`, `user_id`, status, duration, business fields) via `contextvars` and a
  custom ASGI middleware — not scattered `logger.info` calls, and not `BaseHTTPMiddleware`
  (which silently breaks WebSocket support and context propagation).
- **Real integration tests, not mocks.** The suite runs against actual Postgres, Redis, and
  Kafka containers — including a test that kills the real Redpanda container mid-bet to
  verify the outbox survives a broker outage, and a Hypothesis-based property test that
  fuzzes the roulette payout math.
- **Observability as code.** Prometheus, Grafana (datasources + dashboard), Loki, and
  Alertmanager are entirely provisioned via committed config — nothing clicked together in
  a UI.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync                 # Python 3.14 + dependencies
cp .env.example .env    # optional — every value has a sane local default
make up                 # postgres + redpanda + redis + api + observability stack
curl localhost:8010/health
```

<details>
<summary>Full service list and ports</summary>

| Service | URL | Port variable |
|---|---|---|
| API | http://localhost:8010 | `API_PORT` |
| Interactive docs | http://localhost:8010/docs | — |
| Redpanda console | http://localhost:8090 | `CONSOLE_PORT` |
| Postgres | `localhost:5432` (`theclub`/`theclub`) | `POSTGRES_PORT` |
| Kafka (from host) | `localhost:19092` | `KAFKA_PORT` |
| Redis | `localhost:6389` | `REDIS_PORT` |
| Grafana | http://localhost:3002 (anonymous access) | `GRAFANA_PORT` |
| Prometheus | http://localhost:9091 | `PROMETHEUS_PORT` |
| Loki | http://localhost:3100 | `LOKI_PORT` |
| Alertmanager | http://localhost:9093 | `ALERTMANAGER_PORT` |

Ports default off the beaten path (9091, 3002, 6389...) to avoid clashing with other local
projects — override any of them in `.env`.
</details>

```bash
make dev    # local API with hot reload, no image rebuild
make test   # full suite  ·  make test-unit   # no services required
make check  # everything CI runs: lint + typecheck + contracts + db-check + coverage
```

## API

| Endpoint | What it does |
|---|---|
| `POST /api/v1/auth/register` / `/login` / `/refresh` / `/logout` | Account + token lifecycle, refresh rotation with reuse detection |
| `GET /api/v1/roulette/fairness/current` / `POST .../rotate` | Commit-reveal seed pair |
| `POST /api/v1/roulette/rounds` | Place one or more bets, resolved in the same request (`Idempotency-Key` required) |
| `GET /api/v1/wallet/balance` / `/transactions` / `POST /deposit` | Balance, ledger history, simulated deposit |
| `GET /api/v1/ws?token=` | WebSocket — pushes `round.settled` / `balance.updated` live |
| `GET /health` / `/ready` / `/metrics` | Liveness, readiness, Prometheus metrics |

Auth endpoints are rate-limited to 5/min per IP; everything else defaults to 200/min.
Full request/response schema: [`contracts/openapi.json`](contracts/openapi.json) (auto-verified
against the code in CI) or `/docs` once the API is running.

## Tech stack

| Layer | Choices |
|---|---|
| API | FastAPI, Pydantic v2, WebSockets, `slowapi` (rate limiting) |
| Data | PostgreSQL, SQLAlchemy 2.0 (async), Alembic, Redis |
| Messaging | Kafka (Redpanda), `aiokafka` |
| Auth | PyJWT, `argon2-cffi` (Argon2id password hashing) |
| Observability | Prometheus, Grafana, Loki, Promtail, Alertmanager |
| Testing | pytest, pytest-asyncio, Hypothesis (property-based), httpx, coverage.py |
| Tooling | Docker, Docker Compose, GitHub Actions, uv, ruff, mypy (strict) |

## Status

All 10 phases of the original plan are complete.

| | | | |
|---|---|---|---|
| 0 · Foundation | ✅ | 6 · Kafka + outbox relay | ✅ |
| 1 · Event contracts | ✅ | 7 · WebSocket | ✅ |
| 2 · Fairness + roulette domain | ✅ | 8 · Hardening + observability | ✅ |
| 3 · Persistence | ✅ | 9 · CI | ✅ |
| 4 · Auth | ✅ | Redis rate limiting + secret rotation | ✅ |
| 5 · Place-bet use case | ✅ | Alertmanager | ✅ |

## Docs

- [`plan/theclub-api-PLAN.md`](plan/theclub-api-PLAN.md) — full design: data model, fairness
  algorithm, event contract, phase-by-phase decisions and trade-offs.
- [`contracts/`](contracts/) — event JSON Schemas and the OpenAPI contract shared with
  `theclub-web`/`theclub-data`.
- [`CLAUDE.md`](CLAUDE.md) — how this repo is built with Claude Code (role-based workflow,
  commit conventions).
- [`LICENSE`](LICENSE) — MIT.
