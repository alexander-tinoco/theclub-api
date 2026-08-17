import base64
import uuid
from datetime import UTC, datetime

import pytest

from app.api.pagination import InvalidCursorError, decode_cursor, encode_cursor

pytestmark = pytest.mark.unit


def test_cursor_roundtrip() -> None:
    created_at = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    item_id = uuid.uuid4()

    cursor = encode_cursor(created_at, item_id)
    decoded_at, decoded_id = decode_cursor(cursor)

    assert decoded_at == created_at
    assert decoded_id == item_id


def test_cursor_basura_levanta() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor("no-es-base64-valido!!!")


def test_cursor_base64_valido_pero_sin_el_separador_esperado() -> None:
    bogus = base64.urlsafe_b64encode(b"sin-el-separador-correcto").decode()

    with pytest.raises(InvalidCursorError):
        decode_cursor(bogus)


def test_cursor_con_uuid_invalido() -> None:
    bogus = base64.urlsafe_b64encode(b"2026-01-01T00:00:00+00:00|no-es-un-uuid").decode()

    with pytest.raises(InvalidCursorError):
        decode_cursor(bogus)
