"""Provably fair: commit-reveal + deterministic derivation of the outcome.

Generic on purpose — this module doesn't know what roulette is.
`roulette/engine.py` is the one that asks it for a number modulo 37.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass

SEED_BYTES = 32
_CHUNK_SIZE = 4
_CHUNK_SPACE = 2**32


@dataclass(frozen=True, slots=True)
class SeedMaterial:
    """The minimum needed to reproduce an outcome."""

    server_seed: bytes
    client_seed: str
    nonce: int


def new_server_seed() -> bytes:
    """32 cryptographically random bytes. Stored; only its hash is published."""
    return secrets.token_bytes(SEED_BYTES)


def hash_seed(server_seed: bytes) -> str:
    """The only thing shown to the player before spinning."""
    return hashlib.sha256(server_seed).hexdigest()


def _message(client_seed: str, nonce: int, round_index: int) -> bytes:
    if round_index == 0:
        return f"{client_seed}:{nonce}".encode()
    return f"{client_seed}:{nonce}:r{round_index}".encode()


def _uniform_below(digest: bytes, modulus: int) -> int | None:
    """Scans `digest`, 4 bytes at a time, for an unbiased value under `modulus`.

    A block is only accepted if it falls below the largest multiple of
    `modulus` that doesn't exceed 2**32 — that way no remainder ends up
    overrepresented. Returns None if no block in the digest worked (see
    `derive_outcome`).
    """
    limit = (_CHUNK_SPACE // modulus) * modulus
    for offset in range(0, len(digest) - _CHUNK_SIZE + 1, _CHUNK_SIZE):
        value = int.from_bytes(digest[offset : offset + _CHUNK_SIZE], "big")
        if value < limit:
            return value % modulus
    return None


def derive_outcome(seed: SeedMaterial, *, modulus: int) -> int:
    """HMAC-SHA256 + rejection sampling: an unbiased integer in [0, modulus).

    Deterministic: the same `SeedMaterial` always produces the same
    outcome, so anyone can verify it after the server_seed reveal. For
    modulus=37 the odds of rejecting all 8 blocks of a single HMAC are
    indistinguishable from zero, but for rigor the loop keeps requesting
    additional HMACs instead of biasing the outcome as a last resort.
    """
    round_index = 0
    while True:
        message = _message(seed.client_seed, seed.nonce, round_index)
        digest = hmac.new(seed.server_seed, message, hashlib.sha256).digest()
        result = _uniform_below(digest, modulus)
        if result is not None:
            return result
        round_index += 1
