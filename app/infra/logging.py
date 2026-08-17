"""Structured JSON logging. Without this, correlating what happened during
a given request means eyeballing timestamps with grep — with `request_id`
on every line (the canonical one and any loose log from that same
request), a `jq 'select(.request_id == "...")'` is enough.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.api.request_context import request_id_var, user_id_var


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # The canonical line already comes with its own dict built
        # (`bind_canonical` + what the middleware collects); no point
        # rebuilding it here or duplicating `request_id`/`user_id`, which it
        # already includes.
        canonical = getattr(record, "canonical", None)
        if canonical is not None:
            payload: dict[str, Any] = {
                "timestamp": self._timestamp(record),
                "level": record.levelname,
                "event": record.getMessage(),
                **canonical,
            }
            return json.dumps(payload, default=str)

        payload = {
            "timestamp": self._timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        user_id = user_id_var.get()
        if user_id is not None:
            payload["user_id"] = user_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)

    @staticmethod
    def _timestamp(record: logging.LogRecord) -> str:
        return datetime.fromtimestamp(record.created, tz=UTC).isoformat()


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
