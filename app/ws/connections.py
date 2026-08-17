"""Registro de conexiones WS activas: para el límite global de conexiones y
para poder cerrarlas todas de forma ordenada cuando la app se apaga — sin
esto, un `docker compose down` simplemente corta el TCP sin avisar al
cliente.
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
        """Manda el frame de cierre a cada conexión activa. No espera a que
        el handler de cada una termine de desenrollarse del lado del
        servidor — eso lo resuelve cada `/ws` al notar la desconexión.
        """
        connections = list(self._connections)
        self._connections.clear()
        ws_connections_active.dec(len(connections))
        for websocket in connections:
            with contextlib.suppress(Exception):
                await websocket.close(code=code)
