"""slowapi has a `Limiter(default_limits=[...])` that in theory applies a
limit to any route without its own `@limiter.limit(...)`, applied via
`SlowAPIMiddleware`. In this version of FastAPI (0.141) it doesn't work:
the middleware walks `app.routes` looking for each route's handler
(`_find_route_handler`), and with the internal `_IncludedRouter` this
version uses, that search never finds anything — `_should_exempt` treats
every request as exempt, and `default_limits` never fires, with no error
to give it away.

That's why `GLOBAL_RATE_LIMIT` is applied with an explicit
`@limiter.limit(...)` on every route that previously had no limit of its
own — that mechanism doesn't depend on `SlowAPIMiddleware` or
`_find_route_handler`, and it was already proven to work (the auth
endpoints with their own limits since Phase 4). This test proves that a
route which, *before* this fix, had no limit at all (`/auth/me`) now really
does have one.
"""

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_a_route_with_no_limit_of_its_own_now_gets_limited(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            email = f"{uuid.uuid4()}@example.com"
            register = await client.post(
                "/api/v1/auth/register", json={"email": email, "password": "a-long-password"}
            )
            headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

            statuses = [
                (await client.get("/api/v1/auth/me", headers=headers)).status_code
                for _ in range(205)
            ]

    assert statuses.count(200) == 200
    assert statuses.count(429) == 5
