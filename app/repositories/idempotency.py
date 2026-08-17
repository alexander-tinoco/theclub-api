"""Acceso a datos de idempotency_keys.

`create_pending` es el mecanismo real: la UNIQUE(user_id, key) de la tabla es
quien decide, a nivel de Postgres, cuál de dos peticiones simultáneas con la
misma clave gana la reserva — ver `app/services/idempotency.py`.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyKey


class IdempotencyKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID, key: str) -> IdempotencyKey | None:
        result = await self._session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id, IdempotencyKey.key == key
            )
        )
        return result.scalar_one_or_none()

    async def create_pending(
        self, *, user_id: uuid.UUID, key: str, request_hash: str, expires_at: datetime
    ) -> IdempotencyKey:
        row = IdempotencyKey(
            user_id=user_id,
            key=key,
            request_hash=request_hash,
            status="pending",
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def mark_completed(self, row_id: uuid.UUID, *, response_body: dict[str, Any]) -> None:
        await self._session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == row_id)
            .values(status="completed", response_body=response_body)
        )

    async def delete(self, row_id: uuid.UUID) -> None:
        await self._session.execute(delete(IdempotencyKey).where(IdempotencyKey.id == row_id))
