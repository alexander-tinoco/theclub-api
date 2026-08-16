"""Contraparte Pydantic de los JSON Schema en `contracts/events/`.

Dos artefactos describen la misma forma de dato desde ángulos distintos: los JSON
Schema son el contrato público hacia `theclub-web` y `theclub-data`, estos modelos son
lo que usa `app` internamente al escribir el outbox (Fase 6). `tests/unit/test_event_contracts.py`
valida los mismos ejemplos contra ambos para que no se desincronicen.

Una asimetría intencional: `selection` aquí es `dict[str, int | list[int]]` (más
estricto que el `type: object` sin restricciones del JSON Schema). El JSON Schema
documenta la forma pública sin atarse a un tipo interno; Pydantic sí impone ese tipo
porque es lo que el dominio de la ruleta (Fase 2) va a producir y consumir.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

BetType = Literal[
    "straight",
    "split",
    "street",
    "corner",
    "line",
    "dozen",
    "column",
    "red",
    "black",
    "odd",
    "even",
    "high",
    "low",
]

Currency = Literal["EUR"]

Selection = dict[str, int | list[int]]


class EventEnvelope[TData: BaseModel](BaseModel):
    """El sobre común a los tres eventos. Ver `contracts/events/envelope.v1.schema.json`."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: str
    event_version: Literal[1]
    occurred_at: datetime
    producer: Literal["theclub-api"] = "theclub-api"
    idempotency_key: str | None
    data: TData


class BetPlacedData(BaseModel):
    """theclub.bets.placed.v1 — qué apostó el usuario, antes de resolver la ronda."""

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
    """theclub.rounds.settled.v1 — el resultado y cómo pagó cada apuesta de la ronda."""

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
    """theclub.wallet.transactions.v1 — un evento por cada fila del ledger."""

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

#: Sufijo de topic por tipo de evento. La Fase 6 antepone `settings.KAFKA_TOPIC_PREFIX`
#: (p. ej. "theclub" + "." + "bets.placed.v1" = "theclub.bets.placed.v1") en vez de
#: hardcodear el prefijo aquí, para que cambiarlo sea una variable de entorno.
EVENT_TOPIC_SUFFIXES: dict[str, str] = {
    "bet.placed": "bets.placed.v1",
    "round.settled": "rounds.settled.v1",
    "wallet.transaction": "wallet.transactions.v1",
}
