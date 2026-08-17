"""DoD de la Fase 6, parte 1: apostar produce los eventos reales en sus
topics de Kafka (Redpanda), no solo filas en la tabla `outbox` — la
diferencia con `test_outbox_contracts.py` (Fase 5) es que aquí se consume
del broker real, con un `AIOKafkaConsumer`, no se lee la base de datos.
"""

import asyncio
import contextlib
import json
import uuid
from typing import Any

import pytest
from aiokafka import AIOKafkaConsumer, ConsumerRecord
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.rate_limit import limiter
from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration

EXPECTED_TOPICS = (
    "theclub.bets.placed.v1",
    "theclub.rounds.settled.v1",
    "theclub.wallet.transactions.v1",
)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    limiter.reset()


async def _consume(
    topics: tuple[str, ...], *, bootstrap_servers: str, user_id: str, expected: int
) -> list[ConsumerRecord]:
    """Los topics son append-only y no se limpian entre corridas de tests
    (a diferencia de las tablas de Postgres, vía `_clean_tables`). Con
    `auto_offset_reset="earliest"` un consumer group nuevo lee TODO el
    historial del topic, incluyendo mensajes de corridas anteriores — por
    eso se descarta cualquier mensaje que no sea de este `user_id`.
    """
    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=bootstrap_servers,
        auto_offset_reset="earliest",
        group_id=f"test-{uuid.uuid4()}",
    )
    await consumer.start()
    messages: list[ConsumerRecord] = []
    try:

        async def _collect() -> None:
            async for msg in consumer:
                payload = json.loads(msg.value)
                if payload.get("data", {}).get("user_id") != user_id:
                    continue
                messages.append(msg)
                if len(messages) >= expected:
                    return

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_collect(), timeout=15)
    finally:
        await consumer.stop()
    return messages


async def test_apostar_publica_los_3_eventos_en_kafka_real(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            email = f"{uuid.uuid4()}@example.com"
            register = await client.post(
                "/api/v1/auth/register", json={"email": email, "password": "contraseña-larga"}
            )
            headers = {"Authorization": f"Bearer {register.json()['access_token']}"}
            me = await client.get("/api/v1/auth/me", headers=headers)
            user_id = me.json()["id"]
            await client.post(
                "/api/v1/wallet/deposit",
                json={"amount_minor": 100_000},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            response = await client.post(
                "/api/v1/roulette/rounds",
                json={"bets": [{"bet_type": "red", "selection": {}, "stake_minor": 1000}]},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            assert response.status_code == 201

            ready = await client.get("/ready")
            assert ready.json()["checks"]["kafka"] == "ok"

        # El relay sondea cada OUTBOX_POLL_INTERVAL_MS (500ms por defecto):
        # se le da tiempo antes de cerrar el lifespan (que cancela la tarea).
        await asyncio.sleep(2)

    messages = await _consume(
        EXPECTED_TOPICS,
        bootstrap_servers=integration_settings.KAFKA_BOOTSTRAP_SERVERS,
        user_id=user_id,
        expected=4,
    )

    assert len(messages) >= 4  # deposit + bet.placed + round.settled + bet_stake
    seen_by_topic: dict[str, list[dict[str, Any]]] = {topic: [] for topic in EXPECTED_TOPICS}
    for msg in messages:
        payload = json.loads(msg.value)
        seen_by_topic[msg.topic].append(payload)

    assert len(seen_by_topic["theclub.bets.placed.v1"]) == 1
    assert seen_by_topic["theclub.bets.placed.v1"][0]["event_type"] == "bet.placed"

    assert len(seen_by_topic["theclub.rounds.settled.v1"]) == 1
    assert seen_by_topic["theclub.rounds.settled.v1"][0]["event_type"] == "round.settled"

    # deposit + bet_stake: dos wallet.transaction
    assert len(seen_by_topic["theclub.wallet.transactions.v1"]) == 2
    assert {e["data"]["kind"] for e in seen_by_topic["theclub.wallet.transactions.v1"]} == {
        "deposit",
        "bet_stake",
    }
