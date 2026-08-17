"""Garantiza que una operación con `Idempotency-Key` se ejecuta como mucho una
vez por (user_id, key), incluso bajo peticiones concurrentes idénticas.

Reutilizado por `place_bet` y `deposit` — ambos mueven dinero, y un reintento
de red no debe cobrar (ni acreditar) dos veces en ninguno de los dos.

El mecanismo son tres transacciones independientes, no una:

1. Reservar la clave (INSERT `status='pending'`, se comete de inmediato). La
   UNIQUE(user_id, key) de la tabla es quien decide, a nivel de Postgres, cuál
   de dos peticiones simultáneas gana — no hay ventana de carrera posible: la
   segunda petición ve el choque de la constraint antes de tocar nada de negocio.
2. Si se ganó la reserva: ejecutar el trabajo real y marcar la clave como
   completada, en la misma transacción (se comete junto o no se comete nada).
3. Si el trabajo real falla: esa transacción se revierte entera, pero la fila
   'pending' de (1) ya estaba comprometida aparte y queda huérfana — se borra
   en una tercera transacción, para que un reintento posterior con la misma
   clave no se quede bloqueado para siempre viendo un "en curso" que ya no lo está.
"""

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.idempotency import IdempotencyKeyRepository

#: Cuánto se espera, como mucho, a que termine una petición antes de asumir
#: que el proceso que la empezó murió a medio camino y la fila 'pending' es
#: basura reclamable. Muy por encima de lo que tarda cualquier operación real.
PENDING_RECLAIM_SECONDS = 30


class IdempotencyKeyConflictError(Exception):
    """La misma Idempotency-Key se usó antes con un cuerpo de petición distinto."""


class IdempotencyInProgressError(Exception):
    """Ya hay una petición con esta misma clave procesándose en este momento."""


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
    """None si la clave ya existe (choque de UNIQUE); si no, el id reservado."""
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
    """La respuesta cacheada si ya terminó; None si se reclamó una fila
    abandonada y toca reintentar la reserva. Levanta si hay conflicto real."""
    async with session_factory() as session:
        repo = IdempotencyKeyRepository(session)
        existing = await repo.get(user_id, key)

        if existing is None:
            return None  # otra petición la reclamó justo entre el choque y esta lectura

        if existing.request_hash != request_hash:
            raise IdempotencyKeyConflictError

        if existing.status == "completed":
            return existing.response_body or {}

        stuck = datetime.now(UTC) - existing.created_at > timedelta(seconds=PENDING_RECLAIM_SECONDS)
        if not stuck:
            raise IdempotencyInProgressError

        # Sin `session.begin()` aquí: el SELECT de arriba ya auto-inició una
        # transacción en esta sesión (autobegin de SQLAlchemy) — abrir otra
        # encima levantaría "a transaction is already begun".
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
        # Se reclamó una fila abandonada: un único reintento de la reserva.
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
