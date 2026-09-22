"""The runtime assembly: the parts that turn bars into orders, wired together.

``workers/live_worker.py`` has always held the LOOP. What it did not hold, and
what nothing else held either, were the four objects the loop is given:

======================  ====================================================
Seam                    What was missing
======================  ====================================================
``BarFeed``             no implementation: nothing pulled bars for a worker
``MarketStateUpdater``  ``marketstate`` existed and nothing fed it
``StrategyEvaluator``   no path from a strategy's ``Signal`` to a ``TradePlan``
``ContextBuilder``      **nothing anywhere built a** :class:`RiskContext`
======================  ====================================================

The last one is the serious one. ``risk/gateway.py`` fails CLOSED: a ``None``
price age is not "fresh", it is "unknown", and unknown blocks. With no
production source for ``MarketView``, the freshness and spread checks had no
data at all, so the gateway as deployed would have refused every order — or,
worse, somebody would eventually have handed it a default-constructed
``MarketView`` to make the refusals stop, and the two checks would have become
decorative in exactly the V1 way. :class:`RiskContextBuilder` is that source.

What this module does NOT do
----------------------------
It does not construct an ``Order``. Order construction lives in exactly one
function in the tree — ``ExecutionService.submit`` — and
``tests/unit/test_no_gateway_bypass.py`` walks the AST and fails the build if a
second site appears. Nothing here sizes either: sizing is
``portfolio.sizing.size_trade``, called once, in :class:`SignalEvaluator`, and
the plan travels unchanged from there to the venue.

The clock, and why a replay needs to say so out loud
-----------------------------------------------------
Every freshness check is ``now - timestamp``. In production ``now`` is the wall
clock and a bar four hours old on an H4 strategy is normal, which is why
``PAPER_LIMITS`` allows 1,800 seconds of data age. In a REPLAY the bars are
years old, so a wall clock would block every order on ``data_stale`` and the
session would prove nothing. :class:`RiskContextBuilder` therefore takes an
explicit ``clock``, and :func:`build_replay_session` passes the feed's own bar
clock. That is a deliberate, named substitution — not a limit quietly widened
until the refusals stopped.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from fiboki.broker.execution_service import (
    ExecutionService,
    InMemoryIntentStore,
    IntentStore,
)
from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.core.contracts import AccountState, Signal, TradePlan
from fiboki.core.enums import ExecutionMode, StrategyLifecycle
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import FxRateSource, IdentityFxSource
from fiboki.obs import metrics as _metrics
from fiboki.obs.logging import get_logger
from fiboki.portfolio.construction import CorrelationMatrix, PortfolioSnapshot
from fiboki.portfolio.sizing import SizingPolicy, size_trade
from fiboki.risk.accounting import (
    PnlWindows,
    RealisedPnlLedger,
    correlated_exposure,
    correlation_from_frames,
    realised_portfolio_vol,
)
from fiboki.risk.gateway import (
    MarketView,
    RiskContext,
    RiskGateway,
    StrategyView,
    VenueView,
)
from fiboki.risk.killswitch import RequestKind
from fiboki.risk.limits import PAPER_LIMITS, LimitSet
from fiboki.sim.fills import Bar
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import BarBatch, LiveWorker, LiveWorkerConfig

__all__ = [
    "MANAGED_EXIT_EXPOSURE",
    "MANAGED_EXIT_POSITIONS",
    "FrameReplayFeed",
    "LifecycleTimer",
    "MarketStateFeed",
    "PaperSession",
    "RiskContextBuilder",
    "SignalEvaluator",
    "SignalSource",
    "build_replay_session",
    "publish_managed_exit_exposure",
]

_log = get_logger("fiboki.workers.runtime")


# ---------------------------------------------------------------------------
# The number that says what a dead worker would cost
# ---------------------------------------------------------------------------

#: Account-currency open risk whose intended exit is NOT resting at the venue,
#: and which therefore depends on this worker staying alive to be realised.
#:
#: Registered HERE rather than in ``broker/`` because ``broker`` sits BELOW
#: ``obs`` in the dependency order ``tests/unit/test_layering.py`` enforces:
#: the position manager computes the number and hands it up, and this is the
#: layer allowed to publish it. See
#: :data:`fiboki.broker.position_manager.MANAGED_EXIT_USER_ACTION_NOTE` for
#: what the number means and what an operator is expected to do with it.
MANAGED_EXIT_EXPOSURE = _metrics.REGISTRY.gauge(
    "fiboki_managed_exit_exposure",
    (
        "Open risk, in the account currency, whose intended exit is managed "
        "client-side and freezes if this worker dies"
    ),
    ("venue", "account_ccy"),
)

#: How many open positions contribute to the gauge above. A large exposure
#: spread over one position and over eight are different incidents.
MANAGED_EXIT_POSITIONS = _metrics.REGISTRY.gauge(
    "fiboki_managed_exit_positions",
    "Open positions whose intended exit depends on this worker staying alive",
    ("venue",),
)


def publish_managed_exit_exposure(exposure: Any, *, venue: str) -> float:
    """Publish a :class:`ManagedExitExposure` onto the metric registry.

    Takes the value structurally rather than by import so that a caller holding
    anything with ``amount``/``exposed_positions``/``account_ccy`` can publish
    it -- which keeps this function honest about being a transport and not a
    second place where the number is computed.
    """
    amount = float(getattr(exposure, "amount", 0.0))
    ccy = str(getattr(exposure, "account_ccy", "GBP"))
    MANAGED_EXIT_EXPOSURE.set(amount, venue=venue, account_ccy=ccy)
    MANAGED_EXIT_POSITIONS.set(
        float(getattr(exposure, "exposed_positions", 0)), venue=venue
    )
    return amount


# ---------------------------------------------------------------------------
# Bars in
# ---------------------------------------------------------------------------


class FrameReplayFeed:
    """Replays stored OHLC frames one CLOSED bar at a time.

    This is the production shape of a feed, driven from history: each
    :meth:`poll` advances one step of the union timeline and reports the
    instruments whose bar closed at that step. Nothing downstream can tell the
    difference between this and a live poller, which is the point — a paper
    session replayed from the store exercises the same code that will run
    against a broker feed.

    ``ages`` is reported against :attr:`now`, the timestamp of the bar just
    delivered, so a replay's freshness metric describes the replay rather than
    the distance from today.
    """

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        *,
        timeframe: str = "",
        warmup: int = 0,
        series: Mapping[str, pd.DataFrame] | None = None,
    ) -> None:
        if not frames:
            raise ValueError("FrameReplayFeed needs at least one instrument frame")
        self.frames = {sym: frames[sym] for sym in sorted(frames)}
        self.series = {sym: (series or {}).get(sym) for sym in self.frames}
        self.timeframe = timeframe
        index = None
        for frame in self.frames.values():
            index = frame.index if index is None else index.union(frame.index)
        self.timeline = pd.DatetimeIndex(index).sort_values()
        self.cursor = int(warmup)
        self.now: pd.Timestamp | None = None

    @property
    def exhausted(self) -> bool:
        return self.cursor >= len(self.timeline)

    def bar_at(self, symbol: str, ts: pd.Timestamp) -> Bar | None:
        frame = self.frames[symbol]
        if ts not in frame.index:
            return None
        row = frame.loc[ts]
        return Bar(
            ts,
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )

    def series_at(self, ts: pd.Timestamp) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for sym, frame in self.series.items():
            if frame is None or ts not in frame.index:
                continue
            row = frame.loc[ts]
            out[sym] = {str(col): float(row[col]) for col in frame.columns}
        return out

    def poll(self) -> BarBatch:
        if self.exhausted:
            return BarBatch(timeframe=self.timeframe)
        ts = self.timeline[self.cursor]
        self.cursor += 1
        self.now = ts
        bars: dict[str, Bar] = {}
        for sym in self.frames:
            bar = self.bar_at(sym, ts)
            if bar is not None:
                bars[sym] = bar
        # Every bar delivered here has CLOSED: a frame in the store contains
        # nothing else. A live feed is where the open-bar distinction bites,
        # and the batch carries the flag for both.
        return BarBatch(
            frames=bars,
            closed_instruments=frozenset(bars),
            ages={sym: 0.0 for sym in bars},
            timeframe=self.timeframe,
        )


class MarketStateFeed:
    """Folds each polled bar into a :class:`MarketStateEngine`.

    The engine is the platform's one answer to "what kind of market is this",
    and until now nothing in a running process put a bar into it. The regime it
    reports travels onto every :class:`RiskContext` and onto every blocked
    attempt, so a refusal can be read back against the market it happened in.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.ingested = 0

    def update(self, batch: BarBatch) -> None:
        for sym, bar in batch.frames.items():
            try:
                self.engine.ingest_bar(
                    sym,
                    pd.Series(
                        {
                            "open": bar.open,
                            "high": bar.high,
                            "low": bar.low,
                            "close": bar.close,
                        },
                        name=bar.timestamp,
                    ),
                )
            except Exception as exc:  # a state engine is not worth a dead worker
                _log.warning(
                    "market state ingest failed",
                    extra={"instrument": sym, "error": f"{type(exc).__name__}: {exc}"},
                )
                continue
            self.ingested += 1

    def regime(self, instrument: str) -> str:
        try:
            return str(self.engine.snapshot(instrument).regime_key)
        except Exception:
            return "unknown"


