"""Provably fair use cases: look up the active seed, rotate it."""

import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.fairness import hash_seed, new_server_seed
from app.repositories.seed_pairs import SeedPairRepository
from app.services.exceptions import DataIntegrityError

#: 16 bytes -> 32 hex characters, enough entropy for the client to show and
#: verify without being uncomfortably long.
CLIENT_SEED_BYTES = 16


def generate_client_seed() -> str:
    return secrets.token_hex(CLIENT_SEED_BYTES)


@dataclass(frozen=True, slots=True)
class CurrentSeedView:
    server_seed_hash: str
    client_seed: str
    nonce: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_seed_hash": self.server_seed_hash,
            "client_seed": self.client_seed,
            "nonce": self.nonce,
        }


@dataclass(frozen=True, slots=True)
class RotateSeedView:
    revealed_server_seed: str
    revealed_server_seed_hash: str
    new_server_seed_hash: str
    new_client_seed: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "revealed_server_seed": self.revealed_server_seed,
            "revealed_server_seed_hash": self.revealed_server_seed_hash,
            "new_server_seed_hash": self.new_server_seed_hash,
            "new_client_seed": self.new_client_seed,
        }


async def get_current_seed(session: AsyncSession, *, user_id: uuid.UUID) -> CurrentSeedView:
    seed_pair = await SeedPairRepository(session).get_active_by_user_id(user_id)
    if seed_pair is None:
        raise DataIntegrityError(f"user {user_id} has no active seed pair")
    return CurrentSeedView(
        server_seed_hash=seed_pair.server_seed_hash,
        client_seed=seed_pair.client_seed,
        nonce=seed_pair.nonce,
    )


async def rotate_seed(session: AsyncSession, *, user_id: uuid.UUID) -> RotateSeedView:
    """Reveals the active server_seed (it can no longer be used) and
    activates a new one. The client can verify
    `sha256(revealed_server_seed) == revealed_server_seed_hash` and
    recompute every spin they made with the previous one.
    """
    repo = SeedPairRepository(session)
    current = await repo.get_active_by_user_id(user_id)
    if current is None:
        raise DataIntegrityError(f"user {user_id} has no active seed pair")

    await repo.reveal_and_deactivate(current.id)

    new_seed = new_server_seed()
    new_pair = await repo.create_active(
        user_id=user_id,
        server_seed=new_seed,
        server_seed_hash=hash_seed(new_seed),
        client_seed=generate_client_seed(),
    )

    return RotateSeedView(
        revealed_server_seed=current.server_seed.hex(),
        revealed_server_seed_hash=current.server_seed_hash,
        new_server_seed_hash=new_pair.server_seed_hash,
        new_client_seed=new_pair.client_seed,
    )
