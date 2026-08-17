import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SeedPair(Base):
    """Provably fair. `server_seed` solo se lee tras el reveal; hasta entonces
    solo `server_seed_hash` es público. El índice único parcial garantiza, a
    nivel de base de datos, que un usuario nunca tenga dos pares activos a la
    vez — sin que la app tenga que bloquear nada para sostener ese invariante.
    """

    __tablename__ = "seed_pairs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    server_seed: Mapped[bytes]
    server_seed_hash: Mapped[str]
    client_seed: Mapped[str]
    nonce: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="active")
    revealed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('active', 'revealed')", name="valid_status"),
        Index(
            "uq_one_active_seed_pair_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
