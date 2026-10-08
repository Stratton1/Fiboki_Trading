"""One entry point: strategy document + bars + broker profile -> ValidationReport.

Everything the ladder needs is assembled here, in one place, so that running a
candidate against the real engine on real data is a single call with no room for
the caller to wire two of the pieces together inconsistently.

What this module refuses to let a caller do
-------------------------------------------
* **Run a strategy on a holdout that was never defined.** The segment is defined
  from the bars' own date range before anything runs, and defining it twice with
  different facts raises inside the registry.
* **Run a template.** The evaluator binds every grid point before compiling, so
  the thing that executes always has a complete, recorded binding.
* **Lose the provenance.** The dataset version id, the engine config
  fingerprint, the broker profile and the gate-set version all reach the report,
  and the same fingerprint is on every individual evaluation.
* **Silently convert currency.** An FX source must be supplied whenever the
  account currency differs from the instrument's quote currency; there is no
  1.0 default. Research runs in GBP, the operator's account currency, and
  :func:`build_research_fx_source` builds the rate source from the STORED
  daily closes of the GBP crosses, refusing with the list of instruments to
  ingest when one is missing. Pass ``fx_store`` and run_validation builds it.
* **Trade BID bars as mid.** A frame labelled ``bid`` is converted to
  ``synthetic_mid`` with half the instrument's registered typical spread
  (:func:`research_mid_frame`) and the conversion is recorded in the engine
  fingerprint; an ``ask`` or ``last`` frame is refused; an unlabelled frame is
  recorded as ``assumed_mid``.
* **Run an event blackout against a calendar that cannot answer.** A supplied
  ``calendar`` must be populated, span the bars and know the instrument's
  currencies, or the run refuses with
  ``fiboki.marketstate.calendar.USER_ACTION_NOTE``, unless the caller passes
  ``allow_empty_calendar=True``. With ``calendar=None`` no blackout is
  applied at all and a document that declares one gets a logged WARNING;
  ``discovery.campaign.run_cell`` now loads the official calendar by default,
  so a campaign reaches this path only by an explicit opt-out.

What it does NOT do
-------------------
It does not decide anything. The verdict is the ladder's and the gates', and a
rejection is returned as a report, not raised: a rejection is a research result
and the record of it is the point.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import (
    DEFAULT_MAX_STALENESS,
    FxRateSource,
    IdentityFxSource,
    SeriesFxSource,
)
from fiboki.data.providers.histdata import bid_to_mid
from fiboki.marketstate.calendar import EconomicCalendar, instrument_currencies
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import (
    EngineEvaluator,
    EvaluationCache,
    EvaluatorConfig,
    research_construction_policy,
)
from fiboki.validation.gates import PRODUCTION_GATE_SET, GateSet, sanity_trade_floor
from fiboki.validation.holdout import DEFAULT_HOLDOUT_FRACTION, HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder
from fiboki.validation.report import ValidationReport

__all__ = [
    "RESEARCH_ACCOUNT_CCY",
    "RESEARCH_CONSTRUCTION",
    "FxSourceUnavailable",
    "ValidationRun",
    "build_research_fx_source",
    "research_fx_pairs",
    "research_mid_frame",
    "run_validation",
]

#: Every research run is denominated in the operator's account currency, so a
#: research figure and a paper figure for the same strategy are comparable.
RESEARCH_ACCOUNT_CCY = "GBP"

#: The currency research triangulates through when no direct GBP cross exists.
_PIVOT = "USD"

#: ``run_validation(construction=...)`` default: the paper runtime's portfolio
#: construction (:func:`~fiboki.validation.engine_evaluator.
#: research_construction_policy`). Pass ``None`` for the flat risk_fraction path.
RESEARCH_CONSTRUCTION = "research_default"


class FxSourceUnavailable(RuntimeError):
    """A rate the research run needs is not in the data store.

    Carries ``missing`` -- the instruments to ingest -- so the message is an
    instruction, not a symptom.
    """

    def __init__(self, missing: Iterable[str], detail: str = "") -> None:
        self.missing = tuple(sorted(set(missing)))
        super().__init__(
            "research FX conversion cannot be built: ingest "
            + ", ".join(self.missing)
            + " (D1, validated) into the data store"
            + (f". {detail}" if detail else "")
        )


def _pair_symbol(a: str, b: str) -> str | None:
    """The registered symbol quoting ``a`` against ``b`` in either order."""
    for sym in (a + b, b + a):
        try:
            get_instrument(sym)
        except KeyError:
            continue
        return sym
    return None


def research_fx_pairs(quote_ccy: str, account_ccy: str = RESEARCH_ACCOUNT_CCY) -> tuple[str, ...]:
    """The FX series needed to convert ``quote_ccy`` into ``account_ccy``.

    The registered cross when one exists (USD->GBP is GBPUSD, JPY->GBP is
    GBPJPY, EUR->GBP is EURGBP, NZD->GBP is GBPNZD), else one triangulation
    through USD (SEK->GBP is USDSEK and GBPUSD). Raises
    :class:`FxSourceUnavailable` naming the pair that would have to be
    REGISTERED when even that is impossible (ILS: OANDA offers no ILS pair).

    "Registered" is not "stored": since the registry lists every OANDA
    instrument (2026-09-30), a direct cross can be registered before its bars
    are backfilled, and :func:`build_research_fx_source` then refuses naming it.
    """
    q, a = quote_ccy.upper(), account_ccy.upper()
    if q == a:
        return ()
    direct = _pair_symbol(q, a)
    if direct is not None:
        return (direct,)
    first, second = _pair_symbol(q, _PIVOT), _pair_symbol(_PIVOT, a)
    if first is not None and second is not None:
        return (first, second)
    missing = [
        name
        for name, sym in ((f"{_PIVOT}{q}", first), (f"{a}{_PIVOT}", second))
        if sym is None
    ]
    raise FxSourceUnavailable(
        missing,
        f"{q}->{a} has no registered cross and no route through {_PIVOT}; "
        "register the pair in core/instruments.py first",
    )


def build_research_fx_source(
    store: Any,
    *,
    quote_currencies: Iterable[str],
    account_ccy: str = RESEARCH_ACCOUNT_CCY,
    max_staleness: pd.Timedelta = DEFAULT_MAX_STALENESS,
    kind: Any = None,
) -> tuple[SeriesFxSource, str]:
    """A :class:`SeriesFxSource` of DAILY rates from the store, and its label.

    Each pair is the latest validated ``D1`` dataset, each close indexed at the
    bar's CLOSE time (bar open + 1 day), not its open: a daily bar stamped
    2024-01-05 00:00 is not known until 2024-01-06 00:00, and as-of lookup at
    2024-01-05 10:00 must not read it. When a pair has no ``D1`` dataset, its
    validated ``H4`` (else ``H1``) bars are reduced to one rate per UTC day,
    the last bar to close that day, stamped at that bar's close
    (:func:`~fiboki.core.money.daily_rates_from_intraday_closes`); the label and
    ``source.lineage`` say so. ``max_staleness`` is four days (a weekend plus
    one holiday).

    USD fallback: for every quote currency converted through a direct cross
    (JPY->GBP via GBPJPY), the two USD legs (USDJPY and GBPUSD) are loaded as
    well when the store has them, and the source is built with
    ``fallback_via_pivot=True``: the direct cross answers whenever it has a
    fresh rate, and only where it has none (it starts later than the
    instrument, or has a gap over ``max_staleness``) is the product of the two
    legs used, each leg staleness-checked, recorded as route ``via_usd``. A
    missing USD leg is not a refusal; the direct cross is then the only route
    and ``source.lineage['_fallback']`` records which fallbacks exist.

    Refuses with :class:`FxSourceUnavailable`, listing EVERY missing primary
    pair at once, rather than building a partial source that would fail cell
    by cell.

    Known approximation: the store's FX bars are usually HistData BID closes,
    so each rate is a bid rather than a mid -- an error of half a spread on the
    conversion rate (about 0.005% on GBPUSD), not on the trade.
    """
    from fiboki.core.money import daily_rates_from_intraday_closes, route_via
    from fiboki.data.schema import DatasetKind

    dkind = kind if kind is not None else DatasetKind.VALIDATED
    account = account_ccy.upper()
    needed: dict[str, None] = {}
    fallback_legs: dict[str, tuple[str, str]] = {}
    unroutable: list[str] = []
    for ccy in sorted({c.upper() for c in quote_currencies}):
        try:
            pairs = research_fx_pairs(ccy, account)
        except FxSourceUnavailable as exc:
            unroutable.extend(exc.missing)
            continue
        for sym in pairs:
            needed.setdefault(sym, None)
        if len(pairs) == 1 and _PIVOT not in (ccy, account):
            first, second = _pair_symbol(ccy, _PIVOT), _pair_symbol(_PIVOT, account)
            if first is not None and second is not None:
                fallback_legs[ccy] = (first, second)
    if unroutable:
        raise FxSourceUnavailable(unroutable, "no registered route exists")

    def _load(sym: str) -> tuple[pd.Series, str, dict[str, Any]] | None:
        for tf in (Timeframe.D1, Timeframe.H4, Timeframe.H1):
            try:
                frame, version = store.read_latest(sym, tf, kind=dkind)
            except Exception:  # DatasetNotFound, or an unreadable dataset
                continue
            if frame is None or len(frame) == 0:
                continue
            vid = str(getattr(version, "version_id", version))
            if tf is Timeframe.D1:
                known_at = frame.index + pd.Timedelta(days=1)
                series = pd.Series(frame["close"].to_numpy(dtype=float), index=known_at, name=sym)
                derivation = "D1 closes stamped at bar close (open + 1 day)"
                tag = f"{sym}@{vid}"
            else:
                series = daily_rates_from_intraday_closes(frame, tf.minutes, name=sym)
                derivation = (
                    f"derived from {tf.value} closes: the last {tf.value} bar to close in "
                    "each UTC day, stamped at that bar's close"
                )
                tag = f"{sym}@{vid}[derived:{tf.value} last close per UTC day]"
            return series, tag, {
                "dataset_version_id": vid,
                "source_timeframe": tf.value,
                "derivation": derivation,
                "first_known": str(series.index[0]),
                "last_known": str(series.index[-1]),
                "observations": int(len(series)),
            }
        return None

    series: dict[str, pd.Series] = {}
    lineage: dict[str, dict[str, Any]] = {}
    tags: list[str] = []
    missing: list[str] = []
    for sym in needed:
        loaded = _load(sym)
        if loaded is None:
            missing.append(sym)
            continue
        series[sym], tag, lineage[sym] = loaded
        tags.append(tag)
    if missing:
        raise FxSourceUnavailable(
            missing,
            "a validated D1 dataset is preferred; H4 or H1 bars of the same pair are "
            "accepted and reduced to daily closes",
        )
    fallback_state: dict[str, Any] = {}
    fallback_tags: list[str] = []
    for ccy, legs in sorted(fallback_legs.items()):
        absent = []
        for sym in legs:
            if sym in series:
                continue
            loaded = _load(sym)
            if loaded is None:
                absent.append(sym)
                continue
            series[sym], tag, lineage[sym] = loaded
            fallback_tags.append(tag)
        fallback_state[ccy] = {
            "route": route_via(_PIVOT),
            "legs": list(legs),
            "available": not absent,
            "missing_legs": absent,
        }
    lineage["_fallback"] = fallback_state
    available = sorted(c for c, v in fallback_state.items() if v["available"])
    label = (
        f"SeriesFxSource({account}; daily closes as-of bar close; "
        f"max_staleness={max_staleness}; pivot={_PIVOT}; "
        + ", ".join(tags)
        + (
            f"; fallback {route_via(_PIVOT)} where the direct cross has no fresh rate for "
            + ", ".join(available)
            + (" using " + ", ".join(fallback_tags) if fallback_tags else "")
            if available
            else ("; no via_usd fallback" if fallback_state else "")
        )
        + ")"
    )
    source = SeriesFxSource(
        series=series,
        max_staleness=max_staleness,
        pivot=_PIVOT,
        fallback_via_pivot=True,
        lineage=lineage,
    )
    return source, label


def research_mid_frame(
    frame: pd.DataFrame, symbol: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Bars the engine can trade, and the record of how they got there.

    * ``mid`` / ``synthetic_mid``: returned unchanged.
    * ``bid``: converted with
      :func:`fiboki.data.providers.histdata.bid_to_mid` at the instrument's
      REGISTERED typical spread, and stamped ``synthetic_mid``. The lineage
      records the source basis, the converter and the spread, and enters the
      engine fingerprint, so a converted run is never confused with a run on
      quoted mids.
    * ``ask`` / ``last``: refused. There is no converter, and a guessed one
      would be a guessed half-spread on every fill.
    * no ``price_basis`` column: returned unchanged, lineage ``assumed_mid``.
    """
    if "price_basis" not in frame.columns:
        return frame, {"basis": "assumed_mid"}
    bases = sorted({str(getattr(b, "value", b)).lower() for b in frame["price_basis"].unique()})
    if bases in (["mid"], ["synthetic_mid"]):
        return frame, {"basis": bases[0]}
    if bases == ["bid"]:
        spec = get_instrument(symbol)
        converted = bid_to_mid(
            frame, assumed_spread_pips=spec.typical_spread_pips, pip_size=spec.pip_size
        )
        return converted, {
            "basis": "synthetic_mid",
            "from": "bid",
            "via": "bid_to_mid",
            "assumed_spread_pips": float(spec.typical_spread_pips),
            "pip_size": float(spec.pip_size),
        }
    raise ValueError(
        f"{symbol}: bars are priced on {bases}; only mid, synthetic_mid or bid "
        "(converted with bid_to_mid) can be validated. The fill model assumes "
        "mid bars."
    )

