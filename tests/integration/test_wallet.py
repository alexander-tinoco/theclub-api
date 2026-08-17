"""Balance, paginated history, simulated deposit — with its own idempotency."""

import uuid
from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.rate_limit import limiter
from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    limiter.reset()


@pytest.fixture
async def client(integration_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _register(client: AsyncClient) -> dict[str, str]:
    email = f"{uuid.uuid4()}@example.com"
    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "a-long-password"}
    )
    return {"Authorization": f"Bearer {register.json()['access_token']}"}


async def test_initial_balance_is_zero(client: AsyncClient) -> None:
    headers = await _register(client)

    response = await client.get("/api/v1/wallet/balance", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"balance_minor": 0, "currency": "EUR"}


async def test_deposit_credits_the_balance(client: AsyncClient) -> None:
    headers = await _register(client)

    response = await client.post(
        "/api/v1/wallet/deposit",
        json={"amount_minor": 5000},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 200
    assert response.json() == {"balance_minor": 5000, "currency": "EUR"}


async def test_deposit_retry_does_not_duplicate(client: AsyncClient) -> None:
    headers = await _register(client)
    key = str(uuid.uuid4())

    first = await client.post(
        "/api/v1/wallet/deposit",
        json={"amount_minor": 5000},
        headers={**headers, "Idempotency-Key": key},
    )
    second = await client.post(
        "/api/v1/wallet/deposit",
        json={"amount_minor": 5000},
        headers={**headers, "Idempotency-Key": key},
    )

    assert first.json() == second.json() == {"balance_minor": 5000, "currency": "EUR"}


async def test_deposit_invalid_amount(client: AsyncClient) -> None:
    headers = await _register(client)

    response = await client.post(
        "/api/v1/wallet/deposit",
        json={"amount_minor": 0},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_transactions_paginated_by_cursor(client: AsyncClient) -> None:
    headers = await _register(client)
    for i in range(3):
        await client.post(
            "/api/v1/wallet/deposit",
            json={"amount_minor": 100 * (i + 1)},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

    page1 = await client.get("/api/v1/wallet/transactions?limit=2", headers=headers)
    assert page1.status_code == 200
    assert len(page1.json()["items"]) == 2
    assert page1.json()["next_cursor"] is not None

    page2 = await client.get(
        f"/api/v1/wallet/transactions?limit=2&cursor={page1.json()['next_cursor']}",
        headers=headers,
    )
    assert len(page2.json()["items"]) == 1
    assert page2.json()["next_cursor"] is None


async def test_deposit_amount_above_the_cap_gets_rejected(client: AsyncClient) -> None:
    headers = await _register(client)

    response = await client.post(
        "/api/v1/wallet/deposit",
        json={"amount_minor": 10_000_001},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_deposit_has_a_rate_limit(client: AsyncClient) -> None:
    headers = await _register(client)

    responses = [
        await client.post(
            "/api/v1/wallet/deposit",
            json={"amount_minor": 100},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        for _ in range(31)
    ]

    assert responses[-1].status_code == 429
