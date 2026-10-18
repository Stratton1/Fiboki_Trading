"""Known-answer tests for the research tearsheet analytics (research/tearsheet.py).

Every figure the research screens draw comes from these functions, so each one
is pinned to a hand-computed answer on a synthetic input: drawdown depth,
periods and recovery; calendar-month returns; R multiples, MAE and MFE in R;
histogram counts; turnover; return correlation; the parameter surface's
plateau classification; walk-forward fold stability; and Monte Carlo
determinism in its seed.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from fiboki.research import tearsheet as ts
from fiboki.validation.gates import PRODUCTION_GATE_SET


def _curve(values: list[float], start: str = "2026-01-01", freq: str = "1D") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


# ------------------------------------------------------------------ drawdown


def test_underwater_is_distance_below_the_running_peak():
    eq = _curve([100, 110, 99, 121, 108.9])
    uw = ts.underwater(eq).round(10).tolist()
    assert uw == [0.0, 0.0, -10.0, 0.0, -10.0]


def test_drawdown_periods_known_answer():
    # Peak 110 -> trough 88 (-20%) recovered at 115; peak 115 -> 103.5 (-10%), open.
    eq = _curve([100, 110, 99, 88, 100, 115, 103.5, 110])
    periods = ts.drawdown_periods(eq, top=5)
    assert [round(p.depth_pct, 10) for p in periods] == [-20.0, -10.0]
    first, second = periods
    assert first.rank == 1
    assert first.peak_at == eq.index[1] and first.trough_at == eq.index[3]
    assert first.recovered_at == eq.index[5]
    assert first.decline_days == pytest.approx(2.0)
    assert first.recovery_days == pytest.approx(2.0)
    assert first.length_days == pytest.approx(4.0)
    assert second.recovered_at is None and second.recovery_days is None
    # An open period runs to the end of the curve.
    assert second.length_days == pytest.approx(2.0)


def test_drawdown_periods_keep_only_the_deepest():
    eq = _curve([100, 90, 100, 95, 100, 80, 100, 99, 100, 70, 100, 98, 100])
    depths = [round(p.depth_pct, 6) for p in ts.drawdown_periods(eq, top=3)]
    assert depths == [-30.0, -20.0, -10.0]


def test_a_monotonic_curve_has_no_drawdown():
    assert ts.drawdown_periods(_curve([1, 2, 3, 4])) == []


def test_thinning_never_drops_the_trough():
    values = [100.0] * 5000
    values[3217] = 40.0
    eq = _curve(values, freq="1h")
    thin = ts.thin(ts.underwater(eq), 300)
    assert len(thin) <= 302
    assert thin.min() == pytest.approx(-60.0)


# ------------------------------------------------------------ monthly returns


def test_monthly_returns_known_answer():
    idx = pd.DatetimeIndex(
        ["2026-01-10", "2026-01-31 23:00", "2026-02-15", "2026-02-28 12:00", "2026-04-02"],
        tz="UTC",
    )
    eq = pd.Series([101.0, 110.0, 105.0, 99.0, 108.9], index=idx)
    table = ts.monthly_returns(eq, initial_equity=100.0)
    got = {(c.year, c.month): round(c.return_pct, 10) for c in table.cells}
    # Jan from the initial 100; Feb from Jan's last point; March has no point.
    assert got == {(2026, 1): 10.0, (2026, 2): -10.0, (2026, 4): 10.0}
    assert round(table.years[2026], 10) == round((108.9 / 100 - 1) * 100, 10)


def test_monthly_returns_compound_across_years():
    idx = pd.DatetimeIndex(["2025-12-31", "2026-06-30"], tz="UTC")
    table = ts.monthly_returns(pd.Series([120.0, 90.0], index=idx), initial_equity=100.0)
    assert table.years[2025] == pytest.approx(20.0)
    assert table.years[2026] == pytest.approx(-25.0)


# ------------------------------------------------------------- trades and R


def _trade(**kw):
    base = {
        "trade_id": "t", "instrument": "EURUSD", "direction": "long", "size": 10_000.0,
        "entry_price": 1.1000, "exit_price": 1.1020, "initial_stop": 1.0990,
        "net_pnl": 20.0, "fx_rate_used": 1.0, "max_adverse_excursion": -5.0,
        "max_favourable_excursion": 30.0, "spread_cost": 1.0, "commission": 0.5,
        "slippage_cost": 0.25, "financing_cost": 0.25, "bars_held": 4,
        "entry_time": "2026-01-02 00:00:00+00:00", "exit_time": "2026-01-02 04:00:00+00:00",
    }
    base.update(kw)
    return base


def test_r_multiple_mae_mfe_in_r_known_answer():
    # Risk = |1.1000 - 1.0990| * 10,000 * 1 * 1.0 = 10.0 account units.
    [row] = ts.excursions([_trade()], {"EURUSD": 1.0})
    assert row.r_multiple == pytest.approx(2.0)
    assert row.mae == -5.0 and row.mfe == 30.0
    assert row.mae_r == pytest.approx(-0.5)
    assert row.mfe_r == pytest.approx(3.0)


def test_r_uses_the_stored_fx_rate_and_contract_size():
    t = _trade(fx_rate_used=0.8, net_pnl=-8.0)
    assert ts.trade_risk(t, 1.0) == pytest.approx(8.0)
    assert ts.r_multiple(t, 1.0) == pytest.approx(-1.0)
    assert ts.trade_risk(t, 100.0) == pytest.approx(800.0)


def test_a_trade_without_a_stop_has_no_r_and_rows_without_excursions_have_none():
    t = _trade()
    del t["initial_stop"], t["max_adverse_excursion"], t["max_favourable_excursion"]
    [row] = ts.excursions([t], {})
    assert row.r_multiple is None and row.mae is None and row.mfe is None
    assert row.mae_r is None


def test_trade_stats_known_answer():
    trades = [_trade(net_pnl=20.0), _trade(net_pnl=-10.0), _trade(net_pnl=30.0, initial_stop=None)]
    s = ts.trade_stats(trades, {"EURUSD": 1.0})
    assert s.n == 3 and s.net == pytest.approx(40.0)
    assert s.win_rate_pct == pytest.approx(200 / 3)
    assert s.profit_factor == pytest.approx(5.0)
    assert s.expectancy == pytest.approx(40 / 3)
    # Only the two trades with a stop have an R: (2 + -1) / 2.
    assert s.n_with_r == 2 and s.expectancy_r == pytest.approx(0.5)


def test_costs_and_turnover_known_answer():
    trades = [_trade(), _trade()]
    c = ts.costs(trades)
    assert c.total == pytest.approx(4.0)
    annual, notional = ts.turnover(trades, {"EURUSD": 1.0}, mean_equity=10_000.0, years=0.5)
    # Two round trips of 10,000 x 1.1 = 44,000 notional; / 10,000 / 0.5 years.
    assert notional == pytest.approx(44_000.0)
    assert annual == pytest.approx(8.8)


def test_trade_returns_compound_on_running_equity():
    r = ts.trade_returns([{"net_pnl": 100.0}, {"net_pnl": -110.0}], 1_000.0)
    assert r.tolist() == pytest.approx([0.1, -0.1])


def test_histogram_counts_every_value():
    h = ts.histogram([1.0, 1.0, 2.0, 3.0, -1.0])
    assert sum(h.counts) == 5 and h.n == 5
    assert h.edges[0] == pytest.approx(-1.0) and h.edges[-1] == pytest.approx(3.0)
    assert h.median == pytest.approx(1.0)
    one = ts.histogram([2.0, 2.0])
    assert one.counts == [2]


# ------------------------------------------------------------- Sharpe family


def test_rolling_sharpe_known_answer():
    idx = pd.date_range("2026-01-01", periods=4, freq="1D", tz="UTC")
    rets = pd.Series([0.01, 0.03, 0.01, 0.03], index=idx)
    out = ts.rolling_sharpe(rets, window=2, days_per_year=4.0)
    # mean 0.02, sd (ddof 1) = 0.0141421..., x sqrt(4) = 2.828427...
    assert out.round(6).tolist() == [2.828427] * 3


def test_per_trade_psr_matches_the_stats_library():
    from fiboki.stats.sharpe import probabilistic_sharpe_ratio, sharpe_moments

    pnls = [10.0, -5.0, 7.0, -3.0, 12.0, -8.0, 4.0]
    trades = [{"net_pnl": p} for p in pnls]
    got = ts.per_trade_psr(trades, 1_000.0)
    m = sharpe_moments(ts.trade_returns(trades, 1_000.0))
    assert got.psr == pytest.approx(probabilistic_sharpe_ratio(m.sr_hat, m.n_obs, m.skew, m.kurtosis))
    assert ts.per_trade_psr([{"net_pnl": 1.0}], 1_000.0).psr is None


def test_buy_and_hold_takes_the_last_close_at_or_before_each_point():
    eq = _curve([100, 100, 100], start="2026-01-02")
    closes = pd.Series([2.0, 4.0], index=pd.DatetimeIndex(["2026-01-01", "2026-01-03"], tz="UTC"))
    bh = ts.buy_and_hold(eq, closes, 1_000.0)
    assert bh.tolist() == [1_000.0, 2_000.0, 2_000.0]


def test_return_correlation_on_common_days_only():
    idx = pd.date_range("2026-01-01", periods=30, freq="1D", tz="UTC")
    a = pd.Series(np.sin(np.arange(30)), index=idx)
    b = -a.iloc[5:]
    cells = {(c.a, c.b): c for c in ts.return_correlations({"a": a, "b": b}, min_common=10)}
    assert cells[("a", "b")].rho == pytest.approx(-1.0)
    assert cells[("a", "b")].n_common == 25
    short = ts.return_correlations({"a": a, "b": b.iloc[:5]}, min_common=10)
    assert next(c for c in short if c.a != c.b).rho is None


# ------------------------------------------------------------- Monte Carlo


def test_monte_carlo_is_deterministic_in_its_seed():
    r = [1.5, -1.0, 2.0, -1.0, -1.0, 3.0, -1.0, 0.5, -1.0, 1.0]
    a = ts.monte_carlo(r_multiples=r, returns=None, initial_equity=10_000, risk_fraction=0.01,
                       n_paths=500, seed=7)
    b = ts.monte_carlo(r_multiples=r, returns=None, initial_equity=10_000, risk_fraction=0.01,
                       n_paths=500, seed=7)
    c = ts.monte_carlo(r_multiples=r, returns=None, initial_equity=10_000, risk_fraction=0.01,
                       n_paths=500, seed=8)
    assert a.fan == b.fan and a.drawdown_percentiles == b.drawdown_percentiles
    assert a.fan != c.fan
    assert a.seed == 7 and a.basis == "r_multiple"
    # Percentiles are ordered at every step, and every path starts at the initial equity.
    for i in range(len(a.fan_steps)):
        col = [a.fan[p][i] for p in ts.FAN_PERCENTILES]
        assert col == sorted(col)
    assert all(math.isclose(a.fan[p][0], 10_000) for p in ts.FAN_PERCENTILES)
    dd = [a.drawdown_percentiles[p] for p in ts.DRAWDOWN_PERCENTILES]
    assert dd == sorted(dd)


def test_monte_carlo_realised_path_is_the_trades_in_order():
    r = [1.0, -1.0, 1.0]
    mc = ts.monte_carlo(r_multiples=r, returns=None, initial_equity=100.0, risk_fraction=0.1,
                        n_paths=100, seed=1)
    assert mc.realised_final_equity == pytest.approx(100 * 1.1 * 0.9 * 1.1)
    assert mc.realised_max_drawdown_pct == pytest.approx(10.0)


def test_monte_carlo_falls_back_to_returns_without_stops_and_refuses_one_trade():
    mc = ts.monte_carlo(r_multiples=None, returns=[0.01, -0.02, 0.03], initial_equity=100.0,
                        risk_fraction=0.01, n_paths=100, seed=3)
    assert mc.basis == "trade_return" and mc.risk_fraction == 1.0 and mc.notes
    with pytest.raises(ValueError):
        ts.monte_carlo(r_multiples=[1.0], returns=[0.01], initial_equity=100.0,
                       risk_fraction=0.01, n_paths=100, seed=3)


# ------------------------------------------------------------- parameter surface


def _plateau_gates():
    metrics = {"point_plateau_ratio", "plateau_neighbourhood_median_ratio", "plateau_neighbourhood_min"}
    return [g for g in PRODUCTION_GATE_SET.gates if g.metric in metrics]


def test_parameter_surface_marks_a_plateau_and_not_a_spike():
    xs, ys = [1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0]
    obs = []
    for x in xs:
        for y in ys:
            score = 1.0 if x <= 3 else -0.5  # a broad positive region, then a cliff
            obs.append((x, y, score, f"r{x}{y}"))
    # A lone spike far from the plateau, on its own losing surface.
    obs = [o for o in obs if not (o[0] == 4.0 and o[1] == 30.0)] + [(4.0, 30.0, 5.0, "spike")]
    cells = {(c.x, c.y): c for c in ts.parameter_surface(
        obs, x_values=xs, y_values=ys, radius=1, plateau_gates=_plateau_gates())}
    assert cells[(2.0, 20.0)].on_plateau is True
    assert all(cells[(2.0, 20.0)].plateau_checks.values())
    assert cells[(4.0, 30.0)].on_plateau is False
    assert cells[(4.0, 30.0)].isolated_peak is True
    assert cells[(4.0, 10.0)].on_plateau is False  # its own score is negative


def test_parameter_surface_averages_runs_in_a_cell_and_keeps_empty_cells():
    obs = [(1.0, 1.0, 2.0, "a"), (1.0, 1.0, 4.0, "b"), (2.0, 1.0, 1.0, "c")]
    cells = {(c.x, c.y): c for c in ts.parameter_surface(
        obs, x_values=[1.0, 2.0, 3.0], y_values=[1.0], radius=1, plateau_gates=_plateau_gates())}
    assert cells[(1.0, 1.0)].score == pytest.approx(3.0) and cells[(1.0, 1.0)].n_runs == 2
    assert cells[(3.0, 1.0)].score is None and cells[(3.0, 1.0)].n_runs == 0
    assert cells[(3.0, 1.0)].on_plateau is False


# ------------------------------------------------------------- walk-forward


def test_fold_stability_known_answer():
    folds = [
        {"is_rate": 1.0, "oos_rate": 0.5, "selected_params": {"a": 1}},
        {"is_rate": 2.0, "oos_rate": -0.5, "selected_params": {"a": 1}},
        {"is_rate": 0.0, "oos_rate": 1.0, "selected_params": {"a": 2}},
    ]
    s = ts.fold_stability(folds)
    assert s.n_folds == 3
    assert s.oos_profitable_fraction == pytest.approx(2 / 3)
    assert s.fold_efficiency == [0.5, -0.25, None]
    assert s.mean_oos_rate == pytest.approx(1 / 3)
    assert s.oos_rate_sd == pytest.approx(float(np.std([0.5, -0.5, 1.0], ddof=1)))
    assert s.parameter_repeats == 1


def test_every_tearsheet_stat_has_a_definition_and_a_formula():
    for key, d in ts.DEFINITIONS.items():
        assert d.label and d.definition and d.formula, key
