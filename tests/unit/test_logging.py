import json
import logging

import pytest

from app.api.request_context import request_id_var, user_id_var
from app.infra.logging import JsonFormatter

pytestmark = pytest.mark.unit


def _make_record(*, msg: str = "algo pasó", exc_info: bool = False) -> logging.LogRecord:
    exc_info_arg = None
    if exc_info:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info_arg = sys.exc_info()
    return logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info_arg,
    )


def test_linea_normal_sin_contexto_no_incluye_request_id_ni_user_id() -> None:
    formatter = JsonFormatter()

    line = json.loads(formatter.format(_make_record()))

    assert line["message"] == "algo pasó"
    assert line["logger"] == "app.test"
    assert "request_id" not in line
    assert "user_id" not in line


def test_linea_normal_incluye_request_id_y_user_id_si_estan_en_contexto() -> None:
    req_token = request_id_var.set("req-123")
    user_token = user_id_var.set("user-456")
    try:
        formatter = JsonFormatter()
        line = json.loads(formatter.format(_make_record()))
    finally:
        request_id_var.reset(req_token)
        user_id_var.reset(user_token)

    assert line["request_id"] == "req-123"
    assert line["user_id"] == "user-456"


def test_linea_con_excepcion_incluye_el_traceback() -> None:
    formatter = JsonFormatter()

    line = json.loads(formatter.format(_make_record(exc_info=True)))

    assert "ValueError" in line["exception"]
    assert "boom" in line["exception"]


def test_linea_canonica_usa_el_dict_de_extra_tal_cual() -> None:
    formatter = JsonFormatter()
    record = _make_record(msg="request_completed")
    record.canonical = {"request_id": "req-1", "user_id": None, "status_code": 201}

    line = json.loads(formatter.format(record))

    assert line["event"] == "request_completed"
    assert line["status_code"] == 201
    assert line["request_id"] == "req-1"
    # el campo "logger" es del camino normal, no de la línea canónica
    assert "logger" not in line
