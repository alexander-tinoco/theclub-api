# Action plan — theclub-api

## Context

The Club is a casino platform built as a portfolio project, split across 3 repositories that connect to each other:

- **theclub-web**: the interface the user plays on.
- **theclub-api** (this repo): the backend that computes game results and publishes events to Kafka.
- **theclub-data**: the data pipeline (Kafka → S3 → Databricks) that turns those events into business analytics.

`theclub-api` is the game engine: it computes results, manages balances and sessions, and publishes every event to Kafka for `theclub-data` to process. Today the repository is **empty** (no commits), same as `theclub-web` and `theclub-data`. So this repo doesn't just build itself: it **defines the contracts** (REST, WebSocket, and event schema) that the other two depend on. A late change to the event schema breaks the data pipeline, so contracts are frozen and versioned early.

### Stack

Python + FastAPI · PostgreSQL · SQLAlchemy 2.0 + Alembic · aiokafka · JWT · pytest · Docker + GitHub Actions.

### Decisions made

| Open question | Decision |
|---|---|
| Managed or local Kafka | **Redpanda in docker-compose** (Kafka-compatible API), with config isolated so migrating to Confluent Cloud only requires changing environment variables |
| One topic or several | **One topic per event type**, named `theclub.<domain>.<event>.v1` |
| REST or GraphQL | **REST + WebSocket** |
| DB hosting | **Postgres in Docker** for dev and tests; Neon when it's time to deploy |
| MVP games | **European roulette only**, done thoroughly |
| RNG | **Provably fair**: HMAC-SHA256 over `server_seed + client_seed + nonce`, with commit-reveal |
| Deploy | **No deploy for now**; CI on GitHub Actions (lint, types, tests, image build) |
| Dependency manager | **uv** (`uv.lock` versioned, `uv sync --frozen` in CI) |
| Python version | **3.14**, managed by uv locally and in the Docker image. Accepted risk: if some binary dependency doesn't ship a wheel for 3.14 (candidates: `psycopg` in Phase 3, `aiokafka` in Phase 6), the fix is dropping to 3.13 in `.python-version` and the Dockerfile — no code changes needed |

### Out of scope (explicit)

Slot machines, blackjack, real money or a payment gateway, Avro + Schema Registry, cloud deployment, multi-instance with Redis. All of that is left for a later phase and the design leaves room for it, but it isn't built now.

---

## Workflow (swarm-forge style, run by the two of us)

No multi-agents. We work through each phase sequentially and **you request one role at a time**. The value is in not mixing roles: when you ask for code, design isn't discussed, and when you ask for review, no new functionality gets written.

| Role | What gets requested | What it delivers | What it does NOT do |
|---|---|---|---|
| **Architect** | "Design phase N" | File structure, function signatures, table or event schema, decisions and trade-offs | Doesn't write implementation |
| **Implementer** | "Implement X per the design" | Production code for a scoped module, following the agreed design | Doesn't invent scope or write tests |
| **Tester** | "Write the tests for X" | Unit, property-based, and integration tests, including edge and failure cases | Doesn't modify production code to make them pass |
| **Reviewer** | "Review phase N" or `/code-review` | Correctness bugs, race conditions, security gaps, simplifications | Doesn't apply changes unless asked |
| **Integrator** | "Close phase N" | Alembic migration, contracts/OpenAPI update, README, clean commit | Doesn't start the next phase |

**Progress rule:** don't move to the next phase until the current one meets its *Definition of Done* (each phase has its own, below). If a phase reveals that a previous decision was wrong, this document gets updated **before** continuing to write code.

**Order within each phase:** Architect → Implementer → Tester → Reviewer → Integrator. For small phases, Implementer and Tester can be merged into a single request ("implement X with its tests"), but Reviewer always stays separate.

---

## Architecture

### Folder structure

