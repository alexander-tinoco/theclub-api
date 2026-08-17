import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OutboxEvent(Base):
    """Outbox pattern (Phase 6): the service inserts here in the same
    transaction that mutates business state; a background relay publishes
    and marks `published_at`. The partial index is what keeps the relay's
    query (`WHERE published_at IS NULL`) cheap even as the table
    accumulates millions of already-published rows.
    """

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic: Mapped[str]
    key: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None]
    # NULL = eligible right away. After a failure it's set in the future
    # (exponential backoff) so a failing row isn't retried on every poll
    # (every 500ms by default) while Kafka is down.
    next_attempt_at: Mapped[datetime | None]

    __table_args__ = (
        Index(
            "ix_outbox_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )
