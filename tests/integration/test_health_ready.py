"""Cierra el hueco de cobertura que quedó anotado desde la Fase 0: el lifespan
de FastAPI no se dispara con `ASGITransport` a secas, así que el check de
Postgres en `/ready` nunca se había ejercitado de verdad hasta que existió una
base de datos real que consultar.
"""

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_ready_reporta_database_ok_con_postgres_real(
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
        "checks": {"database": "ok", "kafka": "ok"},
    }
