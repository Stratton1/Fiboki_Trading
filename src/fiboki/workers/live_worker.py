"""The market-facing worker: bars in, intents out, orders ONLY via the service.

What this worker is allowed to do
---------------------------------
Ingest bars, update market state, ask strategies for plans, assemble a
:class:`~fiboki.risk.gateway.RiskContext`, and hand each plan to
:meth:`fiboki.broker.execution_service.ExecutionService.submit`.

What it is structurally prevented from doing
--------------------------------------------
Construct an order.  ``Order`` is constructed in exactly one function in the
entire source tree -- ``ExecutionService.submit`` -- and
``tests/unit/test_no_gateway_bypass.py`` walks the AST of ``src/`` and fails
the build if a second site appears.  This module therefore holds no order
construction, no sizing, and no risk decision; it holds the *loop*, and the
loop's only route to the venue is a method call that has already been proven
to pass through the risk gateway.

That is not stylistic.  V1's risk engine had zero call sites: the checks
existed, were unit-tested, passed, and were never invoked by the code that
placed orders.  The only defence that survives a refactor by a tired person at
23:00 is one where the unsafe thing is unreachable, not merely discouraged.

Closed candles only
-------------------
:meth:`LiveWorker.run_cycle` evaluates strategies on CLOSED bars.  The feed
reports whether the newest bar is closed; an open bar is carried forward and
not evaluated.  A signal computed on a forming candle is a signal computed on
information that will change, and a backtest cannot reproduce it, which breaks
research/paper parity at the only point where it is expensive.

The lifecycle timer
-------------------
``lifecycle/service.py`` says, in its own docstring, "It does not start a
thread. The worker owns the timer; this owns the evaluation."  Nothing owned the
timer, so the monitors never ran: no divergence was compared, no pre-registered
stopping rule was evaluated against forward data, and no strategy was ever
demoted automatically.  :attr:`LiveWorker.lifecycle` is that timer.  It runs on
its own cycle count, AFTER reconciliation and BEFORE any plan is submitted, so a
strategy demoted by this tick is already demoted when the gateway reads its
``StrategyView`` in the same cycle.

Kill switch
-----------
Checked before every evaluation pass.  When it blocks new risk, the worker
still ingests bars, still updates state, still reconciles -- it simply does
not submit opening intents.  A kill switch that also stops data is one that
blinds you at the moment you most need to see.

Reconciliation: at startup, fail closed; afterwards, on a timer
---------------------------------------------------------------
:meth:`LiveWorker.resume` runs once, after the lease is held and before the
first cycle, and reconciles against the venue. It used to log a CRITICAL line
on a divergent report and then trade anyway. It now refuses to start:

* the report shows unresolved divergences (an UNKNOWN intent, an orphan
  position at the venue, a size mismatch, or a position-manager divergence) ->
  :class:`StartupReconciliationError` with ``blocking=True``, and
  :meth:`LiveWorker.run` exits with :data:`EXIT_RECONCILE_BLOCKED` so a
  supervisor does not restart straight back into the same divergence;
* reconciliation could not run at all (the venue was unreachable, the service
  has no ``reconcile``) -> the same exception with ``blocking=False`` and the
  ordinary fatal exit code, because that condition may clear by itself.

After startup it reconciles every ``reconcile_interval_seconds`` (default 15
minutes) of wall time, AND every ``reconcile_every_cycles`` evaluation cycles,
whichever comes first, and alerts on any divergence. The timer runs even while
the feed is waiting for a bar, so a weekend with no bars is not a weekend with
no reconciliation.

Cycle-time budget
-----------------
An evaluation cycle that takes longer than ``cycle_budget_fraction`` (25%) of
the bar's timeframe is measured into ``fiboki_live_cycle_seconds``, counted in
``fiboki_live_cycle_budget_exceeded_total`` and alerted at WARNING. The time a
polling feed spends WAITING for the bar boundary is excluded; the time it spends
fetching and re-polling a late candle is not. Pattern after freqtrade
``util/measure_time.py`` (GPL-3.0, not copied).
"""
from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from fiboki.core.enums import Timeframe
from fiboki.obs import metrics as _metrics
from fiboki.obs.alerts import AlertEvent, Severity
from fiboki.obs.health import DEFAULT_HEALTH_THRESHOLDS
from fiboki.obs.logging import bind, get_logger, new_correlation_id
from fiboki.workers.base import (
    EXIT_FATAL,
    EXIT_LEASE_HELD,
    CycleResult,
    Worker,
    WorkerConfig,
    WorkerStore,
    log_once,
)

