"""Alert taxonomy, dispatcher, channels, and an ACTIVE heartbeat watchdog.

The V1 failures this closes
---------------------------
1. **There was no ``worker_down`` event and no ``heartbeat_stale`` event.**
   The alerting channel worked; it simply had nothing to send, because the
   taxonomy only contained trade lifecycle events.  A dead worker produced
   silence, and silence is indistinguishable from "no signals today".
2. **Heartbeat freshness was computed when a human loaded a page.**  It was
   therefore not monitoring, it was a dashboard widget.  If nobody opened the
   page over a weekend, the worker had been dead for two days and nothing had
   evaluated the fact.  :class:`HeartbeatWatchdog` here runs on its own timer
   and fires whether or not anybody is looking.
3. **A channel exception killed the dispatch.**  One misconfigured webhook
   suppressed every other channel for the same alert.  Here each channel is
   isolated: a channel that raises is counted, logged and stepped over.

Delivery guarantees, stated honestly
------------------------------------
Below CRITICAL the dispatcher is best-effort and in-process: an alert raised in
the instant before a hard kill can be lost.  The mitigation is that the
conditions that matter (a stale heartbeat, a divergent reconciliation) are
*level*, not *edge*: the watchdog re-evaluates them on its next tick and fires
again.

CRITICAL alerts to a REMOTE channel (webhook, Telegram) go through an
:class:`AlertOutbox`: the row is written to SQLite (WAL, ``synchronous=FULL``)
BEFORE the send is attempted and marked delivered only after the transport
returned, so a failed or interrupted send is retried by
:meth:`AlertDispatcher.retry_pending` (the watchdog calls it every tick).  That
is at-least-once: a crash between the send and the mark re-sends, and a
duplicate page is the accepted price.  Anything that must not be lost still
belongs in a ledger, not in an alert.

Transports
----------
The channels still take an injected transport so tests never open a socket.
:func:`build_default_dispatcher` now injects :func:`httpx_transport` when the
channel's environment is present (audit F, P1-9: before this, a configured
Telegram channel raised on every send because nothing supplied a transport).
Delivery errors are re-raised with the URL and token removed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from fiboki.core.durable import (
    TornTail,
    configure_sqlite_connection,
    durable_append,
    frame_line,
    read_payloads,
    set_torn_tail_hook,
)
from fiboki.core.paths import resolve_paths
from fiboki.obs import metrics as _metrics
from fiboki.obs.health import DEFAULT_HEALTH_THRESHOLDS, HealthThresholds
from fiboki.obs.logging import correlation_id

__all__ = [
    "Alert",
    "AlertChannel",
    "AlertDeliveryError",
    "AlertDispatcher",
    "AlertEvent",
    "AlertOutbox",
    "ConsoleChannel",
    "FileChannel",
    "HeartbeatWatchdog",
    "MemoryChannel",
    "OutboxChannel",
    "Severity",
    "TelegramChannel",
    "TelegramConfig",
    "WebhookChannel",
    "WebhookConfig",
    "default_severity",
    "httpx_transport",
]

_log = logging.getLogger("fiboki.obs.alerts")


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}[self.value]

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank >= other.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank > other.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank <= other.rank
        return NotImplemented

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank < other.rank
        return NotImplemented


class AlertEvent(str, Enum):
    """The complete taxonomy.  Operational events come FIRST, deliberately.

    V1's enum began and ended with the trade lifecycle, which encoded the
    assumption that the interesting failures happen to trades.  They do not.
    The interesting failures happen to the machinery that is supposed to be
    producing trades, and they are silent.
    """

    # -- operational / infrastructure (absent in V1) ---------------------
    WORKER_DOWN = "worker_down"
    HEARTBEAT_STALE = "heartbeat_stale"
    WORKER_LEASE_CONTENDED = "worker_lease_contended"
    RECONCILIATION_DIVERGENCE = "reconciliation_divergence"
    DATA_STALE = "data_stale"
    DATA_QUALITY_DEFECT = "data_quality_defect"
    BROKER_UNHEALTHY = "broker_unhealthy"
    RISK_LIMIT_BREACH = "risk_limit_breach"
    KILL_SWITCH_ACTIVATED = "kill_switch_activated"
    KILL_SWITCH_DEACTIVATED = "kill_switch_deactivated"
    ORDER_REJECTED_REPEATEDLY = "order_rejected_repeatedly"
    STRATEGY_DEGRADED = "strategy_degraded"
    #: A pre-registered stopping rule FIRED. The halt is latched and only a
    #: named operator can release it. Distinct from DEGRADED because a
    #: degradation is a score drifting and a halt is a rule that has already
    #: taken the strategy out of service -- routing both to one event made an
    #: alert channel unable to tell "watch this" from "this has stopped".
    STRATEGY_HALTED = "strategy_halted"
    #: A strategy was demoted into QUARANTINED. It cannot trade in any mode
    #: until a human puts it back, which is a different message from a demotion
    #: to WATCH or DEGRADED.
    STRATEGY_QUARANTINED = "strategy_quarantined"
    QUEUE_BACKED_UP = "queue_backed_up"
    JOB_DEAD_LETTERED = "job_dead_lettered"
    SWEEP_NO_DATA_EXCEEDED = "sweep_no_data_exceeded"
    MIGRATION_DRIFT = "migration_drift"
    SPREAD_MODEL_DIVERGENCE = "spread_model_divergence"
    #: A durable ledger (intents, kill switch, alerts) had a torn final record,
    #: which was quarantined to ``<file>.torn-<ts>``. A crash happened mid-write.
    LEDGER_TORN_TAIL = "ledger_torn_tail"
    #: ``fiboki alerts test``: proves a channel delivers, on demand.
    ALERT_TEST = "alert_test"

    # -- trade lifecycle -------------------------------------------------
    SIGNAL_GENERATED = "signal_generated"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_ACCEPTED = "order_accepted"
    ORDER_REJECTED = "order_rejected"
    ORDER_UNKNOWN = "order_unknown"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    STOP_LOSS_HIT = "stop_loss_hit"
    TAKE_PROFIT_HIT = "take_profit_hit"
    POSITION_FLATTENED = "position_flattened"


#: Default severity per event.  A dispatcher may override per alert, but the
#: default is here so no call site has to decide how bad a dead worker is.
_DEFAULT_SEVERITY: dict[AlertEvent, Severity] = {
    AlertEvent.WORKER_DOWN: Severity.CRITICAL,
    AlertEvent.HEARTBEAT_STALE: Severity.ERROR,
    AlertEvent.WORKER_LEASE_CONTENDED: Severity.CRITICAL,
    AlertEvent.RECONCILIATION_DIVERGENCE: Severity.CRITICAL,
    AlertEvent.DATA_STALE: Severity.WARNING,
    AlertEvent.DATA_QUALITY_DEFECT: Severity.WARNING,
    AlertEvent.BROKER_UNHEALTHY: Severity.ERROR,
    AlertEvent.RISK_LIMIT_BREACH: Severity.CRITICAL,
    AlertEvent.KILL_SWITCH_ACTIVATED: Severity.CRITICAL,
    AlertEvent.KILL_SWITCH_DEACTIVATED: Severity.WARNING,
    AlertEvent.ORDER_REJECTED_REPEATEDLY: Severity.ERROR,
    AlertEvent.STRATEGY_DEGRADED: Severity.WARNING,
    AlertEvent.STRATEGY_HALTED: Severity.CRITICAL,
    AlertEvent.STRATEGY_QUARANTINED: Severity.ERROR,
    AlertEvent.QUEUE_BACKED_UP: Severity.WARNING,
    AlertEvent.JOB_DEAD_LETTERED: Severity.ERROR,
    AlertEvent.SWEEP_NO_DATA_EXCEEDED: Severity.ERROR,
    AlertEvent.MIGRATION_DRIFT: Severity.ERROR,
    AlertEvent.SPREAD_MODEL_DIVERGENCE: Severity.WARNING,
    AlertEvent.LEDGER_TORN_TAIL: Severity.CRITICAL,
    AlertEvent.ALERT_TEST: Severity.WARNING,
    AlertEvent.SIGNAL_GENERATED: Severity.INFO,
    AlertEvent.ORDER_SUBMITTED: Severity.INFO,
    AlertEvent.ORDER_ACCEPTED: Severity.INFO,
    AlertEvent.ORDER_REJECTED: Severity.WARNING,
    AlertEvent.ORDER_UNKNOWN: Severity.ERROR,
    AlertEvent.POSITION_OPENED: Severity.INFO,
    AlertEvent.POSITION_CLOSED: Severity.INFO,
    AlertEvent.STOP_LOSS_HIT: Severity.INFO,
    AlertEvent.TAKE_PROFIT_HIT: Severity.INFO,
    AlertEvent.POSITION_FLATTENED: Severity.WARNING,
}


def default_severity(event: AlertEvent) -> Severity:
    return _DEFAULT_SEVERITY.get(event, Severity.WARNING)


@dataclass(frozen=True, slots=True)
class Alert:
    """One alert.  Serialisable, because every channel needs it as data."""

    event: AlertEvent
    message: str
    severity: Severity = Severity.WARNING
    at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    source: str = ""
    correlation_id: str = ""
    dedupe_key: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        """What repeat-suppression compares.  Defaults to event+source."""
        return self.dedupe_key or f"{self.event.value}:{self.source}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "severity": self.severity.value,
            "message": self.message,
            "at": self.at.astimezone(UTC).isoformat(),
            "source": self.source,
            "correlation_id": self.correlation_id,
            "dedupe_key": self.key(),
            "context": dict(self.context),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, separators=(",", ":"))

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> Alert:
        """Inverse of :meth:`to_dict`, for the outbox."""
        return Alert(
            event=AlertEvent(raw["event"]),
            message=str(raw.get("message", "")),
            severity=Severity(raw.get("severity", Severity.WARNING.value)),
            at=datetime.fromisoformat(str(raw["at"])),
            source=str(raw.get("source", "")),
            correlation_id=str(raw.get("correlation_id", "")),
            dedupe_key=str(raw.get("dedupe_key", "")),
            context=dict(raw.get("context") or {}),
        )

    def render_text(self) -> str:
        head = f"[{self.severity.value.upper()}] {self.event.value}: {self.message}"
        if not self.context:
            return head
        detail = " ".join(f"{k}={v}" for k, v in sorted(self.context.items()))
        return f"{head} | {detail}"


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@runtime_checkable
class AlertChannel(Protocol):
    """A destination.  ``send`` may raise; the dispatcher isolates it."""

    name: str

    def send(self, alert: Alert) -> None: ...


@dataclass
class MemoryChannel:
    """Collects alerts in a list.  The fixture every test uses."""

    name: str = "memory"
    sent: list[Alert] = field(default_factory=list)
    fail_on: frozenset[AlertEvent] = frozenset()

    def send(self, alert: Alert) -> None:
        if alert.event in self.fail_on:
            raise RuntimeError(f"memory channel configured to fail on {alert.event.value}")
        self.sent.append(alert)

    def events(self) -> list[AlertEvent]:
        return [a.event for a in self.sent]

    def clear(self) -> None:
        self.sent.clear()


@dataclass
class ConsoleChannel:
    """Writes to a stream.  Defaults to the logger, so it inherits JSON output."""

    name: str = "console"
    stream: Any = None

    def send(self, alert: Alert) -> None:
        if self.stream is not None:
            self.stream.write(alert.render_text() + "\n")
            flush = getattr(self.stream, "flush", None)
            if flush:
                flush()
            return
        level = {
            Severity.INFO: logging.INFO,
            Severity.WARNING: logging.WARNING,
            Severity.ERROR: logging.ERROR,
            Severity.CRITICAL: logging.CRITICAL,
        }[alert.severity]
        _log.log(level, alert.message, extra={"alert": alert.to_dict()})


@dataclass
class FileChannel:
    """Append-only, self-verifying JSONL, durable on every send.

    Durable because the interesting alert is the last one before the process
    died: each line goes through :func:`fiboki.core.durable.durable_append`
    (CRC32-framed, ``F_FULLFSYNC`` on macOS).  The framed line is still one
    JSON object, so the incidents router and ``jq`` read it unchanged.
    ``fsync=False`` keeps the framing but skips the flush, for tests.
    """

    path: Path
    name: str = "file"
    fsync: bool = True

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, alert: Alert) -> None:
        if self.fsync:
            durable_append(self.path, alert.to_json())
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(frame_line(alert.to_json()) + "\n")

    def read(self) -> list[dict[str, Any]]:
        """Every verified alert. A torn last line is quarantined, never raised."""
        return [json.loads(payload) for payload in read_payloads(self.path)]


#: What a transport must look like.  ``httpx.post``-shaped, but injected so the
#: test suite never opens a socket and CI never needs a credential.
Transport = Callable[[str, dict[str, Any], Mapping[str, str], float], Any]


class AlertDeliveryError(RuntimeError):
    """A remote channel did not accept an alert. The message carries no secret."""


_TELEGRAM_TOKEN_IN_URL = re.compile(r"/bot[^/\s\"']+/")


class _RedactBotToken(logging.Filter):
    """httpx logs every request URL at INFO, and a Telegram URL embeds the token."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _TELEGRAM_TOKEN_IN_URL.sub("/bot***/", message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


