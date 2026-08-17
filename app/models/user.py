import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, func
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.wallet import Wallet


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # CITEXT: case-insensitive, so "Ana@mail.com" and "ana@mail.com" are the same account.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, index=True)
    password_hash: Mapped[str]
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Quotes required: Wallet is only imported under TYPE_CHECKING, and
    # SQLAlchemy resolves this forward-ref by name against its mapped-class
    # registry at configuration time — without quotes, the annotation gets
    # evaluated before that and blows up with a NameError.
    wallet: Mapped["Wallet"] = relationship(back_populates="user", uselist=False)  # noqa: UP037

    __table_args__ = (CheckConstraint("status IN ('active', 'suspended')", name="valid_status"),)
