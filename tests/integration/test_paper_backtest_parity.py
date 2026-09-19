"""Identical bars through the backtester and the paper adapter: identical output.

V1 had a paper bot and a backtester with separately written execution logic, so
a paper divergence could never be attributed: alpha decay and a code
disagreement looked the same. This is the test that makes paper trading
evidence rather than decoration.

The paper adapter is driven exactly as it would be in production -- one bar at
a time, with no access to the future -- and the resulting trade ledger is
compared byte-for-byte against the backtester's canonical ledger text.
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
from fiboki.broker.paper import PaperBroker, PaperConfig
from fiboki.core.contracts import Order, Signal
from fiboki.core.enums import Direction, ExecutionMode, OrderType, Provenance
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource, SeriesFxSource
from fiboki.portfolio.sizing import PortfolioSizer, SizingPolicy
from fiboki.sim.fills import Bar, IntrabarPolicy
from fiboki.sim.profiles import (
    IDEALISED_RESEARCH,
    IG_REALISTIC,
    OANDA_REALISTIC,
    SEVERE_STRESS,
)
from tests.exec_fixtures import synthetic_frame

SYMBOL = "EURUSD"


def _signals(frame: pd.DataFrame, *, every: int = 17, stop: float = 0.0030,
             target: float = 0.0045) -> list[Signal]:
    out = []
    for i in range(5, len(frame), every):
        ts = frame.index[i]
        ref = float(frame["close"].iloc[i])
        out.append(
            Signal(
                strategy_id="parity_strategy",
                instrument=SYMBOL,
                timeframe="H1",
                direction=Direction.LONG if (i // every) % 2 == 0 else Direction.SHORT,
                bar_time=ts,
                reference_price=ref,
                stop_price=ref - stop if (i // every) % 2 == 0 else ref + stop,
                take_profit_prices=(
                    (ref + target,) if (i // every) % 2 == 0 else (ref - target,)
                ),
            )
        )
    return out


def _run_backtest(frame, signals, sizer, fx, **cfg) -> BacktestResult:
    return BacktestEngine(
        data={SYMBOL: frame},
        config=BacktestConfig(**cfg),
        strategy=PrecomputedSignals(signals),
        sizer=sizer,
        fx=fx,
    ).run()


def _run_paper(frame, signals, sizer, fx, **cfg) -> PaperBroker:
    """Drive the adapter as production does: one bar, then any new orders."""
    paper = PaperBroker(
        config=PaperConfig(provenance=Provenance.PAPER, **cfg), fx=fx
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
        paper.on_bar({SYMBOL: bar}, bar_index=i, timestamp=ts)

        account = paper.account()
        rate = fx.rate(instrument.quote, cfg.get("account_ccy", "GBP"), ts)
        for signal in by_time.get(ts, ()):
            size = sizer.size_for(signal, instrument, account, rate)
            if size <= 0:
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
                    client_ref=f"parity-{signal.signal_id}",
                    stop_loss=signal.stop_price,
                    take_profit=(
                        signal.take_profit_prices[0] if signal.take_profit_prices else None
                    ),
                    created_at=ts,
                )
            )
    paper.finish({SYMBOL: last_bar})
    return paper


def _ledger(trades) -> str:
    """Reuse the backtester's canonical ledger text for the paper trades too."""
    return BacktestResult(
        trades=list(trades), equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={},
    ).ledger_text()


def _compare(profile, *, account_ccy="USD", fx=None, seed=7, n=400,
             intrabar=IntrabarPolicy.STOP_FIRST, max_concurrent=1, **extra):
    frame = synthetic_frame(n=n, seed=seed)
    signals = _signals(frame)
    sizer = PortfolioSizer(policy=SizingPolicy(risk_fraction=0.01))
    fx = fx or IdentityFxSource()
    cfg = dict(
        initial_balance=50_000.0,
        account_ccy=account_ccy,
        profile=profile,
        intrabar_policy=intrabar,
        max_concurrent=max_concurrent,
        max_per_instrument=max_concurrent,
        strategy_id="parity_strategy",
        **extra,
    )
    bt = _run_backtest(frame, signals, sizer, fx, **cfg)
    paper = _run_paper(frame, signals, sizer, fx, **cfg)
    return bt, paper


# ------------------------------------------------------------ the test


