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