__all__ = [
    "EXIT_RECONCILE_BLOCKED",
    "LIVE_CYCLE_BUDGET_EXCEEDED",
    "LIVE_CYCLE_SECONDS",
    "BarBatch",
    "BarFeed",
    "ContextBuilder",
    "LifecycleTicker",
    "LiveWorker",
    "LiveWorkerConfig",
    "MarketStateUpdater",
    "StartupReconciliationError",
    "StrategyEvaluator",
    "SubmissionRecord",
    "timeframe_seconds",
]

_log = get_logger("fiboki.workers.live")

#: Exit code when startup reconciliation found divergences a restart cannot fix.
#:
#: This REUSES the existing do-not-restart-loop code (75, ``EX_TEMPFAIL``),
#: which both deploy units already treat as "do not restart": systemd lists it
#: in ``SuccessExitStatus`` and launchd throttles. A divergence needs an operator
#: to run ``fiboki broker reconcile`` and decide; a supervisor restarting the
#: worker every few seconds would only re-discover it. ``deploy/README.md``
#: documents 75 as "lease held" and does not yet mention this second meaning;
#: the heartbeat row, the stderr line and the CRITICAL alert all say which one
#: it is.
EXIT_RECONCILE_BLOCKED = EXIT_LEASE_HELD

#: Wall time of the last live evaluation cycle, excluding the feed's wait for
#: the bar boundary.
LIVE_CYCLE_SECONDS = _metrics.REGISTRY.gauge(
    "fiboki_live_cycle_seconds",
    "Wall time of the last live evaluation cycle, excluding the wait for the bar",
    ("worker", "timeframe"),
)

#: Cycles that took longer than the configured fraction of the timeframe.
LIVE_CYCLE_BUDGET_EXCEEDED = _metrics.REGISTRY.counter(
    "fiboki_live_cycle_budget_exceeded_total",
    "Live evaluation cycles that exceeded their share of the bar's timeframe",
    ("worker", "timeframe"),
)


def timeframe_seconds(timeframe: str) -> float | None:
    """``"H1" -> 3600.0``. ``None`` for an empty or unknown label."""
    try:
        return float(Timeframe(str(timeframe)).minutes * 60)
    except ValueError:
        return None


class StartupReconciliationError(RuntimeError):
    """Startup reconciliation did not come back clean. The worker must not trade.

    ``blocking`` distinguishes the two cases a supervisor must treat
    differently: True means the venue and our record DISAGREE, which only an
    operator can resolve; False means reconciliation could not run, which may
    clear on its own.
    """

    def __init__(self, message: str, *, blocking: bool) -> None:
        super().__init__(message)
        self.blocking = blocking


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BarBatch:
    """Bars pulled in one poll, plus whether the newest one is CLOSED.

    ``closed_instruments`` is the set the worker is allowed to evaluate.  An
    instrument whose newest bar is still forming is excluded -- not delayed,
    excluded, because evaluating it would use data that has not finished
    happening.
    """

    frames: Mapping[str, Any] = field(default_factory=dict)
    closed_instruments: frozenset[str] = frozenset()
    #: Age in seconds of the newest bar, per instrument. Feeds the freshness
    #: metric and the DATA_STALE alert.
    ages: Mapping[str, float] = field(default_factory=dict)
    timeframe: str = ""
    #: Instruments whose session is CLOSED (weekend, daily break, holiday) per
    #: ``data/calendars.py``. Their age still reaches the metric, but an old
    #: bar on a closed market is "closed", not "stale", and raises no alert.
    market_closed: frozenset[str] = frozenset()

    @property
    def count(self) -> int:
        return len(self.frames)


@runtime_checkable
class BarFeed(Protocol):
    """Pulls new bars.  The worker never talks to a provider directly.

    A feed MAY also implement ``wait_until_due(should_stop) -> bool``. When it
    does, the worker calls it before each cycle: True means "a bar boundary has
    passed, poll now", False means "not yet", and the cycle returns idle so the
    loop can renew the lease and write a heartbeat. The feed bounds how long one
    call blocks; the worker bounds nothing, so a feed that waits an hour inside
    this call will lose its lease -- see ``workers/feeds.py``.
    """

    def poll(self) -> BarBatch: ...


