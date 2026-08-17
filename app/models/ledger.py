import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

LEDGER_KINDS = ("deposit", "bet_stake", "bet_payout", "adjustment")
_KINDS_SQL = ", ".join(f"'{kind}'" for kind in LEDGER_KINDS)


class LedgerEntry(Base):
    """Libro append-only: la fuente de verdad del dinero. El balance del wallet
    es una caché de la suma de estas filas — nunca se actualiza ni se borra una
    entrada ya escrita (el repositorio no expone update/delete).
    """

    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wallets.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)  # con signo
    balance_after_minor: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str]
    ref_type: Mapped[str | None]
    ref_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"kind IN ({_KINDS_SQL})", name="valid_kind"),
        CheckConstraint("balance_after_minor >= 0", name="balance_after_non_negative"),
    )
