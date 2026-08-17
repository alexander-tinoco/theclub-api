"""Motor async de SQLAlchemy y unit of work.

Un único `AsyncEngine` por proceso (se crea en el lifespan de FastAPI, a partir
de la Fase 5); aquí solo se define cómo se construye y cómo se abre una
transacción.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, pool_size: int) -> AsyncEngine:
    return create_async_engine(database_url, pool_size=pool_size, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: sin esto, tras el commit SQLAlchemy invalida los
    # objetos ORM y el siguiente acceso a un atributo dispara una consulta
    # nueva implícita — rompe cuando el objeto ya se devolvió fuera de la sesión.
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Una transacción: commit si todo va bien, rollback si algo levanta."""
    async with session_factory() as session, session.begin():
        yield session
