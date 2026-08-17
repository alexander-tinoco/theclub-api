import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User

DEFAULT_CURRENCY = "EUR"


class Wallet(Base):
    """Sin columna de bloqueo optimista a propósito: el repositorio solo expone
    `debit`/`credit` atómicos (UPDATE ... WHERE ... RETURNING), nunca un
    read-modify-write, así que no hay carrera que un `version` deba prevenir.
    """

    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(default=DEFAULT_CURRENCY)

    # Comillas necesarias: ver la nota equivalente en user.py (User solo se
    # importa bajo TYPE_CHECKING).
    user: Mapped["User"] = relationship(back_populates="wallet")  # noqa: UP037

    __table_args__ = (CheckConstraint("balance_minor >= 0", name="balance_non_negative"),)
