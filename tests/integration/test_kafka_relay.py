"""Phase 6's DoD, part 1: betting produces the real events on their Kafka
topics (Redpanda), not just rows in the `outbox` table — the difference
from `test_outbox_contracts.py` (Phase 5) is that here it consumes from
the real broker, with an `AIOKafkaConsumer`, not from reading the database.
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
    """Topics are append-only and don't get cleaned up between test runs
    (unlike Postgres tables, via `_clean_tables`). With
    `auto_offset_reset="earliest"` a new consumer group reads the topic's
    ENTIRE history, including messages from previous runs — that's why any
    message that isn't for this `user_id` gets discarded.
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


async def test_betting_publishes_the_3_events_to_real_kafka(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            email = f"{uuid.uuid4()}@example.com"
            register = await client.post(
                "/api/v1/auth/register", json={"email": email, "password": "a-long-password"}
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
            # The outcome is random: if the bet wins, `place_bet` enqueues
            # an extra wallet.transaction event (`bet_payout`) on top of
            # `bet_stake` — don't assume a fixed event count, compute it
            # from what actually happened.
            won = response.json()["bets"][0]["won"]

            ready = await client.get("/ready")
            assert ready.json()["checks"]["kafka"] == "ok"

        # The relay polls every OUTBOX_POLL_INTERVAL_MS (500ms by default):
        # give it time before closing the lifespan (which cancels the task).
        await asyncio.sleep(2)

    # deposit + bet.placed + round.settled + bet_stake, and bet_payout if it won.
    expected_events = 4 + (1 if won else 0)
    expected_wallet_kinds = {"deposit", "bet_stake"} | ({"bet_payout"} if won else set())

    messages = await _consume(
        EXPECTED_TOPICS,
        bootstrap_servers=integration_settings.KAFKA_BOOTSTRAP_SERVERS,
        user_id=user_id,
        expected=expected_events,
    )

    assert len(messages) >= expected_events
    seen_by_topic: dict[str, list[dict[str, Any]]] = {topic: [] for topic in EXPECTED_TOPICS}
    for msg in messages:
        payload = json.loads(msg.value)
        seen_by_topic[msg.topic].append(payload)

    assert len(seen_by_topic["theclub.bets.placed.v1"]) == 1
    assert seen_by_topic["theclub.bets.placed.v1"][0]["event_type"] == "bet.placed"

    assert len(seen_by_topic["theclub.rounds.settled.v1"]) == 1
    assert seen_by_topic["theclub.rounds.settled.v1"][0]["event_type"] == "round.settled"

    assert len(seen_by_topic["theclub.wallet.transactions.v1"]) == len(expected_wallet_kinds)
    assert {
        e["data"]["kind"] for e in seen_by_topic["theclub.wallet.transactions.v1"]
    } == expected_wallet_kinds
