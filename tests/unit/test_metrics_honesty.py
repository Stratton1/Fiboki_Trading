"""Metrics tests, with the emphasis on refusing to flatter.

Two classes of assertion here:

1. Arithmetic — annualisation derived from real elapsed time, drawdown from the
   mark-to-market curve, profit factor, ulcer index. Hand-checkable.
2. Honesty — every degenerate input must raise under ``strict=True`` and return
   ``None`` under ``strict=False``. None of them may ever return a number that
   makes the strategy look good. That was V1's single most damaging bug: a
   two-trade strategy with no losers scored a perfect profit factor and topped
   the ranking table.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.backtest.metrics import (
    DegenerateMetricError,
    annualisation_factor,
    compute_metrics,
    max_drawdown,
    ulcer_index,
)
from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason


def _curve(values, start="2020-01-01", freq="4h", extra=None):
    idx = pd.date_range(start, periods=len(values), freq=freq, tz="UTC", name="timestamp")
    data = {"equity": np.asarray(values, dtype=float)}
    if extra:
        data.update(extra)
    return pd.DataFrame(data, index=idx)


def _trade(net, *, gross=None, entry="2020-01-01 04:00", exit_="2020-01-01 08:00", mae=-1.0, mfe=2.0):
    gross = net + 2.0 if gross is None else gross
    return Trade(
        instrument="EURUSD", direction=Direction.LONG, size=1000.0,
        entry_price=1.1, exit_price=1.1 + net / 1000.0,
        entry_time=pd.Timestamp(entry, tz="UTC"),
        exit_time=pd.Timestamp(exit_, tz="UTC"),
        exit_reason=ExitReason.TAKE_PROFIT,
        gross_pnl=gross, spread_cost=2.0, commission=0.0, slippage_cost=0.0,
        financing_cost=0.0, net_pnl=net, account_ccy="GBP",
        bars_held=1, max_adverse_excursion=mae, max_favourable_excursion=mfe,
    )


# ==========================================================================
# Annualisation from real elapsed time
# ==========================================================================


def test_annualisation_factor_is_measured_not_assumed():
    """
    One year of H4 bars: 365.25 days * 24h / 4h = 2191.5 bars.
    Feeding 2191.5 observations across exactly one year must give ~2191.5,
    not 252 and not 252 * anything.
    """
    assert annualisation_factor(2191, 1.0) == pytest.approx(2191.0, abs=1e-9)
    assert annualisation_factor(1000, 2.0) == pytest.approx(500.0, abs=1e-9)


def test_the_same_returns_over_different_spans_annualise_differently():
    """
    Identical return SERIES, different calendar spans. A hardcoded sqrt(252)
    would give both the same Sharpe; a measured factor must not.
    """
    rng = np.random.default_rng(0)
    steps = rng.normal(0.0004, 0.004, size=1200)
    equity = 10_000 * np.cumprod(1 + steps)

    hourly = compute_metrics(
        trades=[], equity_curve=_curve(equity, freq="1h"),
        initial_equity=10_000.0, strict=False,
    )
    daily = compute_metrics(
        trades=[], equity_curve=_curve(equity, freq="1D"),
        initial_equity=10_000.0, strict=False,
    )
    # 1200 hours ~ 0.137 years -> ~8766 bars/yr ; 1200 days ~ 3.29 yr -> ~365/yr
    assert hourly.bars_per_year == pytest.approx(8766.0, rel=0.02)
    assert daily.bars_per_year == pytest.approx(365.25, rel=0.02)
    # The BAR-based Sharpe is the one annualised by bars per year, so it is the
    # one this test is about. Since engine_v3_realism the default ``sharpe`` is
    # on daily (17:00 New York) equity and does not depend on the bar size at
    # all -- tests/unit/test_daily_sharpe_lo.py pins that.
    assert hourly.sharpe_bar_based > daily.sharpe_bar_based * 4  # sqrt(8766/365.25) ~ 4.9


def test_trade_frequency_is_derived_from_the_real_span():
    """20 trades over 2 calendar years must report 10 trades/year."""
    trades = [
        _trade(10.0, entry=f"2020-01-{1 + i:02d} 04:00", exit_=f"2020-01-{1 + i:02d} 08:00")
        for i in range(20)
    ]
    idx_days = 2 * 365.25
    curve = _curve(np.linspace(10_000, 11_000, 500), freq=pd.Timedelta(days=idx_days / 499))
    m = compute_metrics(
        trades=trades, equity_curve=curve, initial_equity=10_000.0, strict=False
    )
    assert m.years == pytest.approx(2.0, rel=0.01)
    assert m.trades_per_year == pytest.approx(10.0, rel=0.01)


# ==========================================================================
# Drawdown from the MARK-TO-MARKET curve
# ==========================================================================


def test_max_drawdown_is_hand_checkable():
    """
    Curve: 100, 120, 90, 130, 80, 140
      running peak: 100, 120, 120, 130, 130, 140
      drawdown:       0,   0, -30,   0, -50,   0
      max absolute = -50
      max percent  = -50 / 130 * 100 = -38.4615...%
    """
    abs_dd, pct_dd = max_drawdown(np.array([100.0, 120.0, 90.0, 130.0, 80.0, 140.0]))
    assert abs_dd == pytest.approx(-50.0, abs=1e-12)
    assert pct_dd == pytest.approx(-50.0 / 130.0 * 100.0, abs=1e-9)


def test_drawdown_sees_open_position_risk_that_a_realised_only_curve_misses():
    """
    THE V1 BUG. Realised equity is flat at 10,000 until the trade closes at
    10,050. Mark-to-market equity dips to 9,000 in between.

      realised-only max drawdown : 0
      mark-to-market max drawdown: 9,000 - 10,000 = -1,000 (-10%)

    V1 reported the first number, so every Calmar, ulcer and risk-of-ruin
    figure it published was computed against a drawdown that never happened.
    """
    realised = _curve([10_000, 10_000, 10_000, 10_000, 10_050])
    marked = _curve([10_000, 9_600, 9_000, 9_800, 10_050])

    assert max_drawdown(realised["equity"].to_numpy())[0] == pytest.approx(0.0)
    abs_dd, pct_dd = max_drawdown(marked["equity"].to_numpy())
    assert abs_dd == pytest.approx(-1000.0, abs=1e-9)
    assert pct_dd == pytest.approx(-10.0, abs=1e-9)


def test_ulcer_index_is_hand_checkable():
    """
    Curve 100, 90, 100:
      peaks      100, 100, 100
      dd percent   0, -10,   0
      ulcer = sqrt((0 + 100 + 0) / 3) = sqrt(33.333...) = 5.7735...
    """
    assert ulcer_index(np.array([100.0, 90.0, 100.0])) == pytest.approx(
        np.sqrt(100.0 / 3.0), abs=1e-9
    )


# ==========================================================================
# Fail loud, never flatter
# ==========================================================================


def test_profit_factor_with_no_losers_raises_instead_of_scoring_full_marks():
    """The exact V1 failure: two winning TRADES and no losers.

    The equity curve deliberately wobbles so that Sharpe and Sortino are both
    computable -- otherwise this test would pass for the wrong reason, on the
    first degeneracy it happened to hit rather than on the profit factor.
    """
    rng = np.random.default_rng(5)
    equity = 10_000 + np.cumsum(rng.normal(2.0, 30.0, 200))
    curve = _curve(equity, extra={"open_positions": np.ones(200, dtype=int)})
    # Different sizes so the trade-based Sharpe is computable too; the ONLY
    # degeneracy left is that not one of them lost money.
    trades = [_trade(50.0), _trade(80.0), _trade(35.0)]
    with pytest.raises(DegenerateMetricError, match="profit_factor"):
        compute_metrics(trades=trades, equity_curve=curve, initial_equity=10_000.0)

    lenient = compute_metrics(
        trades=trades, equity_curve=curve, initial_equity=10_000.0, strict=False
    )
    assert lenient.profit_factor is None
    assert any("profit_factor" in d for d in lenient.degenerate)


def test_profit_factor_is_correct_when_it_is_computable():
    """
    Wins 100 + 50 = 150 ; losses -30 + -20 = -50
    Profit factor = 150 / 50 = 3.0
    Expectancy    = (100 + 50 - 30 - 20) / 4 = 25.0
    Win rate      = 2/4 = 50%
    """
    curve = _curve(np.linspace(10_000, 10_100, 60))
    trades = [_trade(100.0), _trade(50.0), _trade(-30.0), _trade(-20.0)]
    m = compute_metrics(
        trades=trades, equity_curve=curve, initial_equity=10_000.0, strict=False
    )
    assert m.profit_factor == pytest.approx(3.0, abs=1e-12)
    assert m.expectancy == pytest.approx(25.0, abs=1e-12)
    assert m.win_rate_pct == pytest.approx(50.0, abs=1e-12)
    assert m.avg_win == pytest.approx(75.0, abs=1e-12)
    assert m.avg_loss == pytest.approx(-25.0, abs=1e-12)
    assert m.largest_win == pytest.approx(100.0, abs=1e-12)
    assert m.largest_loss == pytest.approx(-30.0, abs=1e-12)


def test_sharpe_on_a_flat_equity_curve_raises():
    """Zero dispersion is undefined, not infinite, and certainly not excellent."""
    flat = _curve([10_000.0] * 100)
    with pytest.raises(DegenerateMetricError, match="sharpe"):
        compute_metrics(trades=[], equity_curve=flat, initial_equity=10_000.0)


def test_sortino_with_no_losing_periods_raises():
    rising = _curve(np.linspace(10_000, 12_000, 200))
    with pytest.raises(DegenerateMetricError, match="sortino"):
        compute_metrics(trades=[], equity_curve=rising, initial_equity=10_000.0)


def test_calmar_with_zero_drawdown_raises():
    rising = _curve(np.linspace(10_000, 12_000, 200))
    lenient = compute_metrics(
        trades=[], equity_curve=rising, initial_equity=10_000.0, strict=False
    )
    assert lenient.calmar is None
    assert lenient.max_drawdown_pct == pytest.approx(0.0, abs=1e-12)


def test_cagr_on_a_wiped_out_account_raises_rather_than_returning_a_rate():
    """Final equity <= 0 has no growth rate. Report the ruin, not a number."""
    blown = _curve(np.linspace(10_000, 0.0, 200))
    lenient = compute_metrics(
        trades=[], equity_curve=blown, initial_equity=10_000.0, strict=False
    )
    assert lenient.cagr_pct is None
    assert lenient.calmar is None
    assert any("cagr" in d for d in lenient.degenerate)
    assert lenient.max_drawdown_pct == pytest.approx(-100.0, abs=1e-9)


def test_strict_is_the_default():
    """A caller who forgets the flag gets the safe behaviour, not the loose one."""
    flat = _curve([10_000.0] * 50)
    with pytest.raises(DegenerateMetricError):
        compute_metrics(trades=[], equity_curve=flat, initial_equity=10_000.0)


def test_no_metric_is_ever_nan_or_inf():
    """A NaN that escapes gets sorted, averaged and eventually published.

    Regression: an earlier version of this module returned ``float('nan')`` for
    ``time_in_market_pct`` when the equity curve lacked an exposure column.
    That is now a named degeneracy, like every other one.
    """
    rng = np.random.default_rng(3)
    equity = 10_000 * np.cumprod(1 + rng.normal(0.0002, 0.004, 800))
    trades = [_trade(float(x)) for x in rng.normal(5.0, 40.0, 60)]
    m = compute_metrics(
        trades=trades,
        equity_curve=_curve(equity, extra={"open_positions": np.ones(800, dtype=int)}),
        initial_equity=10_000.0,
        strict=False,
    )
    for name, value in m.as_dict().items():
        if isinstance(value, float):
            assert np.isfinite(value), f"{name} is {value}"


# ==========================================================================
# MAE / MFE and cost drag
# ==========================================================================


def test_mae_mfe_distribution_and_edge_ratio():
    """
    MAE values -10, -20, -30 -> mean -20, median -20
    MFE values  40,  50,  60 -> mean  50, median  50
    edge ratio = mean MFE / mean |MAE| = 50 / 20 = 2.5
    """
    curve = _curve(np.linspace(10_000, 10_200, 80))
    trades = [
        _trade(10.0, mae=-10.0, mfe=40.0),
        _trade(20.0, mae=-20.0, mfe=50.0),
        _trade(-5.0, mae=-30.0, mfe=60.0),
    ]
    m = compute_metrics(
        trades=trades, equity_curve=curve, initial_equity=10_000.0, strict=False
    )
    assert m.mae_mean == pytest.approx(-20.0, abs=1e-12)
    assert m.mae_median == pytest.approx(-20.0, abs=1e-12)
    assert m.mfe_mean == pytest.approx(50.0, abs=1e-12)
    assert m.edge_ratio == pytest.approx(2.5, abs=1e-12)


def test_cost_drag_is_reported_as_a_share_of_gross():
    """
    Two trades: gross 60 + 40 = 100 ; total costs 2 + 2 = 4
    cost drag = 4 / 100 * 100 = 4.0%
    """
    curve = _curve(np.linspace(10_000, 10_100, 60))
    trades = [_trade(58.0, gross=60.0), _trade(38.0, gross=40.0)]
    m = compute_metrics(
        trades=trades, equity_curve=curve, initial_equity=10_000.0, strict=False
    )
    assert m.gross_pnl == pytest.approx(100.0, abs=1e-12)
    assert m.total_costs == pytest.approx(4.0, abs=1e-12)
    assert m.cost_drag_pct_of_gross == pytest.approx(4.0, abs=1e-12)


def test_time_in_market_comes_from_the_exposure_column():
    """6 of 10 bars with a position open -> 60%."""
    curve = _curve(
        np.linspace(10_000, 10_100, 10),
        extra={"open_positions": [0, 0, 1, 1, 1, 1, 1, 1, 0, 0]},
    )
    m = compute_metrics(
        trades=[_trade(10.0), _trade(-5.0)], equity_curve=curve,
        initial_equity=10_000.0, strict=False,
    )
    assert m.time_in_market_pct == pytest.approx(60.0, abs=1e-9)


def test_trade_based_sharpe_is_reported_alongside_the_returns_based_one():
    """Both estimators must be present so the gap between them is visible."""
    rng = np.random.default_rng(11)
    equity = 10_000 * np.cumprod(1 + rng.normal(0.0003, 0.005, 900))
    trades = [_trade(float(x)) for x in rng.normal(8.0, 45.0, 70)]
    m = compute_metrics(
        trades=trades, equity_curve=_curve(equity), initial_equity=10_000.0, strict=False
    )
    assert m.sharpe is not None
    assert m.sharpe_trade_based is not None
    assert m.sharpe != m.sharpe_trade_based


def test_missing_exposure_column_is_a_named_degeneracy_not_a_nan():
    rng = np.random.default_rng(17)
    equity = 10_000 + np.cumsum(rng.normal(1.0, 25.0, 300))
    curve = _curve(equity)  # no 'open_positions' column
    lenient = compute_metrics(
        trades=[_trade(10.0), _trade(-4.0)], equity_curve=curve,
        initial_equity=10_000.0, strict=False,
    )
    assert lenient.time_in_market_pct is None
    assert any("time_in_market" in d for d in lenient.degenerate)

    with pytest.raises(DegenerateMetricError, match="time_in_market"):
        compute_metrics(
            trades=[_trade(10.0), _trade(-4.0)], equity_curve=curve,
            initial_equity=10_000.0, strict=True,
        )
