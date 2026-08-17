"""El test de la caída real de Redpanda (`test_kafka_outage.py`) no ejercita
la rama de fallo por fila de `relay_once` ni el `except` de ciclo completo de
`relay_loop`: cuando el contenedor vuelve a tiempo, el `AIOKafkaProducer`
reintenta internamente y el envío nunca llega a lanzar hasta nuestro código.
Aquí se fuerza el fallo con un productor falso, sin red real de por medio,
para probar el camino de backoff/reintento que sí es responsabilidad nuestra.
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


async def test_relay_once_marca_failed_y_agenda_backoff_si_falla_el_envio(
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
    producer.send_and_wait.side_effect = RuntimeError("boom: broker inalcanzable")

    published = await relay_once(session_factory, producer)
    assert published == 1

    # Consulta desde una sesión nueva: `db_session` ya tiene `row` en su mapa
    # de identidad con `attempts=0` y `expire_on_commit=False` no lo invalida,
    # así que reutilizarla devolvería el objeto en memoria, no lo que el
    # relay (con su propia sesión) de verdad escribió.
    async with session_factory() as verify_session:
        refreshed = (
            await verify_session.execute(select(OutboxEvent).where(OutboxEvent.id == row.id))
        ).scalar_one()
    assert refreshed.published_at is None
    assert refreshed.attempts == 1
    assert "boom" in (refreshed.last_error or "")
    assert refreshed.next_attempt_at is not None
    assert refreshed.next_attempt_at > datetime.now(UTC)


async def test_relay_once_no_reintenta_una_fila_agendada_en_el_futuro(
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


async def test_relay_loop_no_muere_si_un_ciclo_falla_por_completo(
    monkeypatch: pytest.MonkeyPatch,
    integration_settings: Settings,
) -> None:
    calls = 0

    async def _broken_relay_once(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("fallo de ciclo completo, no de una fila")

    monkeypatch.setattr(relay_module, "relay_once", _broken_relay_once)
    settings = integration_settings.model_copy(update={"OUTBOX_POLL_INTERVAL_MS": 50})

    task = asyncio.create_task(relay_loop(AsyncMock(), AsyncMock(), settings))
    await asyncio.sleep(0.2)
    assert not task.done(), "el loop no debe morir por un ciclo que falla por completo"
    assert calls >= 2, "debe seguir reintentando tras el fallo, no quedarse atascado"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
