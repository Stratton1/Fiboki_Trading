"""Forward validation: expectation against observation, dimension by dimension.

A backtest is a prediction. Once a strategy is running, that prediction is
testable, and the only question that matters is whether reality is still inside
it. This module makes that comparison mechanical across eleven dimensions and
produces a :class:`DivergenceReport` naming which have diverged, by how much, and
with what confidence.

Why the block bootstrap and not a normal approximation
------------------------------------------------------
Trading returns are serially dependent: a trend day is not twenty-four
independent hourly draws. A normal interval -- or an iid bootstrap -- understates
every dispersion it touches, which means it declares divergence too readily on
the dimensions where you want sensitivity and, worse, produces confidently narrow
intervals on the dimensions where you want caution. Every interval here comes
from :mod:`fiboki.stats.bootstrap`, resampling BLOCKS with the block length
estimated from the data by Politis-White rather than guessed.

What the confidence number means, stated precisely
--------------------------------------------------
For the return, Sharpe, win-rate and drawdown dimensions the reference
distribution is built from the **backtest**, not from the forward record:
block-resample the backtest into windows of the forward record's length,
recompute the statistic on each, and ask what fraction of those windows are at
least as bad as what was actually observed. ``confidence = 1 - p``.

The choice of reference is the whole argument, and the obvious alternative is
wrong. Bootstrapping the OBSERVED series asks how variable the statistic is
*within* the forward record -- so a forward record containing a disaster
reproduces the disaster in most of its own resamples and concludes the disaster
was unremarkable. Measured on a planted 64% drawdown against a 4.9% expectation,
the observed-series null returns p = 0.016 and the backtest-window null returns
p = 0.001.

Resampling the backtest also propagates the backtest's own sampling error into
the comparison, and it gets short-window tolerance for free: a 40-trade forward
record is judged against the spread of 40-trade backtest windows, which is wide,
exactly as it should be.

Three dimensions cannot work this way, because the expectation for them is a
single modelled number rather than a series -- spread, slippage and latency have
a cost model, and the per-regime comparison has a mean per regime. Those resample
the observed series and ask whether the modelled number is plausible given what
was measured. That is a weaker question and it is marked as such on the result.

None of these is a formal test that a true parameter equals a backtested value.
They are surprise measures with an honest null, which is what a monitor needs and
is the difference between this and the V1 dashboard widget that compared two
point estimates and called the difference a result.

Eleven dimensions, one family
-----------------------------
Eleven dimensions each flagged at 0.05 would flag a perfectly healthy strategy
about half the time, and a monitor that cries wolf every other evaluation is one
nobody reads by the third month. The p-values are therefore Holm-adjusted across
the dimensions evaluated in one report, and the status is decided on the adjusted
value. The dimensions are strongly dependent -- return, Sharpe and win rate are
three views of one series -- so Holm is conservative here, in the direction of
demoting less readily. That is the direction that needs an argument, and the
argument is that this score is a screen: the halts are the pre-registered rules
in :mod:`fiboki.lifecycle.stopping_rules`, which are not adjusted and not
negotiable.

Dimensions that cannot be computed
----------------------------------
A dimension with too few observations is ``NOT_EVALUATED``, never ``AGREES``. A
monitor that reports agreement because it had no data is worse than no monitor,
because it manufactures confidence. :attr:`DivergenceReport.coverage` states what
fraction of dimensions were actually evaluated, and the promotion criteria in
:mod:`fiboki.lifecycle.promotion` refuse a promotion whose divergence report has
flagged dimensions.

Approximations, named rather than hidden
----------------------------------------
* **Trade frequency uses a Poisson tail.** Trade arrivals are not Poisson -- they
  cluster around regime changes and sessions -- so the interval is narrower than
  reality and this dimension is the most likely to produce a spurious flag. It is
  reported with that caveat attached to the result.
* **Rejected orders use a binomial tail.** Rejections cluster too (a venue having
  a bad five minutes rejects several), for the same reason and with the same
  consequence.
* **The distribution test is a block-permutation KS.** The KS statistic's usual
  null distribution assumes iid observations; here the null distribution is
  simulated by block-resampling the POOLED sample, which respects dependence at
  the cost of being an approximation of a different kind -- it assumes the two
  samples share a block structure.
* **Per-regime comparison needs the regime labels supplied.** This module parses
  and validates regime keys through
  :class:`fiboki.marketstate.regime.RegimeVector`, but it does not classify bars;
  the caller runs the classifier and hands over the grouping.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import numpy as np
from scipy import stats as _sps

from fiboki.marketstate.regime import RegimeError, RegimeVector
from fiboki.stats.bootstrap import (
    optimal_block_length,
    stationary_bootstrap_indices,
)
from fiboki.stats.multiple_testing import holm

__all__ = [
    "Divergence",
    "DivergenceDimension",
    "DivergenceReport",
    "DivergenceStatus",
    "Expectation",
    "ForwardMonitor",
    "MonitorConfig",
    "Observation",
    "max_drawdown",
    "two_sample_block_ks",
]


# ==========================================================================
# Small statistics, defined once so every dimension uses the same one
# ==========================================================================


def _clean(series: Sequence[float] | np.ndarray | None) -> np.ndarray:
    if series is None:
        return np.empty(0, dtype=float)
    arr = np.asarray(series, dtype=float).ravel()
    return arr[np.isfinite(arr)]


def _mean(arr: np.ndarray) -> float:
    return float(arr.mean())


def _sharpe(arr: np.ndarray) -> float:
    """Per-observation Sharpe. NOT annualised: the expectation must match."""
    sd = float(arr.std(ddof=1)) if arr.size > 1 else 0.0
    if sd <= 0.0:
        return 0.0
    return float(arr.mean() / sd)


def _win_rate(arr: np.ndarray) -> float:
    return float((arr > 0.0).mean())


def max_drawdown(returns: Sequence[float] | np.ndarray) -> float:
    """Fractional peak-to-trough drawdown of the COMPOUNDED equity curve, in [0, 1].

    Compounded, not summed: a strategy that risks a fixed fraction of running
    equity does not experience its drawdowns additively, and the additive version
    understates them exactly where it matters. Same reasoning as
    ``bootstrap.resample_with_compounding``.
    """
    arr = _clean(returns)
    if arr.size == 0:
        return 0.0
    equity = np.cumprod(1.0 + arr)
    peak = np.maximum.accumulate(equity)
    return float(np.max(1.0 - equity / peak))


def two_sample_block_ks(
    expected: Sequence[float] | np.ndarray,
    observed: Sequence[float] | np.ndarray,
    *,
    n_boot: int = 500,
    block_length: float | None = None,
    rng: np.random.Generator | int | None = None,
) -> tuple[float, float]:
    """Two-sample KS statistic with a BLOCK-resampled null distribution.

    The classical two-sample KS p-value assumes both samples are iid. Trading
    returns are not, and under dependence the classical p-value is far too small
    -- it would flag almost every live strategy as distributionally different
    from its backtest within weeks.

    The null here is simulated instead: pool the two samples, draw a stationary
    block bootstrap of the pooled series, split it at the original sizes, and
    compute the KS statistic. Repeating that gives the distribution of the
    statistic when the two samples DO share a distribution and a block structure.

    Returns ``(statistic, p_value)``. ``p_value`` is the fraction of simulated
    null statistics at least as large as the observed one, with the usual
    ``(count + 1) / (n_boot + 1)`` correction so it is never exactly zero.
    """
    a = _clean(expected)
    b = _clean(observed)
    if a.size < 2 or b.size < 2:
        raise ValueError("both samples need at least 2 finite observations")
    statistic = float(_sps.ks_2samp(a, b).statistic)

    pooled = np.concatenate([a, b])
    b_len = optimal_block_length(pooled).stationary if block_length is None else block_length
    idx = stationary_bootstrap_indices(pooled.size, float(b_len), n_boot, rng)
    resampled = pooled[idx]
    null = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        row = resampled[i]
        null[i] = _sps.ks_2samp(row[: a.size], row[a.size :]).statistic
    p_value = float((np.count_nonzero(null >= statistic) + 1) / (n_boot + 1))
    return statistic, p_value


# ==========================================================================
# Report vocabulary
# ==========================================================================


class DivergenceDimension(str, Enum):
    """The eleven things compared. Order is display order, cheapest first."""

    RETURN = "return"
    SHARPE = "sharpe"
    WIN_RATE = "win_rate"
    TRADE_FREQUENCY = "trade_frequency"
    RETURN_DISTRIBUTION = "return_distribution"
    SPREAD = "spread"
    SLIPPAGE = "slippage"
    LATENCY = "latency"
    DRAWDOWN = "drawdown"
    REJECTED_ORDERS = "rejected_orders"
    REGIME = "regime"

    @property
    def larger_is_better(self) -> bool:
        return self in (
            DivergenceDimension.RETURN,
            DivergenceDimension.SHARPE,
            DivergenceDimension.WIN_RATE,
            DivergenceDimension.REGIME,
        )

    @property
    def is_cost(self) -> bool:
        """Cost and execution-quality dimensions: a divergence here invalidates
        stored expectancies rather than merely disappointing."""
        return self in (
            DivergenceDimension.SPREAD,
            DivergenceDimension.SLIPPAGE,
            DivergenceDimension.LATENCY,
            DivergenceDimension.REJECTED_ORDERS,
        )


class DivergenceStatus(str, Enum):
    AGREES = "agrees"
    DIVERGED = "diverged"
    NOT_EVALUATED = "not_evaluated"
    """No expectation, or not enough observations. NEVER treated as agreement."""

    @property
    def is_known_good(self) -> bool:
        return self is DivergenceStatus.AGREES


@dataclass(frozen=True, slots=True)
class Divergence:
    """One dimension's verdict, with the arithmetic a reader needs to check it."""

    dimension: DivergenceDimension
    status: DivergenceStatus
    expected: float | None = None
    observed: float | None = None
    n_observations: int = 0
    ci_lower: float | None = None
    ci_upper: float | None = None
    p_value: float | None = None
    adjusted_p_value: float | None = None
    """Holm-adjusted across the dimensions evaluated in the SAME report. The
    status is decided on this, never on the raw value."""
    method: str = ""
    reason: str = ""
    caveat: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def diverged(self) -> bool:
        return self.status is DivergenceStatus.DIVERGED

    @property
    def confidence(self) -> float | None:
        """Confidence that the observed data are worse than expectation.

        Taken from the ADJUSTED p-value where there is one: the confidence an
        operator should have is the one that accounts for the other ten
        dimensions that were examined at the same time.
        """
        p = self.adjusted_p_value if self.adjusted_p_value is not None else self.p_value
        return None if p is None else 1.0 - float(p)

    @property
    def absolute_gap(self) -> float | None:
        if self.expected is None or self.observed is None:
            return None
        return float(self.observed - self.expected)

    @property
    def relative_gap(self) -> float | None:
        """Adverse gap as a fraction of expectation. Positive means worse."""
        if self.expected is None or self.observed is None:
            return None
        scale = abs(float(self.expected))
        if scale <= 0.0:
            return None
        raw = (float(self.expected) - float(self.observed)) / scale
        return float(raw if self.dimension.larger_is_better else -raw)

    @property
    def severity(self) -> float:
        """0.0 (no concern) .. 1.0 (fully adverse). Feeds the degradation score.

        A dimension that could not be evaluated scores ``0.0`` here and is
        counted separately through :attr:`DivergenceReport.coverage`: it would be
        dishonest to score it as bad, and dishonest to let it dilute the mean.
        """
        if self.status is not DivergenceStatus.DIVERGED:
            return 0.0
        gap = self.relative_gap
        magnitude = 1.0 if gap is None else min(1.0, max(0.0, gap))
        conf = self.confidence if self.confidence is not None else 1.0
        return float(min(1.0, 0.5 * magnitude + 0.5 * max(0.0, conf)))

    def describe(self) -> str:
        if self.status is DivergenceStatus.NOT_EVALUATED:
            return f"{self.dimension.value}: NOT EVALUATED -- {self.reason}"
        head = f"{self.dimension.value}: expected {self.expected:.6g}, observed {self.observed:.6g}"
        tail = "" if self.p_value is None else f", p={self.p_value:.3f}"
        if self.adjusted_p_value is not None:
            tail += f" (Holm {self.adjusted_p_value:.3f})"
        verdict = "DIVERGED" if self.diverged else "agrees"
        return f"{head}{tail} -> {verdict} (n={self.n_observations}, {self.method})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "status": self.status.value,
            "expected": self.expected,
            "observed": self.observed,
            "n_observations": int(self.n_observations),
            "ci_lower": self.ci_lower,
            "ci_upper": self.ci_upper,
            "p_value": self.p_value,
            "adjusted_p_value": self.adjusted_p_value,
            "confidence": self.confidence,
            "relative_gap": self.relative_gap,
            "severity": self.severity,
            "method": self.method,
            "reason": self.reason,
            "caveat": self.caveat,
            "detail": dict(self.detail),
            "description": self.describe(),
        }


