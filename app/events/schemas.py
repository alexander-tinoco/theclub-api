"""Pydantic counterpart of the JSON Schemas in `contracts/events/`.

Two artifacts describe the same data shape from different angles: the JSON
Schemas are the public contract towards `theclub-web` and `theclub-data`,
these models are what `app` uses internally when writing to the outbox
(Phase 6). `tests/unit/test_event_contracts.py` validates the same examples
against both so they never drift apart.

`BetType` and `Selection` are imported from `app.domain.roulette` instead
of being redeclared here: the domain (Phase 2) owns roulette's vocabulary,
and this events layer reuses it so the 13 bet types don't live duplicated
in two places that could diverge. `app/domain/` still doesn't know
`app/events/` exists — the dependency only goes one way.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.roulette.bets import Selection
from app.domain.roulette.table import BetType

Currency = Literal["EUR"]


class EventEnvelope[TData: BaseModel](BaseModel):
    """The envelope shared by all three events. See `contracts/events/envelope.v1.schema.json`."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: str
    event_version: Literal[1]
    occurred_at: datetime
    producer: Literal["theclub-api"] = "theclub-api"
    idempotency_key: str | None
    data: TData


class BetPlacedData(BaseModel):
    """theclub.bets.placed.v1 — what the user bet, before the round is resolved."""

    model_config = ConfigDict(extra="forbid")

    round_id: UUID
    bet_id: UUID
    user_id: UUID
    bet_type: BetType
    selection: Selection
    stake_minor: int = Field(ge=1)
    currency: Currency


class SettledBet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bet_id: UUID
    bet_type: BetType
    selection: Selection
    stake_minor: int = Field(ge=1)
    payout_minor: int = Field(ge=0)
    won: bool


class RoundSettledData(BaseModel):
    """theclub.rounds.settled.v1 — the outcome and how each bet in the round paid out."""

    model_config = ConfigDict(extra="forbid")

    round_id: UUID
    user_id: UUID
    seed_pair_id: UUID
    nonce: int = Field(ge=0)
    outcome: int = Field(ge=0, le=36)
    bets: list[SettledBet] = Field(min_length=1)
    total_stake_minor: int = Field(ge=1)
    total_payout_minor: int = Field(ge=0)
    net_minor: int
    currency: Currency


WalletTransactionKind = Literal["deposit", "bet_stake", "bet_payout", "adjustment"]
WalletTransactionRefType = Literal["round", "bet", "manual"]


class WalletTransactionData(BaseModel):
    """theclub.wallet.transactions.v1 — one event per ledger row."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: UUID
    user_id: UUID
    wallet_id: UUID
    kind: WalletTransactionKind
    amount_minor: int
    balance_after_minor: int = Field(ge=0)
    currency: Currency
    ref_type: WalletTransactionRefType | None
    ref_id: UUID | None


BetPlacedEvent = EventEnvelope[BetPlacedData]
RoundSettledEvent = EventEnvelope[RoundSettledData]
WalletTransactionEvent = EventEnvelope[WalletTransactionData]

#: Topic suffix per event type. Phase 6 prepends `settings.KAFKA_TOPIC_PREFIX`
#: (e.g. "theclub" + "." + "bets.placed.v1" = "theclub.bets.placed.v1") instead
#: of hardcoding the prefix here, so changing it is just an environment variable.
EVENT_TOPIC_SUFFIXES: dict[str, str] = {
    "bet.placed": "bets.placed.v1",
    "round.settled": "rounds.settled.v1",
    "wallet.transaction": "wallet.transactions.v1",
}
