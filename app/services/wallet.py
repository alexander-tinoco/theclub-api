"""Casos de uso de wallet: balance, historial, depósito simulado."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.money import Money
from app.events.outbox import enqueue_event, new_envelope
from app.events.schemas import WalletTransactionData
from app.repositories.ledger import LedgerRepository
from app.repositories.wallets import WalletRepository


async def get_balance(session: AsyncSession, *, user_id: uuid.UUID) -> dict[str, Any]:
    wallet = await WalletRepository(session).get_by_user_id(user_id)
    assert wallet is not None  # todo user tiene wallet desde el registro
    return {"balance_minor": wallet.balance_minor, "currency": wallet.currency}


async def list_transactions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cursor: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[Any]:
    wallet = await WalletRepository(session).get_by_user_id(user_id)
    assert wallet is not None
    return await LedgerRepository(session).list_by_wallet(wallet.id, cursor=cursor, limit=limit)


async def deposit(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    amount_minor: int,
) -> dict[str, Any]:
    wallets = WalletRepository(session)
    wallet = await wallets.get_by_user_id(user_id)
    assert wallet is not None

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

    return {"balance_minor": balance_after, "currency": wallet.currency}