def test_identical_bars_produce_identical_fills_and_pnl() -> None:
    bt, paper = _compare(IG_REALISTIC)

    assert bt.trades, "the fixture produced no trades; the test would prove nothing"
    assert len(paper.trades) == len(bt.trades)
    assert _ledger(paper.trades) == bt.ledger_text(), (
        "the paper adapter and the backtester disagree about the trade ledger"
    )


def test_final_equity_matches_exactly() -> None:
    bt, paper = _compare(IG_REALISTIC)
    assert paper.balance == bt.final_equity


def test_entry_and_exit_prices_match_trade_by_trade() -> None:
    bt, paper = _compare(IG_REALISTIC)
    for mine, theirs in zip(paper.trades, bt.trades, strict=True):
        assert mine.entry_price == theirs.entry_price
        assert mine.exit_price == theirs.exit_price
        assert mine.entry_time == theirs.entry_time
        assert mine.exit_time == theirs.exit_time
        assert mine.exit_reason is theirs.exit_reason
        assert mine.size == theirs.size


def test_every_cost_component_matches_not_just_the_total() -> None:
    """Totals can agree while the decomposition disagrees. Check the parts."""
    bt, paper = _compare(IG_REALISTIC)
    for mine, theirs in zip(paper.trades, bt.trades, strict=True):
        assert mine.spread_cost == theirs.spread_cost
        assert mine.commission == theirs.commission
        assert mine.slippage_cost == theirs.slippage_cost
        assert mine.financing_cost == theirs.financing_cost
        assert mine.gross_pnl == theirs.gross_pnl
        assert mine.net_pnl == theirs.net_pnl


def test_rejection_counts_match() -> None:
    """A divergence in WHAT was refused is a divergence, even with equal P&L."""
    bt, paper = _compare(IG_REALISTIC)
    assert paper.rejections == bt.rejections


@pytest.mark.parametrize(
    "profile", [IDEALISED_RESEARCH, IG_REALISTIC, OANDA_REALISTIC, SEVERE_STRESS]
)
def test_parity_holds_across_every_execution_profile(profile) -> None:
    """Including SEVERE_STRESS, which has latency, partial fills and rejections."""
    bt, paper = _compare(profile)
    assert _ledger(paper.trades) == bt.ledger_text()
    assert paper.rejections == bt.rejections


@pytest.mark.parametrize("policy", list(IntrabarPolicy))
def test_parity_holds_across_every_intrabar_policy(policy) -> None:
    bt, paper = _compare(IG_REALISTIC, intrabar=policy)
    assert _ledger(paper.trades) == bt.ledger_text()


@pytest.mark.parametrize("seed", [1, 7, 99, 20260919])
def test_parity_holds_across_random_price_paths(seed: int) -> None:
    bt, paper = _compare(IG_REALISTIC, seed=seed)
    assert _ledger(paper.trades) == bt.ledger_text()


def test_parity_holds_with_multiple_concurrent_positions() -> None:
    bt, paper = _compare(IG_REALISTIC, max_concurrent=3)
    assert len(bt.trades) > 0
    assert _ledger(paper.trades) == bt.ledger_text()


def test_parity_holds_with_a_real_fx_conversion() -> None:
    """A GBP account trading a USD-quoted instrument: the V1 currency bug's home."""
    frame = synthetic_frame(n=400, seed=7)
    rates = pd.Series(
        1.2500 + 0.00001 * pd.Series(range(len(frame))).to_numpy(), index=frame.index
    )
    fx = SeriesFxSource({"GBPUSD": rates})
    bt, paper = _compare(IG_REALISTIC, account_ccy="GBP", fx=fx)
    assert _ledger(paper.trades) == bt.ledger_text()
    assert all(t.fx_rate_used != 1.0 for t in paper.trades)


def test_parity_holds_when_financing_is_charged() -> None:
    bt, paper = _compare(IG_REALISTIC, n=900, charge_financing=True)
    assert any(t.financing_cost != 0 for t in bt.trades), "no financing was charged"
    assert _ledger(paper.trades) == bt.ledger_text()


