"""Cross-asset market structure: correlation, currency strength, risk appetite, lead-lag.

Everything here is **windowed and causal**. A rolling correlation at bar ``t``
uses bars ``t-window+1 .. t`` and nothing else; a lead-lag measure lags the
*candidate leader*, so the correlation it reports is one a trader could have
computed at ``t``. There is no full-sample correlation matrix in this module,
because a full-sample matrix is exactly the statistic that makes a portfolio
look diversified in the years before the diversification broke.

Alignment is the other quiet source of dishonesty. Two instruments' H4 files do
not have identical timestamps — different histories, different holidays,
different missing bars. Forward-filling one onto the other's clock manufactures
observations and biases correlation towards zero (stale prices don't move
together). This module therefore **inner-joins on common timestamps only** and
returns a :class:`CoverageReport` saying how much of each series survived, so a
correlation computed on 30% overlap is visibly a correlation computed on 30%
overlap.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import AssetClass

CROSS_ASSET_VERSION = "1.0.0"


class CrossAssetError(ValueError):
    """The panel cannot honestly support the requested statistic."""


# =====================================================================
# Alignment and coverage
# =====================================================================


@dataclass(frozen=True, slots=True)
class InstrumentCoverage:
    instrument: str
    bars_supplied: int
    bars_used: int
    first_supplied: pd.Timestamp | None
    last_supplied: pd.Timestamp | None

    @property
    def coverage(self) -> float:
        """Fraction of this instrument's own bars that survived the inner join."""
        if self.bars_supplied == 0:
            return 0.0
        return self.bars_used / self.bars_supplied

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "bars_supplied": self.bars_supplied,
            "bars_used": self.bars_used,
            "coverage": round(self.coverage, 6),
            "first_supplied": str(self.first_supplied),
            "last_supplied": str(self.last_supplied),
        }


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """What the inner join actually kept. Read it before believing a correlation."""

    per_instrument: tuple[InstrumentCoverage, ...]
    common_bars: int
    first_common: pd.Timestamp | None
    last_common: pd.Timestamp | None

    @property
    def worst_coverage(self) -> float:
        return min((c.coverage for c in self.per_instrument), default=0.0)

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(c.instrument for c in self.per_instrument)

    def warnings(self, *, floor: float = 0.75) -> tuple[str, ...]:
        out = []
        for c in self.per_instrument:
            if c.coverage < floor:
                out.append(
                    f"{c.instrument}: only {c.coverage:.1%} of its bars are shared "
                    f"with the rest of the panel ({c.bars_used}/{c.bars_supplied})"
                )
        return tuple(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "common_bars": self.common_bars,
            "first_common": str(self.first_common),
            "last_common": str(self.last_common),
            "worst_coverage": round(self.worst_coverage, 6),
            "per_instrument": [c.to_dict() for c in self.per_instrument],
            "warnings": list(self.warnings()),
        }


@dataclass(frozen=True, slots=True)
class AlignedPanel:
    """Close prices for several instruments on one common, honest clock."""

    prices: pd.DataFrame
    coverage: CoverageReport

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(self.prices.columns)

    @property
    def log_returns(self) -> pd.DataFrame:
        return np.log(self.prices).diff()

    def __len__(self) -> int:
        return len(self.prices)


def align_panel(
    frames: Mapping[str, pd.DataFrame],
    *,
    column: str = "close",
    min_common_bars: int = 2,
) -> AlignedPanel:
    """Inner-join several bar frames onto their common timestamps.

    No reindexing, no forward fill, no interpolation. If two series barely
    overlap the panel will be short and the coverage report will say so — which
    is the correct outcome, not a bug to paper over.
    """
    if len(frames) < 2:
        raise CrossAssetError("a cross-asset panel needs at least two instruments")
    series: dict[str, pd.Series] = {}
    supplied: dict[str, pd.Series] = {}
    for name, frame in frames.items():
        if column not in frame.columns:
            raise CrossAssetError(f"{name}: no {column!r} column")
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
            raise CrossAssetError(f"{name}: needs a tz-aware DatetimeIndex")
        s = frame[column].astype(float)
        s = s[~s.index.duplicated(keep="first")].sort_index()
        supplied[name] = s
        series[name] = s

    common = None
    for s in series.values():
        common = s.index if common is None else common.intersection(s.index)
    assert common is not None
    common = common.sort_values()
    if len(common) < min_common_bars:
        raise CrossAssetError(
            f"only {len(common)} common timestamps across "
            f"{sorted(frames)}; refusing to compute cross-asset statistics on that"
        )
    prices = pd.DataFrame(
        {name: s.reindex(common) for name, s in series.items()}, index=common
    )
    prices.index.name = "timestamp"
    cov = CoverageReport(
        per_instrument=tuple(
            InstrumentCoverage(
                instrument=name,
                bars_supplied=len(s),
                bars_used=int(s.index.isin(common).sum()),
                first_supplied=s.index.min() if len(s) else None,
                last_supplied=s.index.max() if len(s) else None,
            )
            for name, s in supplied.items()
        ),
        common_bars=len(common),
        first_common=common.min(),
        last_common=common.max(),
    )
    return AlignedPanel(prices=prices, coverage=cov)


