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


def test_spin_es_determinista() -> None:
    seed = SeedMaterial(server_seed=b"5" * 32, client_seed="jugador-1", nonce=1)

    assert spin(seed) == spin(seed)


def test_spin_esta_en_0_36() -> None:
    for nonce in range(500):
        seed = SeedMaterial(server_seed=b"6" * 32, client_seed="jugador-1", nonce=nonce)
        assert 0 <= spin(seed) <= 36


def test_resolve_bets_pleno_ganador_paga_stake_por_36() -> None:
    bet = PlacedBet(bet_type=BetType.STRAIGHT, selection={"numbers": [17]}, stake=Money(500))

    [resolved] = resolve_bets([bet], outcome=17)

    assert resolved.won is True
    assert resolved.payout == Money(500 * 36)


def test_resolve_bets_pleno_perdedor_no_paga_nada() -> None:
    bet = PlacedBet(bet_type=BetType.STRAIGHT, selection={"numbers": [17]}, stake=Money(500))

    [resolved] = resolve_bets([bet], outcome=18)

    assert resolved.won is False
    assert resolved.payout == Money.zero()


def test_resolve_bets_rojo_ganador_paga_stake_por_2() -> None:
    bet = PlacedBet(bet_type=BetType.RED, selection={}, stake=Money(1000))

    [resolved] = resolve_bets([bet], outcome=1)  # 1 es rojo

    assert resolved.won is True
    assert resolved.payout == Money(2000)


def test_resolve_bets_resuelve_varias_apuestas_de_la_misma_ronda() -> None:
    bets = [
        PlacedBet(bet_type=BetType.STRAIGHT, selection={"numbers": [17]}, stake=Money(500)),
        PlacedBet(bet_type=BetType.RED, selection={}, stake=Money(1000)),
        PlacedBet(bet_type=BetType.EVEN, selection={}, stake=Money(200)),
    ]

    resolved = resolve_bets(bets, outcome=17)

    won = {r.bet.bet_type: r.won for r in resolved}
    assert won == {BetType.STRAIGHT: True, BetType.RED: False, BetType.EVEN: False}


@given(bet=placed_bets(), outcome=st.integers(min_value=0, max_value=36))
def test_resolve_bets_nunca_paga_negativo(bet: PlacedBet, outcome: int) -> None:
    [resolved] = resolve_bets([bet], outcome=outcome)

    assert resolved.payout.minor >= 0
    if not resolved.won:
        assert resolved.payout == Money.zero(bet.stake.currency)


# --- 1M giros compartidos entre el test de uniformidad y el de RTP ---

N_SPINS = 1_000_000
CHI_SQUARE_ALPHA = 0.001
_SIMULATION_SEED = hashlib.sha256(b"fase-2-simulacion-de-uniformidad-y-rtp").digest()


def _chi_square_critical(df: int, alpha: float) -> float:
    """Aproximación de Wilson-Hilferty al percentil superior de una chi-cuadrado.
    Evita añadir scipy como dependencia solo para este test.
    """
    z = NormalDist().inv_cdf(1 - alpha)
    term = 1 - 2 / (9 * df) + z * math.sqrt(2 / (9 * df))
    return df * term**3


@pytest.fixture(scope="module")
def outcome_counts() -> Counter[int]:
    return Counter(
        derive_outcome(
            SeedMaterial(server_seed=_SIMULATION_SEED, client_seed="simulacion", nonce=nonce),
            modulus=POCKET_COUNT,
        )
        for nonce in range(N_SPINS)
    )


def test_derive_outcome_es_uniforme_sobre_un_millon_de_giros(
    outcome_counts: Counter[int],
) -> None:
    assert set(outcome_counts) == set(range(POCKET_COUNT))

    expected = N_SPINS / POCKET_COUNT
    chi_square = sum((observed - expected) ** 2 / expected for observed in outcome_counts.values())
    critical = _chi_square_critical(df=POCKET_COUNT - 1, alpha=CHI_SQUARE_ALPHA)

    assert chi_square < critical


def test_rtp_converge_al_97_30_por_ciento(outcome_counts: Counter[int]) -> None:
    stake_minor = 100
    ratio = BET_SPECS[BetType.RED].payout_ratio  # 1 -> paga stake*2 si gana

    wins = sum(outcome_counts[n] for n in RED_NUMBERS)
    total_stake = N_SPINS * stake_minor
    total_payout = wins * stake_minor * (ratio + 1)
    rtp = total_payout / total_stake

    assert abs(rtp - 36 / 37) < 0.01
