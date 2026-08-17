"""El caso de uso central: colocar una o más apuestas y resolver la ronda.

Orden deliberado: validar forma y límites (sin tocar dinero) → debitar el
stake completo, gane o pierda → girar → resolver → acreditar si corresponde
→ persistir → encolar eventos. Cada paso que mueve dinero es una sola
sentencia atómica (ver `WalletRepository`); nada de leer-calcular-escribir.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.fairness import SeedMaterial
from app.domain.money import Money
from app.domain.roulette.bets import PlacedBet, Selection, validate_bet
from app.domain.roulette.engine import resolve_bets, spin
from app.domain.roulette.table import BetType
from app.events.outbox import enqueue_event, new_envelope
from app.events.schemas import BetPlacedData, RoundSettledData, SettledBet, WalletTransactionData
from app.repositories.ledger import LedgerRepository
from app.repositories.rounds import ResolvedBetInput, RoundRepository
from app.repositories.seed_pairs import SeedPairRepository
from app.repositories.wallets import WalletRepository


@dataclass(frozen=True, slots=True)
class BetRequest:
    """Entrada de una apuesta, ya desacoplada de Pydantic/HTTP."""

    bet_type: BetType
    selection: Selection
    stake_minor: int


async def place_bet(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    bet_requests: list[BetRequest],
) -> dict[str, Any]:
    placed_bets = [
        PlacedBet(bet_type=b.bet_type, selection=b.selection, stake=Money(b.stake_minor))
        for b in bet_requests
    ]

    min_bet = Money(settings.TABLE_MIN_BET_MINOR)
    max_bet = Money(settings.TABLE_MAX_BET_MINOR)
    for bet in placed_bets:
        validate_bet(bet, min_bet=min_bet, max_bet=max_bet)

    total_stake = sum(bet.stake.minor for bet in placed_bets)

    wallets = WalletRepository(session)
    wallet = await wallets.get_by_user_id(user_id)
    assert wallet is not None  # todo user tiene wallet desde el registro

    balance_after_stake = await wallets.debit(wallet.id, Money(total_stake))

    seed_pairs = SeedPairRepository(session)
    seed_pair = await seed_pairs.get_active_by_user_id(user_id)
    assert seed_pair is not None  # todo user tiene un seed pair activo desde el registro

    nonce = await seed_pairs.consume_nonce(seed_pair.id)
    seed_material = SeedMaterial(
        server_seed=seed_pair.server_seed, client_seed=seed_pair.client_seed, nonce=nonce
    )
    outcome = spin(seed_material)
    resolved = resolve_bets(placed_bets, outcome)

    total_payout = sum(r.payout.minor for r in resolved)
    balance_after_payout: int | None = None
    if total_payout > 0:
        balance_after_payout = await wallets.credit(wallet.id, Money(total_payout))
    final_balance = (
        balance_after_payout if balance_after_payout is not None else balance_after_stake
    )

    round_, bet_rows = await RoundRepository(session).create_settled_round(
        user_id=user_id,
        seed_pair_id=seed_pair.id,
        nonce=nonce,
        outcome=outcome,
        bets=[
            ResolvedBetInput(
                bet_type=r.bet.bet_type.value,
                selection=r.bet.selection,
                stake_minor=r.bet.stake.minor,
                payout_minor=r.payout.minor,
                won=r.won,
            )
            for r in resolved
        ],
    )

    ledger = LedgerRepository(session)
    stake_entry = await ledger.append(
        wallet_id=wallet.id,
        amount=Money(-total_stake),
        balance_after_minor=balance_after_stake,
        kind="bet_stake",
        ref_type="round",
        ref_id=round_.id,
    )
    payout_entry = None
    if total_payout > 0:
        assert balance_after_payout is not None
        payout_entry = await ledger.append(
            wallet_id=wallet.id,
            amount=Money(total_payout),
            balance_after_minor=balance_after_payout,
            kind="bet_payout",
            ref_type="round",
            ref_id=round_.id,
        )

    await _enqueue_round_events(
        session,
        settings,
        user_id=user_id,
        wallet_id=wallet.id,
        round_=round_,
        seed_pair_id=seed_pair.id,
        nonce=nonce,
        outcome=outcome,
        bet_rows=bet_rows,
        resolved=resolved,
        total_stake=total_stake,
        total_payout=total_payout,
        stake_entry_id=stake_entry.id,
        payout_entry_id=payout_entry.id if payout_entry is not None else None,
        balance_after_stake=balance_after_stake,
        balance_after_payout=balance_after_payout,
        currency=wallet.currency,
        idempotency_key=idempotency_key,
    )

    return {
        "round_id": str(round_.id),
        "outcome": outcome,
        "bets": [
            {
                "bet_type": r.bet.bet_type.value,
                "selection": r.bet.selection,
                "stake_minor": r.bet.stake.minor,
                "payout_minor": r.payout.minor,
                "won": r.won,
            }
            for r in resolved
        ],
        "total_stake_minor": total_stake,
        "total_payout_minor": total_payout,
        "net_minor": total_payout - total_stake,
        "balance_minor": final_balance,
    }


async def _enqueue_round_events(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    wallet_id: uuid.UUID,
    round_: Any,
    seed_pair_id: uuid.UUID,
    nonce: int,
    outcome: int,
    bet_rows: list[Any],
    resolved: list[Any],
    total_stake: int,
    total_payout: int,
    stake_entry_id: uuid.UUID,
    payout_entry_id: uuid.UUID | None,
    balance_after_stake: int,
    balance_after_payout: int | None,
    currency: str,
    idempotency_key: str,
) -> None:
    for bet_row, r in zip(bet_rows, resolved, strict=True):
        envelope = new_envelope(
            "bet.placed",
            BetPlacedData(
                round_id=round_.id,
                bet_id=bet_row.id,
                user_id=user_id,
                bet_type=r.bet.bet_type,
                selection=r.bet.selection,
                stake_minor=r.bet.stake.minor,
                currency=currency,
            ),
            idempotency_key=idempotency_key,
        )
        await enqueue_event(session, settings, envelope, key=str(user_id))

    round_envelope = new_envelope(
        "round.settled",
        RoundSettledData(
            round_id=round_.id,
            user_id=user_id,
            seed_pair_id=seed_pair_id,
            nonce=nonce,
            outcome=outcome,
            bets=[
                SettledBet(
                    bet_id=b.id,
                    bet_type=r.bet.bet_type,
                    selection=r.bet.selection,
                    stake_minor=r.bet.stake.minor,
                    payout_minor=r.payout.minor,
                    won=r.won,
                )
                for b, r in zip(bet_rows, resolved, strict=True)
            ],
            total_stake_minor=total_stake,
            total_payout_minor=total_payout,
            net_minor=total_payout - total_stake,
            currency=currency,
        ),
        idempotency_key=idempotency_key,
    )
    await enqueue_event(session, settings, round_envelope, key=str(user_id))

    stake_tx_envelope = new_envelope(
        "wallet.transaction",
        WalletTransactionData(
            transaction_id=stake_entry_id,
            user_id=user_id,
            wallet_id=wallet_id,
            kind="bet_stake",
            amount_minor=-total_stake,
            balance_after_minor=balance_after_stake,
            currency=currency,
            ref_type="round",
            ref_id=round_.id,
        ),
        idempotency_key=idempotency_key,
    )
    await enqueue_event(session, settings, stake_tx_envelope, key=str(user_id))

    if payout_entry_id is not None:
        assert balance_after_payout is not None
        payout_tx_envelope = new_envelope(
            "wallet.transaction",
            WalletTransactionData(
                transaction_id=payout_entry_id,
                user_id=user_id,
                wallet_id=wallet_id,
                kind="bet_payout",
                amount_minor=total_payout,
                balance_after_minor=balance_after_payout,
                currency=currency,
                ref_type="round",
                ref_id=round_.id,
            ),
            idempotency_key=idempotency_key,
        )
        await enqueue_event(session, settings, payout_tx_envelope, key=str(user_id))


async def list_rounds(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cursor: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[Any]:
    return await RoundRepository(session).list_by_user(user_id, cursor=cursor, limit=limit)
