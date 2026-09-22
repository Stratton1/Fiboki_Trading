"""Tests for Strategy Factory Gen-1 primitives.

Validates that every primitive's required-indicator factories build, the
indicators emit the columns the primitive reads, each primitive returns a bool
for both directions, and a sample of new primitives are look-ahead safe.
"""

import numpy as np
import pandas as pd
import pytest

from fibokei.strategies.factory.primitives import PRIMITIVES


def _df(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.normal(0, 0.3, n)
    vol = rng.uniform(100, 1000, n)
    ts = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"timestamp": ts, "open": open_, "high": high, "low": low,
         "close": close, "volume": vol}
    )


def _with_indicators(prim, df):
    d = df.copy()
    for factory in prim.requires:
        d = factory({}).compute(d)
    return d


@pytest.mark.parametrize("name", sorted(PRIMITIVES))
def test_primitive_returns_bool_both_directions(name):
    prim = PRIMITIVES[name]
    d = _with_indicators(prim, _df())
    idx = len(d) - 1
    for direction in ("long", "short"):
        result = prim.fn(d, idx, {}, direction)
        assert isinstance(result, (bool, np.bool_)), f"{name} returned {type(result)}"


@pytest.mark.parametrize("name", ["macd_cross", "donchian_breakout", "bb_breakout", "adx_filter"])
def test_new_primitive_no_lookahead(name):
    prim = PRIMITIVES[name]
    df = _df()
    base_df = _with_indicators(prim, df)
    base = prim.fn(base_df, 100, {}, "long")

    mutated = df.copy()
    mutated.loc[150:, ["open", "high", "low", "close"]] *= 1.4
    mut_df = _with_indicators(prim, mutated)
    after = prim.fn(mut_df, 100, {}, "long")
    assert base == after, f"{name} changed at idx 100 when future bars mutated"


def test_gen1_primitive_count():
    # 10 original + 19 Gen-1 additions.
    assert len(PRIMITIVES) >= 29
    for n in ("macd_cross", "stoch_threshold", "bb_revert", "bb_breakout",
              "adx_filter", "donchian_breakout", "keltner_breakout", "psar_flip",
              "cci_threshold", "roc_threshold", "pivot_bounce", "sr_breakout",
              "sr_bounce", "vwap_bias", "obv_confirm", "sma_cross", "price_vs_sma",
              "macd_zero", "atr_breakout"):
        assert n in PRIMITIVES, f"{n} not registered"
