"""The continuous market-state engine.

One engine holds several instruments. For each it keeps the bar history it has
been given, the causal feature frame derived from it, and the regime series
derived from that. Two ingestion paths, and they agree:

``ingest_frame``
    Bulk. Give it history, get state for every bar. This is the path research
    and backtesting use.

``ingest_bar``
    One bar at a time, as a paper bot receives them. Recomputes over the
    retained history.

They agree *exactly*, not approximately, and ``tests/integration/
test_marketstate_engine.py`` asserts it bar for bar. That is the whole point:
the regime a paper bot sees at 12:00 on Tuesday must be the regime the backtest
saw at 12:00 on Tuesday, or regime-conditional research is worthless the moment
it leaves the laboratory. The agreement rests on the feature engine's truncation
equivalence (computing on ``df[:k+1]`` equals the first ``k+1`` rows of
computing on ``df``), which is itself a consequence of every feature being
causal.

The cost is honest: ``ingest_bar`` recomputes over the retained window, so
streaming ``n`` bars is O(n·h) where ``h`` is the retained history. On H4 that
is nothing; on M1 it is not, and :attr:`EngineConfig.max_history_bars` bounds
it. **Bounding history changes the answer**: an expanding percentile over a
capped window is a rolling percentile, and once the cap binds, the engine says
so in :attr:`MarketStateSnapshot.history_capped` rather than pretending
otherwise.

A signal is joined to its regime with :meth:`MarketStateEngine.attach_regime`,
which does a backward as-of join onto the *last closed bar* at or before the
signal's timestamp. Never the containing bar, which would not have closed yet.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.contracts import Signal
from fiboki.core.enums import Timeframe
from fiboki.marketstate.calendar import EconomicCalendar, ImpactLevel
from fiboki.marketstate.cross_asset import (
    CrossAssetError,
    CrossAssetState,
    build_cross_asset_state,
)
from fiboki.marketstate.features import (
    OHLC,
    FeatureConfig,
    FeatureEngine,
    FeatureSet,
)
from fiboki.marketstate.regime import (
    REGIME_JOIN_COLUMNS,
    RegimeAxis,
    RegimeClassifier,
    RegimeConfig,
    RegimeSeries,
    RegimeVector,
    describe_regimes,
    regime_column,
)

STATE_ENGINE_VERSION = "1.0.0"


class StateEngineError(RuntimeError):
    """The engine cannot produce state it would stand behind."""


# =====================================================================
# Configuration and snapshots
# =====================================================================


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """How the engine is wired. Hashed into every snapshot's fingerprint."""

    timeframe: Timeframe = Timeframe.H4
    features: FeatureConfig = field(default_factory=FeatureConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    #: Bars retained per instrument. ``None`` retains everything, which keeps
    #: expanding percentiles genuinely expanding. A finite cap turns them into
    #: rolling percentiles over the cap — a different statistic, flagged on the
    #: snapshot when it binds.
    max_history_bars: int | None = None
    #: What to do about sentinel/impossible bars. ``"drop"`` records the drop.
    on_invalid_bars: str = "drop"
    #: Blackout margins used by :meth:`MarketStateEngine.blackout`.
    blackout_minutes_before: int = 60
    blackout_minutes_after: int = 60
    blackout_min_impact: ImpactLevel = ImpactLevel.HIGH

    def __post_init__(self) -> None:
        if self.max_history_bars is not None and self.max_history_bars < 500:
            raise StateEngineError(
                "max_history_bars below 500 cannot support the feature warmups; "
                "either raise it or accept an engine that never warms up"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STATE_ENGINE_VERSION,
            "timeframe": self.timeframe.value,
            "features": self.features.to_dict(),
            "regime": self.regime.to_dict(),
            "max_history_bars": self.max_history_bars,
            "on_invalid_bars": self.on_invalid_bars,
            "blackout_minutes_before": self.blackout_minutes_before,
            "blackout_minutes_after": self.blackout_minutes_after,
            "blackout_min_impact": self.blackout_min_impact.value,
        }


@dataclass(frozen=True, slots=True)
class MarketStateSnapshot:
    """Everything the engine currently believes about one instrument."""

    instrument: str
    timeframe: str
    as_of: pd.Timestamp
    bar: dict[str, float]
    features: dict[str, float]
    regime: RegimeVector
    regime_key: str
    bars_since_regime_change: int | None
    warm: bool
    bars_seen: int
    history_capped: bool
    feature_fingerprint: str
    regime_fingerprint: str
    notes: tuple[str, ...] = ()

    def axis(self, axis: RegimeAxis | str) -> str:
        return self.regime.axis(axis)

    def feature(self, name: str) -> float:
        if name not in self.features:
            raise KeyError(f"no feature {name!r} in this snapshot")
        return self.features[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "as_of": str(self.as_of),
            "regime_key": self.regime_key,
            "regime": self.regime.to_dict(),
            "bars_since_regime_change": self.bars_since_regime_change,
            "warm": self.warm,
            "bars_seen": self.bars_seen,
            "history_capped": self.history_capped,
            "feature_fingerprint": self.feature_fingerprint,
            "regime_fingerprint": self.regime_fingerprint,
            "bar": self.bar,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class _InstrumentState:
    instrument: str
    bars: pd.DataFrame
    features: FeatureSet | None = None
    regimes: RegimeSeries | None = None
    capped: bool = False


# =====================================================================
# Engine
# =====================================================================


class MarketStateEngine:
    """Maintains current market state across instruments, with history."""

    def __init__(
        self,
        *,
        config: EngineConfig | None = None,
        calendar: EconomicCalendar | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.calendar = calendar
        self._states: dict[str, _InstrumentState] = {}
        self._classifier = RegimeClassifier(self.config.regime)
        self._cross: CrossAssetState | None = None

    # ----------------------------------------------------------- admin

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(sorted(self._states))

    def _engine_for(self, instrument: str) -> FeatureEngine:
        return FeatureEngine(
            timeframe=self.config.timeframe,
            config=self.config.features,
            instrument=instrument,
            on_invalid_bars=self.config.on_invalid_bars,
        )

    def _require(self, instrument: str) -> _InstrumentState:
        key = instrument.upper()
        if key not in self._states:
            raise StateEngineError(
                f"no state for {key}; ingest bars for it first "
                f"(known: {list(self.instruments)})"
            )
        return self._states[key]

    # --------------------------------------------------------- ingest

    def ingest_frame(
        self, instrument: str, frame: pd.DataFrame
    ) -> MarketStateSnapshot | None:
        """Ingest a block of bars, replacing any overlap, then recompute.

        Returns ``None`` while too few bars have been seen for the feature
        engine to say anything. Ingestion itself is not an error in that case —
        a paper bot starting from cold feeds bars for hours before the first
        regime exists, and that is the normal path, not a failure. Call
        :meth:`snapshot` to *demand* state and be told why there is none.
        """
        key = instrument.upper()
        incoming = self._clean_incoming(frame)
        state = self._states.get(key)
        if state is None:
            state = _InstrumentState(instrument=key, bars=incoming)
            self._states[key] = state
        else:
            merged = pd.concat([state.bars, incoming])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            state.bars = merged
        self._trim(state)
        self._recompute(state)
        if state.features is None or state.regimes is None:
            return None
        return self.snapshot(key)

    def ingest_bar(
        self, instrument: str, bar: Mapping[str, Any] | pd.Series, *,
        timestamp: pd.Timestamp | None = None,
    ) -> MarketStateSnapshot | None:
        """Ingest one closed bar. ``None`` until there is state worth reporting.

        ``bar`` may be a mapping with OHLC(V) keys plus a ``timestamp``, or a
        Series whose ``name`` is the timestamp. The bar must be strictly after
        the last one held, because a bar that arrives out of order is a data
        problem, not something to sort away silently.
        """
        key = instrument.upper()
        ts, row = self._as_row(bar, timestamp)
        state = self._states.get(key)
        if state is not None and len(state.bars):
            last = state.bars.index[-1]
            if ts <= last:
                raise StateEngineError(
                    f"{key}: bar at {ts} is not after the last held bar {last}. "
                    "Out-of-order bars are a feed problem; the engine will not "
                    "reorder them behind your back."
                )
        frame = pd.DataFrame([row], index=pd.DatetimeIndex([ts], name="timestamp"))
        return self.ingest_frame(key, frame)

    @staticmethod
    def _as_row(
        bar: Mapping[str, Any] | pd.Series, timestamp: pd.Timestamp | None
    ) -> tuple[pd.Timestamp, dict[str, Any]]:
        if isinstance(bar, pd.Series):
            data = bar.to_dict()
            ts = timestamp if timestamp is not None else bar.name
        else:
            data = dict(bar)
            ts = timestamp if timestamp is not None else data.pop("timestamp", None)
        if ts is None:
            raise StateEngineError("ingest_bar needs a timestamp")
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            raise StateEngineError("bar timestamp must be tz-aware UTC")
        missing = [c for c in OHLC if c not in data]
        if missing:
            raise StateEngineError(f"bar is missing {missing}")
        return ts.tz_convert("UTC"), data

    def _clean_incoming(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise StateEngineError("bars need a DatetimeIndex")
        if frame.index.tz is None:
            raise StateEngineError("bar index must be tz-aware UTC")
        out = frame.copy()
        out.index = out.index.tz_convert("UTC")
        # Drop any inferred freq, exactly as fiboki.data.schema.canonical_frame
        # does: a bar series with real gaps has no freq, and carrying one makes
        # the bulk and incremental paths compare unequal for no reason at all.
        out.index.freq = None
        out.index.name = "timestamp"
        missing = [c for c in OHLC if c not in out.columns]
        if missing:
            raise StateEngineError(f"bars missing {missing}")
        if out.index.has_duplicates:
            raise StateEngineError("incoming bars contain duplicate timestamps")
        return out.sort_index()

    def _trim(self, state: _InstrumentState) -> None:
        cap = self.config.max_history_bars
        if cap is None or len(state.bars) <= cap:
            return
        state.bars = state.bars.iloc[-cap:]
        state.capped = True

    def _recompute(self, state: _InstrumentState) -> None:
        engine = self._engine_for(state.instrument)
        if len(state.bars) < 3:
            state.features = None
            state.regimes = None
            return
        features = engine.compute(state.bars)
        state.features = features
        try:
            state.regimes = self._classifier.classify(
                features, stress_inputs=self._stress_inputs_for(state.instrument)
            )
        except Exception as exc:
            state.regimes = None
            if not isinstance(exc, ValueError | KeyError):
                raise

    def _stress_inputs_for(self, instrument: str) -> pd.DataFrame | None:
        if self._cross is None:
            return None
        try:
            return self._cross.stress_inputs
        except Exception:  # pragma: no cover - defensive
            return None

    # ------------------------------------------------------- querying

    def snapshot(self, instrument: str) -> MarketStateSnapshot:
        """Current state for one instrument."""
        state = self._require(instrument)
        if state.features is None or state.regimes is None:
            raise StateEngineError(
                f"{state.instrument}: only {len(state.bars)} bars ingested; the "
                "feature engine has nothing to say yet"
            )
        f = state.features
        r = state.regimes
        pos = len(r.frame) - 1
        warm = pos >= r.warmup
        key = str(r.frame["regime_key"].iloc[pos])
        since: int | None = None
        if warm:
            col = r.frame["regime_key"].to_numpy()
            i = pos
            while i > r.warmup and col[i - 1] == col[i]:
                i -= 1
            since = pos - i
        bar_row = state.bars.iloc[-1]
        bar = {
            c: float(bar_row[c])
            for c in ("open", "high", "low", "close")
            if c in state.bars.columns
        }
        return MarketStateSnapshot(
            instrument=state.instrument,
            timeframe=f.timeframe,
            as_of=state.bars.index[-1],
            bar=bar,
            features=f.latest(),
            regime=RegimeVector.from_key(key),
            regime_key=key,
            bars_since_regime_change=since,
            warm=warm,
            bars_seen=int(len(state.bars)),
            history_capped=state.capped,
            feature_fingerprint=f.fingerprint,
            regime_fingerprint=r.fingerprint,
            notes=tuple([*f.notes, *r.notes]),
        )

    def snapshots(self) -> dict[str, MarketStateSnapshot]:
        """Every instrument that has enough bars to say something."""
        out: dict[str, MarketStateSnapshot] = {}
        for name in self.instruments:
            try:
                out[name] = self.snapshot(name)
            except StateEngineError:
                continue
        return out

    def features(self, instrument: str) -> FeatureSet:
        state = self._require(instrument)
        if state.features is None:
            raise StateEngineError(f"{state.instrument}: no features yet")
        return state.features

    def regimes(self, instrument: str) -> RegimeSeries:
        state = self._require(instrument)
        if state.regimes is None:
            raise StateEngineError(f"{state.instrument}: no regimes yet")
        return state.regimes

    def bars(self, instrument: str) -> pd.DataFrame:
        return self._require(instrument).bars

    def regime_at(
        self, instrument: str, ts: pd.Timestamp
    ) -> RegimeVector | None:
        """The regime current at ``ts``, or ``None`` before the classifier warms."""
        r = self.regimes(instrument)
        warm = r.frame.iloc[r.warmup :]
        if warm.empty:
            return None
        pos = warm.index.searchsorted(pd.Timestamp(ts), side="right") - 1
        if pos < 0:
            return None
        return RegimeVector.from_key(str(warm["regime_key"].iloc[pos]))

    def history(self, instrument: str, *, include_features: bool = True) -> pd.DataFrame:
        """Per-bar regime history, optionally with the features behind it.

        This is what makes a signal joinable to the state it fired in, months
        later, without recomputing anything.
        """
        state = self._require(instrument)
        if state.regimes is None or state.features is None:
            raise StateEngineError(f"{state.instrument}: nothing to persist yet")
        out = state.regimes.frame.copy()
        out.insert(0, "instrument", state.instrument)
        out.insert(1, "timeframe", state.features.timeframe)
        out["warm"] = np.arange(len(out)) >= state.regimes.warmup
        if include_features:
            out = out.join(state.features.frame, how="left", rsuffix="_feat")
        return out

    # -------------------------------------------------- joins to alpha

    def attach_regime(
        self,
        signals: Sequence[Signal] | pd.DataFrame,
        *,
        instrument_column: str = "instrument",
        time_column: str = "bar_time",
    ) -> pd.DataFrame:
        """Join signals to the regime that was current when each one fired.

        Accepts a sequence of :class:`~fiboki.core.contracts.Signal` or a frame
        with instrument/time columns. The join is backward as-of onto the last
        *warm* classified bar at or before the signal time, per instrument.
        Signals fired before their instrument's classifier was warm come back
        with ``NaN`` regime columns, deliberately: an unclassified signal must
        be excluded from regime statistics, not filed under "unknown" where it
        will masquerade as a regime of its own.
        """
        if isinstance(signals, pd.DataFrame):
            left = signals.copy()
            if instrument_column not in left or time_column not in left:
                raise StateEngineError(
                    f"signal frame needs {instrument_column!r} and {time_column!r}"
                )
        else:
            if not signals:
                return pd.DataFrame(
                    columns=[
                        "signal_id", instrument_column, time_column,
                        "strategy_id", "direction", *REGIME_JOIN_COLUMNS,
                    ]
                )
            left = pd.DataFrame(
                {
                    "signal_id": [s.signal_id for s in signals],
                    instrument_column: [s.instrument for s in signals],
                    time_column: [pd.Timestamp(s.bar_time) for s in signals],
                    "strategy_id": [s.strategy_id for s in signals],
                    "direction": [s.direction.value for s in signals],
                }
            )
        left[instrument_column] = left[instrument_column].astype(str).str.upper()
        cols = list(REGIME_JOIN_COLUMNS)
        pieces: list[pd.DataFrame] = []
        for inst, grp in left.groupby(instrument_column, sort=False):
            grp = grp.sort_values(time_column)
            try:
                r = self.regimes(inst)
            except StateEngineError:
                for c in cols:
                    grp[c] = np.nan
                pieces.append(grp)
                continue
            warm = r.frame.iloc[r.warmup :][
                ["regime_key", *[a.value for a in RegimeAxis]]
            ].copy()
            warm = warm.rename(
                columns={a.value: regime_column(a) for a in RegimeAxis}
            )
            warm["_regime_time"] = warm.index
            merged = pd.merge_asof(
                grp.reset_index(drop=True),
                warm.reset_index(drop=True).sort_values("_regime_time"),
                left_on=time_column,
                right_on="_regime_time",
                direction="backward",
            )
            pieces.append(merged.drop(columns=["_regime_time"]))
        out = pd.concat(pieces, ignore_index=True)
        return out.sort_values(time_column).reset_index(drop=True)

    # ------------------------------------------------------ calendar

    def blackout(self, instrument: str, ts: pd.Timestamp) -> bool:
        """Is this instrument inside an event blackout right now?

        Returns ``False`` when no calendar is attached — and that is exactly the
        dangerous default the calendar module warns about, so a pipeline that
        must not trade blind should call
        :meth:`~fiboki.marketstate.calendar.EconomicCalendar.assert_populated`
        at startup rather than relying on this.
        """
        if self.calendar is None:
            return False
        return self.calendar.in_blackout(
            instrument,
            ts,
            minutes_before=self.config.blackout_minutes_before,
            minutes_after=self.config.blackout_minutes_after,
            min_impact=self.config.blackout_min_impact,
        )

    # ---------------------------------------------------- cross asset

    def update_cross_asset(self, **kwargs: Any) -> CrossAssetState | None:
        """Rebuild the cross-asset view from the bars currently held.

        Needs at least two instruments with overlapping history. Returns
        ``None`` (and records nothing) when the panel cannot be built, rather
        than fabricating a neutral correlation.
        """
        frames = {
            name: st.bars
            for name, st in self._states.items()
            if len(st.bars) > 2
        }
        if len(frames) < 2:
            self._cross = None
            return None
        try:
            self._cross = build_cross_asset_state(frames, **kwargs)
        except CrossAssetError:
            self._cross = None
            return None
        return self._cross

    @property
    def cross_asset(self) -> CrossAssetState | None:
        return self._cross

    def reclassify_all(self) -> None:
        """Recompute features and regimes for every instrument held.

        Needed after :meth:`update_cross_asset`, because the stress axis can
        consume cross-asset inputs that did not exist when the instruments were
        first ingested.
        """
        for name in list(self._states):
            self._recompute(self._states[name])

    # ----------------------------------------------------- persistence

    def persist(self, root: str | Path, *, include_features: bool = True) -> Path:
        """Write per-instrument regime history plus the engine manifest.

        Layout::

            <root>/manifest.json
            <root>/<INSTRUMENT>.parquet

        The manifest records the engine config and both fingerprints, so a
        stored regime label can always be traced to the code and parameters that
        produced it. That is the property V1 could not provide for any number it
        ever displayed.
        """
        out = Path(root)
        out.mkdir(parents=True, exist_ok=True)
        written: dict[str, Any] = {}
        for name in self.instruments:
            state = self._states[name]
            if state.regimes is None or state.features is None:
                continue
            frame = self.history(name, include_features=include_features)
            # Parquet cannot hold an object column of mixed types; the regime
            # labels are strings by construction, so state that explicitly.
            for col in frame.columns:
                if frame[col].dtype == object:
                    frame[col] = frame[col].astype("string")
            path = out / f"{name}.parquet"
            frame.to_parquet(path)
            written[name] = {
                "path": path.name,
                "rows": int(len(frame)),
                "warmup": state.regimes.warmup,
                "first": str(frame.index.min()),
                "last": str(frame.index.max()),
                "feature_fingerprint": state.features.fingerprint,
                "regime_fingerprint": state.regimes.fingerprint,
                "dropped_bars": state.features.dropped_bars.to_dict(),
                "history_capped": state.capped,
            }
        manifest = {
            "version": STATE_ENGINE_VERSION,
            "config": self.config.to_dict(),
            "instruments": written,
        }
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        return out

    @staticmethod
    def load_history(root: str | Path, instrument: str) -> pd.DataFrame:
        """Read back a persisted regime history."""
        path = Path(root) / f"{instrument.upper()}.parquet"
        if not path.exists():
            raise StateEngineError(f"no persisted history at {path}")
        return pd.read_parquet(path)

    @staticmethod
    def load_manifest(root: str | Path) -> dict[str, Any]:
        path = Path(root) / "manifest.json"
        if not path.exists():
            raise StateEngineError(f"no manifest at {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # --------------------------------------------------------- report

    def describe(self, instrument: str, *, top: int = 12) -> dict[str, Any]:
        """A reportable summary of one instrument's regime history."""
        r = self.regimes(instrument)
        f = self.features(instrument)
        out = describe_regimes(r, top=top)
        out["feature_fingerprint"] = f.fingerprint
        out["feature_warmup"] = f.warmup
        out["dropped_bars"] = f.dropped_bars.to_dict()
        out["feature_notes"] = list(f.notes)
        out["bars_ingested"] = int(len(self.bars(instrument)))
        out["history_capped"] = self._states[instrument.upper()].capped
        return out


def build_engine(
    frames: Mapping[str, pd.DataFrame],
    *,
    config: EngineConfig | None = None,
    calendar: EconomicCalendar | None = None,
    with_cross_asset: bool = True,
) -> MarketStateEngine:
    """Convenience: build an engine, load history, wire cross-asset stress.

    When ``with_cross_asset`` is set and the panel can be built, the engine is
    populated twice — once to get bars in, once so the regime classifier can see
    the cross-asset stress inputs. The second pass is what makes correlation
    convergence visible on the stress axis.
    """
    engine = MarketStateEngine(config=config, calendar=calendar)
    for name, frame in frames.items():
        engine.ingest_frame(name, frame)
    if (
        with_cross_asset
        and len(frames) > 1
        and engine.update_cross_asset() is not None
    ):
        engine.reclassify_all()
    return engine
