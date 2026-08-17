"""Recuperación tras un proceso que murió a medio camino: una fila 'pending'
abandonada (más vieja que PENDING_RECLAIM_SECONDS) no bloquea reintentos para
siempre con un 409 perpetuo -- se reclama y el negocio se ejecuta de verdad.
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


async def test_una_fila_pending_abandonada_se_reclama(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    integration_settings: Settings,
) -> None:
    user = await UserRepository(db_session).create(
        email=f"{uuid.uuid4()}@example.com", password_hash="x"
    )
    await db_session.commit()

    key = "clave-abandonada"
    request_hash = hash_request_body(b'{"x": 1}')

    # Simula un proceso que reservó la clave y murió antes de terminar: una
    # fila 'pending' con created_at ya viejo, sin pasar por run_idempotent.
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
