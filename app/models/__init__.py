"""Importing every model here is what registers them on `Base.metadata` —
Alembic (`env.py`) imports this package so it can autogenerate migrations
by comparing the database's real state against these classes.
"""

from app.models.base import Base
from app.models.fairness import SeedPair
from app.models.idempotency import IdempotencyKey
from app.models.ledger import LedgerEntry
from app.models.outbox import OutboxEvent
from app.models.refresh_token import RefreshToken
from app.models.round import Bet, Round
from app.models.user import User
from app.models.wallet import Wallet

__all__ = [
    "Base",
    "Bet",
    "IdempotencyKey",
    "LedgerEntry",
    "OutboxEvent",
    "RefreshToken",
    "Round",
    "SeedPair",
    "User",
    "Wallet",
]
