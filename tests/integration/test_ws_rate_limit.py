"""`WsConnectRateLimiter` lives on Redis (not an in-memory dict) since the
rest of the rate limiting moved there — needs a real Redis, which is why
it's in `integration/` and not `unit/`.
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
    # A distinct key per test, not a fixed IP: two tests in the same real
    # time window shouldn't step on each other's counter.
    return f"203.0.113.{uuid.uuid4().int % 255}"


async def test_allows_up_to_the_max_attempts(redis_client: Redis) -> None:
    limiter = WsConnectRateLimiter(redis_client, max_attempts=3, window_seconds=60)
    ip = _unique_ip()

    assert await limiter.allow(ip) is True
    assert await limiter.allow(ip) is True
    assert await limiter.allow(ip) is True


async def test_rejects_once_the_max_is_exceeded_within_the_window(redis_client: Redis) -> None:
    limiter = WsConnectRateLimiter(redis_client, max_attempts=3, window_seconds=60)
    ip = _unique_ip()
    for _ in range(3):
        await limiter.allow(ip)

    assert await limiter.allow(ip) is False


async def test_each_ip_has_its_own_counter(redis_client: Redis) -> None:
    limiter = WsConnectRateLimiter(redis_client, max_attempts=1, window_seconds=60)
    ip_a, ip_b = _unique_ip(), _unique_ip()

    assert await limiter.allow(ip_a) is True
    assert await limiter.allow(ip_b) is True
    assert await limiter.allow(ip_a) is False


async def test_attempts_outside_the_window_do_not_count(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    limiter = WsConnectRateLimiter(redis_client, max_attempts=1, window_seconds=60)
    ip = _unique_ip()
    times = iter([1_700_000_000.0, 1_700_000_200.0])  # 200s apart > the 60s window
    monkeypatch.setattr("app.ws.rate_limit.time.time", lambda: next(times))

    assert await limiter.allow(ip) is True
    assert await limiter.allow(ip) is True
