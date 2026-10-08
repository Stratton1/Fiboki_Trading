"""The runtime assembly: the parts that turn bars into orders, wired together.

``workers/live_worker.py`` has always held the LOOP. What it did not hold, and
what nothing else held either, were the four objects the loop is given:

======================  ====================================================
Seam                    What was missing
======================  ====================================================
``BarFeed``             :class:`FrameReplayFeed` (history) and, for a venue,
                        ``workers/feeds.OandaPollingBarFeed``
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

The economic calendar, and what the gateway sees of it
------------------------------------------------------
:func:`build_replay_session` wires the committed official calendar
(:func:`fiboki.marketstate.calendar.load_official_calendar`) into
:attr:`RiskContextBuilder.event_source` by default, so the gateway's
``event_blackout`` check has scheduled releases to compare against, and hands
the same calendar to the paper venue as its blackout source (the one the
engine is given), so a document exit policy that declares an event blackout
consults the same events in paper as in a backtest. A replay the calendar
cannot vouch for (span or currencies) is REFUSED with ``CalendarError`` unless
``allow_empty_calendar=True``; with that flag the calendar is still applied
wherever it has events. The only way to run with no event source at all is to
pass an explicitly empty calendar with ``allow_empty_calendar=True``.
:meth:`PaperSession.summary` reports which of these happened.

The limit set in force is :func:`fiboki.risk.limits.default_limit_set`
(``limits_v2_paper`` for a replay). Under a v2 set the gateway anchors the
blackout at the signal bar's CLOSE and reaches at least one bar ahead, and
:func:`event_source_horizon_minutes` widens the event source to match, so a
release inside a long bar is seen. (Under the v1 sets the window was measured
from the bar's OPEN stamp and an H4 release more than 15 minutes from it was
missed; a caller that passes a v1 set explicitly still gets that behaviour.)
"""
from __future__ import annotations

import contextlib
import math
import os
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.broker.execution_service import (
    ExecutionService,
    InMemoryIntentStore,
    IntentStore,
)
from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.core.contracts import (
    AccountState,
    ConvictionReading,
    Signal,
    TradePlan,
    VetoAssessment,
)
from fiboki.core.enums import ExecutionMode, StrategyLifecycle
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import FxRateSource, IdentityFxSource
from fiboki.core.paths import FibokiPaths, resolve_paths
from fiboki.core.tier import TierReading
from fiboki.data.news.store import to_utc_text
from fiboki.marketstate.calendar import (
    EconomicCalendar,
    ImpactLevel,
    instrument_currencies,
    load_official_calendar,
)
from fiboki.obs import metrics as _metrics
from fiboki.obs.logging import get_logger
from fiboki.portfolio.construction import (
    AllocationResult,
    CandidateSignal,
    ConstructionConfig,
    CorrelationMatrix,
    PortfolioConstructor,
    PortfolioSnapshot,
    StrategyTier,
)
from fiboki.portfolio.sizing import SizingPolicy, size_trade
from fiboki.risk.accounting import (
    PnlWindows,
    RealisedPnlLedger,
    correlated_exposure,
    correlation_from_frames,
    realised_portfolio_vol,
)
from fiboki.risk.gateway import (
    EventVetoProvider,
    MarketView,
    RiskContext,
    RiskGateway,
    StrategyView,
    VenueView,
)
from fiboki.risk.killswitch import KillSwitch, RequestKind
from fiboki.risk.limits import LimitSet, default_limit_set
from fiboki.sim.fills import Bar
from fiboki.validation.engine_evaluator import blackout_fingerprint
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import BarBatch, LiveWorker, LiveWorkerConfig

__all__ = [
    "MANAGED_EXIT_EXPOSURE",
    "MANAGED_EXIT_POSITIONS",
    "CalendarWiring",
    "ConvictionAdapter",
    "FrameReplayFeed",
    "LifecycleTimer",
    "MarketStateFeed",
    "PaperSession",
    "RiskContextBuilder",
    "SignalEvaluator",
    "SignalSource",
    "StampingRecorder",
    "TierGatedVetoSource",
    "build_replay_session",
    "calendar_event_source",
    "conviction_rows_from_sqlite",
    "event_source_horizon_minutes",
    "publish_managed_exit_exposure",
    "replay_market_open_source",
]

