"""Productor de Kafka. `acks="all"` + `enable_idempotence=True` evitan que el
propio productor duplique un mensaje al reintentar un envío que falló a
medio camino de red — no protegen contra que el *relay* publique la misma
fila del outbox dos veces si el proceso muere entre "Kafka confirmó" y
"se marcó published_at". Ese caso es el que hace que el sistema sea
at-least-once de punta a punta, tal como ya documentaba
`contracts/events/README.md` desde la Fase 1: `event_id` es la clave de
deduplicación del lado del consumidor, no algo que el productor resuelva.
"""

from aiokafka import AIOKafkaProducer

from app.config import Settings
from app.events.schemas import EVENT_TOPIC_SUFFIXES


async def check_kafka(producer: AIOKafkaProducer, settings: Settings) -> None:
    """Check de `/ready`: pide los metadatos de un topic real. Si el broker
    no responde, `partitions_for` lanza — eso es justo lo que `/ready`
    necesita para reportar `fail`.
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
