"""Fixtures de los tests de integración: requieren Postgres real (`make up`).

La migración se aplica una vez por sesión de pytest. Cada test limpia sus
propias tablas al terminar con DELETE, no con el rollback de una transacción
envolvente — el test de concurrencia necesita commits reales, visibles desde
conexiones distintas, y una transacción envolvente sin commit se lo impediría.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
