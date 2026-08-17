"""The `outbox` table accumulates one row per published event and the
relay never deletes them — without this separate mechanism it would grow
without bound even if Kafka never fails. `purge_once`/`purge_loop` are the
cleanup equivalent of `relay_once`/`relay_loop`: same "a cycle that fails
doesn't kill the background task" pattern, but for deletion instead of
publishing.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.events import outbox_cleanup as outbox_cleanup_module
from app.events.outbox_cleanup import purge_loop, purge_once
from app.models.outbox import OutboxEvent

pytestmark = pytest.mark.integration


def _row(*, published_at: datetime | None) -> OutboxEvent:
    return OutboxEvent(
        topic="theclub.wallet.transactions.v1",
        key=str(uuid.uuid4()),
        payload={"event_type": "wallet.transaction"},
        published_at=published_at,
    )


async def test_purge_once_only_deletes_rows_published_more_than_retention_ago(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    old_published = _row(published_at=now - timedelta(hours=200))
    recent_published = _row(published_at=now - timedelta(hours=1))
    never_published = _row(published_at=None)
    db_session.add_all([old_published, recent_published, never_published])
    await db_session.commit()

    deleted = await purge_once(session_factory, retention_hours=168)
    assert deleted == 1

    async with session_factory() as verify_session:
        remaining_ids = set((await verify_session.execute(select(OutboxEvent.id))).scalars().all())
    assert old_published.id not in remaining_ids
    assert recent_published.id in remaining_ids
    assert never_published.id in remaining_ids


async def test_purge_once_does_nothing_if_no_rows_are_past_retention(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    db_session.add(_row(published_at=datetime.now(UTC)))
    await db_session.commit()

    deleted = await purge_once(session_factory, retention_hours=168)
    assert deleted == 0


async def test_purge_loop_does_not_die_if_a_cycle_fails_entirely(
    monkeypatch: pytest.MonkeyPatch,
    integration_settings: Settings,
) -> None:
    calls = 0

    async def _broken_purge_once(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("full-cycle failure, not a single-row one")

    monkeypatch.setattr(outbox_cleanup_module, "purge_once", _broken_purge_once)
    settings = integration_settings.model_copy(update={"OUTBOX_CLEANUP_INTERVAL_S": 60})

    task = asyncio.create_task(purge_loop(AsyncMock(), settings))
    await asyncio.sleep(0.1)
    assert not task.done(), "the loop must not die from a cycle that fails entirely"
    assert calls == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
