"""GET /ws — live notifications: `round.settled` and `balance.updated`.

Authentication via query param (`?token=<jwt>`) because the browser won't
let you send the `Authorization` header during a WebSocket handshake. It's
a real, documented trade-off, not an oversight: the token can end up in an
intermediate proxy's access logs if it isn't explicitly filtered out —
acceptable for this MVP, worth reconsidering (an auth message as the first
frame, instead of a query param) if this ever becomes a real concern.

FastAPI's WS endpoints can't reuse `SessionDep`/`SettingsDep` as-is: those
dependencies declare a `Request` parameter, and on a WebSocket route that
fails at runtime (`request` doesn't exist in that scope). That's why
everything here is read straight from `websocket.app.state`.
"""

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket

from app.api.request_context import bind_canonical, user_id_var
from app.config import Settings
from app.infra.security import InvalidTokenError, TokenExpiredError, decode_access_token
from app.repositories.users import UserRepository
from app.ws.broadcaster import Broadcaster
from app.ws.connections import ConnectionRegistry
from app.ws.rate_limit import WsConnectRateLimiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

#: Close codes in the application range (4000-4999, free for private use
#: per RFC 6455) — the client tells them apart from a normal close.
CLOSE_UNAUTHORIZED = 4401
#: Covers two different limits (too many active connections, too many
#: connection attempts in the window) — the client doesn't need to
#: distinguish which of the two triggered the close, in both cases the
#: response is to wait.
CLOSE_TOO_MANY_CONNECTIONS = 4429


async def _authenticate(websocket: WebSocket) -> uuid.UUID | None:
    """None if the token is missing, invalid, expired, or the user doesn't
    exist or is suspended — every case closes the same way (4401), without
    leaking which exact reason it was.
    """
    settings: Settings = websocket.app.state.settings
    token = websocket.query_params.get("token")
    if not token:
        return None

    try:
        user_id = decode_access_token(
            token,
            secret=settings.JWT_SECRET.get_secret_value(),
            algorithm=settings.JWT_ALGORITHM,
            previous_secrets=[s.get_secret_value() for s in settings.JWT_PREVIOUS_SECRETS],
        )
    except (InvalidTokenError, TokenExpiredError) as exc:
        # `except (A, B):` with no name triggers a real bug in the
        # installed version of `ruff format` (it rewrites it to
        # `except A, B:`, Python 2 syntax) — keeping the name avoids it,
        # and doubles as something to log.
        logger.debug("WS token rejected: %s", exc)
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
    """Forwards every message published for this user. If there's nothing
    to forward within `interval_s`, sends a ping — that way the heartbeat
    doesn't compete with real messages, they're interleaved in the same loop.
    """
    while True:
        try:
            message = await asyncio.wait_for(queue.get(), timeout=interval_s)
        except TimeoutError:
            await websocket.send_json({"type": "ping"})
        else:
            await websocket.send_json(message)


async def _receiver(websocket: WebSocket, *, timeout_s: float) -> None:
    """Any frame from the client (typically `{"type":"pong"}`) counts as a
    sign of life. If none arrives within `timeout_s`, a zombie connection is
    assumed (e.g. a phone that fell asleep without closing the TCP
    connection) and the exception is left to end this task — the endpoint
    closes once it notices.
    """
    while True:
        await asyncio.wait_for(websocket.receive_json(), timeout=timeout_s)


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    broadcaster: Broadcaster = websocket.app.state.ws_broadcaster
    registry: ConnectionRegistry = websocket.app.state.ws_connections
    connect_rate_limiter: WsConnectRateLimiter = websocket.app.state.ws_connect_rate_limiter

    client_ip = websocket.client.host if websocket.client else "unknown"
    # Before anything else, not even decoding the token: `slowapi` doesn't
    # cover this endpoint (see the docstring in `ws/rate_limit.py`), so a
    # reconnect loop with a broken token shouldn't even reach the DB.
    if not await connect_rate_limiter.allow(client_ip):
        await websocket.close(code=CLOSE_TOO_MANY_CONNECTIONS)
        return

    user_id = await _authenticate(websocket)
    if user_id is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    user_id_var.set(str(user_id))
    bind_canonical(user_id=str(user_id))

    if not registry.try_register(websocket):
        await websocket.close(code=CLOSE_TOO_MANY_CONNECTIONS)
        return

    close_reason = "disconnected_or_shutdown"
    try:
        # `accept()` goes *inside* the try on purpose: if the handshake
        # fails partway through (the client closes the tab, a proxy cuts
        # the connection), the slot reserved by `try_register` still gets
        # freed in the `finally` — otherwise, every failed handshake would
        # leave a phantom hole in `WS_MAX_CONNECTIONS` forever.
        await websocket.accept()
        async with broadcaster.subscribe(user_id) as queue:
            sender = asyncio.create_task(
                _sender(websocket, queue, interval_s=settings.WS_HEARTBEAT_INTERVAL_S)
            )
            receiver = asyncio.create_task(
                _receiver(websocket, timeout_s=settings.WS_HEARTBEAT_TIMEOUT_S)
            )
            try:
                done, _pending = await asyncio.wait(
                    {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                # The real reason for the close, for the canonical line —
                # doesn't change what we send the client (always a clean
                # close), only what ends up in the log.
                if receiver in done and isinstance(receiver.exception(), TimeoutError):
                    close_reason = "heartbeat_timeout"
            finally:
                sender.cancel()
                receiver.cancel()
                await asyncio.gather(sender, receiver, return_exceptions=True)
    finally:
        registry.unregister(websocket)
        bind_canonical(close_reason=close_reason)
        with contextlib.suppress(Exception):
            await websocket.close()
