"""Multidimensional regime classification.

The crude approach — one label per bar, "trending" or "ranging" — throws away
almost everything that matters. A market can be quietly grinding upward in thin
Asian liquidity, or violently reversing in the London/New York overlap with
correlations collapsing; a single label calls both of those "trending" and then
a strategy's edge appears to be unstable for no visible reason.

Regime here is therefore a **vector of independent axes**:

======================  ================================================
axis                    what it answers
======================  ================================================
:class:`DirectionAxis`  which way, and how convincingly
:class:`VolatilityAxis` how big are the moves, relative to this
                        instrument's own history
:class:`PersistenceAxis` do moves continue or reverse
:class:`LiquidityAxis`  how deep is the book / which session
:class:`StressAxis`     is the market behaving abnormally (gaps, shocks,
                        correlation convergence)
======================  ================================================

Two design commitments:

**Thresholds come from expanding-window quantiles, and they are written down.**
A fixed "ATR > 0.5%" threshold means something different for XAUUSD in 2011 and
2020 and nothing at all for EURUSD. Every cut here is a quantile of the
instrument's *own past*, published in :class:`RegimeThresholds` so a reader can
disagree with a specific number rather than with a vibe.

**Regimes have inertia.** Classified bar by bar, any quantile rule flaps: a
value sitting on a cut line crosses it repeatedly and the "regime" changes every
other bar, making regime-conditional statistics meaningless. A confirmation rule
(:attr:`RegimeConfig.min_dwell_bars`) requires a candidate to hold for N
consecutive bars before it is adopted. This is causal and it *lags* — that is
the honest trade, and it is documented rather than hidden.

The payoff is :func:`regime_segmented_performance`. The V1 audit found that six
of fourteen surviving results were USDJPY during one exceptional trend. That is
not a finding about a strategy, it is a finding about a regime, and the only way
to tell the difference is to cut the trades by the regime that was current when
each one fired.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.contracts import Trade
from fiboki.marketstate.features import (
    SESSION_CODES,
    FeatureSet,
    expanding_rank_pct,
)

REGIME_VERSION = "1.0.0"


class RegimeError(ValueError):
    """The features cannot support a regime classification."""


# =====================================================================
# Axes
# =====================================================================


class RegimeAxis(str, Enum):
    """The axes, as stable strings. Persisted — never renamed."""

    DIRECTION = "direction"
    VOLATILITY = "volatility"
    PERSISTENCE = "persistence"
    LIQUIDITY = "liquidity"
    STRESS = "stress"


class DirectionAxis(str, Enum):
    UNKNOWN = "unknown"
    STRONG_DOWN = "strong_down"
    DOWN = "down"
    NEUTRAL = "neutral"
    UP = "up"
    STRONG_UP = "strong_up"

    @property
    def sign(self) -> int:
        return {
            DirectionAxis.STRONG_DOWN: -1,
            DirectionAxis.DOWN: -1,
            DirectionAxis.NEUTRAL: 0,
            DirectionAxis.UP: 1,
            DirectionAxis.STRONG_UP: 1,
            DirectionAxis.UNKNOWN: 0,
        }[self]


class VolatilityAxis(str, Enum):
    UNKNOWN = "unknown"
    VERY_LOW = "very_low"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class PersistenceAxis(str, Enum):
    UNKNOWN = "unknown"
    MEAN_REVERTING = "mean_reverting"
    RANDOM = "random"
    TRENDING = "trending"


class LiquidityAxis(str, Enum):
    UNKNOWN = "unknown"
    OFF_HOURS = "off_hours"
    THIN = "thin"
    NORMAL = "normal"
    DEEP = "deep"


class StressAxis(str, Enum):
    UNKNOWN = "unknown"
    CALM = "calm"
    ELEVATED = "elevated"
    STRESSED = "stressed"


_AXIS_ENUM: dict[RegimeAxis, type[Enum]] = {
    RegimeAxis.DIRECTION: DirectionAxis,
    RegimeAxis.VOLATILITY: VolatilityAxis,
    RegimeAxis.PERSISTENCE: PersistenceAxis,
    RegimeAxis.LIQUIDITY: LiquidityAxis,
    RegimeAxis.STRESS: StressAxis,
}


def regime_column(axis: RegimeAxis | str) -> str:
    """Column name an axis takes when joined onto trades or signals.

    Prefixed, because ``direction`` is both a regime axis and a property of
    every trade and signal in the system. An unprefixed join silently produced
    ``direction_x``/``direction_y`` and any caller asking for "direction" then
    got whichever pandas happened to keep. Joined regime columns are therefore
    always ``regime_<axis>``; the raw :attr:`RegimeSeries.frame` keeps the bare
    names, because nothing else lives in it.
    """
    return f"regime_{RegimeAxis(axis).value}"


#: The columns a regime join contributes, in a fixed order.
REGIME_JOIN_COLUMNS: tuple[str, ...] = (
    "regime_key",
    *(regime_column(a) for a in RegimeAxis),
)


@dataclass(frozen=True, slots=True)
class RegimeVector:
    """One bar's market state, on five independent axes."""

    direction: DirectionAxis = DirectionAxis.UNKNOWN
    volatility: VolatilityAxis = VolatilityAxis.UNKNOWN
    persistence: PersistenceAxis = PersistenceAxis.UNKNOWN
    liquidity: LiquidityAxis = LiquidityAxis.UNKNOWN
    stress: StressAxis = StressAxis.UNKNOWN

    @property
    def key(self) -> str:
        """Stable grouping key, e.g. ``up|high|trending|deep|calm``.

        Order is fixed forever: direction, volatility, persistence, liquidity,
        stress. Stored results group on this string, so reordering it would
        silently invalidate every stored regime-conditional statistic.
        """
        return "|".join(
            (
                self.direction.value,
                self.volatility.value,
                self.persistence.value,
                self.liquidity.value,
                self.stress.value,
            )
        )

    @classmethod
    def from_key(cls, key: str) -> RegimeVector:
        parts = key.split("|")
        if len(parts) != 5:
            raise RegimeError(f"malformed regime key {key!r}")
        return cls(
            DirectionAxis(parts[0]),
            VolatilityAxis(parts[1]),
            PersistenceAxis(parts[2]),
            LiquidityAxis(parts[3]),
            StressAxis(parts[4]),
        )

    def axis(self, axis: RegimeAxis | str) -> str:
        a = RegimeAxis(axis)
        return str(getattr(self, a.value).value)

    @property
    def is_known(self) -> bool:
        return DirectionAxis.UNKNOWN not in (
            self.direction,
        ) and VolatilityAxis.UNKNOWN is not self.volatility

    def to_dict(self) -> dict[str, str]:
        return {a.value: self.axis(a) for a in RegimeAxis}

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.key


