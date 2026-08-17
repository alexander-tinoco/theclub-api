import pytest
from httpx import AsyncClient

from app.api.health import ReadinessRegistry

pytestmark = pytest.mark.unit


async def test_health_no_toca_dependencias(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["env"] == "test"
    assert body["name"] == "theclub-api"


# El comportamiento real de /ready (con el check de Postgres que create_app()
# registra siempre) depende de infraestructura real y se prueba en
# tests/integration/test_health_ready.py. Aquí se prueba el mecanismo de
# ReadinessRegistry en aislamiento, con un registro propio, no el de la app.


async def test_registry_sin_checks_registrados() -> None:
    registry = ReadinessRegistry()

    assert await registry.run() == {}


async def test_registry_con_check_que_pasa() -> None:
    async def ok() -> None:
        return None

    registry = ReadinessRegistry()
    registry.register("database", ok)

    assert await registry.run() == {"database": "ok"}


async def test_registry_reporta_fail_sin_tumbar_los_demas() -> None:
    async def ok() -> None:
        return None

    async def boom() -> None:
        raise ConnectionError("sin ruta al broker")

    registry = ReadinessRegistry()
    registry.register("database", ok)
    registry.register("kafka", boom)

    assert await registry.run() == {"database": "ok", "kafka": "fail"}


async def test_registrar_dos_veces_el_mismo_check_es_error() -> None:
    async def ok() -> None:
        return None

    registry = ReadinessRegistry()
    registry.register("database", ok)

    with pytest.raises(ValueError, match="database"):
        registry.register("database", ok)
