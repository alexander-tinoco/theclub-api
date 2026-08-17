"""La tabla `outbox` acumula una fila por evento publicado y el relay nunca
las borra — sin este mecanismo aparte crecería sin límite aunque Kafka jamás
falle. `purge_once`/`purge_loop` son el equivalente de limpieza a
`relay_once`/`relay_loop`: mismo patrón de "un ciclo que falla no mata la
tarea de fondo", pero sobre borrado en vez de publicación.
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


async def test_purge_once_borra_solo_lo_publicado_hace_mas_de_retention(
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


async def test_purge_once_no_hace_nada_si_no_hay_filas_vencidas(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    db_session.add(_row(published_at=datetime.now(UTC)))
    await db_session.commit()

    deleted = await purge_once(session_factory, retention_hours=168)
    assert deleted == 0


async def test_purge_loop_no_muere_si_un_ciclo_falla_por_completo(
    monkeypatch: pytest.MonkeyPatch,
    integration_settings: Settings,
) -> None:
    calls = 0

    async def _broken_purge_once(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("fallo de ciclo completo, no de una fila")

    monkeypatch.setattr(outbox_cleanup_module, "purge_once", _broken_purge_once)
    settings = integration_settings.model_copy(update={"OUTBOX_CLEANUP_INTERVAL_S": 60})

    task = asyncio.create_task(purge_loop(AsyncMock(), settings))
    await asyncio.sleep(0.1)
    assert not task.done(), "el loop no debe morir por un ciclo que falla por completo"
    assert calls == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
