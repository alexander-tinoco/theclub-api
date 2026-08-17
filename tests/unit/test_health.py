import pytest
from httpx import AsyncClient

from app.api.health import ReadinessRegistry

pytestmark = pytest.mark.unit


async def test_health_touches_no_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["env"] == "test"
    assert body["name"] == "theclub-api"


# /ready's real behavior (with the Postgres check that create_app() always
# registers) depends on real infrastructure and is tested in
# tests/integration/test_health_ready.py. Here the ReadinessRegistry
# mechanism is tested in isolation, with its own registry, not the app's.


async def test_registry_with_no_checks_registered() -> None:
    registry = ReadinessRegistry()

    assert await registry.run() == {}


async def test_registry_with_a_passing_check() -> None:
    async def ok() -> None:
        return None

    registry = ReadinessRegistry()
    registry.register("database", ok)

    assert await registry.run() == {"database": "ok"}


async def test_registry_reports_fail_without_bringing_down_the_rest() -> None:
    async def ok() -> None:
        return None

    async def boom() -> None:
        raise ConnectionError("no route to broker")

    registry = ReadinessRegistry()
    registry.register("database", ok)
    registry.register("kafka", boom)

    assert await registry.run() == {"database": "ok", "kafka": "fail"}


async def test_registering_the_same_check_twice_is_an_error() -> None:
    async def ok() -> None:
        return None

    registry = ReadinessRegistry()
    registry.register("database", ok)

    with pytest.raises(ValueError, match="database"):
        registry.register("database", ok)
