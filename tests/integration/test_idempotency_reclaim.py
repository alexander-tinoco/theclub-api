"""Recovery after a process that died halfway through: an abandoned
'pending' row (older than PENDING_RECLAIM_SECONDS) doesn't block retries
forever with a perpetual 409 -- it gets reclaimed and the business logic
actually runs.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.idempotency import IdempotencyKey
from app.repositories.idempotency import IdempotencyKeyRepository
from app.repositories.users import UserRepository
from app.services.idempotency import hash_request_body, run_idempotent

pytestmark = pytest.mark.integration


async def test_an_abandoned_pending_row_gets_reclaimed(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    integration_settings: Settings,
) -> None:
    user = await UserRepository(db_session).create(
        email=f"{uuid.uuid4()}@example.com", password_hash="x"
    )
    await db_session.commit()

    key = "abandoned-key"
    request_hash = hash_request_body(b'{"x": 1}')

    # Simulates a process that reserved the key and died before finishing:
    # a 'pending' row with an already-old created_at, never going through
    # run_idempotent.
    stale_row = await IdempotencyKeyRepository(db_session).create_pending(
        user_id=user.id,
        key=key,
        request_hash=request_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await db_session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.id == stale_row.id)
        .values(created_at=datetime.now(UTC) - timedelta(seconds=60))
    )
    await db_session.commit()

    executed = False

    async def work(session: AsyncSession) -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {"ok": True}

    result = await run_idempotent(
        session_factory,
        user_id=user.id,
        key=key,
        request_hash=request_hash,
        ttl_hours=integration_settings.IDEMPOTENCY_KEY_TTL_HOURS,
        work=work,
    )

    assert executed is True
    assert result == {"ok": True}
