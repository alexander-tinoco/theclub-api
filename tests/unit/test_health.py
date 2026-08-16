import pytest
from fastapi import FastAPI
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


async def test_ready_sin_checks_registrados(client: AsyncClient) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {}}


async def test_ready_con_check_que_pasa(app: FastAPI, client: AsyncClient) -> None:
    async def ok() -> None:
        return None

    registry: ReadinessRegistry = app.state.readiness
    registry.register("database", ok)

    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


async def test_ready_devuelve_503_si_un_check_falla(app: FastAPI, client: AsyncClient) -> None:
    async def ok() -> None:
        return None

    async def boom() -> None:
        raise ConnectionError("sin ruta al broker")

    registry: ReadinessRegistry = app.state.readiness
    registry.register("database", ok)
    registry.register("kafka", boom)

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "kafka": "fail"},
    }


async def test_registrar_dos_veces_el_mismo_check_es_error(app: FastAPI) -> None:
    async def ok() -> None:
        return None

    registry: ReadinessRegistry = app.state.readiness
    registry.register("database", ok)

    with pytest.raises(ValueError, match="database"):
        registry.register("database", ok)
