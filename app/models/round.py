import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Round(Base):
    __tablename__ = "rounds"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    seed_pair_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seed_pairs.id"))
    nonce: Mapped[int]
    outcome: Mapped[int | None]
    status: Mapped[str] = mapped_column(default="pending")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    settled_at: Mapped[datetime | None]

    bets: Mapped[list[Bet]] = relationship(back_populates="round")

    __table_args__ = (
        CheckConstraint(
            "outcome IS NULL OR (outcome >= 0 AND outcome <= 36)", name="valid_outcome"
        ),
        CheckConstraint("status IN ('pending', 'settled')", name="valid_status"),
        UniqueConstraint("seed_pair_id", "nonce", name="uq_seed_pair_nonce"),
    )


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    round_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rounds.id"), index=True)
    bet_type: Mapped[str]
    selection: Mapped[dict[str, Any]] = mapped_column(JSONB)
    stake_minor: Mapped[int] = mapped_column(BigInteger)
    payout_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(default="pending")

    round: Mapped[Round] = relationship(back_populates="bets")

    __table_args__ = (
        CheckConstraint("stake_minor > 0", name="positive_stake"),
        CheckConstraint("payout_minor >= 0", name="non_negative_payout"),
        CheckConstraint("status IN ('pending', 'won', 'lost')", name="valid_status"),
    )
