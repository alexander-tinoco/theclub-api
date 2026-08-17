"""Phase 6's DoD, part 2: if Redpanda goes down, the game keeps working
(the outbox accumulates, without losing money) and once it's back the
relay drains what's pending with no manual intervention.

Unlike the rest of the suite, this test controls the real Redpanda
container with `docker compose stop/start` — no mock involved, because
what we want to test is exactly the behavior under a real network/broker
outage, not a simulated exception. Uses a `try/finally` to guarantee
Redpanda is back up even if an assertion fails partway through the test.
"""

import asyncio
import subprocess
import uuid

import pytest
from aiokafka import AIOKafkaProducer
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
    # Fixed args defined in this file, not user input: the command and
    # REDPANDA_SERVICE are hardcoded in the module.
    subprocess.run(  # noqa: S603
        ["docker", "compose", *args],  # noqa: S607
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        timeout=60,
    )


async def _wait_for_redpanda_ready(bootstrap_servers: str, timeout_seconds: float = 30.0) -> None:
    """`docker compose start` returns control before the broker finishes
    accepting connections — you have to actively wait before moving on, or
    the rest of the test (and the next one that runs against the same
    Redpanda) can run into a broker that's technically already listening on
    the port but hasn't finished electing a leader for every partition yet.

    Testing against Postgres here (which was never stopped) wouldn't work:
    it has to attempt a real Kafka `bootstrap()`, not some other dependency
    that was up the whole time.
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_error: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
        try:
            await producer.start()
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1)
        finally:
            await producer.stop()
    raise TimeoutError(f"Redpanda didn't come back in time: {last_error}")


async def test_a_bet_survives_kafka_being_down_and_the_relay_drains_it_once_back(
    integration_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the plan's real scenario: Redpanda is up when the app
    starts (the producer connects fine) and goes down *midway* through a
    round of bets — not before the app exists. This is on purpose:
    `AIOKafkaProducer.start()` doesn't tolerate an unreachable broker at
    startup (it raises and brings down the whole lifespan), so stopping
    Redpanda *before* creating the app would test a different scenario
    (a startup failure) that isn't what this phase's DoD describes nor
    what the current design covers — noted as a known limitation, not
    part of this test.
    """
    app = create_app(integration_settings)
    try:
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                email = f"{uuid.uuid4()}@example.com"
                register = await client.post(
                    "/api/v1/auth/register",
                    json={"email": email, "password": "a-long-password"},
                )
                headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

                _compose("stop", REDPANDA_SERVICE)

                # Nothing is asserted about /ready here on purpose: the
                # Kafka check (`partitions_for`) uses cluster metadata
                # already cached by the producer, so right after stopping
                # the container it can keep returning "ok" until that
                # cache expires — not a reliable test of an immediate
                # outage. What is deterministic, and what matters for the
                # DoD, is that betting keeps working and the outbox
                # accumulates without publishing.
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

                # The spin's outcome is random (win or lose), so the only
                # deterministic thing is that the stake moved: the balance
                # is no longer the untouched deposit.
                balance = await client.get("/api/v1/wallet/balance", headers=headers)
                assert balance.json()["balance_minor"] != 100_000

            # Gives the relay time to attempt a publish and fail at least
            # once (confirms retrying against a downed broker doesn't blow
            # up the background task or block the app).
            await asyncio.sleep(1.5)

            async with session_factory() as session:
                rows = (await session.execute(select(OutboxEvent))).scalars().all()
            assert len(rows) >= 3
            assert all(row.published_at is None for row in rows)

            _compose("start", REDPANDA_SERVICE)
            await _wait_for_redpanda_ready(integration_settings.KAFKA_BOOTSTRAP_SERVERS)

            deadline = asyncio.get_event_loop().time() + 30
            all_published = False
            while asyncio.get_event_loop().time() < deadline:
                async with session_factory() as session:
                    rows = (await session.execute(select(OutboxEvent))).scalars().all()
                if rows and all(row.published_at is not None for row in rows):
                    all_published = True
                    break
                await asyncio.sleep(1)

            assert all_published, "the relay didn't drain the outbox after Redpanda came back"
    finally:
        _compose("start", REDPANDA_SERVICE)
