"""Regime classification: stability, recovery of planted regimes, segmentation.

Three things are pinned here.

**A regime must not flap.** Classified naively, a quantile rule changes label
every time a value crosses a cut, and every regime-conditional statistic built on
top becomes noise. The confirmation rule is tested directly and end to end.

**A planted regime must be recovered.** A synthetic series with a deliberate
quiet-drift / violent-trend / choppy structure must come back with the right
axes in the right places. If the classifier cannot find a regime that was put
there on purpose, it will not find one that was not.

**Segmentation must expose a single-regime artefact.** The V1 audit found six of
fourteen surviving results were USDJPY during one exceptional trend.
:func:`regime_dependence` has to say so out loud on a synthetic case built to
look exactly like that.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason, Provenance
from fiboki.marketstate.features import FeatureConfig, FeatureEngine
from fiboki.marketstate.regime import (
    DirectionAxis,
    PersistenceAxis,
    RegimeAxis,
    RegimeClassifier,
    RegimeConfig,
    RegimeError,
    RegimeSeries,
    RegimeThresholds,
    RegimeVector,
    StressAxis,
    VolatilityAxis,
    confirm,
    describe_regimes,
    regime_column,
    regime_dependence,
    regime_segmented_performance,
)

# A fast-warming configuration so tests stay short without weakening the maths.
FAST_FEATURES = FeatureConfig(
    trend_horizons=(10, 25, 50),
    momentum_horizons=(5, 10, 25),
    vol_periods=(10, 50),
    variance_ratio_window=50,
    autocorr_window=50,
    moment_window=50,
    gap_window=50,
    abnormal_window=50,
    percentile_min_periods=50,
    primary_vol_period=10,
    primary_trend_horizon=25,
)
FAST_REGIME = RegimeConfig(direction_horizon=25, volatility_period=10)


def _frame(close: np.ndarray, *, start: str = "2020-01-06") -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range(start, periods=n, freq="4h", tz="UTC")
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(close) * 0.0008 + 1e-6
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + wick,
            "low": np.minimum(open_, close) - wick,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def _classify(close: np.ndarray, **kwargs) -> RegimeSeries:
    fs = FeatureEngine(timeframe="H4", config=FAST_FEATURES).compute(_frame(close))
    return RegimeClassifier(RegimeConfig(**{**_fast_kwargs(), **kwargs})).classify(fs)


def _fast_kwargs() -> dict:
    return {"direction_horizon": 25, "volatility_period": 10}


# =====================================================================
# The regime vector itself
# =====================================================================


def test_regime_key_round_trips() -> None:
    v = RegimeVector(
        DirectionAxis.STRONG_UP,
        VolatilityAxis.HIGH,
        PersistenceAxis.TRENDING,
        RegimeVector().liquidity,
        StressAxis.ELEVATED,
    )
    assert v.key.count("|") == 4
    assert RegimeVector.from_key(v.key) == v


def test_regime_key_order_is_fixed() -> None:
    """Stored results group on this string; reordering silently invalidates them."""
    v = RegimeVector(
        DirectionAxis.UP, VolatilityAxis.LOW, PersistenceAxis.RANDOM,
        RegimeVector().liquidity, StressAxis.CALM,
    )
    assert v.key.split("|")[0] == "up"
    assert v.key.split("|")[1] == "low"
    assert v.key.split("|")[2] == "random"
    assert v.key.split("|")[4] == "calm"


def test_malformed_key_is_refused() -> None:
    with pytest.raises(RegimeError):
        RegimeVector.from_key("up|low")


def test_thresholds_must_be_ordered_quantiles() -> None:
    with pytest.raises(RegimeError):
        RegimeThresholds(vol_low_q=0.8, vol_high_q=0.2)
    with pytest.raises(RegimeError):
        RegimeThresholds(vol_low_q=1.5)


# =====================================================================
# The anti-flap confirmation rule
# =====================================================================


def test_confirm_requires_consecutive_agreement() -> None:
    labels = ["a", "a", "a", "b", "a", "b", "b", "b", "b"]
    out = confirm(labels, min_dwell=3, unknown="u")
    # "a" is adopted on its third bar; the single "b" at index 3 is rejected;
    # the run of "b" from index 5 is adopted on its third bar (index 7).
    assert list(out) == ["u", "u", "a", "a", "a", "a", "a", "b", "b"]


def test_confirm_with_dwell_one_adopts_immediately() -> None:
    labels = ["a", "b", "a", "b"]
    assert list(confirm(labels, min_dwell=1, unknown="u")) == ["a", "b", "a", "b"]


def test_confirm_is_causal() -> None:
    labels = ["a"] * 20 + ["b"] * 20
    full = confirm(labels, min_dwell=3, unknown="u")
    prefix = confirm(labels[:25], min_dwell=3, unknown="u")
    assert list(full[:25]) == list(prefix)


def test_confirm_holds_through_unknown_gaps() -> None:
    labels = ["a", "a", "a", "u", "u", "a"]
    out = confirm(labels, min_dwell=3, unknown="u")
    assert list(out) == ["u", "u", "a", "a", "a", "a"]


def test_confirm_rejects_a_bad_dwell() -> None:
    with pytest.raises(RegimeError):
        confirm(["a"], min_dwell=0, unknown="u")


# =====================================================================
# Stability end to end
# =====================================================================


@pytest.fixture(scope="module")
def noisy_series() -> np.ndarray:
    rng = np.random.default_rng(21)
    return 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, 2500)))


def test_regimes_do_not_flap_every_bar(noisy_series: np.ndarray) -> None:
    """On a pure random walk the estimated axes must still be stable.

    Judged per axis. The combined rate is the *union* of four axes changing, so
    it is structurally about four times the per-axis rate and is checked at a
    correspondingly looser bound; these fixtures also use deliberately short
    windows so the tests stay fast, which makes every axis noisier than the
    production defaults.
    """
    r = _classify(noisy_series)
    for axis in (RegimeAxis.DIRECTION, RegimeAxis.VOLATILITY, RegimeAxis.PERSISTENCE):
        assert r.axis_flap_rate(axis) < 0.15, (
            f"{axis.value} changes on {r.axis_flap_rate(axis):.1%} of bars"
        )
        assert r.mean_duration(axis) > 5.0, (
            f"{axis.value} episodes average {r.mean_duration(axis):.1f} bars"
        )
    assert r.estimated_flap_rate < 0.25, (
        f"estimated axes change on {r.estimated_flap_rate:.1%} of bars; the "
        "labels are noise"
    )


def test_unconfirmed_classification_really_would_flap(noisy_series: np.ndarray) -> None:
    """The problem the dwell rule solves, demonstrated rather than asserted."""
    jumpy = _classify(noisy_series, min_dwell_bars=1)
    assert jumpy.axis_flap_rate(RegimeAxis.VOLATILITY) > 0.25
    assert jumpy.estimated_flap_rate > 0.35


def test_confirmation_reduces_flapping(noisy_series: np.ndarray) -> None:
    """The dwell rule must earn its lag."""
    jumpy = _classify(noisy_series, min_dwell_bars=1)
    steady = _classify(noisy_series, min_dwell_bars=5)
    assert steady.estimated_flap_rate < jumpy.estimated_flap_rate
    assert steady.mean_duration(RegimeAxis.DIRECTION) > jumpy.mean_duration(
        RegimeAxis.DIRECTION
    )


def test_session_axis_is_not_smoothed(noisy_series: np.ndarray) -> None:
    """The session is a calendar fact; smoothing it would blur the boundaries."""
    r = _classify(noisy_series)
    assert r.axis_flap_rate(RegimeAxis.LIQUIDITY) > r.axis_flap_rate(
        RegimeAxis.DIRECTION
    )
    # And the full key therefore inherits that cadence, which is why the
    # estimated-axis rate is the one to judge stability on.
    assert r.flap_rate > r.estimated_flap_rate


def test_transitions_and_durations_agree(noisy_series: np.ndarray) -> None:
    r = _classify(noisy_series)
    trans = r.transitions()
    dur = r.durations()
    assert len(trans) == int(dur["episodes"].sum()) - 1
    assert dur["share"].sum() == pytest.approx(1.0)
    assert (trans["previous_duration_bars"] > 0).all()
    assert set(trans["axes_changed"].str.split(",").explode()) <= {
        a.value for a in RegimeAxis
    }


def test_axis_distribution_sums_to_one(noisy_series: np.ndarray) -> None:
    r = _classify(noisy_series)
    for axis in RegimeAxis:
        assert r.axis_distribution(axis).sum() == pytest.approx(1.0)


def test_describe_regimes_is_reportable(noisy_series: np.ndarray) -> None:
    out = describe_regimes(_classify(noisy_series))
    for key in (
        "flap_rate", "estimated_flap_rate", "axis_distribution",
        "axis_mean_duration_bars", "mean_key_duration_bars", "n_transitions",
        "warmup_bars", "fingerprint",
    ):
        assert key in out
    assert out["bars_classified"] > 0


# =====================================================================
# Recovering planted regimes
# =====================================================================


def _planted_series() -> tuple[np.ndarray, dict[str, slice]]:
    """Four deliberately different regimes, back to back.

    * ``calm``    — quiet random walk, low volatility, no direction
    * ``rally``   — a clean strong uptrend
    * ``crisis``  — violent, high volatility, downward
    * ``chop``    — strongly mean-reverting, moderate volatility
    """
    rng = np.random.default_rng(77)
    n = 700
    calm = rng.normal(0.0, 0.0012, n)
    rally = rng.normal(0.0035, 0.0012, n)
    crisis = rng.normal(-0.012, 0.012, n)
    chop = np.zeros(n)
    prev = 0.0
    for i in range(n):
        shock = rng.normal(0.0, 0.004)
        prev = -0.75 * prev + shock  # strongly negative autocorrelation
        chop[i] = prev
    rets = np.concatenate([calm, rally, crisis, chop])
    close = 100.0 * np.exp(np.cumsum(rets))
    spans = {
        "calm": slice(200, n),
        "rally": slice(n + 200, 2 * n),
        "crisis": slice(2 * n + 200, 3 * n),
        "chop": slice(3 * n + 200, 4 * n),
    }
    return close, spans


def _dominant(r: RegimeSeries, axis: RegimeAxis, span: slice) -> str:
    return str(r.frame[axis.value].iloc[span].value_counts().idxmax())


@pytest.fixture(scope="module")
def planted() -> tuple[RegimeSeries, dict[str, slice]]:
    close, spans = _planted_series()
    fs = FeatureEngine(timeframe="H4", config=FAST_FEATURES).compute(_frame(close))
    return RegimeClassifier(RegimeConfig(**_fast_kwargs())).classify(fs), spans


def test_planted_direction_regimes_are_recovered(planted) -> None:
    r, spans = planted
    assert _dominant(r, RegimeAxis.DIRECTION, spans["rally"]) in (
        DirectionAxis.UP.value,
        DirectionAxis.STRONG_UP.value,
    )
    assert _dominant(r, RegimeAxis.DIRECTION, spans["crisis"]) in (
        DirectionAxis.DOWN.value,
        DirectionAxis.STRONG_DOWN.value,
    )
    assert _dominant(r, RegimeAxis.DIRECTION, spans["calm"]) == (
        DirectionAxis.NEUTRAL.value
    )


def test_planted_volatility_regimes_are_recovered(planted) -> None:
    r, spans = planted
    order = {
        VolatilityAxis.VERY_LOW.value: 0,
        VolatilityAxis.LOW.value: 1,
        VolatilityAxis.NORMAL.value: 2,
        VolatilityAxis.HIGH.value: 3,
        VolatilityAxis.EXTREME.value: 4,
    }
    calm = order[_dominant(r, RegimeAxis.VOLATILITY, spans["calm"])]
    crisis = order[_dominant(r, RegimeAxis.VOLATILITY, spans["crisis"])]
    assert crisis > calm
    assert crisis >= order[VolatilityAxis.HIGH.value]


def test_planted_persistence_regime_is_recovered(planted) -> None:
    r, spans = planted
    chop = r.frame[RegimeAxis.PERSISTENCE.value].iloc[spans["chop"]]
    rally = r.frame[RegimeAxis.PERSISTENCE.value].iloc[spans["rally"]]
    mr = PersistenceAxis.MEAN_REVERTING.value
    assert (chop == mr).mean() > 0.5
    assert (chop == mr).mean() > (rally == mr).mean()


def test_each_planted_span_has_a_dominant_key(planted) -> None:
    """A regime that is genuinely there should dominate its own span."""
    r, spans = planted
    for name, span in spans.items():
        labels = r.frame[RegimeAxis.DIRECTION.value].iloc[span]
        top = labels.value_counts(normalize=True).iloc[0]
        assert top > 0.45, f"{name}: no dominant direction label ({top:.0%})"


# =====================================================================
# Stress axis
# =====================================================================


def test_cross_asset_stress_inputs_are_consumed() -> None:
    close, _ = _planted_series()
    frame = _frame(close)
    fs = FeatureEngine(timeframe="H4", config=FAST_FEATURES).compute(frame)
    n = len(frame)
    spike = pd.DataFrame(
        {"avg_abs_correlation": np.concatenate([np.full(n - 300, 0.1), np.full(300, 0.9)])},
        index=frame.index,
    )
    base = RegimeClassifier(RegimeConfig(**_fast_kwargs())).classify(fs)
    with_stress = RegimeClassifier(RegimeConfig(**_fast_kwargs())).classify(
        fs, stress_inputs=spike
    )
    assert any("cross_asset:avg_abs_correlation" in n_ for n_ in with_stress.notes)
    stressed = with_stress.frame[RegimeAxis.STRESS.value].iloc[-200:]
    baseline = base.frame[RegimeAxis.STRESS.value].iloc[-200:]
    assert (stressed != StressAxis.CALM.value).mean() >= (
        baseline != StressAxis.CALM.value
    ).mean()


def test_missing_stress_features_are_reported_not_faked() -> None:
    close, _ = _planted_series()
    fs = FeatureEngine(timeframe="H4", config=FAST_FEATURES).compute(_frame(close))
    cfg = RegimeConfig(
        **_fast_kwargs(), stress_features=("gap_frequency", "does_not_exist")
    )
    r = RegimeClassifier(cfg).classify(fs)
    assert any("does_not_exist" in note for note in r.notes)


def test_no_usable_stress_input_is_an_error() -> None:
    close, _ = _planted_series()
    fs = FeatureEngine(timeframe="H4", config=FAST_FEATURES).compute(_frame(close))
    cfg = RegimeConfig(**_fast_kwargs(), stress_features=("nope",))
    with pytest.raises(RegimeError, match="no usable stress inputs"):
        RegimeClassifier(cfg).classify(fs)


def test_classifier_reports_a_missing_driving_feature() -> None:
    close, _ = _planted_series()
    fs = FeatureEngine(timeframe="H4", config=FAST_FEATURES).compute(_frame(close))
    cfg = RegimeConfig(direction_horizon=999, volatility_period=10)
    with pytest.raises(RegimeError, match="trend_tstat_999"):
        RegimeClassifier(cfg).classify(fs)


# =====================================================================
# Trade segmentation — the V1 artefact detector
# =====================================================================


def _trade(
    entry: pd.Timestamp, pnl: float, *, instrument: str = "EURUSD", strategy: str = "s1"
) -> Trade:
    return Trade(
        instrument=instrument,
        direction=Direction.LONG,
        size=10_000.0,
        entry_price=1.1,
        exit_price=1.1 + pnl / 10_000.0,
        entry_time=entry,
        exit_time=entry + pd.Timedelta(hours=8),
        exit_reason=ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS,
        gross_pnl=pnl,
        spread_cost=0.0,
        commission=0.0,
        slippage_cost=0.0,
        financing_cost=0.0,
        net_pnl=pnl,
        account_ccy="GBP",
        strategy_id=strategy,
        bars_held=2,
        provenance=Provenance.BACKTEST,
    )


def test_segment_trades_attaches_the_regime_current_at_entry(planted) -> None:
    r, _ = planted
    warm = r.frame.iloc[r.warmup :]
    bar = warm.index[100]
    # A trade entered 1 hour into an H4 bar belongs to the bar that has closed.
    t = _trade(bar + pd.Timedelta(hours=1), 50.0)
    seg = r.segment_trades([t])
    assert len(seg) == 1
    assert seg["regime_key"].iloc[0] == warm["regime_key"].iloc[100]
    # The trade's own direction survives the join intact: the regime axis is
    # prefixed precisely so the two cannot collide.
    assert seg["direction"].iloc[0] == "long"
    assert seg[regime_column(RegimeAxis.DIRECTION)].iloc[0] == (
        warm[RegimeAxis.DIRECTION.value].iloc[100]
    )


def test_trades_before_warmup_are_not_classified(planted) -> None:
    r, _ = planted
    early = r.frame.index[0] - pd.Timedelta(days=5)
    seg = r.segment_trades([_trade(early, 10.0)])
    assert seg["regime_key"].isna().all()


def test_segment_trades_handles_no_trades(planted) -> None:
    r, _ = planted
    assert regime_segmented_performance([], r).empty
    dep = regime_dependence([], r)
    assert dep.n_trades == 0
    assert "no trades" in dep.verdict


def test_regime_segmented_performance_columns_and_arithmetic(planted) -> None:
    r, _ = planted
    warm = r.frame.iloc[r.warmup :]
    rng = np.random.default_rng(3)
    trades = [
        _trade(ts + pd.Timedelta(hours=1), float(rng.normal(5.0, 40.0)))
        for ts in warm.index[::7]
    ]
    table = regime_segmented_performance(
        trades, r, axis=RegimeAxis.VOLATILITY, min_trades=10
    )
    assert set(table.columns) >= {
        "n_trades", "net_pnl", "mean_pnl", "median_pnl", "win_rate",
        "profit_factor", "pnl_std", "pnl_tstat", "pnl_share", "trade_share",
        "bars_share", "sufficient",
    }
    assert table["n_trades"].sum() == len(trades)
    assert table["trade_share"].sum() == pytest.approx(1.0)
    assert table["net_pnl"].sum() == pytest.approx(sum(t.net_pnl for t in trades))
    # No Sharpe: a bag of trade PnLs cannot honestly produce one.
    assert "sharpe" not in table.columns


def test_small_regime_buckets_are_marked_insufficient(planted) -> None:
    r, _ = planted
    warm = r.frame.iloc[r.warmup :]
    trades = [_trade(ts + pd.Timedelta(hours=1), 1.0) for ts in warm.index[:5]]
    table = regime_segmented_performance(trades, r, min_trades=30)
    assert not table["sufficient"].any()


def test_regime_dependence_flags_a_single_regime_artefact(planted) -> None:
    """The USDJPY finding, mechanised.

    Every profitable trade is placed in one volatility regime and every losing
    trade everywhere else, exactly the shape of a result that is really a
    statement about a regime.
    """
    r, _ = planted
    warm = r.frame.iloc[r.warmup :]
    target = warm[RegimeAxis.VOLATILITY.value].value_counts().idxmax()
    trades = []
    for ts in warm.index[::5]:
        in_target = warm.loc[ts, RegimeAxis.VOLATILITY.value] == target
        trades.append(_trade(ts + pd.Timedelta(hours=1), 120.0 if in_target else -8.0))
    dep = regime_dependence(trades, r, axis=RegimeAxis.VOLATILITY, min_trades=20)
    assert dep.top_regime == target
    assert dep.top_regime_pnl_share is not None and dep.top_regime_pnl_share > 0.9
    assert "regime-conditional" in dep.verdict
    assert dep.profitable_without_top is False


def test_regime_dependence_passes_a_genuinely_broad_edge(planted) -> None:
    r, _ = planted
    warm = r.frame.iloc[r.warmup :]
    rng = np.random.default_rng(12)
    trades = [
        _trade(ts + pd.Timedelta(hours=1), float(rng.normal(10.0, 15.0)))
        for ts in warm.index[::4]
    ]
    dep = regime_dependence(trades, r, axis=RegimeAxis.VOLATILITY, min_trades=20)
    assert dep.top_regime_pnl_share is not None
    assert dep.top_regime_pnl_share < 0.6
    assert "no single-regime artefact" in dep.verdict
    assert dep.profitable_without_top is True


def test_regime_dependence_reports_insufficient_samples(planted) -> None:
    r, _ = planted
    warm = r.frame.iloc[r.warmup :]
    trades = [_trade(ts + pd.Timedelta(hours=1), 5.0) for ts in warm.index[:6]]
    dep = regime_dependence(trades, r, axis=RegimeAxis.VOLATILITY, min_trades=1000)
    assert dep.n_regimes_sufficient == 0
    assert "cannot distinguish" in dep.verdict


def test_bars_share_reflects_market_time(planted) -> None:
    """How often a regime occurs is not how often it is traded — both are shown."""
    r, _ = planted
    warm = r.frame.iloc[r.warmup :]
    target = warm[RegimeAxis.VOLATILITY.value].value_counts().idxmax()
    in_target = warm.index[warm[RegimeAxis.VOLATILITY.value] == target]
    trades = [_trade(ts + pd.Timedelta(hours=1), 1.0) for ts in in_target[:40]]
    table = regime_segmented_performance(
        trades, r, axis=RegimeAxis.VOLATILITY, min_trades=10
    )
    assert table.loc[target, "trade_share"] == pytest.approx(1.0)
    assert table.loc[target, "bars_share"] < 1.0
