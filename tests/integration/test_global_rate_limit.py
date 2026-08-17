"""slowapi tiene un `Limiter(default_limits=[...])` que en teoría aplica un
límite a cualquier ruta sin un `@limiter.limit(...)` propio, aplicado vía
`SlowAPIMiddleware`. En esta versión de FastAPI (0.141) no funciona: el
middleware recorre `app.routes` buscando el handler de cada ruta
(`_find_route_handler`), y con el `_IncludedRouter` interno que usa esta
versión, esa búsqueda nunca encuentra nada — `_should_exempt` trata cada
petición como exenta, y `default_limits` nunca dispara, sin ningún error
que lo delate.

Por eso `GLOBAL_RATE_LIMIT` se aplica con `@limiter.limit(...)` explícito en
cada ruta que antes no tenía límite propio — ese mecanismo no depende de
`SlowAPIMiddleware` ni de `_find_route_handler`, y ya estaba probado que
funciona (los endpoints de auth con límites propios desde la Fase 4). Este
test prueba que una ruta que *antes* de este fix no tenía ningún límite
(`/auth/me`) ahora sí lo tiene de verdad.
"""

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


async def test_una_ruta_sin_limite_propio_ahora_si_limita(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            email = f"{uuid.uuid4()}@example.com"
            register = await client.post(
                "/api/v1/auth/register", json={"email": email, "password": "contraseña-larga"}
            )
            headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

            statuses = [
                (await client.get("/api/v1/auth/me", headers=headers)).status_code
                for _ in range(205)
            ]

    assert statuses.count(200) == 200
    assert statuses.count(429) == 5
