"""DoD de la Fase 7: cliente WS conectado recibe el resultado tras el POST;
token inválido cierra 4401. Se usa `httpx_ws` (no `httpx.ASGITransport` a
secas, que no habla el protocolo de upgrade de WebSocket) para poder probar
esto igual de async que el resto de la suite de integración.
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
    """Devuelve (access_token, user_id)."""
    email = f"{uuid.uuid4()}@example.com"
    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "contraseña-larga"}
    )
    token: str = register.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return token, me.json()["id"]


class _FailingAcceptWebSocket:
    """Doble mínimo de `WebSocket` cuyo `accept()` falla a medio handshake
    — simula un cliente que cierra la pestaña o un proxy que corta la
    conexión justo en ese momento, sin necesitar un cliente real que se
    desconecte con ese timing exacto.
    """

    def __init__(self, app: object, token: str) -> None:
        self.app = app
        self.query_params = {"token": token}
        self.client = Address(host="127.0.0.1", port=12345)

    async def accept(self) -> None:
        raise RuntimeError("handshake abortado a mitad de camino")

    async def close(self, code: int = 1000) -> None:
        pass


async def test_si_falla_el_accept_no_deja_el_cupo_de_conexion_ocupado(
    integration_settings: Settings,
) -> None:
    """Regresión: `accept()` tiene que estar dentro del try/finally que
    desregistra la conexión — si no, un handshake que falla a medio camino
    deja el cupo de `WS_MAX_CONNECTIONS` ocupado para siempre.
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


async def test_cliente_conectado_recibe_round_settled_tras_el_post(
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


async def test_deposito_dispara_balance_updated(integration_settings: Settings) -> None:
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


async def test_token_invalido_cierra_con_4401(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                async with aconnect_ws("http://test/api/v1/ws?token=esto-no-es-un-jwt", client):
                    pass

    assert exc_info.value.code == 4401


async def test_sin_token_cierra_con_4401(integration_settings: Settings) -> None:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                async with aconnect_ws("http://test/api/v1/ws", client):
                    pass

    assert exc_info.value.code == 4401


async def test_token_de_usuario_suspendido_cierra_con_4401(
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


async def test_limite_de_conexiones_cierra_con_4429(integration_settings: Settings) -> None:
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


async def test_pong_a_tiempo_mantiene_la_conexion_viva(integration_settings: Settings) -> None:
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
            # si llegó hasta acá sin WebSocketDisconnect, la conexión
            # sobrevivió a varios ciclos de heartbeat mientras hubo pong.


async def test_conexion_zombie_se_cierra_por_heartbeat_timeout(
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
                # nunca se manda pong: el servidor debe cerrar solo. La
                # excepción se atrapa *dentro* del bloque a propósito: si
                # cruza el `__aexit__` del context manager de httpx_ws, el
                # task group interno de anyio la envuelve en un
                # `ExceptionGroup` y `pytest.raises(WebSocketDisconnect)`
                # ya no la reconoce.
                try:
                    for _ in range(20):
                        await asyncio.wait_for(ws.receive_json(), timeout=2)
                except WebSocketDisconnect:
                    disconnected = True

            assert disconnected


async def test_el_apagado_del_lifespan_cierra_las_conexiones_ws(
    integration_settings: Settings,
) -> None:
    """El DoD pide 'cierre ordenado': al apagar la app, un cliente conectado
    recibe un cierre de verdad (código 1001), no un TCP cortado sin avisar.
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
