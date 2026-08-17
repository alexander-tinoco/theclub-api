"""Cliente de Redis: backend del rate limiting global (slowapi) y del de
`/ws`. A diferencia de `AIOKafkaProducer`, `redis.asyncio.Redis` no exige un
event loop corriendo para construirse — solo para las operaciones que hacen
IO real — así que este cliente se crea en `create_app()`, no en el
`lifespan`, igual que el engine de Postgres.
"""

from redis.asyncio import Redis

from app.config import Settings


async def check_redis(redis: Redis) -> None:
    """Check de `/ready`: si Redis no responde, `ping` lanza — justo lo que
    `/ready` necesita para reportar `fail`.
    """
    await redis.ping()


def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(settings.REDIS_URL)