def test_the_paper_adapter_shares_the_backtesters_fill_simulator() -> None:
    """Structural parity, not a reimplementation."""
    from fiboki.sim.fills import FillSimulator

    paper = PaperBroker(
        config=PaperConfig(initial_balance=10_000.0, profile=IG_REALISTIC),
        fx=IdentityFxSource(),
    )
    assert isinstance(paper.sim, FillSimulator)
    assert paper.sim.profile is IG_REALISTIC


def test_the_paper_adapter_cannot_replay_a_bar() -> None:
    """Replaying a bar would double-charge financing and desynchronise the RNG."""
    paper = PaperBroker(
        config=PaperConfig(initial_balance=10_000.0), fx=IdentityFxSource()
    )
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    bar = Bar(ts, 1.1, 1.11, 1.09, 1.10)
    paper.on_bar({SYMBOL: bar}, bar_index=0, timestamp=ts)
    with pytest.raises(ValueError, match="monotonic"):
        paper.on_bar({SYMBOL: bar}, bar_index=0, timestamp=ts)


def test_the_paper_adapter_refuses_an_order_before_it_has_a_price() -> None:
    paper = PaperBroker(
        config=PaperConfig(initial_balance=10_000.0), fx=IdentityFxSource()
    )
    order = Order(
        plan_id="p", instrument=SYMBOL, direction=Direction.LONG, size=1000.0,
        order_type=OrderType.MARKET, mode=ExecutionMode.PAPER, client_ref="c1",
    )
    with pytest.raises(ValueError, match="no price"):
        paper.place_order(order)


# ==========================================================================
# The full exit vocabulary
# ==========================================================================
#
# Everything above this line predates ``backtest/exits.py``. It compares a
# stop-and-one-target realisation of a strategy, which is the ONLY thing the
# paper adapter could express before the position book was shared -- so it kept
# passing while paper and backtest had drifted into two different strategies.
# A parity test that can only agree about the degenerate case is not evidence.
#
# What follows drives the SAME bars through both paths with a multi-leg ladder,
# a trailing stop, a breakeven rule, a time stop, a cooldown and a reversal, and
# compares the trade ledger, the per-FILL leg ledger and the rejection counts
# byte for byte.

from fiboki.backtest.exits import (  # noqa: E402
    EventBlackout,
    ExitPolicy,
    ReversalMode,
    TrailKind,
    TrailSpec,
)
from fiboki.core.enums import ExitReason  # noqa: E402

ATR_COLUMN = "atr14"


