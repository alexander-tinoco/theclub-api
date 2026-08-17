"""Keyset pagination cursor (`created_at`, `id`) — opaque to the client,
never an offset: an offset suffers "page drift" if something gets inserted
between two pages read; a keyset doesn't, because each page starts from the
last item seen, not from a numeric position.
"""

import base64
import uuid
from datetime import datetime


class InvalidCursorError(Exception):
    """The cursor doesn't have the shape `encode_cursor` produces."""


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
