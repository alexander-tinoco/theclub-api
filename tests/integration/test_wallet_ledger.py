"""Los dos invariantes de dinero del plan, contra Postgres real:

1. El balance del wallet siempre es igual a la suma del ledger.
2. Débitos concurrentes sobre el mismo wallet nunca lo dejan en negativo.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.money import Money
from app.infra.db import create_engine as create_app_engine
from app.infra.db import create_session_factory, unit_of_work
from app.models.ledger import LedgerEntry
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.ledger import LedgerRepository
from app.repositories.wallets import InsufficientFundsError, WalletRepository

pytestmark = pytest.mark.integration


async def _create_user_with_wallet(session: AsyncSession, *, balance_minor: int = 0) -> Wallet:
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="not-a-real-hash")
    session.add(user)
    await session.flush()
    wallet = Wallet(user_id=user.id, balance_minor=balance_minor)
    session.add(wallet)
    await session.flush()
    return wallet


async def test_ledger_balance_invariant(db_session: AsyncSession) -> None:
    wallet = await _create_user_with_wallet(db_session)
    wallets = WalletRepository(db_session)
    ledger = LedgerRepository(db_session)

    balance = await wallets.credit(wallet.id, Money(10_000))
    await ledger.append(
        wallet_id=wallet.id, amount=Money(10_000), balance_after_minor=balance, kind="deposit"
    )

    balance = await wallets.debit(wallet.id, Money(500))
    await ledger.append(
        wallet_id=wallet.id, amount=Money(-500), balance_after_minor=balance, kind="bet_stake"
    )

    balance = await wallets.credit(wallet.id, Money(1_800))
    await ledger.append(
        wallet_id=wallet.id, amount=Money(1_800), balance_after_minor=balance, kind="bet_payout"
    )

    await db_session.commit()

    wallet_balance = (
        await db_session.execute(select(Wallet.balance_minor).where(Wallet.id == wallet.id))
    ).scalar_one()
    ledger_sum = (
        await db_session.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
                LedgerEntry.wallet_id == wallet.id
            )
        )
    ).scalar_one()

    assert wallet_balance == ledger_sum == 10_000 - 500 + 1_800


async def test_debit_insuficiente_no_mueve_el_balance(db_session: AsyncSession) -> None:
    wallet = await _create_user_with_wallet(db_session, balance_minor=100)
    wallets = WalletRepository(db_session)

    with pytest.raises(InsufficientFundsError):
        await wallets.debit(wallet.id, Money(101))

    balance = (
        await db_session.execute(select(Wallet.balance_minor).where(Wallet.id == wallet.id))
    ).scalar_one()
    assert balance == 100


async def test_get_by_user_id(db_session: AsyncSession) -> None:
    wallet = await _create_user_with_wallet(db_session, balance_minor=250)
    wallets = WalletRepository(db_session)

    found = await wallets.get_by_user_id(wallet.user_id)

    assert found is not None
    assert found.id == wallet.id
    assert found.balance_minor == 250


async def test_get_by_user_id_desconocido_devuelve_none(db_session: AsyncSession) -> None:
    wallets = WalletRepository(db_session)

    assert await wallets.get_by_user_id(uuid.uuid4()) is None


@pytest.fixture
async def big_pool_session_factory(
    integration_settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Motor propio con más conexiones que el pool por defecto (5): el test de
    concurrencia necesita que varias tareas puedan tener, de verdad, una
    conexión abierta cada una al mismo tiempo.
    """
    engine = create_app_engine(integration_settings.DATABASE_URL, pool_size=25)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_debitos_concurrentes_nunca_sobregiran(
    session_factory: async_sessionmaker[AsyncSession],
    big_pool_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    starting_balance = 1_000
    stake = 100
    attempts = 20  # el doble de lo que el balance puede cubrir

    async with session_factory() as setup_session:
        wallet = await _create_user_with_wallet(setup_session, balance_minor=starting_balance)
        await setup_session.commit()
        wallet_id = wallet.id

    async def _attempt_debit() -> bool:
        try:
            async with unit_of_work(big_pool_session_factory) as session:
                await WalletRepository(session).debit(wallet_id, Money(stake))
        except InsufficientFundsError:
            return False
        return True

    results = await asyncio.gather(*(_attempt_debit() for _ in range(attempts)))

    assert sum(results) == starting_balance // stake

    async with session_factory() as session:
        final_balance = (
            await session.execute(select(Wallet.balance_minor).where(Wallet.id == wallet_id))
        ).scalar_one()
    assert final_balance == 0
