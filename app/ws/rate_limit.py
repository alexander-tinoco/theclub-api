"""Límite de intentos de conexión a `/ws`, por IP, respaldado en Redis.

`slowapi`/`SlowAPIMiddleware` no sirve aquí: hereda de `BaseHTTPMiddleware`,
que Starlette salta por completo para conexiones WebSocket — así que el
rate limiting "global" de la Fase 8 no llega a `/ws` sin este mecanismo
aparte. Redis (no un dict en memoria) para que el conteo sobreviva a un
redeploy del proceso — igual que el resto del rate limiting desde que se
movió a Redis.

Ventana fija, no deslizante, a propósito: el objetivo es frenar un bucle de
reconexión, no ofrecer precisión de ventana — para eso ya está `slowapi`
(también sobre Redis) en el resto de la API. Una ventana fija es una sola
operación atómica en Redis (`INCR` + `EXPIRE` la primera vez), sin
necesidad de un sorted set ni de podar entradas viejas a mano.
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
            # Solo la primera petición de la ventana pone el TTL — las
            # siguientes reutilizan el mismo `EXPIRE` ya puesto, así la
            # clave desaparece sola sin que nada tenga que limpiarla.
            await self._redis.expire(redis_key, math.ceil(self._window_seconds))

        return attempts <= self._max_attempts
