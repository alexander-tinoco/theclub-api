"""Provably fair: commit-reveal + derivación determinista del resultado.

Genérico a propósito — este módulo no sabe qué es la ruleta. `roulette/engine.py`
es quien le pide un número módulo 37.
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
    """Lo mínimo necesario para reproducir un resultado."""

    server_seed: bytes
    client_seed: str
    nonce: int


def new_server_seed() -> bytes:
    """32 bytes criptográficamente aleatorios. Se guardan; solo su hash se publica."""
    return secrets.token_bytes(SEED_BYTES)


def hash_seed(server_seed: bytes) -> str:
    """Lo único que se muestra al jugador antes de girar."""
    return hashlib.sha256(server_seed).hexdigest()


def _message(client_seed: str, nonce: int, round_index: int) -> bytes:
    if round_index == 0:
        return f"{client_seed}:{nonce}".encode()
    return f"{client_seed}:{nonce}:r{round_index}".encode()


def _uniform_below(digest: bytes, modulus: int) -> int | None:
    """Busca en `digest`, de 4 en 4 bytes, un valor sin sesgo bajo `modulus`.

    Un bloque se acepta solo si cae por debajo del mayor múltiplo de `modulus`
    que no supera 2**32 — así ningún resto queda sobrerrepresentado. Devuelve
    None si ningún bloque del digest sirvió (ver `derive_outcome`).
    """
    limit = (_CHUNK_SPACE // modulus) * modulus
    for offset in range(0, len(digest) - _CHUNK_SIZE + 1, _CHUNK_SIZE):
        value = int.from_bytes(digest[offset : offset + _CHUNK_SIZE], "big")
        if value < limit:
            return value % modulus
    return None


def derive_outcome(seed: SeedMaterial, *, modulus: int) -> int:
    """HMAC-SHA256 + rejection sampling: un entero en [0, modulus) sin sesgo.

    Determinista: la misma `SeedMaterial` siempre produce el mismo resultado,
    así cualquiera puede verificarlo tras el reveal del server_seed. Para
    modulus=37 la probabilidad de rechazar los 8 bloques de un mismo HMAC es
    indistinguible de cero, pero por rigor el bucle sigue pidiendo HMACs
    adicionales en vez de sesgar el resultado como último recurso.
    """
    round_index = 0
    while True:
        message = _message(seed.client_seed, seed.nonce, round_index)
        digest = hmac.new(seed.server_seed, message, hashlib.sha256).digest()
        result = _uniform_below(digest, modulus)
        if result is not None:
            return result
        round_index += 1
