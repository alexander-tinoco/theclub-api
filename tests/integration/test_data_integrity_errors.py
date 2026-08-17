"""Los invariantes "todo usuario tiene wallet / seed pair activo" se sostienen
por construcción (register_user crea ambos), pero si alguna vez no fuera así
—un bug en otra parte, datos corruptos— esto debe fallar con una excepción
clara, no un AttributeError críptico ni (peor) un `assert` que `-O` descarta.
"""

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.roulette.table import BetType
from app.main import create_app
from app.models.wallet import Wallet
from app.repositories.users import UserRepository
from app.services import fairness as fairness_service
from app.services import roulette as roulette_service
from app.services import wallet as wallet_service
from app.services.exceptions import DataIntegrityError

pytestmark = pytest.mark.integration


async def test_get_balance_de_un_usuario_sin_wallet(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await wallet_service.get_balance(db_session, user_id=uuid.uuid4())


async def test_list_transactions_de_un_usuario_sin_wallet(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await wallet_service.list_transactions(
            db_session, user_id=uuid.uuid4(), cursor=None, limit=20
        )


async def test_deposit_de_un_usuario_sin_wallet(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await wallet_service.deposit(
            db_session,
            Settings(APP_ENV="test"),
            user_id=uuid.uuid4(),
            idempotency_key="x",
            amount_minor=100,
        )


async def test_get_current_seed_de_un_usuario_sin_seed_pair(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await fairness_service.get_current_seed(db_session, user_id=uuid.uuid4())


async def test_rotate_seed_de_un_usuario_sin_seed_pair(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await fairness_service.rotate_seed(db_session, user_id=uuid.uuid4())


async def test_place_bet_de_un_usuario_sin_wallet(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await roulette_service.place_bet(
            db_session,
            Settings(APP_ENV="test"),
            user_id=uuid.uuid4(),
            idempotency_key="x",
            bet_requests=[
                roulette_service.BetRequest(bet_type=BetType.RED, selection={}, stake_minor=1000)
            ],
        )


async def test_place_bet_de_un_usuario_con_wallet_pero_sin_seed_pair(
    db_session: AsyncSession,
) -> None:
    # Un usuario "corrupto": tiene wallet, pero nunca se le creó un seed pair
    # -- no debería poder pasar por register_user, pero si pasa por otra vía
    # (un bug, una migración de datos), esto no debe explotar en silencio.
    user = await UserRepository(db_session).create(
        email=f"{uuid.uuid4()}@example.com", password_hash="x"
    )
    db_session.add(Wallet(user_id=user.id, balance_minor=10_000))
    await db_session.commit()

    with pytest.raises(DataIntegrityError):
        await roulette_service.place_bet(
            db_session,
            Settings(APP_ENV="test"),
            user_id=user.id,
            idempotency_key="x",
            bet_requests=[
                roulette_service.BetRequest(bet_type=BetType.RED, selection={}, stake_minor=1000)
            ],
        )


async def test_el_handler_http_de_data_integrity_error_responde_500(
    integration_settings: Settings, db_session: AsyncSession
) -> None:
    """Los tests de arriba prueban que el servicio levanta la excepción; este
    prueba que el endpoint HTTP de verdad la traduce a 500 -- el handler
    registrado en app/api/errors.py nunca se había ejercitado hasta ahora."""
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            email = f"{uuid.uuid4()}@example.com"
            register = await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "contraseña-larga"},
            )
            headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

            user = await UserRepository(db_session).get_by_email(email)
            assert user is not None
            await db_session.execute(delete(Wallet).where(Wallet.user_id == user.id))
            await db_session.commit()

            response = await client.get("/api/v1/wallet/balance", headers=headers)

    assert response.status_code == 500