```
theclub-api/
├── app/
│   ├── main.py                  # FastAPI assembly, lifespan, routers
│   ├── config.py                # Settings via pydantic-settings (12-factor)
│   ├── api/
│   │   ├── deps.py              # injection: DB session, current user, idempotency
│   │   ├── errors.py            # domain exceptions → HTTP responses
│   │   └── v1/
│   │       ├── auth.py          # /register, /login, /refresh, /me
│   │       ├── wallet.py        # /balance, /transactions, /deposit
│   │       ├── roulette.py      # /bets, /rounds, /fairness
│   │       └── ws.py            # /ws — live results and balance
│   ├── domain/                  # PURE CORE: no IO, no SQLAlchemy, no FastAPI
│   │   ├── money.py             # Money type over integers (minor units)
│   │   ├── fairness.py          # commit-reveal, HMAC, unbiased sampling
│   │   └── roulette/
│   │       ├── table.py         # 37 pockets, bet types, payouts
│   │       ├── bets.py          # bet validation and resolution
│   │       └── engine.py        # spin(seed_material) -> Outcome
│   ├── models/                  # SQLAlchemy 2.0 (Mapped/mapped_column)
│   ├── repositories/            # data access; no business logic
│   ├── services/                # use cases: place_bet, deposit, register…
│   ├── events/
│   │   ├── schemas.py           # envelope + payloads (Pydantic)
│   │   ├── outbox.py            # writing to the outbox within the transaction
│   │   └── relay.py             # outbox → Kafka publisher
│   └── infra/
│       ├── db.py                # async engine, session, unit of work
│       ├── kafka.py             # producer (aiokafka), retry, clean shutdown
│       └── security.py          # argon2 hashing, JWT issuance/validation
├── contracts/                   # SHARED ARTIFACT with the other two repos
│   ├── openapi.json             # generated, versioned in git
│   └── events/*.schema.json     # JSON Schema per event type
├── alembic/versions/
├── tests/{unit,integration,e2e}/
├── plan/                        # this document
├── docker-compose.yml           # postgres + redpanda + console
├── Dockerfile
└── .github/workflows/ci.yml
```

**Hard rule:** `app/domain/` doesn't import anything from `app/models`, `app/infra`, or FastAPI. It's pure math, testable without spinning anything up. This is what lets a test simulate millions of spins to verify RTP.

### Data model

Postgres. Money in **`BIGINT` minor units (cents)** — never float, never `Numeric` with ambiguous rounding. All timestamps in `TIMESTAMPTZ` UTC.

- **`users`** — `id` (UUID), `email` (unique, citext), `password_hash` (argon2id), `status`, `created_at`.
- **`wallets`** — `user_id` (unique), `balance_minor` (BIGINT, `CHECK >= 0`), `currency`, `version` (optimistic lock).
- **`ledger_entries`** — append-only ledger, the source of truth for money: `wallet_id`, `amount_minor` (signed), `balance_after_minor`, `kind` (`deposit|bet_stake|bet_payout|adjustment`), `ref_type`, `ref_id`, `created_at`. The wallet's balance is a cache of the ledger's sum; an invariant test verifies it.
- **`seed_pairs`** — provably fair: `user_id`, `server_seed` (revealed only on rotation), `server_seed_hash` (public), `client_seed`, `nonce` (counter), `status` (`active|revealed`), `revealed_at`.
- **`rounds`** — a roulette round: `id`, `user_id`, `seed_pair_id`, `nonce`, `outcome` (0–36), `status`, `created_at`, `settled_at`.
- **`bets`** — `round_id`, `bet_type`, `selection` (JSONB), `stake_minor`, `payout_minor`, `status`.
- **`idempotency_keys`** — `key`, `user_id`, `request_hash`, `response_body`, `status`, `expires_at`. Unique per `(user_id, key)`.
- **`outbox`** — `id`, `topic`, `key`, `payload` (JSONB), `headers`, `created_at`, `published_at`, `attempts`, `last_error`. Partial index on `published_at IS NULL`.

### The three invariants everything rests on

1. **Money is never lost or duplicated.** Every movement is an entry in `ledger_entries` within the same transaction that updates `wallets`. The debit uses `UPDATE wallets SET balance_minor = balance_minor - :stake WHERE id = :id AND balance_minor >= :stake RETURNING balance_minor` — atomic in a single statement, no prior read, so two simultaneous bets can never overdraw. If it affects 0 rows → `InsufficientFunds`.
2. **An event is published if and only if its transaction committed.** No writing to Postgres and publishing to Kafka in parallel (*dual write*): if Kafka fails after the commit, `theclub-data` loses an event forever. Instead, the **outbox pattern**: the service inserts the event into the `outbox` table in the *same* transaction, and a background relay publishes it and marks it. Kafka down = events pile up, zero loss, drained once it's back.
3. **An outcome is reproducible and verifiable.** `outcome = f(server_seed, client_seed, nonce)`, deterministic. Anyone can recompute it with the data we expose after the reveal.

