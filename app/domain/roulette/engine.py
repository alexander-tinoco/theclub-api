"""The game's entry point: spin and resolve a round's bets."""

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
    """Derives the outcome (0-36) deterministically and verifiably."""
    return derive_outcome(seed, modulus=POCKET_COUNT)


def resolve_bets(bets: Sequence[PlacedBet], outcome: int) -> list[ResolvedBet]:
    """For each bet: did it win? and how much gets credited if it did?

    The payout includes the stake's return: it pays `stake * (ratio + 1)`,
    not `stake * ratio` — the stake was already debited upfront (Phase 5),
    so "pays 35 to 1" is only true if the credit also returns the bet.
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
