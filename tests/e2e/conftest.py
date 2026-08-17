"""Fixtures for the end-to-end run: same as `tests/integration/` (real
Postgres, Redis, and Redpanda via `make up`), duplicated here instead of
shared — `pytest_plugins` in a `conftest.py` that isn't the root one has
been forbidden since pytest 5, and promoting these fixtures to the root
would also put them, unnecessarily, in front of every unit test.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.rate_limit import limiter
from app.config import Settings
from app.infra.db import create_engine as create_app_engine
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


@pytest.fixture(autouse=True)
async def _clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture(autouse=True)
def _reset_rate_limiters(integration_settings: Settings) -> None:
    limiter.reset()
    client = redis.Redis.from_url(integration_settings.REDIS_URL)
    keys = client.keys("ws-connect-rl:*")
    if keys:
        client.delete(*keys)
    client.close()
