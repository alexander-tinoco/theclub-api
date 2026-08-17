"""Registry of active WS connections: for the global connection limit and
to be able to close them all cleanly when the app shuts down — without
this, a `docker compose down` would just cut the TCP connection with no
warning to the client.
"""

import contextlib

from fastapi import WebSocket

from app.infra.metrics import ws_connections_active, ws_connections_total


class ConnectionRegistry:
    def __init__(self, *, max_connections: int) -> None:
        self._max_connections = max_connections
        self._connections: set[WebSocket] = set()

    def try_register(self, websocket: WebSocket) -> bool:
        if len(self._connections) >= self._max_connections:
            return False
        self._connections.add(websocket)
        ws_connections_active.inc()
        ws_connections_total.inc()
        return True

    def unregister(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.discard(websocket)
            ws_connections_active.dec()

    async def close_all(self, *, code: int = 1001) -> None:
        """Sends the close frame to every active connection. Doesn't wait
        for each one's handler to finish unwinding on the server side —
        that's resolved by each `/ws` when it notices the disconnect.
        """
        connections = list(self._connections)
        self._connections.clear()
        ws_connections_active.dec(len(connections))
        for websocket in connections:
            with contextlib.suppress(Exception):
                await websocket.close(code=code)