# =====================================================================
# Rolling correlation
# =====================================================================


@dataclass(frozen=True, slots=True)
class RollingCorrelation:
    """Trailing-window pairwise correlations of log returns.

    Stored long-ways (one column per unordered pair) because the full matrix at
    every bar is mostly redundant. :meth:`matrix_at` rebuilds a matrix when one
    is actually wanted.
    """

    pairs: pd.DataFrame
    instruments: tuple[str, ...]
    window: int
    coverage: CoverageReport

    @staticmethod
    def pair_key(a: str, b: str) -> str:
        x, y = sorted((a, b))
        return f"{x}|{y}"

    def pair(self, a: str, b: str) -> pd.Series:
        return self.pairs[self.pair_key(a, b)]

    def matrix_at(self, ts: pd.Timestamp) -> pd.DataFrame:
        """The correlation matrix as it stood at ``ts`` (exact index match)."""
        row = self.pairs.loc[ts]
        n = len(self.instruments)
        m = np.eye(n)
        for i, a in enumerate(self.instruments):
            for j in range(i + 1, n):
                b = self.instruments[j]
                v = float(row[self.pair_key(a, b)])
                m[i, j] = m[j, i] = v
        return pd.DataFrame(m, index=list(self.instruments), columns=list(self.instruments))

    @property
    def average(self) -> pd.Series:
        """Mean pairwise correlation per bar: the single best breadth measure."""
        return self.pairs.mean(axis=1)

    @property
    def average_abs(self) -> pd.Series:
        """Mean |correlation|: rises whether the panel couples long or short."""
        return self.pairs.abs().mean(axis=1)

    @property
    def dispersion(self) -> pd.Series:
        """Cross-sectional spread of pairwise correlations."""
        return self.pairs.std(axis=1, ddof=1)


def rolling_correlation(
    panel: AlignedPanel, *, window: int = 100, min_periods: int | None = None
) -> RollingCorrelation:
    """Pairwise trailing correlation of log returns over ``window`` bars."""
    if window < 5:
        raise CrossAssetError("correlation window below 5 bars is noise")
    rets = panel.log_returns
    mp = min_periods or window
    names = tuple(panel.instruments)
    cols: dict[str, pd.Series] = {}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            cols[RollingCorrelation.pair_key(a, b)] = (
                rets[a].rolling(window, min_periods=mp).corr(rets[b])
            )
    pairs = pd.DataFrame(cols, index=rets.index)
    return RollingCorrelation(
        pairs=pairs, instruments=names, window=window, coverage=panel.coverage
    )


def correlation_breakdown(
    correlation: RollingCorrelation, *, baseline_window: int = 500
) -> pd.DataFrame:
    """How far today's average correlation sits from its own trailing baseline.

    Published as a z-score against a trailing (not expanding, not full-sample)
    baseline, plus the raw shift. A large positive value means the panel has
    *converged* — everything moving together, which is what a risk event looks
    like from the inside, and what makes a "diversified" set of bots one bet.
    """
    avg = correlation.average_abs
    base = avg.rolling(baseline_window, min_periods=baseline_window).mean()
    sd = (
        avg.rolling(baseline_window, min_periods=baseline_window)
        .std(ddof=1)
        .replace(0.0, np.nan)
    )
    out = pd.DataFrame(
        {
            "avg_abs_correlation": avg,
            "baseline": base,
            "shift": avg - base,
            "z": (avg - base) / sd,
        },
        index=avg.index,
    )
    return out


# =====================================================================
# Currency strength
# =====================================================================


