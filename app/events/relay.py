"""Relay del outbox: drena la tabla `outbox` hacia Kafka en background.

No publica en tiempo real — sondea cada `OUTBOX_POLL_INTERVAL_MS`. Es el lado
consumidor del patrón outbox: los casos de uso (Fase 5) solo escriben en la
tabla, dentro de su propia transacción; este proceso, aparte, es quien de
verdad habla con Kafka.
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
    """Publica un lote. Devuelve cuántas filas tenía (0 = nada pendiente).

    El `try/except` es por fila, dentro del bucle, a propósito: si una fila
    falla y se dejara propagar la excepción, se revertiría la transacción
    entera — incluido el `mark_published` de las filas del mismo lote que sí
    se publicaron, y esas se reenviarían por duplicado en el siguiente ciclo.
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
            except Exception as exc:  # cualquier fallo de red/broker se trata igual
                logger.warning("No se pudo publicar el evento %s en %s: %s", row.id, row.topic, exc)
                await repo.mark_failed(row.id, attempts=row.attempts + 1, error=str(exc))
            else:
                await repo.mark_published(row.id)

        return len(rows)


async def relay_loop(
    session_factory: async_sessionmaker[AsyncSession],
    producer: AIOKafkaProducer,
    settings: Settings,
) -> None:
    """Corre para siempre hasta que se cancele la tarea, en el shutdown del
    lifespan. Un ciclo que falla por completo (p. ej. Kafka caído) no debe
    matar la tarea de fondo — se registra y se reintenta en el siguiente poll.
    """
    poll_interval_seconds = settings.OUTBOX_POLL_INTERVAL_MS / 1000
    while True:
        try:
            published = await relay_once(session_factory, producer)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("El relay del outbox falló en un ciclo completo")
            published = 0

        if published == 0:
            await asyncio.sleep(poll_interval_seconds)
