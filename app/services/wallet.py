"""Wallet use cases: balance, history, simulated deposit."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.money import Money
from app.events.outbox import enqueue_event, new_envelope
from app.events.schemas import WalletTransactionData
from app.models.ledger import LedgerEntry
from app.repositories.ledger import LedgerRepository
from app.repositories.wallets import WalletRepository
from app.services.exceptions import DataIntegrityError

#: Sanity cap for the simulated deposit — there's no real payment gateway
#: behind it, so there's no "real" business limit; this only stops an
#: outlandish number from slipping through by accident.
MAX_DEPOSIT_MINOR = 10_000_000  # 100,000.00 in the table's currency


@dataclass(frozen=True, slots=True)
class BalanceView:
    balance_minor: int
    currency: str

    def to_dict(self) -> dict[str, Any]:
        return {"balance_minor": self.balance_minor, "currency": self.currency}


async def get_balance(session: AsyncSession, *, user_id: uuid.UUID) -> BalanceView:
    wallet = await WalletRepository(session).get_by_user_id(user_id)
    if wallet is None:
        raise DataIntegrityError(f"user {user_id} has no wallet")
    return BalanceView(balance_minor=wallet.balance_minor, currency=wallet.currency)


async def list_transactions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cursor: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[LedgerEntry]:
    wallet = await WalletRepository(session).get_by_user_id(user_id)
    if wallet is None:
        raise DataIntegrityError(f"user {user_id} has no wallet")
    return await LedgerRepository(session).list_by_wallet(wallet.id, cursor=cursor, limit=limit)


async def deposit(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    amount_minor: int,
) -> BalanceView:
    wallets = WalletRepository(session)
    wallet = await wallets.get_by_user_id(user_id)
    if wallet is None:
        raise DataIntegrityError(f"user {user_id} has no wallet")

    balance_after = await wallets.credit(wallet.id, Money(amount_minor))

    entry = await LedgerRepository(session).append(
        wallet_id=wallet.id,
        amount=Money(amount_minor),
        balance_after_minor=balance_after,
        kind="deposit",
    )

    envelope = new_envelope(
        "wallet.transaction",
        WalletTransactionData(
            transaction_id=entry.id,
            user_id=user_id,
            wallet_id=wallet.id,
            kind="deposit",
            amount_minor=amount_minor,
            balance_after_minor=balance_after,
            currency=wallet.currency,
            ref_type=None,
            ref_id=None,
        ),
        idempotency_key=idempotency_key,
    )
    await enqueue_event(session, settings, envelope, key=str(user_id))

    return BalanceView(balance_minor=balance_after, currency=wallet.currency)
