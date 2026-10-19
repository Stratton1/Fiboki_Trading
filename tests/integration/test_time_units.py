"""Results must not depend on the unit the bar index is stored in.

The canonical store reads bars as ``datetime64[us, UTC]`` (``data/schema.py``,
``frame_from_arrow``), while every synthetic fixture builds ``datetime64[ns]``.
``DatetimeIndex.asi8`` is in the index's OWN unit, so code that treated it as
nanoseconds was wrong on real data only, and every test passed:

* ``engine._median_interval`` read a 4-hour bar as 14.4 seconds, so every fill
  after the first bar was flagged stale and paid ``stale_spread_multiple`` x the
  spread (since 2026-09-19);
* the engine's equity curve for volatility targeting was rebuilt from
  microsecond integers read as nanoseconds (bars 1,000 times closer together),
  so realised volatility was about 31.6 times too high and the volatility
  target cut positions (since 2026-10-10, the K6 speed-up);
* ``risk.accounting._periods_per_year_arrays`` made the same mistake for any
  microsecond curve;
* ``validation.pooled`` bucketed days as ``ts // 86_400e9`` on microsecond
  integers, so 16 years of H4 bars were 7 "days" and DEFF fell back to the
  sleeve count on every real candidate (E-4 main stage, 2026-10-18).

Found on the MacBook on 2026-10-19 (EURUSD and USDJPY H4, donchian_breakout_atr:
same trades, different sizes, spread cost and net P&L). Fixed in
``engine_v4_time_units``. These tests run every path on the same bars in both
units and require identical answers.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from synthetic_prices import choppy_ohlc

from fiboki.backtest.engine import _median_interval
from fiboki.core.enums import Timeframe
from fiboki.core.money import IdentityFxSource
from fiboki.risk.accounting import (
    _periods_per_year,
    _periods_per_year_arrays,
    _realised_portfolio_vol_reference,
    realised_portfolio_vol,
)
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import EngineEvaluator, EvaluatorConfig
from fiboki.validation.evaluation import DateWindow
from fiboki.validation.pooled import PooledEvaluator, PooledSleeve, design_effect

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "research" / "strategies" / "rsi_band_mean_reversion.json"


def _as_unit(frame: pd.DataFrame, unit: str) -> pd.DataFrame:
    return frame.set_axis(pd.DatetimeIndex(frame.index).as_unit(unit))


def _h4_index(n: int, unit: str) -> pd.DatetimeIndex:
    return pd.date_range("2010-01-04", periods=n, freq="4h", tz="UTC").as_unit(unit)


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_bar_interval_is_the_bar_whatever_the_unit(unit: str) -> None:
    assert _median_interval(_h4_index(500, unit)) == pd.Timedelta(hours=4)


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_periods_per_year_is_unit_free(unit: str) -> None:
    idx = _h4_index(500, unit)
    assert _periods_per_year_arrays(idx) == _periods_per_year(idx) == pytest.approx(2190.0)


@pytest.mark.parametrize("unit", ["us", "ns"])
def test_realised_vol_fast_path_equals_reference_in_any_unit(unit: str) -> None:
    rng = np.random.default_rng(7)
    idx = _h4_index(2_000, unit)
    curve = pd.Series(10_000.0 * np.cumprod(1.0 + rng.normal(0, 0.002, idx.size)), index=idx)
    fast = realised_portfolio_vol(curve, min_observations=20)
    ref = _realised_portfolio_vol_reference(curve, min_observations=20)
    ns = realised_portfolio_vol(curve.set_axis(idx.as_unit("ns")), min_observations=20)
    assert fast == ref == ns
    # About 0.2% per 4-hour bar is roughly 9% a year, not hundreds of percent.
    assert 0.05 < fast < 0.15


def test_design_effect_reads_days_not_microsecond_ticks() -> None:
    idx = _h4_index(6 * 400, "us")
    rng = np.random.default_rng(3)
    common = rng.normal(size=idx.size)
    pnl = np.column_stack([common + rng.normal(size=idx.size) * 0.1 for _ in range(4)])
    ts = pd.DatetimeIndex(idx).as_unit("ns").asi8
    deff = design_effect(pnl, ts)
    assert deff.by_frequency["daily"]["n_periods"] == 400
    assert deff.basis.startswith("max over frequencies")
    assert 3.0 < deff.deff <= 4.0


@pytest.fixture(scope="module")
def document() -> StrategyDocument:
    return StrategyDocument.from_json(DOC.read_text())


def _engine(document: StrategyDocument, symbol: str, frame: pd.DataFrame) -> EngineEvaluator:
    return EngineEvaluator(
        document=document, frame=frame, dataset_version_id=f"synthetic_{symbol}_h1",
        config=EvaluatorConfig(instrument=symbol, timeframe=Timeframe.H1, account_ccy="USD",
                               initial_balance=100_000.0, min_window_bars=20),
        fx=IdentityFxSource(),
    )


def _fingerprint(ev) -> list[tuple]:
    return [
        (t.entry_time, t.exit_time, t.exit_reason, t.net_pnl, t.spread_cost, t.slippage_cost)
        for t in sorted(ev.trades, key=lambda t: (t.entry_time, t.exit_time))
    ]


def test_an_engine_run_is_identical_on_microsecond_and_nanosecond_bars(document) -> None:
    frame = choppy_ohlc(3000, seed=11)
    window = DateWindow("w", frame.index[200], frame.index[2800])
    params = document.default_values()
    ns = _engine(document, "EURUSD", _as_unit(frame, "ns"))(params, window)
    us = _engine(document, "EURUSD", _as_unit(frame, "us"))(params, window)
    assert ns.n_trades > 0
    assert _fingerprint(us) == _fingerprint(ns)
    assert us.net_profit == ns.net_profit
    np.testing.assert_array_equal(us.returns, ns.returns)


def test_a_pooled_evaluation_is_identical_on_microsecond_and_nanosecond_bars(document) -> None:
    base = {"EURUSD": choppy_ohlc(3000, seed=11), "AUDUSD": choppy_ohlc(3000, seed=29)}
    base["GBPUSD"] = base["EURUSD"] * 1.12
    out = {}
    for unit in ("us", "ns"):
        frames = {s: _as_unit(f, unit) for s, f in base.items()}
        pooled = PooledEvaluator(tuple(
            PooledSleeve(s, _engine(document, s, frames[s]), frames[s].index, f"synthetic_{s}_h1")
            for s in sorted(frames)
        ))
        idx = frames["EURUSD"].index
        out[unit] = pooled(document.default_values(), DateWindow("w", idx[200], idx[2800]))
    assert out["us"].meta["design_effect"] == out["ns"].meta["design_effect"]
    assert out["us"].meta["design_effect"]["basis"].startswith("max over frequencies")
    assert out["us"].n_trades == out["ns"].n_trades
    assert out["us"].net_profit == out["ns"].net_profit
