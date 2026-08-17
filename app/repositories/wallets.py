"""Acceso a datos de wallets. Sin lógica de negocio — eso vive en app/services/
(Fase 5). El único mecanismo de escritura son `debit`/`credit`, atómicos; no
hay ni habrá un `set_balance`, para que nunca exista un read-modify-write que
necesite bloqueo optimista.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import Money
from app.models.wallet import Wallet


class InsufficientFundsError(Exception):
    """El wallet no tiene saldo suficiente para el débito solicitado."""


class WalletRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: uuid.UUID) -> Wallet | None:
        result = await self._session.execute(select(Wallet).where(Wallet.user_id == user_id))
        return result.scalar_one_or_none()

    async def debit(self, wallet_id: uuid.UUID, amount: Money) -> int:
        """Descuenta `amount` de forma atómica. Devuelve el balance resultante.

        Una sola sentencia `UPDATE ... WHERE ... RETURNING`, sin lectura previa:
        dos débitos concurrentes sobre el mismo wallet nunca pueden dejarlo en
        negativo, porque es la base de datos —no una carrera de lecturas en
        Python— quien decide si la condición `balance_minor >= amount` se
        cumple en el momento exacto de la escritura.
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
                f"wallet {wallet_id} no cubre un débito de {amount.minor} {amount.currency}"
            )
        return new_balance

    async def credit(self, wallet_id: uuid.UUID, amount: Money) -> int:
        """Acredita `amount`. Igual de atómica que `debit`, sin condición de saldo."""
        stmt = (
            update(Wallet)
            .where(Wallet.id == wallet_id)
            .values(balance_minor=Wallet.balance_minor + amount.minor)
            .returning(Wallet.balance_minor)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()