_REDACTOR = _RedactBotToken()


def _install_http_log_redaction() -> None:
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if _REDACTOR not in logger.filters:
            logger.addFilter(_REDACTOR)


def httpx_transport(client: Any = None) -> Transport:
    """A :data:`Transport` over ``httpx``. Imported lazily; injected, never global.

    ``client`` is an ``httpx.Client``; a test passes one built on
    ``httpx.MockTransport`` so nothing leaves the process.  A non-2xx response
    raises :class:`AlertDeliveryError` naming only the status and the host: a
    Telegram URL embeds the bot token and a webhook URL is itself a secret.
    """
    import httpx

    _install_http_log_redaction()
    http = client if client is not None else httpx.Client()

    def _post(url: str, payload: dict[str, Any], headers: Mapping[str, str], timeout: float) -> Any:
        host = urlsplit(url).hostname or "?"
        try:
            response = http.post(url, json=payload, headers=dict(headers), timeout=timeout)
        except httpx.HTTPError as exc:
            raise AlertDeliveryError(f"{type(exc).__name__} posting to {host}") from None
        if response.status_code >= 300:
            raise AlertDeliveryError(f"HTTP {response.status_code} from {host}")
        return response

    return _post


def _scrubbed(exc: BaseException, *secrets: str) -> AlertDeliveryError:
    text = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return AlertDeliveryError(text)


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 5.0
    min_severity: Severity = Severity.WARNING


