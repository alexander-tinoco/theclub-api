"""GET /ws — notificaciones en vivo: `round.settled` y `balance.updated`.

Autenticación por query param (`?token=<jwt>`) porque el navegador no deja
mandar el header `Authorization` en el handshake de un WebSocket. Es un
trade-off real y documentado, no un descuido: el token puede acabar en logs
de acceso de un proxy intermedio si no se filtra explícitamente — aceptable
para este MVP, a reconsiderar (mensaje de auth como primer frame, en vez de
query param) si esto se produce de verdad alguna vez.

Los endpoints WS de FastAPI no pueden reutilizar `SessionDep`/`SettingsDep`
tal cual: esas dependencias declaran un parámetro `Request`, y en una ruta
WebSocket eso falla en tiempo de ejecución (`request` no existe en ese
scope). Por eso aquí se lee todo de `websocket.app.state` directamente.
"""

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket

from app.config import Settings
from app.infra.security import InvalidTokenError, TokenExpiredError, decode_access_token
from app.repositories.users import UserRepository
from app.ws.broadcaster import Broadcaster
from app.ws.connections import ConnectionRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

#: Códigos de cierre en el rango de aplicación (4000-4999, libre para uso
#: propio según RFC 6455) — el cliente los distingue de un cierre normal.
CLOSE_UNAUTHORIZED = 4401
CLOSE_TOO_MANY_CONNECTIONS = 4429


async def _authenticate(websocket: WebSocket) -> uuid.UUID | None:
    """None si el token falta, es inválido, expiró, o el usuario no existe
    o está suspendido — todos los casos cierran igual (4401), sin filtrar
    cuál fue el motivo exacto.
    """
    settings: Settings = websocket.app.state.settings
    token = websocket.query_params.get("token")
    if not token:
        return None

    try:
        user_id = decode_access_token(
            token, secret=settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALGORITHM
        )
    except (InvalidTokenError, TokenExpiredError) as exc:
        # `except (A, B):` sin nombre dispara un bug real de `ruff format`
        # (reescribe a `except A, B:`, sintaxis de Python 2) en la versión
        # instalada — dejar el nombre lo evita, y de paso sirve para loggear.
        logger.debug("Token de WS rechazado: %s", exc)
        return None

    session_factory = websocket.app.state.db_session_factory
    async with session_factory() as session:
        user = await UserRepository(session).get_by_id(user_id)
    if user is None or user.status == "suspended":
        return None
    return user_id


async def _sender(
    websocket: WebSocket, queue: asyncio.Queue[dict[str, Any]], *, interval_s: float
) -> None:
    """Reenvía cada mensaje publicado para este usuario. Si no hay nada que
    reenviar dentro de `interval_s`, manda un ping — así el heartbeat no
    compite con los mensajes reales, se intercalan en el mismo bucle.
    """
    while True:
        try:
            message = await asyncio.wait_for(queue.get(), timeout=interval_s)
        except TimeoutError:
            await websocket.send_json({"type": "ping"})
        else:
            await websocket.send_json(message)


async def _receiver(websocket: WebSocket, *, timeout_s: float) -> None:
    """Cualquier frame del cliente (típicamente `{"type":"pong"}`) cuenta
    como señal de vida. Si no llega ninguno en `timeout_s`, se asume una
    conexión zombie (p. ej. un móvil que se durmió sin cerrar el TCP) y se
    deja que la excepción termine esta tarea — el endpoint cierra al notarlo.
    """
    while True:
        await asyncio.wait_for(websocket.receive_json(), timeout=timeout_s)


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    broadcaster: Broadcaster = websocket.app.state.ws_broadcaster
    registry: ConnectionRegistry = websocket.app.state.ws_connections

    user_id = await _authenticate(websocket)
    if user_id is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    if not registry.try_register(websocket):
        await websocket.close(code=CLOSE_TOO_MANY_CONNECTIONS)
        return

    try:
        # `accept()` va *dentro* del try a propósito: si el handshake falla
        # a medio camino (el cliente cierra la pestaña, un proxy corta la
        # conexión), el cupo reservado por `try_register` de todos modos se
        # libera en el `finally` — si no, cada handshake fallido deja un
        # hueco fantasma en `WS_MAX_CONNECTIONS` para siempre.
        await websocket.accept()
        async with broadcaster.subscribe(user_id) as queue:
            sender = asyncio.create_task(
                _sender(websocket, queue, interval_s=settings.WS_HEARTBEAT_INTERVAL_S)
            )
            receiver = asyncio.create_task(
                _receiver(websocket, timeout_s=settings.WS_HEARTBEAT_TIMEOUT_S)
            )
            try:
                await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                sender.cancel()
                receiver.cancel()
                await asyncio.gather(sender, receiver, return_exceptions=True)
    finally:
        registry.unregister(websocket)
        with contextlib.suppress(Exception):
            await websocket.close()
