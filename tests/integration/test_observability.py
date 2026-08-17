"""DoD de la Fase 8: handler global que nunca filtra internals, logging
correlacionado (`request_id`/`user_id`) con línea canónica, CORS
restringido, rate limiting global (incluido el de `/ws`, aparte por la
limitación real de `slowapi` con WebSockets), y límite de tamaño de body.

Para capturar la línea canónica se engancha un handler propio directamente
al logger `"canonical"` en vez de usar `caplog` — ver
`test_alembic_logging.py` para el porqué (un bug real de `alembic/env.py`
que salió escribiendo este archivo, y que dejaba sin efecto cualquier
handler que pytest intentara enganchar por su cuenta).
"""

import logging
import uuid
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


class _CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def _register(client: AsyncClient) -> str:
    email = f"{uuid.uuid4()}@example.com"
    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "contraseña-larga"}
    )
    token: str = register.json()["access_token"]
    return token


async def test_excepcion_no_mapeada_responde_500_sin_filtrar_el_mensaje_real(
    integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import wallet as wallet_service

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("credenciales de la base de datos: hunter2")

    monkeypatch.setattr(wallet_service, "get_balance", _boom)

    collector = _CollectingHandler()
    canonical_logger = logging.getLogger("canonical")
    canonical_logger.addHandler(collector)

    app = create_app(integration_settings)
    try:
        async with LifespanManager(app):
            # `raise_app_exceptions=False`: Starlette relanza la excepción
            # tras mandar la respuesta (para que el servidor ASGI la
            # loggee) — sin este flag, httpx la vuelve a levantar aquí y
            # nunca llegaríamos a ver la respuesta real que sí recibió el
            # cliente.
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                token = await _register(client)
                response = await client.get(
                    "/api/v1/wallet/balance",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Origin": "http://localhost:3000",
                    },
                )
    finally:
        canonical_logger.removeHandler(collector)

    assert response.status_code == 500
    assert response.json() == {"detail": "error interno"}
    assert "hunter2" not in response.text
    # Regresión: Starlette manda los handlers de `Exception`/500 registrados
    # con `add_exception_handler` a `ServerErrorMiddleware`, la capa MÁS
    # externa — por fuera de CORS y de RequestContextMiddleware. Una
    # respuesta generada ahí no llevaría estos headers, y la línea canónica
    # quedaría con `status_code: null`. Por eso el catch-all es middleware
    # (`UnhandledExceptionMiddleware`), no un exception handler — estos
    # asserts son lo que se rompe si eso vuelve a cambiar.
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "x-request-id" in response.headers

    canonical_dicts = [getattr(r, "canonical", None) for r in collector.records]
    balance_lines = [
        c for c in canonical_dicts if c is not None and c.get("path") == "/api/v1/wallet/balance"
    ]
    assert len(balance_lines) == 1
    assert balance_lines[0]["status_code"] == 500


async def test_respuesta_http_incluye_x_request_id(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert "x-request-id" in response.headers
    uuid.UUID(response.headers["x-request-id"])  # no levanta si es un UUID válido


async def test_linea_canonica_de_un_post_incluye_lo_que_bind_canonical_agrego(
    integration_settings: Settings,
) -> None:
    collector = _CollectingHandler()
    canonical_logger = logging.getLogger("canonical")
    canonical_logger.addHandler(collector)

    app = create_app(integration_settings)
    try:
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                token = await _register(client)
                headers = {"Authorization": f"Bearer {token}"}
                await client.post(
                    "/api/v1/wallet/deposit",
                    json={"amount_minor": 5_000},
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                )
    finally:
        canonical_logger.removeHandler(collector)

    canonical_dicts = [getattr(r, "canonical", None) for r in collector.records]
    deposit_lines = [
        c for c in canonical_dicts if c is not None and c.get("path") == "/api/v1/wallet/deposit"
    ]
    assert len(deposit_lines) == 1
    line = deposit_lines[0]
    assert line["method"] == "POST"
    assert line["status_code"] == 200
    assert line["amount_minor"] == 5_000
    assert line["balance_minor"] == 5_000
    assert line["user_id"] is not None
    assert "request_id" in line
    assert "duration_ms" in line


async def test_metrics_expone_formato_prometheus_y_refleja_actividad(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/health")
            response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "http_requests_total" in body
    assert 'path="/health"' in body


async def test_body_demasiado_grande_responde_413(integration_settings: Settings) -> None:
    settings = integration_settings.model_copy(update={"MAX_REQUEST_BODY_BYTES": 100})
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = await _register(client)
            oversized_email_field = "x" * 500
            response = await client.post(
                "/api/v1/roulette/rounds",
                json={
                    "bets": [
                        {
                            "bet_type": "red",
                            "selection": {"padding": oversized_email_field},
                            "stake_minor": 1000,
                        }
                    ]
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": str(uuid.uuid4()),
                },
            )

    assert response.status_code == 413


async def test_ws_connect_rate_limit_cierra_tras_demasiados_intentos(
    integration_settings: Settings,
) -> None:
    settings = integration_settings.model_copy(
        update={"WS_CONNECT_RATE_LIMIT_ATTEMPTS": 2, "WS_CONNECT_RATE_LIMIT_WINDOW_S": 60}
    )
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Token inválido a propósito: lo que se limita es el *intento de
            # conexión*, antes incluso de decodificar el token.
            for _ in range(2):
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    async with aconnect_ws("http://test/api/v1/ws?token=bad", client):
                        pass
                assert exc_info.value.code == 4401

            with pytest.raises(WebSocketDisconnect) as exc_info:
                async with aconnect_ws("http://test/api/v1/ws?token=bad", client):
                    pass

    assert exc_info.value.code == 4429
