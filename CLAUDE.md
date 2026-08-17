# theclub-api — Claude Code working guide

## What this repo is

The Club's game engine (portfolio project): resolves roulette rounds, manages balances,
and publishes events to Kafka. The full plan, with context, decisions, and phases, lives in
[`plan/theclub-api-PLAN.md`](plan/theclub-api-PLAN.md) — read it before touching anything if
you need context on why a piece exists.

## Role-based workflow (no multi-agents)

Each phase of the plan is worked through by requesting one role at a time. Don't mix roles
within the same request unless the user explicitly asks for it:

| Role | Requested with | Delivers | Doesn't do |
|---|---|---|---|
| **Architect** | "Design phase N" | File structure, signatures, table/event schema, decisions and trade-offs | Doesn't write implementation |
| **Implementer** | "Implement X" | Production code for a scoped module | Doesn't invent scope, doesn't write tests unless merged with Tester |
| **Tester** | "Write the tests for X" | Unit, property-based, integration; edge and failure cases | Doesn't modify production code to make tests pass |
| **Reviewer** | "Review phase N" or `/code-review` | Bugs, race conditions, security gaps, simplifications | Doesn't apply changes unless asked |
| **Integrator** | "Close phase N" | Alembic migration, contracts/OpenAPI, README, commit | Doesn't start the next phase |

Order within a phase: Architect → Implementer → Tester → Reviewer → Integrator.
Don't move to the next phase until the current one meets its *Definition of Done* (defined
in the plan). If something reveals that a previous decision was wrong, update
`plan/theclub-api-PLAN.md` **before** continuing to write code.

## Commit rules

- **Never** add Claude co-authorship or the `Co-Authored-By` trailer — commits go
  under the user's name only.
- **Never** commit without the user explicitly asking for it.
- After every commit, explain in the chat, in prose (not just the diff):
  - **What** files were touched and **what each one represents**.
  - **Why** those pieces exist — the motivation or problem they solve.
  - **How** they work — the mechanism, not just a paraphrase of the function name.
  - This explanation is mandatory even if the commit looks small.

## Project conventions

- Money: always `BIGINT` in minor units (cents), never `float`.
- `app/domain/` is pure core: no IO, no SQLAlchemy, no FastAPI. If a file there
  needs to import from `app/models` or `app/infra`, it's in the wrong package.
- Events to Kafka go through the outbox pattern — never dual-write (writing to Postgres
  and publishing to Kafka as separate steps without a shared transaction).
- Versioned topics `.v1`; changing an event schema requires bumping the version.
- `/health` = liveness, doesn't touch dependencies. `/ready` = readiness, runs checks
  registered in `app.state.readiness` (see `app/api/health.py`).

## Commands

```bash
make up          # brings up postgres + redpanda + console + api
make dev          # local api with reload, no image rebuild
make test         # all tests
make test-unit    # only the ones that don't require services running
make check         # lint + typecheck + test (what CI requires)
```

Default ports (parametrized in `.env` to avoid clashing with other local
projects): API `8010`, Redpanda console `8090`, Postgres `5432`, Kafka `19092`.
