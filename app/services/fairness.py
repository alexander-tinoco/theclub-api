"""Casos de uso de provably fair: consultar el seed activo, rotarlo."""

import secrets
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.fairness import hash_seed, new_server_seed
from app.repositories.seed_pairs import SeedPairRepository

#: 16 bytes -> 32 caracteres hex, suficiente entropía para que el cliente lo
#: pueda mostrar y verificar sin que sea incómodamente largo.
CLIENT_SEED_BYTES = 16


def generate_client_seed() -> str:
    return secrets.token_hex(CLIENT_SEED_BYTES)


async def get_current_seed(session: AsyncSession, *, user_id: uuid.UUID) -> dict[str, Any]:
    seed_pair = await SeedPairRepository(session).get_active_by_user_id(user_id)
    assert seed_pair is not None  # todo user tiene un seed pair activo desde el registro
    return {
        "server_seed_hash": seed_pair.server_seed_hash,
        "client_seed": seed_pair.client_seed,
        "nonce": seed_pair.nonce,
    }


async def rotate_seed(session: AsyncSession, *, user_id: uuid.UUID) -> dict[str, Any]:
    """Revela el server_seed activo (ya no se puede volver a usar) y activa uno
    nuevo. El cliente puede verificar `sha256(revealed_server_seed) ==
    revealed_server_seed_hash` y recalcular cada giro que hizo con el anterior.
    """
    repo = SeedPairRepository(session)
    current = await repo.get_active_by_user_id(user_id)
    assert current is not None

    await repo.reveal_and_deactivate(current.id)

    new_seed = new_server_seed()
    new_pair = await repo.create_active(
        user_id=user_id,
        server_seed=new_seed,
        server_seed_hash=hash_seed(new_seed),
        client_seed=generate_client_seed(),
    )

    return {
        "revealed_server_seed": current.server_seed.hex(),
        "revealed_server_seed_hash": current.server_seed_hash,
        "new_server_seed_hash": new_pair.server_seed_hash,
        "new_client_seed": new_pair.client_seed,
    }