@runtime_checkable
class PositionReconciler(Protocol):
    """``VenuePositionManager.reconcile``'s shape: venue levels versus intent."""

    def reconcile(self, *, repair: bool = ...) -> Sequence[Any]: ...


@runtime_checkable
class MarketStateUpdater(Protocol):
    """Folds new bars into the market state.  Returns anything; unused."""

    def update(self, batch: BarBatch) -> Any: ...


@runtime_checkable
class StrategyEvaluator(Protocol):
    """Produces trade plans for the given CLOSED instruments.

    Returns ``TradePlan`` objects.  Sizing has already happened inside the
    strategy/sizing layer -- the execution service explicitly does not re-size,
    and neither does this worker.
    """

    def evaluate(self, instruments: Sequence[str], batch: BarBatch) -> Iterable[Any]: ...


#: ``build(plan) -> RiskContext``.  Assembling the context needs the portfolio
#: snapshot, limits, FX and market view, none of which belong in a loop.
ContextBuilder = Callable[[Any], Any]


@runtime_checkable
class KillSwitchView(Protocol):
    def blocks_new_risk(self) -> bool: ...


@runtime_checkable
class LifecycleTicker(Protocol):
    """One monitoring pass over every RUNNING strategy.

    Returns whatever the lifecycle service returns; the worker reads only
    ``demoted``, ``state_after`` and ``summary()`` off each item, and never
    decides a demotion itself.  Promotion is a named human's act and no worker
    has a route to it.
    """

    def tick(self) -> Sequence[Any]: ...


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    plan_id: str
    instrument: str
    accepted: bool
    reason: str = ""
    blocked_by_risk: bool = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class LiveWorkerConfig(WorkerConfig):
    kind: str = "live"
    #: Poll interval when nothing new arrived.
    idle_sleep_seconds: float = 5.0
    #: Reconcile against the venue every N cycles. Never zero: reconciliation
    #: that only runs on demand is reconciliation nobody runs.
    reconcile_every_cycles: int = 12
    #: ... and at least this often in wall time, whether or not bars arrive.
    #: On an H4 strategy twelve cycles is two days; this is what bounds it.
    reconcile_interval_seconds: float = 900.0
    #: Warn when one evaluation cycle takes longer than this fraction of the
    #: bar's timeframe. 0 disables the check.
    cycle_budget_fraction: float = 0.25
    #: A bar OVERDUE by more than this triggers DATA_STALE: the newest closed
    #: bar's age is measured from its close, so on an H4 feed it is up to 4 h
    #: old in perfectly healthy operation. Stale means the next bar should
    #: have arrived and has not: ``age > timeframe + data_stale_after_seconds``.
    #: THE observability threshold (``HealthThresholds.data_stale_after_seconds``):
    #: the default is the shared default and a composition root passes
    #: ``Settings.health``'s value (``FIBOKI_DATA_STALE_SECONDS``). There is no
    #: second constant.
    data_stale_after_seconds: float = DEFAULT_HEALTH_THRESHOLDS.data_stale_after_seconds
    #: Consecutive rejections of the same instrument before alerting.
    reject_alert_threshold: int = 3
    #: Run the lifecycle monitors every N cycles. Never zero for the same reason
    #: reconciliation is never zero: a monitor that only runs on demand is a
    #: monitor nobody runs.
    lifecycle_every_cycles: int = 12
    #: Refuse to start unless the execution service's mode is one of these.
    #: Defaults to paper only: live execution is opt-in, at the config, every
    #: time.
    allowed_modes: tuple[str, ...] = ("paper",)
    lease_ttl_seconds: float = 90.0


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


