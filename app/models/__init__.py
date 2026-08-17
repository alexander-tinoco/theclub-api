"""Importar aquí cada modelo es lo que los registra en `Base.metadata` —
Alembic (`env.py`) importa este paquete para poder autogenerar migraciones
comparando el estado real de la base contra estas clases.
"""

from app.models.base import Base
from app.models.fairness import SeedPair
from app.models.idempotency import IdempotencyKey
from app.models.ledger import LedgerEntry
from app.models.outbox import OutboxEvent
from app.models.round import Bet, Round
from app.models.user import User
from app.models.wallet import Wallet

__all__ = [
    "Base",
    "Bet",
    "IdempotencyKey",
    "LedgerEntry",
    "OutboxEvent",
    "Round",
    "SeedPair",
    "User",
    "Wallet",
]
