"""
Structured JSON logging for the whale follower system.

Provides JSONFormatter: a logging.Formatter that outputs structured JSON with
consistent fields: timestamp, level, component, event, data.

Usage:
    from components.logging_json import setup_json_logging
    logger = setup_json_logging(log_level="INFO")

    logger.info("trade_entered", extra={
        "component": "wf_entries",
        "event": "enter",
        "side": "BUY",
        "size": 12.50,
        "price": 0.2891,
    })
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


# Reserved keys that go at top-level, not inside 'data'
_RESERVED_KEYS = {
    "name", "msg", "args", "created", "relativeCreated",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "pathname", "filename", "module", "threadName", "process",
    "processName", "thread", "msecs", "levelno", "levelname",
    "getMessage", "component", "event",
}


class JSONFormatter(logging.Formatter):
    """
    Logging formatter that outputs structured JSON.

    Output schema:
    {
        "timestamp": "2026-05-11T14:30:00.123456Z",
        "level": "INFO",
        "component": "whale_follower",
        "event": "trade_entered",
        "data": { ... all extra fields ... }
    }

    If record.msg is a dict, it is used as the 'data' field directly.
    Otherwise, all extra kwargs passed via extra={} go into 'data'.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Timestamp in ISO 8601 UTC
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        # Top-level fields
        output = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
        }

        # Component (from extra, or fallback to logger name)
        component = getattr(record, "component", None) or record.name
        output["component"] = component

        # Event (the primary message / event name)
        event = getattr(record, "event", None)
        if event is not None:
            output["event"] = event
        elif isinstance(record.msg, str):
            output["event"] = record.msg

        # Build data dict from extra fields
        data = {}
        for key, value in record.__dict__.items():
            if key in _RESERVED_KEYS:
                continue
            # Skip internal Python objects that aren't JSON-serializable
            data[key] = value

        # If msg is a dict, merge it into data
        if isinstance(record.msg, dict):
            data.update(record.msg)
        elif not isinstance(record.msg, str) or record.msg:
            # Non-empty non-dict msg stored as 'message'
            output["message"] = str(record.msg)

        # Attach error info if present
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)

        if data:
            output["data"] = data

        return json.dumps(output, default=str)


def setup_json_logging(log_level: str = "INFO") -> logging.Logger:
    """
    Configure the root logger with JSONFormatter for structured output.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    Returns:
        The configured root logger.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add JSON handler on stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root_logger.addHandler(handler)

    return root_logger
