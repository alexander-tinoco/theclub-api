import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User

DEFAULT_CURRENCY = "EUR"


class Wallet(Base):
    """No optimistic-locking column on purpose: the repository only exposes
    atomic `debit`/`credit` (UPDATE ... WHERE ... RETURNING), never a
    read-modify-write, so there's no race a `version` column would need to
    prevent.
    """

    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(default=DEFAULT_CURRENCY)

    # Quotes required: see the equivalent note in user.py (User is only
    # imported under TYPE_CHECKING).
    user: Mapped["User"] = relationship(back_populates="wallet")  # noqa: UP037

    __table_args__ = (CheckConstraint("balance_minor >= 0", name="balance_non_negative"),)
