"""The invariants "every user has a wallet / an active seed pair" hold by
construction (register_user creates both), but if that were ever not the
case — a bug elsewhere, corrupted data — this must fail with a clear
exception, not a cryptic AttributeError nor (worse) an `assert` that `-O`
strips out.
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


async def test_get_balance_for_a_user_with_no_wallet(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await wallet_service.get_balance(db_session, user_id=uuid.uuid4())


async def test_list_transactions_for_a_user_with_no_wallet(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await wallet_service.list_transactions(
            db_session, user_id=uuid.uuid4(), cursor=None, limit=20
        )


async def test_deposit_for_a_user_with_no_wallet(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await wallet_service.deposit(
            db_session,
            Settings(APP_ENV="test"),
            user_id=uuid.uuid4(),
            idempotency_key="x",
            amount_minor=100,
        )


async def test_get_current_seed_for_a_user_with_no_seed_pair(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await fairness_service.get_current_seed(db_session, user_id=uuid.uuid4())


async def test_rotate_seed_for_a_user_with_no_seed_pair(db_session: AsyncSession) -> None:
    with pytest.raises(DataIntegrityError):
        await fairness_service.rotate_seed(db_session, user_id=uuid.uuid4())


async def test_place_bet_for_a_user_with_no_wallet(db_session: AsyncSession) -> None:
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


async def test_place_bet_for_a_user_with_a_wallet_but_no_seed_pair(
    db_session: AsyncSession,
) -> None:
    # A "corrupted" user: has a wallet, but was never given a seed pair --
    # shouldn't be possible via register_user, but if it happens through
    # another path (a bug, a data migration), this must not blow up silently.
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


async def test_the_data_integrity_error_http_handler_responds_500(
    integration_settings: Settings, db_session: AsyncSession
) -> None:
    """The tests above prove the service raises the exception; this one
    proves the real HTTP endpoint translates it into a 500 -- the handler
    registered in app/api/errors.py had never been exercised until now."""
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            email = f"{uuid.uuid4()}@example.com"
            register = await client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "a-long-password"},
            )
            headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

            user = await UserRepository(db_session).get_by_email(email)
            assert user is not None
            await db_session.execute(delete(Wallet).where(Wallet.user_id == user.id))
            await db_session.commit()

            response = await client.get("/api/v1/wallet/balance", headers=headers)

    assert response.status_code == 500
