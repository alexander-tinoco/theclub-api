# Events — conventions

## Versioning

`.v1` appears three times per event and all three must match: in the schema file name
(`bet-placed.v1.schema.json`), in the Kafka topic name (`theclub.bets.placed.v1`), and in
the envelope's `event_version` field. A change that breaks an existing consumer (renaming a
field, removing it, changing its type) requires bumping to `.v2` in all three places at
once — the `.v1` topic freezes and stops being written to, it's never rewritten with a new
shape. An additive change (a new optional field) doesn't require a version bump.

## Partitioning

All three topics are partitioned by `user_id` (found inside `data`, not in the envelope).
This preserves the event order for a given user within a partition — important because a
`round.settled` and a `wallet.transaction` from the same play must be read in the order
they happened.

## Deduplication

`event_id` is generated exactly once, when the row is inserted into the `outbox` table
(Phase 6), and doesn't change across retries. It's the key any consumer should use to
deduplicate — the outbox pattern guarantees *at-least-once*, not *exactly-once*, so an
event can arrive twice after a relay crash mid-publish.

`idempotency_key` is a different field: it's the HTTP `Idempotency-Key` of the request that
originated the event (Phase 5). It's used to *correlate* several events that came from the
same user request (in this directory's three examples they share the same value, because
they all originate from the same call to `POST /roulette/rounds`), not to deduplicate. It
can be `null` if the event doesn't originate from a direct HTTP request.

## No PII

Events carry no email or any personal data beyond `user_id` (UUID). If personal data ever
needs to go into the stream, that's a reason to bump the version and discuss it explicitly,
not to just add a field.
