"""Data access for rounds and bets — the game history."""

import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.round import Bet, Round


class ResolvedBetInput(TypedDict):
    bet_type: str
    selection: dict[str, Any]
    stake_minor: int
    payout_minor: int
    won: bool


class RoundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_settled_round(
        self,
        *,
        user_id: uuid.UUID,
        seed_pair_id: uuid.UUID,
        nonce: int,
        outcome: int,
        bets: list[ResolvedBetInput],
    ) -> tuple[Round, list[Bet]]:
        """Creates the round already settled — this game has no intermediate
        state: it spins and resolves within the same request."""
        now = datetime.now(UTC)
        round_ = Round(
            user_id=user_id,
            seed_pair_id=seed_pair_id,
            nonce=nonce,
            outcome=outcome,
            status="settled",
            settled_at=now,
        )
        self._session.add(round_)
        await self._session.flush()

        bet_rows = [
            Bet(
                round_id=round_.id,
                bet_type=bet["bet_type"],
                selection=bet["selection"],
                stake_minor=bet["stake_minor"],
                payout_minor=bet["payout_minor"],
                status="won" if bet["won"] else "lost",
            )
            for bet in bets
        ]
        self._session.add_all(bet_rows)
        await self._session.flush()
        return round_, bet_rows

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        cursor: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[Round]:
        stmt = (
            select(Round)
            .options(selectinload(Round.bets))
            .where(Round.user_id == user_id)
            .order_by(Round.created_at.desc(), Round.id.desc())
            .limit(limit)
        )
        if cursor is not None:
            created_at, round_id = cursor
            stmt = stmt.where(
                (Round.created_at < created_at)
                | ((Round.created_at == created_at) & (Round.id < round_id))
            )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
