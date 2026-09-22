"""The single most important test in the indicator package.

Every registered indicator is computed twice: once on a clean frame, once on a
frame whose bars strictly after ``k`` have been replaced with violently
different prices. Every published value at or before ``k`` must be bit-identical
(NaN positions included). Anything that reads the future fails here.

The suite is collected from :func:`fiboki.indicators.registry.causality_suite`,
which itself asserts that it covers every registered indicator class -- so a new
indicator cannot be added without being proved causal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.indicators import assert_causal, causality_suite
from fiboki.indicators.base import CausalityError, _corrupt_future
from fiboki.indicators.ichimoku import Ichimoku, chikou_span_display

SUITE = causality_suite()
CUT_POINTS = (60, 150, 320)


@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    return synthetic_ohlcv()


def _ident(indicator) -> str:
    return f"{type(indicator).__name__}:{indicator.name}"


@pytest.mark.parametrize("indicator", SUITE, ids=[_ident(i) for i in SUITE])
@pytest.mark.parametrize("k", CUT_POINTS)
def test_indicator_is_causal(indicator, k, ohlcv: pd.DataFrame) -> None:
    assert_causal(indicator, ohlcv, k)


@pytest.mark.parametrize("indicator", SUITE, ids=[_ident(i) for i in SUITE])
def test_indicator_does_not_mutate_input(indicator, ohlcv: pd.DataFrame) -> None:
    """V1 wrote columns into the caller's frame, making order of execution matter."""
    before = ohlcv.copy(deep=True)
    out = indicator.compute(ohlcv)
    pd.testing.assert_frame_equal(ohlcv, before)
    assert set(indicator.output_columns).issubset(out.columns)
    assert len(out) == len(ohlcv)


@pytest.mark.parametrize("indicator", SUITE, ids=[_ident(i) for i in SUITE])
def test_prefix_of_frame_gives_same_prefix_of_values(
    indicator, ohlcv: pd.DataFrame
) -> None:
    """Truncation equivalence: computing on df[:k+1] equals computing on df[:k+1].

    This is the property the compiler relies on when it evaluates a strategy at
    bar ``idx`` against a frame whose indicators were computed once over the
    whole history. If it failed, backtest and paper could not agree.
    """
    k = 200
    full = indicator.compute(ohlcv)
    partial = indicator.compute(ohlcv.iloc[: k + 1])
    for col in indicator.output_columns:
        a = full[col].to_numpy(dtype=float)[: k + 1]
        b = partial[col].to_numpy(dtype=float)
        assert np.array_equal(np.isnan(a), np.isnan(b)), col
        assert np.array_equal(a[~np.isnan(a)], b[~np.isnan(b)]), col


def test_the_causality_harness_actually_catches_a_leak(ohlcv: pd.DataFrame) -> None:
    """A negative control: a deliberately non-causal indicator must fail."""

    class Leaky:
        name = "leaky"
        output_columns = ("leaky",)

        def compute(self, df: pd.DataFrame) -> pd.DataFrame:
            out = df.copy()
            out["leaky"] = df["close"].shift(-1)  # tomorrow's close, today
            return out

    with pytest.raises(CausalityError):
        assert_causal(Leaky(), ohlcv, 100)


def test_corrupt_future_leaves_history_untouched(ohlcv: pd.DataFrame) -> None:
    dirty = _corrupt_future(ohlcv, 100)
    pd.testing.assert_frame_equal(ohlcv.iloc[:101], dirty.iloc[:101])
    assert not np.allclose(
        ohlcv["close"].to_numpy()[101:], dirty["close"].to_numpy()[101:]
    )


def test_chikou_display_is_not_in_the_strategy_frame(ohlcv: pd.DataFrame) -> None:
    """The V1 landmine: a future close sitting in the strategy DataFrame."""
    ich = Ichimoku(tenkan_period=3, kijun_period=5, senkou_b_period=7, senkou_shift=3)
    out = ich.compute(ohlcv)
    assert not any("chikou_span" in c for c in out.columns)
    assert all("chikou_above_price" in c or "chikou_span" not in c for c in out.columns)

    # And the display helper really is non-causal -- which is why it is quarantined.
    display = chikou_span_display(ohlcv, 3)
    assert display.iloc[0] == pytest.approx(ohlcv["close"].iloc[3])
