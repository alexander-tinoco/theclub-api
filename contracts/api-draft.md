# API draft — REST + WebSocket

Reference draft so `theclub-web` can start building against something stable. It's not a
generated OpenAPI — that arrives once the real endpoints exist (Phases 4, 5, and 7), at
which point this file stops being the source of truth and `openapi.json` (generated from
the app, verified in CI in Phase 9) takes over.

Common prefix: `/api/v1`. Auth: `Authorization: Bearer <access_token>` unless stated
otherwise.

## Auth (Phase 4)

| Verb | Path | Auth | What it does |
|---|---|---|---|
| POST | `/auth/register` | no | Creates a user + wallet at zero |
| POST | `/auth/login` | no | Returns an access + refresh token |
| POST | `/auth/refresh` | no (refresh token in body) | Rotates the refresh token; revokes the whole family if an old one is reused |
| GET | `/auth/me` | yes | Authenticated user's data |

## Wallet (Phase 5)

| Verb | Path | Auth | What it does |
|---|---|---|---|
| GET | `/wallet/balance` | yes | Current balance, in cents |
| GET | `/wallet/transactions` | yes | Ledger history, cursor-paginated |
| POST | `/wallet/deposit` | yes | Simulated deposit (no real gateway) |

## Roulette (Phase 5)

| Verb | Path | Auth | What it does |
|---|---|---|---|
| GET | `/roulette/fairness/current` | yes | Hash of the active server seed + client seed |
| POST | `/roulette/fairness/rotate` | yes | Reveals the previous seed, activates a new one |
| POST | `/roulette/rounds` | yes, + mandatory `Idempotency-Key` | Places one or more bets, resolves the round, and returns the result |
| GET | `/roulette/rounds` | yes | Round history, cursor-paginated |

## WebSocket (Phase 7)

| Path | Auth | What it emits |
|---|---|---|
| `/ws` | token in query string or subprotocol | `round.settled`, `balance.updated` — the same `data` shape as their Kafka counterparts |
