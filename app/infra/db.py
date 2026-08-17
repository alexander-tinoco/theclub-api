"""SQLAlchemy async engine and unit of work.

A single `AsyncEngine` per process, created in `create_app()` (not in the
lifespan: that way tests that don't start it still get a working
`db_session_factory`); this only defines how it's built and how a
transaction is opened.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

#: If a single statement takes longer than this, Postgres cancels it on its
#: own. Without this, a hung query (deadlock, slow IO) leaves the request
#: waiting forever instead of failing with an error that can be handled.
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
    # expire_on_commit=False: without this, after a commit SQLAlchemy
    # invalidates the ORM objects and the next attribute access triggers an
    # implicit new query — breaks once the object has already been returned
    # outside the session.
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One transaction: commit if everything goes well, rollback if anything raises."""
    async with session_factory() as session, session.begin():
        yield session