# =====================================================================
# Thresholds and configuration
# =====================================================================


@dataclass(frozen=True, slots=True)
class RegimeThresholds:
    """Every cut point, as an expanding-window quantile in ``[0, 1]``.

    Read these as "this bar sits above the q-th percentile of everything this
    instrument has done up to and including now". They are not tuned; they are
    stated. Change them deliberately and re-run, do not nudge them until a
    strategy looks good.
    """

    # -- direction: quantiles of |trend t-stat| ----------------------
    #: Below this the trend is unremarkable for this instrument → NEUTRAL.
    direction_neutral_q: float = 0.60
    #: Above this the trend is in the instrument's own top decile → STRONG_*.
    direction_strong_q: float = 0.90
    #: Absolute guard band, in **window-normalised** t-stat units
    #: ``|t| / sqrt(horizon)``.
    #:
    #: A rank-only direction axis has a failure mode that is easy to miss and
    #: embarrassing to ship: an instrument that has already produced one
    #: exceptional trend judges every later trend against it, so a genuine crash
    #: with ``|t| = 31`` can rank at the median of its own history and be
    #: labelled NEUTRAL. The floor prevents that — a move this far from a random
    #: walk is never "no trend", whatever its rank.
    #:
    #: The value is calibrated against the *driftless random walk* null, not a
    #: t-table. Regressing a random walk on time is a spurious regression: the
    #: t-stat is not t-distributed and grows like ``sqrt(window)``. Measured over
    #: 160k simulated windows, ``|t| / sqrt(window)`` is approximately
    #: window-independent with median ~0.86 and 90th percentile ~2.25 at both
    #: window 25 and window 50. A textbook ``|t| > 2`` rule would therefore
    #: label a random walk "trending" on roughly 85% of bars. 2.25 is the 90th
    #: percentile of the null: one bar in ten of pure noise clears it, which is
    #: an honest false-positive rate, not a promise of none.
    direction_random_walk_floor: float = 2.25

    # -- volatility: quantiles of realised volatility ----------------
    vol_very_low_q: float = 0.10
    vol_low_q: float = 0.30
    vol_high_q: float = 0.70
    vol_extreme_q: float = 0.90

    # -- persistence: quantiles of the variance ratio ----------------
    persistence_mean_reverting_q: float = 0.30
    persistence_trending_q: float = 0.70
    #: Absolute guard band. Even at an extreme quantile, a variance ratio inside
    #: ``1 ± band`` is not evidence of anything — it is a random walk that
    #: happens to be at the edge of a quiet sample. Both the quantile *and* the
    #: band must agree before a non-RANDOM label is issued.
    persistence_vr_band: float = 0.05

    # -- liquidity: quantiles of the liquidity proxy -----------------
    liquidity_thin_q: float = 0.30
    liquidity_deep_q: float = 0.70

    # -- stress: quantiles of the composite stress score -------------
    stress_elevated_q: float = 0.70
    stress_extreme_q: float = 0.90

    def __post_init__(self) -> None:
        pairs = [
            ("direction_neutral_q", "direction_strong_q"),
            ("vol_very_low_q", "vol_low_q"),
            ("vol_low_q", "vol_high_q"),
            ("vol_high_q", "vol_extreme_q"),
            ("persistence_mean_reverting_q", "persistence_trending_q"),
            ("liquidity_thin_q", "liquidity_deep_q"),
            ("stress_elevated_q", "stress_extreme_q"),
        ]
        for lo, hi in pairs:
            if not getattr(self, lo) < getattr(self, hi):
                raise RegimeError(f"{lo} must be strictly below {hi}")
        for f in asdict(self):
            v = getattr(self, f)
            if f.endswith("_q") and not 0.0 < v < 1.0:
                raise RegimeError(f"{f} must be a quantile strictly inside (0,1)")
        if self.direction_random_walk_floor <= 0.0:
            raise RegimeError("direction_random_walk_floor must be positive")

    def to_dict(self) -> dict[str, float]:
        return dict(asdict(self))


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    """How the axes are wired to features, and how much inertia they have."""

    thresholds: RegimeThresholds = field(default_factory=RegimeThresholds)
    #: Consecutive bars a candidate regime must hold before it is adopted. 1
    #: disables confirmation and will flap; the default of 3 is the smallest
    #: value that removes single-bar chatter on H4 in testing.
    min_dwell_bars: int = 3
    #: Trend horizon whose t-stat drives the direction axis.
    direction_horizon: int = 50
    #: Realised-volatility period driving the volatility axis.
    volatility_period: int = 20
    #: Variance-ratio lag driving the persistence axis.
    persistence_lag: int = 2
    #: Stress inputs, each a feature name expected to be an expanding percentile
    #: (or a raw series, which will be ranked). Averaged into one score.
    stress_features: tuple[str, ...] = ("gap_frequency", "abnormal_frequency")
    #: Treat off-session bars as their own liquidity regime rather than ranking
    #: them against in-session bars.
    off_hours_is_its_own_regime: bool = True

    def __post_init__(self) -> None:
        if self.min_dwell_bars < 1:
            raise RegimeError("min_dwell_bars must be >= 1")
        if not self.stress_features:
            raise RegimeError("at least one stress feature is required")

    def to_dict(self) -> dict[str, Any]:
        d = {
            "thresholds": self.thresholds.to_dict(),
            "min_dwell_bars": self.min_dwell_bars,
            "direction_horizon": self.direction_horizon,
            "volatility_period": self.volatility_period,
            "persistence_lag": self.persistence_lag,
            "stress_features": list(self.stress_features),
            "off_hours_is_its_own_regime": self.off_hours_is_its_own_regime,
        }
        return d


