import asyncio
import uuid

import pytest

from app.ws.broadcaster import QUEUE_MAXSIZE, InMemoryBroadcaster
from app.ws.connections import ConnectionRegistry

pytestmark = pytest.mark.unit


async def test_publish_sin_ningun_suscriptor_no_hace_nada() -> None:
    broadcaster = InMemoryBroadcaster()

    await broadcaster.publish(uuid.uuid4(), {"type": "round.settled"})


async def test_un_suscriptor_recibe_lo_publicado_para_su_usuario() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id) as queue:
        await broadcaster.publish(user_id, {"type": "round.settled"})

        message = await asyncio.wait_for(queue.get(), timeout=1)

    assert message == {"type": "round.settled"}


async def test_no_recibe_mensajes_de_otro_usuario() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id) as queue:
        await broadcaster.publish(other_user_id, {"type": "round.settled"})

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.1)


async def test_varias_conexiones_del_mismo_usuario_reciben_el_mismo_mensaje() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id) as queue_a, broadcaster.subscribe(user_id) as queue_b:
        await broadcaster.publish(user_id, {"type": "balance.updated"})

        message_a = await asyncio.wait_for(queue_a.get(), timeout=1)
        message_b = await asyncio.wait_for(queue_b.get(), timeout=1)

    assert message_a == {"type": "balance.updated"}
    assert message_b == {"type": "balance.updated"}


async def test_desuscribirse_deja_de_recibir_y_limpia_el_registro_interno() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id):
        pass

    assert user_id not in broadcaster._subscribers

    # publicar después de que no queda nadie no debe levantar
    await broadcaster.publish(user_id, {"type": "round.settled"})


async def test_una_queue_llena_descarta_el_mensaje_mas_viejo_no_bloquea() -> None:
    broadcaster = InMemoryBroadcaster()
    user_id = uuid.uuid4()

    async with broadcaster.subscribe(user_id) as queue:
        for i in range(QUEUE_MAXSIZE + 5):
            await broadcaster.publish(user_id, {"seq": i})

        assert queue.qsize() == QUEUE_MAXSIZE
        first = await queue.get()
        # se descartaron los más viejos: el primero que queda no es el 0
        assert first["seq"] > 0


class _FakeWebSocket:
    """Doble mínimo: `ConnectionRegistry` solo necesita que sea hasheable y
    que tenga un `close(code=...)` awaitable — no vale la pena montar un
    `WebSocket` real de Starlette para esto.
    """

    def __init__(self) -> None:
        self.closed_with: int | None = None

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


async def test_connection_registry_respeta_el_limite_maximo() -> None:
    registry = ConnectionRegistry(max_connections=1)
    first = _FakeWebSocket()
    second = _FakeWebSocket()

    assert registry.try_register(first) is True  # type: ignore[arg-type]
    assert registry.try_register(second) is False  # type: ignore[arg-type]


async def test_connection_registry_libera_el_cupo_al_desregistrar() -> None:
    registry = ConnectionRegistry(max_connections=1)
    first = _FakeWebSocket()
    second = _FakeWebSocket()

    registry.try_register(first)  # type: ignore[arg-type]
    registry.unregister(first)  # type: ignore[arg-type]

    assert registry.try_register(second) is True  # type: ignore[arg-type]


async def test_close_all_cierra_todas_las_conexiones_registradas() -> None:
    registry = ConnectionRegistry(max_connections=10)
    connections = [_FakeWebSocket() for _ in range(3)]
    for ws in connections:
        registry.try_register(ws)  # type: ignore[arg-type]

    await registry.close_all(code=1001)

    assert all(ws.closed_with == 1001 for ws in connections)


async def test_close_all_con_una_conexion_que_falla_al_cerrar_no_frena_al_resto() -> None:
    registry = ConnectionRegistry(max_connections=10)

    class _BrokenWebSocket(_FakeWebSocket):
        async def close(self, code: int = 1000) -> None:
            raise RuntimeError("ya estaba cerrada")

    broken = _BrokenWebSocket()
    healthy = _FakeWebSocket()
    registry.try_register(broken)  # type: ignore[arg-type]
    registry.try_register(healthy)  # type: ignore[arg-type]

    await registry.close_all()

    assert healthy.closed_with == 1001
