"""DoD de la Fase 5: fondos insuficientes, apuesta malformada, límites de
mesa, reintento con la misma Idempotency-Key (misma respuesta, sin doble
cobro), y misma clave con cuerpo distinto (409).
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
        "/api/v1/auth/register", json={"email": email, "password": "contraseña-larga"}
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


async def test_place_bet_exitoso(client: AsyncClient) -> None:
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


async def test_fondos_insuficientes(client: AsyncClient) -> None:
    headers = await _register_and_fund(client, balance_minor=0)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=1000),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 409


async def test_apuesta_malformada(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(bet_type="corner", selection={"numbers": [1, 5, 20, 36]}),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_stake_por_debajo_del_minimo_de_mesa(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=1),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_stake_por_encima_del_maximo_de_mesa(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=1_000_000),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_stake_cero_lo_rechaza_ya_en_la_validacion_de_la_peticion(
    client: AsyncClient,
) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(stake_minor=0),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422


async def test_reintento_misma_clave_misma_respuesta_sin_doble_cobro(client: AsyncClient) -> None:
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


async def test_idempotency_key_demasiado_larga_se_rechaza(client: AsyncClient) -> None:
    headers = await _register_and_fund(client)

    response = await client.post(
        "/api/v1/roulette/rounds",
        json=_bet(),
        headers={**headers, "Idempotency-Key": "k" * 201},
    )

    assert response.status_code == 422


async def test_misma_clave_cuerpo_distinto_409(client: AsyncClient) -> None:
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


async def test_fairness_current_y_rotate(client: AsyncClient) -> None:
    headers = await _register_and_fund(client, balance_minor=0)

    current = await client.get("/api/v1/roulette/fairness/current", headers=headers)
    assert current.status_code == 200
    assert len(current.json()["server_seed_hash"]) == 64  # sha256 en hex

    rotate = await client.post("/api/v1/roulette/fairness/rotate", headers=headers)
    assert rotate.status_code == 200
    body = rotate.json()
    revealed = bytes.fromhex(body["revealed_server_seed"])
    assert hashlib.sha256(revealed).hexdigest() == body["revealed_server_seed_hash"]

    new_current = await client.get("/api/v1/roulette/fairness/current", headers=headers)
    assert new_current.json()["server_seed_hash"] == body["new_server_seed_hash"]
    assert new_current.json()["nonce"] == 0


async def test_historial_paginado_por_cursor(client: AsyncClient) -> None:
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
    assert len(seen_ids) == 3  # sin repetidos ni saltados entre páginas


async def test_rounds_tiene_rate_limit(client: AsyncClient) -> None:
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
