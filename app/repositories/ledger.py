"""Acceso a datos del ledger. Solo `append` — es un libro append-only de
verdad: no hay `update` ni `delete` porque el repositorio no los expone.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import Money
from app.models.ledger import LedgerEntry


class LedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        wallet_id: uuid.UUID,
        amount: Money,
        balance_after_minor: int,
        kind: str,
        ref_type: str | None = None,
        ref_id: uuid.UUID | None = None,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            wallet_id=wallet_id,
            amount_minor=amount.minor,
            balance_after_minor=balance_after_minor,
            kind=kind,
            ref_type=ref_type,
            ref_id=ref_id,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry
