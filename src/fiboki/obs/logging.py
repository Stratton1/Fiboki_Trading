"""Structured JSON logging with correlation ids.

The V1 failure this closes
--------------------------
V1 built its "JSON" log lines with an f-string::

    f'{{"level":"{level}","msg":"{message}"}}'

which produced a line that was not JSON the moment a message contained a
quote, a backslash or a newline -- i.e. every traceback and every broker error
body.  The log shipper dropped those lines silently, so the only records that
survived were the boring ones.  The interesting ones -- the ones with the
quoted broker payload in them -- were exactly the ones that vanished.

Here the record is built as a ``dict`` and handed to :func:`json.dumps`.  There
is no string concatenation anywhere in the formatting path, so escaping is
the standard library's problem and it is correct by construction.
``tests/unit/test_obs_logging.py`` asserts a round-trip through
``json.loads`` for a message containing quotes, backslashes, newlines,
control characters and non-ASCII.

Correlation ids
---------------
A :mod:`contextvars` variable carries the correlation id, so a log line
emitted deep inside a strategy evaluation is attributable to the job that
caused it without threading an id through every signature.  The context is
inherited by threads started via :func:`bind`-wrapped callables and by asyncio
tasks, and is restored on exit even when the block raises.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import socket
import sys
import time
import uuid
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from types import MappingProxyType
from typing import Any, TextIO

__all__ = [
    "JsonFormatter",
    "LogContext",
    "bind",
    "clear_context",
    "configure_logging",
    "correlation_id",
    "current_context",
    "get_logger",
    "new_correlation_id",
    "set_correlation_id",
]

#: Attributes :class:`logging.LogRecord` sets itself.  Anything on a record that
#: is not in here arrived via ``extra=`` and belongs in the JSON payload.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

#: The default is an IMMUTABLE empty mapping. A plain ``{}`` default is shared
#: by every context that never set the variable, so one accidental in-place
#: mutation would leak fields into unrelated log records.
_EMPTY: Mapping[str, Any] = MappingProxyType({})
_CONTEXT: contextvars.ContextVar[Mapping[str, Any]] = contextvars.ContextVar(
    "fiboki_log_context", default=_EMPTY
)

#: The correlation id key.  Named once so a dashboard query is not a guess.
CORRELATION_KEY = "correlation_id"


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def current_context() -> dict[str, Any]:
    """The context dict that will be merged into the next log record."""
    return dict(_CONTEXT.get())


def correlation_id() -> str:
    """The active correlation id, or ``""`` when nothing has been bound."""
    value = _CONTEXT.get().get(CORRELATION_KEY, "")
    return str(value) if value else ""


def new_correlation_id(prefix: str = "cid") -> str:
    """Mint a correlation id.  Not bound -- pass it to :func:`bind`."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def set_correlation_id(value: str) -> contextvars.Token[Mapping[str, Any]]:
    """Bind a correlation id for the remainder of this context.

    Returns the token so the caller can reset it; prefer :func:`bind`, which
    resets for you even on an exception.
    """
    merged = dict(_CONTEXT.get())
    merged[CORRELATION_KEY] = value
    return _CONTEXT.set(merged)


def clear_context() -> None:
    _CONTEXT.set(_EMPTY)


@contextmanager
def bind(**fields: Any) -> Iterator[dict[str, Any]]:
    """Merge ``fields`` into the log context for the duration of the block.

    ``bind(correlation_id=..., job_id=...)`` is the normal call.  Nesting
    merges; the inner binding wins on a key collision and the outer one is
    restored on exit, including when the block raises.
    """
    merged = dict(_CONTEXT.get())
    merged.update({k: v for k, v in fields.items() if v is not None})
    token = _CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _CONTEXT.reset(token)


