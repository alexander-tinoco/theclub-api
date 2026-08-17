"""/roulette/fairness/*, /roulette/rounds."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUserDep, SessionDep, SessionFactoryDep, SettingsDep
from app.api.pagination import decode_cursor, encode_cursor
from app.domain.roulette.bets import Selection
from app.domain.roulette.table import BetType
from app.services import fairness as fairness_service
from app.services import roulette as roulette_service
from app.services.idempotency import hash_request_body, run_idempotent

router = APIRouter(prefix="/roulette", tags=["roulette"])

MAX_BETS_PER_ROUND = 20


# --- fairness ---------------------------------------------------------------


class FairnessCurrentResponse(BaseModel):
    server_seed_hash: str
    client_seed: str
    nonce: int


class FairnessRotateResponse(BaseModel):
    revealed_server_seed: str
    revealed_server_seed_hash: str
    new_server_seed_hash: str
    new_client_seed: str


@router.get("/fairness/current", response_model=FairnessCurrentResponse)
async def fairness_current(user: CurrentUserDep, session: SessionDep) -> dict[str, Any]:
    return await fairness_service.get_current_seed(session, user_id=user.id)


@router.post("/fairness/rotate", response_model=FairnessRotateResponse)
async def fairness_rotate(user: CurrentUserDep, session: SessionDep) -> dict[str, Any]:
    return await fairness_service.rotate_seed(session, user_id=user.id)


# --- rounds -------------------------------------------------------------------


class BetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bet_type: BetType
    selection: Selection
    stake_minor: int = Field(gt=0)


class PlaceRoundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bets: list[BetInput] = Field(min_length=1, max_length=MAX_BETS_PER_ROUND)


class SettledBetResponse(BaseModel):
    bet_type: BetType
    selection: Selection
    stake_minor: int
    payout_minor: int
    won: bool


class RoundResponse(BaseModel):
    round_id: UUID
    outcome: int
    bets: list[SettledBetResponse]
    total_stake_minor: int
    total_payout_minor: int
    net_minor: int
    balance_minor: int


@router.post("/rounds", response_model=RoundResponse, status_code=status.HTTP_201_CREATED)
async def create_round(
    request: Request,
    body: PlaceRoundRequest,
    user: CurrentUserDep,
    settings: SettingsDep,
    session_factory: SessionFactoryDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> dict[str, Any]:
    raw_body = await request.body()
    request_hash = hash_request_body(raw_body)

    async def work(session: Any) -> dict[str, Any]:
        bet_requests = [
            roulette_service.BetRequest(
                bet_type=b.bet_type, selection=b.selection, stake_minor=b.stake_minor
            )
            for b in body.bets
        ]
        return await roulette_service.place_bet(
            session,
            settings,
            user_id=user.id,
            idempotency_key=idempotency_key,
            bet_requests=bet_requests,
        )

    return await run_idempotent(
        session_factory,
        user_id=user.id,
        key=idempotency_key,
        request_hash=request_hash,
        ttl_hours=settings.IDEMPOTENCY_KEY_TTL_HOURS,
        work=work,
    )


class RoundHistoryItem(BaseModel):
    round_id: UUID
    outcome: int
    created_at: datetime
    bets: list[SettledBetResponse]


class RoundHistoryPage(BaseModel):
    items: list[RoundHistoryItem]
    next_cursor: str | None


@router.get("/rounds", response_model=RoundHistoryPage)
async def list_rounds(
    user: CurrentUserDep,
    session: SessionDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> RoundHistoryPage:
    decoded_cursor = decode_cursor(cursor) if cursor else None
    rounds = await roulette_service.list_rounds(
        session, user_id=user.id, cursor=decoded_cursor, limit=limit
    )

    items = []
    for r in rounds:
        assert r.outcome is not None  # este juego resuelve la ronda de inmediato
        items.append(
            RoundHistoryItem(
                round_id=r.id,
                outcome=r.outcome,
                created_at=r.created_at,
                bets=[
                    SettledBetResponse(
                        bet_type=b.bet_type,
                        selection=b.selection,
                        stake_minor=b.stake_minor,
                        payout_minor=b.payout_minor,
                        won=b.status == "won",
                    )
                    for b in r.bets
                ],
            )
        )

    next_cursor = (
        encode_cursor(rounds[-1].created_at, rounds[-1].id) if len(rounds) == limit else None
    )
    return RoundHistoryPage(items=items, next_cursor=next_cursor)
