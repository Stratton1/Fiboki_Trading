"""Cross-asset statistics: planted correlation recovery, honest alignment, lead-lag.

The alignment tests matter as much as the maths. Two instruments' bar files do
not share a clock, and the usual fix — reindex and forward-fill — manufactures
observations that do not move, which biases correlation towards zero exactly
when correlation matters most. This module inner-joins and reports coverage, and
these tests pin that behaviour.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fiboki.marketstate.cross_asset import (
    AlignedPanel,
    CrossAssetError,
    align_panel,
    build_cross_asset_state,
    correlation_breakdown,
    currency_strength,
    lead_lag,
    risk_appetite,
    rolling_correlation,
)


def _frame_from_returns(rets: np.ndarray, idx: pd.DatetimeIndex, base: float) -> pd.DataFrame:
    close = base * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.0005,
            "low": np.minimum(open_, close) * 0.9995,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        },
        index=idx,
    )


def _index(n: int, start: str = "2020-01-06") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="4h", tz="UTC")


def _correlated(rho: float, n: int, seed: int, sigma: float = 0.004):
    """Two return series with correlation exactly ``rho`` in expectation."""
    rng = np.random.default_rng(seed)
    a = rng.normal(0.0, 1.0, n)
    b = rho * a + math_sqrt(1.0 - rho**2) * rng.normal(0.0, 1.0, n)
    return a * sigma, b * sigma


def math_sqrt(x: float) -> float:
    return float(np.sqrt(x))


# =====================================================================
# Alignment and coverage
# =====================================================================


def test_inner_join_keeps_only_common_timestamps() -> None:
    idx = _index(500)
    a = _frame_from_returns(np.random.default_rng(1).normal(0, 0.004, 500), idx, 1.1)
    b_idx = idx[100:]
    b = _frame_from_returns(
        np.random.default_rng(2).normal(0, 0.004, len(b_idx)), b_idx, 1.3
    )
    panel = align_panel({"EURUSD": a, "GBPUSD": b})
    assert len(panel) == 400
    cov = panel.coverage
    per = {c.instrument: c for c in cov.per_instrument}
    assert per["EURUSD"].bars_supplied == 500
    assert per["EURUSD"].bars_used == 400
    assert per["EURUSD"].coverage == pytest.approx(0.8)
    assert per["GBPUSD"].coverage == pytest.approx(1.0)
    assert cov.worst_coverage == pytest.approx(0.8)


def test_thin_overlap_is_reported_not_hidden() -> None:
    idx = _index(500)
    a = _frame_from_returns(np.random.default_rng(1).normal(0, 0.004, 500), idx, 1.1)
    b_idx = idx[450:]
    b = _frame_from_returns(
        np.random.default_rng(2).normal(0, 0.004, len(b_idx)), b_idx, 1.3
    )
    cov = align_panel({"EURUSD": a, "GBPUSD": b}).coverage
    warnings = cov.warnings()
    assert warnings
    assert "EURUSD" in warnings[0]
    assert "10.0%" in warnings[0]


def test_no_forward_fill_happens() -> None:
    """A missing bar must be dropped from the panel, never carried forward."""
    idx = _index(300)
    a = _frame_from_returns(np.random.default_rng(1).normal(0, 0.004, 300), idx, 1.1)
    keep = idx.delete([50, 51, 52])
    b = a.loc[keep]
    panel = align_panel({"EURUSD": a, "GBPUSD": b})
    assert len(panel) == 297
    assert idx[50] not in panel.prices.index


def test_a_panel_needs_two_instruments() -> None:
    idx = _index(100)
    a = _frame_from_returns(np.random.default_rng(1).normal(0, 0.004, 100), idx, 1.1)
    with pytest.raises(CrossAssetError, match="at least two"):
        align_panel({"EURUSD": a})


def test_a_panel_with_no_overlap_is_refused() -> None:
    a = _frame_from_returns(
        np.random.default_rng(1).normal(0, 0.004, 100), _index(100, "2020-01-06"), 1.1
    )
    b = _frame_from_returns(
        np.random.default_rng(2).normal(0, 0.004, 100), _index(100, "2021-01-04"), 1.3
    )
    with pytest.raises(CrossAssetError, match="common timestamps"):
        align_panel({"EURUSD": a, "GBPUSD": b})


def test_naive_index_is_refused() -> None:
    idx = _index(100)
    a = _frame_from_returns(np.random.default_rng(1).normal(0, 0.004, 100), idx, 1.1)
    b = a.copy()
    b.index = b.index.tz_localize(None)
    with pytest.raises(CrossAssetError, match="tz-aware"):
        align_panel({"EURUSD": a, "GBPUSD": b})


# =====================================================================
# Rolling correlation recovers a planted correlation
# =====================================================================


@pytest.mark.parametrize("rho", [-0.7, 0.0, 0.5, 0.9])
def test_rolling_correlation_recovers_the_planted_value(rho: float) -> None:
    n = 4000
    idx = _index(n)
    ra, rb = _correlated(rho, n, seed=int(abs(rho) * 100) + 5)
    panel = align_panel(
        {
            "EURUSD": _frame_from_returns(ra, idx, 1.1),
            "GBPUSD": _frame_from_returns(rb, idx, 1.3),
        }
    )
    corr = rolling_correlation(panel, window=500)
    measured = corr.pair("EURUSD", "GBPUSD").median()
    assert measured == pytest.approx(rho, abs=0.06), (
        f"planted {rho}, recovered {measured:.3f}"
    )


def test_correlation_is_causal_over_a_regime_change() -> None:
    """A correlation break must not show up before it happens."""
    n = 4000
    idx = _index(n)
    ra_lo, rb_lo = _correlated(0.0, n // 2, seed=1)
    ra_hi, rb_hi = _correlated(0.9, n // 2, seed=2)
    a = np.concatenate([ra_lo, ra_hi])
    b = np.concatenate([rb_lo, rb_hi])
    panel = align_panel(
        {
            "EURUSD": _frame_from_returns(a, idx, 1.1),
            "GBPUSD": _frame_from_returns(b, idx, 1.3),
        }
    )
    corr = rolling_correlation(panel, window=250)
    series = corr.pair("EURUSD", "GBPUSD")
    # Well before the break: still uncorrelated. Well after: correlated.
    assert abs(series.iloc[n // 2 - 300]) < 0.25
    assert series.iloc[-1] > 0.75


def test_correlation_matrix_is_symmetric_with_a_unit_diagonal() -> None:
    n = 2000
    idx = _index(n)
    ra, rb = _correlated(0.6, n, seed=8)
    rc = np.random.default_rng(9).normal(0, 0.004, n)
    panel = align_panel(
        {
            "EURUSD": _frame_from_returns(ra, idx, 1.1),
            "GBPUSD": _frame_from_returns(rb, idx, 1.3),
            "USDJPY": _frame_from_returns(rc, idx, 110.0),
        }
    )
    corr = rolling_correlation(panel, window=500)
    m = corr.matrix_at(idx[-1])
    assert np.allclose(np.diag(m.to_numpy()), 1.0)
    assert np.allclose(m.to_numpy(), m.to_numpy().T)
    assert m.loc["EURUSD", "GBPUSD"] == pytest.approx(0.6, abs=0.1)


def test_average_correlation_and_breakdown_detect_convergence() -> None:
    n = 4000
    idx = _index(n)
    ra_lo, rb_lo = _correlated(0.0, n // 2, seed=11)
    ra_hi, rb_hi = _correlated(0.95, n // 2, seed=12)
    panel = align_panel(
        {
            "EURUSD": _frame_from_returns(np.concatenate([ra_lo, ra_hi]), idx, 1.1),
            "GBPUSD": _frame_from_returns(np.concatenate([rb_lo, rb_hi]), idx, 1.3),
        }
    )
    corr = rolling_correlation(panel, window=250)
    bd = correlation_breakdown(corr, baseline_window=500)
    assert bd["avg_abs_correlation"].iloc[-1] > 0.8
    # The z-score spikes while the correlation is moving away from its own
    # trailing baseline, then decays as the baseline absorbs the new level —
    # it is a change detector, not a level detector, and is documented as one.
    assert bd["z"].iloc[n // 2 : n // 2 + 600].max() > 2.0
    assert bd["z"].iloc[: n // 2 - 300].max() < 2.0


def test_correlation_window_must_be_meaningful() -> None:
    idx = _index(200)
    ra, rb = _correlated(0.5, 200, seed=3)
    panel = align_panel(
        {
            "EURUSD": _frame_from_returns(ra, idx, 1.1),
            "GBPUSD": _frame_from_returns(rb, idx, 1.3),
        }
    )
    with pytest.raises(CrossAssetError, match="below 5 bars"):
        rolling_correlation(panel, window=3)


# =====================================================================
# Currency strength
# =====================================================================


def test_currency_strength_recovers_planted_strengths() -> None:
    """Build pairs from known per-currency strengths; recover them."""
    n = 2000
    idx = _index(n)
    rng = np.random.default_rng(31)
    # Per-bar currency log-return contributions, constrained to sum to zero.
    usd = rng.normal(0.0, 0.002, n)
    eur = rng.normal(0.0, 0.002, n)
    gbp = rng.normal(0.0, 0.002, n)
    jpy = -(usd + eur + gbp)
    pairs = {
        "EURUSD": eur - usd,
        "GBPUSD": gbp - usd,
        "USDJPY": usd - jpy,
        "EURJPY": eur - jpy,
        "EURGBP": eur - gbp,
    }
    frames = {
        name: _frame_from_returns(r, idx, 1.1 if name != "USDJPY" else 110.0)
        for name, r in pairs.items()
    }
    panel = align_panel(frames)
    cs = currency_strength(panel, window=10)
    assert set(cs.currencies) == {"EUR", "GBP", "JPY", "USD"}
    # The system is over-determined and consistent, so it fits essentially exactly.
    assert cs.residual_rms.median(skipna=True) < 1e-12

    truth = pd.DataFrame(
        {"USD": usd, "EUR": eur, "GBP": gbp, "JPY": jpy}, index=idx
    )
    truth_window = truth.rolling(10).sum()
    for ccy in cs.currencies:
        got = cs.strength[ccy].dropna()
        want = truth_window[ccy].reindex(got.index)
        assert np.corrcoef(got, want)[0, 1] > 0.999, ccy
        assert np.abs(got - want).max() < 1e-10, ccy


def test_currency_strength_sums_to_zero() -> None:
    """The identifying constraint must actually hold in the output."""
    n = 800
    idx = _index(n)
    rng = np.random.default_rng(13)
    frames = {
        s: _frame_from_returns(rng.normal(0, 0.004, n), idx, 1.1)
        for s in ("EURUSD", "GBPUSD", "EURGBP")
    }
    cs = currency_strength(align_panel(frames), window=20)
    totals = cs.strength.dropna().sum(axis=1)
    assert np.abs(totals).max() < 1e-12


def test_currency_strength_ignores_non_fx_and_says_so() -> None:
    n = 600
    idx = _index(n)
    rng = np.random.default_rng(14)
    frames = {
        "EURUSD": _frame_from_returns(rng.normal(0, 0.004, n), idx, 1.1),
        "GBPUSD": _frame_from_returns(rng.normal(0, 0.004, n), idx, 1.3),
        "XAUUSD": _frame_from_returns(rng.normal(0, 0.008, n), idx, 1800.0),
    }
    cs = currency_strength(align_panel(frames), window=20)
    # Gold is not a currency: "XAU strength" and "USD weakness" are the same
    # observation and including both makes the system meaningless.
    assert "XAU" not in cs.currencies
    assert set(cs.currencies) == {"EUR", "GBP", "USD"}


def test_currency_strength_needs_fx_pairs() -> None:
    n = 400
    idx = _index(n)
    rng = np.random.default_rng(15)
    frames = {
        "XAUUSD": _frame_from_returns(rng.normal(0, 0.008, n), idx, 1800.0),
        "XAGUSD": _frame_from_returns(rng.normal(0, 0.01, n), idx, 24.0),
    }
    with pytest.raises(CrossAssetError, match="at least two registered FX"):
        currency_strength(align_panel(frames), window=20)


# =====================================================================
# Risk appetite
# =====================================================================


def test_risk_appetite_rises_when_the_pro_cyclical_basket_outperforms() -> None:
    n = 2000
    idx = _index(n)
    rng = np.random.default_rng(41)
    noise = lambda: rng.normal(0.0, 0.003, n)  # noqa: E731
    risk_on = np.concatenate([noise()[: n // 2], noise()[n // 2 :] + 0.004])
    frames = {
        "AUDUSD": _frame_from_returns(risk_on, idx, 0.7),
        "NZDUSD": _frame_from_returns(risk_on, idx, 0.65),
        "USDJPY": _frame_from_returns(noise(), idx, 110.0),
        "USDCHF": _frame_from_returns(noise(), idx, 0.95),
    }
    ra = risk_appetite(align_panel(frames), window=20, zscore_window=250)
    assert set(ra.used_risk_on) == {"AUDUSD", "NZDUSD"}
    assert set(ra.used_risk_off) == {"USDCHF", "USDJPY"}
    # The score rises as the pro-cyclical basket pulls away, then decays as its
    # own trailing z-score baseline catches up with the new level: it is a
    # change detector, not a level detector.
    before = ra.score.iloc[: n // 2].mean()
    after = ra.score.iloc[n // 2 : n // 2 + 300].mean()
    assert after > before + 1.0, f"before {before:.2f}, after {after:.2f}"
    assert after > 1.0


def test_risk_appetite_refuses_an_incomplete_basket() -> None:
    n = 400
    idx = _index(n)
    rng = np.random.default_rng(42)
    frames = {
        "EURUSD": _frame_from_returns(rng.normal(0, 0.004, n), idx, 1.1),
        "GBPUSD": _frame_from_returns(rng.normal(0, 0.004, n), idx, 1.3),
    }
    with pytest.raises(CrossAssetError, match="one instrument on each leg"):
        risk_appetite(align_panel(frames))


# =====================================================================
# Lead-lag
# =====================================================================


def test_lead_lag_finds_a_planted_one_bar_lead() -> None:
    n = 6000
    idx = _index(n)
    rng = np.random.default_rng(51)
    leader = rng.normal(0.0, 0.004, n)
    # The follower repeats the leader's previous-bar return, plus its own noise.
    follower = np.concatenate([[0.0], leader[:-1]]) * 0.9 + rng.normal(0.0, 0.0012, n)
    panel = align_panel(
        {
            "GBPUSD": _frame_from_returns(leader, idx, 1.3),
            "EURUSD": _frame_from_returns(follower, idx, 1.1),
        }
    )
    ll = lead_lag(panel, target="EURUSD", candidate="GBPUSD", window=500, max_lag=4)
    assert ll.summary()["modal_lag"] == 1
    assert ll.by_lag["lag_1"].median() > 0.8
    assert abs(ll.by_lag["lag_0"].median()) < 0.3


def test_lead_lag_reports_no_lead_when_there_is_none() -> None:
    n = 4000
    idx = _index(n)
    ra, rb = _correlated(0.8, n, seed=52)
    panel = align_panel(
        {
            "EURUSD": _frame_from_returns(ra, idx, 1.1),
            "GBPUSD": _frame_from_returns(rb, idx, 1.3),
        }
    )
    ll = lead_lag(panel, target="EURUSD", candidate="GBPUSD", window=500, max_lag=4)
    assert ll.summary()["modal_lag"] == 0
    assert ll.by_lag["lag_0"].median() > 0.7
    for lag in (1, 2, 3, 4):
        assert abs(ll.by_lag[f"lag_{lag}"].median()) < 0.2


def test_lead_lag_only_considers_non_negative_lags() -> None:
    n = 1000
    idx = _index(n)
    ra, rb = _correlated(0.5, n, seed=53)
    panel = align_panel(
        {
            "EURUSD": _frame_from_returns(ra, idx, 1.1),
            "GBPUSD": _frame_from_returns(rb, idx, 1.3),
        }
    )
    ll = lead_lag(panel, target="EURUSD", candidate="GBPUSD", window=250, max_lag=3)
    assert list(ll.by_lag.columns) == ["lag_0", "lag_1", "lag_2", "lag_3"]
    assert ll.best_lag.dropna().min() >= 0


def test_lead_lag_rejects_unknown_instruments() -> None:
    n = 400
    idx = _index(n)
    ra, rb = _correlated(0.5, n, seed=54)
    panel = align_panel(
        {
            "EURUSD": _frame_from_returns(ra, idx, 1.1),
            "GBPUSD": _frame_from_returns(rb, idx, 1.3),
        }
    )
    with pytest.raises(CrossAssetError, match="not both in panel"):
        lead_lag(panel, target="EURUSD", candidate="USDJPY")


# =====================================================================
# The bundled state
# =====================================================================


def test_build_cross_asset_state_degrades_honestly() -> None:
    n = 1500
    idx = _index(n)
    rng = np.random.default_rng(61)
    frames = {
        "EURUSD": _frame_from_returns(rng.normal(0, 0.004, n), idx, 1.1),
        "GBPUSD": _frame_from_returns(rng.normal(0, 0.004, n), idx, 1.3),
    }
    state = build_cross_asset_state(frames, correlation_window=250, baseline_window=500)
    assert state.risk is None
    assert any("risk appetite unavailable" in note for note in state.notes)
    assert state.currency is not None
    assert "avg_abs_correlation" in state.stress_inputs.columns
    assert "correlation_shift_z" in state.stress_inputs.columns
    assert "risk_appetite" not in state.stress_inputs.columns
    summary = state.to_summary()
    assert summary["coverage"]["common_bars"] == n


def test_aligned_panel_log_returns_are_causal() -> None:
    n = 500
    idx = _index(n)
    ra, rb = _correlated(0.4, n, seed=71)
    panel: AlignedPanel = align_panel(
        {
            "EURUSD": _frame_from_returns(ra, idx, 1.1),
            "GBPUSD": _frame_from_returns(rb, idx, 1.3),
        }
    )
    rets = panel.log_returns
    assert rets.iloc[0].isna().all()
    # Corrupting the tail must not move any earlier return.
    corrupted = panel.prices.copy()
    corrupted.iloc[300:] *= 50.0
    corrupted_rets = np.log(corrupted).diff()
    pd.testing.assert_frame_equal(rets.iloc[:300], corrupted_rets.iloc[:300])
