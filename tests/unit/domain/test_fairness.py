import hashlib
import hmac

import pytest

from app.domain.fairness import SEED_BYTES, SeedMaterial, derive_outcome, hash_seed, new_server_seed

pytestmark = pytest.mark.unit


def test_new_server_seed_has_32_bytes() -> None:
    assert len(new_server_seed()) == SEED_BYTES


def test_new_server_seed_does_not_repeat() -> None:
    seeds = {new_server_seed() for _ in range(100)}
    assert len(seeds) == 100


def test_hash_seed_is_sha256_hex() -> None:
    seed = b"0" * 32
    assert hash_seed(seed) == hashlib.sha256(seed).hexdigest()


def test_derive_outcome_is_deterministic() -> None:
    seed = SeedMaterial(server_seed=b"1" * 32, client_seed="player-1", nonce=7)

    results = {derive_outcome(seed, modulus=37) for _ in range(50)}

    assert len(results) == 1


def test_derive_outcome_is_in_range() -> None:
    seed = SeedMaterial(server_seed=b"2" * 32, client_seed="player-1", nonce=0)

    for nonce in range(200):
        outcome = derive_outcome(
            SeedMaterial(server_seed=seed.server_seed, client_seed=seed.client_seed, nonce=nonce),
            modulus=37,
        )
        assert 0 <= outcome <= 36


def test_different_nonces_frequently_give_different_outcomes() -> None:
    server_seed = b"3" * 32
    outcomes = {
        derive_outcome(SeedMaterial(server_seed, "player-1", nonce), modulus=37)
        for nonce in range(200)
    }

    assert len(outcomes) > 1


def test_derive_outcome_matches_the_manual_computation() -> None:
    """Freezes the algorithm: HMAC-SHA256(server_seed, "client:nonce") +
    rejection sampling 4 bytes at a time. If this ever changes, it's a
    version bump of the provably-fair scheme, not a silent refactor.
    """
    server_seed = b"4" * 32
    seed = SeedMaterial(server_seed=server_seed, client_seed="player-1", nonce=99)

    digest = hmac.new(server_seed, b"player-1:99", hashlib.sha256).digest()
    limit = (2**32 // 37) * 37
    expected = None
    for offset in range(0, 32, 4):
        value = int.from_bytes(digest[offset : offset + 4], "big")
        if value < limit:
            expected = value % 37
            break

    assert expected is not None
    assert derive_outcome(seed, modulus=37) == expected
