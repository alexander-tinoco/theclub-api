import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OutboxEvent(Base):
    """Patrón outbox (Fase 6): el servicio inserta aquí en la misma transacción
    que muta el negocio; un relay en background publica y marca `published_at`.
    El índice parcial es lo que hace barata la consulta del relay
    (`WHERE published_at IS NULL`) aunque la tabla acumule millones de filas
    ya publicadas.
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
    # NULL = elegible de inmediato. Tras un fallo se pone en el futuro (backoff
    # exponencial) para que una fila que falla no se reintente en cada poll
    # (cada 500ms por defecto) mientras Kafka esté caído.
    next_attempt_at: Mapped[datetime | None]

    __table_args__ = (
        Index(
            "ix_outbox_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )
