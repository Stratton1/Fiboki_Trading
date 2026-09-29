"""The research composition root: the one place the agent research fleet is wired.

``docs/v2/ARCHITECTURE.md`` §12 recorded that nothing composed the research
side: no process registered the deterministic job handlers, so
``fiboki worker run research`` idled and said so, and nothing ran the agent
research cycle on a schedule. :func:`compose_research_runtime` is that
composition, and :class:`ResearchRuntime` is what it returns:

======================  =====================================================
Part                    Built from
======================  =====================================================
research store          ``FIBOKI_EXPERIMENT_DB`` if set, else
                        ``<FIBOKI_STATE_DIR>/research/research.sqlite``
strategy registry       every document in ``FIBOKI_STRATEGIES_DIR``
bar source              the versioned ``DataStore`` at ``FIBOKI_DATA_ROOT``
job handlers            every entry of :data:`fiboki.agents.jobs.HANDLERS`,
                        registered on the worker's own orchestrator AND on a
                        private one the agent workflows submit to
model provider          ``FIBOKI_AGENT_PROVIDER``: ``echo`` (default) or
                        ``local`` (an Ollama server; see below)
audit ledger            :class:`~fiboki.agents.audit.JsonlAuditLedger` at
                        ``<FIBOKI_STATE_DIR>/agents/audit.jsonl``
nightly cycle           :func:`~fiboki.agents.workflows.run_research_cycle` on
                        ``FIBOKI_AGENT_CYCLE_TARGET`` at
                        ``FIBOKI_AGENT_CYCLE_UTC`` (``HH:MM``, UTC)
incident investigation  :func:`~fiboki.agents.workflows.run_failure_investigation`
                        for an incident that names a backtest
event scan              :func:`~fiboki.agents.workflows.run_event_scan` every
                        ``FIBOKI_EVENT_SCAN_MINUTES`` (default 15; 0 = off)
                        over ``<FIBOKI_STATE_DIR>/news/headlines.sqlite``,
                        filing into ``<FIBOKI_STATE_DIR>/events/annotations.sqlite``
======================  =====================================================

All of it sits behind ``FIBOKI_AGENT_CYCLES`` (default OFF). With the flag off
:func:`research_runtime_from_env` returns ``None`` and a research worker starts
exactly as it did before: no handlers, no schedule, no ledger file.

The agent clock
---------------
Every run builds a fresh :class:`~fiboki.agents.tools.ToolContext` whose
``as_of`` is pinned to the UTC instant the run started, so a cycle that takes
forty minutes reasons about one market, not a market that moved underneath it
between steps.

The schedule
------------
One entry per UTC day at ``FIBOKI_AGENT_CYCLE_UTC``. The slot is CLAIMED in
``<FIBOKI_STATE_DIR>/agents/schedule.json`` before the cycle runs, the same
claim-before-evaluate rule the holdout registry follows: a cycle that crashes
the process is not retried that night, because a retry loop against a model
that crashes the worker is worse than one missed night, and the claim is on
disk for the operator to read. A worker that was down at the slot runs the
missed slot once when it next starts; on the very first start there is no
previous claim and the current slot is recorded without running, so enabling
the flag at 09:00 does not fire a cycle at 09:00.

Incidents
---------
:class:`IncidentChannel` is an :class:`~fiboki.obs.alerts.AlertChannel`: add it
to a dispatcher and every ``STRATEGY_DEGRADED``, ``STRATEGY_HALTED`` or
``STRATEGY_QUARANTINED`` alert whose context carries a ``backtest_id`` queues
one failure investigation. ``send`` only queues; the investigation runs on the
research worker's own thread in its next cycle, so an alert dispatch never
waits on a model. An incident without a ``backtest_id`` is counted and
skipped, because the investigator's evidence is a backtest's drawdown and
trades and it must not guess which one. The dispatcher is IN-PROCESS: an alert
raised by the paper or live worker never reaches this channel. Until alerts
cross processes, the incident path calls :meth:`ResearchRuntime.raise_incident`
(or anything that can reach this process queues through it).

The ``local`` provider
----------------------
``FIBOKI_AGENT_PROVIDER=local`` builds
:meth:`~fiboki.agents.providers.LocalHTTPProvider.for_ollama` for
``FIBOKI_AGENT_LOCAL_MODEL`` (mandatory: there is no default model) at
``FIBOKI_AGENT_LOCAL_URL``, with the explicit client from
:func:`~fiboki.agents.providers.ollama_http_client`. ``echo`` is the offline
test double: with no script every model step fails to parse and is RECORDED as
failed, so an ``echo`` nightly cycle is a wiring check, not research.

The event scan
--------------
Every ``FIBOKI_EVENT_SCAN_MINUTES`` the tool-less ``event_classifier`` role is
shown the headlines recorded since the previous scan (at most seven days, at
most 200) and its annotations are filed in the quarantined store. The slot is
claimed before the scan runs, like the nightly slot; the headline WINDOW,
however, starts where the last LOGGED scan ended, so a scan that crashed
leaves its window to the next one (at-least-once: a batch filed before the
crash may be classified twice, which the append-only store keeps as two rows).
Every scan, including one that found nothing, writes a ``scan_log`` row: that
row is the heartbeat the event veto's freshness guard reads.

What this module does not do
----------------------------
It holds no execution capability and imports nothing from ``broker``,
``risk`` or ``portfolio``. It does not start the heartbeat watchdog (a
watchdog inside the process it watches cannot see that process die) and it
does not reconcile anything (reconciliation belongs to the live worker).
"""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from fiboki.agents.audit import JsonlAuditLedger
from fiboki.agents.capabilities import CapabilityResolver
from fiboki.agents.jobs import HANDLERS, register_research_handlers
from fiboki.agents.orchestrator import Orchestrator, submitter_for
from fiboki.agents.providers import EchoProvider, LLMProvider, ModelRouter
from fiboki.agents.tools import NEWS_MAX_LOOKBACK, BarSource, DataStoreBarSource, ToolContext
from fiboki.agents.workflows import (
    EVENT_CLASSIFICATION_STEP,
    WorkflowDeps,
    WorkflowResult,
    run_event_scan,
    run_failure_investigation,
    run_research_cycle,
)
from fiboki.core.enums import Timeframe
from fiboki.data.news.store import HeadlineStore, default_store_path
from fiboki.marketstate.events import AnnotationStore, default_annotation_store_path
from fiboki.obs.alerts import Alert, AlertEvent
from fiboki.obs.logging import get_logger
from fiboki.research.artefacts import ResearchStore
from fiboki.research.experiment import ExperimentLedger
from fiboki.strategy.registry import StrategyRegistry

