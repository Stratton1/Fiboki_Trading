"""The most important test in the market-state package.

Mirrors ``tests/unit/test_indicator_causality.py``: every published feature is
computed twice, once on clean bars and once on bars whose future has been
replaced with violently different prices, and every value at or before the cut
must be bit-identical. It is parametrised across *every* feature the engine
declares, so a new feature cannot be added without being proved causal.

It also pins the thing that is most tempting to get wrong: the percentiles are
expanding, and using the full sample instead would change the answer.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from synthetic_prices import synthetic_ohlcv

from fiboki.indicators.base import CausalityError
from fiboki.marketstate.features import (
    FEATURE_ENGINE_VERSION,
    FeatureConfig,
    FeatureEngine,
    FeatureError,
    InvalidBarError,
    assert_features_causal,
    corrupt_future,
    corwin_schultz_spread,
    drop_invalid_bars,
    expanding_rank_pct,
    hurst_from_variance_ratio,
    invalid_bar_mask,
    naive_expanding_rank_pct,
    rolling_ols_stats,
    session_flags,
    variance_ratio,
)

CUT_POINTS = (250, 400, 600)
N_BARS = 800


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_ohlcv(n=N_BARS)


@pytest.fixture(scope="module")
def engine() -> FeatureEngine:
    return FeatureEngine(timeframe="H4", instrument="TESTFX")


@pytest.fixture(scope="module")
def clean(engine: FeatureEngine, bars: pd.DataFrame) -> pd.DataFrame:
    return engine.compute(bars).frame


@pytest.fixture(scope="module")
def dirty(engine: FeatureEngine, bars: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """One corrupted-future recomputation per cut point, shared by the suite."""
    return {k: engine.compute(corrupt_future(bars, k)).frame for k in CUT_POINTS}


def _feature_names() -> list[str]:
    return list(FeatureEngine(timeframe="H4").feature_names())


FEATURES = _feature_names()


# =====================================================================
# Causality, parametrised across every feature
# =====================================================================


@pytest.mark.parametrize("name", FEATURES, ids=FEATURES)
@pytest.mark.parametrize("k", CUT_POINTS)
def test_feature_is_causal(
    name: str, k: int, clean: pd.DataFrame, dirty: dict[int, pd.DataFrame]
) -> None:
    a = clean[name].to_numpy(dtype=float)[: k + 1]
    b = dirty[k][name].to_numpy(dtype=float)[: k + 1]
    assert np.array_equal(np.isnan(a), np.isnan(b)), (
        f"{name}: NaN pattern changed at or before bar {k} when only later bars "
        "were altered"
    )
    finite = ~np.isnan(a)
    assert np.array_equal(a[finite], b[finite]), (
        f"{name}: values changed at or before bar {k}; the feature reads the future"
    )


def test_every_declared_feature_is_actually_produced(
    engine: FeatureEngine, clean: pd.DataFrame
) -> None:
    assert list(clean.columns) == list(engine.feature_names())
    assert len(clean.columns) == len(set(clean.columns))


def test_assert_features_causal_catches_a_planted_leak(bars: pd.DataFrame) -> None:
    """Negative control: the harness must fail on a deliberately leaky engine."""

    class Leaky(FeatureEngine):
        def _compute_columns(self, df, notes):  # type: ignore[override]
            out = super()._compute_columns(df, notes)
            # Tomorrow's close, published today.
            out["mom_roc_5"] = df["close"].shift(-1).to_numpy()
            return out

    leaky = Leaky(timeframe="H4")
    with pytest.raises(CausalityError):
        assert_features_causal(leaky, bars, 300, columns=["mom_roc_5"])


def test_compute_does_not_mutate_the_input(
    engine: FeatureEngine, bars: pd.DataFrame
) -> None:
    before = bars.copy(deep=True)
    engine.compute(bars)
    pd.testing.assert_frame_equal(bars, before)


@pytest.mark.parametrize("name", FEATURES, ids=FEATURES)
def test_truncation_equivalence(
    name: str, engine: FeatureEngine, bars: pd.DataFrame, clean: pd.DataFrame
) -> None:
    """Computing on ``df[:k+1]`` equals the first ``k+1`` rows of the full run.

    This is the property the state engine's bulk/incremental parity rests on.
    """
    k = 500
    partial = engine.compute(bars.iloc[: k + 1]).frame
    a = clean[name].to_numpy(dtype=float)[: k + 1]
    b = partial[name].to_numpy(dtype=float)
    assert np.array_equal(np.isnan(a), np.isnan(b)), name
    finite = ~np.isnan(a)
    assert np.array_equal(a[finite], b[finite]), name


# =====================================================================
# Warmup declarations
# =====================================================================


def test_every_feature_declares_a_warmup(engine: FeatureEngine) -> None:
    for spec in engine.specs:
        assert spec.warmup >= 1, spec.name
        assert spec.description, spec.name
        assert spec.group


@pytest.mark.parametrize("name", FEATURES, ids=FEATURES)
def test_feature_is_defined_by_its_declared_warmup(
    name: str, engine: FeatureEngine, clean: pd.DataFrame
) -> None:
    """A feature must be non-NaN from its declared warmup, or declare itself absent.

    An over-optimistic warmup is a silent source of NaN-driven behaviour changes
    downstream; an absent-by-nature feature (no quotes, no volume) is allowed to
    be NaN but must say so on its spec.
    """
    spec = engine.spec_for(name)
    tail = clean[name].iloc[spec.warmup :]
    if spec.may_be_unavailable and tail.isna().all():
        pytest.skip(f"{name} is legitimately unavailable on this dataset")
    assert tail.notna().any(), f"{name} is NaN everywhere after its declared warmup"
    # Allow a small residue: rolling statistics can legitimately produce NaN on
    # a degenerate window (zero variance), but not a systematic hole.
    assert tail.isna().mean() < 0.05, (
        f"{name} is NaN on {tail.isna().mean():.1%} of bars after its declared "
        f"warmup of {spec.warmup}; the declaration is wrong"
    )


def test_featureset_warmup_is_the_max_and_valid_slices_there(
    engine: FeatureEngine, bars: pd.DataFrame
) -> None:
    fs = engine.compute(bars)
    assert fs.warmup == max(s.warmup for s in fs.specs)
    assert len(fs.valid()) == len(fs.frame) - fs.warmup
    assert fs.ready_from == fs.frame.index[fs.warmup]


# =====================================================================
# Fingerprint
# =====================================================================


def test_fingerprint_is_stable_and_config_sensitive() -> None:
    a = FeatureEngine(timeframe="H4")
    b = FeatureEngine(timeframe="H4")
    assert a.fingerprint == b.fingerprint
    c = FeatureEngine(timeframe="H1")
    assert c.fingerprint != a.fingerprint
    d = FeatureEngine(timeframe="H4", config=FeatureConfig(atr_period=21))
    assert d.fingerprint != a.fingerprint


def test_fingerprint_travels_with_the_result(
    engine: FeatureEngine, bars: pd.DataFrame
) -> None:
    fs = engine.compute(bars)
    assert fs.fingerprint == engine.fingerprint
    assert len(fs.fingerprint) == 32
    assert fs.to_summary()["fingerprint"] == fs.fingerprint
    assert FEATURE_ENGINE_VERSION.count(".") == 2


# =====================================================================
# Expanding vs full-sample percentile — the look-ahead that looks fine
# =====================================================================


def test_expanding_rank_matches_the_naive_reference() -> None:
    rng = np.random.default_rng(11)
    values = rng.normal(size=400)
    values[17] = np.nan
    values[200] = values[199]  # a tie, to pin the mid-rank convention
    fast = expanding_rank_pct(values, min_periods=10)
    slow = naive_expanding_rank_pct(values, min_periods=10)
    assert np.array_equal(np.isnan(fast), np.isnan(slow))
    ok = ~np.isnan(fast)
    assert np.allclose(fast[ok], slow[ok])


def test_expanding_rank_only_sees_the_past() -> None:
    """Corrupting the tail must not move any earlier rank."""
    rng = np.random.default_rng(5)
    values = rng.normal(size=300)
    mutated = values.copy()
    mutated[150:] = 1e6  # a colossal future
    a = expanding_rank_pct(values, min_periods=10)
    b = expanding_rank_pct(mutated, min_periods=10)
    assert np.allclose(a[:150], b[:150], equal_nan=True)


def test_full_sample_percentile_would_leak_and_differ() -> None:
    """The reason the expanding version exists, demonstrated numerically.

    A series whose volatility regime shifts late in the sample: under a
    full-sample rank the early bars are pushed towards the bottom of a
    distribution that includes a crisis they had not seen. The two answers
    differ by a lot, so 'it is basically the same' is not available as a defence.
    """
    rng = np.random.default_rng(3)
    calm = np.abs(rng.normal(1.0, 0.1, 500))
    crisis = np.abs(rng.normal(6.0, 1.0, 400))
    series = np.concatenate([calm, crisis])

    expanding = expanding_rank_pct(series, min_periods=50)
    order = series.argsort().argsort().astype(float)
    full_sample = (order + 0.5) / series.size

    ok = ~np.isnan(expanding)
    early = ok.copy()
    early[500:] = False
    # Early-sample bars look much calmer under the full-sample rank, because the
    # denominator already contains the crisis.
    assert full_sample[early].mean() < expanding[early].mean() - 0.15
    assert np.abs(expanding[ok] - full_sample[ok]).max() > 0.3


def test_engine_uses_expanding_not_full_sample_percentiles(
    clean: pd.DataFrame, bars: pd.DataFrame, engine: FeatureEngine
) -> None:
    """The published percentile must equal the expanding rank of its own source."""
    src = clean["rv_20"].to_numpy(dtype=float)
    expected = expanding_rank_pct(
        src, min_periods=engine.config.percentile_min_periods
    )
    got = clean["rv_pct_20"].to_numpy(dtype=float)
    assert np.array_equal(np.isnan(expected), np.isnan(got))
    ok = ~np.isnan(got)
    assert np.allclose(got[ok], expected[ok])

    order = pd.Series(src).rank(pct=True).to_numpy()
    both = ok & ~np.isnan(order)
    assert np.abs(got[both] - order[both]).max() > 0.05, (
        "the published percentile is indistinguishable from a full-sample rank; "
        "either the data is degenerate or the expanding window has been lost"
    )


# =====================================================================
# Invalid bars (the HistData sentinel)
# =====================================================================


def _with_sentinel(bars: pd.DataFrame, pos: int = 120) -> pd.DataFrame:
    """Reproduce the EURUSD H1 defect: one bar with OHLC all -0.0001."""
    out = bars.copy()
    for col in ("open", "high", "low", "close"):
        out.iloc[pos, out.columns.get_loc(col)] = -0.0001
    return out


def test_sentinel_bar_is_detected(bars: pd.DataFrame) -> None:
    poisoned = _with_sentinel(bars)
    mask = invalid_bar_mask(poisoned)
    assert mask.sum() == 1
    assert bool(mask[120])
    assert not invalid_bar_mask(bars).any()


def test_engine_refuses_a_sentinel_by_default(bars: pd.DataFrame) -> None:
    engine = FeatureEngine(timeframe="H4")
    with pytest.raises(InvalidBarError, match="not prices"):
        engine.compute(_with_sentinel(bars))


def test_dropping_a_sentinel_is_recorded_and_does_not_poison_statistics(
    bars: pd.DataFrame,
) -> None:
    poisoned = _with_sentinel(bars)
    engine = FeatureEngine(timeframe="H4", on_invalid_bars="drop")
    fs = engine.compute(poisoned)
    assert fs.dropped_bars.count == 1
    assert fs.dropped_bars.timestamps[0] == bars.index[120]
    assert any("dropped 1 invalid bar" in n for n in fs.notes)
    assert len(fs.frame) == len(bars) - 1
    # Nothing infinite, and volatility does not explode: a retained -0.0001 bar
    # produces log returns of order 1e4.
    tail = fs.valid()
    assert np.isfinite(tail["rv_20"].dropna().to_numpy()).all()
    assert tail["rv_20"].max() < 1.0


def test_drop_invalid_bars_is_a_no_op_on_clean_data(bars: pd.DataFrame) -> None:
    out, dropped = drop_invalid_bars(bars)
    assert dropped.count == 0
    assert out is bars


# =====================================================================
# Input validation
# =====================================================================


def test_naive_index_is_refused(bars: pd.DataFrame) -> None:
    naive = bars.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(FeatureError, match="tz-naive"):
        FeatureEngine(timeframe="H4").compute(naive)


def test_unsorted_and_duplicated_indices_are_refused(bars: pd.DataFrame) -> None:
    eng = FeatureEngine(timeframe="H4")
    with pytest.raises(FeatureError, match="monotonic"):
        eng.compute(bars.iloc[::-1])
    dup = pd.concat([bars.iloc[:10], bars.iloc[9:20]])
    with pytest.raises(FeatureError, match="duplicate|monotonic"):
        eng.compute(dup)


def test_bad_config_is_refused() -> None:
    with pytest.raises(FeatureError):
        FeatureConfig(primary_vol_period=7)
    with pytest.raises(FeatureError):
        FeatureConfig(primary_trend_horizon=33)
    with pytest.raises(FeatureError):
        FeatureConfig(percentile_min_periods=5)


# =====================================================================
# Absence is a value
# =====================================================================


def test_absent_volume_is_flagged_not_zeroed(bars: pd.DataFrame) -> None:
    no_vol = bars.copy()
    no_vol["volume"] = -1  # the canonical absent marker
    fs = FeatureEngine(timeframe="H4").compute(no_vol)
    assert (fs.frame["volume_available"] == 0.0).all()
    assert fs.frame["volume_z"].isna().all()
    assert any("volume is absent" in n for n in fs.notes)

    zeros = bars.copy()
    zeros["volume"] = 0
    fs0 = FeatureEngine(timeframe="H4").compute(zeros)
    assert (fs0.frame["volume_available"] == 0.0).all()


def test_present_volume_is_used(bars: pd.DataFrame) -> None:
    fs = FeatureEngine(timeframe="H4").compute(bars)
    assert (fs.frame["volume_available"] == 1.0).all()
    assert fs.frame["volume_z"].iloc[-1] == pytest.approx(
        fs.frame["volume_z"].iloc[-1]
    )
    assert fs.frame["volume_z"].notna().any()


def test_quoted_spread_is_used_when_present(bars: pd.DataFrame) -> None:
    quoted = bars.copy()
    quoted["bid_close"] = quoted["close"] - 0.00005
    quoted["ask_close"] = quoted["close"] + 0.00005
    fs = FeatureEngine(timeframe="H4").compute(quoted)
    assert (fs.frame["spread_available"] == 1.0).all()
    assert fs.frame["spread_rel"].notna().all()
    assert fs.frame["spread_pct"].iloc[-1] == pytest.approx(
        fs.frame["spread_pct"].iloc[-1]
    )


def test_absent_spread_is_nan_not_a_modelled_constant(bars: pd.DataFrame) -> None:
    fs = FeatureEngine(timeframe="H4").compute(bars)
    assert (fs.frame["spread_available"] == 0.0).all()
    assert fs.frame["spread_rel"].isna().all()
    # The high/low estimator still works without quotes, which is the point.
    assert fs.frame["cs_spread_rel"].notna().any()


# =====================================================================
# Individual primitives
# =====================================================================


def test_rolling_ols_recovers_a_planted_slope() -> None:
    n, w = 300, 50
    slope = 0.0013
    y = slope * np.arange(n, dtype=float) + 5.0
    s, r2, t = rolling_ols_stats(y, w)
    assert np.isnan(s[: w - 1]).all()
    assert np.allclose(s[w - 1 :], slope)
    assert np.allclose(r2[w - 1 :], 1.0)
    # A perfect fit has (numerically) zero residual variance, so the t-stat is
    # either undefined or enormous. Both are acceptable; a small one is not.
    tail = t[w - 1 :]
    finite = tail[np.isfinite(tail)]
    assert finite.size == 0 or np.abs(finite).min() > 1e4


def test_rolling_ols_direction_and_strength_respond_as_expected() -> None:
    rng = np.random.default_rng(2)
    n, w = 400, 50
    trend = 0.002 * np.arange(n) + rng.normal(0, 0.002, n)
    noise = rng.normal(0, 0.02, n)
    s_t, r2_t, t_t = rolling_ols_stats(trend, w)
    s_n, r2_n, t_n = rolling_ols_stats(noise, w)
    assert np.nanmean(r2_t) > np.nanmean(r2_n)
    assert np.nanmean(t_t) > 0
    assert abs(np.nanmean(t_n)) < abs(np.nanmean(t_t))


def test_rolling_ols_refuses_non_finite_input() -> None:
    y = np.arange(100, dtype=float)
    y[10] = np.nan
    with pytest.raises(FeatureError, match="finite"):
        rolling_ols_stats(y, 20)


def test_variance_ratio_separates_trending_from_mean_reverting() -> None:
    rng = np.random.default_rng(9)
    n = 3000
    # A random walk: VR ~ 1.
    rw = np.cumsum(rng.normal(0, 0.01, n))
    # A strongly mean-reverting series: VR < 1.
    mr = np.zeros(n)
    for i in range(1, n):
        mr[i] = 0.2 * mr[i - 1] + rng.normal(0, 0.01)
    # A trending series (positively autocorrelated increments): VR > 1.
    inc = np.zeros(n)
    for i in range(1, n):
        inc[i] = 0.5 * inc[i - 1] + rng.normal(0, 0.01)
    tr = np.cumsum(inc)

    idx = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
    vr_rw = variance_ratio(pd.Series(rw, index=idx), lag=2, window=250).median()
    vr_mr = variance_ratio(pd.Series(mr, index=idx), lag=2, window=250).median()
    vr_tr = variance_ratio(pd.Series(tr, index=idx), lag=2, window=250).median()
    assert vr_mr < 0.8 < vr_rw < 1.2 < vr_tr
    assert abs(vr_rw - 1.0) < 0.15


def test_hurst_matches_the_variance_ratio_identity() -> None:
    # VR(q) = q**(2H-1), so at q=2: H=0 -> 0.5, H=0.5 -> 1.0, H=1 -> 2.0.
    vr = pd.Series([0.5, 1.0, 2.0])
    h = hurst_from_variance_ratio(vr, 2)
    assert h.iloc[0] == pytest.approx(0.0)
    assert h.iloc[1] == pytest.approx(0.5)
    assert h.iloc[2] == pytest.approx(1.0)
    # A non-positive variance ratio is not exponentiable; it must be NaN, never
    # a complex number silently cast to a float.
    assert np.isnan(hurst_from_variance_ratio(pd.Series([0.0, -1.0]), 2)).all()


def test_corwin_schultz_is_non_negative_and_rises_with_the_range() -> None:
    n = 400
    idx = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
    close = pd.Series(np.full(n, 100.0), index=idx)
    tight = corwin_schultz_spread(close * 1.0005, close * 0.9995, smooth_window=20)[1]
    wide = corwin_schultz_spread(close * 1.01, close * 0.99, smooth_window=20)[1]
    assert (tight.dropna() >= 0).all()
    assert wide.dropna().mean() >= tight.dropna().mean()


def test_session_flags_handle_daylight_saving() -> None:
    """London opens at 08:00 local — 08:00 UTC in winter, 07:00 UTC in summer."""
    winter = pd.DatetimeIndex(
        pd.date_range("2021-01-13 06:00", periods=6, freq="1h", tz="UTC")
    )
    summer = pd.DatetimeIndex(
        pd.date_range("2021-07-14 06:00", periods=6, freq="1h", tz="UTC")
    )
    w = session_flags(winter)["london"].to_numpy()
    s = session_flags(summer)["london"].to_numpy()
    assert not w[0] and not w[1]  # 06:00, 07:00 UTC closed in winter
    assert w[2]  # 08:00 UTC open
    assert not s[0]  # 06:00 UTC closed in summer
    assert s[1]  # 07:00 UTC == 08:00 London open
    assert math.isclose(1.0, 1.0)


def test_session_codes_mark_the_overlap() -> None:
    # 14:00 UTC in July: London (15:00 local) and New York (10:00 local) both open.
    idx = pd.DatetimeIndex([pd.Timestamp("2021-07-14 14:00", tz="UTC")])
    flags = session_flags(idx)
    assert bool(flags["london"].iloc[0])
    assert bool(flags["new_york"].iloc[0])
    assert int(flags["session_code"].iloc[0]) == 5


def test_weekend_bars_are_off_hours() -> None:
    idx = pd.DatetimeIndex([pd.Timestamp("2021-07-17 12:00", tz="UTC")])  # Saturday
    assert int(session_flags(idx)["session_code"].iloc[0]) == 0


def test_session_flags_require_a_tz(bars: pd.DataFrame) -> None:
    with pytest.raises(FeatureError):
        session_flags(pd.DatetimeIndex(bars.index.tz_localize(None)))


# =====================================================================
# Feature semantics that would be easy to get backwards
# =====================================================================


def test_breakout_state_uses_the_prior_channel(engine: FeatureEngine) -> None:
    """A bar cannot break a channel that contains its own high."""
    n = 200
    idx = pd.date_range("2021-01-04", periods=n, freq="4h", tz="UTC")
    close = np.full(n, 100.0)
    close[-1] = 120.0  # one decisive upside break
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )
    out = FeatureEngine(timeframe="H4").compute(frame).frame
    assert out["breakout_state"].iloc[-1] == 1.0
    assert (out["breakout_state"].iloc[30:-1] == 0.0).all()
    assert out["bars_since_breakout"].iloc[-1] == 0.0


def test_gap_and_abnormal_move_flags_fire_on_a_planted_shock() -> None:
    n = 600
    idx = pd.date_range("2021-01-04", periods=n, freq="4h", tz="UTC")
    rng = np.random.default_rng(4)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    close[500:] *= 1.25  # a 25% overnight gap
    open_ = np.concatenate([[close[0]], close[:-1]])
    open_[500] = close[500]  # the bar opens at the gapped level
    frame = pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.001,
            "low": np.minimum(open_, close) * 0.999,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )
    out = FeatureEngine(timeframe="H4").compute(frame).frame
    assert abs(out["gap_atr"].iloc[500]) > 5.0
    assert out["abnormal_move"].iloc[500] == 1.0
    assert out["abnormal_frequency"].iloc[520] > 0.0


def test_range_position_is_bounded(clean: pd.DataFrame) -> None:
    v = clean["range_position"].dropna()
    assert (v >= 0.0).all() and (v <= 1.0).all()


def test_liquidity_proxy_is_bounded_and_off_hours_is_thinnest(
    clean: pd.DataFrame,
) -> None:
    v = clean["liquidity_proxy"].dropna()
    assert (v >= 0.0).all() and (v <= 1.0).all()
    off = clean[clean["session_code"] == 0]["liquidity_proxy"].dropna()
    overlap = clean[clean["session_code"] == 5]["liquidity_proxy"].dropna()
    if len(off) and len(overlap):
        assert off.mean() < overlap.mean()


def test_calendar_gap_flag_marks_the_weekend() -> None:
    n = 400
    idx = pd.date_range("2021-01-04", periods=n, freq="4h", tz="UTC")
    keep = idx[idx.weekday < 5]  # drop the weekend bars entirely
    rng = np.random.default_rng(6)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, len(keep))))
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.full(len(keep), 1000.0),
        },
        index=keep,
    )
    out = FeatureEngine(timeframe="H4").compute(frame).frame
    flagged = out["is_calendar_gap"].fillna(0.0).to_numpy()
    assert flagged.sum() > 0
    mondays = out.index[flagged == 1.0].weekday
    assert set(mondays) <= {0}
