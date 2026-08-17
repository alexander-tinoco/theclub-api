"""The examples in contracts/events/examples/ must validate against two
independent artifacts: the published JSON Schema and the internal Pydantic
model. If either one drifts from the other, this test catches it.
"""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel
from referencing import Registry, Resource

from app.events.schemas import (
    EVENT_TOPIC_SUFFIXES,
    BetPlacedData,
    EventEnvelope,
    RoundSettledData,
    WalletTransactionData,
)

pytestmark = pytest.mark.unit

EVENTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "events"
SCHEMA_FILENAMES = [
    "envelope.v1.schema.json",
    "bet-placed.v1.schema.json",
    "round-settled.v1.schema.json",
    "wallet-transaction.v1.schema.json",
]


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


CASES: list[tuple[str, str, type[BaseModel]]] = [
    ("bet-placed.v1.schema.json", "bet-placed.v1.example.json", EventEnvelope[BetPlacedData]),
    (
        "round-settled.v1.schema.json",
        "round-settled.v1.example.json",
        EventEnvelope[RoundSettledData],
    ),
    (
        "wallet-transaction.v1.schema.json",
        "wallet-transaction.v1.example.json",
        EventEnvelope[WalletTransactionData],
    ),
]
CASE_IDS = ["bet-placed", "round-settled", "wallet-transaction"]


@pytest.mark.parametrize(
    ("schema_filename", "example_filename", "envelope_type"), CASES, ids=CASE_IDS
)
def test_example_validates_against_json_schema(
    registry: Registry, schema_filename: str, example_filename: str, envelope_type: type[BaseModel]
) -> None:
    schema = _load_json(EVENTS_DIR / schema_filename)
    example = _load_json(EVENTS_DIR / "examples" / example_filename)

    validator = Draft202012Validator(schema, registry=registry)

    validator.validate(example)


@pytest.mark.parametrize(
    ("schema_filename", "example_filename", "envelope_type"), CASES, ids=CASE_IDS
)
def test_example_validates_against_pydantic_model(
    registry: Registry, schema_filename: str, example_filename: str, envelope_type: type[BaseModel]
) -> None:
    example = _load_json(EVENTS_DIR / "examples" / example_filename)

    envelope = cast("EventEnvelope[BaseModel]", envelope_type.model_validate(example))

    assert str(envelope.event_id) == example["event_id"]
    assert envelope.event_type == example["event_type"]
    assert envelope.idempotency_key == example["idempotency_key"]


def test_all_three_event_types_have_an_assigned_topic() -> None:
    event_types = {
        _load_json(EVENTS_DIR / "examples" / example_filename)["event_type"]
        for _, example_filename, _ in CASES
    }

    assert event_types == set(EVENT_TOPIC_SUFFIXES)


def test_envelope_rejects_undeclared_fields() -> None:
    example = _load_json(EVENTS_DIR / "examples" / "bet-placed.v1.example.json")
    example["unexpected_field"] = "should not be here"

    with pytest.raises(ValueError, match="unexpected_field"):
        EventEnvelope[BetPlacedData].model_validate(example)
