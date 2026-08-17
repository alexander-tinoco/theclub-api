"""Bet validation and translating its `selection` into covered numbers."""

from dataclasses import dataclass

from app.domain.money import Money
from app.domain.roulette.table import (
    BLACK_NUMBERS,
    COLUMN_BY_INDEX,
    DOZEN_BY_INDEX,
    RED_NUMBERS,
    VALID_CORNERS,
    VALID_LINES,
    VALID_SPLITS,
    VALID_STREETS,
    BetType,
)

#: Same shape as `Selection` in app/events/schemas.py — see that module for
#: why domain owns it (domain doesn't depend on events, it's the other way
#: around).
Selection = dict[str, int | list[int]]


class InvalidBetError(ValueError):
    """The selection or the stake isn't valid for this bet type."""


@dataclass(frozen=True, slots=True)
class PlacedBet:
    bet_type: BetType
    selection: Selection
    stake: Money


_FIXED_SELECTIONS: dict[BetType, frozenset[int]] = {
    BetType.RED: RED_NUMBERS,
    BetType.BLACK: BLACK_NUMBERS,
    BetType.ODD: frozenset(n for n in range(1, 37) if n % 2 == 1),
    BetType.EVEN: frozenset(n for n in range(2, 37, 2)),
    BetType.HIGH: frozenset(range(19, 37)),
    BetType.LOW: frozenset(range(1, 19)),
}

_NUMBERS_SELECTIONS: dict[BetType, list[frozenset[int]]] = {
    BetType.STRAIGHT: [frozenset({n}) for n in range(37)],
    BetType.SPLIT: VALID_SPLITS,
    BetType.STREET: VALID_STREETS,
    BetType.CORNER: VALID_CORNERS,
    BetType.LINE: VALID_LINES,
}

_INDEX_SELECTIONS: dict[BetType, dict[int, frozenset[int]]] = {
    BetType.DOZEN: DOZEN_BY_INDEX,
    BetType.COLUMN: COLUMN_BY_INDEX,
}


def covered_numbers(bet_type: BetType, selection: Selection) -> frozenset[int]:
    """The numbers that make this bet win. Raises InvalidBetError if `selection`
    doesn't have the right shape or doesn't describe a valid figure on the table.
    """
    if bet_type in _FIXED_SELECTIONS:
        if selection:
            raise InvalidBetError(f"{bet_type} does not accept a selection: {selection!r}")
        return _FIXED_SELECTIONS[bet_type]

    if bet_type in _NUMBERS_SELECTIONS:
        numbers = selection.get("numbers")
        if not isinstance(numbers, list):
            raise InvalidBetError(f"{bet_type} requires 'numbers': {selection!r}")
        candidate = frozenset(numbers)
        if candidate in _NUMBERS_SELECTIONS[bet_type]:
            return candidate
        raise InvalidBetError(f"{numbers} don't form a valid {bet_type.value} on the table")

    if bet_type in _INDEX_SELECTIONS:
        index = selection.get("index")
        by_index = _INDEX_SELECTIONS[bet_type]
        if isinstance(index, int) and index in by_index:
            return by_index[index]
        raise InvalidBetError(f"{bet_type} requires 'index' in 1..3: {selection!r}")

    raise InvalidBetError(f"unknown bet_type: {bet_type!r}")  # pragma: no cover


def validate_bet(bet: PlacedBet, *, min_bet: Money, max_bet: Money) -> None:
    """Validates shape and table limits. Raises InvalidBetError if something doesn't add up."""
    if bet.stake.currency != min_bet.currency:
        raise InvalidBetError(f"stake in {bet.stake.currency}, table in {min_bet.currency}")
    if bet.stake < min_bet or max_bet < bet.stake:
        raise InvalidBetError(
            f"stake {bet.stake.minor} out of bounds [{min_bet.minor}, {max_bet.minor}]"
        )
    covered_numbers(bet.bet_type, bet.selection)  # validates shape; discards the result