@dataclass(frozen=True, slots=True)
class CurrencyStrength:
    """Per-currency strength decomposed from FX pair returns."""

    strength: pd.DataFrame
    currencies: tuple[str, ...]
    window: int
    residual_rms: pd.Series
    coverage: CoverageReport

    def ranking(self, ts: pd.Timestamp) -> pd.Series:
        return self.strength.loc[ts].sort_values(ascending=False)

    def to_summary(self) -> dict[str, Any]:
        return {
            "currencies": list(self.currencies),
            "window": self.window,
            "bars": int(len(self.strength)),
            "median_residual_rms": float(self.residual_rms.median(skipna=True)),
        }


def currency_strength(
    panel: AlignedPanel, *, window: int = 20, min_pairs_per_currency: int = 1
) -> CurrencyStrength:
    """Decompose FX pair returns into per-currency strengths.

    Model: the log return of ``BASEQUOTE`` over the window is
    ``s_base - s_quote + noise``, where ``s_c`` is currency ``c``'s strength.
    That is an over-determined linear system once several pairs share
    currencies, so it is solved by least squares under the identifying
    constraint ``sum(s) = 0`` (strength is inherently relative — without the
    constraint the system is rank-deficient and any solution plus a constant is
    equally good).

    The window return at bar ``t`` is ``log(P_t) - log(P_{t-window})``, so the
    whole decomposition is causal. Non-FX instruments in the panel are ignored
    with a note; XAU/XAG are *not* treated as currencies, because "gold
    strength" and "dollar weakness" are the same observation and including both
    makes the system meaningless.
    """
    fx_pairs: list[tuple[str, str, str]] = []
    for symbol in panel.instruments:
        if not instrument_registry.exists(symbol):
            continue
        inst = instrument_registry.get(symbol)
        if inst.asset_class not in (AssetClass.FX_MAJOR, AssetClass.FX_CROSS):
            continue
        if not (
            instrument_registry.is_iso_currency(inst.base)
            and instrument_registry.is_iso_currency(inst.quote)
        ):
            continue
        fx_pairs.append((symbol, inst.base, inst.quote))
    if len(fx_pairs) < 2:
        raise CrossAssetError(
            "currency-strength decomposition needs at least two registered FX "
            f"pairs; got {[p[0] for p in fx_pairs]}"
        )
    currencies = sorted({c for _, b, q in fx_pairs for c in (b, q)})
    counts = {c: 0 for c in currencies}
    for _, b, q in fx_pairs:
        counts[b] += 1
        counts[q] += 1
    thin = [c for c, k in counts.items() if k < min_pairs_per_currency]
    if thin:
        raise CrossAssetError(
            f"currencies {thin} appear in fewer than {min_pairs_per_currency} pairs"
        )

    cidx = {c: i for i, c in enumerate(currencies)}
    n_pairs, n_ccy = len(fx_pairs), len(currencies)
    design = np.zeros((n_pairs + 1, n_ccy))
    for r, (_, b, q) in enumerate(fx_pairs):
        design[r, cidx[b]] = 1.0
        design[r, cidx[q]] = -1.0
    design[n_pairs, :] = 1.0  # sum(s) = 0 identifying constraint

    log_prices = np.log(panel.prices[[p[0] for p in fx_pairs]])
    window_ret = (log_prices - log_prices.shift(window)).to_numpy()  # (T, n_pairs)
    rhs = np.vstack([window_ret.T, np.zeros((1, window_ret.shape[0]))])  # (n_pairs+1, T)
    pinv = np.linalg.pinv(design)
    strength = (pinv @ np.nan_to_num(rhs, nan=0.0)).T  # (T, n_ccy)
    fitted = (design[:n_pairs] @ strength.T).T
    resid = window_ret - fitted
    invalid = np.isnan(window_ret).any(axis=1)
    rms = np.full(window_ret.shape[0], np.nan)
    if (~invalid).any():
        rms[~invalid] = np.sqrt(np.nanmean(resid[~invalid] ** 2, axis=1))
    strength[invalid] = np.nan
    frame = pd.DataFrame(strength, index=panel.prices.index, columns=currencies)
    return CurrencyStrength(
        strength=frame,
        currencies=tuple(currencies),
        window=window,
        residual_rms=pd.Series(rms, index=panel.prices.index),
        coverage=panel.coverage,
    )


# =====================================================================
# Risk on / risk off
# =====================================================================


