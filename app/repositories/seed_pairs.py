"""Acceso a datos de seed pairs (provably fair). Sin criptografía aquí —
`app/domain/fairness.py` genera server_seed/hash; este módulo solo persiste."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fairness import SeedPair


class SeedPairRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_by_user_id(self, user_id: uuid.UUID) -> SeedPair | None:
        result = await self._session.execute(
            select(SeedPair).where(SeedPair.user_id == user_id, SeedPair.status == "active")
        )
        return result.scalar_one_or_none()

    async def create_active(
        self, *, user_id: uuid.UUID, server_seed: bytes, server_seed_hash: str, client_seed: str
    ) -> SeedPair:
        seed_pair = SeedPair(
            user_id=user_id,
            server_seed=server_seed,
            server_seed_hash=server_seed_hash,
            client_seed=client_seed,
            nonce=0,
            status="active",
        )
        self._session.add(seed_pair)
        await self._session.flush()
        return seed_pair

    async def consume_nonce(self, seed_pair_id: uuid.UUID) -> int:
        """Incrementa el nonce de forma atómica y devuelve el nuevo valor —
        mismo patrón que `WalletRepository.debit`: una sola sentencia UPDATE,
        sin lectura previa, así dos apuestas concurrentes del mismo usuario
        nunca reciben el mismo nonce. El primer giro usable es el 1, no el 0
        (el 0 es "cero giros consumidos todavía").
        """
        stmt = (
            update(SeedPair)
            .where(SeedPair.id == seed_pair_id)
            .values(nonce=SeedPair.nonce + 1)
            .returning(SeedPair.nonce)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def reveal_and_deactivate(self, seed_pair_id: uuid.UUID) -> None:
        await self._session.execute(
            update(SeedPair)
            .where(SeedPair.id == seed_pair_id)
            .values(status="revealed", revealed_at=datetime.now(UTC))
        )