#: ``alert(message, context)``: the composition root adapts this to its
#: dispatcher. Every agent-channel gating decision that a config asked for and
#: the tier refused is announced through it, once per transition.
AlertSink = Callable[[str, Mapping[str, Any]], None]

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
    """Turns signals into :class:`TradePlan` objects. Allocates, then sizes ONCE.

    The execution service explicitly does not re-size and neither does the
    worker: V1 sized the same signal three times — in the bot, in the router and
    in the IG adapter — and the ledger disagreed with the broker. Everything
    downstream of here carries ``plan.size`` unchanged.

    What decides the size (audit F P1-11: this used to call ``size_trade`` at
    a flat ``risk_fraction`` and the portfolio constructor ran nowhere):

    1. every signal on the bar becomes a :class:`CandidateSignal` carrying its
       strategy's lifecycle and health (``strategy``), evidence tier
       (``tier_source``, default PROBATIONARY), its own instrument's regime
       (``regime``), an instrument volatility estimate and, if wired, the
       latest agent conviction (``conviction``);
    2. :class:`PortfolioConstructor` allocates the whole batch against the
       book (``snapshot``): tier base risk x lifecycle x health x confidence x
       correlation x concentration x vol target x margin x drawdown throttle x
       regime x correlated-risk budget x conviction, then the total weight
       budget, the 6% total risk cap and the final down-only tier cap;
    3. :func:`size_trade` runs once per accepted candidate with
       ``risk_fraction = tier base risk`` and ``portfolio_weight = weight``.
       The configured ``policy.risk_fraction`` is a CEILING on the tier base.

    Every allocation result is kept in :attr:`allocations`, and the stamp of
    the allocation behind each plan in :attr:`plan_stamps`, so an attempt row
    can say why a plan was the size it was (see :class:`StampingRecorder`).
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
        constructor: PortfolioConstructor | None = None,
        snapshot: Callable[[pd.Timestamp], PortfolioSnapshot] | None = None,
        regime: Callable[[str], str] | None = None,
        strategy: Callable[[str], StrategyView] | None = None,
        tier_source: Callable[[str], StrategyTier] | None = None,
        conviction: ConvictionAdapter | None = None,
        agent_tier: TierReading | None = None,
        alert: AlertSink | None = None,
    ) -> None:
        self.source = source
        self.account = account
        self.fx = fx
        self.policy = policy
        self.history = history
        self.account_ccy = account_ccy
        ceiling = policy.risk_fraction * 100.0
        if constructor is None:
            constructor = PortfolioConstructor(
                "equal_risk",
                ConstructionConfig(risk_ceiling_pct=ceiling),
                agent_tier=agent_tier,
            )
        else:
            current = constructor.config.risk_ceiling_pct
            constructor.config = replace(
                constructor.config,
                risk_ceiling_pct=ceiling if current is None else min(current, ceiling),
            )
            if agent_tier is not None:
                constructor.agent_tier = agent_tier
        self.constructor = constructor
        self.snapshot = snapshot
        self.regime = regime
        self.strategy = strategy
        self.tier_source = tier_source
        self.conviction = conviction
        self.alert = alert
        self.signals_seen = 0
        self.unsized: list[tuple[str, str]] = []
        self.allocations: list[AllocationResult] = []
        #: ``plan_id -> stamp``: the allocation behind each plan, as data.
        self.plan_stamps: dict[str, dict[str, Any]] = {}
        self._alerted_gate = False
        self._announce_gating()

    # -- the tier gate, announced ----------------------------------------

    def _announce_gating(self) -> None:
        """Config asked for the conviction channel; the tier held it in shadow."""
        if not self.constructor.conviction_gated_by_tier or self._alerted_gate:
            return
        self._alerted_gate = True
        tier = self.constructor.agent_tier
        message = (
            "conviction policy is enabled in config but the agent tier "
            f"({tier.tier.value}, source {tier.source}) does not permit dampening; "
            "it runs in SHADOW only"
        )
        context = {
            "conviction_policy": self.constructor.config.conviction.version,
            **tier.stamp(),
        }
        _log.warning(message, extra=context)
        if self.alert is not None:
            try:
                self.alert(message, context)
            except Exception:  # an alert channel failing must not change sizing
                _log.exception("conviction gate alert sink raised")

    # -- candidate assembly ----------------------------------------------

    def _vol(self, symbol: str) -> float:
        """Annualised close-to-close volatility of the bars seen so far.

        Used only by the volatility-parity allocator (the default equal-risk
        allocator ignores it). Falls back to 10% when fewer than 20 returns
        exist, which is the ``CandidateSignal`` default.
        """
        try:
            closes = self.history(symbol)["close"].astype(float)
        except Exception:
            return 0.10
        rets = closes.ffill().pct_change(fill_method=None).dropna().tail(250)
        if len(rets) < 20:
            return 0.10
        index = closes.index
        periods = 252.0
        if isinstance(index, pd.DatetimeIndex) and len(index) > 2:
            step = pd.Series(index).diff().dropna().median()
            if step > pd.Timedelta(0):
                periods = max(1.0, (pd.Timedelta(days=365.25) / step) * (5.0 / 7.0))
        vol = float(rets.std(ddof=1)) * math.sqrt(periods)
        return vol if math.isfinite(vol) and vol > 0 else 0.10

    def _candidate(self, signal: Signal, symbol: str, now: pd.Timestamp) -> CandidateSignal:
        view = self.strategy(signal.strategy_id) if self.strategy is not None else None
        lifecycle = (
            view.lifecycle
            if view is not None and view.lifecycle is not None
            else StrategyLifecycle.PAPER
        )
        health = 1.0 if view is None else max(0.0, min(1.0, float(view.health)))
        tier = (
            self.tier_source(signal.strategy_id)
            if self.tier_source is not None
            else StrategyTier.PROBATIONARY
        )
        return CandidateSignal(
            signal=signal,
            annualised_vol=self._vol(symbol),
            health=health,
            lifecycle=lifecycle,
            tier=tier,
            regime=self.regime(symbol) if self.regime is not None else None,
            conviction=(
                self.conviction.reading(symbol, now) if self.conviction is not None else None
            ),
        )

    # -- the call the worker makes ---------------------------------------

    def evaluate(self, instruments: Sequence[str], batch: BarBatch) -> Iterable[TradePlan]:
        account = self.account()
        pending: list[tuple[str, Signal, Instrument, float, pd.Timestamp]] = []
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
                pending.append((symbol, signal, instrument, rate, bar.timestamp))
        if not pending:
            return []

        now = max(ts for *_rest, ts in pending)
        candidates = [self._candidate(sig, sym, now) for sym, sig, *_rest in pending]
        snapshot = (
            self.snapshot(now)
            if self.snapshot is not None
            else PortfolioSnapshot(account=account, as_of=now)
        )
        result = self.constructor.allocate(candidates, snapshot)
        self.allocations.append(result)

        plans: list[TradePlan] = []
        for symbol, signal, instrument, rate, _ts in pending:
            allocation = result.allocation_for(signal.signal_id)
            if allocation is None or allocation.dropped or allocation.weight <= 0:
                reason = (
                    "allocation:missing"
                    if allocation is None
                    else f"allocation:{allocation.drop_reason or 'zero_weight'}"
                )
                # Not dropped silently: a signal refused a risk budget is as
                # much a fact as one that was sized.
                self.unsized.append((symbol, reason))
                _log.info(
                    "signal not allocated",
                    extra={"instrument": symbol, "reason": reason},
                )
                continue
            # THE ONE SIZING CALL. The tier base risk is the risk fraction and
            # the allocation weight (<= 1.0) scales it; size_trade decides the
            # units once and nothing downstream re-derives them.
            outcome = size_trade(
                signal=signal,
                instrument=instrument,
                account=account,
                fx_quote_to_account=rate,
                policy=replace(self.policy, risk_fraction=allocation.base_risk_pct / 100.0),
                portfolio_weight=allocation.weight,
                account_ccy=self.account_ccy,
            )
            if not outcome.sized:
                self.unsized.append((symbol, str(outcome.reason)))
                _log.info(
                    "signal not sized",
                    extra={"instrument": symbol, "reason": str(outcome.reason)},
                )
                continue
            plan = outcome.require()
            self.plan_stamps[plan.plan_id] = {
                **dict(result.stamp),
                "signal_id": signal.signal_id,
                "strategy_tier": allocation.candidate.tier.value,
                "base_risk_pct": allocation.base_risk_pct,
                "portfolio_weight": allocation.weight,
                "allocated_risk_pct": allocation.risk_pct,
                "allocation_reasons": [str(r) for r in allocation.reasons],
            }
            plans.append(plan)
        return plans


# ---------------------------------------------------------------------------
# The conviction channel's ONE construction site
# ---------------------------------------------------------------------------


#: ``(instrument, at) -> row``: the latest conviction AVAILABLE at ``at``.
ConvictionRows = Callable[[str, pd.Timestamp], Mapping[str, Any] | None]


class ConvictionAdapter:
    """The ONLY place a :class:`ConvictionReading` is constructed.

    It reads the latest verdict filed for an instrument by the thesis debate
    (``agents.workflows.run_thesis_debate`` -> ``record_conviction``), BY ID and
    point-in-time (only rows whose ``available_at <= at``), and turns it into
    the contract ``_step_conviction`` consumes. ``tests/unit/
    test_conviction_channel.py`` walks the AST and fails if a second
    construction site appears anywhere under ``src/``.

    Fail-neutral by design: an unreadable store, a malformed row or a reading
    the contract refuses yields ``None``, which the policy maps to exactly 1.0.
    An LLM outage or a corrupt row can therefore never change a size, which is
    the opposite of the regime scalar's "unknown reduces size" and deliberately
    so (``B_tradingagents.md`` §6.3(c)).
    """

    def __init__(self, rows: ConvictionRows) -> None:
        self.rows = rows
        self.read_errors = 0

    def reading(self, instrument: str, at: pd.Timestamp) -> ConvictionReading | None:
        try:
            row = self.rows(instrument.upper(), pd.Timestamp(at))
        except Exception as exc:  # a store fault must not become a size change
            self.read_errors += 1
            _log.warning(
                "conviction store unreadable; treating as absent (factor 1.0)",
                extra={"instrument": instrument, "error": f"{type(exc).__name__}: {exc}"},
            )
            return None
        if row is None:
            return None
        try:
            available = _utc_ts(row["available_at"])
            brief_as_of = _utc_ts(row["as_of"])
            return ConvictionReading(
                instrument=str(row["instrument"]).upper(),
                stance=str(row["stance"]),
                strength=int(row["strength"]),
                # Point-in-time: usable from the later of the brief's clock and
                # the instant the verdict was filed.
                as_of=max(available, brief_as_of),
                valid_until=_utc_ts(row["valid_until"]),
                artefact_id=str(row["conviction_id"]),
                policy_version=str(row["policy_version"]),
            )
        except Exception as exc:
            self.read_errors += 1
            _log.warning(
                "conviction row refused by the contract; treating as absent",
                extra={"instrument": instrument, "error": f"{type(exc).__name__}: {exc}"},
            )
            return None


def _utc_ts(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


#: Column contract with ``agents.tools.ThesisStore``'s ``conviction`` table.
#: Pinned by ``tests/integration/test_thesis_debate.py`` (write with the store,
#: read with this function).
_CONVICTION_COLUMNS = (
    "conviction_id",
    "instrument",
    "stance",
    "strength",
    "as_of",
    "available_at",
    "valid_until",
    "policy_version",
)


def conviction_rows_from_sqlite(path: str | Path) -> ConvictionRows:
    """Read the thesis store's ``conviction`` table READ-ONLY, point-in-time.

    Opened with ``mode=ro`` per query, so this process can never write the
    agent store, and a missing file is "no conviction" rather than an error.
    ``workers`` could import ``agents`` by rank, but the execution composition
    reading a table by its column contract keeps the agent package out of the
    paper worker's import graph.
    """
    db = Path(path)
    query = (
        f"SELECT {', '.join(_CONVICTION_COLUMNS)} FROM conviction "
        "WHERE instrument = ? AND available_at <= ? "
        "ORDER BY available_at DESC, conviction_id DESC LIMIT 1"
    )

    def rows(instrument: str, at: pd.Timestamp) -> Mapping[str, Any] | None:
        if not db.exists():
            return None
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            found = conn.execute(
                query, (instrument, to_utc_text(_utc_ts(at).to_pydatetime()))
            ).fetchone()
        finally:
            conn.close()
        return None if found is None else dict(zip(_CONVICTION_COLUMNS, found, strict=True))

    return rows


# ---------------------------------------------------------------------------
# The event veto, held behind the agent tier
# ---------------------------------------------------------------------------


class TierGatedVetoSource:
    """Wraps an :class:`EventVetoProvider` so it can only BLOCK at tier T2+.

    The gateway blocks when ``verdict.enabled and verdict.veto``. Below T2 this
    wrapper reports ``enabled=False`` on every verdict, whatever the policy
    config says, so the gateway records ``event_veto_shadow:<code>`` and passes:
    the channel runs in shadow. When config asked for enforcement and the tier
    refused, an alert is raised once. It never changes ``veto`` or
    ``available``: the shadow record is identical to the enforced one.
    """

    def __init__(
        self,
        inner: EventVetoProvider,
        tier: TierReading,
        *,
        alert: AlertSink | None = None,
    ) -> None:
        self.inner = inner
        self.tier = tier
        self.alert = alert
        self.gated = bool(inner.enabled and not tier.tier.permits_veto)
        if self.gated:
            message = (
                "event veto policy is enabled in config but the agent tier "
                f"({tier.tier.value}, source {tier.source}) does not permit vetoes; "
                "it runs in SHADOW only"
            )
            context = {"event_veto_policy": inner.policy_version, **tier.stamp()}
            _log.warning(message, extra=context)
            if alert is not None:
                try:
                    alert(message, context)
                except Exception:
                    _log.exception("event veto gate alert sink raised")

    @property
    def enabled(self) -> bool:
        return bool(self.inner.enabled and self.tier.tier.permits_veto)

    @property
    def policy_version(self) -> str:
        return f"{self.inner.policy_version}@{self.tier.tier.value}"

    def assess(self, instrument: str, at: datetime) -> VetoAssessment:
        verdict = self.inner.assess(instrument, at)
        if verdict.enabled and not self.tier.tier.permits_veto:
            return replace(verdict, enabled=False)
        return verdict


# ---------------------------------------------------------------------------
# Stamping the sizing decision onto every attempt row
# ---------------------------------------------------------------------------


class StampingRecorder:
    """Adds the allocation stamp and the agent tier to every attempt row.

    The gateway records an attempt per plan (``ExecutionAttempt``) with the
    plan's ``portfolio_weight`` and ``risk_amount`` but not WHY the plan was
    that size. This wraps the gateway's recorder: for a plan this runtime
    sized, it merges the evaluator's :attr:`SignalEvaluator.plan_stamps`
    entry (construction and conviction policy versions, strategy tier, base
    risk, allocated risk, the reasons); for every row, including exits, it
    merges the agent tier. It delegates everything else to the wrapped
    recorder, so ``attempts`` and any persistence behave exactly as before.
    """

    def __init__(
        self,
        inner: Any,
        stamps: Mapping[str, Mapping[str, Any]],
        tier: TierReading,
    ) -> None:
        self._inner = inner
        self._stamps = stamps
        self._tier = tier

    def record(self, attempt: Any) -> None:
        extra = dict(getattr(attempt, "extra", {}) or {})
        extra.update(self._tier.stamp())
        stamp = self._stamps.get(getattr(attempt, "plan_id", ""))
        if stamp is not None:
            extra["sizing"] = dict(stamp)
        # A non-dataclass attempt is recorded untouched rather than dropped.
        with contextlib.suppress(TypeError):
            attempt = replace(attempt, extra=extra)
        self._inner.record(attempt)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


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
    this class holds the wiring. A session built by
    :func:`build_replay_session` gets all four, and :attr:`risk_inputs_live`
    reports which of them are real on every context so a decision record can
    never be read as enforcing a limit it had no data for.

    No benign defaults (round 4)
    ----------------------------
    A missing source is a MISSING input, never a zero or a ``True``:

    * no ``pnl_ledger`` (and no static value pinned) -> ``daily_pnl`` and
      ``weekly_pnl`` are ``None``;
    * no ``correlation`` -> ``correlated_exposure`` is ``None``;
    * no ``market_open_source`` (and no static flag pinned) ->
      ``market.market_open`` is ``None``, which ``market_state`` blocks on.

    The gateway decides what a gap means: it blocks in SHADOW/DEMO/LIVE, and
    in PAPER only a gateway built with ``paper_allows_missing_inputs=True``
    lets the order through, with the gap on the attempt row. That permission
    belongs to the composition root, never to this class.

    ``open_risk`` is measured with the SAME cost-inclusive definition a new
    plan's ``risk_amount`` uses (``sizing_policy``; ``fixed_fractional_v2`` by
    default): stop distance plus the policy's spread and two expected
    slippages, so ``max_account_risk`` adds like to like.
    """

    adapter: Any
    clock: Callable[[], pd.Timestamp]
    fx: FxRateSource
    account_ccy: str = "GBP"
    #: ``None``: :func:`fiboki.risk.limits.default_limit_set` for ``mode``,
    #: resolved in ``__post_init__`` (the one source of truth for which set is
    #: in force).
    limits: LimitSet | None = None
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
    #: Timestamp of the newest bar per instrument, kept by :meth:`observe`
    #: (replay: the bar's own stamp, against the replay clock) or by
    #: :meth:`observe_closes` (a live feed: the instant the bar CLOSED, against
    #: the wall clock). This is what ``data_freshness`` and ``stale_price``
    #: measure their age from; the gateway owns the threshold.
    last_bar_times: dict[str, pd.Timestamp] = field(default_factory=dict)
    #: A STATIC flag, for a caller (or test) that pins one. ``None``, the
    #: default, with no :attr:`market_open_source` means "unknown", and the
    #: gateway's ``market_state`` check blocks on unknown.
    market_open: bool | None = None
    #: ``(instrument, now) -> is the session open``. When set it replaces the
    #: static :attr:`market_open` flag, so ``market_state`` blocks on a weekend
    #: as "market_closed" rather than every check reading the weekend as stale.
    market_open_source: Callable[[str, pd.Timestamp], bool] | None = None
    #: Last closed-bar price per instrument, from :meth:`observe_closes`. Used
    #: as the mid ONLY when the adapter keeps no bar of its own (a real venue
    #: adapter does not; the paper broker does).
    last_closes: dict[str, float] = field(default_factory=dict)
    #: STATIC values, used only when ``pnl_ledger`` is absent, so a test can
    #: pin a value without building a trade history. ``None`` (the default) is
    #: "not supplied": the loss checks see a missing input, not a flat day.
    daily_pnl: float | None = None
    weekly_pnl: float | None = None

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
    #: The agent event-veto source, normally a :class:`TierGatedVetoSource`.
    #: ``None``: not wired, and every attempt row says ``not_wired``.
    event_veto: EventVetoProvider | None = None
    #: The sizing rule new plans are sized under. ``open_risk`` prices each open
    #: position's risk-to-stop with the same rule, so the book and the plan are
    #: added in the same units. Pass the evaluator's policy.
    sizing_policy: SizingPolicy = field(default_factory=SizingPolicy)

    def __post_init__(self) -> None:
        if self.limits is None:
            self.limits = default_limit_set(self.mode)

    def observe(self, batch: BarBatch) -> None:
        """Record when each instrument last produced a closed bar."""
        for sym, bar in batch.frames.items():
            self.last_bar_times[sym] = bar.timestamp

    def observe_closes(
        self,
        closes: Mapping[str, pd.Timestamp],
        prices: Mapping[str, float] | None = None,
    ) -> None:
        """A LIVE feed reports when each instrument's newest bar CLOSED.

        Not the bar's start stamp: on the wall clock an H1 bar stamped 09:00 is
        an hour old the instant it closes at 10:00, so feeding the start stamp
        would block every order on ``data_stale`` and invite somebody to widen
        the limit until it stopped. The age the gateway computes from this is
        "seconds since the data became complete", which is the quantity its
        limits were written for.
        """
        for sym, closed_at in closes.items():
            self.last_bar_times[sym] = closed_at
        for sym, price in (prices or {}).items():
            self.last_closes[sym] = float(price)

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
            market_open=(
                self.market_open_source(instrument.symbol, now)
                if self.market_open_source is not None
                else self.market_open
            ),
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
        if last is not None:
            return float(last.close)
        return self.last_closes.get(symbol)

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
            # MEASURED, so the construction's correlated and total open-risk
            # budgets run against the book rather than reporting "unmeasured".
            open_risk_by_instrument=self.open_risk_by_instrument(now),
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

    def _position_cost_per_unit(self, pos: Any, instrument: Instrument) -> float:
        """The sizing rule's stop-out cost for an OPEN position, price units.

        Mirrors :func:`fiboki.backtest.engine.stop_out_cost_per_unit`, which a
        new plan's ``risk_amount`` includes under ``fixed_fractional_v2``: the
        profile's full spread at the entry hour and entry price plus two
        expected slippages. ``0`` under ``fixed_fractional_v1``.
        """
        profile = self.sizing_policy.resolved_cost_profile
        if profile is None:
            return 0.0
        entry_time = pd.Timestamp(pos.entry_time)
        spread = profile.spread_price(instrument, int(entry_time.hour), float(pos.entry_price))
        return float(spread + 2.0 * profile.expected_slippage_price(instrument))

    def open_risk_by_instrument(self, now: pd.Timestamp) -> dict[str, float]:
        """Risk to stop per instrument, ACCOUNT currency. ``open_risk`` sums it.

        Cost-inclusive under the builder's :attr:`sizing_policy`, the same
        definition as ``TradePlan.risk_amount``.
        """
        out: dict[str, float] = {}
        for pos in self.adapter.positions():
            instrument = get_instrument(pos.instrument)
            rate = float(self.fx.rate(instrument.quote, self.account_ccy, now))
            out[pos.instrument] = out.get(pos.instrument, 0.0) + (
                (abs(pos.entry_price - pos.stop_loss) + self._position_cost_per_unit(pos, instrument))
                * pos.size
                * instrument.contract_size
                * rate
            )
        return out

    def open_risk(self, now: pd.Timestamp) -> float:
        return float(sum(self.open_risk_by_instrument(now).values()))

    # -- the call the worker makes --------------------------------------

    def __call__(self, plan: TradePlan) -> RiskContext:
        now = self.clock()
        instrument = get_instrument(plan.instrument)
        rate = float(self.fx.rate(instrument.quote, self.account_ccy, now))
        snapshot = self.snapshot(now)
        windows = self.windows(now)
        limits = self.limits
        assert limits is not None  # resolved in __post_init__
        # No correlation matrix is a MISSING input (None), not "uncorrelated".
        correlated = (
            correlated_exposure(
                plan.instrument,
                snapshot=snapshot,
                threshold=limits.correlation_threshold,
                correlation=self.correlation,
            )
            if self.correlation is not None
            else None
        )
        extra: dict[str, Any] = {
            "estimated_spread": self.spread_source is None,
            "risk_inputs_live": self.risk_inputs_live(),
            "realised_portfolio_vol": snapshot.realised_portfolio_vol,
            "open_risk_basis": self.sizing_policy.basis,
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
            event_veto=self.event_veto,
        )


