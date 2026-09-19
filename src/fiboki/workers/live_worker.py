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

Kill switch
-----------
Checked before every evaluation pass.  When it blocks new risk, the worker
still ingests bars, still updates state, still reconciles -- it simply does
not submit opening intents.  A kill switch that also stops data is one that
blinds you at the moment you most need to see.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from fiboki.obs import metrics as _metrics
from fiboki.obs.alerts import AlertEvent, Severity
from fiboki.obs.logging import bind, get_logger, new_correlation_id
from fiboki.workers.base import CycleResult, Worker, WorkerConfig, WorkerStore

__all__ = [
    "BarBatch",
    "BarFeed",
    "ContextBuilder",
    "LiveWorker",
    "LiveWorkerConfig",
    "MarketStateUpdater",
    "StrategyEvaluator",
    "SubmissionRecord",
]

_log = get_logger("fiboki.workers.live")


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

    @property
    def count(self) -> int:
        return len(self.frames)


@runtime_checkable
class BarFeed(Protocol):
    """Pulls new bars.  The worker never talks to a provider directly."""

    def poll(self) -> BarBatch: ...


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
    #: Bars older than this trigger DATA_STALE.
    data_stale_after_seconds: float = 900.0
    #: Consecutive rejections of the same instrument before alerting.
    reject_alert_threshold: int = 3
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
        self.submissions: list[SubmissionRecord] = []
        self._rejections: dict[str, int] = {}
        self._cycles = 0

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
        """Reconcile BEFORE trading anything.

        This is the crash-safe resumption point.  A predecessor that died
        mid-order left an intent in ``PENDING`` or ``UNKNOWN`` with a real
        position possibly behind it.  Trading before resolving that is how a
        restart doubles a position.  Holding the lease is what makes it safe to
        do here: nothing else can be submitting.
        """
        report = self._reconcile(reason="startup")
        if report is not None and not getattr(report, "clean", True):
            _log.critical(
                "startup reconciliation is NOT clean",
                extra={"summary": getattr(report, "summary", lambda: "")()},
            )

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
        self._cycles += 1
        correlation = new_correlation_id("live")
        with bind(correlation_id=correlation, cycle=self._cycles):
            batch = self.feed.poll()
            self._record_freshness(batch)

            if self.market_state is not None and batch.count:
                self.market_state.update(batch)

            if self._cycles % max(1, self.lconfig.reconcile_every_cycles) == 0:
                self._reconcile(reason="periodic")

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

    # -- reconciliation and freshness ------------------------------------

    def _reconcile(self, *, reason: str) -> Any:
        reconcile = getattr(self.execution, "reconcile", None)
        if reconcile is None:
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
            return None

        unknown = len(getattr(report, "still_unknown", ()) or ())
        orphans = len(getattr(report, "orphan_broker_refs", ()) or ())
        mismatches = len(getattr(report, "size_mismatches", ()) or ())
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
        return report

    def _record_freshness(self, batch: BarBatch) -> None:
        stale: list[str] = []
        for instrument, age in batch.ages.items():
            _metrics.record_data_freshness(instrument, batch.timeframe or "?", age)
            if age >= self.lconfig.data_stale_after_seconds:
                stale.append(f"{instrument}={age:.0f}s")
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
        }
