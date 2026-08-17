"""/wallet/balance, /wallet/transactions, /wallet/deposit."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import BroadcasterDep, CurrentUserDep, SessionDep, SessionFactoryDep, SettingsDep
from app.api.pagination import decode_cursor, encode_cursor
from app.api.rate_limit import limiter
from app.api.request_context import bind_canonical
from app.services import wallet as wallet_service
from app.services.idempotency import IDEMPOTENCY_KEY_MAX_LENGTH, hash_request_body, run_idempotent
from app.services.wallet import MAX_DEPOSIT_MINOR

router = APIRouter(prefix="/wallet", tags=["wallet"])


class BalanceResponse(BaseModel):
    balance_minor: int
    currency: str


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(user: CurrentUserDep, session: SessionDep) -> dict[str, Any]:
    result = await wallet_service.get_balance(session, user_id=user.id)
    return result.to_dict()


class TransactionItem(BaseModel):
    id: UUID
    amount_minor: int
    balance_after_minor: int
    kind: str
    ref_type: str | None
    ref_id: UUID | None
    created_at: datetime


class TransactionsPage(BaseModel):
    items: list[TransactionItem]
    next_cursor: str | None


@router.get("/transactions", response_model=TransactionsPage)
async def list_transactions(
    user: CurrentUserDep,
    session: SessionDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> TransactionsPage:
    decoded_cursor = decode_cursor(cursor) if cursor else None
    entries = await wallet_service.list_transactions(
        session, user_id=user.id, cursor=decoded_cursor, limit=limit
    )

    items = [
        TransactionItem(
            id=e.id,
            amount_minor=e.amount_minor,
            balance_after_minor=e.balance_after_minor,
            kind=e.kind,
            ref_type=e.ref_type,
            ref_id=e.ref_id,
            created_at=e.created_at,
        )
        for e in entries
    ]
    next_cursor = (
        encode_cursor(entries[-1].created_at, entries[-1].id) if len(entries) == limit else None
    )
    return TransactionsPage(items=items, next_cursor=next_cursor)


class DepositRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0, le=MAX_DEPOSIT_MINOR)


class DepositResponse(BaseModel):
    balance_minor: int
    currency: str


@router.post("/deposit", response_model=DepositResponse)
@limiter.limit("30/minute")
async def deposit(
    request: Request,
    body: DepositRequest,
    user: CurrentUserDep,
    settings: SettingsDep,
    session_factory: SessionFactoryDep,
    broadcaster: BroadcasterDep,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=IDEMPOTENCY_KEY_MAX_LENGTH)
    ],
) -> dict[str, Any]:
    raw_body = await request.body()
    request_hash = hash_request_body(raw_body)

    async def work(session: Any) -> dict[str, Any]:
        result = await wallet_service.deposit(
            session,
            settings,
            user_id=user.id,
            idempotency_key=idempotency_key,
            amount_minor=body.amount_minor,
        )
        return result.to_dict()

    result = await run_idempotent(
        session_factory,
        user_id=user.id,
        key=idempotency_key,
        request_hash=request_hash,
        ttl_hours=settings.IDEMPOTENCY_KEY_TTL_HOURS,
        work=work,
    )
    await broadcaster.publish(user.id, {"type": "balance.updated", **result})
    bind_canonical(amount_minor=body.amount_minor, balance_minor=result["balance_minor"])
    return result
