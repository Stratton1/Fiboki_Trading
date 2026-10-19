"""Advisory analytics (ops/analytics.py): daily returns, correlation, portfolio weights."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.ops.analytics import daily_returns_from_trades, pairwise, portfolio_weights
from tests.api.ops_fixtures import synthetic_trades


def test_daily_returns_are_zero_on_days_without_an_exit():
    trades = [
        {"exit_time": "2026-01-05T10:00:00Z", "net_pnl": 100.0},
        {"exit_time": "2026-01-08T10:00:00Z", "net_pnl": -50.0},
    ]
    r = daily_returns_from_trades(trades, 10_000.0)
    assert len(r) == 4 and r.iloc[0] == pytest.approx(0.01)
    assert list(r.iloc[1:3]) == [0.0, 0.0]
    assert r.iloc[3] == pytest.approx(-50.0 / 10_100.0)
    assert daily_returns_from_trades([], 10_000.0).empty
    assert daily_returns_from_trades(trades, None).empty


def test_pairwise_correlation_on_common_days_only():
    pnl = np.random.default_rng(3).normal(5, 40, 80)
    a = daily_returns_from_trades(synthetic_trades(seed=1, n=80, strategy_id="a", pnl=pnl), 10_000.0)
    b = daily_returns_from_trades(synthetic_trades(seed=1, n=80, strategy_id="b", pnl=pnl * 1.5), 10_000.0)
    c = daily_returns_from_trades(synthetic_trades(seed=9, n=80, strategy_id="c"), 10_000.0)
    assert pairwise(a, b).rho == pytest.approx(1.0, abs=0.01)
    assert abs(pairwise(a, c).rho) < 0.5
    late = daily_returns_from_trades(synthetic_trades(seed=1, n=10, strategy_id="d", start="2030-01-01"), 10_000.0)
    short = pairwise(a, late)
    assert short.rho is None and short.n_common == 0


def test_portfolio_weights_are_deterministic_long_only_and_sum_to_one():
    rng = np.random.default_rng(5)
    base = rng.normal(0, 0.01, 300)
    idx = pd.date_range("2025-01-01", periods=300, freq="D", tz="UTC")
    returns = {
        "calm": pd.Series(rng.normal(0.0005, 0.004, 300), index=idx),
        "wild": pd.Series(rng.normal(0.0008, 0.02, 300), index=idx),
        "twin_a": pd.Series(base + rng.normal(0, 0.002, 300), index=idx),
        "twin_b": pd.Series(base + rng.normal(0, 0.002, 300), index=idx),
    }
    schemes, names, days, reason = portfolio_weights(returns)
    assert reason == "" and days == 300 and names == ["calm", "twin_a", "twin_b", "wild"]
    by = {s.scheme: s for s in schemes}
    assert set(by) == {"equal_weight", "equal_risk", "inverse_vol", "min_correlation"}
    for s in schemes:
        assert sum(s.weights.values()) == pytest.approx(1.0, abs=1e-6)
        assert all(w >= -1e-12 for w in s.weights.values())
    inv = by["inverse_vol"].weights
    assert inv["calm"] > inv["wild"]
    mc = by["min_correlation"].weights
    assert mc["twin_a"] + mc["twin_b"] < mc["calm"] + mc["wild"]
    again, *_ = portfolio_weights(returns)
    assert [s.weights for s in again] == [s.weights for s in schemes]


def test_portfolio_weights_refuse_too_little_overlap():
    idx = pd.date_range("2025-01-01", periods=10, freq="D", tz="UTC")
    schemes, _, days, reason = portfolio_weights({"a": pd.Series(np.arange(10.0), index=idx),
                                                  "b": pd.Series(np.arange(10.0)[::-1], index=idx)})
    assert schemes == [] and days == 10 and "common days" in reason