### Provably fair — how it works

1. On registration (or rotation), a 32-byte `server_seed` is generated with `secrets.token_bytes`. It's stored, and only `sha256(server_seed)` is published.
2. The client can set their `client_seed`; if not, one is assigned.
3. Each spin consumes a `nonce` (incremental, unique per seed pair).
4. `digest = HMAC-SHA256(key=server_seed, msg=f"{client_seed}:{nonce}")`.
5. From the digest, a uniform integer in 0–36 is derived with **rejection sampling**: 4 bytes are taken, and if the value falls in the modulus's biased range, it advances to the next 4 bytes. Never plain `int(digest) % 37` — that introduces a measurable bias a uniformity test would catch.
6. On seed rotation, the previous `server_seed` is revealed; the player verifies their hash matches and recomputes their spins.

`GET /fairness/current` returns the hash and client seed; `POST /fairness/rotate` reveals the previous one and activates a new one.

### European roulette

37 pockets (0–36), a single zero. Exact payouts, all with a **2.70% house edge** (97.30% RTP):

| Bet | Coverage | Pays | Bet | Coverage | Pays |
|---|---|---|---|---|---|
| Straight | 1 | 35:1 | Dozen | 12 | 2:1 |
| Split | 2 | 17:1 | Column | 12 | 2:1 |
| Street | 3 | 11:1 | Red/Black | 18 | 1:1 |
| Corner | 4 | 8:1 | Odd/Even | 18 | 1:1 |
| Line | 6 | 5:1 | High/Low | 18 | 1:1 |

Zero is neither red nor black, neither odd nor even, neither high nor low: it loses every outside bet (no *la partage* — that choice is documented, not implicit). The red-numbers table is a literal set, not a formula. A round admits several bets: all are validated, the total stake is summed, debited once, and each is resolved.

### Event contract toward Kafka

Topics (one per type, partitioned by `user_id` to preserve per-user ordering):

- `theclub.bets.placed.v1`
- `theclub.rounds.settled.v1`
- `theclub.wallet.transactions.v1`

Common envelope across all of them:

```json
{
  "event_id": "uuid",
  "event_type": "round.settled",
  "event_version": 1,
  "occurred_at": "2026-08-16T12:00:00Z",
  "producer": "theclub-api",
  "idempotency_key": "…",
  "data": { }
}
```

Rules: `event_id` allows downstream deduplication; `occurred_at` is business time (not publish time); amounts are always integers in minor units with their `currency`; no PII beyond `user_id` (UUID) — no emails in the stream. JSON for the MVP, but the JSON Schemas live in `contracts/events/` and **CI fails if a schema changes without a version bump**. That's the seam through which Avro + Schema Registry can come in later without drama.

---

## Phases

Each phase ends with its *Definition of Done* met and a commit.

### Phase 0 — Foundation
Repo structure, `pyproject.toml` (uv or poetry), strict `ruff` + `mypy`, `docker-compose.yml` (Postgres + Redpanda + console), multi-stage `Dockerfile`, `Settings` via pydantic-settings, `/health` and `/ready`, `.env.example`, `pytest` configured.
**DoD:** `docker compose up` brings everything up; `GET /health` responds 200; `pytest` runs (even with just 1 trivial test).

### Phase 1 — Contracts (before any logic)
JSON Schema for the three events in `contracts/events/`, Pydantic models for the envelope, and a documented draft of the REST/WS endpoints. This comes first because `theclub-web` and `theclub-data` can't start without it.
**DoD:** schemas validated with examples; contract document readable by the other two repos.

### Phase 2 — Pure domain: fairness + roulette
Complete `app/domain/`: `Money`, commit-reveal, HMAC, unbiased sampling, roulette table, bet validation and resolution. Zero IO.
**DoD:** unit tests of payouts for all 13 bet types; reproducibility test (same seeds → same result); uniformity test over ≥1M spins (chi-squared); RTP test converging to 97.30% ± margin; property-based tests with `hypothesis` so no payout is negative or overflows.

