"""Acceso a datos del ledger. Solo `append` y lectura — es un libro append-only
de verdad: no hay `update` ni `delete` porque el repositorio no los expone.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
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

    async def list_by_wallet(
        self,
        wallet_id: uuid.UUID,
        *,
        cursor: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[LedgerEntry]:
        stmt = (
            select(LedgerEntry)
            .where(LedgerEntry.wallet_id == wallet_id)
            .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
            .limit(limit)
        )
        if cursor is not None:
            created_at, entry_id = cursor
            stmt = stmt.where(
                (LedgerEntry.created_at < created_at)
                | ((LedgerEntry.created_at == created_at) & (LedgerEntry.id < entry_id))
            )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
