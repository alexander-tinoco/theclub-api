import pytest

from app.domain.roulette.table import (
    BET_SPECS,
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

pytestmark = pytest.mark.unit


def test_rojo_y_negro_son_18_y_18_y_no_se_solapan() -> None:
    assert len(RED_NUMBERS) == 18
    assert len(BLACK_NUMBERS) == 18
    assert RED_NUMBERS.isdisjoint(BLACK_NUMBERS)
    assert RED_NUMBERS | BLACK_NUMBERS | {0} == set(range(37))


def test_streets_cubren_1_36_sin_solape() -> None:
    assert len(VALID_STREETS) == 12
    assert all(len(s) == 3 for s in VALID_STREETS)
    assert set().union(*VALID_STREETS) == set(range(1, 37))
    assert sum(len(s) for s in VALID_STREETS) == 36  # sin solape


def test_columnas_cubren_1_36_sin_solape() -> None:
    assert len(COLUMN_BY_INDEX) == 3
    assert all(len(cols) == 12 for cols in COLUMN_BY_INDEX.values())
    assert set().union(*COLUMN_BY_INDEX.values()) == set(range(1, 37))


def test_docenas() -> None:
    assert DOZEN_BY_INDEX[1] == frozenset(range(1, 13))
    assert DOZEN_BY_INDEX[2] == frozenset(range(13, 25))
    assert DOZEN_BY_INDEX[3] == frozenset(range(25, 37))


def test_corners_son_22_de_4_numeros() -> None:
    assert len(VALID_CORNERS) == 22
    assert all(len(c) == 4 for c in VALID_CORNERS)
    assert len(set(VALID_CORNERS)) == 22  # sin duplicados


def test_lines_son_11_de_6_numeros() -> None:
    assert len(VALID_LINES) == 11
    assert all(len(line_) == 6 for line_ in VALID_LINES)


def test_splits_son_57_de_2_numeros_sin_duplicados() -> None:
    assert len(VALID_SPLITS) == 57
    assert all(len(s) == 2 for s in VALID_SPLITS)
    assert len(set(VALID_SPLITS)) == 57


@pytest.mark.parametrize("bet_type", list(BetType))
def test_house_edge_uniforme_2_70_por_ciento(bet_type: BetType) -> None:
    """coverage * (ratio + 1) == 36 para las 13 apuestas: es la forma matemática
    de decir "ventaja de casa del 2.70%, siempre igual, sin importar la apuesta".
    Si algún payout_ratio está mal tecleado, este test lo detecta solo.
    """
    spec = BET_SPECS[bet_type]

    assert spec.coverage * (spec.payout_ratio + 1) == 36