# ---------------------------------------------------------------------------
# The economic calendar, as the gateway's event source
# ---------------------------------------------------------------------------


def calendar_event_source(
    calendar: EconomicCalendar,
    *,
    horizon_minutes: float,
    min_impact: ImpactLevel | str = ImpactLevel.HIGH,
) -> Callable[[str, pd.Timestamp], tuple[pd.Timestamp, ...]]:
    """``(instrument, now) -> event times`` for :attr:`RiskContextBuilder.event_source`.

    Returns the relevant events (the instrument's currencies, at or above
    ``min_impact``) whose span lies within ``horizon_minutes`` of ``now``. The
    gateway owns the threshold and applies ``|event - now| <= window`` itself;
    ``horizon_minutes`` only needs to be at least that window, so the source
    never hides an event the gateway would have blocked on.

    An event with no fixed release time (a Bank of Japan meeting) spans
    ``[event_time, window_end]``. The gateway takes one timestamp per event,
    so the one reported is the point of that span nearest to ``now``: inside
    the span that is ``now`` itself, which blocks, and outside it the nearer
    end, which is exactly the distance the blackout is defined on.
    """
    minutes = max(0, int(math.ceil(float(horizon_minutes))))

    def source(instrument: str, now: pd.Timestamp) -> tuple[pd.Timestamp, ...]:
        ts = pd.Timestamp(now)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        near = calendar.events_near(
            instrument,
            ts,
            minutes_before=minutes,
            minutes_after=minutes,
            min_impact=min_impact,
        )
        return tuple(min(max(ts, e.event_time), e.span_end) for e in near)

    return source


