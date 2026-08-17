"""Writes events into the outbox table, within the same transaction as the
business mutation that originates them — never a dual-write. The relay that
actually publishes them to Kafka arrives in Phase 6; until then they pile up here.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.events.schemas import EVENT_TOPIC_SUFFIXES, EventEnvelope
from app.models.outbox import OutboxEvent


def new_envelope(event_type: str, data: Any, *, idempotency_key: str | None) -> EventEnvelope[Any]:
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type=event_type,
        event_version=1,
        occurred_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
        data=data,
    )


async def enqueue_event(
    session: AsyncSession, settings: Settings, envelope: EventEnvelope[Any], *, key: str
) -> None:
    suffix = EVENT_TOPIC_SUFFIXES[envelope.event_type]
    topic = f"{settings.KAFKA_TOPIC_PREFIX}.{suffix}"
    session.add(OutboxEvent(topic=topic, key=key, payload=envelope.model_dump(mode="json")))
    await session.flush()