__all__ = [
    "INCIDENT_EVENTS",
    "CycleTarget",
    "DailyCycleSchedule",
    "Incident",
    "IncidentChannel",
    "IntervalSchedule",
    "ResearchRuntime",
    "ResearchRuntimeSettings",
    "RuntimeConfigError",
    "compose_research_runtime",
    "research_runtime_from_env",
]

_log = get_logger("fiboki.workers.research_runtime")

#: The alert events that queue a failure investigation.
INCIDENT_EVENTS: frozenset[AlertEvent] = frozenset(
    {
        AlertEvent.STRATEGY_DEGRADED,
        AlertEvent.STRATEGY_HALTED,
        AlertEvent.STRATEGY_QUARANTINED,
    }
)

#: The queue the job-submission tools default to, on the PRIVATE cycle
#: orchestrator; the workflows drain it themselves.
CYCLE_QUEUE = "research"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class RuntimeConfigError(ValueError):
    """The environment cannot be turned into a research runtime. Refuse to start."""


def _parse_bool(name: str, raw: str, default: bool) -> bool:
    """Strict, as ``fiboki.api.settings.parse_bool`` (which ``workers`` may not import)."""
    text = raw.strip().lower()
    if not text:
        return default
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise RuntimeConfigError(
        f"{name}={raw!r} is not a boolean; use one of {sorted(_TRUE | _FALSE)}. "
        "Refusing to guess."
    )


def _parse_minutes(name: str, raw: str, default: int) -> int:
    text = raw.strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as exc:
        raise RuntimeConfigError(f"{name}={raw!r} is not a whole number of minutes") from exc
    if value < 0 or value > 24 * 60:
        raise RuntimeConfigError(f"{name}={raw!r} must be 0 (off) to 1440 minutes")
    return value


