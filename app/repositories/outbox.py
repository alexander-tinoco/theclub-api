"""Acceso a datos del outbox para el relay (Fase 6). El repositorio de
escritura (`enqueue_event`, en `app/events/outbox.py`) es distinto a
propósito: ese lo usa cualquier caso de uso que mueva negocio; este lo usa
solo el relay, que es quien lee, publica y marca.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxEvent

#: Backoff exponencial con tope: 2, 4, 8... hasta 60s. Evita que una fila que
#: falla se reintente en cada poll (cada 500ms por defecto) mientras Kafka
#: esté caído, sin dejar de reintentar indefinidamente.
BASE_BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 60


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_unpublished_batch(self, *, limit: int) -> list[OutboxEvent]:
        """Bloquea hasta `limit` filas pendientes y listas para reintentar.

        `FOR UPDATE SKIP LOCKED` es lo que hace seguro correr varias
        instancias del relay a la vez: si dos procesos compiten por el mismo
        lote, cada uno se queda con lo que consiguió bloquear y salta lo que
        el otro ya tomó, en vez de esperar o duplicar el envío.
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
