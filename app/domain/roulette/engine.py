"""Punto de entrada del juego: girar y resolver las apuestas de una ronda."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.fairness import SeedMaterial, derive_outcome
from app.domain.money import Money
from app.domain.roulette.bets import PlacedBet, covered_numbers
from app.domain.roulette.table import BET_SPECS, POCKET_COUNT


@dataclass(frozen=True, slots=True)
class ResolvedBet:
    bet: PlacedBet
    won: bool
    payout: Money


def spin(seed: SeedMaterial) -> int:
    """Deriva el resultado (0-36) de forma determinista y verificable."""
    return derive_outcome(seed, modulus=POCKET_COUNT)


def resolve_bets(bets: Sequence[PlacedBet], outcome: int) -> list[ResolvedBet]:
    """Por cada apuesta: ¿ganó? y ¿cuánto se le acredita si ganó?

    El payout incluye la devolución del stake: paga `stake * (ratio + 1)`, no
    `stake * ratio` — el stake ya se debitó por adelantado (Fase 5), así que
    "paga 35 a 1" solo es cierto si el crédito devuelve también la apuesta.
    """
    resolved = []
    for bet in bets:
        won = outcome in covered_numbers(bet.bet_type, bet.selection)
        if won:
            ratio = BET_SPECS[bet.bet_type].payout_ratio
            payout = bet.stake * (ratio + 1)
        else:
            payout = Money.zero(bet.stake.currency)
        resolved.append(ResolvedBet(bet=bet, won=won, payout=payout))
    return resolved