@dataclass(frozen=True, slots=True)
class RiskProxyConfig:
    """Which instruments stand in for risk appetite, and how much they count.

    This is a *stated assumption*, not a discovered fact. AUD and NZD are the
    conventional pro-cyclical currencies; JPY, CHF and gold are the conventional
    havens. The mapping is configurable precisely so that nobody has to pretend
    it is universal — it broke down in 2022 when gold and the dollar rose
    together, and a proxy that cannot be argued with is a proxy that cannot be
    audited.
    """

    risk_on: Mapping[str, float] = field(
        default_factory=lambda: {"AUDUSD": 1.0, "NZDUSD": 1.0, "AUDJPY": 1.0}
    )
    risk_off: Mapping[str, float] = field(
        default_factory=lambda: {"USDJPY": -1.0, "USDCHF": 1.0, "XAUUSD": 1.0}
    )

    def to_dict(self) -> dict[str, Any]:
        return {"risk_on": dict(self.risk_on), "risk_off": dict(self.risk_off)}


@dataclass(frozen=True, slots=True)
class RiskAppetite:
    score: pd.Series
    risk_on_leg: pd.Series
    risk_off_leg: pd.Series
    used_risk_on: tuple[str, ...]
    used_risk_off: tuple[str, ...]
    window: int

    def to_summary(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "used_risk_on": list(self.used_risk_on),
            "used_risk_off": list(self.used_risk_off),
            "bars": int(len(self.score)),
        }


def risk_appetite(
    panel: AlignedPanel,
    *,
    window: int = 20,
    zscore_window: int = 250,
    config: RiskProxyConfig | None = None,
) -> RiskAppetite:
    """A risk-on / risk-off proxy from a basket differential.

    Each leg is the weighted mean of its members' ``window``-bar log returns,
    z-scored against a *trailing* ``zscore_window``. The score is
    ``risk_on_z - risk_off_z``: positive means the pro-cyclical basket is
    outperforming the haven basket.

    Weights may be negative to express an inverted quote (``USDJPY`` is weighted
    ``-1`` because a *falling* USDJPY is yen strength, i.e. risk-off).
    """
    cfg = config or RiskProxyConfig()
    available = set(panel.instruments)
    on = {k: v for k, v in cfg.risk_on.items() if k in available}
    off = {k: v for k, v in cfg.risk_off.items() if k in available}
    if not on or not off:
        raise CrossAssetError(
            "risk_appetite needs at least one instrument on each leg; panel has "
            f"{sorted(available)}, config wants {sorted(cfg.risk_on)} / "
            f"{sorted(cfg.risk_off)}"
        )
    logp = np.log(panel.prices)
    win_ret = logp - logp.shift(window)

    def _leg(weights: Mapping[str, float]) -> pd.Series:
        total = sum(abs(w) for w in weights.values())
        acc = sum(win_ret[k] * w for k, w in weights.items())
        return acc / total

    on_leg = _leg(on)
    off_leg = _leg(off)

    def _z(s: pd.Series) -> pd.Series:
        m = s.rolling(zscore_window, min_periods=zscore_window).mean()
        sd = (
            s.rolling(zscore_window, min_periods=zscore_window)
            .std(ddof=1)
            .replace(0.0, np.nan)
        )
        return (s - m) / sd

    score = _z(on_leg) - _z(off_leg)
    return RiskAppetite(
        score=score,
        risk_on_leg=on_leg,
        risk_off_leg=off_leg,
        used_risk_on=tuple(sorted(on)),
        used_risk_off=tuple(sorted(off)),
        window=window,
    )


# =====================================================================
# Lead-lag
# =====================================================================


@dataclass(frozen=True, slots=True)
class LeadLag:
    """Trailing cross-correlation of ``target`` against lagged ``candidate``."""

    target: str
    candidate: str
    by_lag: pd.DataFrame
    best_lag: pd.Series
    best_corr: pd.Series
    window: int
    max_lag: int

    def summary(self) -> dict[str, Any]:
        lags = self.best_lag.dropna()
        return {
            "target": self.target,
            "candidate": self.candidate,
            "window": self.window,
            "max_lag": self.max_lag,
            "bars": int(len(lags)),
            "modal_lag": (int(lags.mode().iloc[0]) if len(lags) else None),
            "median_best_corr": float(self.best_corr.median(skipna=True)),
        }


