"""Pub/sub en memoria para notificaciones WebSocket, por usuario.

`Broadcaster` separa "quién dispara un evento" (las rutas HTTP, después de
comprometer una transacción) de "quién lo entrega" (el endpoint `/ws`).
`InMemoryBroadcaster` es la única implementación hoy — vive en el proceso,
así que solo entrega a clientes conectados a *esta* instancia. El día que
haga falta correr varias instancias, una `RedisBroadcaster` (PUBLISH/
SUBSCRIBE) implementa el mismo `Protocol` y ningún endpoint cambia.
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

#: Cuántos mensajes sin entregar se guardan por conexión antes de descartar
#: el más viejo. Un cliente con la pestaña en segundo plano no debe frenar a
#: los demás ni acumular memoria sin límite mientras siga conectado.
QUEUE_MAXSIZE = 32

Message = dict[str, Any]


class Broadcaster(Protocol):
    async def publish(self, user_id: uuid.UUID, message: Message) -> None: ...

    def subscribe(
        self, user_id: uuid.UUID
    ) -> AbstractAsyncContextManager[asyncio.Queue[Message]]: ...


class InMemoryBroadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[Message]]] = {}

    async def publish(self, user_id: uuid.UUID, message: Message) -> None:
        for queue in self._subscribers.get(user_id, ()):
            if queue.full():
                # Cliente lento: se descarta el mensaje más viejo en vez de
                # bloquear a quien publica (que es una petición HTTP en curso).
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(message)

    @asynccontextmanager
    async def subscribe(self, user_id: uuid.UUID) -> AsyncIterator[asyncio.Queue[Message]]:
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.setdefault(user_id, set()).add(queue)
        try:
            yield queue
        finally:
            subscribers = self._subscribers.get(user_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    del self._subscribers[user_id]
