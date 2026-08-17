"""Regresión: `alembic/env.py` llamaba a `fileConfig()` con su default
(`disable_existing_loggers=True`), que desactiva cualquier logger ya creado
en ese momento y no listado en `alembic.ini`. La fixture de sesión
`_migrated_schema` (`conftest.py`) corre `alembic upgrade head` *en el mismo
proceso* que pytest — a diferencia de producción, donde Alembic es un
comando aparte (`make db-upgrade`) — y para entonces pytest ya importó toda
`app/` y creó sus loggers de módulo. Sin `disable_existing_loggers=False`,
**todo el logging de la aplicación quedaba silenciosamente descartado
durante la suite entera**, sin ningún error que lo delatara.
"""

import logging

import pytest

pytestmark = pytest.mark.integration


def test_alembic_no_desactiva_loggers_de_la_app() -> None:
    """Se apoya en `_migrated_schema` (autouse, sesión) para forzar que
    Alembic ya haya corrido antes de este assert.
    """
    assert logging.getLogger("canonical").disabled is False
    assert logging.getLogger("app.main").disabled is False
