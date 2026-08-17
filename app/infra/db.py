"""Motor async de SQLAlchemy y unit of work.

Un único `AsyncEngine` por proceso, creado en `create_app()` (no en el
lifespan: así los tests que no lo arrancan siguen teniendo un
`db_session_factory` funcional); aquí solo se define cómo se construye y cómo
se abre una transacción.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

#: Si una sola sentencia tarda más de esto, Postgres la cancela sola. Sin
#: esto, una consulta colgada (deadlock, IO lento) deja el request esperando
#: para siempre en vez de fallar con un error que se pueda manejar.
STATEMENT_TIMEOUT_MS = 30_000


def create_engine(database_url: str, *, pool_size: int) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        pool_pre_ping=True,
        pool_timeout=30,
        connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"},
    )


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