def event_source_horizon_minutes(limits: LimitSet, bar_minutes: float = 0.0) -> float:
    """How far from ``now`` an event source must look so the gateway sees every event.

    v1 sets: the symmetric ``event_blackout_minutes``. v2 sets
    (``asymmetric_blackout``): the gateway's window is
    ``[decision - post, decision + max(pre, bar)]`` with ``decision`` up to one
    bar after ``now`` (the signal bar's close), so the source must reach
    ``bar + max(pre, bar)`` ahead and ``post`` behind. A source narrower than
    that hides an event the gateway would have blocked on.
    """
    if not limits.asymmetric_blackout:
        return float(limits.event_blackout_minutes)
    bar = max(0.0, float(bar_minutes))
    pre = float(limits.event_blackout_pre_minutes or 0.0)
    post = float(limits.event_blackout_post_minutes or 0.0)
    return max(post, bar + max(pre, bar))


def replay_market_open_source(
    builder: RiskContextBuilder,
) -> Callable[[str, pd.Timestamp], bool]:
    """A REPLAY's market-open source: the data itself.

    The instrument is open at ``now`` when the replay delivered a closed bar
    for it at ``now``. The venue's session calendar decides FILLS (an order
    queued on Friday's last bar fills at Sunday's open, as in the engine);
    blocking the decision on it here would break that parity. Anything the
    data does not show at ``now`` is reported closed rather than assumed open.
    """

    def source(symbol: str, now: pd.Timestamp) -> bool:
        seen = builder.last_bar_times.get(symbol)
        return seen is not None and pd.Timestamp(seen) == pd.Timestamp(now)

    return source


