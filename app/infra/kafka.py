"""Kafka producer. `acks="all"` + `enable_idempotence=True` stop the
producer itself from duplicating a message when retrying a send that
failed partway through the network — they don't protect against the
*relay* publishing the same outbox row twice if the process dies between
"Kafka confirmed" and "published_at got marked". That case is what makes
the system at-least-once end to end, as `contracts/events/README.md`
already documented since Phase 1: `event_id` is the consumer-side
deduplication key, not something the producer resolves.
"""

from aiokafka import AIOKafkaProducer

from app.config import Settings
from app.events.schemas import EVENT_TOPIC_SUFFIXES


async def check_kafka(producer: AIOKafkaProducer, settings: Settings) -> None:
    """`/ready` check: asks for a real topic's metadata. If the broker
    doesn't respond, `partitions_for` raises — which is exactly what
    `/ready` needs to report `fail`.
    """
    topic = f"{settings.KAFKA_TOPIC_PREFIX}.{EVENT_TOPIC_SUFFIXES['bet.placed']}"
    await producer.partitions_for(topic)


def create_producer(settings: Settings) -> AIOKafkaProducer:
    sasl_kwargs: dict[str, str] = {}
    if settings.KAFKA_SECURITY_PROTOCOL.startswith("SASL"):
        password = settings.KAFKA_SASL_PASSWORD
        sasl_kwargs = {
            "sasl_mechanism": settings.KAFKA_SASL_MECHANISM or "",
            "sasl_plain_username": settings.KAFKA_SASL_USERNAME or "",
            "sasl_plain_password": password.get_secret_value() if password else "",
        }

    return AIOKafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        security_protocol=settings.KAFKA_SECURITY_PROTOCOL,
        acks="all",
        enable_idempotence=True,
        **sasl_kwargs,
    )