@dataclass
class WebhookChannel:
    """Posts the alert as JSON.  The transport is injected, never imported.

    No credential is read here and none is stored.  A deployment supplies the
    URL through the environment; a test supplies a recording function.
    """

    config: WebhookConfig
    transport: Transport | None = None
    name: str = "webhook"

    @classmethod
    def from_env(
        cls, transport: Transport | None = None, *, env: Mapping[str, str] | None = None
    ) -> WebhookChannel | None:
        environ = env if env is not None else os.environ
        url = environ.get("FIBOKI_ALERT_WEBHOOK_URL", "").strip()
        if not url:
            return None
        return cls(WebhookConfig(url=url), transport=transport)

    def send(self, alert: Alert) -> None:
        if alert.severity.rank < self.config.min_severity.rank:
            return
        if self.transport is None:
            raise RuntimeError(
                "WebhookChannel has no transport. Inject one "
                "(e.g. lambda url, payload, headers, timeout: httpx.post(...)); "
                "this module deliberately does not import an HTTP client."
            )
        try:
            self.transport(
                self.config.url,
                alert.to_dict(),
                dict(self.config.headers),
                self.config.timeout_seconds,
            )
        except AlertDeliveryError:
            raise
        except Exception as exc:
            # A webhook URL is a credential: never let it reach a log line.
            raise _scrubbed(exc, self.config.url) from None


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    """Bot token and chat id.  NEVER defaulted, NEVER logged.

    ``__repr__`` is suppressed for the token so a config dump in a traceback
    does not leak it.
    """

    bot_token: str
    chat_id: str
    api_base: str = "https://api.telegram.org"
    timeout_seconds: float = 5.0
    min_severity: Severity = Severity.WARNING

    def method_url(self, method: str = "sendMessage") -> str:
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    def redacted(self) -> dict[str, Any]:
        tail = self.bot_token[-4:] if len(self.bot_token) >= 4 else ""
        return {
            "bot_token": f"***{tail}" if tail else "***",
            "chat_id": self.chat_id,
            "api_base": self.api_base,
        }


