# contracts/

This directory is `theclub-api`'s public contract toward the project's other two repos. If
you're in `theclub-web` or `theclub-data`, this is what you should read — no need to dig
into `app/`'s code.

- [`api-draft.md`](api-draft.md) — the REST and WebSocket endpoints the API will expose
  (draft; becomes a real generated `openapi.json` starting in Phase 5).
- [`events/`](events/) — the three events published to Kafka: their JSON Schema, a valid
  example of each, and the versioning, partitioning, and deduplication conventions in
  [`events/README.md`](events/README.md).

Hard rule: a change to an event schema that isn't purely additive requires a version bump
(see `events/README.md`). A published `.v1` schema is never rewritten with a different shape.
