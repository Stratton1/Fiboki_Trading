"""Run a real Phase K discovery campaign against the real engine on real bars.

Usage::

    python research/run_discovery_campaign.py \\
        --data-root /path/to/a/fiboki/data/root \\
        --out research/reports/campaign_k1 \\
        --cache /tmp/ladder-cache \\
        --gates production

What this script is
-------------------
The end-to-end proof that the discovery loop runs on real data: seeds are read
from ``research/strategies``, hypotheses from ``research/hypotheses``, mutations
are proposed and validated, novelty is checked against the experiment ledger
BEFORE compute is spent, the true campaign-wide trial count is fixed before the
first evaluation, and every cell goes through the full validation ladder with
that count supplied as ``external_trial_count``.

What it is NOT
--------------
It is not a search for something that passes. Nothing here is tuned to produce a
survivor, and the expected outcome on a single instrument and a single timeframe
is that nothing does. A campaign that honestly rejects everything is a
successful campaign.

Two gate sets, and the difference matters
-----------------------------------------
``--gates production`` uses ``GATE_SET_V2``, whose ``min_trades`` is 400.
``--gates diagnostic`` mints a NEW, explicitly named gate set with a lower
``min_trades`` so that the rungs after 0 execute on real data at all. Its
reports carry that version string, so nothing produced under it can ever be
mistaken for a promotion decision. No other threshold is moved, ever.

Account currency and FX (engine_v3_realism)
-------------------------------------------
``--account-ccy GBP`` (the default) is the operator's account currency and
``run_validation``'s own default. Quote currencies are converted by
:func:`fiboki.validation.run.build_research_fx_source`: daily rates from the
store's validated D1 GBP crosses, or, where a cross has no D1, from its H4 (else
H1) bars reduced to the last close of each UTC day and stamped at that bar's
close; where the direct cross has no fresh rate (it starts later than the
instrument), the product of the two USD legs, recorded as ``via_usd``. A cross
missing at every timeframe REFUSES the run up front with the list of pairs to
ingest. ``fx_coverage.json`` records each pair's derivation and, per series,
how many bars (at bar open) convert directly, via USD, or not at all. ``--account-ccy USD`` keeps the K1/K2 conversion (the
script's own ``FX_SERIES_FOR`` table over H4 bid closes) so an earlier campaign
can be reproduced; it is not the research default.

Economic calendar
-----------------
``--calendar official`` (the default) runs every cell against the committed
official calendar under the policy "enforce where covered, record the rest":
blackouts are applied on every bar inside the calendar's declared span for the
currencies it carries, and are absent before its declared start and for
currencies it does not carry. The covered fraction of bars per instrument is
printed, written to ``calendar_coverage.json`` and written into the campaign
notes, so the report says how much of each series ran with blackouts. The run
is REFUSED when a series has bars after the calendar's declared end (an
uncovered tail is not the declared design) or when the calendar carries none of
an instrument's currencies (nothing would be enforced). ``allow_empty_calendar``
is set only when some series is partly uncovered; it lifts ``run_validation``'s
coverage refusal and nothing else, because the official calendar is still the
blackout source. ``--calendar none`` runs with no calendar at all and says in
the notes that no blackout was enforced.

Engine version
--------------
``--engine-version-check`` refuses to run unless
``fiboki.backtest.version.ENGINE_VERSION`` is ``engine_v3_realism`` (or the
version given), so a stale checkout cannot produce a report under a new
campaign id. The effective configuration (engine version, account currency, FX
label, calendar coverage, construction policy, gate set) is the first block of
``<out>/run.log``, which this script writes itself (appending, one block per
invocation, so a resumed campaign keeps its whole transcript).

Resume
------
``--out`` holds ``checkpoint.json``. Re-running the same command resumes: cells
that produced a report are replayed from the checkpoint rather than recomputed,
and cells that returned no data are retried, because a missing feed is a gap in
the search rather than a result about a strategy.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, TextIO

import numpy as np
import pandas as pd

from fiboki.backtest.version import ENGINE_VERSION
from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import SeriesFxSource
from fiboki.data.schema import DatasetKind
from fiboki.data.store import DataStore
from fiboki.discovery.campaign import (
    BarSet,
    CampaignCheckpoint,
    CampaignRunner,
    CampaignSpec,
    seed_documents,
)
from fiboki.discovery.hypothesis import (
    HypothesisLedger,
    HypothesisStatus,
    load_hypotheses,
)
from fiboki.marketstate.calendar import (
    EconomicCalendar,
    InMemoryEconomicCalendar,
    instrument_currencies,
    load_official_calendar,
)
from fiboki.research.experiment import (
    ActorKind,
    ExperimentDraft,
    ExperimentLedger,
    Outcome,
)
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import research_construction_policy
from fiboki.validation.gates import GATE_SET_V2, GateSet
from fiboki.validation.holdout import HoldoutRegistry
from fiboki.validation.report import ValidationReport
from fiboki.validation.run import (
    RESEARCH_ACCOUNT_CCY,
    RESEARCH_CONSTRUCTION,
    FxSourceUnavailable,
    build_research_fx_source,
    research_fx_pairs,
)

REPO = Path(__file__).resolve().parents[1]
SEED_DIR = REPO / "research" / "strategies"
HYPOTHESIS_DIR = REPO / "research" / "hypotheses"

#: The engine generation ``--engine-version-check`` requires by default.
K3_ENGINE_VERSION = "engine_v3_realism"

#: The account currency the legacy ``FX_SERIES_FOR`` table converts into. Only
#: ``--account-ccy USD`` uses it; research runs in GBP.
LEGACY_FX_ACCOUNT_CCY = "USD"

#: Quote currency -> the HistData series that converts it into USD, for
#: ``--account-ccy USD`` only (the K1/K2 conversion, kept so those campaigns can
#: be reproduced).
#:
#: :class:`~fiboki.core.money.SeriesFxSource` derives the inverse itself, so
#: ``USDJPY`` serves ``JPY -> USD`` as ``1 / USDJPY`` and no rate is inverted by
#: hand. The rates are bid closes from the same HistData source as the bars, and
#: the conversion is therefore a bid-to-bid conversion, not a mid one -- stated
#: rather than corrected, because correcting it would need an ask series that
#: does not exist.
FX_SERIES_FOR: dict[str, str] = {
    "JPY": "USDJPY",
    "CHF": "USDCHF",
    "CAD": "USDCAD",
    "GBP": "GBPUSD",
    "EUR": "EURUSD",
}

#: Loosened ONLY under ``--gates diagnostic``, and only so that the rungs after
#: 0 execute on real data. 25 trades is not a promotion bar.
DIAGNOSTIC_MIN_TRADES = 25

#: Which declared axes each seed sweeps. Grid capping never DROPS an axis, so a
#: seven-parameter document has a 128-point floor; a campaign that wants an
#: eight-point grid per cell has to say out loud which three axes it swept. The
#: unswept parameters take their declared defaults and are recorded in every
#: binding. Mutants inherit their root seed's entry.
SWEEP_AXES: dict[str, tuple[str, ...]] = {
    "donchian_breakout_atr": ("channel_period", "stop_atr_multiple", "trail_atr_multiple"),
    "ichimoku_kumo_trend": ("tenkan_period", "kijun_period", "stop_buffer_atr"),
    "macd_ema_trend_hybrid": ("macd_fast", "macd_slow", "stop_atr_multiple"),
    "fib_golden_pocket_pullback": ("swing_lookback", "pocket_near", "stop_buffer_atr"),
    "rsi_band_mean_reversion": ("rsi_period", "rsi_floor", "bb_num_std"),
}

#: Exit codes for the refusals, so a wrapper can tell them apart.
EXIT_ENGINE_VERSION = 2
EXIT_FX_REFUSED = 3
EXIT_CALENDAR_REFUSED = 4


def gate_set_for(mode: str) -> GateSet:
    if mode == "production":
        return GATE_SET_V2
    return GATE_SET_V2.with_overrides(
        f"diagnostic-single-instrument-min{DIAGNOSTIC_MIN_TRADES}",
        min_trades=float(DIAGNOSTIC_MIN_TRADES),
    )


# --------------------------------------------------------------------------
# FX
# --------------------------------------------------------------------------


def build_fx_source(
    store: DataStore, instruments: Sequence[str], timeframe: Timeframe
) -> tuple[SeriesFxSource | None, str, dict[str, Any]]:
    """The LEGACY USD-account FX source (``--account-ccy USD`` only).

    Returns ``(source, label, coverage)``. ``coverage`` names which pairs were
    loaded, which quote currencies each instrument needs, and -- the part that
    matters -- any instrument whose first bar predates the first observation of
    the series that has to convert it, because ``SeriesFxSource`` looks up
    as-of BACKWARD and will raise rather than extrapolate. Such an instrument is
    reported here so it can be dropped from the universe deliberately instead of
    erroring cell by cell halfway through a campaign.
    """
    needed: dict[str, list[str]] = {}
    for symbol in instruments:
        quote = get_instrument(symbol.upper()).quote.upper()
        if quote != LEGACY_FX_ACCOUNT_CCY:
            needed.setdefault(quote, []).append(symbol.upper())
    if not needed:
        return None, "", {"pairs_loaded": [], "needed_by_quote_ccy": {}}

    series: dict[str, pd.Series] = {}
    loaded: dict[str, dict[str, Any]] = {}
    missing: dict[str, str] = {}
    for quote in sorted(needed):
        pair = FX_SERIES_FOR.get(quote)
        if pair is None:
            missing[quote] = f"no series declared in FX_SERIES_FOR for {quote}"
            continue
        try:
            frame, version = store.read_latest(
                pair, timeframe, kind=DatasetKind.VALIDATED
            )
        except Exception as exc:
            missing[quote] = f"{pair} {timeframe.value} not readable: {exc}"
            continue
        close = frame["close"].astype(float)
        series[pair.upper()] = close
        loaded[pair.upper()] = {
            "quote_ccy_served": quote,
            "dataset_version_id": str(version.version_id),
            "observations": int(len(close)),
            "first": str(close.index[0]),
            "last": str(close.index[-1]),
        }

    # Coverage: a rate must exist at or before every bar it has to convert.
    uncovered: list[dict[str, Any]] = []
    for quote, symbols in sorted(needed.items()):
        pair = FX_SERIES_FOR.get(quote)
        if pair is None or pair.upper() not in series:
            for symbol in symbols:
                uncovered.append(
                    {"instrument": symbol, "quote_ccy": quote, "reason": missing.get(quote, "")}
                )
            continue
        fx_first = series[pair.upper()].index[0]
        for symbol in symbols:
            try:
                bars, _ = store.read_latest(symbol, timeframe, kind=DatasetKind.VALIDATED)
            except Exception as exc:
                uncovered.append(
                    {"instrument": symbol, "quote_ccy": quote, "reason": f"bars unreadable: {exc}"}
                )
                continue
            if len(bars) and bars.index[0] < fx_first:
                uncovered.append(
                    {
                        "instrument": symbol,
                        "quote_ccy": quote,
                        "reason": (
                            f"first bar {bars.index[0]} predates the first {pair} "
                            f"observation {fx_first}; an as-of backward lookup has "
                            "nothing to read there and would raise"
                        ),
                    }
                )

    label = (
        "SeriesFxSource(" + ", ".join(f"{k}->{v['quote_ccy_served']}" for k, v in sorted(loaded.items()))
        + f"; bid closes, HistData {timeframe.value}, as-of backward)"
    )
    coverage = {
        "account_ccy": LEGACY_FX_ACCOUNT_CCY,
        "builder": "run_discovery_campaign.build_fx_source (legacy FX_SERIES_FOR)",
        "pairs_loaded": loaded,
        "needed_by_quote_ccy": {k: sorted(v) for k, v in sorted(needed.items())},
        "missing_series": missing,
        "instruments_without_usable_coverage": uncovered,
        "label": label,
    }
    return (SeriesFxSource(series=series) if series else None), label, coverage


def research_fx_source(
    store: DataStore,
    instruments: Sequence[str],
    timeframes: Sequence[Timeframe],
    *,
    account_ccy: str,
    bars: Any,
) -> tuple[SeriesFxSource | None, str, dict[str, Any], str]:
    """The research FX source (:func:`build_research_fx_source`) and its coverage.

    Returns ``(source, label, coverage, refusal)``. ``refusal`` is empty unless
    the store lacks a cross the universe needs, in which case it is
    :class:`FxSourceUnavailable`'s message (the pairs to ingest) and the
    caller must not run.

    ``coverage`` records, per instrument, the pairs that convert it and whether
    its bars start before the first KNOWN rate of any of them or end after the
    last rate plus ``max_staleness``; either would raise inside the engine, so
    such an instrument is named here instead of erroring cell by cell. Gaps in
    a series longer than ``max_staleness`` are counted per pair for the same
    reason.
    """
    needed: dict[str, list[str]] = {}
    for symbol in instruments:
        quote = get_instrument(symbol).quote.upper()
        if quote != account_ccy.upper():
            needed.setdefault(quote, []).append(symbol)
    coverage: dict[str, Any] = {
        "account_ccy": account_ccy.upper(),
        "builder": "fiboki.validation.run.build_research_fx_source (D1 closes as-of bar close)",
        "needed_by_quote_ccy": {k: sorted(v) for k, v in sorted(needed.items())},
    }
    if not needed:
        coverage.update(pairs_loaded={}, instruments_without_usable_coverage=[], label="")
        return None, "", coverage, ""
    try:
        source, label = build_research_fx_source(
            store, quote_currencies=tuple(needed), account_ccy=account_ccy
        )
    except FxSourceUnavailable as exc:
        coverage.update(missing_pairs=list(exc.missing), refusal=str(exc), label="")
        return None, "", coverage, str(exc)

    staleness = source.max_staleness
    loaded: dict[str, dict[str, Any]] = {}
    for pair, series in sorted(source.series.items()):
        gaps = series.index.to_series().diff().dropna()
        loaded[pair] = {
            **dict(source.lineage.get(pair, {})),
            "observations": int(len(series)),
            "first_known": str(series.index[0]),
            "last_known": str(series.index[-1]),
            "gaps_over_max_staleness": int((gaps > staleness).sum()),
        }
    fallback = dict(source.lineage.get("_fallback", {}))
    routes: dict[str, dict[str, Any]] = {}
    uncovered: list[dict[str, Any]] = []
    for quote, symbols in sorted(needed.items()):
        pairs = research_fx_pairs(quote, account_ccy)
        legs = fallback.get(quote, {})
        leg_pairs = tuple(legs.get("legs", ())) if legs.get("available") else ()
        for symbol in symbols:
            for tf in timeframes:
                barset = bars(symbol, tf)
                if barset is None or len(barset.frame) == 0:
                    continue
                index = barset.frame.index
                direct_ok = _fresh(source, pairs, index, staleness)
                via_ok = (
                    _fresh(source, leg_pairs, index, staleness)
                    if leg_pairs
                    else np.zeros(len(index), dtype=bool)
                )
                via = ~direct_ok & via_ok
                none = ~direct_ok & ~via_ok
                row = {
                    "quote_ccy": quote,
                    "direct": list(pairs),
                    "via_usd_legs": list(leg_pairs),
                    "bars": int(len(index)),
                    "bars_direct": int(direct_ok.sum()),
                    "bars_via_usd": int(via.sum()),
                    "bars_without_rate": int(none.sum()),
                    "via_usd_first": str(index[via][0]) if via.any() else None,
                    "via_usd_last": str(index[via][-1]) if via.any() else None,
                }
                routes[f"{symbol} {tf.value}"] = row
                if none.any():
                    uncovered.append(
                        {
                            "instrument": symbol,
                            "timeframe": tf.value,
                            "quote_ccy": quote,
                            "pairs": list(pairs),
                            "reason": (
                                f"{row['bars_without_rate']:,} of {row['bars']:,} bars "
                                f"(first {index[none][0]}, last {index[none][-1]}) have no "
                                f"fresh {'/'.join(pairs)} rate"
                                + (
                                    f" and no fresh {'/'.join(leg_pairs)} fallback"
                                    if leg_pairs
                                    else " and no via_usd fallback"
                                )
                                + ". SeriesFxSource raises rather than extrapolate, so "
                                "cells on this series will error where a trade needs a rate"
                            ),
                        }
                    )
    coverage.update(
        pairs_loaded=loaded,
        max_staleness=str(staleness),
        fallback=fallback,
        routes_at_bar_open=routes,
        instruments_without_usable_coverage=uncovered,
        label=label,
    )
    return source, label, coverage, ""


def _fresh(
    source: SeriesFxSource,
    pairs: Sequence[str],
    index: pd.DatetimeIndex,
    staleness: pd.Timedelta,
) -> np.ndarray:
    """Per timestamp: does every pair in ``pairs`` have a rate no older than ``staleness``?

    The same as-of rule as ``SeriesFxSource._asof`` (newest observation at or
    before the timestamp), vectorised, evaluated at bar OPEN times.
    """
    ok = np.ones(len(index), dtype=bool)
    for pair in pairs:
        known = source.series[pair].index
        pos = known.searchsorted(index, side="right") - 1
        has = pos >= 0
        age = np.full(len(index), np.inf)
        age[has] = (index[has] - known[pos[has]]).total_seconds()
        ok &= has & (age <= staleness.total_seconds())
    return ok


# --------------------------------------------------------------------------
# Economic calendar
# --------------------------------------------------------------------------


class CalendarPlan(NamedTuple):
    """How the campaign treats scheduled releases, decided before planning.

    A NamedTuple, not a dataclass: ``scripts/build_research_ledger.py`` loads
    this file by path without registering it in ``sys.modules``, and a
    dataclass cannot be created in a module that is not registered there.
    """

    mode: str
    calendar: EconomicCalendar
    allow_empty_calendar: bool
    summary: str
    notes: str
    coverage: dict[str, Any]
    refusal: str = ""


def calendar_plan(
    mode: str,
    instruments: Sequence[str],
    timeframes: Sequence[Timeframe],
    *,
    bars: Any,
    calendar: EconomicCalendar | None = None,
) -> CalendarPlan:
    """Resolve ``--calendar`` into a calendar, a flag, and the record of both.

    ``official``: enforce where covered, record the rest (see the module
    docstring). Per series: ``time_covered_fraction`` is the share of bars
    inside the declared span; ``currencies_not_carried`` are the instrument's
    currencies the calendar has no events for; ``fully_covered_fraction`` is
    the time fraction when every currency is carried and 0 otherwise.
    """
    if mode == "none":
        return CalendarPlan(
            mode="none",
            calendar=InMemoryEconomicCalendar.empty(),
            allow_empty_calendar=True,
            summary="none (--calendar none): event blackouts NOT enforced; allow_empty_calendar=True",
            notes=(
                "Economic calendar: NONE (--calendar none). Event blackouts were NOT "
                "enforced in this run: every document's declared blackout was skipped "
                "and every result trades straight through every scheduled release."
            ),
            coverage={"mode": "none", "blackouts_enforced": False},
        )
    if mode != "official":
        raise ValueError(f"--calendar {mode!r}: expected 'official' or 'none'")

    cal = calendar if calendar is not None else load_official_calendar()
    cov = cal.coverage()
    if not cov.is_populated:
        return CalendarPlan(
            mode="official",
            calendar=cal,
            allow_empty_calendar=False,
            summary="official: EMPTY",
            notes="",
            coverage={"mode": "official", "calendar": cov.to_dict()},
            refusal="the official calendar has no events; use --calendar none to run without one",
        )
    lo = cov.declared_start if cov.declared_start is not None else cov.first_event
    hi = cov.declared_end if cov.declared_end is not None else cov.last_event
    carried = set(cov.currencies)

    rows: dict[str, dict[str, Any]] = {}
    refusals: list[str] = []
    partial = False
    for symbol in instruments:
        currencies = instrument_currencies(symbol)
        not_carried = [c for c in currencies if c not in carried]
        for tf in timeframes:
            key = f"{symbol} {tf.value}"
            barset = bars(symbol, tf)
            if barset is None or len(barset.frame) == 0:
                rows[key] = {"bars": 0, "note": "no bars; the cell will be retried, not run"}
                continue
            index = barset.frame.index
            inside = int(((index >= lo) & (index <= hi)).sum())
            n = int(len(index))
            time_frac = inside / n
            row = {
                "bars": n,
                "first_bar": str(index[0]),
                "last_bar": str(index[-1]),
                "bars_inside_declared_span": inside,
                "bars_before_declared_start": int((index < lo).sum()),
                "bars_after_declared_end": int((index > hi).sum()),
                "time_covered_fraction": round(time_frac, 6),
                "currencies": list(currencies),
                "currencies_not_carried": not_carried,
                "fully_covered_fraction": round(time_frac if not not_carried else 0.0, 6),
            }
            rows[key] = row
            if row["bars_after_declared_end"]:
                refusals.append(
                    f"{key}: {row['bars_after_declared_end']} bar(s) after the calendar's "
                    f"declared end {hi}; refresh the fixture or cut the series"
                )
            if currencies and len(not_carried) == len(currencies):
                refusals.append(
                    f"{key}: the calendar carries none of {list(currencies)}, so no "
                    "blackout would be enforced at all; use --calendar none for it"
                )
            if time_frac < 1.0 or not_carried:
                partial = True

    summary = (
        f"official: {cov.n_events} events, declared {lo} .. {hi}, currencies "
        f"{', '.join(cov.currencies)}; policy enforce-where-covered; "
        f"allow_empty_calendar={partial}"
    )
    per_series = "; ".join(
        f"{k} {100 * r['time_covered_fraction']:.1f}% of {r['bars']:,} bars"
        + (f" (not carried: {', '.join(r['currencies_not_carried'])})" if r["currencies_not_carried"] else "")
        for k, r in rows.items()
        if r.get("bars")
    )
    notes = (
        f"Economic calendar: official fixture, {cov.n_events} events, declared span "
        f"{lo} .. {hi}, currencies {', '.join(cov.currencies)}. Policy "
        "enforce-where-covered: blackouts were enforced on bars inside the declared "
        "span for the currencies the calendar carries and were ABSENT before "
        f"{lo} and for currencies it does not carry"
        + (
            "; allow_empty_calendar=True lifted run_validation's coverage refusal only, "
            "the official calendar remained the blackout source"
            if partial
            else "; every series is fully covered and the coverage refusal stayed on"
        )
        + f". Share of bars inside the declared span, per series: {per_series}."
    )
    return CalendarPlan(
        mode="official",
        calendar=cal,
        allow_empty_calendar=partial,
        summary=summary,
        notes=notes,
        coverage={
            "mode": "official",
            "policy": "enforce where covered; record the uncovered fraction",
            "allow_empty_calendar": partial,
            "calendar": cov.to_dict(),
            "series": rows,
        },
        refusal="; ".join(refusals),
    )


# --------------------------------------------------------------------------
# Bars, run.log
# --------------------------------------------------------------------------


def bars_from_store(store: DataStore, *, bars_from: pd.Timestamp | None = None):
    """A :class:`~fiboki.discovery.campaign.BarSource` over a data root.

    ``bars_from`` trims every series to bars at or after that UTC instant. It is
    a recorded, campaign-level decision (written to run.log and the campaign
    notes), used when the earliest years have no usable FX coverage: dropping
    bars is honest, inventing a rate is not. The dataset version id is kept, so
    the trim is visible as a start-date difference, never as a different dataset.
    """

    def source(instrument: str, timeframe: Timeframe) -> BarSet | None:
        try:
            frame, version = store.read_latest(
                instrument, timeframe, kind=DatasetKind.VALIDATED
            )
        except Exception as exc:
            print(f"  no bars for {instrument} {timeframe.value}: {exc}")
            return None
        if frame is None or len(frame) == 0:
            return None
        if bars_from is not None:
            before = len(frame)
            frame = frame.loc[frame.index >= bars_from]
            print(
                f"  trim {instrument} {timeframe.value}: bars_from={bars_from.isoformat()} "
                f"dropped {before - len(frame):,} of {before:,} bars"
            )
            if len(frame) == 0:
                return None
        return BarSet(
            instrument=instrument.upper(),
            timeframe=timeframe,
            frame=frame,
            dataset_version_id=str(version.version_id),
        )

    return source


def _memoised(source: Any) -> Any:
    """Read each series once for the pre-flight checks, then drop the cache."""
    cache: dict[tuple[str, str], BarSet | None] = {}

    def read(instrument: str, timeframe: Timeframe) -> BarSet | None:
        key = (instrument.upper(), timeframe.value)
        if key not in cache:
            cache[key] = source(instrument, timeframe)
        return cache[key]

    return read


class _Tee:
    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, text: str) -> int:
        for stream in self._streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


@contextlib.contextmanager
def _run_log(path: Path) -> Iterator[None]:
    """Everything printed goes to the console AND ``path`` (appended)."""
    with path.open("a", encoding="utf-8") as handle, contextlib.redirect_stdout(
        _Tee(sys.stdout, handle)
    ):
        yield


#: The provenance file ``research/generate_families.py`` writes beside its documents.
GENERATED_MANIFEST = "MANIFEST.json"


class GeneratedRoster(NamedTuple):
    documents: tuple[StrategyDocument, ...]
    path: Path
    manifest_sha256: str

    def describe(self) -> str:
        return (
            f"{len(self.documents)} from {self.path}, manifest sha256 {self.manifest_sha256}"
        )


def load_generated(path: Path, seeds: Sequence[StrategyDocument]) -> GeneratedRoster:
    """The ``--generated-dir`` documents, which the planner treats as seeds.

    Every ``*.json`` except the manifest, ordered by id. The manifest's sha256 is
    recorded (``absent`` when there is none) so a report says exactly which
    generated set it planned from. A generated id that collides with a seed id is
    refused rather than letting one silently shadow the other.
    """
    if not path.is_dir():
        raise ValueError(f"--generated-dir {path} is not a directory")
    found = sorted(
        (
            StrategyDocument.from_json(p.read_text(encoding="utf-8"))
            for p in sorted(path.glob("*.json"))
            if p.name != GENERATED_MANIFEST
        ),
        key=lambda d: d.strategy_id,
    )
    if not found:
        raise ValueError(f"--generated-dir {path} holds no strategy documents")
    clash = sorted({d.strategy_id for d in found} & {d.strategy_id for d in seeds})
    if clash:
        raise ValueError(f"--generated-dir {path} reuses seed id(s) {clash}")
    manifest = path / GENERATED_MANIFEST
    digest = (
        hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else "absent"
    )
    return GeneratedRoster(tuple(found), path, digest)


def _construction_label() -> str:
    fp = research_construction_policy().fingerprint()
    return (
        f"{fp['construction_version']} (run_validation default {RESEARCH_CONSTRUCTION!r}: "
        f"allocator {fp['allocator']}, tier {fp['tier']}, lifecycle {fp['lifecycle']}, "
        f"config_sha256 {str(fp['config_sha256'])[:12]}, correlation {fp['instrument_correlation']})"
    )


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--out", default=Path("research/reports/campaign"), type=Path)
    parser.add_argument("--cache", default=None, type=Path)
    parser.add_argument("--gates", choices=("production", "diagnostic"), default="production")
    parser.add_argument("--campaign-id", default="k1_xauusd_h4")
    parser.add_argument(
        "--seeds",
        nargs="+",
        default=None,
        metavar="STRATEGY_ID",
        help=(
            "Restrict the campaign to these seed documents (default: every document in "
            "research/strategies). Hypotheses are unaffected. An unknown id is an error, "
            "so a typo cannot silently run the whole roster. Recorded in run.log."
        ),
    )
    parser.add_argument(
        "--generated-dir",
        default=None,
        type=Path,
        metavar="PATH",
        help=(
            "ADD the documents in this directory (research/generate_families.py output) "
            "to the seed roster for planning. Recorded in run.log and the report notes "
            "with the directory's MANIFEST.json sha256. --seeds filters the combined roster."
        ),
    )
    parser.add_argument("--instruments", nargs="+", default=["XAUUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["H4"])
    parser.add_argument(
        "--bars-from",
        default=None,
        help=(
            "Trim every series to bars at or after this UTC instant (ISO 8601). A "
            "recorded campaign-level decision for years with no usable FX coverage."
        ),
    )
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--max-evaluations", type=int, default=400)
    parser.add_argument("--max-grid-points", type=int, default=8)
    parser.add_argument("--max-values-per-axis", type=int, default=2)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--actor", default="script:run_discovery_campaign")
    parser.add_argument(
        "--account-ccy",
        choices=(RESEARCH_ACCOUNT_CCY, LEGACY_FX_ACCOUNT_CCY),
        default=RESEARCH_ACCOUNT_CCY,
        help=(
            "Account currency. GBP (default): FX from the store's D1 GBP crosses via "
            "build_research_fx_source. USD: the legacy FX_SERIES_FOR table, for "
            "reproducing K1/K2 only."
        ),
    )
    parser.add_argument(
        "--calendar",
        choices=("official", "none"),
        default="official",
        help=(
            "official (default): enforce blackouts where the official calendar covers "
            "and record the covered fraction per series. none: no blackouts at all, "
            "stated in the campaign notes."
        ),
    )
    parser.add_argument(
        "--engine-version-check",
        nargs="?",
        const=K3_ENGINE_VERSION,
        default=None,
        metavar="VERSION",
        help=(
            f"Refuse to run unless ENGINE_VERSION equals VERSION (default "
            f"{K3_ENGINE_VERSION}), so a stale checkout cannot produce the report."
        ),
    )
    parser.add_argument(
        "--backfill-reports",
        default=None,
        type=Path,
        help=(
            "Directory of ValidationReport JSON produced by EARLIER runs on the "
            "same bars. Each one is filed in the campaign's experiment ledger "
            "before planning, so its trials are counted in the true trial count "
            "and its strategies are visible to the novelty check. Those trials "
            "were part of the search whatever directory they were written to."
        ),
    )
    parser.add_argument(
        "--external-prior-trials",
        type=int,
        default=0,
        help=(
            "Trials already spent on THESE BARS that this ledger cannot see. A "
            "dataset version id hashes content together with lineage, so a fresh "
            "ingest of identical bytes mints a new id and the ledger reports zero "
            "prior trials for a series that has been searched hundreds of times. "
            "This adds them back. It can only raise the trial count, and it is "
            "refused without --external-prior-trials-reason."
        ),
    )
    parser.add_argument(
        "--external-prior-trials-reason",
        default="",
        help="Where those trials were spent and how you know they were on these bars.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the plan and the trial accounting, and run nothing.",
    )
    args = parser.parse_args(argv)

    # Before any side effect: a stale engine must not even create the out dir.
    if args.engine_version_check is not None and args.engine_version_check != ENGINE_VERSION:
        print(
            f"REFUSED: this checkout's engine is {ENGINE_VERSION!r} but the campaign "
            f"requires {args.engine_version_check!r}. A report produced here would be "
            "stamped with the wrong engine generation. Update the checkout.",
            file=sys.stderr,
        )
        return EXIT_ENGINE_VERSION

    # Likewise an unreadable --generated-dir: refused before the out dir exists.
    roster = seed_documents(SEED_DIR)
    if args.generated_dir is not None:
        try:
            roster = roster + load_generated(args.generated_dir, roster).documents
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2

    # Likewise an unknown --seeds id: refused before the out dir exists.
    if args.seeds:
        known = sorted(d.strategy_id for d in roster)
        unknown = sorted(set(args.seeds) - set(known))
        if unknown:
            print(
                f"REFUSED: --seeds names unknown document(s) {unknown}; known: {known}",
                file=sys.stderr,
            )
            return 2

    args.out.mkdir(parents=True, exist_ok=True)
    with _run_log(args.out / "run.log"):
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    store = DataStore(args.data_root)
    try:
        return _run_with_store(args, store)
    finally:
        store.close()


def _run_with_store(args: argparse.Namespace, store: DataStore) -> int:
    gates = gate_set_for(args.gates)
    seeds = seed_documents(SEED_DIR)
    generated = (
        load_generated(args.generated_dir, seeds) if args.generated_dir is not None else None
    )
    if generated is not None:  # validated in main() before any side effect
        seeds = seeds + generated.documents
    if args.seeds:  # validated in main() before any side effect
        known = {d.strategy_id: d for d in seeds}
        seeds = tuple(known[s] for s in sorted(set(args.seeds)))
    hypotheses = load_hypotheses(HYPOTHESIS_DIR)
    account_ccy = str(args.account_ccy).upper()

    instruments = [s.upper() for s in args.instruments]
    timeframes = [Timeframe(t) for t in args.timeframes]
    bars_from = pd.Timestamp(args.bars_from, tz="UTC") if args.bars_from else None
    if bars_from is not None and bars_from.tzinfo is None:
        bars_from = bars_from.tz_localize("UTC")
    preflight_bars = _memoised(bars_from_store(store, bars_from=bars_from))

    # --- FX
    if account_ccy == LEGACY_FX_ACCOUNT_CCY:
        fx, fx_label, fx_coverage = build_fx_source(store, instruments, timeframes[0])
        fx_refusal = ""
    else:
        fx, fx_label, fx_coverage, fx_refusal = research_fx_source(
            store, instruments, timeframes, account_ccy=account_ccy, bars=preflight_bars
        )
    (args.out / "fx_coverage.json").write_text(
        json.dumps(fx_coverage, indent=2, default=str), encoding="utf-8"
    )

    # --- calendar
    cal = calendar_plan(args.calendar, instruments, timeframes, bars=preflight_bars)
    del preflight_bars  # the runner reads the bars again; do not hold two copies
    (args.out / "calendar_coverage.json").write_text(
        json.dumps(cal.coverage, indent=2, default=str), encoding="utf-8"
    )

    # --- the effective configuration: the first block of run.log
    check = (
        f"required {args.engine_version_check}: ok"
        if args.engine_version_check is not None
        else "not checked (--engine-version-check not given)"
    )
    fx_line = (
        f"REFUSED: {fx_refusal}"
        if fx_refusal
        else fx_label or f"none needed: every instrument is quoted in {account_ccy}"
    )
    print(f"==== run_discovery_campaign {datetime.now(tz=UTC).isoformat(timespec='seconds')} ====")
    print(f"campaign_id: {args.campaign_id}")
    print(f"engine_version: {ENGINE_VERSION} ({check})")
    print(f"account_ccy: {account_ccy}")
    print(f"fx_label: {fx_line}")
    print(f"calendar: {cal.summary}")
    covered = [r for r in cal.coverage.get("series", {}).values() if r.get("bars")]
    if covered:
        fractions = [r["time_covered_fraction"] for r in covered]
        print(
            f"calendar_coverage: {len(covered)} series, share of bars inside the declared "
            f"span {100 * min(fractions):.1f}% .. {100 * max(fractions):.1f}% "
            "(per series below and in calendar_coverage.json)"
        )
    else:
        print(f"calendar_coverage: {'n/a' if cal.mode == 'none' else 'no series read'}")
    print(f"construction_policy: {_construction_label()}")
    print(f"gate_set: {gates.version} (min_trades={gates.by_name('min_trades').threshold:g})")
    print(f"bars_from: {bars_from.isoformat() if bars_from is not None else 'none (full series)'}")
    print(f"universe: {len(instruments)} instrument(s) {' '.join(instruments)}; timeframes {' '.join(args.timeframes)}")
    print(
        "seeds_requested: "
        + (
            " ".join(d.strategy_id for d in seeds)
            if args.seeds
            else "all (every document in research/strategies"
            + (" and --generated-dir)" if generated is not None else ")")
        )
    )
    if generated is not None:
        print(f"generated_documents: {generated.describe()}")
    print(
        f"budget: max_evaluations={args.max_evaluations} generations={args.generations} "
        f"grid={args.max_grid_points}x{args.max_values_per_axis} folds={args.folds}"
    )
    print(f"external_prior_trials: {args.external_prior_trials}")
    print(f"data_root: {args.data_root}")
    print("====")

    for pair, detail in sorted((fx_coverage.get("pairs_loaded") or {}).items()):
        print(f"  fx {pair}: {json.dumps(detail, default=str)}")
    for key, row in sorted((fx_coverage.get("routes_at_bar_open") or {}).items()):
        print(
            f"  fx route {key}: {row['bars_direct']:,} bars direct, {row['bars_via_usd']:,} via_usd"
            + (
                f" ({row['via_usd_first'][:10]}..{row['via_usd_last'][:10]})"
                if row["bars_via_usd"]
                else ""
            )
            + f", {row['bars_without_rate']:,} without a rate"
        )
    for row in fx_coverage.get("instruments_without_usable_coverage", ()):
        print(f"  NO FX COVERAGE {row['instrument']}: {row['reason']}")
    for key, row in cal.coverage.get("series", {}).items():
        if not row.get("bars"):
            print(f"  calendar {key}: {row.get('note', '')}")
            continue
        print(
            f"  calendar {key}: {row['bars']:,} bars {row['first_bar'][:10]}..{row['last_bar'][:10]}, "
            f"{100 * row['time_covered_fraction']:.1f}% inside the declared span"
            + (
                f", not carried: {', '.join(row['currencies_not_carried'])}"
                if row["currencies_not_carried"]
                else ""
            )
        )

    if fx_refusal:
        print(f"REFUSED (fx): {fx_refusal}")
        return EXIT_FX_REFUSED
    if cal.refusal:
        print(f"REFUSED (calendar): {cal.refusal}")
        return EXIT_CALENDAR_REFUSED

    spec = CampaignSpec(
        campaign_id=args.campaign_id,
        universe=tuple(instruments),
        timeframes=tuple(timeframes),
        hypotheses=hypotheses,
        actor=args.actor,
        actor_kind=ActorKind.AGENT,
        account_ccy=account_ccy,
        gate_set=gates,
        max_evaluations=args.max_evaluations,
        max_grid_points=args.max_grid_points,
        max_values_per_axis=args.max_values_per_axis,
        sweep_parameters=SWEEP_AXES,
        walk_forward_folds=args.folds,
        max_generations=args.generations,
        external_prior_trials=args.external_prior_trials,
        external_prior_trials_reason=args.external_prior_trials_reason,
        fx_label=fx_label,
        allow_empty_calendar=cal.allow_empty_calendar,
        notes=(
            (f"bars_from={bars_from.isoformat()} (series trimmed; recorded decision). "
             if bars_from is not None else "")
            + f"Phase K campaign {args.campaign_id} on "
            f"{len(instruments)} instrument(s) {', '.join(instruments)} "
            f"{', '.join(args.timeframes)}, {args.gates} gate set, "
            f"engine {ENGINE_VERSION}, "
            f"{account_ccy} account, IG_REALISTIC profile, "
            + (
                f"quote currencies converted by {fx_label}. "
                if fx is not None
                else "no FX conversion performed. "
            )
            + f"Portfolio construction {_construction_label()}. "
            + cal.notes
            + " Every cell is deflated against the campaign's true trial count, "
            "not against its own parameter sweep."
            + (
                f" Generated documents added to the seed roster: {generated.describe()}."
                if generated is not None
                else ""
            )
        ),
    )

    ledger = ExperimentLedger(args.out / "experiments.sqlite")
    try:
        return _plan_and_run(args, spec, store, ledger, fx, cal.calendar, seeds, hypotheses)
    finally:
        ledger.close()


def _plan_and_run(
    args: argparse.Namespace,
    spec: CampaignSpec,
    store: DataStore,
    ledger: ExperimentLedger,
    fx: SeriesFxSource | None,
    calendar: EconomicCalendar,
    seeds: Sequence[StrategyDocument],
    hypotheses: Sequence[Any],
) -> int:
    registry = HoldoutRegistry(args.out / "holdout.sqlite")
    hypothesis_store = HypothesisLedger(ledger)
    for hypothesis in hypotheses:
        if hypothesis_store.get(hypothesis.hypothesis_id) is None:
            hypothesis_store.record(
                hypothesis,
                actor_name=args.actor,
                reason=f"campaign {args.campaign_id}: pre-registering the claim",
                campaign_id=args.campaign_id,
            )

    if args.backfill_reports is not None:
        filed = backfill(ledger, args.backfill_reports, actor=args.actor)
        print(f"backfilled {filed} prior experiment(s) from {args.backfill_reports}")

    runner = CampaignRunner(
        spec,
        bars=bars_from_store(
            store,
            bars_from=pd.Timestamp(args.bars_from, tz="UTC") if args.bars_from else None,
        ),
        ledger=ledger,
        registry=registry,
        checkpoint=CampaignCheckpoint(args.out / "checkpoint.json"),
        report_dir=args.out,
        cache_dir=args.cache,
        fx=fx,
        calendar=calendar,
    )

    print(f"seeds: {[d.strategy_id for d in seeds]}")
    print(f"hypotheses: {[h.hypothesis_id for h in hypotheses]}")

    started = time.time()
    plan = runner.plan(list(seeds))
    print()
    print(plan.describe())
    print()
    for skip in plan.skipped:
        print(f"  SKIP {skip.describe()[:200]}")
    print()
    print(
        f"TRUE TRIAL COUNT {plan.true_trial_count} "
        f"(planned {plan.planned_trial_count} "
        f"+ ledger prior {plan.prior_trial_count} "
        f"+ declared prior {plan.external_prior_trial_count})"
    )
    if plan.external_prior_trial_count:
        print(f"  declared prior trials because: {plan.external_prior_trials_reason}")
    if args.plan_only:
        return 0

    report = runner.run(plan)
    elapsed = time.time() - started

    print()
    print(report.summary())
    print()
    for cell in report.attempted:
        print(f"  {cell.describe()[:220]}")
    print()
    print(json.dumps(_digest(report, elapsed), indent=2, default=str))
    print()
    print(report.honest_statement())
    print()
    print(f"  -> {args.out / f'campaign_{spec.campaign_id}.json'}")

    _record_hypothesis_outcomes(hypothesis_store, report, spec, args.actor)
    return 0


def backfill(ledger: ExperimentLedger, directory: Path, *, actor: str) -> int:
    """File earlier ValidationReports as experiments, once each.

    Why this exists: ``research/reports/xauusd_h4/`` holds real ladder runs on
    the very bars this campaign uses, and those runs swept real parameter grids.
    They were written to a directory rather than to a ledger, so a campaign that
    ignored them would under-count its own search -- which is the exact defect
    Phase K exists to fix, in a smaller form.

    The seed DOCUMENT is attached where it can be found, so the ledger holds the
    structural fingerprint and the novelty check can see the prior work. Note
    that a report's own ``strategy_content_hash`` is the hash of the BOUND
    defaults while the document's is the template's; both are recorded, and the
    template hash is the one the ledger keys on because that is what a campaign
    proposes.
    """
    existing = {
        str(e.outputs.get("backfilled_report_hash"))
        for e in ledger.list()
        if e.outputs.get("backfilled_report_hash")
    }
    filed = 0
    for path in sorted(Path(directory).glob("*.json")):
        try:
            report = ValidationReport.load(path)
        except Exception:
            continue
        digest = report.content_hash()
        if digest in existing:
            continue
        document = None
        seed_path = SEED_DIR / f"{report.strategy_id}.json"
        if seed_path.exists():
            document = StrategyDocument.from_json(seed_path.read_text(encoding="utf-8"))
        ledger.create(
            ExperimentDraft(
                actor_kind=ActorKind.SCHEDULE,
                actor_name=actor,
                reason=(
                    f"backfilled prior ladder run from {path.name}: it swept "
                    f"{report.raw_trial_count} parameterisation(s) on dataset "
                    f"{report.dataset_version_id}, and those trials are part of the "
                    "search this campaign's results must be corrected for"
                ),
                strategy_id=report.strategy_id,
                strategy_document=document,
                dataset_version_id=report.dataset_version_id,
                validation_report=report,
                outcome=Outcome.REJECTED if not report.verdict.promotable else Outcome.PROMOTED,
                conclusion=report.binding_constraint.describe(),
                outputs={
                    "backfilled_from": str(path),
                    "backfilled_report_hash": digest,
                    "report_strategy_content_hash": report.strategy_content_hash,
                    "raw_trial_count": report.raw_trial_count,
                },
                tags=("backfill", f"gates:{report.gate_set_version}"),
            )
        )
        existing.add(digest)
        filed += 1
    return filed


def _digest(report: Any, elapsed: float) -> dict[str, Any]:
    return {
        "campaign_id": report.campaign_id,
        "engine_version": ENGINE_VERSION,
        "gate_set": report.gate_set_version,
        "datasets": report.dataset_versions,
        "planned_trials": report.planned_trial_count,
        "prior_trials": report.prior_trial_count,
        "trial_accounting": report.lineage.get("trial_accounting", {}),
        "true_trial_count": report.true_trial_count,
        "ladder_evaluations": report.n_ladder_evaluations,
        "engine_backtests_run": report.n_engine_evaluations,
        "cells_attempted": len(report.attempted),
        "cells_skipped": len(report.skipped),
        "skips_by_kind": report.skips_by_kind(),
        "rejections_by_rung": report.rejections_by_rung(),
        "mutations_refused": len(report.rejected_mutations),
        "survivors": [c.strategy_id for c in report.survivors],
        "campaign_deflation_threshold": report.campaign_deflation_threshold,
        "deflation_variance_used": report.deflation_variance_used,
        "holdout_unconsumed": report.holdout.get("unconsumed"),
        "wall_clock_seconds": round(elapsed, 1),
    }


def _record_hypothesis_outcomes(
    store: HypothesisLedger, report: Any, spec: CampaignSpec, actor: str
) -> None:
    """File what the campaign did to each claim. A refutation is a result."""
    from fiboki.discovery.hypothesis import Evidence

    by_hypothesis: dict[str, list[Any]] = {}
    for cell in report.attempted:
        by_hypothesis.setdefault(cell.hypothesis_id, []).append(cell)

    for hypothesis in spec.hypotheses:
        cells = by_hypothesis.get(hypothesis.hypothesis_id, [])
        current = store.get(hypothesis.hypothesis_id) or hypothesis
        if not cells:
            continue
        survivors = [c for c in cells if c.survived]
        if survivors:
            store.set_status(
                current,
                HypothesisStatus.TESTING,
                reason=(
                    f"{len(survivors)} of {len(cells)} candidate(s) cleared every gate "
                    f"on {report.dataset_versions}. One cell is not evidence of an "
                    "edge; the claim stays under test."
                ),
                actor_name=actor,
                campaign_id=spec.campaign_id,
            )
            continue
        rungs = sorted({c.died_at_rung for c in cells if c.died_at_rung})
        store.set_status(
            current,
            HypothesisStatus.REFUTED,
            reason=(
                f"all {len(cells)} candidate(s) expressing this claim on "
                f"{report.dataset_versions} were rejected (died at: "
                f"{', '.join(rungs) or 'unrecorded'}). This refutes the claim ON THIS "
                "CELL, under this cost model and this gate set. It does not refute "
                "the mechanism in general."
            ),
            actor_name=actor,
            evidence=Evidence(
                direction="against",
                claim=(
                    f"campaign {spec.campaign_id}: every candidate was rejected; "
                    f"rungs reached: {', '.join(rungs) or 'unrecorded'}"
                ),
                source=f"campaign_{spec.campaign_id}.json",
                strength="moderate",
            ),
            campaign_id=spec.campaign_id,
        )


if __name__ == "__main__":
    sys.exit(main())
