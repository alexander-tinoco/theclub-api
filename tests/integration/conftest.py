"""Fixtures for the integration tests: require a real Postgres (`make up`).

The migration is applied once per pytest session. Each test cleans up its
own tables at the end with DELETE, not with an enclosing transaction's
rollback — the concurrency test needs real commits, visible from separate
connections, which an enclosing uncommitted transaction would prevent.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.rate_limit import limiter
from app.config import Settings
from app.infra.db import create_engine as create_app_engine
from app.infra.db import create_session_factory
from app.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema() -> None:
    command.upgrade(_alembic_config(), "head")


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    return Settings(APP_ENV="test")


@pytest.fixture(scope="session")
async def engine(integration_settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = create_app_engine(
        integration_settings.DATABASE_URL, pool_size=integration_settings.DB_POOL_SIZE
    )
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
async def _clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture(autouse=True)
def _reset_rate_limiters(integration_settings: Settings) -> None:
    """Both limiters live in the same Redis, across processes and across
    tests: without this, a test that exhausts its quota leaves the counter
    loaded for the next one that reuses the same IP/key within the same
    real time window.

    `limiter.reset()` (slowapi) only clears keys under its own prefix —
    `/ws`'s `WsConnectRateLimiter` lives under its own (`ws-connect-rl:`)
    and needs its own separate cleanup.
    """
    limiter.reset()
    client = redis.Redis.from_url(integration_settings.REDIS_URL)
    keys = client.keys("ws-connect-rl:*")
    if keys:
        client.delete(*keys)
    client.close()