@dataclass(frozen=True, slots=True)
class DivergenceReport:
    """Every dimension's verdict for one strategy at one moment."""

    strategy_content_hash: str
    at: datetime
    divergences: tuple[Divergence, ...]
    config_version: str = ""
    expectation_source: str = ""
    """Hash or id of the ValidationReport the expectation was taken from."""
    n_observations: int = 0

    def dimension(self, dim: DivergenceDimension) -> Divergence | None:
        for d in self.divergences:
            if d.dimension is dim:
                return d
        return None

    @property
    def diverged(self) -> tuple[Divergence, ...]:
        return tuple(d for d in self.divergences if d.diverged)

    @property
    def n_flagged(self) -> int:
        return len(self.diverged)

    @property
    def not_evaluated(self) -> tuple[Divergence, ...]:
        return tuple(
            d for d in self.divergences if d.status is DivergenceStatus.NOT_EVALUATED
        )

    @property
    def coverage(self) -> float:
        """Fraction of dimensions that were actually computed."""
        if not self.divergences:
            return 0.0
        return 1.0 - len(self.not_evaluated) / len(self.divergences)

    @property
    def worst(self) -> Divergence | None:
        flagged = self.diverged
        return max(flagged, key=lambda d: d.severity) if flagged else None

    @property
    def cost_divergence(self) -> bool:
        """True when a cost or execution-quality dimension diverged.

        Separated because it means something different: a return shortfall may
        be luck, but a spread or slippage shortfall means every stored expectancy
        for this instrument is overstated by a measurable amount and must be
        re-run rather than accepted.
        """
        return any(d.dimension.is_cost for d in self.diverged)

    def summary(self) -> str:
        if not self.divergences:
            return f"{self.strategy_content_hash[:12]}: nothing to compare"
        if not self.diverged:
            return (
                f"{self.strategy_content_hash[:12]}: no divergence across "
                f"{len(self.divergences)} dimensions "
                f"(coverage {self.coverage:.0%}, n={self.n_observations})"
            )
        names = ", ".join(d.dimension.value for d in self.diverged)
        worst = self.worst
        return (
            f"{self.strategy_content_hash[:12]}: DIVERGED on {names} "
            f"(worst: {worst.describe() if worst else '-'}; "
            f"coverage {self.coverage:.0%})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_content_hash": self.strategy_content_hash,
            "at": self.at.astimezone(UTC).isoformat(),
            "config_version": self.config_version,
            "expectation_source": self.expectation_source,
            "n_observations": int(self.n_observations),
            "n_flagged": self.n_flagged,
            "coverage": self.coverage,
            "cost_divergence": self.cost_divergence,
            "summary": self.summary(),
            "divergences": [d.to_dict() for d in self.divergences],
        }


