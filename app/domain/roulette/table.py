"""Geometría de la mesa: de dónde salen los tipos de apuesta válidos.

Los números 1-36 se ubican en una rejilla de 3 filas x 12 columnas, tal como
están impresos en una mesa real:

    3  6  9 12 15 18 21 24 27 30 33 36
    2  5  8 11 14 17 20 23 26 29 32 35
    1  4  7 10 13 16 19 22 25 28 31 34

Split/street/corner/line/column no son listas escritas a mano: se derivan de
esa rejilla, así una apuesta que no encaje geométricamente (un "corner" con 4
números sueltos) se rechaza sola en `bets.py`.

Simplificación consciente: el 0 solo participa en straight. Las apuestas de
"cesto" que en la ruleta francesa incluyen al 0 en un split/corner tienen una
geometría irregular fuera de esta rejilla y quedan fuera de alcance.
"""

from dataclasses import dataclass
from enum import StrEnum

POCKET_COUNT = 37

RED_NUMBERS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})
BLACK_NUMBERS = frozenset(range(1, 37)) - RED_NUMBERS

_ROWS = 3
_COLS = 12


def _position(number: int) -> tuple[int, int]:
    """(fila, columna), ambas 1-indexadas. Solo válido para 1..36."""
    row = (number - 1) % _ROWS + 1
    col = (number - 1) // _ROWS + 1
    return row, col


def _number_at(row: int, col: int) -> int:
    return (col - 1) * _ROWS + row


class BetType(StrEnum):
    STRAIGHT = "straight"
    SPLIT = "split"
    STREET = "street"
    CORNER = "corner"
    LINE = "line"
    DOZEN = "dozen"
    COLUMN = "column"
    RED = "red"
    BLACK = "black"
    ODD = "odd"
    EVEN = "even"
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class BetSpec:
    coverage: int
    payout_ratio: int


BET_SPECS: dict[BetType, BetSpec] = {
    BetType.STRAIGHT: BetSpec(coverage=1, payout_ratio=35),
    BetType.SPLIT: BetSpec(coverage=2, payout_ratio=17),
    BetType.STREET: BetSpec(coverage=3, payout_ratio=11),
    BetType.CORNER: BetSpec(coverage=4, payout_ratio=8),
    BetType.LINE: BetSpec(coverage=6, payout_ratio=5),
    BetType.DOZEN: BetSpec(coverage=12, payout_ratio=2),
    BetType.COLUMN: BetSpec(coverage=12, payout_ratio=2),
    BetType.RED: BetSpec(coverage=18, payout_ratio=1),
    BetType.BLACK: BetSpec(coverage=18, payout_ratio=1),
    BetType.ODD: BetSpec(coverage=18, payout_ratio=1),
    BetType.EVEN: BetSpec(coverage=18, payout_ratio=1),
    BetType.HIGH: BetSpec(coverage=18, payout_ratio=1),
    BetType.LOW: BetSpec(coverage=18, payout_ratio=1),
}


def _streets() -> list[frozenset[int]]:
    """Una columna de la rejilla = 3 números consecutivos: {1,2,3}, {4,5,6}..."""
    return [frozenset(_number_at(r, c) for r in range(1, _ROWS + 1)) for c in range(1, _COLS + 1)]


def _columns() -> list[frozenset[int]]:
    """Una fila de la rejilla = los 12 números de una "columna" de apuesta."""
    return [frozenset(_number_at(r, c) for c in range(1, _COLS + 1)) for r in range(1, _ROWS + 1)]


def _corners() -> list[frozenset[int]]:
    """Cada bloque 2x2 de la rejilla."""
    return [
        frozenset(
            {
                _number_at(r, c),
                _number_at(r + 1, c),
                _number_at(r, c + 1),
                _number_at(r + 1, c + 1),
            }
        )
        for c in range(1, _COLS)
        for r in range(1, _ROWS)
    ]


def _lines() -> list[frozenset[int]]:
    """Dos streets contiguos."""
    streets = _streets()
    return [streets[c] | streets[c + 1] for c in range(len(streets) - 1)]


def _splits() -> list[frozenset[int]]:
    """Cada par de celdas vecinas (comparten un lado) de la rejilla."""
    splits = []
    for n in range(1, 37):
        r, c = _position(n)
        if c < _COLS:
            splits.append(frozenset({n, _number_at(r, c + 1)}))
        if r < _ROWS:
            splits.append(frozenset({n, _number_at(r + 1, c)}))
    return splits


VALID_STREETS = _streets()
VALID_CORNERS = _corners()
VALID_LINES = _lines()
VALID_SPLITS = _splits()

DOZEN_BY_INDEX: dict[int, frozenset[int]] = {
    1: frozenset(range(1, 13)),
    2: frozenset(range(13, 25)),
    3: frozenset(range(25, 37)),
}
COLUMN_BY_INDEX: dict[int, frozenset[int]] = dict(enumerate(_columns(), start=1))