# =====================================================================
# Confirmation (the anti-flap rule)
# =====================================================================


def confirm(labels: Sequence[str], *, min_dwell: int, unknown: str) -> np.ndarray:
    """Adopt a new label only after it has held for ``min_dwell`` bars.

    Strictly causal: the output at bar ``t`` depends on ``labels[0..t]``. The
    state machine is:

    * start in ``unknown``;
    * the first non-``unknown`` label that repeats ``min_dwell`` times is
      adopted immediately (there is nothing to be loyal to yet);
    * afterwards, a differing candidate must repeat ``min_dwell`` consecutive
      times before it replaces the held label; any interruption resets the count.

    The cost is a ``min_dwell - 1`` bar lag at every genuine transition. That is
    a real cost and it is the reason the classifier's transition timestamps are
    described as "confirmed at", never "occurred at".
    """
    if min_dwell < 1:
        raise RegimeError("min_dwell must be >= 1")
    out = np.empty(len(labels), dtype=object)
    held = unknown
    candidate = unknown
    run = 0
    for i, lab in enumerate(labels):
        if lab == unknown:
            # No opinion this bar. Keep what we hold, reset the challenge.
            candidate = unknown
            run = 0
            out[i] = held
            continue
        if lab == held:
            candidate = unknown
            run = 0
            out[i] = held
            continue
        if lab == candidate:
            run += 1
        else:
            candidate = lab
            run = 1
        if run >= min_dwell:
            held = candidate
            candidate = unknown
            run = 0
        out[i] = held
    return out


# =====================================================================
# Classifier
# =====================================================================


