import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RefreshToken(Base):
    """Refresh session, not a JWT: only the real token's hash is stored (see
    `app/infra/security.py`). `family_id` groups an entire rotation chain
    from one login — detecting a reuse revokes the whole family, not just
    the row.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