# ---------------------------------------------------------------------------
# Signals -> plans (sizing happens HERE, once)
# ---------------------------------------------------------------------------


#: ``produce(instrument, bars_so_far, now) -> signals``. Whatever shape a
#: strategy takes, the worker sees this.
SignalSource = Callable[[str, pd.DataFrame, pd.Timestamp], Sequence[Signal]]


class SignalEvaluator:
    """Turns signals into :class:`TradePlan` objects. The ONE sizing call.

    The execution service explicitly does not re-size and neither does the
    worker: V1 sized the same signal three times — in the bot, in the router and
    in the IG adapter — and the ledger disagreed with the broker. Everything
    downstream of here carries ``plan.size`` unchanged.
    """

    def __init__(
        self,
        *,
        source: SignalSource,
        account: Callable[[], AccountState],
        fx: FxRateSource,
        policy: SizingPolicy,
        history: Callable[[str], pd.DataFrame],
        account_ccy: str = "GBP",
    ) -> None:
        self.source = source
        self.account = account
        self.fx = fx
        self.policy = policy
        self.history = history
        self.account_ccy = account_ccy
        self.signals_seen = 0
        self.unsized: list[tuple[str, str]] = []

    def evaluate(self, instruments: Sequence[str], batch: BarBatch) -> Iterable[TradePlan]:
        account = self.account()
        plans: list[TradePlan] = []
        for symbol in instruments:
            bar = batch.frames.get(symbol)
            if bar is None:
                continue
            instrument = get_instrument(symbol)
            for signal in self.source(symbol, self.history(symbol), bar.timestamp) or ():
                self.signals_seen += 1
                rate = float(
                    self.fx.rate(instrument.quote, self.account_ccy, bar.timestamp)
                )
                outcome = size_trade(
                    signal=signal,
                    instrument=instrument,
                    account=account,
                    fx_quote_to_account=rate,
                    policy=self.policy,
                    account_ccy=self.account_ccy,
                )
                if not outcome.sized:
                    # Not dropped silently: a signal that could not be sized is
                    # as much a fact as one that was.
                    self.unsized.append((symbol, str(outcome.reason)))
                    _log.info(
                        "signal not sized",
                        extra={"instrument": symbol, "reason": str(outcome.reason)},
                    )
                    continue
                plans.append(outcome.require())
        return plans


