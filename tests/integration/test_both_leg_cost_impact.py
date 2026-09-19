"""Measure, on REAL EURUSD H4 data, what charging the exit leg actually costs.

This test is a measurement harness as much as an assertion. V1 called
``_apply_costs`` on the entry path only, so closing a position was free. The
question "how much did that flatter the results?" has a number, and it belongs
in the test suite rather than in a claim in a commit message.

Method
------
The V1 bug is emulated by patching :meth:`FillSimulator._price_exit` to return
an empty cost object. Everything else is held fixed, and sizing is CONSTANT
(``FixedSizeSizer``) so that both runs take exactly the same trades at exactly
the same prices. The only difference between the two ledgers is the exit leg's
cost, which isolates the effect completely.

The strategy is a plain Donchian breakout chosen from a small grid because it
is PROFITABLE before costs on this sample. That selection is in-sample and this
is NOT a claim of edge — a losing baseline simply makes "percentage of profit
removed" meaningless, which is the only reason the parameters matter here.

Skipped automatically when the sample data is not present.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fiboki.backtest.engine import (
    BacktestConfig,
    FixedSizeSizer,
    PrecomputedSignals,
    run_backtest,
)
from fiboki.core.contracts import Signal
from fiboki.core.enums import Direction
from fiboki.core.money import IdentityFxSource
from fiboki.sim.profiles import (
    IG_REALISTIC,
    CommissionModel,
    FinancingModel,
)

DATA = Path("/home/claude/fiboki/data/canonical/histdata/EURUSD/eurusd_h4.parquet")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not DATA.exists(), reason=f"sample data not present at {DATA}"),
]

LOOKBACK = 60
STOP_FRACTION = 0.5
RR = 1.0
SIZE = 10_000


@pytest.fixture(scope="module")
def eurusd_h4() -> pd.DataFrame:
    frame = pd.read_parquet(DATA)[["open", "high", "low", "close"]].astype(float)
    assert frame.index.tz is not None, "sample data must be tz-aware UTC"
    return frame


def _donchian_signals(frame: pd.DataFrame) -> list[Signal]:
    """Breakout of the prior ``LOOKBACK``-bar range. Strictly causal.

    The rolling window is shifted by one bar before it is compared with the
    close, so a bar never breaks out of a range that includes itself.
    """
    high = pd.Series(frame["high"].to_numpy())
    low = pd.Series(frame["low"].to_numpy())
    close = frame["close"].to_numpy()
    idx = frame.index

    hi = np.concatenate([[np.nan], high.rolling(LOOKBACK).max().to_numpy()[:-1]])
    lo = np.concatenate([[np.nan], low.rolling(LOOKBACK).min().to_numpy()[:-1]])

    out: list[Signal] = []
    for i in range(len(close)):
        span = hi[i] - lo[i]
        if not np.isfinite(span) or span <= 0:
            continue
        c = float(close[i])
        stop_distance = span * STOP_FRACTION
        if stop_distance <= 0 or stop_distance >= c:
            continue
        if c > hi[i]:
            out.append(Signal(
                strategy_id="donchian", instrument="EURUSD", timeframe="H4",
                direction=Direction.LONG, bar_time=idx[i], reference_price=c,
                stop_price=c - stop_distance,
                take_profit_prices=(c + stop_distance * RR,),
            ))
        elif c < lo[i]:
            out.append(Signal(
                strategy_id="donchian", instrument="EURUSD", timeframe="H4",
                direction=Direction.SHORT, bar_time=idx[i], reference_price=c,
                stop_price=c + stop_distance,
                take_profit_prices=(c - stop_distance * RR,),
            ))
    return out


def _run(frame, signals, profile, *, emulate_v1_free_exit: bool):
    import fiboki.sim.fills as fills_module
    from fiboki.sim.fills import LegCosts

    original = fills_module.FillSimulator._price_exit
    if emulate_v1_free_exit:
        def free_exit(self, *args, **kwargs):
            return dataclasses.replace(original(self, *args, **kwargs), costs=LegCosts())

        fills_module.FillSimulator._price_exit = free_exit
    try:
        cfg = BacktestConfig(
            initial_balance=100_000.0,
            account_ccy="USD",           # == quote ccy, so IdentityFxSource is exact
            profile=profile,
            max_concurrent=1,
            strategy_id="donchian",
        )
        return run_backtest(
            data={"EURUSD": frame},
            config=cfg,
            strategy=PrecomputedSignals(signals),
            sizer=FixedSizeSizer(SIZE),
            fx=IdentityFxSource(),
        )
    finally:
        fills_module.FillSimulator._price_exit = original


SPREAD_ONLY = dataclasses.replace(
    IG_REALISTIC,
    commission=CommissionModel(),
    financing=FinancingModel(),
    name="IG_SPREAD_ONLY",
)


@pytest.fixture(scope="module")
def measured(eurusd_h4):
    signals = _donchian_signals(eurusd_h4)
    out = {}
    for label, profile in (("spread_only", SPREAD_ONLY), ("full_ig", IG_REALISTIC)):
        both = _run(eurusd_h4, signals, profile, emulate_v1_free_exit=False)
        entry_only = _run(eurusd_h4, signals, profile, emulate_v1_free_exit=True)
        out[label] = (both, entry_only)
    return out


def test_the_sample_is_large_enough_to_mean_anything(measured):
    both, _ = measured["spread_only"]
    assert len(both.trades) >= 80, (
        "Fiboki's primary ranking threshold is 80 trades; a cost measurement "
        f"on fewer is noise. Got {len(both.trades)}."
    )
    span_years = (
        both.equity_curve.index[-1] - both.equity_curve.index[0]
    ).total_seconds() / (365.25 * 24 * 3600)
    assert span_years > 20


def test_both_runs_take_identical_trades_so_only_the_cost_differs(measured):
    """The measurement is only clean if nothing but the exit cost moved."""
    for label in ("spread_only", "full_ig"):
        both, entry_only = measured[label]
        assert len(both.trades) == len(entry_only.trades)
        for a, b in zip(both.trades, entry_only.trades, strict=True):
            assert a.entry_time == b.entry_time
            assert a.exit_time == b.exit_time
            assert a.entry_price == b.entry_price
            assert a.exit_price == b.exit_price
            assert a.gross_pnl == pytest.approx(b.gross_pnl, abs=1e-9)


def test_the_exit_leg_is_charged_and_is_material(measured):
    """
    The headline measurement. Reported as an absolute figure and as a share of
    the profit the V1 accounting would have claimed.
    """
    lines = []
    for label in ("spread_only", "full_ig"):
        both, entry_only = measured[label]
        net_both = sum(t.net_pnl for t in both.trades)
        net_entry = sum(t.net_pnl for t in entry_only.trades)
        gross = sum(t.gross_pnl for t in both.trades)
        removed = net_entry - net_both
        lines.append(
            f"{label:12s} trades={len(both.trades):4d} gross={gross:10.2f} "
            f"net_v1={net_entry:10.2f} net_v2={net_both:10.2f} "
            f"exit_leg_cost={removed:9.2f} "
            f"({removed / net_entry * 100:6.1f}% of the V1 net)"
        )
        assert both.costs.exit_leg_spread > 0.0, "exit leg was not charged at all"
        assert removed > 0.0
        assert net_both < net_entry

    print("\n" + "\n".join(lines))


def test_the_two_legs_cost_the_same_order_of_magnitude(measured):
    """
    They are not identical -- the spread model widens at certain UTC hours and
    across stale (weekend) gaps, and entries and exits do not share an hour
    distribution -- but a factor-of-two-or-more gap would indicate one leg is
    being partly skipped, which is the failure mode this suite exists for.
    """
    both, _ = measured["spread_only"]
    ratio = both.costs.exit_leg_spread / both.costs.entry_leg_spread
    assert 0.5 < ratio < 2.0, (
        f"entry leg {both.costs.entry_leg_spread:.2f} vs exit leg "
        f"{both.costs.exit_leg_spread:.2f} (ratio {ratio:.2f})"
    )


def test_net_equals_gross_minus_costs_for_every_single_trade(measured):
    """The decomposition identity, checked across ~600 real trades."""
    both, _ = measured["full_ig"]
    for t in both.trades:
        expected = (
            t.gross_pnl - t.spread_cost - t.commission - t.slippage_cost - t.financing_cost
        )
        assert t.net_pnl == pytest.approx(expected, abs=1e-9)


def test_metrics_can_be_computed_on_the_real_run(measured):
    from fiboki.backtest.metrics import compute_metrics

    both, _ = measured["full_ig"]
    m = compute_metrics(
        trades=both.trades,
        equity_curve=both.equity_curve,
        initial_equity=100_000.0,
        strict=True,
    )
    # Annualisation MEASURED, not assumed. The naive figure for H4 is
    # 365.25 * 24 / 4 = 2191 bars/year, but FX trades ~120 of 168 hours a week,
    # so the real sample contains 40,541 bars over 25.59 years = ~1584/year --
    # 28% below the naive number. A hardcoded constant (V1 used sqrt(252))
    # would mis-annualise every Sharpe on this data set.
    from fiboki.core.enums import Timeframe

    assert 1450 < m.bars_per_year < 1750, m.bars_per_year
    assert m.bars_per_year == pytest.approx(Timeframe.H4.bars_per_year, rel=0.10), (
        "measured bar frequency disagrees with the registry's FX-week estimate"
    )
    assert m.bars_per_year < 2191 * 0.85
    assert m.n_trades == len(both.trades)
    assert m.max_drawdown_pct < 0
    assert m.profit_factor is not None
    assert m.time_in_market_pct is not None
    print(
        f"\nfull_ig: trades={m.n_trades} sharpe={m.sharpe:.3f} "
        f"sharpe_trade={m.sharpe_trade_based:.3f} pf={m.profit_factor:.3f} "
        f"maxDD={m.max_drawdown_pct:.2f}% ulcer={m.ulcer:.2f} "
        f"cost_drag={m.cost_drag_pct_of_gross:.1f}% of gross"
    )
