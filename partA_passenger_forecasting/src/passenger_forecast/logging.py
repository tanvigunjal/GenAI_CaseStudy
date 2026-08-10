"""Small JSON logging boundary that deliberately excludes row payloads."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        body.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _RESERVED and not key.startswith("_")
            }
        )
        if record.exc_info:
            body["exception"] = self.formatException(record.exc_info)
        return json.dumps(body, sort_keys=True, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once for command-line use."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit a structured event; callers must pass only lineage and aggregate fields."""

    logger.info(event, extra=fields)
