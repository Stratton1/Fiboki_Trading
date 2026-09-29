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
from fiboki.validation.gates import GATE_SET_V2, GateSet
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
    GBPJPY, EUR->GBP is EURGBP), else one triangulation through USD (NZD->GBP
    is NZDUSD and GBPUSD). Raises :class:`FxSourceUnavailable` naming the pair
    that would have to be REGISTERED when even that is impossible (HKD: there
    is no USDHKD in ``core/instruments.py``).
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
    """A :class:`SeriesFxSource` from the store's DAILY closes, and its label.

    Every series is the latest validated ``D1`` dataset of the pair, and each
    close is indexed at the bar's CLOSE time (bar open + 1 day), not its open:
    a daily bar stamped 2024-01-05 00:00 is not known until 2024-01-06 00:00,
    and as-of lookup at 2024-01-05 10:00 must not read it. ``max_staleness`` is
    four days (a weekend plus one holiday). The label names every pair and its
    dataset version, because the source object cannot go into a JSON report.

    Refuses with :class:`FxSourceUnavailable`, listing EVERY missing pair at
    once, rather than building a partial source that would fail cell by cell.

    Known approximation: the store's FX bars are usually HistData BID closes,
    so each rate is a bid rather than a mid -- an error of half a spread on the
    conversion rate (about 0.005% on GBPUSD), not on the trade.
    """
    from fiboki.data.schema import DatasetKind

    dkind = kind if kind is not None else DatasetKind.VALIDATED
    needed: dict[str, None] = {}
    unroutable: list[str] = []
    for ccy in sorted({c.upper() for c in quote_currencies}):
        try:
            for sym in research_fx_pairs(ccy, account_ccy):
                needed.setdefault(sym, None)
        except FxSourceUnavailable as exc:
            unroutable.extend(exc.missing)
    if unroutable:
        raise FxSourceUnavailable(unroutable, "no registered route exists")
    series: dict[str, pd.Series] = {}
    versions: list[str] = []
    missing: list[str] = []
    for sym in needed:
        try:
            frame, version = store.read_latest(sym, Timeframe.D1, kind=dkind)
        except Exception:  # DatasetNotFound, or an unreadable dataset
            missing.append(sym)
            continue
        if frame is None or len(frame) == 0:
            missing.append(sym)
            continue
        known_at = frame.index + pd.Timedelta(days=1)
        series[sym] = pd.Series(
            frame["close"].to_numpy(dtype=float), index=known_at, name=sym
        )
        versions.append(f"{sym}@{getattr(version, 'version_id', version)}")
    if missing:
        raise FxSourceUnavailable(missing)
    label = (
        f"SeriesFxSource({account_ccy.upper()}; D1 closes as-of bar close; "
        f"max_staleness={max_staleness}; pivot={_PIVOT}; "
        + ", ".join(versions)
        + ")"
    )
    return SeriesFxSource(series=series, max_staleness=max_staleness, pivot=_PIVOT), label


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
    gate_set: GateSet = GATE_SET_V2,
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
        config=ladder_config or LadderConfig(min_trades=int(gate_set.by_name("min_trades").threshold)),
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
