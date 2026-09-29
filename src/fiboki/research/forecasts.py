"""Pre-registered forecasts and the deterministic scorer that marks them.

Why this exists
---------------
Before an agent's output is trusted anywhere, Fiboki needs to know whether its
calls are worth anything. A forecast here is a claim written down BEFORE its
horizon, in a closed vocabulary that cannot read as an order (where price will
be relative to where it was, with a probability), and scored AFTER the horizon
by this module, which consults no model. The idea of recording agent
predictions comes from AI-Trader's ``signal_predictions`` table (no licence
file, so nothing is copied); that table is never scored, which is the part
that matters and the part this module adds.

What a forecast claims
----------------------
Three mutually exclusive realised states, measured in ATR units::

    m = (close at horizon_end - close at horizon_start) / ATR(horizon_start)

    higher  if m >=  band
    lower   if m <= -band
    range   otherwise                (band = RANGE_BAND_ATR = 0.5)

A forecast names one state and the probability it assigns to it (0.5 to 0.95;
below 0.5 is a claim about a different state). The optional magnitude bucket
names where ``|m|`` lands: ``<0.5atr``, ``0.5-1atr``, ``1-2atr``, ``>2atr``.
The range band IS the first bucket edge, so a directional claim with a
``<0.5atr`` bucket is incoherent and refused at write time.

Scoring
-------
``outcome`` is ``hit`` (the claimed state occurred), ``miss`` (another state
occurred), ``range`` (a DIRECTIONAL claim that landed inside the band) or
``not_evaluable`` (the bars needed to decide are missing). ``range`` is scored
as the claimed event NOT occurring, exactly like a miss, and counts against
the hit rate. It is kept as a separate label only so a reader can see how much
of a forecaster's error is "called a move that never came". Treating it as a
push would let a forecaster that calls a direction every quiet day escape
scoring entirely.

Per forecast, with ``o = 1`` for a hit and ``0`` otherwise:

* Brier contribution ``(p - o)^2``: lower is better; always saying 0.5 scores
  0.25, which is the reference a forecaster must beat.
* Log score ``ln(p)`` if hit, ``ln(1 - p)`` otherwise: higher (closer to 0) is
  better. Finite because ``p <= 0.95``.

Price and ATR are read from CLOSED bars only (bars are left-labelled: a bar
stamped ``t`` closes at ``t + timeframe``). The start price is the close of the
last bar closed at or before ``horizon_start``; the end price is the close of
the last bar closed at or before ``horizon_end``; ATR is Wilder ATR from the
centralised :class:`fiboki.indicators.ATR`, computed over bars closed at or
before ``horizon_start`` only.

Approximations, named
---------------------
* ATR is seeded at the start of a fixed look-back window of
  ``atr_lookback_bars`` bars before ``horizon_start``, not at the start of the
  dataset. It is deterministic given the data, and with 150 bars the residual
  weight of the Wilder seed is about ``(13/14)^135 < 1e-4``, but it is not
  bit-identical to a full-history ATR.
* Only the two endpoints are read. A move that crossed the band and came back
  inside the horizon scores as ``range``: the claim is about the endpoint, and
  the tool description says so.
* An endpoint bar that closed more than ``max_endpoint_staleness`` before the
  instant it stands for is treated as missing data (``not_evaluable``). A
  weekend is inside that tolerance; a long holiday closure may not be.
* A ``not_evaluable`` score is final (the store is append-only). Run the scorer
  with a ``settlement_lag`` at least as long as data ingestion takes, or a
  forecast whose bars simply had not arrived yet is lost to scoring. It is
  still counted as NOT_EVALUABLE, never silently dropped.

Where it sits
-------------
``research`` sits below ``agents`` (``tests/unit/test_layering.py``), so this
module cannot import the agents' ``BarSource``; :class:`ForecastBarSource` is
the same structural protocol, and every agent bar source satisfies it.
Nothing in this module is wired to a schedule yet; see the build log.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict

from fiboki.core.enums import Timeframe
from fiboki.data.store import DataRootNotFound, DatasetNotFound
from fiboki.data.versioning import VersionNotFound
from fiboki.indicators import ATR
from fiboki.research.artefacts import (
    Forecast,
    ForecastScore,
    ResearchStore,
)

#: Changing anything that alters a score changes this string. Stamped on every
#: :class:`ForecastScore`, so a score says which rules produced it.
SCORER_VERSION = "forecast_scorer:1.0.0"

#: The actor recorded on every score. Scores are written by this module, never
#: by an agent: there is no agent tool that writes one.
SCORER_ACTOR = "forecast_scorer"

#: Bucket edges in ATR units; also the definition of the range band.
MAGNITUDE_EDGES_ATR: tuple[float, float, float] = (0.5, 1.0, 2.0)
MAGNITUDE_BUCKETS: tuple[str, str, str, str] = ("<0.5atr", "0.5-1atr", "1-2atr", ">2atr")
RANGE_BAND_ATR: float = MAGNITUDE_EDGES_ATR[0]

#: Calibration bins over the permitted probability range. The last is closed.
CALIBRATION_EDGES: tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)


@dataclass(frozen=True, slots=True)
class ForecastPolicy:
    """Every constant a forecast is written or scored under. Versioned.

    Lives in ``research`` rather than in the agent package, so the agent layer
    reads it and cannot redefine it.
    """

    version: str = "forecast_policy:1.0.0"
    max_horizon: timedelta = timedelta(days=30)
    min_probability: float = 0.5
    max_probability: float = 0.95
    allowed_timeframes: tuple[str, ...] = ("H1", "H4", "D1")
    atr_period: int = 14
    atr_lookback_bars: int = 150
    range_band_atr: float = RANGE_BAND_ATR
    max_endpoint_staleness: timedelta = timedelta(hours=72)
    #: A forecast recorded more than this long after its horizon started is a
    #: backfill: the model may have seen the answer (weights or context), so it
    #: is kept apart from forward evidence (plan decision D-A3).
    forward_tolerance: timedelta = timedelta(hours=1)
    #: Below this many evaluable forecasts an aggregate is anecdote, and says so.
    min_n_for_comparison: int = 30


FORECAST_POLICY = ForecastPolicy()


@runtime_checkable
class ForecastBarSource(Protocol):
    """Read-only bars. Structurally identical to ``fiboki.agents.tools.BarSource``."""

    def load(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> tuple[pd.DataFrame, str]:
        """Return ``(frame, dataset_version_id)``. Absence must raise."""


#: Exceptions that mean "the bars are not there", which is NOT_EVALUABLE.
#: Anything else (a corrupt store, a bug in this module) propagates: an
#: incident must not be recorded as a forecast outcome.
_ABSENCE: tuple[type[BaseException], ...] = (
    KeyError,
    LookupError,
    FileNotFoundError,
    DatasetNotFound,
    DataRootNotFound,
    VersionNotFound,
)


# ---------------------------------------------------------------------------
# Claim vocabulary
# ---------------------------------------------------------------------------


def magnitude_bucket(abs_move_atr: float) -> str:
    """The bucket an absolute move in ATR units falls in (lower edge inclusive)."""
    if abs_move_atr < MAGNITUDE_EDGES_ATR[0]:
        return MAGNITUDE_BUCKETS[0]
    if abs_move_atr < MAGNITUDE_EDGES_ATR[1]:
        return MAGNITUDE_BUCKETS[1]
    if abs_move_atr < MAGNITUDE_EDGES_ATR[2]:
        return MAGNITUDE_BUCKETS[2]
    return MAGNITUDE_BUCKETS[3]


def realised_direction(move_atr: float, band: float) -> str:
    if move_atr >= band:
        return "higher"
    if move_atr <= -band:
        return "lower"
    return "range"


def claim_incoherence(direction: str, bucket: str | None) -> str | None:
    """Why a direction and magnitude bucket cannot both be true, or None.

    ``range`` means ``|m| < 0.5``, which is exactly the ``<0.5atr`` bucket; a
    directional claim means ``|m| >= 0.5``, which excludes it.
    """
    if bucket is None:
        return None
    if direction == "range" and bucket != MAGNITUDE_BUCKETS[0]:
        return (
            f"direction 'range' means |move| < {RANGE_BAND_ATR} ATR, so only the "
            f"{MAGNITUDE_BUCKETS[0]!r} bucket is coherent with it, not {bucket!r}"
        )
    if direction != "range" and bucket == MAGNITUDE_BUCKETS[0]:
        return (
            f"direction {direction!r} means |move| >= {RANGE_BAND_ATR} ATR, which "
            f"excludes the {bucket!r} bucket; a move that small is 'range'"
        )
    return None


def classify_provenance(
    recorded_at: datetime,
    horizon_start: datetime,
    policy: ForecastPolicy = FORECAST_POLICY,
) -> Literal["forward", "backfill"]:
    """``forward`` if written no later than ``forward_tolerance`` after start."""
    return "forward" if recorded_at - horizon_start <= policy.forward_tolerance else "backfill"


# ---------------------------------------------------------------------------
# Scoring one forecast
# ---------------------------------------------------------------------------


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{value!r} is timezone-naive; the scorer's clock must be UTC-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def evaluate_forecast(
    forecast: Forecast,
    bars: ForecastBarSource,
    *,
    scored_at: datetime,
    policy: ForecastPolicy = FORECAST_POLICY,
) -> ForecastScore:
    """Score one forecast from closed bars. Pure given the bars; writes nothing."""
    base: dict[str, object] = {
        "score_id": ForecastScore.id_for(forecast.forecast_id),
        "forecast_id": forecast.forecast_id,
        "instrument": forecast.instrument,
        "timeframe": forecast.timeframe,
        "forecaster": forecast.created_by,
        "forecaster_role": forecast.role,
        "model_id": forecast.model_id,
        "provenance": forecast.provenance,
        "claimed_direction": forecast.direction,
        "claimed_magnitude_bucket": forecast.magnitude_bucket,
        "probability": forecast.probability,
        "scorer_version": SCORER_VERSION,
        "created_at": _utc(scored_at),
        "created_by": SCORER_ACTOR,
        "role": SCORER_ACTOR,
    }

    def not_evaluable(reason: str, **extra: object) -> ForecastScore:
        return ForecastScore.model_validate(
            {**base, **extra, "outcome": "not_evaluable", "not_evaluable_reason": reason}
        )

    tf = Timeframe(forecast.timeframe)
    bar = timedelta(minutes=tf.minutes)
    start = _utc(forecast.horizon_start)
    end = _utc(forecast.horizon_end)
    # Calendar span generous enough to hold atr_lookback_bars trading bars
    # across weekends and holidays.
    lookback = bar * policy.atr_lookback_bars * 1.5 + timedelta(days=7)
    try:
        frame, version_id = bars.load(
            forecast.instrument, tf, start=_iso(start - lookback), end=_iso(end)
        )
    except _ABSENCE as exc:
        return not_evaluable(
            f"bars unavailable for {forecast.instrument} {tf.value}: "
            f"{type(exc).__name__}: {exc}"
        )
    if frame is None or len(frame) == 0:
        return not_evaluable(
            f"no {tf.value} bars for {forecast.instrument} between "
            f"{_iso(start - lookback)} and {_iso(end)}",
            dataset_version_id=str(version_id or ""),
        )
    version = str(version_id or "")
    missing_cols = [c for c in ("open", "high", "low", "close") if c not in frame.columns]
    if missing_cols:
        return not_evaluable(f"bars lack columns {missing_cols}", dataset_version_id=version)
    index = pd.DatetimeIndex(frame.index)
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    if not (index.is_monotonic_increasing and index.is_unique):
        return not_evaluable(
            "bar index is not strictly increasing; refusing to guess which bar is which",
            dataset_version_id=version,
        )
    frame = frame.set_axis(index, axis=0)

    closes_at = index + bar
    closed_mask = closes_at <= pd.Timestamp(end)
    closed = frame.loc[closed_mask]
    closed_closes_at = closes_at[closed_mask]
    at_start = closed_closes_at <= pd.Timestamp(start)
    if not at_start.any():
        return not_evaluable(
            f"no {tf.value} bar closed at or before horizon_start {_iso(start)}",
            dataset_version_id=version,
        )
    start_pos = int(at_start.nonzero()[0][-1])
    start_closed_at = closed_closes_at[start_pos].to_pydatetime()
    if start - start_closed_at > policy.max_endpoint_staleness:
        return not_evaluable(
            f"the last bar before horizon_start closed at {_iso(start_closed_at)}, "
            f"more than {policy.max_endpoint_staleness} earlier: missing data, not a price",
            dataset_version_id=version,
        )
    history = closed.iloc[: start_pos + 1]
    atr_column = ATR(forecast.atr_period)
    atr_value = float(atr_column.compute(history)[atr_column.name].iloc[-1])
    if not math.isfinite(atr_value) or atr_value <= 0.0:
        return not_evaluable(
            f"ATR({forecast.atr_period}) at horizon_start is not available "
            f"({len(history)} closed bars of history; value {atr_value!r})",
            dataset_version_id=version,
            start_bar_closed_at=start_closed_at,
        )
    end_pos = len(closed) - 1
    if end_pos <= start_pos:
        return not_evaluable(
            f"no {tf.value} bar closed inside the horizon "
            f"({_iso(start)} to {_iso(end)})",
            dataset_version_id=version,
            atr_at_start=atr_value,
            start_bar_closed_at=start_closed_at,
        )
    end_closed_at = closed_closes_at[end_pos].to_pydatetime()
    if end - end_closed_at > policy.max_endpoint_staleness:
        return not_evaluable(
            f"the last bar before horizon_end closed at {_iso(end_closed_at)}, more "
            f"than {policy.max_endpoint_staleness} earlier: missing data, not a price",
            dataset_version_id=version,
            atr_at_start=atr_value,
            start_bar_closed_at=start_closed_at,
            end_bar_closed_at=end_closed_at,
        )
    p0 = float(closed["close"].iloc[start_pos])
    p1 = float(closed["close"].iloc[end_pos])
    if not (math.isfinite(p0) and math.isfinite(p1)):
        return not_evaluable(
            f"non-finite close at an endpoint (start {p0!r}, end {p1!r})",
            dataset_version_id=version,
        )

    move = (p1 - p0) / atr_value
    realised = realised_direction(move, forecast.range_band_atr)
    bucket = magnitude_bucket(abs(move))
    if realised == forecast.direction:
        outcome = "hit"
    elif forecast.direction != "range" and realised == "range":
        outcome = "range"
    else:
        outcome = "miss"
    o = 1.0 if outcome == "hit" else 0.0
    p = forecast.probability
    return ForecastScore.model_validate(
        {
            **base,
            "outcome": outcome,
            "realised_direction": realised,
            "realised_move_atr": move,
            "realised_magnitude_bucket": bucket,
            "magnitude_hit": (
                None if forecast.magnitude_bucket is None else bucket == forecast.magnitude_bucket
            ),
            "atr_at_start": atr_value,
            "start_price": p0,
            "end_price": p1,
            "start_bar_closed_at": start_closed_at,
            "end_bar_closed_at": end_closed_at,
            "dataset_version_id": version,
            "brier": (p - o) ** 2,
            "log_score": math.log(p) if o == 1.0 else math.log(1.0 - p),
        }
    )


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CalibrationBucket(_Frozen):
    """Stated probability against observed frequency, for one probability bin.

    Empty bins are reported with ``n = 0`` and ``None`` averages: an absent
    bin is not a perfectly calibrated one.
    """

    lower: float
    upper: float
    n: int
    mean_probability: float | None
    observed_frequency: float | None


class ForecastAggregate(_Frozen):
    """One group's scorecard. ``None`` means "no evaluable forecasts", not zero."""

    group_kind: Literal["role", "model", "actor"]
    group: str
    provenance: Literal["forward", "backfill", "all"]
    n_scored: int
    n_evaluable: int
    n_not_evaluable: int
    n_hit: int
    n_miss: int
    n_range: int
    hit_rate: float | None
    brier: float | None
    log_score: float | None
    n_with_magnitude: int
    magnitude_hit_rate: float | None
    calibration: tuple[CalibrationBucket, ...]
    #: False below ``min_n_for_comparison`` evaluable forecasts.
    sufficient: bool


