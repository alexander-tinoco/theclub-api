"""Phase 7's DoD: a connected WS client receives the result after the POST;
an invalid token closes with 4401. Uses `httpx_ws` (not plain
`httpx.ASGITransport`, which doesn't speak the WebSocket upgrade protocol)
to test this just as async as the rest of the integration suite.
"""

import asyncio
import contextlib
import uuid
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient
from httpx_ws import AsyncWebSocketSession, WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import update
from starlette.datastructures import Address

from app.api.v1.ws import ws_endpoint
from app.config import Settings
from app.main import create_app
from app.models.user import User

pytestmark = pytest.mark.integration


async def _register(client: AsyncClient) -> tuple[str, str]:
    """Returns (access_token, user_id)."""
    email = f"{uuid.uuid4()}@example.com"
    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "a-long-password"}
    )
    token: str = register.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return token, me.json()["id"]


class _FailingAcceptWebSocket:
    """Minimal `WebSocket` double whose `accept()` fails mid-handshake —
    simulates a client that closes the tab or a proxy that cuts the
    connection at that exact moment, without needing a real client that
    disconnects with that exact timing.
    """

    def __init__(self, app: object, token: str) -> None:
        self.app = app
        self.query_params = {"token": token}
        self.client = Address(host="127.0.0.1", port=12345)

    async def accept(self) -> None:
        raise RuntimeError("handshake aborted midway")

    async def close(self, code: int = 1000) -> None:
        pass


async def test_a_failed_accept_does_not_leave_the_connection_slot_occupied(
    integration_settings: Settings,
) -> None:
    """Regression: `accept()` has to be inside the try/finally that
    unregisters the connection — otherwise, a handshake that fails midway
    leaves a `WS_MAX_CONNECTIONS` slot occupied forever.
    """
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token, _ = await _register(client)

        fake_ws = _FailingAcceptWebSocket(app, token)
        with contextlib.suppress(RuntimeError):
            await ws_endpoint(fake_ws)  # type: ignore[arg-type]

        assert len(app.state.ws_connections._connections) == 0


async def test_connected_client_receives_round_settled_after_the_post(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token, _ = await _register(client)
            headers = {"Authorization": f"Bearer {token}"}
            await client.post(
                "/api/v1/wallet/deposit",
                json={"amount_minor": 100_000},
                headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            )

            ws: AsyncWebSocketSession
            async with aconnect_ws(f"http://test/api/v1/ws?token={token}", client) as ws:
                response = await client.post(
                    "/api/v1/roulette/rounds",
                    json={"bets": [{"bet_type": "red", "selection": {}, "stake_minor": 1000}]},
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                )
                assert response.status_code == 201
                round_id = response.json()["round_id"]

                message: dict[str, Any] = await asyncio.wait_for(ws.receive_json(), timeout=5)

    assert message["type"] == "round.settled"
    assert message["round_id"] == round_id


async def test_deposit_triggers_balance_updated(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token, _ = await _register(client)

            ws: AsyncWebSocketSession
            async with aconnect_ws(f"http://test/api/v1/ws?token={token}", client) as ws:
                await client.post(
                    "/api/v1/wallet/deposit",
                    json={"amount_minor": 25_000},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Idempotency-Key": str(uuid.uuid4()),
                    },
                )
                message: dict[str, Any] = await asyncio.wait_for(ws.receive_json(), timeout=5)

    assert message == {"type": "balance.updated", "balance_minor": 25_000, "currency": "EUR"}


async def test_invalid_token_closes_with_4401(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                async with aconnect_ws("http://test/api/v1/ws?token=this-is-not-a-jwt", client):
                    pass

    assert exc_info.value.code == 4401


async def test_no_token_closes_with_4401(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                async with aconnect_ws("http://test/api/v1/ws", client):
                    pass

    assert exc_info.value.code == 4401


async def test_suspended_user_token_closes_with_4401(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token, user_id = await _register(client)

            async with app.state.db_session_factory() as session, session.begin():
                await session.execute(
                    update(User).where(User.id == uuid.UUID(user_id)).values(status="suspended")
                )

            with pytest.raises(WebSocketDisconnect) as exc_info:
                async with aconnect_ws(f"http://test/api/v1/ws?token={token}", client):
                    pass

    assert exc_info.value.code == 4401


async def test_connection_limit_closes_with_4429(integration_settings: Settings) -> None:
    settings = integration_settings.model_copy(update={"WS_MAX_CONNECTIONS": 1})
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token_a, _ = await _register(client)
            token_b, _ = await _register(client)

            async with aconnect_ws(f"http://test/api/v1/ws?token={token_a}", client):
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    async with aconnect_ws(f"http://test/api/v1/ws?token={token_b}", client):
                        pass

    assert exc_info.value.code == 4429


async def test_a_timely_pong_keeps_the_connection_alive(integration_settings: Settings) -> None:
    settings = integration_settings.model_copy(
        update={"WS_HEARTBEAT_INTERVAL_S": 0.2, "WS_HEARTBEAT_TIMEOUT_S": 1.0}
    )
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token, _ = await _register(client)

            ws: AsyncWebSocketSession
            async with aconnect_ws(f"http://test/api/v1/ws?token={token}", client) as ws:
                for _ in range(5):
                    message: dict[str, Any] = await asyncio.wait_for(ws.receive_json(), timeout=1)
                    assert message == {"type": "ping"}
                    await ws.send_json({"type": "pong"})
            # if it got here without a WebSocketDisconnect, the connection
            # survived several heartbeat cycles as long as there was a pong.


async def test_a_zombie_connection_closes_on_heartbeat_timeout(
    integration_settings: Settings,
) -> None:
    settings = integration_settings.model_copy(
        update={"WS_HEARTBEAT_INTERVAL_S": 0.2, "WS_HEARTBEAT_TIMEOUT_S": 0.5}
    )
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token, _ = await _register(client)

            ws: AsyncWebSocketSession
            disconnected = False
            async with aconnect_ws(f"http://test/api/v1/ws?token={token}", client) as ws:
                # a pong is never sent: the server must close on its own.
                # The exception is caught *inside* the block on purpose: if
                # it crosses httpx_ws's context manager `__aexit__`, anyio's
                # internal task group wraps it in an `ExceptionGroup` and
                # `pytest.raises(WebSocketDisconnect)` no longer recognizes it.
                try:
                    for _ in range(20):
                        await asyncio.wait_for(ws.receive_json(), timeout=2)
                except WebSocketDisconnect:
                    disconnected = True

            assert disconnected


async def test_lifespan_shutdown_closes_the_ws_connections(
    integration_settings: Settings,
) -> None:
    """The DoD asks for an "orderly close": when the app shuts down, a
    connected client receives a real close (code 1001), not a TCP
    connection cut with no warning.
    """
    app = create_app(integration_settings)
    lifespan_cm = LifespanManager(app)
    await lifespan_cm.__aenter__()

    transport = ASGIWebSocketTransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token, _ = await _register(client)

        ws_cm: AbstractAsyncContextManager[AsyncWebSocketSession] = aconnect_ws(
            f"http://test/api/v1/ws?token={token}", client
        )
        ws = await ws_cm.__aenter__()
        try:
            await lifespan_cm.__aexit__(None, None, None)
            with pytest.raises(WebSocketDisconnect) as exc_info:
                await asyncio.wait_for(ws.receive_json(), timeout=3)
            assert exc_info.value.code == 1001
        finally:
            with contextlib.suppress(Exception):
                await ws_cm.__aexit__(None, None, None)
