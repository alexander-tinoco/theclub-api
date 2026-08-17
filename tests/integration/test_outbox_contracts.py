"""Los eventos que `place_bet`/`deposit` escriben de verdad en el outbox deben
cumplir el mismo JSON Schema que `contracts/events/` define — no basta con
confiar en que Pydantic ya lo garantiza. La Fase 1 solo probó ejemplos
estáticos contra el schema; esto toma una fila real, escrita por el flujo
completo de la Fase 5, y la valida.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.config import Settings
from app.main import create_app
from app.models.outbox import OutboxEvent

pytestmark = pytest.mark.integration

EVENTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "events"
SCHEMA_FILENAMES = [
    "envelope.v1.schema.json",
    "bet-placed.v1.schema.json",
    "round-settled.v1.schema.json",
    "wallet-transaction.v1.schema.json",
]
SCHEMA_BY_EVENT_TYPE = {
    "bet.placed": "bet-placed.v1.schema.json",
    "round.settled": "round-settled.v1.schema.json",
    "wallet.transaction": "wallet-transaction.v1.schema.json",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def registry() -> Registry:
    resources = [
        (schema["$id"], Resource.from_contents(schema))
        for filename in SCHEMA_FILENAMES
        if (schema := _load_json(EVENTS_DIR / filename))
    ]
    return Registry().with_resources(resources)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    limiter.reset()


@pytest.fixture
async def client(integration_settings: Settings) -> Any:
    app = create_app(integration_settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_los_eventos_reales_del_outbox_cumplen_el_json_schema(
    client: AsyncClient, db_session: AsyncSession, registry: Registry
) -> None:
    email = f"{uuid.uuid4()}@example.com"
    register = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "contraseña-larga"}
    )
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}
    await client.post(
        "/api/v1/wallet/deposit",
        json={"amount_minor": 100_000},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    response = await client.post(
        "/api/v1/roulette/rounds",
        json={"bets": [{"bet_type": "red", "selection": {}, "stake_minor": 1000}]},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201

    rows = (await db_session.execute(select(OutboxEvent))).scalars().all()
    event_types = {row.payload["event_type"] for row in rows}
    # deposit -> wallet.transaction; apostar -> bet.placed + round.settled + wallet.transaction
    assert event_types == {"bet.placed", "round.settled", "wallet.transaction"}

    for row in rows:
        event_type = row.payload["event_type"]
        schema = _load_json(EVENTS_DIR / SCHEMA_BY_EVENT_TYPE[event_type])
        Draft202012Validator(schema, registry=registry).validate(row.payload)