def lead_lag(
    panel: AlignedPanel,
    target: str,
    candidate: str,
    *,
    window: int = 250,
    max_lag: int = 5,
) -> LeadLag:
    """Does ``candidate`` lead ``target``, and by how many bars?

    Computes ``corr(r_target[t], r_candidate[t-l])`` over a trailing window for
    ``l = 0..max_lag``. Only non-negative lags are considered: a negative lag
    would ask whether the target's return predicts the candidate's *future*
    return, which is a fine question but cannot be answered at bar ``t`` without
    the future, so it is not answered here.

    ``best_lag`` is the lag with the largest absolute correlation. A persistent
    ``best_lag > 0`` is evidence of a genuine lead; a ``best_lag`` that jumps
    around every bar is evidence of noise, and the caller should say so.
    """
    if max_lag < 1:
        raise CrossAssetError("lead_lag needs max_lag >= 1")
    if target not in panel.instruments or candidate not in panel.instruments:
        raise CrossAssetError(f"{target}/{candidate} not both in panel")
    rets = panel.log_returns
    rt = rets[target]
    rc = rets[candidate]
    cols: dict[str, pd.Series] = {}
    for lag in range(max_lag + 1):
        cols[f"lag_{lag}"] = rt.rolling(window, min_periods=window).corr(rc.shift(lag))
    by_lag = pd.DataFrame(cols, index=rets.index)
    arr = by_lag.to_numpy()
    with np.errstate(invalid="ignore"):
        absarr = np.abs(arr)
    all_nan = np.isnan(absarr).all(axis=1)
    idx = np.full(len(arr), np.nan)
    corr = np.full(len(arr), np.nan)
    if (~all_nan).any():
        pick = np.nanargmax(np.where(np.isnan(absarr), -np.inf, absarr)[~all_nan], axis=1)
        idx[~all_nan] = pick
        corr[~all_nan] = arr[~all_nan][np.arange(pick.size), pick]
    return LeadLag(
        target=target,
        candidate=candidate,
        by_lag=by_lag,
        best_lag=pd.Series(idx, index=rets.index),
        best_corr=pd.Series(corr, index=rets.index),
        window=window,
        max_lag=max_lag,
    )


# =====================================================================
# Convenience bundle
# =====================================================================


@dataclass(frozen=True, slots=True)
class CrossAssetState:
    """Everything the regime engine needs from the rest of the market."""

    panel: AlignedPanel
    correlation: RollingCorrelation
    breakdown: pd.DataFrame
    risk: RiskAppetite | None
    currency: CurrencyStrength | None
    notes: tuple[str, ...] = ()

    @property
    def stress_inputs(self) -> pd.DataFrame:
        """Columns the regime classifier can consume for its stress axis."""
        out = pd.DataFrame(index=self.panel.prices.index)
        out["avg_abs_correlation"] = self.correlation.average_abs
        out["correlation_shift_z"] = self.breakdown["z"]
        if self.risk is not None:
            out["risk_appetite"] = self.risk.score
        return out

    def to_summary(self) -> dict[str, Any]:
        return {
            "version": CROSS_ASSET_VERSION,
            "coverage": self.panel.coverage.to_dict(),
            "correlation_window": self.correlation.window,
            "risk": self.risk.to_summary() if self.risk else None,
            "currency": self.currency.to_summary() if self.currency else None,
            "notes": list(self.notes),
        }


def build_cross_asset_state(
    frames: Mapping[str, pd.DataFrame],
    *,
    correlation_window: int = 100,
    baseline_window: int = 500,
    risk_config: RiskProxyConfig | None = None,
    currency_window: int = 20,
) -> CrossAssetState:
    """Assemble the cross-asset view, degrading honestly when inputs are thin.

    Risk appetite and currency strength need particular instruments to be
    present. When they are not, the corresponding field is ``None`` and the
    reason is recorded in ``notes`` — never substituted with a neutral constant.
    """
    panel = align_panel(frames)
    corr = rolling_correlation(panel, window=correlation_window)
    breakdown = correlation_breakdown(corr, baseline_window=baseline_window)
    notes: list[str] = list(panel.coverage.warnings())
    try:
        risk = risk_appetite(panel, config=risk_config)
    except CrossAssetError as exc:
        risk = None
        notes.append(f"risk appetite unavailable: {exc}")
    try:
        ccy = currency_strength(panel, window=currency_window)
    except CrossAssetError as exc:
        ccy = None
        notes.append(f"currency strength unavailable: {exc}")
    return CrossAssetState(
        panel=panel,
        correlation=corr,
        breakdown=breakdown,
        risk=risk,
        currency=ccy,
        notes=tuple(notes),
    )
