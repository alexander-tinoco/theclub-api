"""Middlewares ASGI que no encajan en `request_context.py` (esa es sobre
correlación/observabilidad; esto es endurecimiento de verdad).
"""

from starlette.types import ASGIApp, Receive, Scope, Send


class MaxBodySizeMiddleware:
    """Rechaza con 413 antes de que el body llegue a Pydantic si
    `Content-Length` supera el límite. Gatea solo `scope["type"] == "http"`
    a propósito: el handshake de WebSocket no tiene body que limitar aquí.

    Límite conocido: un cliente que mienta el `Content-Length`, o que mande
    el body con *chunked transfer encoding* sin ese header, lo esquiva. En
    un despliegue real esto se refuerza en el proxy/load balancer
    (`client_max_body_size` de nginx, por ejemplo) — este middleware es la
    defensa de la app misma, no la única línea de defensa.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None and int(content_length) > self.max_body_bytes:
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"cuerpo de la petici\\u00f3n demasiado grande"}',
                }
            )
            return

        await self.app(scope, receive, send)
