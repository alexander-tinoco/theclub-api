"""Cursor de paginación por keyset (`created_at`, `id`) — opaco para el
cliente, nunca offset: un offset sufre "page drift" si algo se inserta entre
dos páginas leídas; un keyset no, porque cada página parte del último
elemento visto, no de una posición numérica.
"""

import base64
import uuid
from datetime import datetime


class InvalidCursorError(Exception):
    """El cursor no tiene la forma que `encode_cursor` produce."""


def encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at_str, id_str = raw.split("|")
        return datetime.fromisoformat(created_at_str), uuid.UUID(id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursorError from exc
