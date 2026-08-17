"""Data access to the outbox for the relay (Phase 6). The write-side
repository (`enqueue_event`, in `app/events/outbox.py`) is deliberately
separate: that one is used by any use case that moves business state; this
one is used only by the relay, which reads, publishes, and marks.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxEvent

#: Capped exponential backoff: 2, 4, 8... up to 60s. Stops a failing row
#: from being retried on every poll (every 500ms by default) while Kafka is
#: down, without ever giving up retrying.
BASE_BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 60


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_unpublished_batch(self, *, limit: int) -> list[OutboxEvent]:
        """Locks up to `limit` pending rows that are ready to be retried.

        `FOR UPDATE SKIP LOCKED` is what makes it safe to run several relay
        instances at once: if two processes compete for the same batch,
        each keeps whatever it managed to lock and skips what the other
        already took, instead of waiting or double-sending.
        """
        now = datetime.now(UTC)
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .where((OutboxEvent.next_attempt_at.is_(None)) | (OutboxEvent.next_attempt_at <= now))
            .order_by(OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_published(self, event_id: uuid.UUID) -> None:
        await self._session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(published_at=datetime.now(UTC))
        )

    async def mark_failed(self, event_id: uuid.UUID, *, attempts: int, error: str) -> None:
        backoff_seconds = min(BASE_BACKOFF_SECONDS * 2**attempts, MAX_BACKOFF_SECONDS)
        await self._session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(
                attempts=attempts,
                last_error=error[:500],
                next_attempt_at=datetime.now(UTC) + timedelta(seconds=backoff_seconds),
            )
        )

    async def purge_published(self, *, older_than: datetime) -> int:
        """Deletes already-published rows older than `older_than`. Without
        this the table grows forever even if the relay works perfectly —
        every published row just stays there. Unpublished rows are never
        touched, no matter their age: only `published_at IS NOT NULL` is a
        candidate.
        """
        result = await self._session.execute(
            delete(OutboxEvent)
            .where(OutboxEvent.published_at.is_not(None))
            .where(OutboxEvent.published_at < older_than)
        )
        # `execute()` on a DELETE returns a CursorResult at runtime (it
        # carries rowcount); SQLAlchemy's generic typing doesn't reflect that.
        return cast("CursorResult[Any]", result).rowcount