@dataclass
class TelegramChannel:
    """Telegram delivery as an INTERFACE, fixture-tested, credential-free here.

    The channel builds the request and hands it to an injected transport.  No
    token is hardcoded, none is committed, and the only way to get one in is
    ``FIBOKI_TELEGRAM_BOT_TOKEN`` / ``FIBOKI_TELEGRAM_CHAT_ID`` at runtime.
    ``from_env`` returns ``None`` when they are absent, so an un-configured
    deployment degrades to "no Telegram" rather than to a crash loop.
    """

    config: TelegramConfig
    transport: Transport | None = None
    name: str = "telegram"
    parse_mode: str = "HTML"

    @classmethod
    def from_env(
        cls, transport: Transport | None = None, *, env: Mapping[str, str] | None = None
    ) -> TelegramChannel | None:
        environ = env if env is not None else os.environ
        token = environ.get("FIBOKI_TELEGRAM_BOT_TOKEN", "").strip()
        chat = environ.get("FIBOKI_TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat:
            return None
        return cls(TelegramConfig(bot_token=token, chat_id=chat), transport=transport)

    def build_payload(self, alert: Alert) -> dict[str, Any]:
        # Display glyphs in an outgoing Telegram message, not identifiers, so
        # the ambiguous-character lint does not apply to them.
        icon = {
            Severity.INFO: "ℹ",  # noqa: RUF001
            Severity.WARNING: "⚠",
            Severity.ERROR: "❌",
            Severity.CRITICAL: "\U0001f6a8",
        }[alert.severity]
        lines = [
            f"{icon} <b>{_html_escape(alert.event.value)}</b>",
            _html_escape(alert.message),
        ]
        if alert.source:
            lines.append(f"source: <code>{_html_escape(alert.source)}</code>")
        for key in sorted(alert.context):
            lines.append(f"{_html_escape(key)}: <code>{_html_escape(alert.context[key])}</code>")
        return {
            "chat_id": self.config.chat_id,
            "text": "\n".join(lines),
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True,
        }

    def send(self, alert: Alert) -> None:
        if alert.severity.rank < self.config.min_severity.rank:
            return
        if self.transport is None:
            raise RuntimeError(
                "TelegramChannel has no transport. Inject one at wiring time; "
                "this module does not import an HTTP client and holds no credential."
            )
        try:
            self.transport(
                self.config.method_url(),
                self.build_payload(alert),
                {"Content-Type": "application/json"},
                self.config.timeout_seconds,
            )
        except AlertDeliveryError:
            raise
        except Exception as exc:
            # The method URL embeds the bot token.
            raise _scrubbed(exc, self.config.bot_token) from None


def _html_escape(value: Any) -> str:
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Outbox: at-least-once delivery of CRITICAL alerts to remote channels
# ---------------------------------------------------------------------------


_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS alert_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    alert_json TEXT NOT NULL,
    enqueued_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    delivered_at TEXT
)
"""


@dataclass(frozen=True, slots=True)
class OutboxRow:
    id: int
    channel: str
    alert: Alert
    attempts: int
    last_error: str


class AlertOutbox:
    """SQLite outbox. A row is written BEFORE the send and cleared after it.

    This is an outbox, not a ledger: rows change state (attempts, delivered).
    The record of what was alerted is the ``FileChannel`` log.  Retry backoff
    doubles from ``base_backoff_seconds`` up to ``max_backoff_seconds``; there
    is no give-up, because a CRITICAL alert nobody received is still owed.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        base_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 3600.0,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.clock = clock
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(_OUTBOX_DDL)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
        configure_sqlite_connection(conn, synchronous="FULL")
        return conn

    def enqueue(self, channel: str, alert: Alert) -> int:
        now = self.clock().isoformat()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO alert_outbox (channel, alert_json, enqueued_at, next_attempt_at)"
                " VALUES (?, ?, ?, ?)",
                (channel, alert.to_json(), now, now),
            )
            return int(cur.lastrowid or 0)

    def mark_delivered(self, row_id: int) -> None:
        now = self.clock().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE alert_outbox SET delivered_at = ?, attempts = attempts + 1,"
                " last_attempt_at = ? WHERE id = ?",
                (now, now, row_id),
            )

    def mark_failed(self, row_id: int, error: str) -> None:
        now = self.clock()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM alert_outbox WHERE id = ?", (row_id,)
            ).fetchone()
            attempts = int(row[0]) + 1 if row else 1
            delay = min(
                self.base_backoff_seconds * (2 ** (attempts - 1)), self.max_backoff_seconds
            )
            conn.execute(
                "UPDATE alert_outbox SET attempts = ?, last_attempt_at = ?,"
                " next_attempt_at = ?, last_error = ? WHERE id = ?",
                (
                    attempts,
                    now.isoformat(),
                    datetime.fromtimestamp(now.timestamp() + delay, tz=UTC).isoformat(),
                    error[:500],
                    row_id,
                ),
            )

    def pending(self, *, due_only: bool = True) -> list[OutboxRow]:
        now = self.clock().isoformat()
        query = "SELECT id, channel, alert_json, attempts, last_error FROM alert_outbox"
        query += " WHERE delivered_at IS NULL"
        params: tuple[Any, ...] = ()
        if due_only:
            query += " AND next_attempt_at <= ?"
            params = (now,)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query + " ORDER BY id", params).fetchall()
        return [
            OutboxRow(int(r[0]), str(r[1]), Alert.from_dict(json.loads(r[2])), int(r[3]), str(r[4]))
            for r in rows
        ]

    def counts(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            undelivered = conn.execute(
                "SELECT COUNT(*) FROM alert_outbox WHERE delivered_at IS NULL"
            ).fetchone()[0]
            delivered = conn.execute(
                "SELECT COUNT(*) FROM alert_outbox WHERE delivered_at IS NOT NULL"
            ).fetchone()[0]
        return {"undelivered": int(undelivered), "delivered": int(delivered)}

    def retry(self, channels: Mapping[str, AlertChannel]) -> tuple[int, int]:
        """Re-send every due row whose channel is in ``channels``.

        Returns ``(delivered, failed)``.  A row for a channel this process does
        not have stays pending: it is owed by whichever process does.
        """
        delivered = failed = 0
        for row in self.pending():
            channel = channels.get(row.channel)
            if channel is None:
                continue
            try:
                channel.send(row.alert)
            except Exception as exc:
                self.mark_failed(row.id, f"{type(exc).__name__}: {exc}")
                failed += 1
                continue
            self.mark_delivered(row.id)
            delivered += 1
        return delivered, failed


@dataclass
class OutboxChannel:
    """Wraps a remote channel so its CRITICAL alerts are delivered at least once.

    Below ``min_severity`` it is a plain pass-through.  At or above it, the
    alert is enqueued durably first, then sent; a send that raises leaves the
    row pending for :meth:`AlertOutbox.retry` and still raises, so the
    dispatcher counts the failure exactly as before.
    """

    inner: AlertChannel
    outbox: AlertOutbox
    min_severity: Severity = Severity.CRITICAL
    name: str = ""

    def __post_init__(self) -> None:
        self.name = self.name or getattr(self.inner, "name", type(self.inner).__name__)

    def send(self, alert: Alert) -> None:
        if alert.severity.rank < self.min_severity.rank:
            self.inner.send(alert)
            return
        row_id = self.outbox.enqueue(self.name, alert)
        try:
            self.inner.send(alert)
        except Exception as exc:
            self.outbox.mark_failed(row_id, f"{type(exc).__name__}: {exc}")
            raise
        self.outbox.mark_delivered(row_id)

    def retry(self) -> tuple[int, int]:
        return self.outbox.retry({self.name: self.inner})


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class AlertDispatcher:
    """Fans one alert out to every channel, isolating failures.

    Repeat suppression is *time-windowed per dedupe key*, not "fire once":
    a condition that is still true after the window fires again.  A one-shot
    alert for a level condition is how a stale heartbeat gets alerted at 02:00
    and then never mentioned again.
    """

    def __init__(
        self,
        channels: Sequence[AlertChannel] = (),
        *,
        min_severity: Severity = Severity.INFO,
        repeat_after_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        source: str = "",
        registry: _metrics.MetricRegistry | None = None,
    ) -> None:
        self.channels: list[AlertChannel] = list(channels)
        self.min_severity = min_severity
        self.repeat_after_seconds = repeat_after_seconds
        self.clock = clock
        self.source = source
        self._registry = registry
        self._last_sent: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}
        self._lock = threading.Lock()
        self.delivery_failures: list[tuple[str, Alert, str]] = []

    def add_channel(self, channel: AlertChannel) -> AlertDispatcher:
        self.channels.append(channel)
        return self

    def channel_names(self) -> tuple[str, ...]:
        return tuple(getattr(c, "name", type(c).__name__) for c in self.channels)

    # -- the call every caller makes -------------------------------------

    def fire(
        self,
        event: AlertEvent,
        message: str,
        *,
        severity: Severity | None = None,
        source: str | None = None,
        dedupe_key: str = "",
        force: bool = False,
        **context: Any,
    ) -> Alert | None:
        """Build and dispatch.  Returns the alert, or ``None`` if suppressed."""
        alert = Alert(
            event=event,
            message=message,
            severity=severity or default_severity(event),
            source=source if source is not None else self.source,
            correlation_id=correlation_id(),
            dedupe_key=dedupe_key,
            context=dict(context),
        )
        return self.dispatch(alert, force=force)

    def dispatch(self, alert: Alert, *, force: bool = False) -> Alert | None:
        if alert.severity.rank < self.min_severity.rank:
            return None
        if not force and self._suppress(alert):
            return None

        _metrics.ALERTS_DISPATCHED.inc(event=alert.event.value, severity=alert.severity.value)
        for channel in self.channels:
            name = getattr(channel, "name", type(channel).__name__)
            try:
                channel.send(alert)
            except Exception as exc:
                # ISOLATED. One broken webhook must not silence the file log.
                self.delivery_failures.append((name, alert, f"{type(exc).__name__}: {exc}"))
                _metrics.ALERT_CHANNEL_FAILURES.inc(channel=name)
                _log.error(
                    "alert channel failed",
                    extra={
                        "channel": name,
                        "alert_event": alert.event.value,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return alert

    def _suppress(self, alert: Alert) -> bool:
        key = alert.key()
        now = self.clock()
        with self._lock:
            last = self._last_sent.get(key)
            if last is not None and (now - last) < self.repeat_after_seconds:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return True
            self._last_sent[key] = now
            return False

    def suppressed_counts(self) -> dict[str, int]:
        return dict(self._suppressed)

    def retry_pending(self) -> tuple[int, int]:
        """Re-send owed CRITICAL alerts on every :class:`OutboxChannel`.

        Returns ``(delivered, failed)``.  The watchdog calls this every tick.
        """
        delivered = failed = 0
        for channel in self.channels:
            if isinstance(channel, OutboxChannel):
                d, f = channel.retry()
                delivered, failed = delivered + d, failed + f
        return delivered, failed

    def reset_suppression(self) -> None:
        with self._lock:
            self._last_sent.clear()
            self._suppressed.clear()


def build_default_dispatcher(
    *,
    log_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    webhook_transport: Transport | None = None,
    telegram_transport: Transport | None = None,
    source: str = "",
    http_client: Any = None,
    outbox_path: Path | str | None = None,
    install_ledger_hook: bool = True,
) -> AlertDispatcher:
    """Console + file always; webhook and Telegram only when configured.

    Configuration is read from the environment at call time.  Nothing here
    fails because a credential is missing -- the channel is simply absent, and
    :meth:`AlertDispatcher.channel_names` says so, which is what
    ``fiboki system doctor`` prints.

    When a remote channel IS configured and no transport was passed, an
    ``httpx`` transport is injected (``http_client`` lets a test supply one on
    ``httpx.MockTransport``), and the channel is wrapped in an
    :class:`OutboxChannel` over ``outbox_path`` (default: the resolved
    ``<FIBOKI_STATE_DIR>/alerts_outbox.sqlite``) so CRITICAL alerts are
    delivered at least once.  With ``install_ledger_hook`` a torn record in
    any durable ledger raises ``LEDGER_TORN_TAIL`` through this dispatcher.
    """
    environ = env if env is not None else os.environ
    channels: list[AlertChannel] = [ConsoleChannel()]
    path = log_path or environ.get("FIBOKI_ALERT_LOG", "")
    if path:
        channels.append(FileChannel(Path(path)))
    remote: list[AlertChannel] = []
    webhook = WebhookChannel.from_env(webhook_transport, env=environ)
    if webhook is not None:
        if webhook.transport is None:
            webhook.transport = httpx_transport(http_client)
        remote.append(webhook)
    telegram = TelegramChannel.from_env(telegram_transport, env=environ)
    if telegram is not None:
        if telegram.transport is None:
            telegram.transport = httpx_transport(http_client)
        remote.append(telegram)
    if remote:
        outbox = AlertOutbox(outbox_path or resolve_paths(environ).alert_outbox)
        channels.extend(OutboxChannel(channel, outbox) for channel in remote)
    dispatcher = AlertDispatcher(channels, source=source)
    if install_ledger_hook:
        set_torn_tail_hook("fiboki.obs.alerts", _torn_tail_alerter(dispatcher))
    return dispatcher


def _torn_tail_alerter(dispatcher: AlertDispatcher) -> Callable[[TornTail], Any]:
    def _fire(torn: TornTail) -> None:
        dispatcher.fire(
            AlertEvent.LEDGER_TORN_TAIL,
            (
                f"{torn.path.name}: the last record was torn by a crash mid-write and "
                f"has been quarantined ({torn.reason}). The ledger loaded without it. "
                "Check the quarantined bytes: a torn intent is an order whose "
                "dispatch never started, a torn kill-switch line is an activation "
                "that never took effect."
            ),
            dedupe_key=f"ledger_torn_tail:{torn.path}",
            force=True,
            ledger=str(torn.path),
            quarantine=str(torn.quarantine_path) if torn.quarantine_path else "FAILED",
            bytes=torn.bytes_quarantined,
        )

    return _fire


# ---------------------------------------------------------------------------
# The watchdog
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeartbeatView:
    """What the watchdog needs to know about one worker.

    Supplied by a callable so the watchdog does not import the worker package
    (and so a test can hand it a list).
    """

    worker_id: str
    kind: str
    age_seconds: float
    status: str = ""
    last_error: str = ""
    expected: bool = True


@dataclass(frozen=True, slots=True)
class WatchdogThresholds:
    """The watchdog's view of :class:`fiboki.obs.health.HealthThresholds`.

    Defaults come from :data:`DEFAULT_HEALTH_THRESHOLDS`; a deployment passes
    ``WatchdogThresholds.from_health(settings.health)``.
    """

    #: Older than this and the heartbeat is stale -- something is wrong.
    stale_after_seconds: float = DEFAULT_HEALTH_THRESHOLDS.worker_stale_after_seconds
    #: Older than this and we stop calling it stale and call it dead.
    down_after_seconds: float = DEFAULT_HEALTH_THRESHOLDS.worker_down_after_seconds

    @classmethod
    def from_health(cls, thresholds: HealthThresholds) -> WatchdogThresholds:
        return cls(
            stale_after_seconds=thresholds.worker_stale_after_seconds,
            down_after_seconds=thresholds.worker_down_after_seconds,
        )


class HeartbeatWatchdog:
    """Evaluates heartbeat freshness ON A TIMER, in this process.

    This is the piece V1 did not have in any form.  ``evaluate`` is pure and
    returns the alerts it fired, so the logic is testable without a thread;
    ``start`` runs ``evaluate`` on an interval in a daemon thread for a real
    deployment.  Nothing about the evaluation depends on a page being loaded.
    """

    def __init__(
        self,
        dispatcher: AlertDispatcher,
        heartbeats: Callable[[], Iterable[HeartbeatView]],
        *,
        thresholds: WatchdogThresholds | None = None,
        interval_seconds: float = 30.0,
        expected_workers: Sequence[str] = (),
        name: str = "heartbeat-watchdog",
    ) -> None:
        self.dispatcher = dispatcher
        self.heartbeats = heartbeats
        self.thresholds = thresholds or WatchdogThresholds()
        self.interval_seconds = interval_seconds
        self.expected_workers = tuple(expected_workers)
        self.name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0
        self.last_error: str = ""

    # -- pure evaluation --------------------------------------------------

    def evaluate(self) -> list[Alert]:
        """One pass.  Returns every alert that was actually dispatched."""
        self.ticks += 1
        fired: list[Alert] = []
        seen: set[str] = set()

        for view in self.heartbeats():
            seen.add(view.worker_id)
            _metrics.record_worker_liveness(
                view.worker_id,
                view.kind,
                age_seconds=view.age_seconds,
                fresh=view.age_seconds <= self.thresholds.stale_after_seconds,
            )
            if view.age_seconds >= self.thresholds.down_after_seconds:
                alert = self.dispatcher.fire(
                    AlertEvent.WORKER_DOWN,
                    (
                        f"worker {view.worker_id} ({view.kind}) has not written a heartbeat "
                        f"for {view.age_seconds:.0f}s "
                        f"(threshold {self.thresholds.down_after_seconds:.0f}s). "
                        "Treat it as DEAD: it is not processing work and the API "
                        "being healthy says nothing about it."
                    ),
                    source=view.worker_id,
                    dedupe_key=f"worker_down:{view.worker_id}",
                    worker_kind=view.kind,
                    age_seconds=round(view.age_seconds, 1),
                    last_status=view.status,
                    last_error=view.last_error,
                )
                if alert is not None:
                    fired.append(alert)
            elif view.age_seconds >= self.thresholds.stale_after_seconds:
                alert = self.dispatcher.fire(
                    AlertEvent.HEARTBEAT_STALE,
                    (
                        f"worker {view.worker_id} ({view.kind}) heartbeat is "
                        f"{view.age_seconds:.0f}s old "
                        f"(threshold {self.thresholds.stale_after_seconds:.0f}s)"
                    ),
                    source=view.worker_id,
                    dedupe_key=f"heartbeat_stale:{view.worker_id}",
                    worker_kind=view.kind,
                    age_seconds=round(view.age_seconds, 1),
                    last_status=view.status,
                    last_error=view.last_error,
                )
                if alert is not None:
                    fired.append(alert)

        # A worker that was expected and has NO heartbeat row at all is down.
        # V1 could not see this case: with no row there was nothing to compute
        # a freshness from, so the dashboard showed a blank and everyone read
        # a blank as "fine".
        for expected in self.expected_workers:
            if expected in seen:
                continue
            _metrics.record_worker_liveness(expected, "unknown", age_seconds=-1.0, fresh=False)
            alert = self.dispatcher.fire(
                AlertEvent.WORKER_DOWN,
                (
                    f"worker {expected} is expected to be running but has written no "
                    "heartbeat at all. It never started, or its record was lost."
                ),
                source=expected,
                dedupe_key=f"worker_down:{expected}",
                worker_kind="unknown",
                age_seconds=-1,
            )
            if alert is not None:
                fired.append(alert)
        return fired

    # -- timer ------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.evaluate()
            except Exception as exc:  # pragma: no cover - defensive
                self.last_error = f"{type(exc).__name__}: {exc}"
                _log.exception("watchdog evaluation failed")
            self._stop.wait(self.interval_seconds)

    def start(self) -> HeartbeatWatchdog:
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self) -> HeartbeatWatchdog:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def alert_to_row(alert: Alert) -> dict[str, Any]:
    """Flat dict for a rich table."""
    row = asdict(alert)  # type: ignore[call-overload]
    row["event"] = alert.event.value
    row["severity"] = alert.severity.value
    return row
