"""Guarantees that an operation with an `Idempotency-Key` runs at most once
per (user_id, key), even under identical concurrent requests.

Reused by `place_bet` and `deposit` — both move money, and a network retry
must not charge (or credit) twice in either one.

The mechanism is three independent transactions, not one:

1. Reserve the key (INSERT `status='pending'`, committed immediately). The
   table's UNIQUE(user_id, key) is what decides, at the Postgres level,
   which of two simultaneous requests wins — there's no possible race
   window: the second request hits the constraint violation before
   touching any business logic.
2. If the reservation was won: run the real work and mark the key as
   completed, in the same transaction (committed together or not at all).
3. If the real work fails: that transaction rolls back entirely, but the
   'pending' row from (1) was already committed separately and is now
   orphaned — it gets deleted in a third transaction, so a later retry
   with the same key doesn't stay blocked forever looking at an
   "in progress" that no longer is one.
"""

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.idempotency import IdempotencyKeyRepository

#: The longest we wait for a request to finish before assuming the process
#: that started it died halfway through and the 'pending' row is
#: reclaimable garbage. Well above what any real operation takes.
PENDING_RECLAIM_SECONDS = 30

#: Cap on the Idempotency-Key header. 200 is generous for any reasonable
#: client scheme (UUID, ULID, etc.) and stops someone from sending a
#: multi-megabyte string that would end up stored as-is in the table.
IDEMPOTENCY_KEY_MAX_LENGTH = 200


class IdempotencyKeyConflictError(Exception):
    """The same Idempotency-Key was used before with a different request body."""


class IdempotencyInProgressError(Exception):
    """A request with this same key is already being processed right now."""


def hash_request_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


async def _try_reserve(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    key: str,
    request_hash: str,
    ttl_hours: int,
) -> uuid.UUID | None:
    """None if the key already exists (UNIQUE violation); otherwise, the reserved id."""
    async with session_factory() as session:
        try:
            async with session.begin():
                row = await IdempotencyKeyRepository(session).create_pending(
                    user_id=user_id,
                    key=key,
                    request_hash=request_hash,
                    expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
                )
                row_id = row.id
        except IntegrityError:
            return None
        return row_id


async def _resolve_existing(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    key: str,
    request_hash: str,
) -> dict[str, Any] | None:
    """The cached response if it already finished; None if an abandoned row
    was reclaimed and the reservation needs to be retried. Raises if there's
    a real conflict."""
    async with session_factory() as session:
        repo = IdempotencyKeyRepository(session)
        existing = await repo.get(user_id, key)

        if existing is None:
            return None  # another request reclaimed it right between the collision and this read

        if existing.request_hash != request_hash:
            raise IdempotencyKeyConflictError

        if existing.status == "completed":
            return existing.response_body or {}

        stuck = datetime.now(UTC) - existing.created_at > timedelta(seconds=PENDING_RECLAIM_SECONDS)
        if not stuck:
            raise IdempotencyInProgressError

        # No `session.begin()` here: the SELECT above already auto-started a
        # transaction on this session (SQLAlchemy's autobegin) — opening
        # another one on top would raise "a transaction is already begun".
        await repo.delete(existing.id)
        await session.commit()
        return None


async def run_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    key: str,
    request_hash: str,
    ttl_hours: int,
    work: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    row_id = await _try_reserve(
        session_factory, user_id=user_id, key=key, request_hash=request_hash, ttl_hours=ttl_hours
    )

    if row_id is None:
        cached = await _resolve_existing(
            session_factory, user_id=user_id, key=key, request_hash=request_hash
        )
        if cached is not None:
            return cached
        # An abandoned row was reclaimed: a single retry of the reservation.
        row_id = await _try_reserve(
            session_factory,
            user_id=user_id,
            key=key,
            request_hash=request_hash,
            ttl_hours=ttl_hours,
        )
        if row_id is None:
            raise IdempotencyInProgressError

    try:
        async with session_factory() as session, session.begin():
            response_body = await work(session)
            await IdempotencyKeyRepository(session).mark_completed(
                row_id, response_body=response_body
            )
        return response_body
    except Exception:
        async with session_factory() as session, session.begin():
            await IdempotencyKeyRepository(session).delete(row_id)
        raise