class LogContext:
    """Re-usable binding object.  ``with LogContext(job_id=...):``."""

    __slots__ = ("_fields", "_stack")

    def __init__(self, **fields: Any) -> None:
        self._fields = dict(fields)
        self._stack: list[Any] = []

    def __enter__(self) -> dict[str, Any]:
        cm = bind(**self._fields)
        self._stack.append(cm)
        return cm.__enter__()

    def __exit__(self, *exc: object) -> None:
        cm = self._stack.pop()
        cm.__exit__(*exc)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def _fallback(value: Any) -> Any:
    """Last-resort coercion for a value ``json`` cannot encode.

    A log line must never be lost because somebody put a ``Decimal`` or a
    dataclass in ``extra=``.  Anything unknown becomes its ``repr``.
    """
    try:
        return repr(value)
    except Exception:  # pragma: no cover - a __repr__ that raises
        return f"<unreprable {type(value).__name__}>"


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as exactly one line of valid JSON.

    Every value goes through :func:`json.dumps`.  Nothing is interpolated into
    a JSON-shaped string, which is the entire point of this class.
    """

    def __init__(
        self,
        *,
        service: str = "fiboki",
        component: str = "",
        static_fields: Mapping[str, Any] | None = None,
        include_source: bool = True,
        ensure_ascii: bool = False,
    ) -> None:
        super().__init__()
        self.service = service
        self.component = component
        self.static_fields = dict(static_fields or {})
        self.include_source = include_source
        self.ensure_ascii = ensure_ascii
        self._host = socket.gethostname()
        self._pid = os.getpid()

    def _base(self, record: logging.LogRecord) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "host": self._host,
            "pid": self._pid,
        }
        if self.component:
            payload["component"] = self.component
        if self.include_source:
            payload["src"] = f"{record.module}:{record.funcName}:{record.lineno}"
            payload["thread"] = record.threadName
        return payload

    def format(self, record: logging.LogRecord) -> str:
        payload = self._base(record)
        payload.update(self.static_fields)
        payload.update(current_context())

        # The message.  record.getMessage() applies %-args; if THAT raises
        # (mismatched args is the classic) we still emit a line rather than
        # letting logging swallow the event.
        try:
            payload["msg"] = record.getMessage()
        except Exception as exc:  # pragma: no cover - defensive
            payload["msg"] = f"<unformattable log message: {exc!r}>"
            payload["raw_msg"] = str(record.msg)

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        # Never let an ``extra`` shadow a structural field; a dashboard that
        # filters on ``level`` must not be defeated by extra={"level": ...}.
        for key, value in extras.items():
            payload["x_" + key if key in payload else key] = value

        if record.exc_info:
            payload["exc_type"] = getattr(record.exc_info[0], "__name__", "")
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        try:
            line = json.dumps(
                payload,
                ensure_ascii=self.ensure_ascii,
                default=_fallback,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            line = json.dumps(
                {
                    "ts": payload.get("ts"),
                    "level": "ERROR",
                    "logger": record.name,
                    "msg": "log record was not serialisable",
                    "error": repr(exc),
                },
                separators=(",", ":"),
            )
        # A newline inside the payload is escaped by json.dumps, so the line
        # is single-line by construction.  Assert it cheaply rather than trust.
        return line.replace("\n", "\\n")


class PlainFormatter(logging.Formatter):
    """Human formatter for an interactive terminal.  Same context, no JSON."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = current_context()
        cid = ctx.get(CORRELATION_KEY, "")
        prefix = f"[{cid}] " if cid else ""
        base = super().format(record)
        return f"{prefix}{base}"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def configure_logging(
    *,
    level: int | str = "INFO",
    stream: TextIO | None = None,
    json_output: bool | None = None,
    service: str = "fiboki",
    component: str = "",
    static_fields: Mapping[str, Any] | None = None,
    replace_handlers: bool = True,
) -> logging.Logger:
    """Install the root handler.  Idempotent enough to call from any entrypoint.

    ``json_output`` defaults to *on* unless ``FIBOKI_LOG_FORMAT=plain`` or the
    stream is an interactive tty, because a human reading a worker in the
    foreground should not have to pipe it through ``jq``.
    """
    target = stream if stream is not None else sys.stderr
    if json_output is None:
        env = os.environ.get("FIBOKI_LOG_FORMAT", "").strip().lower()
        if env in {"plain", "text", "console"}:
            json_output = False
        elif env in {"json"}:
            json_output = True
        else:
            json_output = not bool(getattr(target, "isatty", lambda: False)())

    handler = logging.StreamHandler(target)
    if json_output:
        handler.setFormatter(
            JsonFormatter(service=service, component=component, static_fields=static_fields)
        )
    else:
        handler.setFormatter(
            PlainFormatter("%(asctime)s %(levelname)-7s %(name)s %(message)s")
        )

    root = logging.getLogger()
    if replace_handlers:
        for existing in list(root.handlers):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    _quiet_transport_loggers()
    return root


#: Third-party loggers that write one INFO line per HTTP request. A poller on a
#: 30 s cadence (the quote recorder, the news service) would otherwise put
#: thousands of lines a day into its launchd log, which nothing rotates.
#: Failures still surface: WARNING and above pass, and Fiboki's own clients
#: report errors through their own loggers and poll reports.
NOISY_TRANSPORT_LOGGERS: tuple[str, ...] = ("httpx", "httpcore")


def _quiet_transport_loggers() -> None:
    """Hold the per-request transport loggers at WARNING unless told otherwise.

    ``FIBOKI_LOG_HTTP=INFO`` (or DEBUG) restores the request lines for a
    debugging session.
    """
    wanted = os.environ.get("FIBOKI_LOG_HTTP", "").strip().upper() or "WARNING"
    level = logging.getLevelName(wanted)
    if not isinstance(level, int):
        level = logging.WARNING
    for name in NOISY_TRANSPORT_LOGGERS:
        logging.getLogger(name).setLevel(level)


class _MergingAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """A ``LoggerAdapter`` that MERGES its static fields with per-call ``extra``.

    The stdlib adapter *replaces* ``kwargs["extra"]`` with ``self.extra``, so a
    per-call ``extra=`` silently discards the logger's static fields.  That is
    a data-loss bug in a structured-logging setup, so it is fixed here rather
    than worked around at every call site.
    """

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        merged = dict(self.extra or {})
        merged.update(kwargs.get("extra") or {})
        kwargs["extra"] = merged
        return msg, kwargs


def get_logger(name: str, **static: Any) -> logging.LoggerAdapter:  # type: ignore[type-arg]
    """A logger whose every record carries ``static`` in the JSON payload."""
    return _MergingAdapter(logging.getLogger(name), static)
