"""ASGI middlewares that don't fit in `request_context.py` (that one is
about correlation/observability; this is actual hardening).
"""

from starlette.types import ASGIApp, Receive, Scope, Send


class MaxBodySizeMiddleware:
    """Rejects with 413 before the body reaches Pydantic if `Content-Length`
    exceeds the limit. Only gates `scope["type"] == "http"` on purpose: a
    WebSocket handshake has no body to limit here.

    Known limitation: a client that lies about `Content-Length`, or sends
    the body with *chunked transfer encoding* without that header, slips
    past it. In a real deployment this is reinforced at the proxy/load
    balancer (nginx's `client_max_body_size`, for example) — this
    middleware is the app's own defense, not the only line of defense.
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
                    "body": b'{"detail":"request body too large"}',
                }
            )
            return

        await self.app(scope, receive, send)
