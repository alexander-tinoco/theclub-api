"""Phase 8's DoD: a global handler that never leaks internals, correlated
logging (`request_id`/`user_id`) with a canonical line, restricted CORS,
global rate limiting (including `/ws`'s, kept separate because of
`slowapi`'s real limitation with WebSockets), and a body size limit.

To capture the canonical line, a handler is attached directly to the
`"canonical"` logger instead of using `caplog` — see
`test_alembic_logging.py` for why (a real bug in `alembic/env.py` that
surfaced while writing this file, which nullified any handler pytest tried
to attach on its own).
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
        "/api/v1/auth/register", json={"email": email, "password": "a-long-password"}
    )
    token: str = register.json()["access_token"]
    return token


async def test_unmapped_exception_responds_500_without_leaking_the_real_message(
    integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import wallet as wallet_service

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database credentials: hunter2")

    monkeypatch.setattr(wallet_service, "get_balance", _boom)

    collector = _CollectingHandler()
    canonical_logger = logging.getLogger("canonical")
    canonical_logger.addHandler(collector)

    app = create_app(integration_settings)
    try:
        async with LifespanManager(app):
            # `raise_app_exceptions=False`: Starlette re-raises the
            # exception after sending the response (so the ASGI server logs
            # it) — without this flag, httpx raises it again here and we'd
            # never get to see the real response the client did receive.
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
    assert response.json() == {"detail": "internal error"}
    assert "hunter2" not in response.text
    # Regression: Starlette routes `Exception`/500 handlers registered with
    # `add_exception_handler` to `ServerErrorMiddleware`, the layer
    # MOST outer of all — outside CORS and RequestContextMiddleware. A
    # response generated there wouldn't carry these headers, and the
    # canonical line would end up with `status_code: null`. That's why the
    # catch-all is middleware (`UnhandledExceptionMiddleware`), not an
    # exception handler — these asserts are what breaks if that ever
    # changes again.
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "x-request-id" in response.headers

    canonical_dicts = [getattr(r, "canonical", None) for r in collector.records]
    balance_lines = [
        c for c in canonical_dicts if c is not None and c.get("path") == "/api/v1/wallet/balance"
    ]
    assert len(balance_lines) == 1
    assert balance_lines[0]["status_code"] == 500


async def test_http_response_includes_x_request_id(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert "x-request-id" in response.headers
    uuid.UUID(response.headers["x-request-id"])  # doesn't raise if it's a valid UUID


async def test_a_posts_canonical_line_includes_what_bind_canonical_added(
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


async def test_metrics_exposes_prometheus_format_and_reflects_activity(
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


async def test_oversized_body_responds_413(integration_settings: Settings) -> None:
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


async def test_ws_connect_rate_limit_closes_after_too_many_attempts(
    integration_settings: Settings,
) -> None:
    settings = integration_settings.model_copy(
        update={"WS_CONNECT_RATE_LIMIT_ATTEMPTS": 2, "WS_CONNECT_RATE_LIMIT_WINDOW_S": 60}
    )
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Invalid token on purpose: what's being limited is the
            # *connection attempt*, before the token is even decoded.
            for _ in range(2):
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    async with aconnect_ws("http://test/api/v1/ws?token=bad", client):
                        pass
                assert exc_info.value.code == 4401

            with pytest.raises(WebSocketDisconnect) as exc_info:
                async with aconnect_ws("http://test/api/v1/ws?token=bad", client):
                    pass

    assert exc_info.value.code == 4429
