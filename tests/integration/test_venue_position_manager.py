"""The IG and OANDA adapters run the backtester's position book. Byte for byte.

``tests/integration/test_paper_backtest_parity.py`` proved that the PAPER
adapter and the backtester agree across the whole exit vocabulary. It could not
prove anything about IG or OANDA, because those adapters drove no position book
at all: a demo deployment would have attached one stop and one target per
position and nothing would have trailed.

This file closes that. The same bars, the same signals and the same exit policy
are driven through:

* :class:`~fiboki.backtest.engine.BacktestEngine` -- the reference,
* :class:`~fiboki.broker.paper.PaperBroker` -- the existing proof,
* :class:`~fiboki.broker.position_manager.VenuePositionManager` over
  :class:`~fiboki.broker.simulated_venue.SimulatedVenue`,
* the same manager over :class:`~fiboki.broker.ig.IgAdapter`,
* and over :class:`~fiboki.broker.oanda.OandaAdapter`,

and the TRADE ledger, the per-FILL leg ledger, the rejection counts and the
final balance are compared as text. The two HTTP adapters talk to the in-process
venues in ``tests/venue_fixtures.py`` over their real request payloads, so the
amendment and partial-close paths are exercised end to end rather than mocked.

What parity does and does not mean here
----------------------------------------
It means the EXIT DECISIONS are the same object on all five paths: one
``PositionBook``, one ``ExitPolicy``, one ``FillSimulator``, one sequence
counter feeding one counter-based RNG. A trail step, a scale-out leg, a
breakeven move and a time stop are decided identically whichever venue is
behind them.

It does NOT mean the P&L a demo account reports will equal the backtest's. The
book here is MODELLED -- its entry price comes from the fill simulator, not
from the venue's dealt price -- and the difference between the two is recorded
on every ``AmendTelemetry`` row rather than reconciled away. See the module
docstring of ``broker/position_manager.py``.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    PrecomputedSignals,
)
from fiboki.backtest.exits import ExitPolicy, ReversalMode, TrailKind, TrailSpec
from fiboki.backtest.position import BookConfig, PositionBook
from fiboki.broker.execution_service import ExecutionService, InMemoryIntentStore
from fiboki.broker.ig import IgAdapter, IgConfig
from fiboki.broker.oanda import OANDA_PRACTICE_HOST, OandaAdapter, OandaConfig
from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.broker.position_manager import RetryPolicy, VenuePositionManager
from fiboki.broker.simulated_venue import SimulatedVenue, VenueConfig
from fiboki.core.contracts import Order, Signal
from fiboki.core.enums import Direction, ExecutionMode, ExitReason, OrderType, Provenance
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.portfolio.sizing import PortfolioSizer, SizingPolicy
from fiboki.risk.gateway import ExitContext, MarketView, RiskGateway, VenueView
from fiboki.risk.killswitch import RequestKind
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.fills import Bar, FillSimulator, IntrabarPolicy
from fiboki.sim.profiles import IG_REALISTIC, OANDA_REALISTIC
from tests.exec_fixtures import synthetic_frame
from tests.venue_fixtures import IgHttpVenue, OandaHttpVenue

SYMBOL = "EURUSD"
ATR_COLUMN = "atr14"
START_BALANCE = 50_000.0
VENUE_PRICE = 1.1000


# ==========================================================================
# Fixtures shared with the paper parity suite, restated rather than imported
# ==========================================================================


def _atr_frame(frame: pd.DataFrame) -> pd.DataFrame:
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / 14, adjust=False).mean().bfill()
    return pd.DataFrame({ATR_COLUMN: atr.to_numpy()}, index=frame.index)


def _ladder_signals(
    frame: pd.DataFrame, *, every: int = 23, stop: float = 0.0030
) -> list[Signal]:
    out: list[Signal] = []
    for i in range(5, len(frame), every):
        ts = frame.index[i]
        ref = float(frame["close"].iloc[i])
        long = (i // every) % 2 == 0
        sign = 1.0 if long else -1.0
        out.append(
            Signal(
                strategy_id="venue_parity",
                instrument=SYMBOL,
                timeframe="H1",
                direction=Direction.LONG if long else Direction.SHORT,
                bar_time=ts,
                reference_price=ref,
                stop_price=ref - sign * stop,
                take_profit_prices=(
                    ref + sign * 0.0020,
                    ref + sign * 0.0045,
                    ref + sign * 0.0090,
                ),
                take_profit_allocations=(0.4, 0.3, 0.3),
            )
        )
    return out


#: Every branch of the exit vocabulary at once -- the same policy the paper
#: parity suite uses, so a divergence here is a venue-path divergence and not a
#: different strategy.
FULL_POLICY = ExitPolicy(
    allocations=(0.4, 0.3, 0.3),
    trailing=TrailSpec(
        kind=TrailKind.ATR_CHANDELIER,
        value=2.5,
        activate_after_r=0.5,
        atr_column=ATR_COLUMN,
    ),
    breakeven_at_r=0.25,
    max_bars_in_trade=40,
    cooldown_bars_after_exit=25,
    reversal=ReversalMode.REVERSE,
)

TRAIL_ONLY_POLICY = ExitPolicy(
    trailing=TrailSpec(
        kind=TrailKind.ATR_CHANDELIER,
        value=3.0,
        activate_after_r=0.0,
        atr_column=ATR_COLUMN,
    )
)


# ==========================================================================
# Building the five paths
# ==========================================================================


def _cfg(profile=IG_REALISTIC, **extra):
    return dict(
        initial_balance=START_BALANCE,
        account_ccy="USD",
        profile=profile,
        intrabar_policy=IntrabarPolicy.STOP_FIRST,
        max_concurrent=1,
        max_per_instrument=1,
        strategy_id="venue_parity",
        **extra,
    )


def _run_backtest(frame, signals, sizer, fx, policy, series, **cfg) -> BacktestResult:
    return BacktestEngine(
        data={SYMBOL: frame},
        config=BacktestConfig(**cfg),
        strategy=PrecomputedSignals(signals),
        sizer=sizer,
        fx=fx,
        exit_policy=policy,
        exit_series={SYMBOL: series} if series is not None else None,
    ).run()


def make_ig_adapter(venue: IgHttpVenue) -> IgAdapter:
    adapter = IgAdapter(
        IgConfig(identifier="joe", password="x", api_key="k", account_id="ABC123"),
        transport=venue,
    )
    adapter.connect()
    return adapter


def make_oanda_adapter(venue: OandaHttpVenue) -> OandaAdapter:
    adapter = OandaAdapter(
        OandaConfig(
            account_id=venue.account_id,
            api_token="t",
            base_url=f"https://{OANDA_PRACTICE_HOST}",
            mode=ExecutionMode.DEMO,
        ),
        transport=venue,
    )
    adapter.connect()
    return adapter


def _exit_context_factory(now_fn):
    """Permissive but REAL: the exit check set runs on every instruction.

    Not a stub that returns ``allowed``. ``evaluate_exit`` runs kill_switch,
    market_state, data_freshness, stale_price and broker_health against this,
    so an instruction issued while the market is shut or the feed is stale is
    refused here exactly as it would be in production.
    """

    def factory(position, kind: str) -> ExitContext:
        now = now_fn()
        return ExitContext(
            instrument=position.instrument,
            strategy_id=position.strategy_id or "venue_parity",
            size=position.size,
            now=now,
            mode=ExecutionMode.PAPER,
            position_id=position.position_id,
            limits=PAPER_LIMITS,
            market=MarketView(
                mid_price=VENUE_PRICE,
                spread_price=0.00012,
                quote_time=now,
                last_bar_time=now,
                market_open=True,
            ),
            venue=VenueView(connected=True, score=1.0),
            request_kind=(
                RequestKind.PROTECTIVE_AMEND
                if kind == "amend"
                else RequestKind.REDUCE
                if kind == "reduce"
                else RequestKind.CLOSE
            ),
        )

    return factory


def build_manager(adapter, *, policy: ExitPolicy, profile, fx, account_ccy="USD"):
    """A manager configured EXACTLY as the paper adapter configures its book."""
    sim = FillSimulator(profile=profile, intrabar_policy=IntrabarPolicy.STOP_FIRST)
    book = PositionBook(
        sim=sim,
        fx=fx,
        config=BookConfig(
            account_ccy=account_ccy,
            max_concurrent=1,
            max_per_instrument=1,
            charge_financing=True,
            financing_rollover_hour_utc=21,
            strategy_id="venue_parity",
            provenance=Provenance.PAPER,
        ),
        policy=policy,
        initial_balance=START_BALANCE,
        latency_bars=profile.latency_bars,
        financing_profile=profile.financing,
    )
    execution = ExecutionService(
        adapter=adapter,
        gateway=RiskGateway(limits=PAPER_LIMITS),
        store=InMemoryIntentStore(),
        mode=ExecutionMode.PAPER,
    )
    manager = VenuePositionManager(
        execution=execution,
        book=book,
        context_factory=_exit_context_factory(lambda: manager.now or pd.Timestamp(
            "2024-01-01", tz="UTC"
        )),
        fx=fx,
        account_ccy=account_ccy,
        latency_bars=profile.latency_bars,
        retry=RetryPolicy(attempts=2, initial_backoff_seconds=0.0),
        sleeper=lambda _s: None,
    )
    manager.set_bar_interval(SYMBOL, pd.Timedelta(hours=1))
    return manager


def drive_manager(manager, adapter, frame, signals, sizer, fx, policy, series, *,
                  account_ccy="USD"):
    """Drive the manager one bar at a time, exactly as the live worker does."""
    by_time: dict[pd.Timestamp, list[Signal]] = {}
    for s in signals:
        by_time.setdefault(s.bar_time, []).append(s)
    instrument = get_instrument(SYMBOL)
    last_bar: Bar | None = None

    for i, ts in enumerate(frame.index):
        row = frame.iloc[i]
        bar = Bar(ts, float(row["open"]), float(row["high"]), float(row["low"]),
                  float(row["close"]))
        last_bar = bar
        columns = (
            {SYMBOL: {ATR_COLUMN: float(series[ATR_COLUMN].iloc[i])}}
            if series is not None
            else None
        )
        if hasattr(adapter, "set_time"):
            adapter.set_time(ts)
        if hasattr(adapter, "now"):
            adapter.now = ts
        manager.on_bar({SYMBOL: bar}, bar_index=i, timestamp=ts, series=columns)

        account = manager.account()
        rate = fx.rate(instrument.quote, account_ccy, ts)
        for signal in by_time.get(ts, ()):
            scheduled = manager.book.schedule_reversal(
                signal.instrument, signal.direction, index=i
            )
            if scheduled and policy.reversal is ReversalMode.CLOSE_ONLY:
                manager.book.bump("reversal_close_only")
                continue
            size = sizer.size_for(signal, instrument, account, rate)
            if size <= 0:
                manager.book.bump("sized_to_zero")
                continue
            # THE PRE-TRADE CHECK. A venue fills a market order the instant it
            # is sent, so an entry the book will refuse must not be dealt: the
            # account would hold a position the model does not. This is the
            # live worker's obligation and the test carries it too.
            if manager.precheck_entry(SYMBOL) is not None:
                continue
            order = Order(
                plan_id=signal.signal_id,
                instrument=SYMBOL,
                direction=signal.direction,
                size=size,
                order_type=OrderType.MARKET,
                mode=ExecutionMode.PAPER,
                client_ref=f"venue-{signal.signal_id}"[:30],
                stop_loss=signal.stop_price,
                take_profit=signal.take_profit_prices[0],
                take_profit_prices=tuple(signal.take_profit_prices),
                take_profit_allocations=tuple(signal.take_profit_allocations),
                created_at=ts,
            )
            ack = adapter.place_order(order)
            manager.adopt(order, ack, strategy_id=signal.strategy_id)
    manager.finish({SYMBOL: last_bar})
    return manager


def run_paper(frame, signals, sizer, fx, policy, series, **cfg) -> PaperBroker:
    paper = PaperBroker(
        config=PaperConfig(provenance=Provenance.PAPER, exit_policy=policy, **cfg),
        fx=fx,
    )
    paper.set_bar_interval(SYMBOL, pd.Timedelta(hours=1))
    by_time: dict[pd.Timestamp, list[Signal]] = {}
    for s in signals:
        by_time.setdefault(s.bar_time, []).append(s)
    instrument = get_instrument(SYMBOL)
    last_bar: Bar | None = None
    for i, ts in enumerate(frame.index):
        row = frame.iloc[i]
        bar = Bar(ts, float(row["open"]), float(row["high"]), float(row["low"]),
                  float(row["close"]))
        last_bar = bar
        columns = (
            {SYMBOL: {ATR_COLUMN: float(series[ATR_COLUMN].iloc[i])}}
            if series is not None
            else None
        )
        paper.on_bar({SYMBOL: bar}, bar_index=i, timestamp=ts, series=columns)
        account = paper.account()
        rate = fx.rate(instrument.quote, cfg.get("account_ccy", "GBP"), ts)
        for signal in by_time.get(ts, ()):
            scheduled = paper.book.schedule_reversal(
                signal.instrument, signal.direction, index=i
            )
            if scheduled and policy.reversal is ReversalMode.CLOSE_ONLY:
                paper.book.bump("reversal_close_only")
                continue
            size = sizer.size_for(signal, instrument, account, rate)
            if size <= 0:
                paper.book.bump("sized_to_zero")
                continue
            paper.register_plan(signal.signal_id, signal.strategy_id)
            paper.place_order(
                Order(
                    plan_id=signal.signal_id,
                    instrument=SYMBOL,
                    direction=signal.direction,
                    size=size,
                    order_type=OrderType.MARKET,
                    mode=ExecutionMode.PAPER,
                    client_ref=f"paper-{signal.signal_id}",
                    stop_loss=signal.stop_price,
                    take_profit=signal.take_profit_prices[0]
                    if signal.take_profit_prices
                    else None,
                    take_profit_prices=tuple(signal.take_profit_prices),
                    take_profit_allocations=tuple(signal.take_profit_allocations),
                    created_at=ts,
                )
            )
    paper.finish({SYMBOL: last_bar})
    return paper


# ==========================================================================
# Ledger text
# ==========================================================================


def ledger(trades) -> str:
    return BacktestResult(
        trades=list(trades), equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={},
    ).ledger_text()


def leg_ledger(legs) -> str:
    return BacktestResult(
        trades=[], equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={},
        exit_legs=list(legs),
    ).leg_ledger_text()


# ==========================================================================
# Venue construction
# ==========================================================================


def make_venue(kind: str, profile):
    fx = IdentityFxSource()
    if kind == "simulated":
        venue = SimulatedVenue(
            VenueConfig(prices={SYMBOL: VENUE_PRICE}),
            balance=START_BALANCE,
            currency="USD",
            now=pd.Timestamp("2024-01-01", tz="UTC"),
        )
        return venue, venue, fx
    if kind == "ig":
        http = IgHttpVenue(prices={SYMBOL: VENUE_PRICE}, currency="USD",
                           balance=START_BALANCE)
        return make_ig_adapter(http), http, fx
    if kind == "oanda":
        http = OandaHttpVenue(prices={SYMBOL: VENUE_PRICE}, currency="USD",
                              balance=START_BALANCE)
        return make_oanda_adapter(http), http, fx
    raise AssertionError(kind)


def compare(kind: str, *, policy=FULL_POLICY, profile=IG_REALISTIC, seed=7, n=900,
            signals=None, with_series=True):
    frame = synthetic_frame(n=n, seed=seed)
    series = _atr_frame(frame) if with_series else None
    signals = _ladder_signals(frame) if signals is None else signals
    sizer = PortfolioSizer(policy=SizingPolicy(risk_fraction=0.01))
    fx = IdentityFxSource()
    cfg = _cfg(profile=profile)

    bt = _run_backtest(frame, signals, sizer, fx, policy, series, **cfg)
    adapter, http, _ = make_venue(kind, profile)
    manager = build_manager(adapter, policy=policy, profile=profile, fx=fx)
    drive_manager(manager, adapter, frame, signals, sizer, fx, policy, series)
    return bt, manager, adapter, http


VENUES = ["simulated", "ig", "oanda"]


# ==========================================================================
# THE PARITY TESTS
# ==========================================================================


@pytest.mark.parametrize("kind", VENUES)
def test_every_venue_drives_the_same_position_book_as_the_backtester(kind: str) -> None:
    """The headline. One book, three venues, one ledger."""
    bt, manager, _adapter, _http = compare(kind)
    assert bt.trades, "the fixture produced no trades; the test would prove nothing"
    assert bt.partial_exits > 0, "no position scaled out; the ladder was not exercised"
    assert ledger(manager.book.trades) == bt.ledger_text(), (
        f"the {kind} position manager and the backtester disagree about the "
        "TRADE ledger"
    )


@pytest.mark.parametrize("kind", VENUES)
def test_the_per_fill_leg_ledger_matches_too(kind: str) -> None:
    """Equal totals reached by scaling out differently is still a divergence."""
    bt, manager, _adapter, _http = compare(kind)
    assert leg_ledger(manager.book.exit_legs) == bt.leg_ledger_text()


@pytest.mark.parametrize("kind", VENUES)
def test_rejections_and_final_balance_match(kind: str) -> None:
    bt, manager, _adapter, _http = compare(kind)
    assert manager.book.rejections == bt.rejections
    assert manager.book.balance == bt.final_equity


@pytest.mark.parametrize("kind", VENUES)
def test_the_venue_paths_agree_with_the_paper_path(kind: str) -> None:
    """Transitive, but worth asserting directly: paper was the previous proof."""
    frame = synthetic_frame(n=900, seed=7)
    series = _atr_frame(frame)
    signals = _ladder_signals(frame)
    sizer = PortfolioSizer(policy=SizingPolicy(risk_fraction=0.01))
    fx = IdentityFxSource()
    paper = run_paper(frame, signals, sizer, fx, FULL_POLICY, series, **_cfg())
    _bt, manager, _adapter, _http = compare(kind)
    assert ledger(manager.book.trades) == ledger(paper.trades)
    assert leg_ledger(manager.book.exit_legs) == leg_ledger(paper.exit_legs)


@pytest.mark.parametrize("kind", VENUES)
@pytest.mark.parametrize("seed", [1, 7, 99])
def test_parity_across_random_price_paths(kind: str, seed: int) -> None:
    bt, manager, _adapter, _http = compare(kind, seed=seed)
    assert ledger(manager.book.trades) == bt.ledger_text()
    assert leg_ledger(manager.book.exit_legs) == bt.leg_ledger_text()


@pytest.mark.parametrize("kind", VENUES)
def test_parity_holds_under_the_oanda_profile_too(kind: str) -> None:
    bt, manager, _adapter, _http = compare(kind, profile=OANDA_REALISTIC)
    assert ledger(manager.book.trades) == bt.ledger_text()


def test_the_vocabulary_fixture_exercises_every_branch() -> None:
    """A parity test over a path that never trails proves nothing about trailing."""
    bt, _manager, _adapter, _http = compare("simulated")
    trade_reasons = {t.exit_reason for t in bt.trades}
    leg_reasons = {leg.exit_reason for leg in bt.exit_legs}
    assert "take_profit" in leg_reasons, "no leg ever hit a target"
    assert bt.partial_exits > 0, "no position scaled out"
    assert ExitReason.TRAILING_STOP in trade_reasons, "the trail never moved a stop"
    assert ExitReason.OPPOSITE_SIGNAL in trade_reasons, "nothing ever reversed"
    assert bt.rejections.get("cooldown", 0) > 0, "the cooldown never refused an entry"


# ==========================================================================
# What actually reached the venue
# ==========================================================================


@pytest.mark.parametrize("kind", ["ig", "oanda"])
def test_a_trail_step_becomes_a_real_venue_amendment(kind: str) -> None:
    """The point of the whole exercise: the stop MOVED at the broker.

    Asserted against the venue's own record of what it holds, not against our
    belief about it. A manager that updated its own cache and never sent the
    request would pass every assertion phrased the other way round.
    """
    _bt, manager, _adapter, http = compare(kind)
    amended = [p for p in http.positions.values() if p.amendments]
    assert amended, "no position was ever amended at the venue"

    moved = 0
    for position in amended:
        first = position.amendments[0]["stopLevel"]
        last = position.amendments[-1]["stopLevel"]
        if first is not None and last is not None and first != last:
            moved += 1
    assert moved, "amendments were sent but no stop level ever changed"

    kinds = {t.kind.value for cycle in manager.cycles for t in cycle.telemetry}
    assert "trail" in kinds or "breakeven" in kinds, (
        f"no trail or breakeven instruction was telemetered; saw {sorted(kinds)}"
    )


@pytest.mark.parametrize("kind", ["ig", "oanda"])
def test_a_scale_out_leg_becomes_a_real_partial_close(kind: str) -> None:
    _bt, manager, _adapter, http = compare(kind)
    partial = [p for p in http.positions.values() if len(p.partials) > 1]
    assert partial, "no position was ever closed in more than one piece at the venue"
    scale_outs = [
        t for cycle in manager.cycles for t in cycle.telemetry
        if t.kind.value == "scale_out"
    ]
    assert scale_outs, "no scale-out instruction was telemetered"
    assert all(t.applied for t in scale_outs), [t.error for t in scale_outs]


@pytest.mark.parametrize("kind", VENUES)
def test_every_instruction_is_telemetered(kind: str) -> None:
    """Live-versus-modelled divergence in exit MANAGEMENT must be measurable."""
    _bt, manager, _adapter, _http = compare(kind)
    rows = [t.as_row() for cycle in manager.cycles for t in cycle.telemetry]
    assert rows, "no amendment telemetry was produced at all"
    required = {
        "kind", "applied", "attempts", "latency_ms", "intended_stop",
        "venue_stop_before", "venue_stop_after", "modelled_entry", "venue_entry",
        "entry_divergence",
    }
    assert required <= set(rows[0])
    # The modelled/dealt entry divergence is the thing that makes every
    # derived level approximate, and it is RECORDED rather than reconciled.
    with_entry = [r for r in rows if r["venue_entry"] is not None]
    assert with_entry, "no telemetry row carried the venue's dealt entry price"


@pytest.mark.parametrize("kind", VENUES)
def test_nothing_diverged_silently_on_a_healthy_run(kind: str) -> None:
    """A clean venue produces no unrealised intents. Silence must mean agreement."""
    _bt, manager, _adapter, _http = compare(kind)
    assert manager.unrealised_intents == [], [
        d.summary() for d in manager.unrealised_intents
    ]


@pytest.mark.parametrize("kind", VENUES)
def test_the_venue_is_flat_at_the_end(kind: str) -> None:
    """The book closed everything; so must the account."""
    _bt, manager, adapter, _http = compare(kind)
    assert manager.book.open == []
    assert adapter.positions() == ()


# ==========================================================================
# managed_exit_exposure
# ==========================================================================


def test_a_trail_only_document_exposes_its_whole_size() -> None:
    """``donchian_breakout_atr``'s shape: no target rests at the venue at all.

    This is the highest-exposure case the note in ``position_manager`` names,
    and the number has to reflect that rather than merely the docstring.
    """
    frame = synthetic_frame(n=300, seed=7)
    series = _atr_frame(frame)
    signals = [
        Signal(
            strategy_id="trail_only",
            instrument=SYMBOL,
            timeframe="H1",
            direction=Direction.LONG,
            bar_time=frame.index[5],
            reference_price=float(frame["close"].iloc[5]),
            stop_price=float(frame["close"].iloc[5]) - 0.0030,
        )
    ]
    sizer = PortfolioSizer(policy=SizingPolicy(risk_fraction=0.01))
    fx = IdentityFxSource()
    adapter, _http, _ = make_venue("simulated", IG_REALISTIC)
    manager = build_manager(
        adapter, policy=TRAIL_ONLY_POLICY, profile=IG_REALISTIC, fx=fx
    )
    by_time = {signals[0].bar_time: signals}
    instrument = get_instrument(SYMBOL)
    measured: list[float] = []
    for i, ts in enumerate(frame.index):
        row = frame.iloc[i]
        bar = Bar(ts, float(row["open"]), float(row["high"]), float(row["low"]),
                  float(row["close"]))
        adapter.set_time(ts)
        cycle = manager.on_bar(
            {SYMBOL: bar},
            bar_index=i,
            timestamp=ts,
            series={SYMBOL: {ATR_COLUMN: float(series[ATR_COLUMN].iloc[i])}},
        )
        if manager.book.open:
            measured.append(cycle.exposure.amount)
        for signal in by_time.get(ts, ()):
            size = sizer.size_for(signal, instrument, manager.account(), 1.0)
            order = Order(
                plan_id=signal.signal_id, instrument=SYMBOL,
                direction=signal.direction, size=size, order_type=OrderType.MARKET,
                mode=ExecutionMode.PAPER, client_ref="trail-only-1",
                stop_loss=signal.stop_price, created_at=ts,
            )
            manager.adopt(order, adapter.place_order(order), strategy_id="trail_only")

    assert measured, "the position never opened; the test would prove nothing"
    assert all(x > 0 for x in measured), (
        "a trail-only position has NO target resting at the venue, so its whole "
        "size depends on the worker. The exposure must never read zero."
    )
    last = manager.cycles[-1].exposure
    assert last is not None
    assert set(last.per_strategy) <= {"trail_only"}


def test_a_single_target_document_with_no_trail_exposes_nothing() -> None:
    """The mirror. A dead worker changes nothing about this shape, so it reads 0."""
    frame = synthetic_frame(n=120, seed=7)
    ref = float(frame["close"].iloc[5])
    signals = [
        Signal(
            strategy_id="plain",
            instrument=SYMBOL,
            timeframe="H1",
            direction=Direction.LONG,
            bar_time=frame.index[5],
            reference_price=ref,
            stop_price=ref - 0.0030,
            take_profit_prices=(ref + 0.0060,),
        )
    ]
    fx = IdentityFxSource()
    adapter, _http, _ = make_venue("simulated", IG_REALISTIC)
    manager = build_manager(adapter, policy=ExitPolicy(), profile=IG_REALISTIC, fx=fx)
    sizer = PortfolioSizer(policy=SizingPolicy(risk_fraction=0.01))
    instrument = get_instrument(SYMBOL)
    seen: list[float] = []
    for i, ts in enumerate(frame.index):
        row = frame.iloc[i]
        bar = Bar(ts, float(row["open"]), float(row["high"]), float(row["low"]),
                  float(row["close"]))
        adapter.set_time(ts)
        cycle = manager.on_bar({SYMBOL: bar}, bar_index=i, timestamp=ts)
        if manager.book.open:
            seen.append(cycle.exposure.amount)
        for signal in ({frame.index[5]: signals}.get(ts) or ()):
            size = sizer.size_for(signal, instrument, manager.account(), 1.0)
            order = Order(
                plan_id=signal.signal_id, instrument=SYMBOL,
                direction=signal.direction, size=size, order_type=OrderType.MARKET,
                mode=ExecutionMode.PAPER, client_ref="plain-1",
                stop_loss=signal.stop_price,
                take_profit=signal.take_profit_prices[0],
                take_profit_prices=tuple(signal.take_profit_prices),
                created_at=ts,
            )
            manager.adopt(order, adapter.place_order(order), strategy_id="plain")

    assert seen, "the position never opened"
    assert max(seen) == 0.0, (
        "a stop and one full-size target both rest at the venue, so nothing "
        f"depends on the worker; got {max(seen)}"
    )


def test_a_multi_leg_document_exposes_only_the_unattached_legs() -> None:
    """Leg one rests at the venue; legs two and three are ours."""
    _bt, manager, _adapter, _http = compare("simulated", n=300)
    exposures = [
        c.exposure for c in manager.cycles
        if c.exposure is not None and c.exposure.exposed_positions
    ]
    assert exposures, "no cycle ever reported managed-exit exposure"
    assert all(e.amount > 0 for e in exposures)
    assert all(e.exposed_positions <= e.positions for e in exposures)


def test_ig_amendment_restates_the_limit_it_was_not_given() -> None:
    """IG's whole-set semantics would DELETE the resting target otherwise.

    The adapter compensates by restating whichever level it was not asked to
    change. Without that a trail step silently removes the first scale-out leg
    and the position runs to its stop with nothing banked -- a bug that cannot
    be seen in our own records at all, only in the venue's.
    """
    _bt, _manager, _adapter, http = compare("ig")
    with_both = [
        p for p in http.positions.values()
        if p.amendments and any(a["limitLevel"] is not None for a in p.amendments)
    ]
    assert with_both, "no amendment ever preserved a resting limit"
    for position in with_both:
        # Once a target has been attached, no amendment may drop it while the
        # ladder still has a leg to place.
        seen_target = False
        for amendment in position.amendments:
            if amendment["limitLevel"] is not None:
                seen_target = True
            elif seen_target and not position.closed:
                pytest.fail(
                    f"{position.deal_id}: an amendment removed the resting limit"
                )


def test_the_ig_and_oanda_adapters_send_the_amendment_to_the_right_endpoint() -> None:
    """Payload shape, asserted once, because the venues above trust it."""
    _bt, _m, _a, ig = compare("ig", n=300)
    ig_amends = [c for c in ig.calls if c["method"] == "PUT"]
    assert ig_amends, "the IG adapter never issued a PUT"
    assert all(c["path"].startswith("/positions/otc/") for c in ig_amends)
    assert all("stopLevel" in c["body"] for c in ig_amends)

    _bt, _m, _a, oanda = compare("oanda", n=300)
    oanda_amends = [
        c for c in oanda.calls if c["method"] == "PUT" and c["path"].endswith("/orders")
    ]
    assert oanda_amends, "the OANDA adapter never issued a dependent-order PUT"
    assert all(
        "stopLoss" in c["body"] or "takeProfit" in c["body"] for c in oanda_amends
    )
