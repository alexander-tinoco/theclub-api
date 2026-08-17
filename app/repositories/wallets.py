"""Data access for wallets. No business logic — that lives in app/services/
(Phase 5). The only write mechanisms are `debit`/`credit`, both atomic; there
is not, and will never be, a `set_balance`, so a read-modify-write that needs
optimistic locking can never exist.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import Money
from app.models.wallet import Wallet


class InsufficientFundsError(Exception):
    """The wallet doesn't have enough balance for the requested debit."""


class WalletRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: uuid.UUID) -> Wallet | None:
        result = await self._session.execute(select(Wallet).where(Wallet.user_id == user_id))
        return result.scalar_one_or_none()

    async def debit(self, wallet_id: uuid.UUID, amount: Money) -> int:
        """Deducts `amount` atomically. Returns the resulting balance.

        A single `UPDATE ... WHERE ... RETURNING` statement, with no prior
        read: two concurrent debits against the same wallet can never leave
        it negative, because it's the database — not a read race in
        Python — that decides whether the `balance_minor >= amount`
        condition holds at the exact moment of the write.
        """
        stmt = (
            update(Wallet)
            .where(Wallet.id == wallet_id, Wallet.balance_minor >= amount.minor)
            .values(balance_minor=Wallet.balance_minor - amount.minor)
            .returning(Wallet.balance_minor)
        )
        result = await self._session.execute(stmt)
        new_balance = result.scalar_one_or_none()
        if new_balance is None:
            raise InsufficientFundsError(
                f"wallet {wallet_id} can't cover a debit of {amount.minor} {amount.currency}"
            )
        return new_balance

    async def credit(self, wallet_id: uuid.UUID, amount: Money) -> int:
        """Credits `amount`. Just as atomic as `debit`, with no balance condition."""
        stmt = (
            update(Wallet)
            .where(Wallet.id == wallet_id)
            .values(balance_minor=Wallet.balance_minor + amount.minor)
            .returning(Wallet.balance_minor)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()