_log = logging.getLogger(__name__)


def _resolve_construction(construction: Any) -> Any:
    if isinstance(construction, str):
        if construction != RESEARCH_CONSTRUCTION:
            raise ValueError(
                f"construction={construction!r}: pass a ConstructionPolicy, None, or "
                f"{RESEARCH_CONSTRUCTION!r}"
            )
        return research_construction_policy()
    return construction


def _declares_calendar_blackout(document: StrategyDocument) -> bool:
    ev = document.events
    return int(ev.block_minutes_before) > 0 or int(ev.block_minutes_after) > 0


@dataclass(frozen=True, slots=True)
class ValidationRun:
    """The report, plus the machinery that produced it.

    Returned rather than just the report so a caller can inspect the evaluator's
    cache statistics, count how many engine runs a campaign actually cost, and
    read back the exact grid that was swept.
    """

    report: ValidationReport
    evaluator: EngineEvaluator = field(repr=False)
    registry: HoldoutRegistry = field(repr=False)
    candidate: Any = field(repr=False)

    @property
    def verdict(self) -> Any:
        return self.report.verdict

    def summary(self) -> str:
        return self.report.summary()


def run_validation(
    *,
    document: StrategyDocument,
    bars: pd.DataFrame,
    dataset_version_id: str,
    instrument: str,
    timeframe: Timeframe | str,
    registry: HoldoutRegistry,
    profile_name: str = "IG_REALISTIC",
    account_ccy: str = RESEARCH_ACCOUNT_CCY,
    fx: FxRateSource | None = None,
    fx_label: str = "",
    fx_store: Any = None,
    initial_balance: float = 10_000.0,
    risk_fraction: float = 0.01,
    gate_set: GateSet = PRODUCTION_GATE_SET,
    ladder_config: LadderConfig | None = None,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    max_grid_points: int = 32,
    max_values_per_axis: int = 3,
    sweep_parameters: Iterable[str] | None = None,
    cache_dir: str | Path | None = None,
    evaluator_overrides: Mapping[str, Any] | None = None,
    experiment_id: str = "",
    actor: str = "",
    notes: str = "",
    calendar: EconomicCalendar | None = None,
    allow_empty_calendar: bool = False,
    construction: Any = RESEARCH_CONSTRUCTION,
) -> ValidationRun:
    """Run every rung of the ladder against the real engine and return the report.

    ``registry`` is passed in rather than created here on purpose: the holdout
    ledger is the one piece of state that MUST outlive a single run. A registry
    created inside this function would give every process a fresh, unspent
    holdout, which is exactly the property the registry exists to deny.

    ``calendar`` is handed to the engine as its blackout source. Before
    anything runs it must pass ``assert_populated`` over the bars' span and
    the instrument's currencies; ``allow_empty_calendar=True`` is the explicit,
    recorded opt-out.

    ``construction`` defaults to the paper runtime's portfolio construction, so
    a validated strategy is sized in research exactly as paper will size it;
    ``None`` is the flat ``risk_fraction`` path. It enters the engine
    fingerprint recorded on the report and every evaluation.
    """
    tf = Timeframe(timeframe) if not isinstance(timeframe, Timeframe) else timeframe
    symbol = instrument.upper()
    spec = get_instrument(symbol)

    if fx is None and fx_store is not None and spec.quote.upper() != account_ccy.upper():
        fx, built_label = build_research_fx_source(
            fx_store, quote_currencies=(spec.quote,), account_ccy=account_ccy
        )
        fx_label = fx_label or built_label
    if fx is None:
        if spec.quote.upper() != account_ccy.upper():
            raise ValueError(
                f"{symbol} is quoted in {spec.quote} but the account currency is "
                f"{account_ccy} and no FX source was supplied. Pass one (or "
                "fx_store=<DataStore> to build it from the stored GBP crosses with "
                f"build_research_fx_source), or run in {spec.quote}. A 1.0 "
                "conversion here would mis-state every monetary figure in the "
                "report by the exchange rate -- the exact V1 defect "
                "fiboki.core.money exists to prevent."
            )
        fx = IdentityFxSource()
        fx_label = fx_label or f"identity({spec.quote}=={account_ccy.upper()})"

    # BID bars become synthetic mid at the instrument's typical spread, and the
    # conversion is recorded; the engine would refuse them otherwise.
    bars, price_lineage = research_mid_frame(bars, symbol)

    if calendar is not None and not allow_empty_calendar:
        # Raises CalendarError carrying USER_ACTION_NOTE: an empty or
        # non-covering calendar would answer "not in blackout" for every bar.
        calendar.assert_populated(
            start=bars.index[0],
            end=bars.index[-1],
            currencies=instrument_currencies(symbol),
        )
    if calendar is None and _declares_calendar_blackout(document):
        _log.warning(
            "validation without an economic calendar: %s declares an event "
            "blackout but none will be applied; results trade through every "
            "scheduled release",
            document.strategy_id,
            extra={"strategy_id": document.strategy_id, "instrument": symbol},
        )
    # No per-calendar cache subdirectory: EngineEvaluator puts the calendar's
    # content fingerprint into engine_config_hash and so into every cache key.

    config = EvaluatorConfig(
        instrument=symbol,
        timeframe=tf,
        initial_balance=initial_balance,
        account_ccy=account_ccy,
        risk_fraction=risk_fraction,
        profile_name=profile_name,
        **dict(evaluator_overrides or {}),
    )
    evaluator = EngineEvaluator(
        document=document,
        frame=bars,
        dataset_version_id=dataset_version_id,
        config=config,
        fx=fx,
        fx_label=fx_label or type(fx).__name__,
        cache=EvaluationCache(cache_dir),
        blackout=calendar,
        price_basis_lineage=price_lineage,
        construction=_resolve_construction(construction),
    )

    # The segment is fixed from the DATA, before a single evaluation runs, and
    # the registry refuses to redefine it later with different facts.
    registry.define(
        dataset_version_id,
        data_start=bars.index[0],
        data_end=bars.index[-1],
        holdout_fraction=holdout_fraction,
        label=f"{symbol} {tf.value}",
    )

    candidate = evaluator.candidate(
        max_points=max_grid_points,
        max_values_per_axis=max_values_per_axis,
        include=sweep_parameters,
    )
    ladder = ValidationLadder(
        config=ladder_config or LadderConfig(min_trades=sanity_trade_floor(gate_set)),
        gate_set=gate_set,
    )
    report = ladder.run(
        candidate,
        evaluator,
        registry=registry,
        dataset_version_id=dataset_version_id,
        engine_config=evaluator.engine_fingerprint(),
        broker_profile=config.profile.fingerprint(),
        experiment_id=experiment_id,
        actor=actor,
        notes=notes,
    )
    return ValidationRun(
        report=report, evaluator=evaluator, registry=registry, candidate=candidate
    )
