"""Redis client: backend for both the global rate limiter (slowapi) and the
`/ws` one. Unlike `AIOKafkaProducer`, `redis.asyncio.Redis` doesn't require
a running event loop to be constructed — only for the operations that do
real IO — so this client is created in `create_app()`, not in the
`lifespan`, just like the Postgres engine.
"""

from redis.asyncio import Redis

from app.config import Settings


async def check_redis(redis: Redis) -> None:
    """`/ready` check: if Redis doesn't respond, `ping` raises — exactly
    what `/ready` needs to report `fail`.
    """
    await redis.ping()


def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(settings.REDIS_URL)
