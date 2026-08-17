"""`WsConnectRateLimiter` vive sobre Redis (no un dict en memoria) desde que
el resto del rate limiting se movió ahí — necesita un Redis real, de ahí que
esté en `integration/` y no en `unit/`.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.config import Settings
from app.infra.redis import create_redis_client
from app.ws.rate_limit import WsConnectRateLimiter

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_client(integration_settings: Settings) -> AsyncIterator[Redis]:
    client = create_redis_client(integration_settings)
    yield client
    await client.aclose()


def _unique_ip() -> str:
    # Claves distintas por test, no una IP fija: dos tests en la misma
    # ventana de tiempo real no deben pisarse el contador entre ellos.
    return f"203.0.113.{uuid.uuid4().int % 255}"


async def test_permite_hasta_el_maximo_de_intentos(redis_client: Redis) -> None:
    limiter = WsConnectRateLimiter(redis_client, max_attempts=3, window_seconds=60)
    ip = _unique_ip()

    assert await limiter.allow(ip) is True
    assert await limiter.allow(ip) is True
    assert await limiter.allow(ip) is True


async def test_rechaza_al_superar_el_maximo_dentro_de_la_ventana(redis_client: Redis) -> None:
    limiter = WsConnectRateLimiter(redis_client, max_attempts=3, window_seconds=60)
    ip = _unique_ip()
    for _ in range(3):
        await limiter.allow(ip)

    assert await limiter.allow(ip) is False


async def test_cada_ip_tiene_su_propio_contador(redis_client: Redis) -> None:
    limiter = WsConnectRateLimiter(redis_client, max_attempts=1, window_seconds=60)
    ip_a, ip_b = _unique_ip(), _unique_ip()

    assert await limiter.allow(ip_a) is True
    assert await limiter.allow(ip_b) is True
    assert await limiter.allow(ip_a) is False


async def test_los_intentos_fuera_de_la_ventana_no_cuentan(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    limiter = WsConnectRateLimiter(redis_client, max_attempts=1, window_seconds=60)
    ip = _unique_ip()
    times = iter([1_700_000_000.0, 1_700_000_200.0])  # 200s de separación > la ventana de 60s
    monkeypatch.setattr("app.ws.rate_limit.time.time", lambda: next(times))

    assert await limiter.allow(ip) is True
    assert await limiter.allow(ip) is True
