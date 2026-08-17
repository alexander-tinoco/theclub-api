"""Logging estructurado en JSON. Sin esto, correlacionar qué pasó en una
petición concreta significa grepear timestamps a ojo — con `request_id` en
cada línea (la canónica y cualquier log suelto de esa misma petición), un
`jq 'select(.request_id == "...")'` alcanza.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.api.request_context import request_id_var, user_id_var


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # La línea canónica ya trae su propio dict armado (`bind_canonical`
        # + lo que junta el middleware); no tiene sentido reconstruirlo aquí
        # ni duplicar `request_id`/`user_id`, que ella ya incluye.
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
