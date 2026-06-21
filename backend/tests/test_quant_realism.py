"""Tests for the quant-realism upgrade: per-instrument cost stress + block MC."""

import numpy as np
import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.sizing import get_default_spread
from fibokei.core.models import Timeframe
from fibokei.research.monte_carlo import run_monte_carlo
from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.traditional import TRADITIONAL_GEN1_SPECS

EMA_X = next(s for s in TRADITIONAL_GEN1_SPECS if s.spec_id == "trad_ema_crossover")


def _df(n=600, seed=4):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 0.02 * t + 6 * np.sin(t / 20.0) + np.cumsum(rng.normal(0, 0.2, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.5, n)
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": high,
                         "low": low, "close": close, "volume": 0.0})


# ── Per-instrument cost stress via spread_multiplier ──────────────────

def test_spread_multiplier_default_is_realistic():
    # multiplier 1.0 must equal the realistic per-instrument default spread.
    assert BacktestConfig().spread_multiplier == 1.0


def test_spread_multiplier_widens_costs_and_reduces_pnl():
    df = _df()
    strat = compile_spec(EMA_X)
    base = Backtester(strat, BacktestConfig(initial_capital=10000.0))
    stressed = Backtester(strat, BacktestConfig(initial_capital=10000.0,
                                                spread_multiplier=2.0))
    rb = base.run(df, "EURUSD", Timeframe.H1)
    rs = stressed.run(df, "EURUSD", Timeframe.H1)
    base_net = sum(t.pnl for t in rb.trades)
    stressed_net = sum(t.pnl for t in rs.trades)
    # Same trades, wider spread → stressed net profit must be <= base.
    assert len(rb.trades) == len(rs.trades)
    assert stressed_net <= base_net


def test_default_spread_is_per_instrument():
    # JPY pairs quote to 2dp so their price-terms spread differs from EURUSD.
    assert get_default_spread("USDJPY") != get_default_spread("EURUSD")


# ── Block-bootstrap Monte Carlo ───────────────────────────────────────

def test_block_bootstrap_runs_and_is_deterministic():
    pnls = list(np.random.default_rng(0).normal(5, 50, 200))
    a = run_monte_carlo(pnls, "s", "EURUSD", "H4", block_size=5)
    b = run_monte_carlo(pnls, "s", "EURUSD", "H4", block_size=5)
    assert a.profit_probability == b.profit_probability
    assert 0.0 <= a.profit_probability <= 1.0
    assert a.num_trades == 200


def test_block_vs_iid_differ_on_autocorrelated_pnl():
    # Construct serially-correlated pnl (long win/loss runs).
    runs = ([20.0] * 20 + [-18.0] * 20) * 5
    iid = run_monte_carlo(runs, "s", "EURUSD", "H4", block_size=1, seed=1)
    block = run_monte_carlo(runs, "s", "EURUSD", "H4", block_size=10, seed=1)
    # Block bootstrap keeps streaks → wider drawdown tail than i.i.d.
    assert block.p95_max_drawdown >= iid.p95_max_drawdown


def test_block_size_one_matches_iid_behaviour():
    pnls = list(np.random.default_rng(2).normal(3, 30, 150))
    r = run_monte_carlo(pnls, "s", "EURUSD", "H4", block_size=1)
    assert r.num_trades == 150 and 0.0 <= r.ruin_probability <= 1.0