UNRECORDED = "(unrecorded)"


def _group_key(score: ForecastScore, group_by: str) -> str:
    value = {
        "role": score.forecaster_role,
        "model": score.model_id,
        "actor": score.forecaster,
    }[group_by]
    return value or UNRECORDED


def _calibration(scores: Sequence[ForecastScore]) -> tuple[CalibrationBucket, ...]:
    edges = CALIBRATION_EDGES
    out: list[CalibrationBucket] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        inside = [
            s for s in scores if lo <= s.probability < hi or (last and s.probability == hi)
        ]
        n = len(inside)
        out.append(
            CalibrationBucket(
                lower=lo,
                upper=hi,
                n=n,
                mean_probability=(sum(s.probability for s in inside) / n) if n else None,
                observed_frequency=(
                    sum(1 for s in inside if s.outcome == "hit") / n if n else None
                ),
            )
        )
    return tuple(out)


def aggregate_scores(
    scores: Iterable[ForecastScore],
    *,
    group_by: Literal["role", "model", "actor"],
    provenance: Literal["forward", "backfill", "all"] = "forward",
    policy: ForecastPolicy = FORECAST_POLICY,
) -> tuple[ForecastAggregate, ...]:
    """Per-group scorecards, sorted by group name. Deterministic.

    ``provenance="forward"`` is the default because only a forecast written
    before its horizon is evidence about the forecaster; backfills are kept
    apart and are never merged into the forward figures silently.
    """
    chosen = sorted(
        (s for s in scores if provenance == "all" or s.provenance == provenance),
        key=lambda s: s.forecast_id,
    )
    groups: dict[str, list[ForecastScore]] = defaultdict(list)
    for s in chosen:
        groups[_group_key(s, group_by)].append(s)
    rows: list[ForecastAggregate] = []
    for name in sorted(groups):
        members = groups[name]
        evaluable = [s for s in members if s.outcome != "not_evaluable"]
        n_eval = len(evaluable)
        with_mag = [s for s in evaluable if s.magnitude_hit is not None]
        n_hit = sum(1 for s in evaluable if s.outcome == "hit")
        rows.append(
            ForecastAggregate(
                group_kind=group_by,
                group=name,
                provenance=provenance,
                n_scored=len(members),
                n_evaluable=n_eval,
                n_not_evaluable=len(members) - n_eval,
                n_hit=n_hit,
                n_miss=sum(1 for s in evaluable if s.outcome == "miss"),
                n_range=sum(1 for s in evaluable if s.outcome == "range"),
                hit_rate=(n_hit / n_eval) if n_eval else None,
                brier=(sum(s.brier or 0.0 for s in evaluable) / n_eval) if n_eval else None,
                log_score=(
                    sum(s.log_score or 0.0 for s in evaluable) / n_eval if n_eval else None
                ),
                n_with_magnitude=len(with_mag),
                magnitude_hit_rate=(
                    sum(1 for s in with_mag if s.magnitude_hit) / len(with_mag)
                    if with_mag
                    else None
                ),
                calibration=_calibration(evaluable),
                sufficient=n_eval >= policy.min_n_for_comparison,
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# The honest trial count
# ---------------------------------------------------------------------------


def n_forecasts_by_actor(
    store: ResearchStore, *, as_of: datetime | None = None
) -> dict[str, int]:
    """Every forecast each agent has made, scored or not, evaluable or not.

    Each forecast is a trial. An agent that files two hundred directional calls
    and a hypothesis built on the ten that came good has searched two hundred
    times, and the statistical auditor must add that search to
    ``LadderConfig.external_trial_count`` when a forecast-derived idea reaches
    the validation ladder. Counting only scored, or only evaluable, forecasts
    would understate the search, which is the flattering direction, so the
    count here includes everything that was filed.

    ``as_of`` restricts to forecasts whose horizon had started by then.
    """
    return _count_forecasts(store, key="actor", as_of=as_of)


def n_forecasts_by_role(
    store: ResearchStore, *, as_of: datetime | None = None
) -> dict[str, int]:
    """As :func:`n_forecasts_by_actor`, keyed by role."""
    return _count_forecasts(store, key="role", as_of=as_of)


def _count_forecasts(
    store: ResearchStore, *, key: str, as_of: datetime | None
) -> dict[str, int]:
    pin = _utc(as_of) if as_of is not None else None
    counts: Counter[str] = Counter()
    for f in store.forecasts():
        if pin is not None and f.horizon_start > pin:
            continue
        counts[(f.created_by if key == "actor" else f.role) or UNRECORDED] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# The scoring run
# ---------------------------------------------------------------------------


class ScoringReport(_Frozen):
    """What one scoring run did, plus the cumulative scorecards after it."""

    now: datetime
    scorer_version: str
    policy_version: str
    n_forecasts: int
    #: Horizon (plus settlement lag) not yet over.
    n_not_yet_due: int
    n_due: int
    n_already_scored: int
    n_scored_now: int
    n_not_evaluable_now: int
    scored_now: tuple[str, ...]
    by_role: tuple[ForecastAggregate, ...]
    by_model: tuple[ForecastAggregate, ...]
    n_forecasts_by_actor: dict[str, int]


def score_due_forecasts(
    store: ResearchStore,
    bars: ForecastBarSource,
    now: datetime,
    *,
    settlement_lag: timedelta = timedelta(0),
    policy: ForecastPolicy = FORECAST_POLICY,
) -> ScoringReport:
    """Score every forecast whose horizon is over and which has no score yet.

    Idempotent: a forecast already scored is skipped, and the derived
    ``score_id`` makes a second score for it a primary-key violation even if
    two runs race. Deterministic: forecasts are scored in
    ``(horizon_end, forecast_id)`` order, each score is stamped with ``now``
    (not the wall clock), and aggregates are computed in a fixed order.

    A forecast whose bars are missing is written as ``not_evaluable`` with the
    reason; it is never skipped.
    """
    now = _utc(now)
    if settlement_lag < timedelta(0):
        raise ValueError("settlement_lag cannot be negative")
    forecasts = sorted(store.forecasts(), key=lambda f: (f.horizon_end, f.forecast_id))
    scored_ids = {s.forecast_id for s in store.forecast_scores()}
    due = [f for f in forecasts if f.horizon_end + settlement_lag <= now]
    already = [f for f in due if f.forecast_id in scored_ids]
    written: list[ForecastScore] = []
    for forecast in due:
        if forecast.forecast_id in scored_ids:
            continue
        score = evaluate_forecast(forecast, bars, scored_at=now, policy=policy)
        try:
            store.add_forecast_score(score)
        except ValueError:
            # Another scorer filed it between our read and our write. The
            # primary key held; this run simply did not write it.
            already.append(forecast)
            continue
        written.append(score)
    all_scores = store.forecast_scores()
    return ScoringReport(
        now=now,
        scorer_version=SCORER_VERSION,
        policy_version=policy.version,
        n_forecasts=len(forecasts),
        n_not_yet_due=len(forecasts) - len(due),
        n_due=len(due),
        n_already_scored=len(already),
        n_scored_now=len(written),
        n_not_evaluable_now=sum(1 for s in written if s.outcome == "not_evaluable"),
        scored_now=tuple(s.score_id for s in written),
        by_role=aggregate_scores(all_scores, group_by="role", provenance="forward", policy=policy)
        + aggregate_scores(all_scores, group_by="role", provenance="backfill", policy=policy),
        by_model=aggregate_scores(all_scores, group_by="model", provenance="forward", policy=policy)
        + aggregate_scores(all_scores, group_by="model", provenance="backfill", policy=policy),
        n_forecasts_by_actor=n_forecasts_by_actor(store),
    )


# ---------------------------------------------------------------------------
# The read side used by the agent tool
# ---------------------------------------------------------------------------


class Scoreboard(_Frozen):
    aggregates: tuple[ForecastAggregate, ...]
    n_forecasts: int
    n_scored: int
    n_awaiting_score: int
    n_forecasts_by_actor: dict[str, int]
    n_forecasts_by_role: dict[str, int]


def scoreboard(
    store: ResearchStore,
    *,
    group_by: Literal["role", "model", "actor"],
    provenance: Literal["forward", "backfill", "all"] = "forward",
    as_of: datetime | None = None,
    policy: ForecastPolicy = FORECAST_POLICY,
) -> Scoreboard:
    """Aggregates as they could have been known at ``as_of``.

    Pinned, a forecast counts only if its horizon had started by ``as_of`` and
    a score counts only if the forecast's horizon had ENDED by then, so a
    reader pinned in the past cannot see an outcome that had not happened yet.
    """
    pin = _utc(as_of) if as_of is not None else None
    forecasts = {
        f.forecast_id: f
        for f in store.forecasts()
        if pin is None or f.horizon_start <= pin
    }
    scores = [
        s
        for s in store.forecast_scores()
        if s.forecast_id in forecasts
        and (pin is None or forecasts[s.forecast_id].horizon_end <= pin)
    ]
    return Scoreboard(
        aggregates=aggregate_scores(
            scores, group_by=group_by, provenance=provenance, policy=policy
        ),
        n_forecasts=len(forecasts),
        n_scored=len(scores),
        n_awaiting_score=len(forecasts) - len(scores),
        n_forecasts_by_actor=n_forecasts_by_actor(store, as_of=pin),
        n_forecasts_by_role=n_forecasts_by_role(store, as_of=pin),
    )


__all__ = [
    "CALIBRATION_EDGES",
    "FORECAST_POLICY",
    "MAGNITUDE_BUCKETS",
    "MAGNITUDE_EDGES_ATR",
    "RANGE_BAND_ATR",
    "SCORER_ACTOR",
    "SCORER_VERSION",
    "UNRECORDED",
    "CalibrationBucket",
    "ForecastAggregate",
    "ForecastBarSource",
    "ForecastPolicy",
    "Scoreboard",
    "ScoringReport",
    "aggregate_scores",
    "claim_incoherence",
    "classify_provenance",
    "evaluate_forecast",
    "magnitude_bucket",
    "n_forecasts_by_actor",
    "n_forecasts_by_role",
    "realised_direction",
    "score_due_forecasts",
    "scoreboard",
]
