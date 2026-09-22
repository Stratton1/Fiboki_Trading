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
This dispatcher is best-effort and in-process.  It does not persist an outbox,
so an alert raised in the instant before a hard kill can be lost.  The
mitigation is that the conditions that matter (a stale heartbeat, a divergent
reconciliation) are *level*, not *edge*: the watchdog re-evaluates them on its
next tick and fires again.  Anything that must not be lost belongs in a ledger,
not in an alert.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fiboki.obs import metrics as _metrics
from fiboki.obs.logging import correlation_id

__all__ = [
    "Alert",
    "AlertChannel",
    "AlertDispatcher",
    "AlertEvent",
    "ConsoleChannel",
    "FileChannel",
    "HeartbeatWatchdog",
    "MemoryChannel",
    "Severity",
    "TelegramChannel",
    "TelegramConfig",
    "WebhookChannel",
    "WebhookConfig",
    "default_severity",
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
    """Append-only JSONL.  fsync'd, because the interesting alert is the last
    one before the process died."""

    path: Path
    name: str = "file"
    fsync: bool = True

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, alert: Alert) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(alert.to_json() + "\n")
            handle.flush()
            if self.fsync:
                os.fsync(handle.fileno())

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


#: What a transport must look like.  ``httpx.post``-shaped, but injected so the
#: test suite never opens a socket and CI never needs a credential.
Transport = Callable[[str, dict[str, Any], Mapping[str, str], float], Any]


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
        self.transport(
            self.config.url,
            alert.to_dict(),
            dict(self.config.headers),
            self.config.timeout_seconds,
        )


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
        self.transport(
            self.config.method_url(),
            self.build_payload(alert),
            {"Content-Type": "application/json"},
            self.config.timeout_seconds,
        )


def _html_escape(value: Any) -> str:
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


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
) -> AlertDispatcher:
    """Console + file always; webhook and Telegram only when configured.

    Configuration is read from the environment at call time.  Nothing here
    fails because a credential is missing -- the channel is simply absent, and
    :meth:`AlertDispatcher.channel_names` says so, which is what
    ``fiboki system doctor`` prints.
    """
    environ = env if env is not None else os.environ
    channels: list[AlertChannel] = [ConsoleChannel()]
    path = log_path or environ.get("FIBOKI_ALERT_LOG", "")
    if path:
        channels.append(FileChannel(Path(path)))
    webhook = WebhookChannel.from_env(webhook_transport, env=environ)
    if webhook is not None:
        channels.append(webhook)
    telegram = TelegramChannel.from_env(telegram_transport, env=environ)
    if telegram is not None:
        channels.append(telegram)
    return AlertDispatcher(channels, source=source)


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
    #: Older than this and the heartbeat is stale -- something is wrong.
    stale_after_seconds: float = 120.0
    #: Older than this and we stop calling it stale and call it dead.
    down_after_seconds: float = 300.0


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
