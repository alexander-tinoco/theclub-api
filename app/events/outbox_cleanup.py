"""Periodic outbox cleanup. The relay (`relay.py`) publishes and marks every
row, but never deletes them — without this separate process the table would
grow forever even if Kafka never fails. Runs on its own interval, much
longer than the relay's polling interval, because there's no urgency: a row
published an hour ago doesn't hurt anyone by sticking around a bit longer.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.repositories.outbox import OutboxRepository

logger = logging.getLogger(__name__)


async def purge_once(
    session_factory: async_sessionmaker[AsyncSession], *, retention_hours: int
) -> int:
    """Deletes a batch of published rows older than `retention_hours`.
    Returns how many it deleted (0 = nothing to clean up yet).
    """
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    async with session_factory() as session, session.begin():
        deleted = await OutboxRepository(session).purge_published(older_than=cutoff)
    if deleted:
        logger.info("Outbox: purged %d published rows (retention %dh)", deleted, retention_hours)
    return deleted


async def purge_loop(session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    """Runs forever until the task is cancelled, at lifespan shutdown — same
    as `relay_loop`, a cycle that fails must not kill the background task.
    """
    interval_seconds = settings.OUTBOX_CLEANUP_INTERVAL_S
    while True:
        try:
            await purge_once(session_factory, retention_hours=settings.OUTBOX_RETENTION_HOURS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Outbox cleanup failed on a full cycle")
        await asyncio.sleep(interval_seconds)