def _parse_hhmm(name: str, raw: str) -> time:
    try:
        hours, minutes = (int(part) for part in raw.strip().split(":"))
        return time(hours, minutes, tzinfo=UTC)
    except (ValueError, TypeError) as exc:
        raise RuntimeConfigError(
            f"{name}={raw!r} is not a UTC time of day; use HH:MM, e.g. 02:15"
        ) from exc


@dataclass(frozen=True, slots=True)
class CycleTarget:
    """What the nightly cycle researches: a registered seed on one series."""

    seed_strategy_id: str
    instrument: str
    timeframe: str
    question: str = ""

    @classmethod
    def parse(cls, raw: str) -> CycleTarget:
        """``strategy_id:INSTRUMENT:TIMEFRAME``, e.g. ``donchian_breakout_atr:XAUUSD:H4``."""
        parts = [p.strip() for p in raw.split(":")]
        if len(parts) != 3 or not all(parts):
            raise RuntimeConfigError(
                f"FIBOKI_AGENT_CYCLE_TARGET={raw!r}: expected "
                "strategy_id:INSTRUMENT:TIMEFRAME, e.g. donchian_breakout_atr:XAUUSD:H4"
            )
        try:
            timeframe = Timeframe(parts[2].upper()).value
        except ValueError as exc:
            raise RuntimeConfigError(
                f"FIBOKI_AGENT_CYCLE_TARGET={raw!r}: {parts[2]!r} is not a timeframe"
            ) from exc
        return cls(parts[0], parts[1].upper(), timeframe)


