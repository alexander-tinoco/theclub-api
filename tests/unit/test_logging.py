import json
import logging

import pytest

from app.api.request_context import request_id_var, user_id_var
from app.infra.logging import JsonFormatter

pytestmark = pytest.mark.unit


def _make_record(*, msg: str = "something happened", exc_info: bool = False) -> logging.LogRecord:
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


def test_a_normal_line_with_no_context_includes_no_request_id_or_user_id() -> None:
    formatter = JsonFormatter()

    line = json.loads(formatter.format(_make_record()))

    assert line["message"] == "something happened"
    assert line["logger"] == "app.test"
    assert "request_id" not in line
    assert "user_id" not in line


def test_a_normal_line_includes_request_id_and_user_id_if_theyre_in_context() -> None:
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


def test_a_line_with_an_exception_includes_the_traceback() -> None:
    formatter = JsonFormatter()

    line = json.loads(formatter.format(_make_record(exc_info=True)))

    assert "ValueError" in line["exception"]
    assert "boom" in line["exception"]


def test_the_canonical_line_uses_the_extra_dict_as_is() -> None:
    formatter = JsonFormatter()
    record = _make_record(msg="request_completed")
    record.canonical = {"request_id": "req-1", "user_id": None, "status_code": 201}

    line = json.loads(formatter.format(record))

    assert line["event"] == "request_completed"
    assert line["status_code"] == 201
    assert line["request_id"] == "req-1"
    # the "logger" field belongs to the normal path, not the canonical line
    assert "logger" not in line
