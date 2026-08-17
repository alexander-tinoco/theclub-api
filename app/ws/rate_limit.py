"""Límite de intentos de conexión a `/ws`, por IP.

`slowapi`/`SlowAPIMiddleware` no sirve aquí: hereda de `BaseHTTPMiddleware`,
que Starlette salta por completo para conexiones WebSocket — así que el
rate limiting "global" de la Fase 8 no llega a `/ws` sin este mecanismo
aparte. Es deliberadamente más simple que `slowapi` (una ventana fija, no
deslizante): el objetivo es frenar un bucle de reconexión, no ofrecer
precisión de ventana — para eso ya existe `slowapi` en el resto de la API.
"""

import time
from collections import OrderedDict

#: Cuántas IPs distintas se recuerdan como máximo. Sin este tope, una IP que
#: se conecta una sola vez y nunca vuelve deja su entrada para siempre — con
#: suficientes IPs distintas a lo largo de la vida del proceso (bots,
#: escáneres, NAT/IPs dinámicas), el diccionario crece sin límite. Al
#: llegar al tope se descarta la IP con la que hace más tiempo no se
#: interactúa (para eso `OrderedDict` + `move_to_end` en cada acceso).
MAX_TRACKED_IPS = 10_000


class WsConnectRateLimiter:
    def __init__(self, *, max_attempts: int, window_seconds: float) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts: OrderedDict[str, list[float]] = OrderedDict()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        attempts = [t for t in self._attempts.get(key, []) if t > cutoff]

        allowed = len(attempts) < self._max_attempts
        if allowed:
            attempts.append(now)

        if attempts:
            self._attempts[key] = attempts
            self._attempts.move_to_end(key)
        else:
            self._attempts.pop(key, None)

        while len(self._attempts) > MAX_TRACKED_IPS:
            self._attempts.popitem(last=False)

        return allowed
