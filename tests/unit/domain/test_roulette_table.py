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


def test_red_and_black_are_18_and_18_and_do_not_overlap() -> None:
    assert len(RED_NUMBERS) == 18
    assert len(BLACK_NUMBERS) == 18
    assert RED_NUMBERS.isdisjoint(BLACK_NUMBERS)
    assert RED_NUMBERS | BLACK_NUMBERS | {0} == set(range(37))


def test_streets_cover_1_36_with_no_overlap() -> None:
    assert len(VALID_STREETS) == 12
    assert all(len(s) == 3 for s in VALID_STREETS)
    assert set().union(*VALID_STREETS) == set(range(1, 37))
    assert sum(len(s) for s in VALID_STREETS) == 36  # no overlap


def test_columns_cover_1_36_with_no_overlap() -> None:
    assert len(COLUMN_BY_INDEX) == 3
    assert all(len(cols) == 12 for cols in COLUMN_BY_INDEX.values())
    assert set().union(*COLUMN_BY_INDEX.values()) == set(range(1, 37))


def test_dozens() -> None:
    assert DOZEN_BY_INDEX[1] == frozenset(range(1, 13))
    assert DOZEN_BY_INDEX[2] == frozenset(range(13, 25))
    assert DOZEN_BY_INDEX[3] == frozenset(range(25, 37))


def test_corners_are_22_of_4_numbers() -> None:
    assert len(VALID_CORNERS) == 22
    assert all(len(c) == 4 for c in VALID_CORNERS)
    assert len(set(VALID_CORNERS)) == 22  # no duplicates


def test_lines_are_11_of_6_numbers() -> None:
    assert len(VALID_LINES) == 11
    assert all(len(line_) == 6 for line_ in VALID_LINES)


def test_splits_are_57_of_2_numbers_with_no_duplicates() -> None:
    assert len(VALID_SPLITS) == 57
    assert all(len(s) == 2 for s in VALID_SPLITS)
    assert len(set(VALID_SPLITS)) == 57


@pytest.mark.parametrize("bet_type", list(BetType))
def test_uniform_house_edge_of_2_70_percent(bet_type: BetType) -> None:
    """coverage * (ratio + 1) == 36 for all 13 bets: it's the mathematical
    way of saying "2.70% house edge, always the same, regardless of the
    bet". If any payout_ratio is mistyped, this test catches it on its own.
    """
    spec = BET_SPECS[bet_type]

    assert spec.coverage * (spec.payout_ratio + 1) == 36
