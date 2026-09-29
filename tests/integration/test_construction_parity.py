"""Portfolio construction: one allocation, three paths, byte-identical ledgers.

In the style of ``tests/integration/test_venue_position_manager.py`` (the
five-path parity suite) and ``tests/integration/test_lock_parity.py``. The paper
runtime sizes every bar's signals through ``PortfolioConstructor`` against the
venue's own book; the backtest engine, with ``BacktestConfig.construction``,
now does the same against its OWN simulated book. The same bars, the same
signal source and the same construction policy are driven through:

* :class:`~fiboki.backtest.engine.BacktestEngine` with
  :class:`~fiboki.portfolio.engine_policy.BacktestConstructionPolicy`;
* the paper runtime's sizing components, unmodified: ``SignalEvaluator`` over a
  ``RiskContextBuilder.snapshot`` of a :class:`~fiboki.broker.paper.
  PaperBroker`, each plan placed on the venue directly;
* :func:`~fiboki.workers.runtime.build_replay_session`, end to end: the
  worker loop, the risk gateway and the execution service.

Asserted, as text: the size-and-reason ledger (every step's factor and detail
for every signal, and the size it produced), the trade ledger and the per-fill
leg ledger. The fixture is held to firing EVERY drawdown band of the throttle
(de-risk, severe, PAUSE, FLATTEN-required), and each band must first fire on
the same bar on every path; a pass cannot come from a throttle that never bit.

The gateway on the third path runs a limit set whose loss and drawdown limits
are widened (``PARITY_LIMITS``) and every one of its attempts is asserted
ALLOWED: the gateway is not the subject here, and a refusal would make the two
books diverge for a reason that has nothing to do with sizing.
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from fiboki.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    FixedFractionalSizer,
    run_backtest,
)
from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.core.contracts import Order, Signal
from fiboki.core.enums import Direction, ExecutionMode, OrderType
from fiboki.core.money import IdentityFxSource
from fiboki.core.tier import TierReading
from fiboki.marketstate.calendar import InMemoryEconomicCalendar
from fiboki.portfolio.construction import (
    ConstructionConfig,
    PortfolioConstructor,
    StrategyTier,
)
from fiboki.portfolio.engine_policy import allocation_record
from fiboki.portfolio.sizing import SizingPolicy
from fiboki.risk.accounting import correlation_from_frames
from fiboki.risk.killswitch import KillSwitch
from fiboki.risk.limits import PAPER_LIMITS
from fiboki.sim.fills import Bar
from fiboki.sim.profiles import IG_REALISTIC
from fiboki.validation.engine_evaluator import research_construction_policy
from fiboki.workers.base import WorkerStore
from fiboki.workers.live_worker import BarBatch, LiveWorkerConfig
from fiboki.workers.runtime import RiskContextBuilder, SignalEvaluator, build_replay_session
from tests.exec_fixtures import synthetic_frame

SYMBOLS = ("EURUSD", "GBPUSD")
STRATEGY = "construction_parity"
START = 100_000.0
RISK = 0.01
N_BARS = 1500
#: Chosen by searching seeds for a fixture that crosses all four bands in order
#: with a meaningful number of trades (85). GBPUSD is an independent walk, so
#: a signal on one instrument reaches the drawdown step while the other holds a
#: position (a same-instrument signal is dropped at instrument_correlation,
#: rho 1.0, first), which is the only way FLATTEN can fire after PAUSE.
SEEDS = (8, 108)

#: The throttle's bands pulled in so 1500 bars of random walk cross all four,
#: and a PROVEN tier (1.5%, capped at the sizer's 1%) with the "unknown" regime
#: scalar at 1.0 so each trade risks enough to get there. Every other number is
#: construction_v2's.
CONFIG = ConstructionConfig(
    drawdown_derisk_start_pct=2.0,
    drawdown_derisk_factor=0.8,
    drawdown_severe_pct=4.0,
    drawdown_severe_factor=0.5,
    drawdown_pause_pct=6.0,
    drawdown_zero_pct=6.4,
    regime_scalars={"trend": 1.0, "range": 0.7, "high_vol": 0.5, "crisis": 0.25, "unknown": 1.0},
)
TIER = StrategyTier.PROVEN

#: The gateway is not under test on the replay path: its loss and drawdown
#: limits are widened so that none of them can refuse a plan the other two
#: paths dealt. Every attempt is asserted allowed below.
PARITY_LIMITS = PAPER_LIMITS.derive(
    "limits_parity_test",
    max_per_trade_risk_pct=5.0,
    max_account_risk_pct=50.0,
    max_daily_loss_pct=100.0,
    max_weekly_loss_pct=100.0,
    max_total_drawdown_pct=100.0,
)

BANDS = ("derisk", "severe", "pause", "flatten_required")


# ==========================================================================
# Shared inputs
# ==========================================================================


class Breakout:
    """Closed-bar 20-bar breakout; a function of the bars seen so far only."""

    def __call__(self, symbol: str, history: pd.DataFrame, now: pd.Timestamp) -> list[Signal]:
        if len(history) < 24:
            return []
        window = history.iloc[-21:-1]
        close = float(history["close"].iloc[-1])
        hi, lo = float(window["high"].max()), float(window["low"].min())
        risk = max((hi - lo) * 0.5, close * 0.0015)
        if close > hi:
            direction, sign = Direction.LONG, 1.0
        elif close < lo:
            direction, sign = Direction.SHORT, -1.0
        else:
            return []
        return [
            Signal(
                strategy_id=STRATEGY, instrument=symbol, timeframe="H1",
                direction=direction, bar_time=now, reference_price=close,
                stop_price=close - sign * risk,
                take_profit_prices=(close + sign * risk * 1.5,),
            )
        ]

    def on_bar(self, ctx):  # the engine's Strategy protocol; sorted, as the worker is
        out: list[Signal] = []
        for symbol in SYMBOLS:
            if ctx.has_bar(symbol):
                out.extend(self(symbol, ctx.history(symbol), ctx.timestamp))
        return out


def _paper_config() -> PaperConfig:
    return PaperConfig(
        initial_balance=START, account_ccy="USD", profile=IG_REALISTIC,
        max_concurrent=4, max_per_instrument=2, strategy_id=STRATEGY,
    )


def _bar(frame: pd.DataFrame, i: int) -> Bar:
    row = frame.iloc[i]
    return Bar(frame.index[i], float(row["open"]), float(row["high"]), float(row["low"]),
               float(row["close"]))


def _ledger(trades) -> str:
    return BacktestResult(
        trades=list(trades), equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={},
    ).ledger_text()


def _leg_ledger(legs) -> str:
    return BacktestResult(
        trades=[], equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={}, exit_legs=list(legs),
    ).leg_ledger_text()


def _allocation_text(rows) -> str:
    return BacktestResult(
        trades=[], equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={},
        allocation_ledger=list(rows),
    ).allocation_ledger_text()


class _Recorder:
    """Wraps ``SignalEvaluator.evaluate`` to keep the plans it returned, per bar.

    Instrumentation only: the evaluator runs unmodified and its plans are passed
    on untouched.
    """

    def __init__(self, evaluator: SignalEvaluator) -> None:
        self.evaluator = evaluator
        self.plans: dict[str, float] = {}
        inner = evaluator.evaluate

        def evaluate(instruments, batch):
            plans = list(inner(instruments, batch))
            for plan in plans:
                self.plans[plan.signal.signal_id] = plan.size
            return plans

        evaluator.evaluate = evaluate  # type: ignore[method-assign]

    def rows(self) -> list[dict]:
        """The paper side's size-and-reason ledger, in the engine's row shape."""
        out = []
        for result in self.evaluator.allocations:
            # The constructor orders a batch by (strategy, instrument, id); one
            # strategy and at most one signal per instrument per bar make that
            # the engine's emission order (instruments sorted).
            assert len({a.candidate.symbol for a in result.allocations}) == len(
                result.allocations
            )
            for a in result.allocations:
                row = {**allocation_record(a), "bar_time": result.as_of.isoformat()}
                size = self.plans.get(a.signal_id)
                if a.dropped or a.weight <= 0:
                    row.update(size=0.0, outcome="allocation_dropped")
                elif size is None:
                    row.update(size=0.0, outcome="sized_to_zero")
                else:
                    row.update(size=size, outcome="queued")
                out.append(row)
        return out


def first_fire(rows) -> dict[str, str]:
    """Bar time at which each drawdown band first appears in a ledger."""
    out: dict[str, str] = {}
    for row in rows:
        for step, _factor, detail in row["trace"]:
            if step != "drawdown":
                continue
            for band in BANDS:
                if detail.endswith(f":{band}") and band not in out:
                    out[band] = row["bar_time"]
    return out


# ==========================================================================
# The three paths
# ==========================================================================


def run_engine(frames: dict[str, pd.DataFrame]) -> BacktestResult:
    policy = dataclasses.replace(
        research_construction_policy(), config=CONFIG, tier=TIER,
        # The matrix the replay session measures (over the whole replay, which
        # is its stated approximation), handed to the engine so both see one.
        correlation=correlation_from_frames(frames),
    )
    return run_backtest(
        data=dict(frames),
        config=BacktestConfig(
            initial_balance=START, account_ccy="USD", profile=IG_REALISTIC,
            max_concurrent=4, max_per_instrument=2, strategy_id=STRATEGY,
            construction=policy,
        ),
        strategy=Breakout(),
        sizer=FixedFractionalSizer(risk_fraction=RISK),
        fx=IdentityFxSource(),
    )


def run_paper_components(frames: dict[str, pd.DataFrame]):
    """The runtime's sizing path, unmodified, over the paper venue's own book."""
    fx = IdentityFxSource()
    broker = PaperBroker(config=_paper_config(), fx=fx)
    for symbol in SYMBOLS:
        broker.set_bar_interval(
            symbol, frames[symbol].index.to_series().diff().dropna().median()
        )
    frame = frames[SYMBOLS[0]]
    assert all(f.index.equals(frame.index) for f in frames.values())
    cursor = {"n": 0}

    def equity_curve() -> pd.Series:  # build_replay_session's _equity_curve
        rows = broker.equity_curve
        return pd.Series(
            [float(r["equity"]) for r in rows],
            index=pd.DatetimeIndex([r["timestamp"] for r in rows]),
        )

    builder = RiskContextBuilder(
        adapter=broker, clock=lambda: frame.index[max(cursor["n"] - 1, 0)], fx=fx,
        account_ccy="USD", correlation=correlation_from_frames(frames),
        equity_curve=equity_curve,
    )
    evaluator = SignalEvaluator(
        source=Breakout(), account=broker.account, fx=fx,
        policy=SizingPolicy(risk_fraction=RISK),
        history=lambda sym: frames[sym].iloc[: cursor["n"]], account_ccy="USD",
        constructor=PortfolioConstructor(
            "equal_risk", CONFIG, agent_tier=TierReading.default("parity")
        ),
        snapshot=builder.snapshot, tier_source=lambda _sid: TIER,
    )
    recorder = _Recorder(evaluator)
    bars: dict[str, Bar] = {}
    for i in range(len(frame)):
        bars = {symbol: _bar(frames[symbol], i) for symbol in SYMBOLS}
        cursor["n"] = i + 1
        broker.on_bar(bars, bar_index=i, timestamp=frame.index[i])
        batch = BarBatch(frames=bars, closed_instruments=frozenset(SYMBOLS))
        for plan in evaluator.evaluate(list(SYMBOLS), batch):
            signal = plan.signal
            broker.register_plan(plan.plan_id, signal.strategy_id)
            broker.place_order(
                Order(
                    plan_id=plan.plan_id, instrument=signal.instrument,
                    direction=signal.direction,
                    size=plan.size, order_type=OrderType.MARKET, mode=ExecutionMode.PAPER,
                    client_ref=f"paper-{plan.plan_id}", stop_loss=signal.stop_price,
                    take_profit=signal.take_profit_prices[0],
                    take_profit_prices=tuple(signal.take_profit_prices),
                    created_at=frame.index[i],
                )
            )
    broker.finish(bars)
    return broker, recorder


def run_replay_session(frames: dict[str, pd.DataFrame], tmp_path):
    """``build_replay_session``: worker, gateway, execution service, venue."""
    with WorkerStore.sqlite_at(tmp_path / "workers.sqlite") as store:
        session = build_replay_session(
            frames=dict(frames),
            signal_source=Breakout(),
            store=store,
            paper_config=_paper_config(),
            sizing_policy=SizingPolicy(risk_fraction=RISK),
            fx=IdentityFxSource(),
            timeframe="H1",
            warmup=0,
            limits=PARITY_LIMITS,
            # The documented way to run with no event source: an explicitly
            # empty calendar plus the opt-out. The engine here has no blackout
            # either; with the official calendar the gateway (correctly)
            # refuses entries near releases and the books diverge for a
            # reason that is not sizing.
            calendar=InMemoryEconomicCalendar([]),
            allow_empty_calendar=True,
            kill_switch=KillSwitch.at_path(tmp_path / "killswitch.jsonl"),
            construction=CONFIG,
            tier_source=lambda _sid: TIER,
            worker_config=LiveWorkerConfig(
                max_cycles=N_BARS, idle_sleep_seconds=0.0, busy_sleep_seconds=0.0,
                reconcile_every_cycles=10_000_000, lifecycle_every_cycles=10_000_000,
                lease_name=f"construction-parity-{id(frames)}",
            ),
        )
        recorder = _Recorder(session.evaluator)
        assert session.worker.run(install_signals=False) == 0
        session.broker.finish({s: _bar(frames[s], len(frames[s]) - 1) for s in SYMBOLS})
        return session, recorder


# ==========================================================================
# Fixtures
# ==========================================================================


def make_frames(seeds: tuple[int, int] = SEEDS, n: int = N_BARS) -> dict[str, pd.DataFrame]:
    """Two independent random walks on one hourly index (both quoted in USD)."""
    return {symbol: synthetic_frame(n=n, seed=seed) for symbol, seed in zip(SYMBOLS, seeds, strict=True)}


@pytest.fixture(scope="module")
def frames() -> dict[str, pd.DataFrame]:
    return make_frames()


@pytest.fixture(scope="module")
def engine(frames) -> BacktestResult:
    return run_engine(frames)


@pytest.fixture(scope="module")
def paper(frames):
    return run_paper_components(frames)


# ==========================================================================
# The tests
# ==========================================================================


def test_the_fixture_fires_every_drawdown_band(engine) -> None:
    fired = first_fire(engine.allocation_ledger)
    assert set(fired) == set(BANDS), fired
    assert fired["derisk"] < fired["severe"] < fired["pause"] < fired["flatten_required"]
    outcomes = {row["outcome"] for row in engine.allocation_ledger}
    assert {"queued", "allocation_dropped"} <= outcomes
    assert len(engine.trades) >= 20
    weights = {round(row["weight"], 6) for row in engine.allocation_ledger if not row["dropped"]}
    assert len(weights) >= 3, f"the throttle never changed a weight: {weights}"
    assert engine.config_fingerprint["construction"]["construction_version"] == "construction_v2"


def test_paper_components_match_the_engine(engine, paper) -> None:
    broker, recorder = paper
    rows = recorder.rows()
    assert _allocation_text(rows) == engine.allocation_ledger_text()
    assert _ledger(broker.trades) == engine.ledger_text()
    assert _leg_ledger(broker.exit_legs) == engine.leg_ledger_text()
    assert first_fire(rows) == first_fire(engine.allocation_ledger)


def test_the_replay_session_matches_the_engine(frames, engine, tmp_path) -> None:
    session, recorder = run_replay_session(frames, tmp_path)
    attempts = list(session.gateway.recorder.attempts)
    assert attempts and all(a.allowed for a in attempts), [
        a.decision.reasons for a in attempts if not a.allowed
    ]
    rows = recorder.rows()
    assert _allocation_text(rows) == engine.allocation_ledger_text()
    assert _ledger(session.broker.trades) == engine.ledger_text()
    assert _leg_ledger(session.broker.exit_legs) == engine.leg_ledger_text()
    assert first_fire(rows) == first_fire(engine.allocation_ledger)


def test_each_trade_carries_the_decision_that_opened_it(engine) -> None:
    linked = engine.trade_allocations()
    assert len(linked) == len(engine.trades) and all(x is not None for x in linked)
    frame = engine.ledger_frame()
    assert list(frame["allocation"]) == linked
    queued = [r for r in engine.allocation_ledger if r["outcome"] == "queued"]
    for trade, index in zip(engine.trades, engine.trade_allocation_index, strict=True):
        row = engine.allocation_ledger[index]
        assert row["outcome"] == "queued"
        assert row["size"] == trade.size
        assert row["instrument"] == trade.instrument
    assert len(queued) >= len(engine.trades)


def test_the_flat_path_would_have_sized_differently(frames, engine) -> None:
    """Otherwise every assertion above could pass with construction switched off."""
    flat = run_backtest(
        data=dict(frames),
        config=BacktestConfig(
            initial_balance=START, account_ccy="USD", profile=IG_REALISTIC,
            max_concurrent=4, max_per_instrument=2, strategy_id=STRATEGY,
        ),
        strategy=Breakout(),
        sizer=FixedFractionalSizer(risk_fraction=RISK),
        fx=IdentityFxSource(),
    )
    assert flat.allocation_ledger == [] and flat.config_fingerprint["construction"] == "none"
    assert flat.ledger_text() != engine.ledger_text()
