"""The default Sharpe is computed on 17:00-New-York daily equity, with Lo's adjustment (audit P2-13).

Bar-return Sharpe annualised by sqrt(bars per year) is biased when positions
persist, because the bar returns are autocorrelated (Lo 2002, "The Statistics
of Sharpe Ratios", Financial Analysts Journal 58(4)). The daily series is
comparable across timeframes, and Lo's correction is reported beside it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.backtest.metrics import (
    DegenerateMetricError,
    compute_metrics,
    daily_equity,
    lo_adjusted_sharpe,
)


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def test_lo_adjustment_worked_example() -> None:
    """
    r = [0.01, 0.02, -0.01, 0.03, 0.00, 0.01], q = 252 periods per year.

      mean = 0.01; deviations d = [0, 0.01, -0.02, 0.02, -0.01, 0]
      sum d^2 = 0.0001 + 0.0004 + 0.0004 + 0.0001 = 0.0010
      sd (ddof=1) = sqrt(0.0010 / 5) = 0.0141421356
      lags L = floor(4 * (6/100)^(2/9)) = floor(2.14) = 2
      rho_1 = (0*0.01 + 0.01*-0.02 + -0.02*0.02 + 0.02*-0.01 + -0.01*0) / 0.001
            = (-0.0002 - 0.0004 - 0.0002) / 0.001 = -0.8
      rho_2 = (0*-0.02 + 0.01*0.02 + -0.02*-0.01 + 0.02*0) / 0.001
            = (0.0002 + 0.0002) / 0.001 = 0.4
      q + 2 * [(q-1) rho_1 + (q-2) rho_2] = 252 + 2 * (251 * -0.8 + 250 * 0.4)
                                          = 252 - 401.6 + 200 = 50.4
      eta = 252 / sqrt(50.4) = 35.4964787
      SR  = (0.01 / 0.0141421356) * 35.4964787 = 25.0998008

    The naive sqrt(252) scaling gives 11.2249722: strong negative
    autocorrelation means the naive figure UNDERstates here, and positive
    autocorrelation (persistent positions) makes it overstate.
    """
    r = np.array([0.01, 0.02, -0.01, 0.03, 0.00, 0.01])
    sr, eta, lags = lo_adjusted_sharpe(r, 252.0)
    assert lags == 2
    assert eta == pytest.approx(252 / np.sqrt(50.4), abs=1e-9)
    assert sr == pytest.approx(0.01 / np.sqrt(0.0010 / 5) * 252 / np.sqrt(50.4), abs=1e-9)
    assert sr == pytest.approx(25.0998008, abs=1e-6)


def test_no_autocorrelation_leaves_the_sharpe_alone() -> None:
    rng = np.random.default_rng(3)
    r = rng.normal(0.0005, 0.01, 5000)
    sr, eta, _ = lo_adjusted_sharpe(r, 252.0)
    naive = r.mean() / r.std(ddof=1) * np.sqrt(252.0)
    assert eta == pytest.approx(np.sqrt(252.0), rel=0.05)
    assert sr == pytest.approx(naive, rel=0.05)


def test_positive_autocorrelation_is_penalised() -> None:
    rng = np.random.default_rng(5)
    e = rng.normal(0.0, 0.01, 4000)
    r = np.empty_like(e)
    r[0] = e[0]
    for i in range(1, e.size):
        r[i] = 0.5 * r[i - 1] + e[i]
    r += 0.0004
    sr, _, _ = lo_adjusted_sharpe(r, 252.0)
    naive = r.mean() / r.std(ddof=1) * np.sqrt(252.0)
    assert sr < 0.75 * naive


def test_a_degenerate_series_raises() -> None:
    with pytest.raises(DegenerateMetricError):
        lo_adjusted_sharpe(np.array([0.01, 0.01, 0.01, 0.01]), 252.0)


def test_trading_days_end_at_17_new_york_in_winter_and_summer() -> None:
    """Winter: 17:00 EST = 22:00 UTC. Summer: 17:00 EDT = 21:00 UTC."""
    idx = pd.DatetimeIndex(
        [
            T("2024-01-08 21:59"),  # Mon 16:59 EST -> Monday
            T("2024-01-08 22:00"),  # Mon 17:00 EST -> Tuesday
            T("2024-07-08 20:59"),  # Mon 16:59 EDT -> Monday
            T("2024-07-08 21:00"),  # Mon 17:00 EDT -> Tuesday
        ]
    )
    days = daily_equity(pd.Series([1.0, 2.0, 3.0, 4.0], index=idx))
    assert [d.strftime("%Y-%m-%d") for d in days.index] == [
        "2024-01-08", "2024-01-09", "2024-07-08", "2024-07-09"
    ]
    assert list(days) == [1.0, 2.0, 3.0, 4.0]


def test_the_last_value_of_the_day_is_taken_and_the_opening_is_prefixed() -> None:
    idx = pd.date_range("2024-01-08 00:00", periods=48, freq="1h", tz="UTC")
    eq = pd.Series(np.arange(48, dtype=float) + 100.0, index=idx)
    days = daily_equity(eq, initial_equity=90.0)
    # 00:00..21:00 UTC on the 8th belong to Monday the 8th (last = 21:00 -> 121),
    # 22:00 on the 8th .. 21:00 on the 9th to Tuesday (last = 45 -> 145),
    # 22:00 and 23:00 on the 9th to Wednesday (last = 147).
    assert list(days) == [90.0, 121.0, 145.0, 147.0]


def test_the_daily_sharpe_does_not_depend_on_the_bar_size() -> None:
    """The same equity path sampled hourly and 4-hourly gives the same daily
    series, so the same Sharpe; the bar-based figure does not.

    The window is northern winter (2022-11-07 to 2023-03-07, before US DST
    starts on 2023-03-12), so every trading day ends at 22:00 UTC and the last
    bar of each day, 21:00 UTC, is on both the H1 and the H4 grid
    (01, 05, 09, 13, 17, 21)."""
    rng = np.random.default_rng(0)
    idx_h1 = pd.date_range("2022-11-07", periods=24 * 120, freq="1h", tz="UTC")
    eq_h1 = 10_000 * np.cumprod(1 + rng.normal(0.00002, 0.001, idx_h1.size))
    # 01:00 on the first day to 21:00 on the last, so both grids span exactly
    # the same elapsed time (the annualisation is measured from the span).
    h1 = pd.DataFrame({"equity": eq_h1}, index=idx_h1).iloc[1:-2]
    h4 = h1.iloc[::4]
    assert h1.index[0] == h4.index[0] and h1.index[-1] == h4.index[-1]
    m1 = compute_metrics(trades=[], equity_curve=h1, initial_equity=10_000.0, strict=False)
    m4 = compute_metrics(trades=[], equity_curve=h4, initial_equity=10_000.0, strict=False)
    assert m1.daily_observations == m4.daily_observations
    assert m1.sharpe == pytest.approx(m4.sharpe, rel=1e-9)
    assert m1.sharpe_bar_based != pytest.approx(m4.sharpe_bar_based, rel=1e-3)
    assert m1.sharpe_lo_adjusted is not None and m1.lo_lags >= 1
