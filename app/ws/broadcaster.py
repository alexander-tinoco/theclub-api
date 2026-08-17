"""In-memory pub/sub for per-user WebSocket notifications.

`Broadcaster` separates "who triggers an event" (the HTTP routes, after
committing a transaction) from "who delivers it" (the `/ws` endpoint).
`InMemoryBroadcaster` is the only implementation today — it lives in the
process, so it only delivers to clients connected to *this* instance. The
day running several instances becomes necessary, a `RedisBroadcaster`
(PUBLISH/SUBSCRIBE) implements the same `Protocol` and no endpoint changes.
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

#: How many undelivered messages are kept per connection before the oldest
#: gets dropped. A client with the tab in the background shouldn't slow
#: down anyone else, nor accumulate unbounded memory while it stays connected.
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
                # Slow client: drop the oldest message instead of blocking
                # the publisher (which is an HTTP request in progress).
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
