"""Instancia compartida del limitador de peticiones (slowapi).

Backend en memoria — no hay Redis en este proyecto y no hace falta para una
sola instancia. Vive en su propio módulo (no en cada router) porque
`app/main.py` necesita la misma instancia para registrar el middleware y el
exception handler.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

#: Cubre cualquier endpoint HTTP sin un `@limiter.limit(...)` explícito
#: (`/fairness/*`, `/rounds` GET, `/transactions`, `/balance`, `/auth/me`,
#: `/metrics`) — antes de la Fase 8 estaban completamente sin límite.
#: Constante, no `Settings`, por la misma razón que `MAX_BETS_PER_ROUND`:
#: no es un valor que deba cambiar entre entornos.
GLOBAL_RATE_LIMIT = "200/minute"

limiter = Limiter(key_func=get_remote_address, default_limits=[GLOBAL_RATE_LIMIT])
