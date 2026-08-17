"""Closes the coverage gap noted since Phase 0: FastAPI's lifespan doesn't
fire with plain `ASGITransport`, so the Postgres check in `/ready` had
never really been exercised until a real database existed to query.
"""

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_ready_reports_database_ok_with_a_real_postgres(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok", "kafka": "ok"},
    }