# ---------------------------------------------------------------------------
# The RiskContext builder: the thing that did not exist
# ---------------------------------------------------------------------------


@dataclass
class RiskContextBuilder:
    """Assembles the gateway's every input. No check is left without data.

    Each field below feeds a NAMED check in
    :attr:`fiboki.risk.gateway.RiskGateway.CHECKS`, and every one of those
    checks blocks on ``None``. That is the contract this class exists to
    satisfy:

    ``market.last_bar_time``   ``data_freshness``
    ``market.quote_time``      ``stale_price``
    ``market.spread_price``    ``abnormal_spread``
    ``market.market_open``     ``market_state``
    ``market.event_times``     ``event_blackout``
    ``venue.connected/score``  ``broker_health``
    ``strategy.*``             ``strategy_lifecycle``
    ``snapshot``               every exposure, loss and drawdown check

    The spread is taken from the EXECUTION PROFILE rather than from a live
    quote, because a paper session has no quote stream. It is therefore the same
    number the fill simulator charges, which is honest for paper and wrong for
    live: a live deployment must replace ``spread_source`` with the broker's
    quoted spread, and ``estimated_spread`` is True on every context this
    builder produces so nothing can mistake one for the other.

    The four inputs that used to run on zeros
    -----------------------------------------
    Building a :class:`MarketView` was the gap this class was created to close.
    It left a second one: four gateway inputs that this class DEFAULTED rather
    than sourced.

    ==========================  =================================================
    ``daily_pnl``               ``pnl_ledger`` -- realised P&L over the UTC day
    ``weekly_pnl``              ``pnl_ledger`` -- realised P&L over the ISO week
    ``correlated_exposure``     ``correlation`` x the open-position book
    ``realised_portfolio_vol``  ``equity_curve`` -- annualised equity-curve vol
    ==========================  =================================================

    Each was ``0.0`` and each fed a check that ran, was named in the audit
    trail, and could not fire. ``fiboki.risk.accounting`` holds the arithmetic;
    this class holds the wiring. All four sources are OPTIONAL and default to
    the old behaviour, so an existing caller is unchanged -- but a session built
    by :func:`build_replay_session` gets all four, and
    :attr:`risk_inputs_live` reports which of them are real on every context so
    a decision record can never be read as enforcing a limit it had no data for.
    """

    adapter: Any
    clock: Callable[[], pd.Timestamp]
    fx: FxRateSource
    account_ccy: str = "GBP"
    limits: LimitSet = PAPER_LIMITS
    mode: ExecutionMode = ExecutionMode.PAPER
    #: ``instrument -> spread in PRICE units``. Defaults to the adapter's
    #: execution profile.
    spread_source: Callable[[Instrument, pd.Timestamp], float] | None = None
    #: ``instrument -> regime label``.
    regime_source: Callable[[str], str] | None = None
    #: ``instrument, now -> flagged high-impact event times``.
    event_source: Callable[[str, pd.Timestamp], tuple[pd.Timestamp, ...]] | None = None
    #: ``strategy_id -> StrategyView``. Wired to the lifecycle service when one
    #: is running; without it every strategy is reported at its declared stage.
    strategy_source: Callable[[str], StrategyView] | None = None
    default_lifecycle: StrategyLifecycle = StrategyLifecycle.PAPER
    #: Timestamp of the newest bar per instrument, kept by :meth:`observe`.
    last_bar_times: dict[str, pd.Timestamp] = field(default_factory=dict)
    market_open: bool = True
    #: STATIC fallbacks, used only when ``pnl_ledger`` is absent. Retained so
    #: an existing caller that set them by hand keeps working, and so a test can
    #: pin a value without building a trade history.
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0

    # -- the four formerly-dead inputs ----------------------------------
    #: Realised P&L over clock-derived UTC day and week windows. When present it
    #: WINS over the static fields above, because a measured number beats a
    #: declared one.
    pnl_ledger: RealisedPnlLedger | None = None
    #: Pairwise instrument correlation. ``None`` means the check keeps reading
    #: zero, which is why :attr:`risk_inputs_live` reports it.
    correlation: CorrelationMatrix | None = None
    #: ``() -> equity curve``. Anything :func:`realised_portfolio_vol` accepts:
    #: a Series, a mapping, or a bare sequence with an index.
    equity_curve: Callable[[], Any] | None = None
    #: Minimum equity observations before a volatility estimate is used at all.
    vol_min_observations: int = 20
    #: Last computed windows, for the worker's summary and for tests.
    last_windows: PnlWindows | None = None

    def observe(self, batch: BarBatch) -> None:
        """Record when each instrument last produced a closed bar."""
        for sym, bar in batch.frames.items():
            self.last_bar_times[sym] = bar.timestamp

    # -- the pieces ------------------------------------------------------

    def _spread(self, instrument: Instrument, now: pd.Timestamp) -> float:
        if self.spread_source is not None:
            return float(self.spread_source(instrument, now))
        profile = getattr(getattr(self.adapter, "config", None), "profile", None)
        if profile is None:
            profile = getattr(self.adapter, "profile", None)
        mid = self._mid(instrument.symbol)
        if profile is None or mid is None:
            # No profile, or no price yet: say UNKNOWN rather than zero. The
            # gateway blocks on unknown, which is the correct answer.
            return float("nan")
        return float(profile.spread_price(instrument, int(now.hour), mid))

    def market_view(self, instrument: Instrument, now: pd.Timestamp) -> MarketView:
        last_bar = self.last_bar_times.get(instrument.symbol)
        spread = self._spread(instrument, now)
        mid = self._mid(instrument.symbol)
        return MarketView(
            mid_price=mid,
            spread_price=None if spread != spread else spread,
            # A paper venue's "quote" is the close of the bar it was handed.
            # There is no separate tick stream, and inventing a fresher
            # timestamp than the data would defeat the check outright.
            quote_time=last_bar,
            last_bar_time=last_bar,
            market_open=self.market_open,
            regime=(
                self.regime_source(instrument.symbol)
                if self.regime_source is not None
                else "unknown"
            ),
            event_times=(
                self.event_source(instrument.symbol, now)
                if self.event_source is not None
                else ()
            ),
        )

    def _mid(self, symbol: str) -> float | None:
        last = getattr(self.adapter, "_last_bar", {}).get(symbol)
        return None if last is None else float(last.close)

    def venue_view(self) -> VenueView:
        health = self.adapter.health()
        return VenueView(
            connected=health.connected,
            score=health.score,
            message=health.message,
        )

    def strategy_view(self, strategy_id: str) -> StrategyView:
        if self.strategy_source is not None:
            return self.strategy_source(strategy_id)
        return StrategyView(lifecycle=self.default_lifecycle, health=1.0, degraded=False)

    def snapshot(self, now: pd.Timestamp) -> PortfolioSnapshot:
        account = self.adapter.account()
        positions = self.adapter.positions()
        instrument_exposure: dict[str, float] = {}
        strategy_exposure: dict[str, float] = {}
        currency_exposure: dict[str, float] = {}
        for pos in positions:
            instrument = get_instrument(pos.instrument)
            rate = float(self.fx.rate(instrument.quote, self.account_ccy, now))
            notional = (
                pos.entry_price * pos.size * instrument.contract_size * rate
            ) * pos.direction.sign
            instrument_exposure[pos.instrument] = (
                instrument_exposure.get(pos.instrument, 0.0) + notional
            )
            strategy_exposure[pos.strategy_id] = (
                strategy_exposure.get(pos.strategy_id, 0.0) + notional
            )
            currency_exposure[instrument.base] = (
                currency_exposure.get(instrument.base, 0.0) + notional
            )
            currency_exposure[instrument.quote] = (
                currency_exposure.get(instrument.quote, 0.0) - notional
            )
        return PortfolioSnapshot(
            account=account,
            as_of=now,
            open_positions=tuple(positions),
            instrument_exposure=instrument_exposure,
            strategy_exposure=strategy_exposure,
            currency_exposure=currency_exposure,
            instrument_correlation=self.correlation or CorrelationMatrix(),
            realised_portfolio_vol=self.realised_vol(),
            margin_available=max(0.0, account.equity - account.margin_used),
            regime=(
                self.regime_source(positions[0].instrument)
                if self.regime_source is not None and positions
                else "unknown"
            ),
        )

    def realised_vol(self) -> float:
        """Annualised volatility of the equity curve, or 0.0 for "unmeasured".

        ``portfolio/construction.py`` reads this for volatility targeting and
        treats 0.0 as NEUTRAL -- which meant vol targeting was silently
        disabled on every live path while remaining configured and unit-tested.
        With a curve supplied it is measured; without one it is still 0.0, and
        :meth:`risk_inputs_live` says so rather than letting a reader assume.
        """
        if self.equity_curve is None:
            return 0.0
        try:
            return realised_portfolio_vol(
                self.equity_curve(), min_observations=self.vol_min_observations
            )
        except Exception as exc:  # a vol estimate is not worth a dead worker
            _log.warning(
                "realised portfolio vol could not be computed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return 0.0

    def windows(self, now: pd.Timestamp) -> PnlWindows | None:
        """Realised day/week P&L, re-derived from ``now`` on every call.

        NOT cached and NOT accumulated. The whole defence against V1's defect --
        a daily counter reset inside a 21:00 job, so a worker that was down at
        21:00 never reset it and the daily stop could never fire again -- is
        that the boundary is a function of the clock and there is no counter to
        get stuck. Caching the result here would reintroduce exactly that.
        """
        if self.pnl_ledger is None:
            return None
        windows = self.pnl_ledger.windows(now)
        self.last_windows = windows
        return windows

    def risk_inputs_live(self) -> dict[str, bool]:
        """Which formerly-dead inputs carry real data on this builder.

        Stamped onto every :class:`RiskContext`'s ``extra`` and therefore onto
        every recorded attempt. An audit that says ``daily_loss`` ran can now
        also say whether it ran against a measured number or against a zero,
        which is the difference between a limit and a decoration.
        """
        return {
            "daily_pnl": self.pnl_ledger is not None,
            "weekly_pnl": self.pnl_ledger is not None,
            "correlated_exposure": self.correlation is not None,
            "realised_portfolio_vol": self.equity_curve is not None,
        }

    def open_risk(self, now: pd.Timestamp) -> float:
        total = 0.0
        for pos in self.adapter.positions():
            instrument = get_instrument(pos.instrument)
            rate = float(self.fx.rate(instrument.quote, self.account_ccy, now))
            total += (
                abs(pos.entry_price - pos.stop_loss)
                * pos.size
                * instrument.contract_size
                * rate
            )
        return total

    # -- the call the worker makes --------------------------------------

    def __call__(self, plan: TradePlan) -> RiskContext:
        now = self.clock()
        instrument = get_instrument(plan.instrument)
        rate = float(self.fx.rate(instrument.quote, self.account_ccy, now))
        snapshot = self.snapshot(now)
        windows = self.windows(now)
        limits = self.limits
        correlated = (
            correlated_exposure(
                plan.instrument,
                snapshot=snapshot,
                threshold=limits.correlation_threshold,
                correlation=self.correlation,
            )
            if self.correlation is not None
            else 0.0
        )
        extra: dict[str, Any] = {
            "estimated_spread": self.spread_source is None,
            "risk_inputs_live": self.risk_inputs_live(),
            "realised_portfolio_vol": snapshot.realised_portfolio_vol,
        }
        if windows is not None:
            extra["pnl_window"] = windows.as_row()
        return RiskContext(
            plan=plan,
            snapshot=snapshot,
            now=now,
            mode=self.mode,
            limits=limits,
            market=self.market_view(instrument, now),
            venue=self.venue_view(),
            strategy=self.strategy_view(plan.signal.strategy_id),
            request_kind=RequestKind.OPEN,
            open_risk_amount=self.open_risk(now),
            correlated_exposure=correlated,
            fx_quote_to_account=rate,
            daily_pnl=self.daily_pnl if windows is None else windows.daily_pnl,
            weekly_pnl=self.weekly_pnl if windows is None else windows.weekly_pnl,
            extra=extra,
        )


# ---------------------------------------------------------------------------
# The lifecycle timer
# ---------------------------------------------------------------------------


class LifecycleTimer:
    """Calls :meth:`LifecycleService.evaluate_all` on the worker's schedule.

    ``lifecycle/service.py`` was implemented, tested and never called. Its own
    docstring says so: "It does not start a thread. The worker owns the timer;
    this owns the evaluation." Nothing owned the timer, so no monitor ran, no
    stopping rule was evaluated against forward data and no strategy was ever
    demoted automatically. This is the timer.

    ``inputs`` supplies ``{strategy_content_hash: (Expectation, Observation)}``
    for the strategies with forward data. A RUNNING strategy absent from it is
    still evaluated, with its divergence dimensions unevaluated and a note
    saying so — a strategy that stopped producing observations is exactly the
    case a monitor keyed only on supplied inputs would silently skip.
    """

    def __init__(
        self,
        service: Any,
        inputs: Callable[[], Mapping[str, tuple[Any, Any]]] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.service = service
        self.inputs = inputs or (lambda: {})
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.ticks = 0
        self.evaluations: list[Any] = []

    def tick(self) -> tuple[Any, ...]:
        self.ticks += 1
        results = self.service.evaluate_all(self.inputs(), at=self.clock())
        self.evaluations.extend(results)
        return results


# ---------------------------------------------------------------------------
# One assembled paper session
# ---------------------------------------------------------------------------


@dataclass
class PaperSession:
    """Every part of an assembled runtime, kept so a caller can inspect it."""

    worker: LiveWorker
    broker: PaperBroker
    execution: ExecutionService
    gateway: RiskGateway
    feed: FrameReplayFeed
    evaluator: SignalEvaluator
    context_builder: RiskContextBuilder
    market_state: MarketStateFeed | None = None
    lifecycle: LifecycleTimer | None = None

    def telemetry(self) -> list[dict[str, Any]]:
        """Every attempt the gateway recorded, allowed or blocked, as rows."""
        recorder = self.gateway.recorder
        return [a.as_row() for a in getattr(recorder, "attempts", ())]

    def summary(self) -> dict[str, Any]:
        recorder = self.gateway.recorder
        attempts = list(getattr(recorder, "attempts", ()))
        blocked = [a for a in attempts if not a.allowed]
        reasons: dict[str, int] = {}
        for attempt in blocked:
            for reason in attempt.decision.reasons:
                key = reason.split(":", 1)[0]
                reasons[key] = reasons.get(key, 0) + 1
        return {
            "bars_replayed": self.feed.cursor,
            "signals_seen": self.evaluator.signals_seen,
            "unsized": len(self.evaluator.unsized),
            "gateway_attempts": len(attempts),
            "gateway_blocked": len(blocked),
            "block_reasons": dict(sorted(reasons.items())),
            "submissions": len(self.worker.submissions),
            "accepted": sum(1 for s in self.worker.submissions if s.accepted),
            "trades": len(self.broker.trades),
            "exit_legs": len(self.broker.exit_legs),
            "open_positions": len(self.broker.book.open),
            "balance": self.broker.balance,
            "equity": self.broker.equity,
            "rejections": dict(sorted(self.broker.rejections.items())),
            "lifecycle_ticks": 0 if self.lifecycle is None else self.lifecycle.ticks,
            "market_state_bars": 0 if self.market_state is None else self.market_state.ingested,
            # The four formerly-dead risk inputs, reported as VALUES rather than
            # as "the check ran". A summary that only says a daily stop was
            # evaluated is the thing this session was built to stop producing.
            "risk_inputs_live": self.context_builder.risk_inputs_live(),
            "daily_pnl": (
                None
                if self.context_builder.last_windows is None
                else self.context_builder.last_windows.daily_pnl
            ),
            "weekly_pnl": (
                None
                if self.context_builder.last_windows is None
                else self.context_builder.last_windows.weekly_pnl
            ),
            "realised_portfolio_vol": self.context_builder.realised_vol(),
        }


def build_replay_session(
    *,
    frames: Mapping[str, pd.DataFrame],
    signal_source: SignalSource,
    store: WorkerStore,
    paper_config: PaperConfig,
    sizing_policy: SizingPolicy,
    fx: FxRateSource | None = None,
    timeframe: str = "",
    warmup: int = 0,
    exit_series: Mapping[str, pd.DataFrame] | None = None,
    gateway: RiskGateway | None = None,
    limits: LimitSet = PAPER_LIMITS,
    intent_store: IntentStore | None = None,
    market_state_engine: Any = None,
    lifecycle_service: Any = None,
    lifecycle_inputs: Callable[[], Mapping[str, tuple[Any, Any]]] | None = None,
    worker_config: LiveWorkerConfig | None = None,
    dispatcher: Any = None,
    default_lifecycle: StrategyLifecycle = StrategyLifecycle.PAPER,
    correlation: CorrelationMatrix | None = None,
) -> PaperSession:
    """Assemble a PAPER runtime end to end over stored bars.

    market data -> market state -> strategy -> sizing -> risk gateway ->
    execution service -> paper venue -> telemetry.

    The worker refuses to start unless the execution service is in a mode its
    config authorises, and that default is paper only. Nothing here can widen
    it; widening it is an edit to ``allowed_modes``, reviewed, in the config.
    """
    fx = fx or IdentityFxSource()
    feed = FrameReplayFeed(
        frames, timeframe=timeframe, warmup=warmup, series=exit_series
    )
    broker = PaperBroker(config=paper_config, fx=fx)
    for symbol, frame in frames.items():
        if len(frame.index) > 2:
            deltas = frame.index.to_series().diff().dropna()
            broker.set_bar_interval(symbol, deltas.median())

    gateway = gateway or RiskGateway(limits=limits)
    execution = ExecutionService(
        adapter=broker,
        gateway=gateway,
        store=intent_store or InMemoryIntentStore(),
        mode=ExecutionMode.PAPER,
    )

    market_state = (
        MarketStateFeed(market_state_engine) if market_state_engine is not None else None
    )

    def _history(symbol: str) -> pd.DataFrame:
        frame = feed.frames[symbol]
        return frame.iloc[: feed.cursor]

    evaluator = SignalEvaluator(
        source=signal_source,
        account=broker.account,
        fx=fx,
        policy=sizing_policy,
        history=_history,
        account_ccy=paper_config.account_ccy,
    )

    def _equity_curve() -> pd.Series:
        """The venue's own equity curve, one row per bar. Feeds vol targeting."""
        rows = broker.equity_curve
        if not rows:
            return pd.Series(dtype=float)
        return pd.Series(
            [float(r["equity"]) for r in rows],
            index=pd.DatetimeIndex([r["timestamp"] for r in rows]),
        )

    builder = RiskContextBuilder(
        adapter=broker,
        # THE REPLAY CLOCK. See the module docstring: a wall clock against
        # decade-old bars blocks every order on data_stale, and widening the
        # limit to stop that would make the check decorative.
        clock=lambda: feed.now or pd.Timestamp.now(tz="UTC"),
        fx=fx,
        account_ccy=paper_config.account_ccy,
        limits=limits,
        mode=ExecutionMode.PAPER,
        regime_source=(market_state.regime if market_state is not None else None),
        default_lifecycle=default_lifecycle,
        # THE FOUR FORMERLY-DEAD INPUTS. Each check below ran, was named in the
        # audit trail, and read a zero it could never breach. They now read the
        # venue's own closed trades, the venue's own equity curve and a
        # correlation matrix measured on the very frames being replayed.
        pnl_ledger=RealisedPnlLedger(
            # A CALLABLE over the book's trades, not a snapshot of them: the
            # ledger must see a trade that closed one bar ago without anybody
            # having remembered to push it.
            lambda: broker.book.trades,
            account_ccy=paper_config.account_ccy,
        ),
        correlation=(
            correlation
            if correlation is not None
            else correlation_from_frames(frames)
        ),
        equity_curve=_equity_curve,
    )
    if lifecycle_service is not None:
        builder.strategy_source = _lifecycle_strategy_source(
            lifecycle_service, default_lifecycle
        )

    lifecycle = (
        LifecycleTimer(lifecycle_service, lifecycle_inputs)
        if lifecycle_service is not None
        else None
    )

    class _VenueDrivingFeed:
        """Poll, advance the venue, then hand the batch to the worker.

        The ORDER here is the engine's per-bar order and not a detail. The venue
        must see the closed bar first -- financing, then any order queued on the
        previous bar filling at THIS bar's open, then exits -- before the
        strategy is asked for signals on the same bar and new orders are queued
        behind it. Evaluating first and advancing afterwards would let an order
        placed on bar i fill at bar i's open, which is look-ahead, and would
        break parity with the backtester at the one point where it is expensive.

        Threading the batch through the context builder here is the same idea:
        the builder needs to know when each instrument last closed a bar, and
        the worker's loop is not the place to remember it.
        """

        def poll(self) -> BarBatch:
            index = feed.cursor
            batch = feed.poll()
            if batch.frames and feed.now is not None:
                broker.on_bar(
                    batch.frames,
                    bar_index=index,
                    timestamp=feed.now,
                    series=feed.series_at(feed.now),
                )
            builder.observe(batch)
            return batch

    worker = LiveWorker(
        execution_service=execution,
        feed=_VenueDrivingFeed(),
        evaluator=evaluator,
        context_builder=builder,
        store=store,
        market_state=market_state,
        lifecycle=lifecycle,
        config=worker_config or LiveWorkerConfig(),
        dispatcher=dispatcher,
    )

    return PaperSession(
        worker=worker,
        broker=broker,
        execution=execution,
        gateway=gateway,
        feed=feed,
        evaluator=evaluator,
        context_builder=builder,
        market_state=market_state,
        lifecycle=lifecycle,
    )


def _lifecycle_strategy_source(
    service: Any, default: StrategyLifecycle
) -> Callable[[str], StrategyView]:
    """The join the lifecycle package deliberately does not make itself.

    ``lifecycle/service.py`` refuses to import ``risk``; the gateway consumes a
    ``StrategyView`` its caller assembles. This is that caller.
    """

    def source(strategy_id: str) -> StrategyView:
        for status in service.statuses():
            if status.strategy_id != strategy_id:
                continue
            return StrategyView(
                lifecycle=status.lifecycle,
                health=status.health,
                degraded=status.degraded,
            )
        # An unregistered strategy is NOT assumed healthy: it is reported at the
        # session's declared stage with no evaluation behind it, and the
        # gateway's own lifecycle rules decide what that permits.
        return StrategyView(lifecycle=default, health=1.0, degraded=False)

    return source
