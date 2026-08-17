"""The real Redpanda outage test (`test_kafka_outage.py`) doesn't exercise
`relay_once`'s per-row failure branch nor `relay_loop`'s full-cycle
`except`: when the container comes back in time, `AIOKafkaProducer`
retries internally and the send never gets to raise up into our code.
Here the failure is forced with a fake producer, with no real network
involved, to test the backoff/retry path that actually is our
responsibility.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.events import relay as relay_module
from app.events.relay import relay_loop, relay_once
from app.models.outbox import OutboxEvent

pytestmark = pytest.mark.integration


async def test_relay_once_marks_failed_and_schedules_backoff_if_the_send_fails(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = OutboxEvent(
        topic="theclub.wallet.transactions.v1",
        key=str(uuid.uuid4()),
        payload={"event_type": "wallet.transaction"},
    )
    db_session.add(row)
    await db_session.commit()

    producer = AsyncMock()
    producer.send_and_wait.side_effect = RuntimeError("boom: broker unreachable")

    published = await relay_once(session_factory, producer)
    assert published == 1

    # Queried from a fresh session: `db_session` already has `row` in its
    # identity map with `attempts=0`, and `expire_on_commit=False` doesn't
    # invalidate that, so reusing it would return the in-memory object, not
    # what the relay (with its own session) actually wrote.
    async with session_factory() as verify_session:
        refreshed = (
            await verify_session.execute(select(OutboxEvent).where(OutboxEvent.id == row.id))
        ).scalar_one()
    assert refreshed.published_at is None
    assert refreshed.attempts == 1
    assert "boom" in (refreshed.last_error or "")
    assert refreshed.next_attempt_at is not None
    assert refreshed.next_attempt_at > datetime.now(UTC)


async def test_relay_once_does_not_retry_a_row_scheduled_in_the_future(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    row = OutboxEvent(
        topic="theclub.wallet.transactions.v1",
        key=str(uuid.uuid4()),
        payload={"event_type": "wallet.transaction"},
        attempts=1,
        next_attempt_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    db_session.add(row)
    await db_session.commit()

    producer = AsyncMock()
    published = await relay_once(session_factory, producer)

    assert published == 0
    producer.send_and_wait.assert_not_called()


async def test_relay_loop_does_not_die_if_a_cycle_fails_entirely(
    monkeypatch: pytest.MonkeyPatch,
    integration_settings: Settings,
) -> None:
    calls = 0

    async def _broken_relay_once(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("full-cycle failure, not a single-row one")

    monkeypatch.setattr(relay_module, "relay_once", _broken_relay_once)
    settings = integration_settings.model_copy(update={"OUTBOX_POLL_INTERVAL_MS": 50})

    task = asyncio.create_task(relay_loop(AsyncMock(), AsyncMock(), settings))
    await asyncio.sleep(0.2)
    assert not task.done(), "the loop must not die from a cycle that fails entirely"
    assert calls >= 2, "must keep retrying after the failure, not get stuck"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
