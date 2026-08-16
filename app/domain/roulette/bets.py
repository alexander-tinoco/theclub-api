"""Validación de apuestas y traducción de su `selection` a números cubiertos."""

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

#: Misma forma que `Selection` en app/events/schemas.py — ver ese módulo para
#: por qué domain es quien la posee (domain no depende de events, es al revés).
Selection = dict[str, int | list[int]]


class InvalidBetError(ValueError):
    """La selección o el stake no son válidos para este tipo de apuesta."""


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
    """Los números que hacen ganar a esta apuesta. Levanta InvalidBetError si `selection`
    no tiene la forma correcta o no describe una figura válida en la mesa.
    """
    if bet_type in _FIXED_SELECTIONS:
        if selection:
            raise InvalidBetError(f"{bet_type} no admite selection: {selection!r}")
        return _FIXED_SELECTIONS[bet_type]

    if bet_type in _NUMBERS_SELECTIONS:
        numbers = selection.get("numbers")
        if not isinstance(numbers, list):
            raise InvalidBetError(f"{bet_type} requiere 'numbers': {selection!r}")
        candidate = frozenset(numbers)
        if candidate in _NUMBERS_SELECTIONS[bet_type]:
            return candidate
        raise InvalidBetError(f"{numbers} no forman un {bet_type.value} válido en la mesa")

    if bet_type in _INDEX_SELECTIONS:
        index = selection.get("index")
        by_index = _INDEX_SELECTIONS[bet_type]
        if isinstance(index, int) and index in by_index:
            return by_index[index]
        raise InvalidBetError(f"{bet_type} requiere 'index' en 1..3: {selection!r}")

    raise InvalidBetError(f"bet_type desconocido: {bet_type!r}")  # pragma: no cover


def validate_bet(bet: PlacedBet, *, min_bet: Money, max_bet: Money) -> None:
    """Válida forma y límites de mesa. Levanta InvalidBetError si algo no cuadra."""
    if bet.stake.currency != min_bet.currency:
        raise InvalidBetError(f"stake en {bet.stake.currency}, mesa en {min_bet.currency}")
    if bet.stake < min_bet or max_bet < bet.stake:
        raise InvalidBetError(
            f"stake {bet.stake.minor} fuera de límites [{min_bet.minor}, {max_bet.minor}]"
        )
    covered_numbers(bet.bet_type, bet.selection)  # valida la forma; descarta el resultado