@dataclass(frozen=True, slots=True)
class RegimeSeries:
    """Per-bar regime labels plus the diagnostics needed to trust them."""

    instrument: str
    timeframe: str
    frame: pd.DataFrame
    config: RegimeConfig
    fingerprint: str
    warmup: int
    notes: tuple[str, ...] = ()

    # -- access ------------------------------------------------------

    @property
    def keys(self) -> pd.Series:
        return self.frame["regime_key"]

    def at(self, ts: pd.Timestamp) -> RegimeVector:
        """The regime *as of* ``ts`` — the last bar at or before it."""
        idx = self.frame.index
        pos = idx.searchsorted(pd.Timestamp(ts), side="right") - 1
        if pos < 0:
            raise KeyError(f"{ts} precedes the regime history ({idx.min()})")
        return RegimeVector.from_key(str(self.frame["regime_key"].iloc[pos]))

    def vectors(self) -> list[RegimeVector]:
        return [RegimeVector.from_key(k) for k in self.frame["regime_key"]]

    # -- distributions -----------------------------------------------

    def axis_distribution(self, axis: RegimeAxis | str) -> pd.Series:
        a = RegimeAxis(axis)
        col = self.frame[a.value]
        valid = col[self.warmup :]
        return valid.value_counts(normalize=True).sort_values(ascending=False)

    def key_distribution(self, *, top: int | None = None) -> pd.Series:
        valid = self.frame["regime_key"].iloc[self.warmup :]
        dist = valid.value_counts(normalize=True).sort_values(ascending=False)
        return dist.head(top) if top else dist

    # -- transitions and durations -----------------------------------

    def transitions(self, axis: RegimeAxis | str | None = None) -> pd.DataFrame:
        """Every confirmed regime change, with the run length that preceded it."""
        col = "regime_key" if axis is None else RegimeAxis(axis).value
        s = self.frame[col].iloc[self.warmup :]
        if s.empty:
            return pd.DataFrame(
                columns=["timestamp", "from", "to", "previous_duration_bars", "axes_changed"]
            ).set_index("timestamp")
        changed = s != s.shift(1)
        changed.iloc[0] = False
        idx = np.flatnonzero(changed.to_numpy())
        rows = []
        prev_start = 0
        for i in idx:
            frm = str(s.iloc[i - 1])
            to = str(s.iloc[i])
            if axis is None:
                a = RegimeVector.from_key(frm)
                b = RegimeVector.from_key(to)
                axes = tuple(
                    ax.value for ax in RegimeAxis if a.axis(ax) != b.axis(ax)
                )
            else:
                axes = (RegimeAxis(axis).value,)
            rows.append(
                {
                    "timestamp": s.index[i],
                    "from": frm,
                    "to": to,
                    "previous_duration_bars": int(i - prev_start),
                    "axes_changed": ",".join(axes),
                }
            )
            prev_start = i
        return pd.DataFrame(rows).set_index("timestamp")

    def durations(self, axis: RegimeAxis | str | None = None) -> pd.DataFrame:
        """Run-length statistics per label: how long a regime actually lasts."""
        col = "regime_key" if axis is None else RegimeAxis(axis).value
        s = self.frame[col].iloc[self.warmup :]
        if s.empty:
            return pd.DataFrame(
                columns=["episodes", "bars", "mean_bars", "median_bars", "max_bars", "share"]
            )
        run_id = (s != s.shift(1)).cumsum()
        runs = s.groupby(run_id).agg(label="first", bars="size")
        grp = runs.groupby("label")["bars"]
        out = pd.DataFrame(
            {
                "episodes": grp.size(),
                "bars": grp.sum(),
                "mean_bars": grp.mean(),
                "median_bars": grp.median(),
                "max_bars": grp.max(),
            }
        )
        out["share"] = out["bars"] / out["bars"].sum()
        return out.sort_values("bars", ascending=False)

    def _change_rate(self, cols: Sequence[str]) -> float:
        warm = self.frame.iloc[self.warmup :]
        if len(warm) < 2:
            return 0.0
        joined = warm[list(cols)].astype(str).agg("|".join, axis=1)
        return float((joined != joined.shift(1)).iloc[1:].mean())

    @property
    def flap_rate(self) -> float:
        """Fraction of warm bars on which the full regime key changed.

        Note what this includes: the liquidity/session axis is a *clock*, and on
        H4 the clock legitimately changes every few bars as Tokyo hands to
        London hands to New York. The full key therefore inherits the session
        cadence and its change rate is structurally high — that is not flapping,
        it is the calendar. Use :attr:`estimated_flap_rate` to judge the axes
        that are actually *estimated* from price.
        """
        return self._change_rate(["regime_key"])

    @property
    def estimated_flap_rate(self) -> float:
        """Change rate of the price-estimated axes only (session excluded).

        This is the number that says whether the classifier is stable. A healthy
        H4 classifier sits well below 0.15; approaching 0.5 means the labels are
        noise and every regime-conditional statistic built on them is worthless.
        """
        cols = [
            a.value
            for a in RegimeAxis
            if a is not RegimeAxis.LIQUIDITY
        ]
        return self._change_rate(cols)

    def axis_flap_rate(self, axis: RegimeAxis | str) -> float:
        return self._change_rate([RegimeAxis(axis).value])

    def mean_duration(self, axis: RegimeAxis | str | None = None) -> float:
        d = self.durations(axis)
        if d.empty:
            return 0.0
        return float(d["bars"].sum() / d["episodes"].sum())

    # -- joining to trades -------------------------------------------

    def segment_trades(
        self, trades: Sequence[Trade], *, on: str = "entry_time"
    ) -> pd.DataFrame:
        """Attach to each trade the regime that was current when it fired.

        ``on="entry_time"`` is the default and the only one that answers "did the
        strategy have an edge in this regime" — the exit regime is contaminated
        by the trade's own outcome.

        The join is backward-asof: a trade entered at 13:07 on an H4 clock is
        assigned the regime of the 12:00 bar, i.e. the last *closed* bar before
        it. A trade before the classifier is warm gets ``NaN`` and is excluded
        from regime statistics rather than dumped into an "unknown" bucket that
        then looks like a real regime.
        """
        if on not in ("entry_time", "exit_time"):
            raise RegimeError("segment_trades 'on' must be entry_time or exit_time")
        if not trades:
            return pd.DataFrame(
                columns=[
                    "trade_id", "instrument", "strategy_id", "direction",
                    "entry_time", "exit_time", "net_pnl", "bars_held",
                    *REGIME_JOIN_COLUMNS,
                ]
            )
        left = pd.DataFrame(
            {
                "trade_id": [t.trade_id for t in trades],
                "instrument": [t.instrument for t in trades],
                "strategy_id": [t.strategy_id for t in trades],
                "direction": [t.direction.value for t in trades],
                "entry_time": [pd.Timestamp(t.entry_time) for t in trades],
                "exit_time": [pd.Timestamp(t.exit_time) for t in trades],
                "net_pnl": [float(t.net_pnl) for t in trades],
                "bars_held": [int(t.bars_held) for t in trades],
            }
        )
        warm = self.frame.iloc[self.warmup :]
        right = warm[["regime_key", *[a.value for a in RegimeAxis]]].copy()
        right = right.rename(columns={a.value: regime_column(a) for a in RegimeAxis})
        right["_regime_time"] = right.index
        right = right.reset_index(drop=True).sort_values("_regime_time")
        left = left.sort_values(on).reset_index(drop=True)
        merged = pd.merge_asof(
            left,
            right,
            left_on=on,
            right_on="_regime_time",
            direction="backward",
        )
        return merged.drop(columns=["_regime_time"])


