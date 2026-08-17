"""Correlación de logs por petición/conexión, y líneas canónicas.

Una línea canónica es *una sola* línea estructurada por petición HTTP (o por
conexión WS, al cerrarse) con todo lo que importó: quién, qué endpoint, qué
pasó, cuánto tardó. Sustituye a ir juntando la historia de una petición a
mano desde varios `logger.info` sueltos y dispersos por el código — acá el
handler solo llama a `bind_canonical(**campos)` cuando tiene algo que decir
y el middleware junta todo al final.

`contextvars` (no un objeto pasado a mano por cada función) porque así
cualquier capa —ruta, servicio, repositorio— puede enriquecer la línea sin
que su firma tenga que aceptar un parámetro de contexto que no le interesa
para nada más que loggear.
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
    """Añade campos a la línea canónica de la petición/conexión actual.

    No hace nada (ni levanta) si se llama fuera de una petición real — por
    ejemplo, un test unitario de un servicio que no pasa por el middleware.
    El logging de negocio no debería depender de estar montado en un
    request HTTP para funcionar.
    """
    fields_dict = _canonical_fields_var.get()
    if fields_dict is not None:
        fields_dict.update(fields)


class RequestContextMiddleware:
    """ASGI puro, no `BaseHTTPMiddleware`: Starlette salta por completo
    `BaseHTTPMiddleware` para conexiones WebSocket, así que con esa base
    `/ws` se quedaría sin `request_id` ni línea canónica. Con ASGI puro,
    ambos scopes ("http" y "websocket") pasan por aquí igual.
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
                # El path *crudo* de la petición, no un patrón con nombres
                # (`/rounds/{id}`): ninguna ruta de esta API usa parámetros
                # de path hoy, así que no explota la cardinalidad de las
                # métricas — si eso cambiara, esto tendría que resolver la
                # ruta declarada en vez del path tal cual.
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