class LiveWorker(Worker):
    """One process, one lease, one route to the venue."""

    def __init__(
        self,
        *,
        execution_service: Any,
        feed: BarFeed,
        evaluator: StrategyEvaluator,
        context_builder: ContextBuilder,
        store: WorkerStore,
        market_state: MarketStateUpdater | None = None,
        kill_switch: KillSwitchView | None = None,
        lifecycle: LifecycleTicker | None = None,
        position_reconciler: PositionReconciler | None = None,
        config: LiveWorkerConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config or LiveWorkerConfig(), store, **kwargs)
        self.execution = execution_service
        self.feed = feed
        self.evaluator = evaluator
        self.context_builder = context_builder
        self.market_state = market_state
        self.kill_switch = kill_switch
        self.lifecycle = lifecycle
        self.position_reconciler = position_reconciler
        self.submissions: list[SubmissionRecord] = []
        self.demotions: list[str] = []
        self._rejections: dict[str, int] = {}
        self._cycles = 0
        self._lifecycle_ticks = 0
        #: Monotonic time of the last reconciliation attempt; None until the
        #: first cycle or ``resume``.
        self._last_reconcile_mono: float | None = None
        self.reconciliations = 0
        self.last_reconciliation: Any = None
        self.last_position_divergences: tuple[Any, ...] = ()
        #: Set when ``resume`` refused to start; read by :meth:`run`.
        self.startup_error: StartupReconciliationError | None = None
        self.budget_breaches = 0

    @property
    def lconfig(self) -> LiveWorkerConfig:
        return self.config  # type: ignore[return-value]

    # -- lifecycle -------------------------------------------------------

    def setup(self) -> None:
        """Refuse to start in a mode the config did not authorise.

        V1 shipped ``FIBOKEI_LIVE_EXECUTION_ENABLED: "true"`` inside
        ``render.yaml``, committed, for months.  The environment was the only
        gate and the environment was wrong.  A second, local gate that the
        process itself enforces costs one comparison and closes that door.
        """
        mode = getattr(self.execution, "mode", None)
        label = getattr(mode, "value", str(mode)).lower()
        allowed = {m.lower() for m in self.lconfig.allowed_modes}
        if label not in allowed:
            raise RuntimeError(
                f"LiveWorker refuses to start: execution service is in {label!r} mode but "
                f"this worker is configured to allow {sorted(allowed)}. Widening this is a "
                "deliberate, reviewed change to allowed_modes -- not an environment variable."
            )
        _log.info("live worker mode check passed", extra={"mode": label})

    def resume(self) -> None:
        """Reconcile BEFORE trading anything, and refuse to trade if unclean.

        This is the crash-safe resumption point.  A predecessor that died
        mid-order left an intent in ``PENDING`` or ``UNKNOWN`` with a real
        position possibly behind it.  Trading before resolving that is how a
        restart doubles a position.  Holding the lease is what makes it safe to
        do here: nothing else can be submitting.

        FAIL CLOSED. The previous version logged "NOT clean" at CRITICAL and
        then went on to trade, which made the log line the only control. Any
        exception raised here is caught by :meth:`Worker._run_guarded`, which
        records it on the heartbeat as CRASHED, releases the lease and returns
        without running a single cycle.
        """
        try:
            report = self._reconcile(reason="startup", strict=True)
        except StartupReconciliationError as exc:
            self.startup_error = exc
            raise
        divergences = _divergence_counts(report)
        if self.last_position_divergences:
            divergences["position_manager"] = len(self.last_position_divergences)
        errors = tuple(getattr(report, "errors", ()) or ())
        if not any(divergences.values()) and not errors:
            _log.info(
                "startup reconciliation clean",
                extra={"summary": getattr(report, "summary", lambda: "")()},
            )
            return
        # Could not reconcile at all (venue unreachable) is NOT the same as
        # reconciled-and-disagreed: the first may clear, the second needs a
        # human. Divergences win when both are present.
        blocking = any(divergences.values())
        problems = [f"{k}={v}" for k, v in divergences.items() if v]
        problems += [f"error={e}" for e in errors]
        message = (
            "startup reconciliation is NOT clean; REFUSING TO TRADE: "
            + "; ".join(problems)
            + (
                ". Run `fiboki broker reconcile`, resolve every divergence, then restart."
                if blocking
                else ". Reconciliation could not complete; the worker will not trade "
                "until it can."
            )
        )
        _log.critical(message, extra={"blocking": blocking})
        if self.dispatcher is not None:
            self.dispatcher.fire(
                AlertEvent.RECONCILIATION_DIVERGENCE
                if blocking
                else AlertEvent.BROKER_UNHEALTHY,
                message,
                severity=Severity.CRITICAL,
                source=self.worker_id,
                dedupe_key="startup_reconcile_refused",
                force=True,
            )
        self.startup_error = StartupReconciliationError(message, blocking=blocking)
        raise self.startup_error

    def run(self, *, install_signals: bool = True) -> int:
        """As :meth:`Worker.run`, but a BLOCKING startup refusal exits 75.

        The base class maps every ``resume`` failure to ``EXIT_FATAL``, which a
        supervisor restarts. For a divergence that is a restart loop that
        re-discovers the same divergence every few seconds, so it is mapped to
        :data:`EXIT_RECONCILE_BLOCKED` here, where the reason is known.
        """
        self.startup_error = None
        code = super().run(install_signals=install_signals)
        if (
            code == EXIT_FATAL
            and self.startup_error is not None
            and self.startup_error.blocking
        ):
            code = EXIT_RECONCILE_BLOCKED
            self.exit_code = code
            print(
                f"FATAL (exit {code}, do not restart-loop): {self.startup_error}",
                file=sys.stderr,
                flush=True,
            )
        return code

    def on_abandon(self) -> None:
        """A cycle abandoned mid-submit leaves the intent store as the truth.

        Nothing is "rolled back" here, deliberately: the execution service
        writes its PENDING intent before dispatch precisely so that an
        abandoned submit is recoverable.  What this does is say so, loudly, in
        the record the next operator reads.
        """
        _log.critical(
            "live cycle abandoned; an in-flight submission may exist at the venue. "
            "Run `fiboki broker reconcile` before restarting.",
            extra={"worker_id": self.worker_id},
        )
        if self.dispatcher is not None:
            self.dispatcher.fire(
                AlertEvent.ORDER_UNKNOWN,
                "live worker abandoned a cycle mid-flight; reconcile before restarting",
                severity=Severity.CRITICAL,
                source=self.worker_id,
                force=True,
            )

    # -- the cycle -------------------------------------------------------

    def run_cycle(self) -> CycleResult:
        correlation = new_correlation_id("live")
        with bind(correlation_id=correlation):
            # The timer runs whether or not a bar is due: a weekend with no
            # bars must still reconcile.
            reconciled = self._reconcile_if_interval_elapsed()

            wait = getattr(self.feed, "wait_until_due", None)
            if callable(wait) and not wait(should_stop=lambda: self.stopping):
                return CycleResult.idle("waiting for the next bar boundary")

            self._cycles += 1
            started = self.monotonic()
            timeframe = ""
            try:
                with bind(cycle=self._cycles):
                    batch = self.feed.poll()
                    timeframe = batch.timeframe
                    return self._evaluate(batch, reconciled=reconciled)
            finally:
                self._check_cycle_budget(self.monotonic() - started, timeframe)

    def _evaluate(self, batch: BarBatch, *, reconciled: bool = False) -> CycleResult:
        """One evaluation pass over a polled batch. The old body of run_cycle."""
        self._record_freshness(batch)

        if self.market_state is not None and batch.count:
            self.market_state.update(batch)

        if (
            not reconciled
            and self._cycles % max(1, self.lconfig.reconcile_every_cycles) == 0
        ):
            self._reconcile(reason="periodic")

        # BEFORE evaluation, so a strategy this tick demotes is already
        # demoted when the gateway reads its lifecycle later in this cycle.
        if (
            self.lifecycle is not None
            and self._cycles % max(1, self.lconfig.lifecycle_every_cycles) == 0
        ):
            self._tick_lifecycle()

        instruments = sorted(batch.closed_instruments)
        if not instruments:
            return CycleResult.idle("no closed bars")

        if self.kill_switch is not None and self.kill_switch.blocks_new_risk():
            _log.warning(
                "kill switch blocks new risk; evaluating nothing this cycle",
                extra={"instruments": len(instruments)},
            )
            return CycleResult.idle("kill switch active")

        plans = list(self.evaluator.evaluate(instruments, batch))
        if not plans:
            return CycleResult.idle(f"{len(instruments)} closed bar(s), no plans")

        submitted = 0
        for plan in plans:
            if self.stopping:
                _log.info("stop requested; not submitting remaining plans")
                break
            if self._submit(plan):
                submitted += 1
        return CycleResult.worked(
            submitted, f"{len(plans)} plan(s), {submitted} submitted"
        )

    def _submit(self, plan: Any) -> bool:
        """The ONLY place this module touches execution.  One call. No Order."""
        instrument = str(getattr(plan, "instrument", ""))
        plan_id = str(getattr(plan, "plan_id", ""))
        context = self.context_builder(plan)
        started = time.monotonic()
        try:
            outcome = self.execution.submit(plan, context)
        except Exception as exc:
            # Do NOT swallow. The loop's handler records it on the heartbeat
            # and reports it; an execution error is exactly the thing V1's
            # bare except hid.
            _log.error(
                "submit raised",
                extra={
                    "plan_id": plan_id,
                    "instrument": instrument,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        finally:
            _metrics.BROKER_LATENCY.observe(
                time.monotonic() - started,
                broker=getattr(self.execution.adapter, "name", "adapter"),
                operation="submit",
            )

        accepted = bool(getattr(outcome, "accepted", False))
        reason = str(getattr(outcome, "reason", ""))
        blocked = bool(getattr(outcome, "blocked_by_risk", False))
        self.submissions.append(
            SubmissionRecord(plan_id, instrument, accepted, reason, blocked)
        )

        if accepted:
            self._rejections.pop(instrument, None)
            self._alert(
                AlertEvent.ORDER_SUBMITTED,
                f"order submitted for {instrument} (plan {plan_id})",
                instrument=instrument,
                plan_id=plan_id,
            )
            return True

        if blocked:
            self._alert(
                AlertEvent.RISK_LIMIT_BREACH,
                f"risk gateway blocked {instrument}: {reason}",
                dedupe_key=f"risk_block:{instrument}",
                instrument=instrument,
                plan_id=plan_id,
            )
            return False

        count = self._rejections.get(instrument, 0) + 1
        self._rejections[instrument] = count
        if count >= self.lconfig.reject_alert_threshold:
            self._alert(
                AlertEvent.ORDER_REJECTED_REPEATEDLY,
                f"{instrument} rejected {count}x in a row; latest reason: {reason}",
                dedupe_key=f"repeat_reject:{instrument}",
                instrument=instrument,
                rejections=count,
            )
        else:
            self._alert(
                AlertEvent.ORDER_REJECTED,
                f"order rejected for {instrument}: {reason}",
                instrument=instrument,
                plan_id=plan_id,
            )
        return False

    # -- the lifecycle timer ---------------------------------------------

    def _tick_lifecycle(self) -> None:
        """Run the monitors. A failure here is reported, never swallowed.

        The service raises its own alerts through its own dispatcher, so this
        does not re-fire them -- two alerts for one demotion trains an operator
        to skim. What it does do is record the demotions on the worker and shout
        if the monitor itself could not run, because a monitor that silently
        stopped running looks exactly like a fleet with nothing wrong.
        """
        try:
            results = self.lifecycle.tick()  # type: ignore[union-attr]
        except Exception as exc:
            _log.error(
                "lifecycle evaluation failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            self._alert(
                AlertEvent.STRATEGY_DEGRADED,
                (
                    "the lifecycle monitors could not run: "
                    f"{type(exc).__name__}: {exc}. No strategy was evaluated this "
                    "tick, which is not the same as every strategy being healthy."
                ),
                dedupe_key="lifecycle_tick_error",
                severity=Severity.ERROR,
            )
            return
        self._lifecycle_ticks += 1
        for result in results or ():
            if not getattr(result, "demoted", False):
                continue
            summary = getattr(result, "summary", lambda: "")()
            self.demotions.append(summary)
            _log.warning("strategy demoted by the lifecycle monitors", extra={"summary": summary})

    # -- reconciliation and freshness ------------------------------------

    def _reconcile_if_interval_elapsed(self) -> bool:
        """The wall-time reconciliation timer. True if it reconciled."""
        interval = self.lconfig.reconcile_interval_seconds
        now = self.monotonic()
        if self._last_reconcile_mono is None:
            # First cycle without a resume() (a unit test driving run_cycle):
            # start the clock rather than reconciling immediately.
            self._last_reconcile_mono = now
            return False
        if interval <= 0 or (now - self._last_reconcile_mono) < interval:
            return False
        self._reconcile(reason="interval")
        return True

    def _reconcile(self, *, reason: str, strict: bool = False) -> Any:
        """Reconcile against the venue and alert on anything that is not clean.

        ``strict`` is the startup mode: a reconciliation that could not run at
        all raises :class:`StartupReconciliationError` instead of returning
        ``None``, because "we could not check" must not read as "clean".
        """
        self._last_reconcile_mono = self.monotonic()
        self.reconciliations += 1
        reconcile = getattr(self.execution, "reconcile", None)
        if reconcile is None:
            if strict:
                raise StartupReconciliationError(
                    "the execution service has no reconcile(); a live worker cannot "
                    "prove the venue agrees with its record and will not start",
                    blocking=False,
                )
            return None
        try:
            report = reconcile()
        except Exception as exc:
            _log.error(
                "reconciliation failed",
                extra={"reason": reason, "error": f"{type(exc).__name__}: {exc}"},
            )
            self._alert(
                AlertEvent.BROKER_UNHEALTHY,
                f"reconciliation ({reason}) failed: {type(exc).__name__}: {exc}",
                dedupe_key="reconcile_error",
            )
            if strict:
                raise StartupReconciliationError(
                    f"startup reconciliation raised {type(exc).__name__}: {exc}; "
                    "REFUSING TO TRADE until it can run",
                    blocking=False,
                ) from exc
            return None
        self.last_reconciliation = report

        counts = _divergence_counts(report)
        unknown = counts["unknown"]
        orphans = counts["orphans"]
        mismatches = counts["size_mismatches"]
        _metrics.record_reconciliation_divergence(
            unknown=unknown, orphan=orphans, size_mismatch=mismatches
        )
        divergences = unknown + orphans + mismatches
        if divergences:
            self._alert(
                AlertEvent.RECONCILIATION_DIVERGENCE,
                (
                    f"reconciliation ({reason}) found {divergences} divergence(s): "
                    f"{unknown} unknown, {orphans} orphan position(s) at the venue, "
                    f"{mismatches} size mismatch(es). An orphan is a REAL position with no "
                    "local record and no stop attached to it."
                ),
                dedupe_key="reconcile_divergence",
                unknown=unknown,
                orphans=orphans,
                size_mismatches=mismatches,
            )

        # ``ExecutionService.reconcile`` catches a venue failure and returns it
        # as ``errors`` rather than raising. Counting only the three divergence
        # fields made "the venue did not answer" indistinguishable from clean.
        errors = tuple(getattr(report, "errors", ()) or ())
        if errors:
            self._alert(
                AlertEvent.BROKER_UNHEALTHY,
                f"reconciliation ({reason}) could not complete: " + "; ".join(errors),
                dedupe_key="reconcile_error",
                errors=len(errors),
            )

        self._reconcile_positions(reason=reason, strict=strict)
        return report

    def _reconcile_positions(self, *, reason: str, strict: bool) -> None:
        """Venue-side levels versus the position manager's intent, detect only.

        ``repair=False``: this timer DETECTS. Correcting a stop is an amendment
        routed through the gateway by the manager's own bar loop; a
        reconciliation timer that also amended would be a second, unreviewed
        route to the venue.
        """
        self.last_position_divergences = ()
        if self.position_reconciler is None:
            return
        try:
            found = tuple(self.position_reconciler.reconcile(repair=False) or ())
        except Exception as exc:
            _log.error(
                "position reconciliation failed",
                extra={"reason": reason, "error": f"{type(exc).__name__}: {exc}"},
            )
            self._alert(
                AlertEvent.BROKER_UNHEALTHY,
                f"position reconciliation ({reason}) failed: {type(exc).__name__}: {exc}",
                dedupe_key="position_reconcile_error",
            )
            if strict:
                raise StartupReconciliationError(
                    f"startup position reconciliation raised {type(exc).__name__}: {exc}",
                    blocking=False,
                ) from exc
            return
        self.last_position_divergences = found
        if found:
            kinds: dict[str, int] = {}
            for divergence in found:
                kind = str(getattr(divergence, "kind", "divergence"))
                kinds[kind] = kinds.get(kind, 0) + 1
            self._alert(
                AlertEvent.RECONCILIATION_DIVERGENCE,
                (
                    f"position reconciliation ({reason}) found {len(found)} "
                    "divergence(s) between the venue's resting levels and the "
                    "manager's intent: "
                    + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
                ),
                dedupe_key="position_reconcile_divergence",
                divergences=len(found),
            )

    # -- the cycle-time budget -------------------------------------------

    def _check_cycle_budget(self, elapsed: float, timeframe: str) -> None:
        label = timeframe or "?"
        LIVE_CYCLE_SECONDS.set(elapsed, worker=self.worker_id, timeframe=label)
        fraction = self.lconfig.cycle_budget_fraction
        span = timeframe_seconds(timeframe)
        if fraction <= 0 or span is None:
            return
        budget = fraction * span
        if elapsed <= budget:
            return
        self.budget_breaches += 1
        LIVE_CYCLE_BUDGET_EXCEEDED.inc(worker=self.worker_id, timeframe=label)
        message = (
            f"live cycle took {elapsed:.1f}s, over its budget of {budget:.0f}s "
            f"({fraction:.0%} of {label}). Orders from a slow cycle reach the venue "
            "later than the bar they were computed on."
        )
        if log_once(f"cycle_budget:{self.worker_id}", ttl=span):
            _log.warning(message, extra={"elapsed_s": elapsed, "budget_s": budget})
        # There is no dedicated slow-worker event in the taxonomy; the base
        # worker already reports its own degradation as STRATEGY_DEGRADED.
        self._alert(
            AlertEvent.STRATEGY_DEGRADED,
            message,
            severity=Severity.WARNING,
            dedupe_key=f"cycle_budget:{self.worker_id}",
            elapsed_s=round(elapsed, 3),
            budget_s=budget,
        )

    def _record_freshness(self, batch: BarBatch) -> None:
        """DATA_STALE when the NEXT bar is overdue, not when the last one is old.

        ``batch.ages`` are seconds since the newest closed bar's close. A bar
        that closed 59 minutes ago on an H4 feed is the current bar, not a
        stale one; the next close is due at ``timeframe`` and the feed is
        stale once that is ``data_stale_after_seconds`` late. An unknown
        timeframe (``""``) falls back to the bare threshold.
        """
        span = timeframe_seconds(batch.timeframe) or 0.0
        limit = span + self.lconfig.data_stale_after_seconds
        stale: list[str] = []
        for instrument, age in batch.ages.items():
            _metrics.record_data_freshness(instrument, batch.timeframe or "?", age)
            if instrument in batch.market_closed:
                # Closed, not stale: a Friday bar on a Sunday is expected.
                continue
            if age >= limit:
                stale.append(f"{instrument}={age:.0f}s (next bar {age - span:.0f}s overdue)")
        if stale:
            self._alert(
                AlertEvent.DATA_STALE,
                "newest bar is stale for: " + ", ".join(sorted(stale)),
                dedupe_key="data_stale",
                instruments=len(stale),
            )

    def _alert(self, event: AlertEvent, message: str, **kwargs: Any) -> None:
        if self.dispatcher is None:
            return
        self.dispatcher.fire(event, message, source=self.worker_id, **kwargs)

    # -- introspection ---------------------------------------------------

    def summary(self) -> dict[str, Any]:
        accepted = sum(1 for s in self.submissions if s.accepted)
        return {
            **self.status(),
            "cycles": self._cycles,
            "submissions": len(self.submissions),
            "accepted": accepted,
            "blocked_by_risk": sum(1 for s in self.submissions if s.blocked_by_risk),
            "repeat_rejections": dict(self._rejections),
            "lifecycle_ticks": self._lifecycle_ticks,
            "demotions": list(self.demotions),
            "reconciliations": self.reconciliations,
            "cycle_budget_breaches": self.budget_breaches,
        }


def _divergence_counts(report: Any) -> dict[str, int]:
    """The three divergence kinds on a ``ReconciliationReport``, as counts."""
    return {
        "unknown": len(getattr(report, "still_unknown", ()) or ()),
        "orphans": len(getattr(report, "orphan_broker_refs", ()) or ()),
        "size_mismatches": len(getattr(report, "size_mismatches", ()) or ()),
    }
