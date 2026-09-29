"""The MANDATORY pre-order risk gateway.

V1's ``RiskEngine.check_trade_allowed`` had **zero call sites in production
code**. The limits were computed, rendered on a dashboard, and enforced
nowhere. Every number an operator looked at was decorative.

V2 inverts that. :class:`RiskGateway` is the only way to obtain permission to
place an order, :class:`fiboki.broker.execution_service.ExecutionService` will
not construct an ``Order`` without one, and
``tests/unit/test_no_gateway_bypass.py`` walks the AST of the entire source
tree asserting that no other module constructs an ``Order`` at all.

Three properties matter more than the individual checks:

**Every check is named.** ``RiskDecision.checks_run`` lists all nineteen check
names on every decision, allowed or blocked. An audit can prove which rules ran
rather than trusting that they did.

**Fail closed.** An exception inside any check blocks the order and is reported
as ``check_error:<name>``. So does a check that somehow did not run at all
(:meth:`RiskGateway._verify_coverage`), and so does missing input data: a
``None`` price age is not "fresh", it is "unknown", and unknown blocks.

**Nothing is dropped silently.** Every blocked plan produces an
:class:`ExecutionAttempt` row carrying the named reasons, which the recorder
persists exactly like a filled order's telemetry. V1 dropped rejects on the
floor, so "why didn't it trade?" was unanswerable after the fact.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import pandas as pd

from fiboki.backtest.locks import LockSource, LockStateUnavailable
from fiboki.core.contracts import ExecutionTelemetry, RiskDecision, TradePlan
from fiboki.core.enums import ExecutionMode, StrategyLifecycle
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.portfolio.construction import PortfolioSnapshot
from fiboki.risk.killswitch import KillSwitch, RequestKind
from fiboki.risk.limits import DEFAULT_LIMITS, LimitSet
from fiboki.strategy.dsl import LOCKS_DECLARED_FEATURE

__all__ = [
    "AttemptRecorder",
    "ExecutionAttempt",
    "ExitContext",
    "InMemoryAttemptRecorder",
    "MarketView",
    "RiskContext",
    "RiskGateway",
    "StrategyView",
    "VenueView",
]


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketView:
    """What we believe about the market for this instrument, right now.

    ``None`` never means "fine". It means "unknown", and unknown blocks.
    """

    mid_price: float | None = None
    spread_price: float | None = None
    #: Timestamp of the most recent tradeable quote.
    quote_time: pd.Timestamp | None = None
    #: Timestamp of the most recent completed bar in our own data store.
    last_bar_time: pd.Timestamp | None = None
    market_open: bool | None = None
    regime: str = "unknown"
    #: Timestamps of flagged high-impact economic events.
    event_times: tuple[pd.Timestamp, ...] = ()
    halted: bool = False


@dataclass(frozen=True, slots=True)
class VenueView:
    """Broker/venue health as reported by the adapter's ``health()``."""

    connected: bool | None = None
    #: 0.0 (unusable) .. 1.0 (nominal).
    score: float | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class StrategyView:
    lifecycle: StrategyLifecycle | None = None
    #: 0.0 (fully degraded) .. 1.0 (healthy).
    health: float = 1.0
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Everything the gateway needs. Assembled by the caller, never fetched here.

    The gateway performs no I/O. It cannot reach a broker, a database or a
    clock of its own, which is what makes it exhaustively testable and what
    stops a check from silently succeeding because a network call timed out.
    """

    plan: TradePlan
    snapshot: PortfolioSnapshot
    now: pd.Timestamp
    mode: ExecutionMode
    limits: LimitSet = DEFAULT_LIMITS
    market: MarketView = field(default_factory=MarketView)
    venue: VenueView = field(default_factory=VenueView)
    strategy: StrategyView = field(default_factory=StrategyView)
    request_kind: RequestKind = RequestKind.OPEN

    #: Sum of risk-to-stop across all open positions, ACCOUNT currency.
    open_risk_amount: float = 0.0
    #: Gross notional already held in instruments correlated above the limit
    #: set's ``correlation_threshold`` with this plan's instrument.
    correlated_exposure: float = 0.0
    #: Realised + unrealised P&L over the rolling day / week, account currency.
    #: Negative numbers are losses.
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    #: FX rate converting the instrument's quote currency to the account
    #: currency at decision time. Used for notional arithmetic.
    fx_quote_to_account: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def instrument(self) -> Instrument:
        return get_instrument(self.plan.instrument)

    @property
    def equity(self) -> float:
        return self.snapshot.account.equity

    @property
    def plan_notional_account(self) -> float:
        """Gross notional this plan would add, in the account currency."""
        price = self.market.mid_price or self.plan.signal.reference_price
        return abs(
            price
            * self.plan.size
            * self.instrument.contract_size
            * self.fx_quote_to_account
        )


@dataclass(frozen=True, slots=True)
class ExitContext:
    """Inputs for a RISK-REDUCING order (a close or a partial reduction).

    An exit runs a deliberately smaller check set. Running ``max_account_risk``
    on a closing order would refuse to let you out of the book precisely when
    the book is over its limit -- the trap version of a risk control. What an
    exit still must satisfy is that the venue is reachable, the market is open,
    the price is not stale and the kill switch permits this *kind* of request
    (FLATTEN permits closes and forbids opens; PAUSE permits both closes and
    reductions).

    It carries the same ``market``/``venue``/``now``/``request_kind`` surface as
    :class:`RiskContext` so the very same check methods run against it. There is
    no second implementation of "is this price stale".
    """

    instrument: str
    strategy_id: str
    size: float
    now: pd.Timestamp
    mode: ExecutionMode
    position_id: str = ""
    limits: LimitSet = DEFAULT_LIMITS
    market: MarketView = field(default_factory=MarketView)
    venue: VenueView = field(default_factory=VenueView)
    request_kind: RequestKind = RequestKind.CLOSE
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Attempt records
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """One attempt to place an order, allowed or not. NEVER dropped.

    A blocked attempt is as much a fact about the system as a fill is. V1 kept
    no record, so a strategy that stopped trading looked identical to a
    strategy with no signals.
    """

    plan_id: str
    signal_id: str
    strategy_id: str
    instrument: str
    mode: ExecutionMode
    requested_size: float
    decision: RiskDecision
    decided_at: pd.Timestamp
    limits_version: str
    limits_fingerprint: str
    client_ref: str | None = None
    order_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def reason(self) -> str:
        return ";".join(self.decision.reasons) if self.decision.reasons else ""

    def to_telemetry(self, *, spread_at_decision: float = 0.0) -> ExecutionTelemetry:
        """Render as :class:`ExecutionTelemetry` so blocked and filled orders
        land in one table. A blocked attempt has no submit or ack, so those
        carry the decision time and ``None`` respectively, and the named block
        reason goes in ``venue_error`` -- it is the reason nothing reached a
        venue."""
        return ExecutionTelemetry(
            order_id=self.order_id or self.plan_id,
            signal_time=self.decided_at,
            decision_time=self.decided_at,
            submit_time=self.decided_at,
            ack_time=None,
            requested_price=float(self.extra.get("requested_price", 0.0)),
            filled_price=None,
            requested_size=self.requested_size,
            filled_size=0.0,
            rejected_size=self.requested_size,
            spread_at_decision=spread_at_decision,
            latency_ms=0.0,
            venue_error=self.reason or None,
            market_regime=str(self.extra.get("regime", "")) or None,
            mode=self.mode,
            extra={
                "blocked_by_risk_gateway": not self.allowed,
                "checks_run": list(self.decision.checks_run),
                "limits_version": self.limits_version,
                "limits_fingerprint": self.limits_fingerprint,
                "strategy_id": self.strategy_id,
                "instrument": self.instrument,
                "client_ref": self.client_ref,
                **self.extra,
            },
        )

    def as_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["decision"] = {
            "allowed": self.decision.allowed,
            "reasons": list(self.decision.reasons),
            "checks_run": list(self.decision.checks_run),
        }
        d["mode"] = self.mode.value
        d["decided_at"] = self.decided_at.isoformat()
        return d


class AttemptRecorder(Protocol):
    def record(self, attempt: ExecutionAttempt) -> None: ...


class InMemoryAttemptRecorder:
    """Default recorder. Keeps every attempt in submission order."""

    def __init__(self) -> None:
        self.attempts: list[ExecutionAttempt] = []

    def record(self, attempt: ExecutionAttempt) -> None:
        self.attempts.append(attempt)

    @property
    def blocked(self) -> list[ExecutionAttempt]:
        return [a for a in self.attempts if not a.allowed]

    def reasons(self) -> list[str]:
        return [a.reason for a in self.blocked]


# --------------------------------------------------------------------------
# The gateway
# --------------------------------------------------------------------------


class _CheckFailure(Exception):
    """Internal: a check voted to block, with a named reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RiskGateway:
    """Pre-order risk control. Mandatory, exhaustive, fail-closed.

    Usage is always the same two lines, and the second may not be skipped::

        decision = gateway.evaluate(context)
        if not decision.allowed:
            return   # the attempt row has already been recorded

    :meth:`evaluate` records the attempt itself, so there is no way to obtain a
    block and forget to persist it.
    """

    #: Every check, in the order it runs. The gateway asserts after the fact
    #: that each of these names appears in ``checks_run``; a check that did not
    #: run blocks the order.
    CHECKS: tuple[str, ...] = (
        "kill_switch",
        "strategy_lifecycle",
        "market_state",
        "data_freshness",
        "stale_price",
        "abnormal_spread",
        "broker_health",
        "event_blackout",
        "instrument_lock",
        "max_per_trade_risk",
        "max_account_risk",
        "max_instrument_exposure",
        "max_strategy_exposure",
        "max_currency_exposure",
        "max_correlated_exposure",
        "daily_loss",
        "weekly_loss",
        "total_drawdown",
        "margin_utilisation",
    )

    def __init__(
        self,
        *,
        kill_switch: KillSwitch | None = None,
        recorder: AttemptRecorder | None = None,
        limits: LimitSet = DEFAULT_LIMITS,
        locks: LockSource | None = None,
    ) -> None:
        self.kill_switch = kill_switch or KillSwitch()
        self.recorder: AttemptRecorder = recorder or InMemoryAttemptRecorder()
        self.default_limits = limits
        #: Where entry-lock state comes from. Like the kill switch it is held by
        #: the gateway rather than passed per call, and like the kill switch it
        #: must survive a restart: the intended source is
        #: :class:`fiboki.backtest.locks.LedgerLockView` over the durable trade
        #: (or intent) ledger, which rebuilds the state on every question.
        self.locks = locks

    # ------------------------------------------------------------ evaluate

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        """Run every check, record the attempt, return the decision.

        Checks are never short-circuited. A blocked order still runs the
        remaining checks so the operator sees *all* the reasons, not just the
        first one -- "it was blocked for stale price" is a much worse answer
        than "it was blocked for stale price, 6% account risk and a paused kill
        switch" when you are deciding whether to intervene.
        """
        limits = ctx.limits or self.default_limits
        reasons: list[str] = []
        ran: list[str] = []

        for name in self.CHECKS:
            ran.append(name)
            try:
                handler = getattr(self, f"_check_{name}")
            except AttributeError:  # pragma: no cover - defensive
                reasons.append(f"check_missing:{name}")
                continue
            try:
                handler(ctx, limits)
            except _CheckFailure as blocked:
                reasons.append(blocked.reason)
            except Exception as exc:
                # FAIL CLOSED. A check that raises has not passed. Swallowing
                # this and continuing is how a risk system quietly becomes
                # decorative.
                reasons.append(f"check_error:{name}:{type(exc).__name__}:{exc}")

        coverage = self._verify_coverage(tuple(ran))
        if coverage:
            reasons.extend(coverage)

        decision = RiskDecision(
            allowed=not reasons,
            reasons=tuple(reasons),
            adjusted_size=None,
            checks_run=tuple(ran),
            decided_at=ctx.now,
        )
        self._record(ctx, decision, limits)
        return decision

    #: The subset that applies to a risk-REDUCING order. Named separately so a
    #: decision record makes plain which set ran; an exit decision listing five
    #: checks is not a bug, it is the exit check set.
    EXIT_CHECKS: tuple[str, ...] = (
        "kill_switch",
        "market_state",
        "data_freshness",
        "stale_price",
        "broker_health",
    )

    def evaluate_exit(self, ctx: ExitContext) -> RiskDecision:
        """Permission for a closing or reducing order. Same checks, smaller set.

        This exists so that *no* order path bypasses the gateway. V1's kill
        switch blocked new orders and abandoned open positions; here the closing
        order is a first-class gateway decision with its own recorded attempt.
        """
        if ctx.request_kind.adds_risk:
            raise ValueError(
                f"evaluate_exit called with {ctx.request_kind.value}, which ADDS "
                "risk. Risk-adding orders must go through evaluate() and the "
                "full check set."
            )
        limits = ctx.limits or self.default_limits
        reasons: list[str] = []
        ran: list[str] = []
        for name in self.EXIT_CHECKS:
            ran.append(name)
            handler = getattr(self, f"_check_{name}")
            try:
                handler(ctx, limits)
            except _CheckFailure as blocked:
                reasons.append(blocked.reason)
            except Exception as exc:
                reasons.append(f"check_error:{name}:{type(exc).__name__}:{exc}")

        missing = [c for c in self.EXIT_CHECKS if c not in ran]
        reasons.extend(f"check_did_not_run:{m}" for m in missing)

        decision = RiskDecision(
            allowed=not reasons,
            reasons=tuple(reasons),
            adjusted_size=None,
            checks_run=tuple(ran),
            decided_at=ctx.now,
        )
        stamp = limits.stamp()
        self.recorder.record(
            ExecutionAttempt(
                plan_id=ctx.position_id or f"exit:{ctx.instrument}",
                signal_id="",
                strategy_id=ctx.strategy_id,
                instrument=ctx.instrument,
                mode=ctx.mode,
                requested_size=ctx.size,
                decision=decision,
                decided_at=ctx.now,
                limits_version=stamp["limits_version"],
                limits_fingerprint=stamp["limits_fingerprint"],
                extra={
                    "requested_price": ctx.market.mid_price or 0.0,
                    "regime": ctx.market.regime,
                    "request_kind": ctx.request_kind.value,
                    "exit": True,
                    **ctx.extra,
                },
            )
        )
        return decision

    def _verify_coverage(self, ran: Sequence[str]) -> list[str]:
        missing = [c for c in self.CHECKS if c not in ran]
        return [f"check_did_not_run:{m}" for m in missing]

    def _record(self, ctx: RiskContext, decision: RiskDecision, limits: LimitSet) -> None:
        stamp = limits.stamp()
        attempt = ExecutionAttempt(
            plan_id=ctx.plan.plan_id,
            signal_id=ctx.plan.signal.signal_id,
            strategy_id=ctx.plan.signal.strategy_id,
            instrument=ctx.plan.instrument,
            mode=ctx.mode,
            requested_size=ctx.plan.size,
            decision=decision,
            decided_at=ctx.now,
            limits_version=stamp["limits_version"],
            limits_fingerprint=stamp["limits_fingerprint"],
            extra={
                "requested_price": ctx.market.mid_price or ctx.plan.signal.reference_price,
                "regime": ctx.market.regime,
                "request_kind": ctx.request_kind.value,
                "risk_amount": ctx.plan.risk_amount,
                "portfolio_weight": ctx.plan.portfolio_weight,
            },
        )
        self.recorder.record(attempt)

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _block(reason: str) -> None:
        raise _CheckFailure(reason)

    @staticmethod
    def _pct_of_equity(amount: float, equity: float) -> float:
        if equity <= 0:
            raise ValueError("equity must be positive to express a percentage of it")
        return amount / equity * 100.0

    @staticmethod
    def _age_seconds(then: pd.Timestamp | None, now: pd.Timestamp) -> float:
        if then is None:
            raise ValueError("timestamp is unknown")
        if then.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        return float((now - then).total_seconds())

    # -------------------------------------------------------------- checks

    def _check_kill_switch(self, ctx: RiskContext, limits: LimitSet) -> None:
        ok, why = self.kill_switch.allows(ctx.request_kind)
        if not ok:
            self._block(why)

    def _check_strategy_lifecycle(self, ctx: RiskContext, limits: LimitSet) -> None:
        lc = ctx.strategy.lifecycle
        if lc is None:
            self._block("strategy_lifecycle_unknown")
            return
        forbidden = {
            StrategyLifecycle.QUARANTINED,
            StrategyLifecycle.RETIRED,
            StrategyLifecycle.DEGRADED,
            StrategyLifecycle.DISCOVERY,
            StrategyLifecycle.RESEARCH,
            StrategyLifecycle.VALIDATING,
            StrategyLifecycle.CANDIDATE,
        }
        if lc in forbidden:
            self._block(f"strategy_lifecycle_blocks:{lc.value}")
        if ctx.strategy.degraded:
            self._block("strategy_degraded")
        # A strategy may only trade in a mode at or below its lifecycle stage.
        if ctx.mode is ExecutionMode.LIVE and lc is not StrategyLifecycle.LIVE:
            self._block(f"strategy_not_live_approved:{lc.value}")
        if ctx.mode is ExecutionMode.DEMO and lc in (StrategyLifecycle.PAPER,):
            self._block(f"strategy_not_demo_approved:{lc.value}")

    def _check_market_state(self, ctx: RiskContext, limits: LimitSet) -> None:
        if ctx.market.halted:
            self._block("market_halted")
        if ctx.market.market_open is None:
            self._block("market_state_unknown")
            return
        if not ctx.market.market_open:
            self._block("market_closed")

    def _check_data_freshness(self, ctx: RiskContext, limits: LimitSet) -> None:
        age = self._age_seconds(ctx.market.last_bar_time, ctx.now)
        if age < 0:
            self._block(f"data_from_the_future:{age:.1f}s")
        if age > limits.max_data_age_seconds:
            self._block(f"data_stale:{age:.1f}s>{limits.max_data_age_seconds}s")

    def _check_stale_price(self, ctx: RiskContext, limits: LimitSet) -> None:
        age = self._age_seconds(ctx.market.quote_time, ctx.now)
        if age < 0:
            self._block(f"quote_from_the_future:{age:.1f}s")
        if age > limits.max_price_age_seconds:
            self._block(f"stale_price:{age:.1f}s>{limits.max_price_age_seconds}s")

    def _check_abnormal_spread(self, ctx: RiskContext, limits: LimitSet) -> None:
        spread = ctx.market.spread_price
        if spread is None:
            self._block("spread_unknown")
            return
        if spread < 0:
            self._block(f"negative_spread:{spread}")
        typical = ctx.instrument.typical_spread_pips * ctx.instrument.pip_size
        if typical <= 0:
            return
        multiple = spread / typical
        if multiple > limits.max_spread_multiple:
            self._block(
                f"abnormal_spread:{multiple:.2f}x>{limits.max_spread_multiple}x_typical"
            )

    def _check_broker_health(self, ctx: RiskContext, limits: LimitSet) -> None:
        if ctx.venue.connected is None or ctx.venue.score is None:
            self._block("broker_health_unknown")
            return
        if not ctx.venue.connected:
            self._block(f"broker_disconnected:{ctx.venue.message}")
        if ctx.venue.score < limits.min_broker_health:
            self._block(
                f"broker_unhealthy:{ctx.venue.score:.2f}<{limits.min_broker_health}"
            )

    def _check_event_blackout(self, ctx: RiskContext, limits: LimitSet) -> None:
        window = pd.Timedelta(minutes=limits.event_blackout_minutes)
        if window <= pd.Timedelta(0):
            return
        for ev in ctx.market.event_times:
            if ev.tzinfo is None:
                self._block("event_time_not_utc")
                continue
            if abs(ev - ctx.now) <= window:
                self._block(f"event_blackout:{ev.isoformat()}")

    def _check_instrument_lock(self, ctx: RiskContext, limits: LimitSet) -> None:
        """Entry locks declared by strategy documents. Fail-closed.

        Asked on the signal's DECISION bar (``plan.signal.bar_time``), not on
        ``ctx.now``: the backtester asks on the bar that produced the signal, and
        asking on the wall clock a few seconds later would put the two on
        different bars exactly at a lock's boundary.

        Three ways to block, each named:

        * ``instrument_lock_state_unavailable`` -- the signal's own document
          declares locks and this gateway has no lock source, or the source
          cannot read its ledger, or it has no policy for the strategy. A
          lock-declaring strategy never trades unlocked because a deployment
          forgot to wire the lock state;
        * ``instrument_lock:<code>`` -- a lock is in force.

        A gateway with no lock source passes a signal whose document declares
        no locks: there is nothing it could be locked by, because no lock-
        declaring strategy can trade through such a gateway to arm one.
        """
        signal = ctx.plan.signal
        declared = float(signal.features.get(LOCKS_DECLARED_FEATURE, 0.0)) > 0.0
        if self.locks is None:
            if declared:
                self._block("instrument_lock_state_unavailable:no_lock_source")
            return
        try:
            lock = self.locks.lock_for(
                signal.instrument,
                signal.strategy_id,
                signal.bar_time,
                declared=declared,
                timeframe=signal.timeframe,
            )
        except LockStateUnavailable as exc:
            self._block(f"instrument_lock_state_unavailable:{exc}")
            return
        if lock is not None:
            self._block(f"instrument_lock:{lock.code}")

    def _check_max_per_trade_risk(self, ctx: RiskContext, limits: LimitSet) -> None:
        pct = self._pct_of_equity(ctx.plan.risk_amount, ctx.equity)
        if pct > limits.max_per_trade_risk_pct:
            self._block(
                f"max_per_trade_risk:{pct:.3f}%>{limits.max_per_trade_risk_pct}%"
            )

    def _check_max_account_risk(self, ctx: RiskContext, limits: LimitSet) -> None:
        total = ctx.open_risk_amount + ctx.plan.risk_amount
        pct = self._pct_of_equity(total, ctx.equity)
        if pct > limits.max_account_risk_pct:
            self._block(f"max_account_risk:{pct:.3f}%>{limits.max_account_risk_pct}%")

    def _check_max_instrument_exposure(self, ctx: RiskContext, limits: LimitSet) -> None:
        held = abs(float(ctx.snapshot.instrument_exposure.get(ctx.plan.instrument, 0.0)))
        pct = self._pct_of_equity(held + ctx.plan_notional_account, ctx.equity)
        if pct > limits.max_instrument_exposure_pct:
            self._block(
                f"max_instrument_exposure:{ctx.plan.instrument}:{pct:.2f}%>"
                f"{limits.max_instrument_exposure_pct}%"
            )

    def _check_max_strategy_exposure(self, ctx: RiskContext, limits: LimitSet) -> None:
        sid = ctx.plan.signal.strategy_id
        held = abs(float(ctx.snapshot.strategy_exposure.get(sid, 0.0)))
        pct = self._pct_of_equity(held + ctx.plan_notional_account, ctx.equity)
        if pct > limits.max_strategy_exposure_pct:
            self._block(
                f"max_strategy_exposure:{sid}:{pct:.2f}%>{limits.max_strategy_exposure_pct}%"
            )

    def _check_max_currency_exposure(self, ctx: RiskContext, limits: LimitSet) -> None:
        instr = ctx.instrument
        add = ctx.plan_notional_account
        sign = ctx.plan.direction.sign
        # Long EURUSD is +EUR and -USD. Netting matters: a long EURUSD and a
        # long USDJPY do not add USD exposure, they offset it.
        for ccy, delta in ((instr.base, sign * add), (instr.quote, -sign * add)):
            current = float(ctx.snapshot.currency_exposure.get(ccy, 0.0))
            pct = self._pct_of_equity(abs(current + delta), ctx.equity)
            if pct > limits.max_currency_exposure_pct:
                self._block(
                    f"max_currency_exposure:{ccy}:{pct:.2f}%>"
                    f"{limits.max_currency_exposure_pct}%"
                )

    def _check_max_correlated_exposure(self, ctx: RiskContext, limits: LimitSet) -> None:
        total = ctx.correlated_exposure + ctx.plan_notional_account
        pct = self._pct_of_equity(total, ctx.equity)
        if pct > limits.max_correlated_exposure_pct:
            self._block(
                f"max_correlated_exposure:{pct:.2f}%>{limits.max_correlated_exposure_pct}%"
                f"@rho>={limits.correlation_threshold}"
            )

    def _check_daily_loss(self, ctx: RiskContext, limits: LimitSet) -> None:
        if ctx.daily_pnl >= 0:
            return
        pct = self._pct_of_equity(-ctx.daily_pnl, ctx.equity)
        if pct >= limits.max_daily_loss_pct:
            self._block(f"daily_loss:{pct:.2f}%>={limits.max_daily_loss_pct}%")

    def _check_weekly_loss(self, ctx: RiskContext, limits: LimitSet) -> None:
        if ctx.weekly_pnl >= 0:
            return
        pct = self._pct_of_equity(-ctx.weekly_pnl, ctx.equity)
        if pct >= limits.max_weekly_loss_pct:
            self._block(f"weekly_loss:{pct:.2f}%>={limits.max_weekly_loss_pct}%")

    def _check_total_drawdown(self, ctx: RiskContext, limits: LimitSet) -> None:
        dd = ctx.snapshot.account.drawdown_pct
        if dd >= limits.max_total_drawdown_pct:
            self._block(f"total_drawdown:{dd:.2f}%>={limits.max_total_drawdown_pct}%")

    def _check_margin_utilisation(self, ctx: RiskContext, limits: LimitSet) -> None:
        util = ctx.snapshot.margin_utilisation * 100.0
        if util >= limits.max_margin_utilisation_pct:
            self._block(
                f"margin_utilisation:{util:.2f}%>={limits.max_margin_utilisation_pct}%"
            )