@dataclass(frozen=True, slots=True)
class ResearchRuntimeSettings:
    """Everything :func:`compose_research_runtime` reads. Built once, at start.

    A separate type from ``fiboki.api.settings.Settings`` because ``workers``
    sits BELOW ``api`` in the layering (``tests/unit/test_layering.py``). Every
    variable read here is declared in ``ENV_REGISTRY``.
    """

    agent_cycles: bool = False
    state_dir: Path = field(default_factory=lambda: Path("var"))
    experiment_db: Path | None = None
    data_root: Path | None = None
    strategies_dir: Path = field(default_factory=lambda: Path("research/strategies"))
    provider: str = "echo"
    local_url: str = "http://127.0.0.1:11434"
    local_model: str = ""
    cycle_at: time = field(default_factory=lambda: time(2, 15, tzinfo=UTC))
    cycle_target: CycleTarget | None = None
    #: Minutes between event scans; 0 turns the scan off.
    event_scan_minutes: int = 15

    def __post_init__(self) -> None:
        if self.provider not in ("echo", "local"):
            raise RuntimeConfigError(
                f"FIBOKI_AGENT_PROVIDER={self.provider!r}: expected 'echo' or 'local'"
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ResearchRuntimeSettings:
        env = dict(os.environ if environ is None else environ)

        def get(name: str, default: str = "") -> str:
            return str(env.get(name, default)).strip()

        target = get("FIBOKI_AGENT_CYCLE_TARGET")
        experiment_db = get("FIBOKI_EXPERIMENT_DB")
        data_root = get("FIBOKI_DATA_ROOT")
        return cls(
            agent_cycles=_parse_bool(
                "FIBOKI_AGENT_CYCLES", get("FIBOKI_AGENT_CYCLES"), False
            ),
            state_dir=Path(get("FIBOKI_STATE_DIR", "var") or "var"),
            experiment_db=Path(experiment_db) if experiment_db else None,
            data_root=Path(data_root) if data_root else None,
            strategies_dir=Path(
                get("FIBOKI_STRATEGIES_DIR", "research/strategies") or "research/strategies"
            ),
            provider=(get("FIBOKI_AGENT_PROVIDER", "echo") or "echo").lower(),
            local_url=get("FIBOKI_AGENT_LOCAL_URL", "http://127.0.0.1:11434")
            or "http://127.0.0.1:11434",
            local_model=get("FIBOKI_AGENT_LOCAL_MODEL"),
            cycle_at=_parse_hhmm(
                "FIBOKI_AGENT_CYCLE_UTC", get("FIBOKI_AGENT_CYCLE_UTC", "02:15") or "02:15"
            ),
            cycle_target=CycleTarget.parse(target) if target else None,
            event_scan_minutes=_parse_minutes(
                "FIBOKI_EVENT_SCAN_MINUTES", get("FIBOKI_EVENT_SCAN_MINUTES"), 15
            ),
        )

    @property
    def agents_dir(self) -> Path:
        return self.state_dir / "agents"

    @property
    def audit_path(self) -> Path:
        return self.agents_dir / "audit.jsonl"

    @property
    def schedule_path(self) -> Path:
        return self.agents_dir / "schedule.json"

    @property
    def news_path(self) -> Path:
        return default_store_path(self.state_dir)

    @property
    def events_path(self) -> Path:
        return default_annotation_store_path(self.state_dir)


# ---------------------------------------------------------------------------
# The nightly schedule
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DailyCycleSchedule:
    """One run per UTC day at ``at``, claimed on disk before it runs.

    Not an :class:`~fiboki.agents.orchestrator.ScheduledJob`: those submit a
    ``JobType``, and a research cycle is a workflow of agent sessions, not a
    deterministic job. Adding a job type for it would put an agent workflow
    behind the queue whose whole point is that nothing on it consults a model.
    """

    name: str
    at: time
    state_path: Path

    def slot_for(self, now: datetime) -> datetime:
        """The most recent slot at or before ``now``."""
        now = now.astimezone(UTC)
        today = datetime.combine(now.date(), self.at.replace(tzinfo=None), tzinfo=UTC)
        return today if now >= today else today - timedelta(days=1)

    def _read(self) -> dict[str, str]:
        if not self.state_path.exists():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeConfigError(
                f"{self.state_path} is not valid JSON; refusing to guess which "
                "nightly slots already ran. Inspect it and remove it deliberately."
            ) from exc
        return {str(k): str(v) for k, v in dict(raw).items()}

    def _write(self, state: Mapping[str, str]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(state), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def last_claimed(self) -> datetime | None:
        value = self._read().get(self.name)
        return datetime.fromisoformat(value) if value else None

    def initialise(self, now: datetime) -> None:
        """First start: record the current slot as seen, so it does not fire now."""
        if self.last_claimed() is None:
            state = self._read()
            state[self.name] = self.slot_for(now).isoformat()
            self._write(state)

    def due(self, now: datetime) -> datetime | None:
        """The slot to run, or ``None``. A missed slot is run once, not N times."""
        slot = self.slot_for(now)
        last = self.last_claimed()
        if last is not None and last >= slot:
            return None
        return slot

    def claim(self, slot: datetime) -> None:
        state = self._read()
        state[self.name] = slot.isoformat()
        self._write(state)


@dataclass(slots=True)
class IntervalSchedule:
    """One run per ``every`` (slots aligned to the UTC epoch), claimed on disk.

    Same claim-before-run rule and same state file as
    :class:`DailyCycleSchedule`; a missed run of many slots is run ONCE.
    """

    name: str
    every: timedelta
    state_path: Path

    def __post_init__(self) -> None:
        if self.every <= timedelta(0):
            raise RuntimeConfigError(f"{self.name}: interval must be positive")

    def slot_for(self, now: datetime) -> datetime:
        now = now.astimezone(UTC)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        n = (now - epoch) // self.every
        return epoch + n * self.every

    def _daily(self) -> DailyCycleSchedule:
        # Reuse the daily schedule's state-file handling verbatim.
        return DailyCycleSchedule(self.name, time(0, 0, tzinfo=UTC), self.state_path)

    def last_claimed(self) -> datetime | None:
        return self._daily().last_claimed()

    def initialise(self, now: datetime) -> None:
        if self.last_claimed() is None:
            self.claim(self.slot_for(now))

    def due(self, now: datetime) -> datetime | None:
        slot = self.slot_for(now)
        last = self.last_claimed()
        if last is not None and last >= slot:
            return None
        return slot

    def claim(self, slot: datetime) -> None:
        self._daily().claim(slot)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Incident:
    """One request to investigate a backtest's failure."""

    backtest_id: str
    source: str
    reason: str
    raised_at: datetime
    event: str = ""

    @property
    def key(self) -> str:
        """At most one investigation per backtest per UTC day."""
        return f"{self.backtest_id}:{self.raised_at.astimezone(UTC).date().isoformat()}"


class _IncidentInbox:
    """Thread-safe: an alert may be dispatched from the watchdog's thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: deque[Incident] = deque()
        self._seen: set[str] = set()
        self.skipped: list[tuple[str, str]] = []

    def put(self, incident: Incident) -> bool:
        with self._lock:
            if incident.key in self._seen:
                return False
            self._seen.add(incident.key)
            self._queue.append(incident)
            return True

    def skip(self, event: str, why: str) -> None:
        with self._lock:
            self.skipped.append((event, why))

    def pop(self) -> Incident | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)


@dataclass
class IncidentChannel:
    """An alert channel that QUEUES failure investigations. It never runs one."""

    inbox: _IncidentInbox
    clock: Callable[[], datetime]
    name: str = "agent-incidents"

    def send(self, alert: Alert) -> None:
        if alert.event not in INCIDENT_EVENTS:
            return
        backtest_id = str(dict(alert.context).get("backtest_id") or "").strip()
        if not backtest_id:
            self.inbox.skip(
                alert.event.value,
                "no backtest_id in the alert context; the investigator reads a "
                "backtest's drawdown and trades and will not guess which one",
            )
            return
        self.inbox.put(
            Incident(
                backtest_id=backtest_id,
                source=alert.source,
                reason=alert.message,
                raised_at=self.clock(),
                event=alert.event.value,
            )
        )


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------


@dataclass
class ResearchRuntime:
    """Every part of the composed research side, kept so it can be inspected.

    Two orchestrators, deliberately. ``orchestrator`` is the worker's: its
    queues are drained by the worker loop and carry operator-submitted jobs.
    ``cycle_orchestrator`` is private to the agent workflows, which submit
    their jobs and drain them inline. Were they one, a research cycle's
    ``drain`` would also execute whatever an operator had queued and could
    adopt that job's backtest as its own result.
    """

    settings: ResearchRuntimeSettings
    orchestrator: Orchestrator
    cycle_orchestrator: Orchestrator
    research: ResearchStore
    strategies: StrategyRegistry
    bars: BarSource | None
    provider: LLMProvider
    router: ModelRouter
    ledger: JsonlAuditLedger
    resolver: CapabilityResolver
    schedule: tuple[DailyCycleSchedule, ...]
    clock: Callable[[], datetime]
    incidents: IncidentChannel
    results: list[WorkflowResult] = field(default_factory=list)
    #: The event scan (empty when FIBOKI_EVENT_SCAN_MINUTES=0 or cycles are off).
    event_schedule: tuple[IntervalSchedule, ...] = ()
    #: Headline store (read) and quarantined annotation store (written by the
    #: classifier's one tool). ``None`` when the event scan is not composed.
    news: HeadlineStore | None = None
    events: AnnotationStore | None = None

    # -- per-run wiring ---------------------------------------------------

    def tool_context(self, as_of: datetime) -> ToolContext:
        """A context pinned to ``as_of``: every dated read treats it as now."""
        return ToolContext(
            research=self.research,
            strategies=self.strategies,
            bars=self.bars,
            submit_job=submitter_for(self.cycle_orchestrator),
            default_queue=CYCLE_QUEUE,
            as_of=as_of,
            news=self.news,
            events=self.events,
            clock=self.clock,
        )

    def deps(self, as_of: datetime) -> WorkflowDeps:
        return WorkflowDeps(
            context=self.tool_context(as_of),
            resolver=self.resolver,
            ledger=self.ledger,
            router=self.router,
            orchestrator=self.cycle_orchestrator,
            queue=CYCLE_QUEUE,
        )

    def _now(self, now: datetime | None) -> datetime:
        when = now or self.clock()
        if when.tzinfo is None:
            raise ValueError("the agent clock must be timezone-aware UTC")
        return when.astimezone(UTC)

    # -- the two workflows ------------------------------------------------

    def run_research_cycle(
        self,
        *,
        target: CycleTarget | None = None,
        now: datetime | None = None,
        workflow_id: str | None = None,
        backtest_overrides: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        chosen = target or self.settings.cycle_target
        if chosen is None:
            raise RuntimeConfigError(
                "no research-cycle target: set FIBOKI_AGENT_CYCLE_TARGET or pass one"
            )
        as_of = self._now(now)
        result = run_research_cycle(
            self.deps(as_of),
            seed_strategy_id=chosen.seed_strategy_id,
            instrument=chosen.instrument,
            timeframe=chosen.timeframe,
            question=chosen.question,
            workflow_id=workflow_id,
            backtest_overrides=backtest_overrides,
        )
        self.results.append(result)
        _log.info("agent research cycle finished", extra={"summary": result.summary()})
        return result

    def run_failure_investigation(
        self, backtest_id: str, *, now: datetime | None = None, workflow_id: str | None = None
    ) -> WorkflowResult:
        result = run_failure_investigation(
            self.deps(self._now(now)), backtest_id=backtest_id, workflow_id=workflow_id
        )
        self.results.append(result)
        _log.info("failure investigation finished", extra={"summary": result.summary()})
        return result

    def run_event_scan(
        self, *, now: datetime | None = None, workflow_id: str | None = None
    ) -> WorkflowResult:
        """One event scan over the headlines since the last logged scan.

        Always writes one ``scan_log`` row, whatever happened, unless the run
        raised (then the next scan re-covers the window).
        """
        if self.news is None or self.events is None:
            raise RuntimeConfigError(
                "the event scan is not composed (FIBOKI_AGENT_CYCLES off or "
                "FIBOKI_EVENT_SCAN_MINUTES=0)"
            )
        as_of = self._now(now)
        floor = as_of - NEWS_MAX_LOOKBACK
        last = self.events.last_scan_as_of()
        every = timedelta(minutes=self.settings.event_scan_minutes or 15)
        since = (
            last.to_pydatetime() + timedelta(microseconds=1) if last is not None
            else as_of - every
        )
        detail = ""
        if since < floor:
            detail = f"window_clamped:headlines observed before {floor.isoformat()} not scanned"
            since = floor
        if since > as_of:
            since = as_of
        result = run_event_scan(
            self.deps(as_of), headlines_since=since, workflow_id=workflow_id
        )
        self.results.append(result)
        fetch = next((s for s in result.steps if s.step == "fetch_headlines"), None)
        batches = [s for s in result.steps if s.step.startswith(EVENT_CLASSIFICATION_STEP)]
        if fetch is None or not fetch.ok:
            outcome = "error"
        elif all(b.ok for b in batches):
            outcome = "ok"
        elif any(b.ok for b in batches):
            outcome = "partial"
        else:
            outcome = "error"
        failed = [b.step for b in batches if not b.ok]
        if failed:
            detail = "; ".join(x for x in (detail, f"failed batches: {failed}") if x)
        self.events.log_scan(
            as_of=as_of,
            since=since,
            finished_at=self._now(None) if now is None else as_of,
            outcome=outcome,
            n_headlines=int((fetch.output if fetch else {}).get("n_headlines", 0)),
            n_batches=len(batches),
            n_annotations=sum(int(b.output.get("n_written", 0)) for b in batches if b.ok),
            truncated=bool((fetch.output if fetch else {}).get("truncated", False)),
            workflow_id=result.workflow_id,
            detail=detail,
        )
        _log.info("event scan finished", extra={"summary": result.summary(), "outcome": outcome})
        return result

    # -- incidents --------------------------------------------------------

    def raise_incident(
        self, backtest_id: str, *, source: str = "", reason: str = "", event: str = "manual"
    ) -> bool:
        """The incident path's entry point. Queues; the worker runs it next cycle.

        Returns False when an investigation of this backtest is already queued
        or has run today.
        """
        if not backtest_id.strip():
            raise ValueError("an incident must name the backtest to investigate")
        return self.incidents.inbox.put(
            Incident(
                backtest_id=backtest_id.strip(),
                source=source,
                reason=reason,
                raised_at=self._now(None),
                event=event,
            )
        )

    # -- what the worker calls --------------------------------------------

    def has_work(self, now: datetime | None = None) -> bool:
        when = self._now(now)
        return (
            bool(len(self.incidents.inbox))
            or any(s.due(when) for s in self.schedule)
            or any(s.due(when) for s in self.event_schedule)
        )

    def tick(
        self,
        now: datetime | None = None,
        *,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> list[WorkflowResult]:
        """Run the due nightly cycle, if any, then every queued investigation.

        Each run pins its own ``as_of`` to the instant it starts.
        """
        ran: list[WorkflowResult] = []
        when = self._now(now)
        for entry in self.schedule:
            slot = entry.due(when)
            if slot is None or should_stop():
                continue
            entry.claim(slot)  # BEFORE running: see the module docstring
            ran.append(
                self.run_research_cycle(
                    now=self._now(None) if now is None else when,
                    workflow_id=f"wf_{entry.name}_{slot:%Y%m%dT%H%MZ}",
                )
            )
        for scan in self.event_schedule:
            slot = scan.due(when)
            if slot is None or should_stop():
                continue
            scan.claim(slot)  # BEFORE running, as for the nightly slot
            ran.append(
                self.run_event_scan(
                    now=self._now(None) if now is None else when,
                    workflow_id=f"wf_{scan.name}_{slot:%Y%m%dT%H%MZ}",
                )
            )
        while not should_stop():
            incident = self.incidents.inbox.pop()
            if incident is None:
                break
            ran.append(
                self.run_failure_investigation(
                    incident.backtest_id,
                    now=self._now(None) if now is None else when,
                )
            )
        return ran


def _build_provider(settings: ResearchRuntimeSettings) -> LLMProvider:
    if settings.provider == "echo":
        return EchoProvider()
    if not settings.local_model:
        raise RuntimeConfigError(
            "FIBOKI_AGENT_PROVIDER=local needs FIBOKI_AGENT_LOCAL_MODEL (the exact "
            "Ollama model name). There is no default model: a research cycle "
            "attributed to a model nobody chose is not reproducible."
        )
    from fiboki.agents.providers import LocalHTTPProvider, ollama_http_client

    # llama.cpp (GET /props answers) or Ollama, decided by asking the server
    # once at composition. A server that is down at start is a start failure
    # by design: a research cycle attributed to a model nobody could reach is
    # not a cycle, and launchd retries the worker.
    return LocalHTTPProvider.for_local_server(
        settings.local_model, client=ollama_http_client(), base_url=settings.local_url
    )


def _build_research_store(settings: ResearchRuntimeSettings) -> ResearchStore:
    if settings.experiment_db is not None:
        settings.experiment_db.parent.mkdir(parents=True, exist_ok=True)
        return ResearchStore(ledger=ExperimentLedger(settings.experiment_db))
    return ResearchStore(settings.state_dir / "research")


def _build_bars(settings: ResearchRuntimeSettings) -> BarSource | None:
    if settings.data_root is None:
        return None
    from fiboki.data.store import DataStore

    return DataStoreBarSource(DataStore(settings.data_root))


def compose_research_runtime(
    settings: ResearchRuntimeSettings,
    *,
    orchestrator: Orchestrator | None = None,
    provider: LLMProvider | None = None,
    bars: BarSource | None = None,
    strategies: StrategyRegistry | None = None,
    research: ResearchStore | None = None,
    clock: Callable[[], datetime] | None = None,
    news: HeadlineStore | None = None,
    events: AnnotationStore | None = None,
) -> ResearchRuntime:
    """Compose the research side from ``settings``. Explicit arguments win.

    The keyword overrides exist for tests and for an entrypoint that already
    holds one of the parts (the worker passes its own orchestrator, so the
    handlers land where its loop drains). With ``settings.agent_cycles`` off
    the runtime is still built but has NO schedule entry.

    Refuses to compose (``RuntimeConfigError``) a nightly cycle that could only
    fail: no target, a target seed that is not registered, or no bar source.
    """
    clock = clock or (lambda: datetime.now(tz=UTC))
    orchestrator = orchestrator or Orchestrator()
    research = research or _build_research_store(settings)
    if strategies is None:
        strategies = StrategyRegistry()
        if settings.strategies_dir.is_dir():
            strategies.load_directory(settings.strategies_dir)
    bars = bars if bars is not None else _build_bars(settings)
    # Every deterministic handler there is, on both orchestrators (see
    # ResearchRuntime). REGIME_SCAN and LIBRARIAN_FILING are JobTypes with no
    # handler in fiboki.agents.jobs, so either orchestrator refuses to queue
    # them rather than accept work nobody can run.
    cycle_orchestrator = Orchestrator()
    for target_orchestrator in (orchestrator, cycle_orchestrator):
        register_research_handlers(
            target_orchestrator,
            research=research,
            strategies=strategies,
            bars=bars,
            job_types=tuple(HANDLERS),
        )
    provider = provider or _build_provider(settings)
    ledger = JsonlAuditLedger(settings.audit_path)

    schedule: tuple[DailyCycleSchedule, ...] = ()
    if settings.agent_cycles:
        target = settings.cycle_target
        if target is None:
            raise RuntimeConfigError(
                "FIBOKI_AGENT_CYCLES is on but FIBOKI_AGENT_CYCLE_TARGET is unset: a "
                "nightly cycle needs a registered seed, an instrument and a timeframe"
            )
        if target.seed_strategy_id not in strategies:
            raise RuntimeConfigError(
                f"FIBOKI_AGENT_CYCLE_TARGET names {target.seed_strategy_id!r}, which "
                f"is not in the strategy registry loaded from {settings.strategies_dir}"
            )
        if bars is None:
            raise RuntimeConfigError(
                "FIBOKI_AGENT_CYCLES is on but no bar source is wired (set "
                "FIBOKI_DATA_ROOT): every queued backtest would dead-letter"
            )
        entry = DailyCycleSchedule(
            name="nightly_research_cycle",
            at=settings.cycle_at,
            state_path=settings.schedule_path,
        )
        entry.initialise(clock())
        schedule = (entry,)

    event_schedule: tuple[IntervalSchedule, ...] = ()
    if settings.agent_cycles and settings.event_scan_minutes > 0:
        news = news if news is not None else HeadlineStore(settings.news_path)
        events = events if events is not None else AnnotationStore(settings.events_path)
        scan = IntervalSchedule(
            name="event_scan",
            every=timedelta(minutes=settings.event_scan_minutes),
            state_path=settings.schedule_path,
        )
        scan.initialise(clock())
        event_schedule = (scan,)

    runtime = ResearchRuntime(
        settings=settings,
        orchestrator=orchestrator,
        cycle_orchestrator=cycle_orchestrator,
        research=research,
        strategies=strategies,
        bars=bars,
        provider=provider,
        router=ModelRouter([provider]),
        ledger=ledger,
        resolver=CapabilityResolver(),
        schedule=schedule,
        clock=clock,
        incidents=IncidentChannel(inbox=_IncidentInbox(), clock=clock),
        event_schedule=event_schedule,
        news=news,
        events=events,
    )
    _log.info(
        "research runtime composed",
        extra={
            "handlers": [t.value for t in orchestrator.handled_types()],
            "provider": settings.provider,
            "audit_path": str(settings.audit_path),
            "schedule": [f"{s.name}@{s.at.isoformat()}" for s in schedule],
            "event_scan": [f"{s.name}/{s.every}" for s in event_schedule],
        },
    )
    return runtime


def research_runtime_from_env(
    orchestrator: Orchestrator | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    dispatcher: Any = None,
    **overrides: Any,
) -> ResearchRuntime | None:
    """The research worker's hook. ``None`` unless ``FIBOKI_AGENT_CYCLES`` is on.

    When a ``dispatcher`` is given, the runtime's :class:`IncidentChannel` is
    added to it so this process's incident alerts queue investigations.
    """
    settings = ResearchRuntimeSettings.from_env(environ)
    if not settings.agent_cycles:
        return None
    runtime = compose_research_runtime(settings, orchestrator=orchestrator, **overrides)
    if dispatcher is not None:
        dispatcher.add_channel(runtime.incidents)
    return runtime