### Phase 3 — Persistence
Async SQLAlchemy 2.0 models, initial Alembic migration, repositories, unit of work.
**DoD:** clean `alembic upgrade head` and `downgrade base`; ledger↔balance invariant test; concurrency test: N simultaneous bets on the same wallet never leave the balance negative.

### Phase 4 — Authentication
Register/login with argon2id, JWT access (short) + refresh (rotating, with revocation), `get_current_user`, rate limiting on auth endpoints.
**DoD:** tests for expired token, invalid signature, reused refresh (must revoke the whole family), duplicate email, and that the password hash never appears in a response or a log.

### Phase 5 — The betting use case (the heart of it)
`POST /rounds` with a mandatory `Idempotency-Key`: validates bets → debits atomically → derives the outcome → computes payout → credits → writes to outbox → responds. All in one transaction. Plus `/balance`, `/transactions`, `/rounds` (cursor-paginated history), `/deposit` (simulated).
**DoD:** tests for insufficient funds, stake ≤ 0, malformed bet, table limits, retry with the same `Idempotency-Key` (same response, no double charge), and same key with a different body (409).

### Phase 6 — Kafka
`aiokafka` producer with `acks=all`, `enable_idempotence`, retries, and clean shutdown. Outbox relay as a background task with exponential backoff and `FOR UPDATE SKIP LOCKED` locking (safe with multiple instances).
**DoD:** integration test against real Redpanda: bet → all 3 events show up in their topics matching the schema shape; Kafka-down test → events stay in the outbox and drain once it's restored, without duplicating money.

### Phase 7 — WebSocket
Token-authenticated `/ws`, with a per-user channel, ping/pong heartbeat, connection limit, and ordered shutdown. Emits `round.settled` and `balance.updated`.
**DoD:** e2e test: a connected WS client receives the result after the POST; invalid token → 4401 close; the in-memory broadcaster sits behind an interface so it can be swapped for Redis pub/sub without touching the handlers.

### Phase 8 — Hardening and observability
Structured JSON logging with `request_id` and `user_id`, a global exception handler that never leaks internals, restricted CORS, global rate limiting, payload size limits, optional `/metrics`. Full security review.
**DoD:** `/code-review` and `/security-review` with no high-severity findings; no secrets in the repo; logs contain no active seeds or tokens.

### Phase 9 — CI
GitHub Actions: ruff, mypy, pytest with coverage against Postgres + Redpanda services, Dockerfile build, verification that `openapi.json` and the committed JSON Schemas are up to date, and `alembic check` against model drift.
**DoD:** green CI on a PR; README with a startup time under 5 minutes.

---

## Failure scenarios the design covers

| Scenario | Design response |
|---|---|
| Double click / network retry on a bet | Mandatory `Idempotency-Key`; the original response is returned |
| Two concurrent bets with a tight balance | Atomic `UPDATE … WHERE balance >= stake` + `CHECK >= 0` on the table |
| Kafka down | Outbox accumulates; the game keeps working; drains once it's back |
| Crash between commit and publish | The relay picks up the outbox on restart |
| A `theclub-data` consumer that reprocesses | `event_id` allows deduplication; events are immutable |
| Change to an event's schema | Versioned `.v1` topics + CI that requires a version bump |
| RNG bias | Rejection sampling + chi-squared test in CI |
| User accuses the house of manipulation | Commit-reveal: they recompute the result themselves |
| Stolen refresh token | Rotation with reuse detection → revokes the whole family |
| Drift between models and migrations | `alembic check` in CI |
| Multiple API instances | Relay with `SKIP LOCKED`; WS behind an interface for Redis pub/sub |

---

## End-to-end verification

1. `docker compose up -d` → Postgres + Redpanda up.
2. `alembic upgrade head` → schema created.
3. `pytest -m "not integration"` → pure domain, fast, includes RTP and uniformity.
4. `pytest -m integration` → real DB and Kafka.
5. Manual: register a user → `GET /fairness/current` (note the hash) → deposit → connect to the WS → place a bet → see the result over HTTP and over WS → `POST /fairness/rotate` → recompute the result by hand with the revealed seed and check it matches.
6. Redpanda console (`localhost:8090`) → the three topics with their messages.
7. Restart the Redpanda container mid-way through a batch of bets → verify no event is lost and no balance ends up wrong.

---

## First step

Phase 0, **Architect** role: propose the concrete file structure and `docker-compose.yml` before writing any code.
