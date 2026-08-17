import asyncio
import uuid

import pytest

from app.ws.broadcaster import QUEUE_MAXSIZE, InMemoryBroadcaster
from app.ws.connections import ConnectionRegistry

pytestmark = pytest.mark.unit


async def test_publish_with_no_subscribers_does_nothing() -> None:
    broadcaster = InMemoryBroadcaster()

    await broadcaster.publish(uuid.uuid4(), {"type": "round.settled"})


async def test_a_subscriber_receives_what_is_published_for_their_user() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id) as queue:
        await broadcaster.publish(user_id, {"type": "round.settled"})

        message = await asyncio.wait_for(queue.get(), timeout=1)

    assert message == {"type": "round.settled"}


async def test_does_not_receive_messages_for_another_user() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id) as queue:
        await broadcaster.publish(other_user_id, {"type": "round.settled"})

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.1)


async def test_several_connections_for_the_same_user_receive_the_same_message() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id) as queue_a, broadcaster.subscribe(user_id) as queue_b:
        await broadcaster.publish(user_id, {"type": "balance.updated"})

        message_a = await asyncio.wait_for(queue_a.get(), timeout=1)
        message_b = await asyncio.wait_for(queue_b.get(), timeout=1)

    assert message_a == {"type": "balance.updated"}
    assert message_b == {"type": "balance.updated"}


async def test_unsubscribing_stops_delivery_and_cleans_up_the_internal_registry() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id):
        pass

    assert user_id not in broadcaster._subscribers

    # publishing after nobody is left must not raise
    await broadcaster.publish(user_id, {"type": "round.settled"})


async def test_a_full_queue_drops_the_oldest_message_instead_of_blocking() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id) as queue:
        for i in range(QUEUE_MAXSIZE + 5):
            await broadcaster.publish(user_id, {"seq": i})

        assert queue.qsize() == QUEUE_MAXSIZE
        first = await queue.get()
        # the oldest ones were dropped: the first one left isn't 0
        assert first["seq"] > 0


class _FakeWebSocket:
    """Minimal double: `ConnectionRegistry` only needs it to be hashable
    and to have an awaitable `close(code=...)` — not worth setting up a
    real Starlette `WebSocket` for this.
    """

    def __init__(self) -> None:
        self.closed_with: int | None = None

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


async def test_connection_registry_respects_the_max_limit() -> None:
    registry = ConnectionRegistry(max_connections=1)
    first = _FakeWebSocket()
    second = _FakeWebSocket()

    assert registry.try_register(first) is True  # type: ignore[arg-type]
    assert registry.try_register(second) is False  # type: ignore[arg-type]


async def test_connection_registry_frees_the_slot_on_unregister() -> None:
    registry = ConnectionRegistry(max_connections=1)
    first = _FakeWebSocket()
    second = _FakeWebSocket()

    registry.try_register(first)  # type: ignore[arg-type]
    registry.unregister(first)  # type: ignore[arg-type]

    assert registry.try_register(second) is True  # type: ignore[arg-type]


async def test_close_all_closes_every_registered_connection() -> None:
    registry = ConnectionRegistry(max_connections=10)
    connections = [_FakeWebSocket() for _ in range(3)]
    for ws in connections:
        registry.try_register(ws)  # type: ignore[arg-type]

    await registry.close_all(code=1001)

    assert all(ws.closed_with == 1001 for ws in connections)


async def test_close_all_with_a_connection_that_fails_to_close_does_not_stop_the_rest() -> None:
    registry = ConnectionRegistry(max_connections=10)

    class _BrokenWebSocket(_FakeWebSocket):
        async def close(self, code: int = 1000) -> None:
            raise RuntimeError("already closed")

    broken = _BrokenWebSocket()
    healthy = _FakeWebSocket()
    registry.try_register(broken)  # type: ignore[arg-type]
    registry.try_register(healthy)  # type: ignore[arg-type]

    await registry.close_all()

    assert healthy.closed_with == 1001