class RegimeClassifier:
    """Turns a :class:`FeatureSet` into a :class:`RegimeSeries`.

    All classification inputs are expanding percentiles computed inside the
    feature engine (or here, from a raw series). No threshold in this class is
    derived from data the bar could not have seen.
    """

    def __init__(self, config: RegimeConfig | None = None) -> None:
        self.config = config or RegimeConfig()

    @property
    def fingerprint(self) -> str:
        blob = json.dumps(
            {"version": REGIME_VERSION, "config": self.config.to_dict()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(blob).hexdigest()[:32]

    # -- helpers -----------------------------------------------------

    @staticmethod
    def _require(features: pd.DataFrame, name: str, axis: str) -> pd.Series:
        if name not in features.columns:
            raise RegimeError(
                f"the {axis} axis needs feature {name!r}, which the FeatureSet "
                f"does not publish. Available: {sorted(features.columns)[:12]}..."
            )
        return features[name].astype(float)

    # -- classification ---------------------------------------------

    def classify(
        self,
        features: FeatureSet,
        *,
        stress_inputs: pd.DataFrame | None = None,
    ) -> RegimeSeries:
        """Classify every bar. ``stress_inputs`` may add cross-asset columns.

        ``stress_inputs`` is expected to be indexed compatibly with the feature
        frame; it is reindexed onto it with *no* forward fill, so a cross-asset
        series that does not cover a bar simply does not contribute to that
        bar's stress score rather than contributing a stale one.
        """
        cfg = self.config
        th = cfg.thresholds
        f = features.frame
        n = len(f)
        idx = f.index
        notes: list[str] = []

        # -- direction ------------------------------------------------
        h = cfg.direction_horizon
        tstat = self._require(f, f"trend_tstat_{h}", "direction")
        tabs_pct = self._require(f, f"trend_tstat_abs_pct_{h}", "direction")
        raw_t = tstat.to_numpy()
        sign = np.sign(raw_t)
        mag = tabs_pct.to_numpy()
        # Window-normalised |t|: under a driftless random walk this is
        # approximately window-independent (see RegimeThresholds).
        with np.errstate(invalid="ignore"):
            z = np.abs(raw_t) / math.sqrt(h)
        direction = np.full(n, DirectionAxis.UNKNOWN.value, dtype=object)
        known = ~np.isnan(mag) & ~np.isnan(sign) & ~np.isnan(z)
        above_null = z >= th.direction_random_walk_floor
        # NEUTRAL needs BOTH an unremarkable rank AND a move consistent with a
        # random walk; STRONG needs BOTH a top-decile rank AND a move clearly
        # outside the random-walk null. Either condition alone is a known way to
        # be wrong: rank alone mislabels a crash that follows a bigger rally,
        # and an absolute threshold alone calls noise a trend.
        neutral = known & (mag < th.direction_neutral_q) & ~above_null
        strong = known & (mag >= th.direction_strong_q) & above_null
        mild = known & ~neutral & ~strong
        direction[neutral] = DirectionAxis.NEUTRAL.value
        direction[mild & (sign > 0)] = DirectionAxis.UP.value
        direction[mild & (sign < 0)] = DirectionAxis.DOWN.value
        direction[mild & (sign == 0)] = DirectionAxis.NEUTRAL.value
        direction[strong & (sign > 0)] = DirectionAxis.STRONG_UP.value
        direction[strong & (sign < 0)] = DirectionAxis.STRONG_DOWN.value
        direction[strong & (sign == 0)] = DirectionAxis.NEUTRAL.value

        # -- volatility -----------------------------------------------
        vp = cfg.volatility_period
        vol_pct = self._require(f, f"rv_pct_{vp}", "volatility").to_numpy()
        volatility = np.full(n, VolatilityAxis.UNKNOWN.value, dtype=object)
        vk = ~np.isnan(vol_pct)
        volatility[vk] = VolatilityAxis.NORMAL.value
        volatility[vk & (vol_pct < th.vol_low_q)] = VolatilityAxis.LOW.value
        volatility[vk & (vol_pct < th.vol_very_low_q)] = VolatilityAxis.VERY_LOW.value
        volatility[vk & (vol_pct >= th.vol_high_q)] = VolatilityAxis.HIGH.value
        volatility[vk & (vol_pct >= th.vol_extreme_q)] = VolatilityAxis.EXTREME.value

        # -- persistence ----------------------------------------------
        q = cfg.persistence_lag
        vr = self._require(f, f"variance_ratio_{q}", "persistence").to_numpy()
        vr_pct = self._require(f, f"variance_ratio_{q}_pct", "persistence").to_numpy()
        persistence = np.full(n, PersistenceAxis.UNKNOWN.value, dtype=object)
        pk = ~np.isnan(vr_pct) & ~np.isnan(vr)
        persistence[pk] = PersistenceAxis.RANDOM.value
        band = th.persistence_vr_band
        mr = pk & (vr_pct < th.persistence_mean_reverting_q) & (vr < 1.0 - band)
        tr = pk & (vr_pct >= th.persistence_trending_q) & (vr > 1.0 + band)
        persistence[mr] = PersistenceAxis.MEAN_REVERTING.value
        persistence[tr] = PersistenceAxis.TRENDING.value

        # -- liquidity / session --------------------------------------
        liq_pct = self._require(f, "liquidity_proxy", "liquidity")
        session = self._require(f, "session_code", "liquidity").to_numpy()
        # Rank the liquidity proxy against its own expanding history so the cut
        # points are the instrument's own, not a cross-instrument constant.
        liq_rank = expanding_rank_pct(
            liq_pct.to_numpy(), min_periods=features.config.percentile_min_periods
        )
        liquidity = np.full(n, LiquidityAxis.UNKNOWN.value, dtype=object)
        lk = ~np.isnan(liq_rank)
        liquidity[lk] = LiquidityAxis.NORMAL.value
        liquidity[lk & (liq_rank < th.liquidity_thin_q)] = LiquidityAxis.THIN.value
        liquidity[lk & (liq_rank >= th.liquidity_deep_q)] = LiquidityAxis.DEEP.value
        if cfg.off_hours_is_its_own_regime:
            off = session == SESSION_CODES["off_hours"]
            liquidity[off] = LiquidityAxis.OFF_HOURS.value

        # -- stress ---------------------------------------------------
        components: list[np.ndarray] = []
        used: list[str] = []
        for name in cfg.stress_features:
            if name not in f.columns:
                notes.append(f"stress feature {name!r} absent; excluded from the score")
                continue
            raw = f[name].astype(float).to_numpy()
            if np.isnan(raw).all():
                notes.append(f"stress feature {name!r} is entirely NaN; excluded")
                continue
            components.append(
                expanding_rank_pct(
                    raw, min_periods=features.config.percentile_min_periods
                )
            )
            used.append(name)
        if stress_inputs is not None:
            aligned = stress_inputs.reindex(idx)
            for col in aligned.columns:
                raw = aligned[col].astype(float).to_numpy()
                if np.isnan(raw).all():
                    notes.append(f"cross-asset stress input {col!r} does not "
                                 "cover these bars; excluded")
                    continue
                components.append(
                    expanding_rank_pct(
                        raw, min_periods=features.config.percentile_min_periods
                    )
                )
                used.append(f"cross_asset:{col}")
        if not components:
            raise RegimeError(
                "no usable stress inputs; the stress axis would be a constant. "
                f"Requested {list(cfg.stress_features)}."
            )
        stack = np.vstack(components)
        all_nan = np.isnan(stack).all(axis=0)
        score = np.full(n, np.nan)
        if (~all_nan).any():
            score[~all_nan] = np.nanmean(stack[:, ~all_nan], axis=0)
        stress = np.full(n, StressAxis.UNKNOWN.value, dtype=object)
        sk = ~np.isnan(score)
        stress[sk] = StressAxis.CALM.value
        stress[sk & (score >= th.stress_elevated_q)] = StressAxis.ELEVATED.value
        stress[sk & (score >= th.stress_extreme_q)] = StressAxis.STRESSED.value
        notes.append(f"stress score averages {len(used)} input(s): {', '.join(used)}")

        # -- confirmation (anti-flap) ---------------------------------
        raw_labels = {
            RegimeAxis.DIRECTION: direction,
            RegimeAxis.VOLATILITY: volatility,
            RegimeAxis.PERSISTENCE: persistence,
            RegimeAxis.LIQUIDITY: liquidity,
            RegimeAxis.STRESS: stress,
        }
        confirmed: dict[str, np.ndarray] = {}
        for axis, labels in raw_labels.items():
            enum_cls = _AXIS_ENUM[axis]
            unknown = enum_cls.UNKNOWN.value  # type: ignore[attr-defined]
            if axis is RegimeAxis.LIQUIDITY and cfg.off_hours_is_its_own_regime:
                # The session axis is a calendar fact, not a noisy estimate;
                # smoothing it would blur session boundaries, which is the one
                # thing it exists to mark.
                confirmed[axis.value] = np.asarray(labels, dtype=object)
                continue
            confirmed[axis.value] = confirm(
                list(labels), min_dwell=cfg.min_dwell_bars, unknown=unknown
            )

        frame = pd.DataFrame(confirmed, index=idx)
        for axis, labels in raw_labels.items():
            frame[f"{axis.value}_raw"] = labels
        frame["regime_key"] = (
            frame[RegimeAxis.DIRECTION.value]
            + "|" + frame[RegimeAxis.VOLATILITY.value]
            + "|" + frame[RegimeAxis.PERSISTENCE.value]
            + "|" + frame[RegimeAxis.LIQUIDITY.value]
            + "|" + frame[RegimeAxis.STRESS.value]
        )
        frame["stress_score"] = score
        frame["direction_strength"] = mag
        frame["volatility_percentile"] = vol_pct
        frame.index.name = "timestamp"

        # A bar is only classifiable once every driving feature is warm, plus
        # the confirmation lag. Features the *classifier* ranks (rather than
        # consuming an already-ranked feature) need a second percentile warmup
        # on top of their own: an expanding rank of a series that itself only
        # starts at bar 120 does not become meaningful until bar 220.
        pre_ranked = [
            f"trend_tstat_abs_pct_{h}",
            f"rv_pct_{vp}",
            f"variance_ratio_{q}_pct",
        ]
        ranked_here = [
            "liquidity_proxy",
            *[u for u in used if not u.startswith("cross_asset:")],
        ]
        pmin = features.config.percentile_min_periods
        warm = max(
            max(features.spec(name).warmup for name in pre_ranked),
            max(features.spec(name).warmup + pmin for name in ranked_here),
        )
        warm += cfg.min_dwell_bars - 1
        warm = min(warm, n)
        return RegimeSeries(
            instrument=features.instrument,
            timeframe=features.timeframe,
            frame=frame,
            config=cfg,
            fingerprint=self.fingerprint,
            warmup=warm,
            notes=tuple(notes),
        )


# =====================================================================
# Regime-segmented performance
# =====================================================================


def _safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


@dataclass(frozen=True, slots=True)
class RegimeDependence:
    """Verdict on whether a strategy's edge is a regime artefact."""

    table: pd.DataFrame
    n_trades: int
    n_regimes_with_trades: int
    n_regimes_sufficient: int
    top_regime: str | None
    top_regime_pnl_share: float | None
    top_regime_trade_share: float | None
    profitable_without_top: bool | None
    min_trades: int

    @property
    def verdict(self) -> str:
        if self.n_trades == 0:
            return "no trades to segment"
        if self.n_regimes_sufficient == 0:
            return (
                f"no regime reached {self.min_trades} trades: the sample cannot "
                "distinguish a regime effect from noise"
            )
        if self.top_regime_pnl_share is not None and self.top_regime_pnl_share > 0.6:
            tail = "" if self.profitable_without_top else " and is a loss without it"
            return (
                f"{self.top_regime_pnl_share:.0%} of net PnL comes from one regime "
                f"({self.top_regime}){tail}: treat the result as regime-conditional, "
                "not as a general edge"
            )
        return "PnL is spread across regimes; no single-regime artefact detected"

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_trades": self.n_trades,
            "n_regimes_with_trades": self.n_regimes_with_trades,
            "n_regimes_sufficient": self.n_regimes_sufficient,
            "top_regime": self.top_regime,
            "top_regime_pnl_share": self.top_regime_pnl_share,
            "top_regime_trade_share": self.top_regime_trade_share,
            "profitable_without_top": self.profitable_without_top,
            "min_trades": self.min_trades,
            "verdict": self.verdict,
        }


def regime_segmented_performance(
    trades: Sequence[Trade],
    regimes: RegimeSeries,
    *,
    axis: RegimeAxis | str | None = None,
    min_trades: int = 30,
    on: str = "entry_time",
) -> pd.DataFrame:
    """Per-regime trade statistics for one strategy's trades.

    ``axis=None`` groups on the full regime key; passing an axis groups on that
    axis alone, which is usually what you want first — the full key fragments a
    few hundred trades into dozens of one-trade buckets.

    Columns:

    ``n_trades``            trades whose *entry* fell in this regime
    ``net_pnl``             total net PnL, account currency
    ``mean_pnl``            average per trade
    ``median_pnl``          median per trade
    ``win_rate``            fraction of trades with positive net PnL
    ``profit_factor``       gross wins / gross losses (``None`` if no losses)
    ``pnl_std``             standard deviation of trade PnL
    ``pnl_tstat``           ``mean / (std / sqrt(n))`` — the honest small-sample
                            question: is this mean distinguishable from zero?
    ``pnl_share``           this regime's share of total net PnL
    ``trade_share``         this regime's share of trades
    ``bars_share``          share of *market time* spent in this regime
    ``sufficient``          ``n_trades >= min_trades``

    No Sharpe ratio is reported. Sharpe needs an equity curve and a time base;
    computing one from a bag of trade PnLs is exactly the inflation V1 shipped.
    ``pnl_tstat`` answers the question people actually use Sharpe for here, and
    it degrades honestly on small samples.
    """
    seg = regimes.segment_trades(trades, on=on)
    group_col = "regime_key" if axis is None else regime_column(axis)
    empty_cols = [
        "n_trades", "net_pnl", "mean_pnl", "median_pnl", "win_rate",
        "profit_factor", "pnl_std", "pnl_tstat", "pnl_share", "trade_share",
        "bars_share", "sufficient",
    ]
    if seg.empty or group_col not in seg.columns:
        return pd.DataFrame(columns=empty_cols)
    seg = seg[seg[group_col].notna()]
    if seg.empty:
        return pd.DataFrame(columns=empty_cols)

    warm = regimes.frame.iloc[regimes.warmup :]
    bars_col = "regime_key" if axis is None else RegimeAxis(axis).value
    bars_share = warm[bars_col].value_counts(normalize=True) if len(warm) else None

    total_pnl = float(seg["net_pnl"].sum())
    total_trades = int(len(seg))
    rows = []
    for label, grp in seg.groupby(group_col, sort=False):
        pnl = grp["net_pnl"].to_numpy(dtype=float)
        n = pnl.size
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        std = float(np.std(pnl, ddof=1)) if n > 1 else float("nan")
        rows.append(
            {
                group_col: label,
                "n_trades": n,
                "net_pnl": float(pnl.sum()),
                "mean_pnl": float(pnl.mean()),
                "median_pnl": float(np.median(pnl)),
                "win_rate": float((pnl > 0).mean()),
                "profit_factor": _safe_div(float(wins.sum()), float(-losses.sum())),
                "pnl_std": std,
                "pnl_tstat": (
                    float(pnl.mean() / (std / np.sqrt(n)))
                    if n > 1 and std > 0
                    else None
                ),
                "pnl_share": (float(pnl.sum()) / total_pnl) if total_pnl else None,
                "trade_share": n / total_trades,
                "bars_share": (
                    float(bars_share.get(label, 0.0)) if bars_share is not None else None
                ),
                "sufficient": n >= min_trades,
            }
        )
    out = pd.DataFrame(rows).set_index(group_col)
    return out.sort_values("net_pnl", ascending=False)


def regime_dependence(
    trades: Sequence[Trade],
    regimes: RegimeSeries,
    *,
    axis: RegimeAxis | str | None = None,
    min_trades: int = 30,
) -> RegimeDependence:
    """Is this strategy's edge concentrated in one regime?

    This is the V1 audit finding made mechanical: six of fourteen surviving
    results were USDJPY during a single exceptional trend. A result whose PnL
    collapses once its best regime is removed is a statement about that regime,
    and must be reported as one.
    """
    table = regime_segmented_performance(
        trades, regimes, axis=axis, min_trades=min_trades
    )
    if table.empty:
        return RegimeDependence(
            table=table,
            n_trades=0,
            n_regimes_with_trades=0,
            n_regimes_sufficient=0,
            top_regime=None,
            top_regime_pnl_share=None,
            top_regime_trade_share=None,
            profitable_without_top=None,
            min_trades=min_trades,
        )
    total = float(table["net_pnl"].sum())
    top = table["net_pnl"].idxmax()
    top_pnl = float(table.loc[top, "net_pnl"])
    without = total - top_pnl
    return RegimeDependence(
        table=table,
        n_trades=int(table["n_trades"].sum()),
        n_regimes_with_trades=int(len(table)),
        n_regimes_sufficient=int(table["sufficient"].sum()),
        top_regime=str(top),
        top_regime_pnl_share=(top_pnl / total if total else None),
        top_regime_trade_share=float(table.loc[top, "trade_share"]),
        profitable_without_top=bool(without > 0),
        min_trades=min_trades,
    )


def describe_regimes(regimes: RegimeSeries, *, top: int = 12) -> dict[str, Any]:
    """A compact, reportable summary of what a classifier found."""
    warm = regimes.frame.iloc[regimes.warmup :]
    trans = regimes.transitions()
    return {
        "instrument": regimes.instrument,
        "timeframe": regimes.timeframe,
        "fingerprint": regimes.fingerprint,
        "bars_total": int(len(regimes.frame)),
        "bars_classified": int(len(warm)),
        "warmup_bars": regimes.warmup,
        "first_classified": str(warm.index.min()) if len(warm) else None,
        "last_classified": str(warm.index.max()) if len(warm) else None,
        "flap_rate": round(regimes.flap_rate, 6),
        "estimated_flap_rate": round(regimes.estimated_flap_rate, 6),
        "axis_flap_rate": {
            a.value: round(regimes.axis_flap_rate(a), 6) for a in RegimeAxis
        },
        "distinct_keys": int(warm["regime_key"].nunique()) if len(warm) else 0,
        "mean_key_duration_bars": round(regimes.mean_duration(), 3),
        "axis_distribution": {
            a.value: regimes.axis_distribution(a).round(5).to_dict()
            for a in RegimeAxis
        },
        "axis_mean_duration_bars": {
            a.value: round(regimes.mean_duration(a), 3) for a in RegimeAxis
        },
        "top_keys": regimes.key_distribution(top=top).round(5).to_dict(),
        "n_transitions": int(len(trans)),
        "notes": list(regimes.notes),
    }