def _atr_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """A true-range EMA, computed once and handed to BOTH paths.

    The engine takes it as an ``exit_series`` frame; the paper adapter is handed
    the same row, one bar at a time. Neither computes it, which is the point:
    an indicator the two sides derive separately is a second source of truth.
    """
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
    """Signals with a THREE-leg take-profit ladder and explicit allocations."""
    out: list[Signal] = []
    for i in range(5, len(frame), every):
        ts = frame.index[i]
        ref = float(frame["close"].iloc[i])
        long = (i // every) % 2 == 0
        sign = 1.0 if long else -1.0
        out.append(
            Signal(
                strategy_id="parity_strategy",
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


#: Every branch of the vocabulary at once: three-leg scale-out, an ATR
#: chandelier that only arms after 0.5R, breakeven at 0.25R, a 40-bar time stop,
#: a 3-bar cooldown and reversal on an opposite signal.
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


def _run_backtest_policy(frame, signals, sizer, fx, policy, series, **cfg):
    return BacktestEngine(
        data={SYMBOL: frame},
        config=BacktestConfig(**cfg),
        strategy=PrecomputedSignals(signals),
        sizer=sizer,
        fx=fx,
        exit_policy=policy,
        exit_series={SYMBOL: series} if series is not None else None,
    ).run()


def _run_paper_policy(frame, signals, sizer, fx, policy, series, **cfg) -> PaperBroker:
    """Drive the adapter exactly as the live worker does: bar, then orders."""
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
            # The reversal close is scheduled by the BOOK, on the same bar and
            # with the same sequence number the engine would have used, which is
            # why it happens before the replacement order is queued.
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
                    client_ref=f"vocab-{signal.signal_id}",
                    stop_loss=signal.stop_price,
                    take_profit=(
                        signal.take_profit_prices[0]
                        if signal.take_profit_prices
                        else None
                    ),
                    take_profit_prices=tuple(signal.take_profit_prices),
                    take_profit_allocations=tuple(signal.take_profit_allocations),
                    created_at=ts,
                )
            )
    paper.finish({SYMBOL: last_bar})
    return paper


def _compare_vocabulary(
    policy: ExitPolicy,
    *,
    profile=IG_REALISTIC,
    seed: int = 7,
    n: int = 900,
    signals=None,
    with_series: bool = True,
    account_ccy: str = "USD",
    fx=None,
    max_concurrent: int = 1,
    intrabar=IntrabarPolicy.STOP_FIRST,
    **extra,
):
    frame = synthetic_frame(n=n, seed=seed)
    series = _atr_frame(frame) if with_series else None
    signals = _ladder_signals(frame) if signals is None else signals
    sizer = PortfolioSizer(policy=SizingPolicy(risk_fraction=0.01))
    fx = fx or IdentityFxSource()
    cfg = dict(
        initial_balance=50_000.0,
        account_ccy=account_ccy,
        profile=profile,
        intrabar_policy=intrabar,
        max_concurrent=max_concurrent,
        max_per_instrument=max_concurrent,
        strategy_id="parity_strategy",
        **extra,
    )
    bt = _run_backtest_policy(frame, signals, sizer, fx, policy, series, **cfg)
    paper = _run_paper_policy(frame, signals, sizer, fx, policy, series, **cfg)
    return bt, paper


def _leg_ledger(legs) -> str:
    return BacktestResult(
        trades=[], equity_curve=pd.DataFrame(), costs=None,
        config_fingerprint={}, data_fingerprint={}, rejections={},
        exit_legs=list(legs),
    ).leg_ledger_text()


def _assert_parity(bt, paper) -> None:
    assert _ledger(paper.trades) == bt.ledger_text(), (
        "the paper adapter and the backtester disagree about the TRADE ledger"
    )
    assert _leg_ledger(paper.exit_legs) == bt.leg_ledger_text(), (
        "the trade ledgers agree but the per-FILL ledgers do not: the two paths "
        "reached the same totals by scaling out differently"
    )
    assert paper.rejections == bt.rejections
    assert paper.balance == bt.final_equity


def test_the_paper_adapter_shares_the_engines_position_book() -> None:
    """Structural parity: one class decides exits on both paths."""
    from fiboki.backtest.position import PositionBook

    paper = PaperBroker(
        config=PaperConfig(initial_balance=10_000.0, exit_policy=FULL_POLICY),
        fx=IdentityFxSource(),
    )
    assert isinstance(paper.book, PositionBook)
    assert paper.book.policy is FULL_POLICY


def test_parity_across_the_whole_exit_vocabulary() -> None:
    bt, paper = _compare_vocabulary(FULL_POLICY)
    assert bt.trades, "the fixture produced no trades; the test would prove nothing"
    assert bt.partial_exits > 0, "no position scaled out; the ladder was not exercised"
    _assert_parity(bt, paper)


def test_the_vocabulary_fixture_actually_exercises_every_branch() -> None:
    """A parity test over a path that never trails proves nothing about trailing.

    Asserted against the LEG ledger as well as the trade ledger, because a
    scale-out is a take-profit that is not the reason the position finally
    closed. The time stop is exercised by its own case below: under this policy
    the trail or a reversal always gets there first, and forcing it here would
    mean picking a bar count to make an assertion true.
    """
    bt, _ = _compare_vocabulary(FULL_POLICY)
    trade_reasons = {t.exit_reason for t in bt.trades}
    leg_reasons = {leg.exit_reason for leg in bt.exit_legs}
    assert "take_profit" in leg_reasons, "no leg ever hit a target"
    assert bt.partial_exits > 0, "no position scaled out"
    assert ExitReason.TRAILING_STOP in trade_reasons, "the trail never moved a stop"
    assert ExitReason.OPPOSITE_SIGNAL in trade_reasons, "nothing ever reversed"
    assert bt.rejections.get("cooldown", 0) > 0, "the cooldown never refused an entry"


@pytest.mark.parametrize(
    "profile", [IDEALISED_RESEARCH, IG_REALISTIC, OANDA_REALISTIC, SEVERE_STRESS]
)
def test_vocabulary_parity_across_every_execution_profile(profile) -> None:
    bt, paper = _compare_vocabulary(FULL_POLICY, profile=profile)
    _assert_parity(bt, paper)


@pytest.mark.parametrize("policy_intrabar", list(IntrabarPolicy))
def test_vocabulary_parity_across_every_intrabar_policy(policy_intrabar) -> None:
    bt, paper = _compare_vocabulary(FULL_POLICY, intrabar=policy_intrabar)
    _assert_parity(bt, paper)


@pytest.mark.parametrize("seed", [1, 7, 99, 20260919])
def test_vocabulary_parity_across_random_price_paths(seed: int) -> None:
    bt, paper = _compare_vocabulary(FULL_POLICY, seed=seed)
    _assert_parity(bt, paper)


def test_trailing_alone_is_in_parity() -> None:
    """``donchian_breakout_atr``'s shape: a chandelier and NO take-profit."""
    frame = synthetic_frame(n=900, seed=7)
    signals = [
        dataclasses_replace_take_profits(s) for s in _ladder_signals(frame)
    ]
    policy = ExitPolicy(
        trailing=TrailSpec(
            kind=TrailKind.ATR_CHANDELIER,
            value=3.0,
            activate_after_r=0.0,
            atr_column=ATR_COLUMN,
        )
    )
    bt, paper = _compare_vocabulary(policy, signals=signals)
    assert any(t.exit_reason is ExitReason.TRAILING_STOP for t in bt.trades)
    _assert_parity(bt, paper)


def dataclasses_replace_take_profits(signal: Signal) -> Signal:
    import dataclasses

    return dataclasses.replace(
        signal, take_profit_prices=(), take_profit_allocations=()
    )


def test_breakeven_alone_is_in_parity() -> None:
    bt, paper = _compare_vocabulary(
        ExitPolicy(allocations=(0.4, 0.3, 0.3), breakeven_at_r=0.2),
        with_series=False,
    )
    assert bt.trades
    _assert_parity(bt, paper)


def test_time_stop_and_cooldown_alone_are_in_parity() -> None:
    bt, paper = _compare_vocabulary(
        ExitPolicy(max_bars_in_trade=12, cooldown_bars_after_exit=25),
        with_series=False,
    )
    assert any(t.exit_reason is ExitReason.TIME_STOP for t in bt.trades)
    assert bt.rejections.get("cooldown", 0) > 0
    _assert_parity(bt, paper)


def test_reversal_is_in_parity() -> None:
    bt, paper = _compare_vocabulary(
        ExitPolicy(reversal=ReversalMode.REVERSE), with_series=False
    )
    assert any(t.exit_reason is ExitReason.OPPOSITE_SIGNAL for t in bt.trades)
    _assert_parity(bt, paper)


def test_close_only_reversal_is_in_parity() -> None:
    bt, paper = _compare_vocabulary(
        ExitPolicy(reversal=ReversalMode.CLOSE_ONLY), with_series=False
    )
    assert bt.rejections.get("reversal_close_only", 0) > 0
    _assert_parity(bt, paper)


def test_event_blackout_is_in_parity() -> None:
    """``avoid_rollover_hour`` removes one entry bar in six on an H1 clock."""
    bt, paper = _compare_vocabulary(
        ExitPolicy(events=EventBlackout(avoid_rollover_hour=True, avoid_month_end=True)),
        with_series=False,
    )
    assert bt.rejections.get("rollover_hour", 0) > 0
    _assert_parity(bt, paper)


def test_vocabulary_parity_with_financing_and_a_real_fx_conversion() -> None:
    frame = synthetic_frame(n=900, seed=7)
    rates = pd.Series(
        1.2500 + 0.00001 * pd.Series(range(len(frame))).to_numpy(), index=frame.index
    )
    bt, paper = _compare_vocabulary(
        FULL_POLICY,
        account_ccy="GBP",
        fx=SeriesFxSource({"GBPUSD": rates}),
        charge_financing=True,
    )
    assert any(t.financing_cost != 0 for t in bt.trades), "no financing was charged"
    assert all(t.fx_rate_used != 1.0 for t in paper.trades)
    _assert_parity(bt, paper)


def test_vocabulary_parity_with_multiple_concurrent_positions() -> None:
    bt, paper = _compare_vocabulary(FULL_POLICY, max_concurrent=3)
    assert bt.trades
    _assert_parity(bt, paper)