# ==========================================================================
# Inputs
# ==========================================================================


@dataclass(frozen=True, slots=True)
class Expectation:
    """What the research said would happen. Derived once, then frozen.

    Every statistic is at the OBSERVATION frequency of ``returns`` -- per trade
    if the returns are per trade, per bar if they are per bar. Mixing an
    annualised expectation with per-trade observations is the single easiest way
    to manufacture a divergence, so :meth:`from_backtest` derives the whole set
    from one series rather than accepting them piecemeal.
    """

    strategy_content_hash: str
    returns: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)
    mean_return: float | None = None
    sharpe: float | None = None
    win_rate: float | None = None
    max_drawdown: float | None = None
    trades_per_day: float | None = None
    spread_pips: float | None = None
    slippage_pips: float | None = None
    latency_ms: float | None = None
    reject_rate: float | None = None
    regime_mean_return: Mapping[str, float] = field(default_factory=dict)
    source: str = ""
    """Content hash of the ValidationReport this came from, when there is one."""

    def __post_init__(self) -> None:
        for key in self.regime_mean_return:
            try:
                RegimeVector.from_key(str(key))
            except RegimeError as exc:
                raise ValueError(
                    f"regime expectation key {key!r} is not a RegimeVector key: {exc}"
                ) from exc

    @classmethod
    def from_backtest(
        cls,
        strategy_content_hash: str,
        returns: Sequence[float] | np.ndarray,
        *,
        elapsed_days: float | None = None,
        spread_pips: float | None = None,
        slippage_pips: float | None = None,
        latency_ms: float | None = None,
        reject_rate: float | None = None,
        regime_mean_return: Mapping[str, float] | None = None,
        source: str = "",
    ) -> Expectation:
        """Derive a self-consistent expectation from ONE backtest return series."""
        arr = _clean(returns)
        if arr.size < 2:
            raise ValueError("an expectation needs at least 2 finite backtest returns")
        return cls(
            strategy_content_hash=strategy_content_hash,
            returns=arr,
            mean_return=_mean(arr),
            sharpe=_sharpe(arr),
            win_rate=_win_rate(arr),
            max_drawdown=max_drawdown(arr),
            trades_per_day=(
                None if not elapsed_days or elapsed_days <= 0 else arr.size / elapsed_days
            ),
            spread_pips=spread_pips,
            slippage_pips=slippage_pips,
            latency_ms=latency_ms,
            reject_rate=reject_rate,
            regime_mean_return=dict(regime_mean_return or {}),
            source=source,
        )


