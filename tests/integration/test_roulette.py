"""Phase 5's DoD: insufficient funds, malformed bet, table limits, a retry
with the same Idempotency-Key (same response, no double charge), and the
same key with a different body (409).
"""

import hashlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

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


async def _register_and_fund(
    client: AsyncClient, *, balance_minor: int = 100_000
) -> dict[str, str]:
    email = f"{uuid.uuid4()}@example.com"
    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "a-long-password"}
    )
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}
    if balance_minor > 0:
        await client.post(
            "/api/v1/wallet/deposit",
            json={"amount_minor": balance_minor},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
    return headers


def _bet(
    bet_type: str = "red", selection: dict[str, Any] | None = None, stake_minor: int = 1000
) -> dict[str, Any]:
    return {
        "bets": [{"bet_type": bet_type, "selection": selection or {}, "stake_minor": stake_minor}]
    }


async def test_place_bet_success(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["total_stake_minor"] == 1000
    assert 0 <= body["outcome"] <= 36
    assert body["balance_minor"] == 100_000 - 1000 + body["total_payout_minor"]


async def test_insufficient_funds(client: AsyncClient) -> None:
    headers = await _register_and_fund(client, balance_minor=0)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=1000),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 409


async def test_malformed_bet(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(bet_type="corner", selection={"numbers": [1, 5, 20, 36]}),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_stake_below_the_table_minimum(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=1),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_stake_above_the_table_maximum(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=1_000_000),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_zero_stake_is_rejected_already_at_request_validation(
    client: AsyncClient,
) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=0),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_retry_with_the_same_key_gets_the_same_response_with_no_double_charge(
    client: AsyncClient,
) -> None:
    headers = await _register_and_fund(client)
    key = str(uuid.uuid4())
    payload = _bet()

    first = await client.post(
        "/api/v1/roulette/rounds", json=payload, headers={**headers, "Idempotency-Key": key}
    )
    second = await client.post(
        "/api/v1/roulette/rounds", json=payload, headers={**headers, "Idempotency-Key": key}
    )

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()

    balance = await client.get("/api/v1/wallet/balance", headers=headers)
    expected = 100_000 - first.json()["total_stake_minor"] + first.json()["total_payout_minor"]
    assert balance.json()["balance_minor"] == expected


async def test_an_idempotency_key_that_is_too_long_gets_rejected(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(),
        headers={**headers, "Idempotency-Key": "k" * 201},
    )

    assert response.status_code == 422


async def test_same_key_different_body_returns_409(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)
    key = str(uuid.uuid4())

    await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=1000),
        headers={**headers, "Idempotency-Key": key},
    )
    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=2000),
        headers={**headers, "Idempotency-Key": key},
    )

    assert response.status_code == 409


async def test_fairness_current_and_rotate(client: AsyncClient) -> None:
    headers = await _register_and_fund(client, balance_minor=0)

    current = await client.get("/api/v1/roulette/fairness/current", headers=headers)
    assert current.status_code == 200
    assert len(current.json()["server_seed_hash"]) == 64  # sha256 in hex

    rotate = await client.post("/api/v1/roulette/fairness/rotate", headers=headers)
    assert rotate.status_code == 200
    body = rotate.json()
    revealed = bytes.fromhex(body["revealed_server_seed"])
    assert hashlib.sha256(revealed).hexdigest() == body["revealed_server_seed_hash"]

    new_current = await client.get("/api/v1/roulette/fairness/current", headers=headers)
    assert new_current.json()["server_seed_hash"] == body["new_server_seed_hash"]
    assert new_current.json()["nonce"] == 0


async def test_history_paginated_by_cursor(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)
    for _ in range(3):
        await client.post(
            "/api/v1/roulette/rounds",
            json=_bet(),
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

    page1 = await client.get("/api/v1/roulette/rounds?limit=2", headers=headers)
    assert page1.status_code == 200
    assert len(page1.json()["items"]) == 2
    assert page1.json()["next_cursor"] is not None

    page2 = await client.get(
        f"/api/v1/roulette/rounds?limit=2&cursor={page1.json()['next_cursor']}", headers=headers
    )
    assert len(page2.json()["items"]) == 1
    assert page2.json()["next_cursor"] is None

    seen_ids = {item["round_id"] for item in page1.json()["items"] + page2.json()["items"]}
    assert len(seen_ids) == 3  # no repeats or skips across pages


async def test_rounds_has_a_rate_limit(client: AsyncClient) -> None:
    headers = await _register_and_fund(client, balance_minor=1_000_000)

    responses = [
        await client.post(
            "/api/v1/roulette/rounds",
            json=_bet(stake_minor=100),
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        for _ in range(31)
    ]

    assert responses[-1].status_code == 429
