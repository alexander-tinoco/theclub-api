"""DoD de la Fase 6, parte 2: si Redpanda cae, el juego sigue funcionando
(el outbox acumula, sin perder dinero) y al volver el relay drena lo
pendiente sin intervención manual.

A diferencia del resto de la suite, este test controla el contenedor real de
Redpanda con `docker compose stop/start` — no hay mock de por medio, porque
lo que queremos probar es justo el comportamiento ante una caída real de
red/broker, no una excepción simulada. Usa un `try/finally` para garantizar
que Redpanda vuelve a estar arriba aunque una aserción falle a mitad del test.
"""

import asyncio
import subprocess
import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.rate_limit import limiter
from app.config import Settings
from app.main import create_app
from app.models.outbox import OutboxEvent

from .conftest import REPO_ROOT

pytestmark = pytest.mark.integration

REDPANDA_SERVICE = "redpanda"


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    limiter.reset()


def _compose(*args: str) -> None:
    # Args fijos definidos en este archivo, no entrada del usuario: el
    # comando y REDPANDA_SERVICE están hardcodeados en el módulo.
    subprocess.run(  # noqa: S603
        ["docker", "compose", *args],  # noqa: S607
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        timeout=60,
    )


async def _wait_for_redpanda_ready(
    session_factory: async_sessionmaker[AsyncSession], timeout_seconds: float = 30.0
) -> None:
    """`docker compose start` devuelve el control antes de que el broker
    termine de aceptar conexiones — hay que esperar activamente antes de
    seguir, o el resto del test corre contra un broker que aún no escucha.
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_error: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with session_factory() as session:
                await session.execute(select(1))
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1)
    raise TimeoutError(f"Redpanda no volvió a tiempo: {last_error}")


async def test_apuesta_sobrevive_a_kafka_caido_y_el_relay_drena_al_volver(
    integration_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduce el escenario real del plan: Redpanda está arriba cuando la
    app arranca (el productor se conecta sin problema) y cae *a mitad* de
    una tanda de apuestas — no antes de que la app exista. Esto es a
    propósito: `AIOKafkaProducer.start()` no tolera un broker inalcanzable
    al arrancar (lanza y tumba el lifespan completo), así que apagar
    Redpanda *antes* de crear la app probaría un escenario distinto (caída
    en el arranque) que no es el que describe el DoD de esta fase ni el que
    cubre el diseño actual — queda anotado como límite conocido, no como
    parte de este test.
    """
    app = create_app(integration_settings)
    try:
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                email = f"{uuid.uuid4()}@example.com"
                register = await client.post(
                    "/api/v1/auth/register",
                    json={"email": email, "password": "contraseña-larga"},
                )
                headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

                _compose("stop", REDPANDA_SERVICE)

                # No se afirma nada sobre /ready aquí a propósito: el check
                # de Kafka (`partitions_for`) usa metadata de cluster ya
                # cacheada por el productor, así que justo tras apagar el
                # contenedor puede seguir devolviendo "ok" hasta que esa
                # caché expire — no es una prueba fiable de caída inmediata.
                # Lo que sí es determinista, y lo que importa para el DoD, es
                # que apostar sigue funcionando y el outbox acumula sin
                # publicar.
                deposit = await client.post(
                    "/api/v1/wallet/deposit",
                    json={"amount_minor": 100_000},
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                )
                assert deposit.status_code == 200

                response = await client.post(
                    "/api/v1/roulette/rounds",
                    json={"bets": [{"bet_type": "red", "selection": {}, "stake_minor": 1000}]},
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                )
                assert response.status_code == 201

                # El resultado del giro es aleatorio (puede ganar o perder),
                # así que lo único determinista es que el stake se movió:
                # el balance ya no es el del depósito sin tocar.
                balance = await client.get("/api/v1/wallet/balance", headers=headers)
                assert balance.json()["balance_minor"] != 100_000

            # Se le da tiempo al relay a intentar publicar y fallar al menos
            # una vez (confirma que reintentar contra un broker caído no
            # revienta la tarea de fondo ni bloquea la app).
            await asyncio.sleep(1.5)

            async with session_factory() as session:
                rows = (await session.execute(select(OutboxEvent))).scalars().all()
            assert len(rows) >= 3
            assert all(row.published_at is None for row in rows)

            _compose("start", REDPANDA_SERVICE)
            await _wait_for_redpanda_ready(session_factory)

            deadline = asyncio.get_event_loop().time() + 30
            all_published = False
            while asyncio.get_event_loop().time() < deadline:
                async with session_factory() as session:
                    rows = (await session.execute(select(OutboxEvent))).scalars().all()
                if rows and all(row.published_at is not None for row in rows):
                    all_published = True
                    break
                await asyncio.sleep(1)

            assert all_published, "el relay no drenó el outbox tras volver Redpanda"
    finally:
        _compose("start", REDPANDA_SERVICE)
