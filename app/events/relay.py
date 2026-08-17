"""Outbox relay: drains the `outbox` table to Kafka in the background.

Doesn't publish in real time — it polls every `OUTBOX_POLL_INTERVAL_MS`.
It's the consumer side of the outbox pattern: the use cases (Phase 5) only
write to the table, within their own transaction; this process, separately,
is the one that actually talks to Kafka.
"""

import asyncio
import json
import logging

from aiokafka import AIOKafkaProducer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.repositories.outbox import OutboxRepository

logger = logging.getLogger(__name__)

RELAY_BATCH_SIZE = 50


async def relay_once(
    session_factory: async_sessionmaker[AsyncSession],
    producer: AIOKafkaProducer,
    *,
    batch_size: int = RELAY_BATCH_SIZE,
) -> int:
    """Publishes one batch. Returns how many rows it had (0 = nothing pending).

    The `try/except` is per row, inside the loop, on purpose: if a row
    failed and the exception were left to propagate, the entire transaction
    would roll back — including the `mark_published` of rows from the same
    batch that did publish successfully, and those would get resent as
    duplicates on the next cycle.
    """
    async with session_factory() as session, session.begin():
        repo = OutboxRepository(session)
        rows = await repo.lock_unpublished_batch(limit=batch_size)
        if not rows:
            return 0

        for row in rows:
            try:
                await producer.send_and_wait(
                    row.topic,
                    key=row.key.encode(),
                    value=json.dumps(row.payload).encode(),
                )
            except Exception as exc:  # any network/broker failure is handled the same way
                logger.warning("Could not publish event %s to %s: %s", row.id, row.topic, exc)
                await repo.mark_failed(row.id, attempts=row.attempts + 1, error=str(exc))
            else:
                await repo.mark_published(row.id)

        return len(rows)


async def relay_loop(
    session_factory: async_sessionmaker[AsyncSession],
    producer: AIOKafkaProducer,
    settings: Settings,
) -> None:
    """Runs forever until the task is cancelled, at lifespan shutdown. A
    cycle that fails entirely (e.g. Kafka down) must not kill the
    background task — it's logged and retried on the next poll.
    """
    poll_interval_seconds = settings.OUTBOX_POLL_INTERVAL_MS / 1000
    while True:
        try:
            published = await relay_once(session_factory, producer)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("The outbox relay failed on a full cycle")
            published = 0

        if published == 0:
            await asyncio.sleep(poll_interval_seconds)
