import hashlib
import math
from collections import Counter
from statistics import NormalDist

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.fairness import SeedMaterial, derive_outcome
from app.domain.money import Money
from app.domain.roulette.bets import PlacedBet
from app.domain.roulette.engine import resolve_bets, spin
from app.domain.roulette.table import BET_SPECS, POCKET_COUNT, RED_NUMBERS, BetType
from tests.unit.domain.test_roulette_bets import placed_bets

pytestmark = pytest.mark.unit


def test_spin_is_deterministic() -> None:
    seed = SeedMaterial(server_seed=b"5" * 32, client_seed="player-1", nonce=1)

    assert spin(seed) == spin(seed)


def test_spin_is_in_0_36() -> None:
    for nonce in range(500):
        seed = SeedMaterial(server_seed=b"6" * 32, client_seed="player-1", nonce=nonce)
        assert 0 <= spin(seed) <= 36


def test_resolve_bets_winning_straight_pays_stake_times_36() -> None:
    bet = PlacedBet(bet_type=BetType.STRAIGHT, selection={"numbers": [17]}, stake=Money(500))

    [resolved] = resolve_bets([bet], outcome=17)

    assert resolved.won is True
    assert resolved.payout == Money(500 * 36)


def test_resolve_bets_losing_straight_pays_nothing() -> None:
    bet = PlacedBet(bet_type=BetType.STRAIGHT, selection={"numbers": [17]}, stake=Money(500))

    [resolved] = resolve_bets([bet], outcome=18)

    assert resolved.won is False
    assert resolved.payout == Money.zero()


def test_resolve_bets_winning_red_pays_stake_times_2() -> None:
    bet = PlacedBet(bet_type=BetType.RED, selection={}, stake=Money(1000))

    [resolved] = resolve_bets([bet], outcome=1)  # 1 is red

    assert resolved.won is True
    assert resolved.payout == Money(2000)


def test_resolve_bets_resolves_several_bets_from_the_same_round() -> None:
    bets = [
        PlacedBet(bet_type=BetType.STRAIGHT, selection={"numbers": [17]}, stake=Money(500)),
        PlacedBet(bet_type=BetType.RED, selection={}, stake=Money(1000)),
        PlacedBet(bet_type=BetType.EVEN, selection={}, stake=Money(200)),
    ]

    resolved = resolve_bets(bets, outcome=17)

    won = {r.bet.bet_type: r.won for r in resolved}
    assert won == {BetType.STRAIGHT: True, BetType.RED: False, BetType.EVEN: False}


@given(bet=placed_bets(), outcome=st.integers(min_value=0, max_value=36))
def test_resolve_bets_never_pays_a_negative_amount(bet: PlacedBet, outcome: int) -> None:
    [resolved] = resolve_bets([bet], outcome=outcome)

    assert resolved.payout.minor >= 0
    if not resolved.won:
        assert resolved.payout == Money.zero(bet.stake.currency)


# --- 1M spins shared between the uniformity test and the RTP one ---

N_SPINS = 1_000_000
CHI_SQUARE_ALPHA = 0.001
_SIMULATION_SEED = hashlib.sha256(b"phase-2-uniformity-and-rtp-simulation").digest()


def _chi_square_critical(df: int, alpha: float) -> float:
    """Wilson-Hilferty approximation to a chi-square distribution's upper
    percentile. Avoids adding scipy as a dependency just for this test.
    """
    z = NormalDist().inv_cdf(1 - alpha)
    term = 1 - 2 / (9 * df) + z * math.sqrt(2 / (9 * df))
    return df * term**3


@pytest.fixture(scope="module")
def outcome_counts() -> Counter[int]:
    return Counter(
        derive_outcome(
            SeedMaterial(server_seed=_SIMULATION_SEED, client_seed="simulation", nonce=nonce),
            modulus=POCKET_COUNT,
        )
        for nonce in range(N_SPINS)
    )


def test_derive_outcome_is_uniform_over_a_million_spins(
    outcome_counts: Counter[int],
) -> None:
    assert set(outcome_counts) == set(range(POCKET_COUNT))

    expected = N_SPINS / POCKET_COUNT
    chi_square = sum((observed - expected) ** 2 / expected for observed in outcome_counts.values())
    critical = _chi_square_critical(df=POCKET_COUNT - 1, alpha=CHI_SQUARE_ALPHA)

    assert chi_square < critical


def test_rtp_converges_to_97_30_percent(outcome_counts: Counter[int]) -> None:
    stake_minor = 100
    ratio = BET_SPECS[BetType.RED].payout_ratio  # 1 -> pays stake*2 if it wins

    wins = sum(outcome_counts[n] for n in RED_NUMBERS)
    total_stake = N_SPINS * stake_minor
    total_payout = wins * stake_minor * (ratio + 1)
    rtp = total_payout / total_stake

    assert abs(rtp - 36 / 37) < 0.01
