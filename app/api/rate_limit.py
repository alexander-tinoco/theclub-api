"""Shared instance of the request limiter (slowapi).

Redis-backed (Phase 8): in-process memory state is lost on every redeploy —
an attacker who synced their attempts with one would slip past it without
even noticing. With Redis the count survives the process that uses it.
Lives in its own module (not in each router) because `app/main.py` needs
the same instance to register the middleware and the exception handler.

`get_settings()` instead of receiving `Settings` as a parameter: `limiter`
is a module-level singleton, built once on import — routers that use
`@limiter.limit(...)` as a decorator need it available at the moment they
themselves are imported, before any app exists. Same pattern as
`PLACEHOLDER_SECRET` in `config.py`: resolved from the environment once,
not per request.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

#: For any endpoint without a more specific limit — applied with
#: `@limiter.limit(GLOBAL_RATE_LIMIT)` explicitly on each route, NOT via
#: `Limiter(default_limits=[...])`: that mechanism depends on
#: `SlowAPIMiddleware` finding the route's handler by walking `app.routes`,
#: and in this version of FastAPI (0.141) routes get wrapped in an internal
#: `_IncludedRouter` that doesn't expose `.endpoint` the way `slowapi`
#: expects — `_find_route_handler` always returns `None`, so
#: `default_limits` never fires for any route, with no error to give it
#: away. Confirmed by reading `slowapi`'s own code and calling
#: `_find_route_handler` by hand against this app. A constant, not
#: `Settings`, for the same reason as `MAX_BETS_PER_ROUND`: not a value
#: that should change between environments.
GLOBAL_RATE_LIMIT = "200/minute"

limiter = Limiter(key_func=get_remote_address, storage_uri=get_settings().REDIS_URL)
