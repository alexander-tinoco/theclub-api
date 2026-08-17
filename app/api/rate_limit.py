"""Instancia compartida del limitador de peticiones (slowapi).

Backend en memoria — no hay Redis en este proyecto y no hace falta para una
sola instancia. Vive en su propio módulo (no en cada router) porque
`app/main.py` necesita la misma instancia para registrar el middleware y el
exception handler.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

#: Para cualquier endpoint sin un límite más específico — se aplica con
#: `@limiter.limit(GLOBAL_RATE_LIMIT)` explícito en cada ruta, NO vía
#: `Limiter(default_limits=[...])`: ese mecanismo depende de que
#: `SlowAPIMiddleware` encuentre el handler de la ruta recorriendo
#: `app.routes`, y en esta versión de FastAPI (0.141) las rutas quedan
#: envueltas en un `_IncludedRouter` interno que no expone `.endpoint` de la
#: forma que `slowapi` espera — `_find_route_handler` devuelve `None`
#: siempre, así que `default_limits` nunca dispara para ninguna ruta, sin
#: ningún error que lo delate. Confirmado leyendo el propio código de
#: `slowapi` y llamando a `_find_route_handler` a mano contra esta app.
#: Constante, no `Settings`, por la misma razón que `MAX_BETS_PER_ROUND`:
#: no es un valor que deba cambiar entre entornos.
GLOBAL_RATE_LIMIT = "200/minute"

limiter = Limiter(key_func=get_remote_address)
