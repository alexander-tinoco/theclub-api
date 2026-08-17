"""Per-request/per-connection log correlation, and canonical lines.

A canonical line is *a single* structured line per HTTP request (or per WS
connection, on close) with everything that mattered: who, which endpoint,
what happened, how long it took. It replaces piecing together a request's
story by hand from several loose, scattered `logger.info` calls — here the
handler just calls `bind_canonical(**fields)` whenever it has something to
say, and the middleware assembles everything at the end.

`contextvars` (not an object passed by hand through every function)
because that way any layer — route, service, repository — can enrich the
line without its signature having to accept a context parameter it cares
about for nothing but logging.
"""

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infra.metrics import http_request_duration_seconds, http_requests_total

canonical_logger = logging.getLogger("canonical")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
_canonical_fields_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "canonical_fields", default=None
)


def bind_canonical(**fields: Any) -> None:
    """Adds fields to the current request/connection's canonical line.

    Does nothing (doesn't raise) if called outside a real request — for
    example, a unit test of a service that doesn't go through the
    middleware. Business logging shouldn't have to be mounted on an HTTP
    request to work.
    """
    fields_dict = _canonical_fields_var.get()
    if fields_dict is not None:
        fields_dict.update(fields)


class RequestContextMiddleware:
    """Pure ASGI, not `BaseHTTPMiddleware`: Starlette skips
    `BaseHTTPMiddleware` entirely for WebSocket connections, so with that
    base `/ws` would end up with no `request_id` or canonical line. With
    pure ASGI, both scopes ("http" and "websocket") go through here the same way.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        request_id_token = request_id_var.set(request_id)
        fields_token = _canonical_fields_var.set({})
        started_at = time.monotonic()
        status_code: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers.append("X-Request-ID", request_id)
            await send(message)

        try:
            if scope["type"] == "http":
                await self.app(scope, receive, send_wrapper)
            else:
                await self.app(scope, receive, send)
        finally:
            duration_ms = round((time.monotonic() - started_at) * 1000, 2)
            fields = _canonical_fields_var.get() or {}
            line: dict[str, Any] = {
                "request_id": request_id,
                "user_id": user_id_var.get(),
                "type": scope["type"],
                "duration_ms": duration_ms,
                **fields,
            }
            if scope["type"] == "http":
                method = scope["method"]
                # The request's *raw* path, not a named pattern
                # (`/rounds/{id}`): no route in this API uses path
                # parameters today, so this doesn't blow up metric
                # cardinality — if that changed, this would need to resolve
                # the declared route instead of the raw path.
                path = scope["path"]
                line["method"] = method
                line["path"] = path
                line["status_code"] = status_code
                http_requests_total.labels(
                    method=method, path=path, status_code=str(status_code)
                ).inc()
                http_request_duration_seconds.labels(method=method, path=path).observe(
                    duration_ms / 1000
                )
            canonical_logger.info("request_completed", extra={"canonical": line})
            request_id_var.reset(request_id_token)
            _canonical_fields_var.reset(fields_token)
