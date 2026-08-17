"""Redis-backed limit on `/ws` connection attempts, per IP.

`slowapi`/`SlowAPIMiddleware` doesn't work here: it inherits from
`BaseHTTPMiddleware`, which Starlette skips entirely for WebSocket
connections — so Phase 8's "global" rate limiting never reaches `/ws`
without this separate mechanism. Redis (not an in-memory dict) so the
count survives a process redeploy — same as the rest of the rate limiting
since it moved to Redis.

Fixed window, not sliding, on purpose: the goal is to slow down a
reconnect loop, not offer window precision — `slowapi` (also on Redis)
already handles that for the rest of the API. A fixed window is a single
atomic Redis operation (`INCR` + `EXPIRE` the first time), with no need for
a sorted set or manually pruning old entries.
"""

import math
import time

from redis.asyncio import Redis


class WsConnectRateLimiter:
    def __init__(self, redis: Redis, *, max_attempts: int, window_seconds: float) -> None:
        self._redis = redis
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds

    async def allow(self, key: str) -> bool:
        window_bucket = math.floor(time.time() / self._window_seconds)
        redis_key = f"ws-connect-rl:{key}:{window_bucket}"

        attempts = await self._redis.incr(redis_key)
        if attempts == 1:
            # Only the window's first request sets the TTL — subsequent
            # ones reuse the `EXPIRE` already set, so the key disappears on
            # its own with nothing having to clean it up.
            await self._redis.expire(redis_key, math.ceil(self._window_seconds))

        return attempts <= self._max_attempts
