"""Structured JSON logging with a correlation id on every line.

One id is minted per request, returned in ``X-Correlation-Id``, attached to
every log line the request produces, and quoted in any error body. That id is
the whole point: when an operator says "the exposure page showed nothing at
14:07", there is one token that joins their screenshot to the server's record.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

__all__ = [
    "JsonFormatter",
    "configure_logging",
    "current_correlation_id",
    "new_correlation_id",
    "set_correlation_id",
]

_CORRELATION_ID: ContextVar[str] = ContextVar("fiboki_correlation_id", default="")
_ACTOR: ContextVar[str] = ContextVar("fiboki_actor", default="")

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def set_correlation_id(value: str) -> None:
    _CORRELATION_ID.set(value)


def current_correlation_id() -> str:
    return _CORRELATION_ID.get()


def set_actor(value: str) -> None:
    _ACTOR.set(value)


def current_actor() -> str:
    return _ACTOR.get()


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Never multi-line, so log shipping cannot split
    a stack trace across records."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        cid = _CORRELATION_ID.get()
        if cid:
            payload["correlation_id"] = cid
        actor = _ACTOR.get()
        if actor:
            payload["actor"] = actor
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = _safe(value)
        if record.exc_info:
            payload["exc_type"] = getattr(record.exc_info[0], "__name__", "Exception")
            # The formatted traceback goes to the log, never to the HTTP body.
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def _safe(value: Any) -> Any:
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    if isinstance(value, dict | list | tuple):
        try:
            json.dumps(value, default=str)
        except TypeError:  # pragma: no cover - defensive
            return str(value)
        return value
    return str(value)


def configure_logging(level: str = "INFO", *, stream: Any = None) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn duplicates access logs in its own format; route them through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
