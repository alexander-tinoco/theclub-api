import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.money import Money
from app.domain.roulette.bets import (
    _FIXED_SELECTIONS,
    _INDEX_SELECTIONS,
    _NUMBERS_SELECTIONS,
    InvalidBetError,
    PlacedBet,
    Selection,
    covered_numbers,
    validate_bet,
)
from app.domain.roulette.table import BET_SPECS, BLACK_NUMBERS, RED_NUMBERS, BetType

pytestmark = pytest.mark.unit

MIN_BET = Money(100)
MAX_BET = Money(500_000)


def test_straight_covers_a_single_number() -> None:
    assert covered_numbers(BetType.STRAIGHT, {"numbers": [17]}) == frozenset({17})


def test_straight_accepts_zero() -> None:
    assert covered_numbers(BetType.STRAIGHT, {"numbers": [0]}) == frozenset({0})


def test_red_ignores_an_empty_selection() -> None:
    assert covered_numbers(BetType.RED, {}) == RED_NUMBERS


def test_black() -> None:
    assert covered_numbers(BetType.BLACK, {}) == BLACK_NUMBERS


def test_dozen_by_index() -> None:
    assert covered_numbers(BetType.DOZEN, {"index": 1}) == frozenset(range(1, 13))


def test_valid_split() -> None:
    assert covered_numbers(BetType.SPLIT, {"numbers": [1, 2]}) == frozenset({1, 2})


def test_non_adjacent_split_fails() -> None:
    with pytest.raises(InvalidBetError, match="split"):
        covered_numbers(BetType.SPLIT, {"numbers": [1, 36]})


def test_non_adjacent_corner_fails() -> None:
    with pytest.raises(InvalidBetError):
        covered_numbers(BetType.CORNER, {"numbers": [1, 5, 20, 36]})


def test_straight_with_two_numbers_fails() -> None:
    with pytest.raises(InvalidBetError, match="on the table"):
        covered_numbers(BetType.STRAIGHT, {"numbers": [1, 2]})


def test_straight_without_numbers_fails() -> None:
    with pytest.raises(InvalidBetError, match="requires 'numbers'"):
        covered_numbers(BetType.STRAIGHT, {})


def test_dozen_with_invalid_index_fails() -> None:
    with pytest.raises(InvalidBetError, match="index"):
        covered_numbers(BetType.DOZEN, {"index": 4})


def test_red_with_non_empty_selection_fails() -> None:
    with pytest.raises(InvalidBetError, match="does not accept a selection"):
        covered_numbers(BetType.RED, {"index": 1})


def test_validate_bet_accepts_a_valid_bet() -> None:
    bet = PlacedBet(bet_type=BetType.RED, selection={}, stake=Money(1000))

    validate_bet(bet, min_bet=MIN_BET, max_bet=MAX_BET)  # must not raise


def test_validate_bet_rejects_a_stake_below_the_minimum() -> None:
    bet = PlacedBet(bet_type=BetType.RED, selection={}, stake=Money(99))

    with pytest.raises(InvalidBetError, match="out of bounds"):
        validate_bet(bet, min_bet=MIN_BET, max_bet=MAX_BET)


def test_validate_bet_rejects_a_stake_above_the_maximum() -> None:
    bet = PlacedBet(bet_type=BetType.RED, selection={}, stake=Money(500_001))

    with pytest.raises(InvalidBetError, match="out of bounds"):
        validate_bet(bet, min_bet=MIN_BET, max_bet=MAX_BET)


def test_validate_bet_rejects_a_currency_different_from_the_table() -> None:
    bet = PlacedBet(bet_type=BetType.RED, selection={}, stake=Money(1000, "USD"))

    with pytest.raises(InvalidBetError, match="USD"):
        validate_bet(bet, min_bet=MIN_BET, max_bet=MAX_BET)


def test_validate_bet_propagates_invalid_selection() -> None:
    bet = PlacedBet(bet_type=BetType.SPLIT, selection={"numbers": [1, 36]}, stake=Money(1000))

    with pytest.raises(InvalidBetError):
        validate_bet(bet, min_bet=MIN_BET, max_bet=MAX_BET)


# --- generator of always well-formed bets, for the property-based tests ---


@st.composite
def _selections(draw: st.DrawFn, bet_type: BetType) -> Selection:
    if bet_type in _FIXED_SELECTIONS:
        return {}
    if bet_type in _NUMBERS_SELECTIONS:
        options = [sorted(s) for s in _NUMBERS_SELECTIONS[bet_type]]
        numbers: list[int] = draw(st.sampled_from(options))
        return {"numbers": numbers}
    by_index = _INDEX_SELECTIONS[bet_type]
    index: int = draw(st.sampled_from(sorted(by_index)))
    return {"index": index}


@st.composite
def placed_bets(draw: st.DrawFn) -> PlacedBet:
    bet_type = draw(st.sampled_from(list(BetType)))
    selection = draw(_selections(bet_type))
    stake_minor = draw(st.integers(min_value=MIN_BET.minor, max_value=MAX_BET.minor))
    return PlacedBet(bet_type=bet_type, selection=selection, stake=Money(stake_minor))


@given(bet=placed_bets())
def test_every_well_formed_bet_passes_validation(bet: PlacedBet) -> None:
    validate_bet(bet, min_bet=MIN_BET, max_bet=MAX_BET)


@given(bet=placed_bets())
def test_covered_numbers_matches_the_declared_coverage(bet: PlacedBet) -> None:
    numbers = covered_numbers(bet.bet_type, bet.selection)

    assert len(numbers) == BET_SPECS[bet.bet_type].coverage
    assert all(0 <= n <= 36 for n in numbers)