@dataclass(frozen=True, slots=True)
class Observation:
    """What actually happened, forward. Assembled by the caller from telemetry.

    ``returns`` are per-trade fractional returns on the account, matching the
    frequency of the expectation's returns. ``spread_pips``, ``slippage_pips``
    and ``latency_ms`` come from :mod:`fiboki.data.telemetry`.
    """

    strategy_content_hash: str
    returns: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)
    observed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    elapsed_days: float = 0.0
    spread_pips: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)
    slippage_pips: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)
    latency_ms: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)
    orders_submitted: int = 0
    orders_rejected: int = 0
    regime_returns: Mapping[str, Sequence[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "returns", _clean(self.returns))
        object.__setattr__(self, "spread_pips", _clean(self.spread_pips))
        object.__setattr__(self, "slippage_pips", _clean(self.slippage_pips))
        object.__setattr__(self, "latency_ms", _clean(self.latency_ms))
        for key in self.regime_returns:
            try:
                RegimeVector.from_key(str(key))
            except RegimeError as exc:
                raise ValueError(
                    f"observed regime key {key!r} is not a RegimeVector key: {exc}"
                ) from exc

    @property
    def n_trades(self) -> int:
        return int(self.returns.size)


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    """Versioned monitor policy. Stamped onto every report."""

    version: str = "lifecycle_monitor_v1"
    alpha: float = 0.05
    """A dimension diverges when its bootstrap tail probability is at or below
    this. 0.05 across eleven dimensions means roughly one spurious flag in two
    reports if the dimensions were independent -- they are not, and the
    degradation score's hysteresis is what absorbs the rest."""
    n_boot: int = 1000
    ks_n_boot: int = 300
    min_observations: int = 20
    """Below this, the return dimensions are NOT_EVALUATED rather than guessed."""
    min_cost_observations: int = 10
    min_regime_observations: int = 15
    cost_tolerance: float = 1.25
    """A cost dimension diverges only past this multiple of expectation. Spreads
    vary; 25% is the band inside which the static-spread approximation was always
    understood to be wrong."""
    seed: int = 20240919
    """Fixed so two runs over the same data produce the same report. A monitor
    whose verdict depends on an unseeded RNG cannot be argued with."""

    def __post_init__(self) -> None:
        if not 0.0 < self.alpha < 0.5:
            raise ValueError("alpha must be in (0, 0.5)")
        if self.cost_tolerance < 1.0:
            raise ValueError("cost_tolerance below 1.0 would flag costs for being as modelled")


# ==========================================================================
# The monitor
# ==========================================================================


class ForwardMonitor:
    """Compares one :class:`Expectation` against one :class:`Observation`."""

    def __init__(self, config: MonitorConfig | None = None) -> None:
        self.config = config or MonitorConfig()

    # ---------------------------------------------------------------- api

    def compare(
        self,
        expectation: Expectation,
        observation: Observation,
        *,
        at: datetime | None = None,
    ) -> DivergenceReport:
        if expectation.strategy_content_hash != observation.strategy_content_hash:
            raise ValueError(
                "expectation and observation describe different strategies: "
                f"{expectation.strategy_content_hash[:12]} vs "
                f"{observation.strategy_content_hash[:12]}"
            )
        rng = np.random.default_rng(self.config.seed)
        divergences: tuple[Divergence, ...] = (
            self._series_dimension(
                DivergenceDimension.RETURN,
                expectation.mean_return,
                expectation.returns,
                observation.returns,
                _mean,
                rng,
                "mean return against block-resampled backtest windows",
            ),
            self._series_dimension(
                DivergenceDimension.SHARPE,
                expectation.sharpe,
                expectation.returns,
                observation.returns,
                _sharpe,
                rng,
                "per-observation Sharpe against block-resampled backtest windows",
            ),
            self._series_dimension(
                DivergenceDimension.WIN_RATE,
                expectation.win_rate,
                expectation.returns,
                observation.returns,
                _win_rate,
                rng,
                "win rate against block-resampled backtest windows",
            ),
            self._trade_frequency(expectation, observation),
            self._distribution(expectation, observation, rng),
            self._cost_dimension(
                DivergenceDimension.SPREAD, expectation.spread_pips,
                observation.spread_pips, rng,
            ),
            self._cost_dimension(
                DivergenceDimension.SLIPPAGE, expectation.slippage_pips,
                observation.slippage_pips, rng,
            ),
            self._cost_dimension(
                DivergenceDimension.LATENCY, expectation.latency_ms,
                observation.latency_ms, rng,
            ),
            self._drawdown(expectation, observation, rng),
            self._rejected_orders(expectation, observation),
            self._regime(expectation, observation, rng),
        )
        divergences = self._family_wise(divergences)
        return DivergenceReport(
            strategy_content_hash=expectation.strategy_content_hash,
            at=at or observation.observed_at,
            divergences=divergences,
            config_version=self.config.version,
            expectation_source=expectation.source,
            n_observations=observation.n_trades,
        )

    # ----------------------------------------------------------- machinery

    def _family_wise(
        self, divergences: tuple[Divergence, ...]
    ) -> tuple[Divergence, ...]:
        """Holm-adjust across the dimensions this report actually evaluated.

        Eleven dimensions each tested at 0.05 would flag a perfectly healthy
        strategy roughly half the time, and a monitor that cries wolf every other
        evaluation is one nobody reads by the third month. Holm-Bonferroni
        controls the family-wise rate across the dimensions in ONE report, which
        is the family an operator actually looks at.

        Holm rather than Bonferroni because it is uniformly more powerful under
        the same assumptions, and Holm rather than Benjamini-Hochberg because a
        false demotion costs a real strategy real allocation -- this is the place
        for family-wise control, not false-discovery control.

        Stated honestly: the eleven dimensions are NOT independent (return,
        Sharpe and win rate are three views of one series), so Holm is
        conservative here. Conservative in the direction of demoting less
        readily, which is the direction that needs an argument, and that argument
        is that the pre-registered stopping rules -- not this score -- are what
        halt a strategy.
        """
        indexed = [
            (i, d)
            for i, d in enumerate(divergences)
            if d.p_value is not None and d.status is not DivergenceStatus.NOT_EVALUATED
        ]
        if not indexed:
            return divergences
        result = holm([d.p_value for _, d in indexed], alpha=self.config.alpha)
        out = list(divergences)
        for position, (index, d) in enumerate(indexed):
            adjusted = float(result.adjusted_pvalues[position])
            out[index] = replace(
                d,
                adjusted_p_value=adjusted,
                status=(
                    DivergenceStatus.DIVERGED
                    if bool(result.rejected[position])
                    else DivergenceStatus.AGREES
                ),
            )
        return tuple(out)

    def _bootstrap_distribution(
        self,
        series: np.ndarray,
        statistic,
        rng: np.random.Generator,
    ) -> tuple[np.ndarray, float]:
        block = optimal_block_length(series).stationary
        idx = stationary_bootstrap_indices(series.size, block, self.config.n_boot, rng)
        samples = series[idx]
        dist = np.array([float(statistic(row)) for row in samples], dtype=float)
        return dist, float(block)

    def _tail(
        self, dist: np.ndarray, threshold: float, *, larger_is_better: bool
    ) -> float:
        """Fraction of the reference distribution at least as BAD as ``threshold``.

        A small value means the observation sits far out in the adverse tail of
        what the reference would produce. ``(count + 1) / (n + 1)`` so it is never
        exactly zero: a bootstrap cannot establish a probability smaller than its
        own resolution and should not print one.
        """
        if larger_is_better:
            count = int(np.count_nonzero(dist <= threshold))
        else:
            count = int(np.count_nonzero(dist >= threshold))
        return float((count + 1) / (dist.size + 1))

    def _null_windows(
        self,
        backtest: np.ndarray,
        n_live: int,
        statistic,
        rng: np.random.Generator,
    ) -> tuple[np.ndarray, float]:
        """The statistic over block-resampled BACKTEST windows of the live length.

        This is the null the forward dimensions are judged against, and the choice
        of null is the whole argument. Bootstrapping the OBSERVED series answers
        "how variable is this statistic within the forward record", which is not
        the question -- a forward record containing a disaster reproduces the
        disaster in most of its own resamples and so finds the disaster
        unsurprising.

        The question a forward monitor must ask is: *given a strategy that
        behaves as the backtest did, how often would a window of this length look
        at least this bad?* That is answered by resampling the BACKTEST, in
        blocks, into windows of the live length. It propagates the backtest's own
        sampling variation into the comparison, and it gets shorter-window
        tolerance for free: a 40-trade forward record is judged against the spread
        of 40-trade backtest windows, which is wide, exactly as it should be.
        """
        block = optimal_block_length(backtest).stationary
        idx = stationary_bootstrap_indices(
            backtest.size, block, self.config.n_boot, rng
        )
        windows = backtest[idx[:, :n_live]]
        dist = np.array([float(statistic(row)) for row in windows], dtype=float)
        return dist, float(block)

    def _series_dimension(
        self,
        dimension: DivergenceDimension,
        expected: float | None,
        backtest: np.ndarray,
        series: np.ndarray,
        statistic,
        rng: np.random.Generator,
        method: str,
    ) -> Divergence:
        if expected is None:
            return _unevaluated(dimension, "no expectation supplied for this dimension")
        if series.size < self.config.min_observations:
            return _unevaluated(
                dimension,
                f"{series.size} observations, {self.config.min_observations} required",
                n=int(series.size),
            )
        if backtest.size < series.size:
            return _unevaluated(
                dimension,
                f"the forward record ({series.size}) is longer than the backtest "
                f"({backtest.size}); the backtest cannot supply a comparison "
                "window of this length",
                n=int(series.size),
            )
        observed = float(statistic(series))
        null, block = self._null_windows(backtest, int(series.size), statistic, rng)
        p = self._tail(null, observed, larger_is_better=dimension.larger_is_better)
        lo, hi = np.quantile(null, [self.config.alpha / 2.0, 1.0 - self.config.alpha / 2.0])
        return Divergence(
            dimension=dimension,
            status=(
                DivergenceStatus.DIVERGED if p <= self.config.alpha else DivergenceStatus.AGREES
            ),
            expected=float(expected),
            observed=observed,
            n_observations=int(series.size),
            ci_lower=float(lo),
            ci_upper=float(hi),
            p_value=p,
            method=method,
            detail={
                "block_length": block,
                "n_boot": self.config.n_boot,
                "null": "block-resampled backtest windows of the forward length",
                "expected_range": [float(lo), float(hi)],
            },
        )

    def _cost_dimension(
        self,
        dimension: DivergenceDimension,
        expected: float | None,
        series: np.ndarray,
        rng: np.random.Generator,
    ) -> Divergence:
        """Median cost against a tolerated multiple of the modelled cost.

        The median rather than the mean: one requote at a news print should not
        move a monitor that is trying to detect a persistent cost shift.
        """
        if expected is None:
            return _unevaluated(dimension, "no modelled value supplied for this dimension")
        if series.size < self.config.min_cost_observations:
            return _unevaluated(
                dimension,
                f"{series.size} observations, {self.config.min_cost_observations} required",
                n=series.size,
            )
        tolerated = float(expected) * self.config.cost_tolerance
        observed = float(np.median(series))
        dist, block = self._bootstrap_distribution(series, np.median, rng)
        # No backtest series exists for a cost: the model supplies one number. So
        # the reference here IS the observed bootstrap, and the question is
        # whether the tolerated cost is plausible given what was measured.
        p = self._tail(dist, tolerated, larger_is_better=True)
        lo, hi = np.quantile(dist, [self.config.alpha / 2.0, 1.0 - self.config.alpha / 2.0])
        return Divergence(
            dimension=dimension,
            status=(
                DivergenceStatus.DIVERGED if p <= self.config.alpha else DivergenceStatus.AGREES
            ),
            expected=float(expected),
            observed=observed,
            n_observations=int(series.size),
            ci_lower=float(lo),
            ci_upper=float(hi),
            p_value=p,
            method=(
                f"block bootstrap of the median against "
                f"{self.config.cost_tolerance:g}x modelled"
            ),
            caveat=(
                "a cost divergence means every stored expectancy for this "
                "instrument is overstated by a measurable amount; re-run rather "
                "than accept"
            ),
            detail={
                "tolerated": tolerated,
                "block_length": block,
                "tolerance": self.config.cost_tolerance,
            },
        )

    def _trade_frequency(
        self, expectation: Expectation, observation: Observation
    ) -> Divergence:
        expected_rate = expectation.trades_per_day
        if expected_rate is None or observation.elapsed_days <= 0:
            return _unevaluated(
                DivergenceDimension.TRADE_FREQUENCY,
                "needs both an expected rate and a positive elapsed period",
                n=observation.n_trades,
            )
        expected_count = float(expected_rate) * float(observation.elapsed_days)
        observed_count = float(observation.n_trades)
        if expected_count <= 0:
            return _unevaluated(
                DivergenceDimension.TRADE_FREQUENCY, "expected trade count is zero"
            )
        # Two-sided Poisson tail. A strategy trading far MORE than expected is as
        # much a divergence as one trading less: it usually means an entry
        # condition is firing on bars the backtest excluded.
        lower = float(_sps.poisson.cdf(observed_count, expected_count))
        upper = float(_sps.poisson.sf(observed_count - 1, expected_count))
        p = float(min(1.0, 2.0 * min(lower, upper)))
        return Divergence(
            dimension=DivergenceDimension.TRADE_FREQUENCY,
            status=(
                DivergenceStatus.DIVERGED if p <= self.config.alpha else DivergenceStatus.AGREES
            ),
            expected=expected_count,
            observed=observed_count,
            n_observations=observation.n_trades,
            p_value=p,
            method="two-sided Poisson tail on the trade count",
            caveat=(
                "APPROXIMATION: trade arrivals are not Poisson -- they cluster "
                "around regime changes and session opens -- so this interval is "
                "narrower than reality and this is the dimension most likely to "
                "flag spuriously"
            ),
            detail={"elapsed_days": observation.elapsed_days, "rate": expected_rate},
        )

    def _distribution(
        self, expectation: Expectation, observation: Observation, rng: np.random.Generator
    ) -> Divergence:
        a, b = expectation.returns, observation.returns
        if a.size < self.config.min_observations or b.size < self.config.min_observations:
            return _unevaluated(
                DivergenceDimension.RETURN_DISTRIBUTION,
                f"needs {self.config.min_observations} observations on both sides "
                f"(backtest {a.size}, forward {b.size})",
                n=int(b.size),
            )
        statistic, p = two_sample_block_ks(
            a, b, n_boot=self.config.ks_n_boot, rng=rng
        )
        return Divergence(
            dimension=DivergenceDimension.RETURN_DISTRIBUTION,
            status=(
                DivergenceStatus.DIVERGED if p <= self.config.alpha else DivergenceStatus.AGREES
            ),
            expected=0.0,
            observed=float(statistic),
            n_observations=int(b.size),
            p_value=p,
            method="two-sample KS with a block-permutation null",
            caveat=(
                "the shape of the return distribution can diverge while the mean "
                "agrees; that is usually a change in how exits are being hit"
            ),
            detail={"ks_statistic": float(statistic), "n_backtest": int(a.size)},
        )

    def _drawdown(
        self, expectation: Expectation, observation: Observation, rng: np.random.Generator
    ) -> Divergence:
        expected = expectation.max_drawdown
        series = observation.returns
        if expected is None:
            return _unevaluated(
                DivergenceDimension.DRAWDOWN, "no expected max drawdown supplied"
            )
        if series.size < self.config.min_observations:
            return _unevaluated(
                DivergenceDimension.DRAWDOWN,
                f"{series.size} observations, {self.config.min_observations} required",
                n=int(series.size),
            )
        if expectation.returns.size < series.size:
            return _unevaluated(
                DivergenceDimension.DRAWDOWN,
                f"the forward record ({series.size}) is longer than the backtest "
                f"({expectation.returns.size}); the backtest cannot supply a "
                "comparison window of this length",
                n=int(series.size),
            )
        observed = max_drawdown(series)
        null, block = self._null_windows(
            expectation.returns, int(series.size), max_drawdown, rng
        )
        p = self._tail(null, observed, larger_is_better=False)
        lo, hi = np.quantile(null, [self.config.alpha / 2.0, 1.0 - self.config.alpha / 2.0])
        return Divergence(
            dimension=DivergenceDimension.DRAWDOWN,
            status=(
                DivergenceStatus.DIVERGED if p <= self.config.alpha else DivergenceStatus.AGREES
            ),
            expected=float(expected),
            observed=observed,
            n_observations=int(series.size),
            ci_lower=float(lo),
            ci_upper=float(hi),
            p_value=p,
            method=(
                "compounded max drawdown against block-resampled backtest "
                "windows of the forward length"
            ),
            caveat=(
                "this is a DIVERGENCE signal, not the halt: the pre-registered "
                "bootstrap drawdown limit in lifecycle.stopping_rules is what "
                "halts, and its threshold was fixed before the strategy ran"
            ),
            detail={"block_length": block},
        )

    def _rejected_orders(
        self, expectation: Expectation, observation: Observation
    ) -> Divergence:
        expected_rate = expectation.reject_rate
        n = int(observation.orders_submitted)
        if expected_rate is None:
            return _unevaluated(
                DivergenceDimension.REJECTED_ORDERS, "no expected rejection rate supplied"
            )
        if n < self.config.min_cost_observations:
            return _unevaluated(
                DivergenceDimension.REJECTED_ORDERS,
                f"{n} orders submitted, {self.config.min_cost_observations} required",
                n=n,
            )
        rejected = int(observation.orders_rejected)
        observed = rejected / n
        # One-sided binomial: more rejections than modelled. Fewer is good news.
        p = float(_sps.binomtest(rejected, n, max(1e-9, float(expected_rate)),
                                alternative="greater").pvalue)
        return Divergence(
            dimension=DivergenceDimension.REJECTED_ORDERS,
            status=(
                DivergenceStatus.DIVERGED if p <= self.config.alpha else DivergenceStatus.AGREES
            ),
            expected=float(expected_rate),
            observed=float(observed),
            n_observations=n,
            p_value=p,
            method="one-sided binomial tail on the rejection rate",
            caveat=(
                "APPROXIMATION: rejections cluster (a venue having a bad five "
                "minutes rejects several), so the binomial tail is optimistic "
                "about how surprising a burst is"
            ),
            detail={"rejected": rejected, "submitted": n},
        )

    def _regime(
        self, expectation: Expectation, observation: Observation, rng: np.random.Generator
    ) -> Divergence:
        """Per-regime behaviour, via :mod:`fiboki.marketstate.regime` keys.

        The V1 audit's finding made continuous: six of fourteen surviving results
        were one instrument during a single exceptional trend. If an edge is
        regime-conditional, the forward record will show it first as one regime
        going quietly wrong while the aggregate still looks acceptable.
        """
        expected_by_key = expectation.regime_mean_return
        observed_by_key = observation.regime_returns
        if not expected_by_key or not observed_by_key:
            return _unevaluated(
                DivergenceDimension.REGIME,
                "needs an expected and an observed mean return per regime key",
            )
        shared = sorted(set(expected_by_key) & set(observed_by_key))
        rows: list[dict[str, Any]] = []
        worst_p: float | None = None
        worst_key = ""
        for key in shared:
            series = _clean(observed_by_key[key])
            if series.size < self.config.min_regime_observations:
                rows.append({"regime": key, "n": int(series.size), "status": "not_evaluated"})
                continue
            expected = float(expected_by_key[key])
            observed = _mean(series)
            dist, _ = self._bootstrap_distribution(series, _mean, rng)
            # Per regime there is only an expected mean, not a backtest series,
            # so the observed bootstrap is the reference: how plausible is the
            # expected mean given what this regime actually delivered?
            p = self._tail(dist, expected, larger_is_better=False)
            rows.append(
                {
                    "regime": key,
                    "n": int(series.size),
                    "expected": expected,
                    "observed": observed,
                    "p_value": p,
                    "status": "diverged" if p <= self.config.alpha else "agrees",
                }
            )
            if worst_p is None or p < worst_p:
                worst_p, worst_key = p, key
        evaluated = [r for r in rows if r["status"] != "not_evaluated"]
        if not evaluated:
            return _unevaluated(
                DivergenceDimension.REGIME,
                f"no regime reached {self.config.min_regime_observations} observations; "
                "the sample cannot distinguish a regime effect from noise",
                detail={"regimes": rows},
            )
        diverged = [r for r in evaluated if r["status"] == "diverged"]
        worst_row = next((r for r in evaluated if r["regime"] == worst_key), evaluated[0])
        return Divergence(
            dimension=DivergenceDimension.REGIME,
            status=DivergenceStatus.DIVERGED if diverged else DivergenceStatus.AGREES,
            expected=float(worst_row.get("expected", math.nan)),
            observed=float(worst_row.get("observed", math.nan)),
            n_observations=int(sum(int(r["n"]) for r in evaluated)),
            p_value=worst_p,
            method="per-regime block bootstrap of the mean return, worst regime reported",
            reason=(
                f"{len(diverged)} of {len(evaluated)} evaluated regimes diverged"
                if diverged
                else ""
            ),
            caveat=(
                "an aggregate that still looks acceptable while one regime goes "
                "wrong is how a regime-conditional edge decays"
            ),
            detail={"regimes": rows, "worst_regime": worst_key},
        )


def _unevaluated(
    dimension: DivergenceDimension,
    reason: str,
    *,
    n: int = 0,
    detail: Mapping[str, Any] | None = None,
) -> Divergence:
    return Divergence(
        dimension=dimension,
        status=DivergenceStatus.NOT_EVALUATED,
        n_observations=n,
        reason=reason,
        method="none",
        detail=dict(detail or {}),
    )
