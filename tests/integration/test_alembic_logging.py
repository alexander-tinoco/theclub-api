"""Regression: `alembic/env.py` used to call `fileConfig()` with its
default (`disable_existing_loggers=True`), which disables any logger
already created at that point and not listed in `alembic.ini`. The
`_migrated_schema` session fixture (`conftest.py`) runs
`alembic upgrade head` *in the same process* as pytest — unlike
production, where Alembic is a separate command (`make db-upgrade`) — and
by then pytest has already imported all of `app/` and created its module
loggers. Without `disable_existing_loggers=False`, **all of the
application's logging was silently dropped for the entire suite**, with no
error to give it away.
"""

import logging

import pytest

pytestmark = pytest.mark.integration


def test_alembic_does_not_disable_the_apps_loggers() -> None:
    """Relies on `_migrated_schema` (autouse, session) to force Alembic to
    have already run before this assert.
    """
    assert logging.getLogger("canonical").disabled is False
    assert logging.getLogger("app.main").disabled is False