def _median_bar_minutes(frames: Mapping[str, pd.DataFrame]) -> float:
    widest = 0.0
    for frame in frames.values():
        if len(frame.index) > 2:
            delta = frame.index.to_series().diff().dropna().median()
            widest = max(widest, float(pd.Timedelta(delta).total_seconds()) / 60.0)
    return widest


@dataclass(frozen=True, slots=True)
class CalendarWiring:
    """What a paper session was given for scheduled events, as recorded fact."""

    source: str
    wired_into_gateway: bool
    wired_into_venue: bool
    allow_empty_calendar: bool
    covered: bool
    n_events: int
    fingerprint: Mapping[str, Any] | None
    gateway_window_minutes: float
    min_impact: str = ImpactLevel.HIGH.value
    coverage_error: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "wired_into_gateway": self.wired_into_gateway,
            "wired_into_venue": self.wired_into_venue,
            "allow_empty_calendar": self.allow_empty_calendar,
            "covered": self.covered,
            "n_events": self.n_events,
            "fingerprint": None if self.fingerprint is None else dict(self.fingerprint),
            "gateway_window_minutes": self.gateway_window_minutes,
            "min_impact": self.min_impact,
            "coverage_error": self.coverage_error,
        }


def _resolve_calendar(
    calendar: EconomicCalendar | None,
    frames: Mapping[str, pd.DataFrame],
    *,
    allow_empty_calendar: bool,
    limits: LimitSet,
) -> tuple[EconomicCalendar | None, CalendarWiring]:
    """Load the default calendar, check it covers the replay, decide the wiring.

    Refuses (raises ``CalendarError``) when the calendar does not span the
    frames or know every instrument's currencies, unless
    ``allow_empty_calendar``. A calendar with no events is never wired: an
    event source that can only ever answer "nothing scheduled" would read as
    a live check in the summary while blocking nothing.
    """
    label = "explicit"
    if calendar is None:
        calendar = load_official_calendar()
        label = "fiboki.marketstate.calendar.load_official_calendar"
    starts = [f.index[0] for f in frames.values() if len(f.index)]
    ends = [f.index[-1] for f in frames.values() if len(f.index)]
    currencies = sorted({c for sym in frames for c in instrument_currencies(sym)})
    covered, error = True, ""
    try:
        calendar.assert_populated(
            start=min(starts) if starts else None,
            end=max(ends) if ends else None,
            currencies=currencies,
        )
    except Exception as exc:
        if not allow_empty_calendar:
            raise
        covered, error = False, str(exc).splitlines()[0]
    n_events = len(calendar.all_events())
    wired = n_events > 0
    wiring = CalendarWiring(
        source=label,
        wired_into_gateway=wired,
        wired_into_venue=wired,
        allow_empty_calendar=allow_empty_calendar,
        covered=covered,
        n_events=n_events,
        fingerprint=blackout_fingerprint(calendar),
        gateway_window_minutes=(
            float(limits.event_blackout_minutes)
            if not limits.asymmetric_blackout
            else float(max(limits.event_blackout_pre_minutes or 0.0,
                           limits.event_blackout_post_minutes or 0.0))
        ),
        coverage_error=error,
    )
    return (calendar if wired else None), wiring


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
    calendar: CalendarWiring | None = None

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
            # The limit set in force and whether this PAPER gateway was given
            # the explicit permission to pass an order with a missing input.
            "limits_version": getattr(self.context_builder.limits, "version", None),
            "paper_allows_missing_inputs": bool(
                getattr(self.gateway, "paper_allows_missing_inputs", False)
            ),
            # What the gateway's event_blackout check had to compare against.
            # Derived from the builder, not asserted: a session with no event
            # source says so here however it was configured.
            "economic_calendar": {
                **({} if self.calendar is None else self.calendar.as_row()),
                "wired_into_gateway": self.context_builder.event_source is not None,
            },
            # How sizes were decided, as recorded fact: the construction and
            # conviction policy versions, the agent tier in force, and how many
            # signals the allocator refused (``unsized`` counts them with the
            # sizing refusals; ``allocation_dropped`` separates them).
            "sizing": {
                **self.evaluator.constructor.stamp(),
                "allocations": len(self.evaluator.allocations),
                "allocation_dropped": sum(
                    1 for _sym, why in self.evaluator.unsized if why.startswith("allocation:")
                ),
                "conviction_shadow_rows": sum(
                    len(r.shadow) for r in self.evaluator.allocations
                ),
                "event_veto_wired": self.context_builder.event_veto is not None,
                "open_risk_basis": self.context_builder.sizing_policy.basis,
                "kill_switch_journal": (
                    "durable" if getattr(self.gateway.kill_switch, "durable", False)
                    else "in_memory"
                ),
            },
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
    limits: LimitSet | None = None,
    intent_store: IntentStore | None = None,
    market_state_engine: Any = None,
    lifecycle_service: Any = None,
    lifecycle_inputs: Callable[[], Mapping[str, tuple[Any, Any]]] | None = None,
    worker_config: LiveWorkerConfig | None = None,
    dispatcher: Any = None,
    default_lifecycle: StrategyLifecycle = StrategyLifecycle.PAPER,
    correlation: CorrelationMatrix | None = None,
    calendar: EconomicCalendar | None = None,
    allow_empty_calendar: bool = False,
    kill_switch: KillSwitch | None = None,
    paths: FibokiPaths | None = None,
    construction: ConstructionConfig | None = None,
    allocator: str = "equal_risk",
    agent_tier: TierReading | None = None,
    tier_source: Callable[[str], StrategyTier] | None = None,
    conviction_source: ConvictionRows | None = None,
    event_veto: EventVetoProvider | None = None,
    alert: AlertSink | None = None,
) -> PaperSession:
    """Assemble a PAPER runtime end to end over stored bars.

    market data -> market state -> strategy -> PORTFOLIO CONSTRUCTION ->
    sizing -> risk gateway -> execution service -> paper venue -> telemetry.

    Sizing (audit F P1-11): every bar's signals are allocated together by
    :class:`PortfolioConstructor` (``construction``, default construction_v2,
    ``allocator``) against the venue's own book, and each accepted candidate
    is sized once at ``risk_fraction = its tier's base risk`` (``tier_source``,
    default PROBATIONARY for every strategy) times its weight (<= 1.0).
    ``sizing_policy.risk_fraction`` is a ceiling on the tier base.

    The agent channels: ``agent_tier`` (default: T1, shadow only) gates both.
    ``conviction_source`` (e.g. :func:`conviction_rows_from_sqlite`) feeds
    :class:`ConvictionAdapter`; ``event_veto`` is wrapped in
    :class:`TierGatedVetoSource`. ``alert`` hears about any channel a config
    enabled and the tier held in shadow.

    The kill switch: when ``gateway`` is not given, the gateway is built with
    the OPERATOR's durable journal, ``KillSwitch.from_paths(paths)`` with
    ``paths`` defaulting to ``resolve_paths(os.environ)`` (the same file the
    CLI and the API resolve), and ``mode=PAPER``, which refuses an in-memory
    journal. Pass ``kill_switch`` to use another durable journal (a test's
    temporary one).

    The worker refuses to start unless the execution service is in a mode its
    config authorises, and that default is paper only. Nothing here can widen
    it; widening it is an edit to ``allowed_modes``, reviewed, in the config.

    ``calendar`` defaults to the committed official calendar and is wired into
    the gateway's event source and the venue's blackout source. A replay it
    does not cover raises ``CalendarError`` unless ``allow_empty_calendar``;
    see the module docstring for the explicit way to run with none.
    """
    fx = fx or IdentityFxSource()
    # THE limit set in force for paper (limits_v2_paper), from the one function
    # every composition root and the API read.
    limits = limits if limits is not None else default_limit_set(ExecutionMode.PAPER)
    events, calendar_wiring = _resolve_calendar(
        calendar, frames, allow_empty_calendar=allow_empty_calendar, limits=limits
    )
    feed = FrameReplayFeed(
        frames, timeframe=timeframe, warmup=warmup, series=exit_series
    )
    # The venue gets the SAME calendar the engine is given in validation, so a
    # document exit policy that declares an event blackout blocks the same
    # entries in paper as in a backtest.
    broker = PaperBroker(config=paper_config, fx=fx, blackout=events)
    for symbol, frame in frames.items():
        if len(frame.index) > 2:
            deltas = frame.index.to_series().diff().dropna()
            broker.set_bar_interval(symbol, deltas.median())

    tier = agent_tier or TierReading.default("not_supplied")
    if gateway is None:
        switch = kill_switch or KillSwitch.from_paths(
            paths if paths is not None else resolve_paths(os.environ)
        )
        # PAPER composition: the explicit, recorded permission for an order to
        # pass with a missing loss/exposure input (every attempt row carries
        # ``missing_inputs`` and ``paper_allows_missing_inputs``). A DEMO or LIVE
        # composition never passes it.
        gateway = RiskGateway(
            limits=limits,
            kill_switch=switch,
            mode=ExecutionMode.PAPER,
            paper_allows_missing_inputs=True,
        )
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
        # THE EVENT SOURCE. Without it the gateway's event_blackout check ran
        # on every order against an empty tuple and could never fire.
        event_source=(
            calendar_event_source(
                events,
                horizon_minutes=event_source_horizon_minutes(
                    limits, _median_bar_minutes(frames)
                ),
            )
            if events is not None
            else None
        ),
        default_lifecycle=default_lifecycle,
        # open_risk priced with the rule the evaluator sizes new plans under.
        sizing_policy=sizing_policy,
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
    # The market-open input is SOURCED (from the replayed data), never a
    # default True.
    builder.market_open_source = replay_market_open_source(builder)
    if lifecycle_service is not None:
        builder.strategy_source = _lifecycle_strategy_source(
            lifecycle_service, default_lifecycle
        )
    if event_veto is not None:
        builder.event_veto = TierGatedVetoSource(event_veto, tier, alert=alert)

    # THE SIZING PATH. The constructor sees the same book the gateway will
    # (builder.snapshot), the same lifecycle view (builder.strategy_view) and
    # each candidate's own instrument regime.
    evaluator = SignalEvaluator(
        source=signal_source,
        account=broker.account,
        fx=fx,
        policy=sizing_policy,
        history=_history,
        account_ccy=paper_config.account_ccy,
        constructor=PortfolioConstructor(
            allocator, construction or ConstructionConfig(), agent_tier=tier
        ),
        snapshot=builder.snapshot,
        regime=(market_state.regime if market_state is not None else None),
        strategy=builder.strategy_view,
        tier_source=tier_source,
        conviction=(
            ConvictionAdapter(conviction_source) if conviction_source is not None else None
        ),
        agent_tier=tier,
        alert=alert,
    )
    # Every attempt row, allowed or blocked, carries the agent tier, and a
    # plan this runtime sized carries its allocation stamp.
    if not isinstance(gateway.recorder, StampingRecorder):
        gateway.recorder = StampingRecorder(gateway.recorder, evaluator.plan_stamps, tier)

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
        calendar=calendar_wiring,
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
