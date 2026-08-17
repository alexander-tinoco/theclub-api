"""alembic upgrade head y downgrade base, probados de verdad contra Postgres,
no solo verificados a mano una vez desde la terminal.
"""

import asyncio

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import _alembic_config

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "users",
    "wallets",
    "ledger_entries",
    "seed_pairs",
    "rounds",
    "bets",
    "idempotency_keys",
    "outbox",
}


def _inspect(sync_conn: Connection) -> list[str]:
    return inspect(sync_conn).get_table_names()


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        return set(await conn.run_sync(_inspect))


async def test_upgrade_head_crea_las_8_tablas(engine: AsyncEngine) -> None:
    assert await _table_names(engine) >= EXPECTED_TABLES


async def test_downgrade_base_es_completamente_reversible(engine: AsyncEngine) -> None:
    cfg = _alembic_config()

    # env.py hace su propio asyncio.run() al cargar — no se puede llamar
    # directamente desde un test async, ya en un loop; se delega a un hilo.
    await asyncio.to_thread(command.downgrade, cfg, "base")
    try:
        remaining = await _table_names(engine)
        assert not (EXPECTED_TABLES & remaining)
    finally:
        # Pase lo que pase, dejar el esquema en head para el resto de la suite.
        await asyncio.to_thread(command.upgrade, cfg, "head")
