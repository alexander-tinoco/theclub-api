"""Data access for seed pairs (provably fair). No cryptography here —
`app/domain/fairness.py` generates server_seed/hash; this module only persists."""

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
        """Increments the nonce atomically and returns the new value — same
        pattern as `WalletRepository.debit`: a single UPDATE statement,
        with no prior read, so two concurrent bets from the same user never
        get the same nonce. The first usable spin is 1, not 0 (0 means
        "zero spins consumed so far").
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
