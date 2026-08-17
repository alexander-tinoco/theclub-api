"""Limpieza periódica del outbox. El relay (`relay.py`) publica y marca cada
fila, pero nunca las borra — sin este proceso aparte la tabla crecería para
siempre aunque Kafka nunca falle. Corre en su propio intervalo, mucho más
largo que el de sondeo del relay, porque no hay urgencia: una fila publicada
hace una hora no le hace daño a nadie por quedarse un rato más.
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
    """Borra un lote de filas publicadas más viejas que `retention_hours`.
    Devuelve cuántas borró (0 = nada que limpiar todavía).
    """
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    async with session_factory() as session, session.begin():
        deleted = await OutboxRepository(session).purge_published(older_than=cutoff)
    if deleted:
        logger.info(
            "Outbox: %d filas publicadas purgadas (retención %dh)", deleted, retention_hours
        )
    return deleted


async def purge_loop(session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    """Corre para siempre hasta que se cancele la tarea, en el shutdown del
    lifespan — igual que `relay_loop`, un ciclo que falla no debe matar la
    tarea de fondo.
    """
    interval_seconds = settings.OUTBOX_CLEANUP_INTERVAL_S
    while True:
        try:
            await purge_once(session_factory, retention_hours=settings.OUTBOX_RETENTION_HOURS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("La limpieza del outbox falló en un ciclo completo")
        await asyncio.sleep(interval_seconds)
