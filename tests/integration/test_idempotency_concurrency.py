"""El pedido explícito de esta fase: cerrar del todo la carrera de
idempotencia. N peticiones verdaderamente concurrentes (mismo `asyncio.gather`,
no secuenciales) con la misma Idempotency-Key nunca ejecutan el negocio más
de una vez -- ni el débito, ni la ronda, se duplican.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.config import Settings
from app.main import create_app
from app.models.ledger import LedgerEntry
from app.models.round import Round
from app.repositories.users import UserRepository
from app.repositories.wallets import WalletRepository

pytestmark = pytest.mark.integration

CONCURRENT_ATTEMPTS = 10


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


async def test_rondas_concurrentes_identicas_solo_ejecutan_el_negocio_una_vez(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    email = f"{uuid.uuid4()}@example.com"
    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "contraseña-larga"}
    )
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}
    await client.post(
        "/api/v1/wallet/deposit",
        json={"amount_minor": 100_000},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    key = str(uuid.uuid4())
    payload = {"bets": [{"bet_type": "red", "selection": {}, "stake_minor": 1000}]}

    responses = await asyncio.gather(
        *(
            client.post(
                "/api/v1/roulette/rounds",
                json=payload,
                headers={**headers, "Idempotency-Key": key},
            )
            for _ in range(CONCURRENT_ATTEMPTS)
        )
    )

    assert all(r.status_code in (201, 409) for r in responses)
    successful_bodies = [r.json() for r in responses if r.status_code == 201]
    assert len(successful_bodies) >= 1
    # Todas las que "ganaron" ven exactamente la misma respuesta -- son la
    # misma ejecución cacheada, no ejecuciones distintas que casualmente
    # coincidieron.
    assert all(body == successful_bodies[0] for body in successful_bodies)

    user = await UserRepository(db_session).get_by_email(email)
    assert user is not None
    wallet = await WalletRepository(db_session).get_by_user_id(user.id)
    assert wallet is not None

    rounds = (
        (await db_session.execute(select(Round).where(Round.user_id == user.id))).scalars().all()
    )
    assert len(rounds) == 1, (
        "10 peticiones concurrentes con la misma clave crearon más de una ronda"
    )

    stake_entries = (
        (
            await db_session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.wallet_id == wallet.id, LedgerEntry.kind == "bet_stake"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(stake_entries) == 1, "el stake se debitó más de una vez bajo concurrencia"

    balance = await client.get("/api/v1/wallet/balance", headers=headers)
    round_body = successful_bodies[0]
    expected_balance = 100_000 - round_body["total_stake_minor"] + round_body["total_payout_minor"]
    assert balance.json()["balance_minor"] == expected_balance


async def test_depositos_concurrentes_identicos_solo_acreditan_una_vez(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    email = f"{uuid.uuid4()}@example.com"
    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "contraseña-larga"}
    )
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

    key = str(uuid.uuid4())
    payload = {"amount_minor": 5000}

    responses = await asyncio.gather(
        *(
            client.post(
                "/api/v1/wallet/deposit", json=payload, headers={**headers, "Idempotency-Key": key}
            )
            for _ in range(CONCURRENT_ATTEMPTS)
        )
    )

    assert all(r.status_code in (200, 409) for r in responses)

    balance = await client.get("/api/v1/wallet/balance", headers=headers)
    assert balance.json()["balance_minor"] == 5000
