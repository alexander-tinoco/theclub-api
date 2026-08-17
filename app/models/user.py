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
    # CITEXT: case-insensitive, así "Ana@mail.com" y "ana@mail.com" son la misma cuenta.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, index=True)
    password_hash: Mapped[str]
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Comillas necesarias: Wallet solo se importa bajo TYPE_CHECKING, y
    # SQLAlchemy resuelve este forward-ref por nombre contra su registro de
    # clases mapeadas en tiempo de configuración — sin comillas, la anotación
    # se evalúa antes de eso y revienta con NameError.
    wallet: Mapped["Wallet"] = relationship(back_populates="user", uselist=False)  # noqa: UP037

    __table_args__ = (CheckConstraint("status IN ('active', 'suspended')", name="valid_status"),)
