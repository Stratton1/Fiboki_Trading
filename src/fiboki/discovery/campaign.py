"""The campaign runner: a search that knows how big it is.

A campaign takes a set of hypotheses, a universe, a timeframe set, a broker
profile and a compute budget, and turns them into a population of candidates,
each of which is proposed, checked for novelty, queued, run through the
validation ladder, and recorded in the experiment ledger with its lineage
intact.

THE CORRECTNESS REQUIREMENT
---------------------------
The deflated Sharpe corrects a result for the size of the search that produced
it. The ladder can see how many parameterisations of ONE strategy it swept; it
cannot see that the same operator also searched eleven other strategies on eight
instruments over two timeframes. V1 could not see it either, which is precisely
why its 23,040-cell leaderboard was meaningless: every cell was deflated as if
it were the only thing ever tried.

So the campaign computes the TRUE trial count before running anything --

    true N = trials planned across every queued candidate
             + trials the ledger already holds for the same dataset version(s)

-- and hands each candidate ``external_trial_count = true N - its own trials``
through :class:`~fiboki.validation.ladder.LadderConfig`. The ladder ADDS that to
its own clustered effective count before deflating. Two deliberate
conservatisms, both in the direction that makes a false discovery HARDER:

* the external trials are not clustered down (clustering needs aligned return
  series across candidates, which do not exist), so the external count is a raw
  count and therefore an upper bound on their independence;
* skipped rediscoveries are not counted, because they spent no compute HERE --
  their trials are already in the prior count if they are in the ledger.

Economic calendar
-----------------
Every cell runs against the committed official calendar by default (see
:func:`run_cell`), so a document's declared event blackout is enforced in
research exactly as it is in the engine. Until this was wired no calendar ever
reached a campaign cell, which means every earlier campaign result for a
document that declares a blackout was produced WITHOUT that blackout and is
superseded.

Resume
------
A campaign of several hundred evaluations takes hours, and a process that dies
must not start again from nothing or, far worse, silently skip what it never
finished. The checkpoint records one row per cell, and only two statuses count
as complete: a cell that produced a report, and a cell that was deliberately
skipped. A cell that returned NO DATA is explicitly NOT complete -- V1
checkpointed no-data cells as done, so an instrument whose feed was briefly
missing was permanently excluded from every later run and nothing said so.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import FxRateSource
from fiboki.discovery.hypothesis import Hypothesis, HypothesisLedger
from fiboki.discovery.mutation import MutationEngine, MutationProposal, mutation_lineage
from fiboki.discovery.novelty import NoveltyIndex, NoveltyVerdict
from fiboki.discovery.report import CampaignReport, CellResult, SkippedCell, deflation_threshold
from fiboki.marketstate.calendar import EconomicCalendar, load_official_calendar
from fiboki.research.experiment import ActorKind, ExperimentDraft, ExperimentLedger, Outcome
from fiboki.strategy.dsl import StrategyDocument, strategy_key_version
from fiboki.validation.evaluation import ParameterGrid
from fiboki.validation.gates import GATE_SET_V2, GateSet
from fiboki.validation.holdout import DEFAULT_HOLDOUT_FRACTION, HoldoutRegistry
from fiboki.validation.ladder import LadderConfig
from fiboki.validation.report import ValidationReport
from fiboki.validation.run import (
    RESEARCH_ACCOUNT_CCY,
    build_research_fx_source,
    run_validation,
)

__all__ = [
    "BarSet",
    "BarSource",
    "CampaignCheckpoint",
    "CampaignPlan",
    "CampaignRunner",
    "CampaignSpec",
    "CandidateCell",
    "CellStatus",
]


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BarSet:
    """Bars for one cell, with the dataset version that produced them."""

    instrument: str
    timeframe: Timeframe
    frame: pd.DataFrame = field(repr=False)
    dataset_version_id: str

    def __post_init__(self) -> None:
        if not self.dataset_version_id:
            raise ValueError(
                "a BarSet must name its dataset version: numbers that cannot name "
                "the bytes they came from are not reproducible and are not evidence"
            )


class BarSource(Protocol):
    """Supplies bars for a cell, or ``None`` when there are none.

    Returning ``None`` rather than raising is the contract that makes the
    no-data case a first-class outcome: the campaign records the cell as
    incomplete and will retry it, instead of treating an empty feed as a
    finished piece of work.
    """

    def __call__(self, instrument: str, timeframe: Timeframe) -> BarSet | None: ...


# --------------------------------------------------------------------------
# Specification
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CampaignSpec:
    """Everything a campaign is, before it has run."""

    campaign_id: str
    universe: tuple[str, ...]
    timeframes: tuple[Timeframe, ...]
    hypotheses: tuple[Hypothesis, ...] = ()
    actor: str = "discovery-campaign"
    actor_kind: ActorKind = ActorKind.AGENT

    profile_name: str = "IG_REALISTIC"
    #: GBP, the operator's account currency, so campaign figures are comparable
    #: with paper figures. A campaign over a store-backed bar source builds its
    #: rate source from the stored GBP crosses (see :class:`CampaignRunner`).
    account_ccy: str = RESEARCH_ACCOUNT_CCY
    initial_balance: float = 10_000.0
    risk_fraction: float = 0.01
    gate_set: GateSet = GATE_SET_V2
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION

    max_evaluations: int = 400
    """Compute budget, in TRIALS (parameterisations), not engine runs. One trial
    costs several engine runs because the ladder re-sweeps per walk-forward
    fold; the budget is stated in trials because that is the unit the deflation
    counts and therefore the unit a reader has to be able to check."""

    max_grid_points: int = 8
    max_values_per_axis: int = 2
    sweep_parameters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    """``strategy_id -> axes to sweep``. Grid capping never DROPS an axis (that
    would make the report describe a sweep that did not happen), so a
    seven-parameter document has a 128-point floor; a campaign that wants an
    eight-point grid has to say out loud which axes it swept."""

    walk_forward_folds: int = 4
    stress_samples: int = 100
    spa_bootstraps: int = 400
    selection_metric: str = "sharpe"
    seed: int = 20260919

    max_generations: int = 1
    mutation_operators: tuple[str, ...] = ()
    """Empty means every operator in :data:`~fiboki.discovery.mutation.MUTATION_OPERATORS`."""

    include_prior_trials: bool = True
    """Count trials the ledger already holds for the same dataset version(s) in
    the true trial count. On by default: those trials were part of the search
    that this campaign's results have to be corrected for."""

    external_prior_trials: int = 0
    """Trials spent on THESE BARS that the ledger cannot see, declared by hand.

    A :class:`~fiboki.data.versioning.DatasetVersion` id hashes the content
    checksum together with its lineage, so re-ingesting identical bytes through
    a different path mints a NEW id for the SAME BARS. The ledger keys prior
    trials on the version id, so after a re-ingest it reports zero prior trials
    for a series that has in fact been searched hundreds of times -- which is
    the under-counting Phase K exists to prevent, in a new form.

    This field is the escape hatch, and it is deliberately blunt: it can only
    ADD trials, which can only make the deflation harder, and it is refused
    without a reason that a reader can check. It is not a tuning knob.
    """

    external_prior_trials_reason: str = ""
    """Why :attr:`external_prior_trials` is what it is. Mandatory when non-zero:
    a trial count nobody can reconstruct is the defect this phase exists to fix."""

    fx_label: str = ""
    """What converts an instrument's QUOTE currency into :attr:`account_ccy`.

    A campaign over a real universe is mostly NOT quoted in its account
    currency: of sixteen HistData series, nine are quoted in JPY, GBP, CHF, CAD
    or EUR. ``run_validation`` refuses to run those without an FX source rather
    than applying a 1.0 that would mis-state every monetary figure by the
    exchange rate, so a multi-instrument campaign has to supply one -- and this
    field is the serialisable record of WHICH one, since the source object
    itself is passed to the runner and cannot go in a JSON report.
    """

    allow_empty_calendar: bool = False
    """Run cells whose bars or currencies the economic calendar does not cover.

    Off by default: every cell runs against the official calendar
    (:func:`~fiboki.marketstate.calendar.load_official_calendar`) and a cell it
    cannot vouch for is REFUSED rather than validated as if no scheduled
    release ever happened. On, the calendar is still applied wherever it has
    events; only the coverage refusal is lifted. Serialised, so a report says
    which of the two it was produced under.
    """

    notes: str = ""

    def __post_init__(self) -> None:
        if self.external_prior_trials < 0:
            raise ValueError(
                "external_prior_trials cannot be negative: this field exists to "
                "remember trials the ledger has forgotten, never to discount them"
            )
        if self.external_prior_trials and not self.external_prior_trials_reason.strip():
            raise ValueError(
                "external_prior_trials was set without a reason. State where those "
                "trials were spent and how you know they were spent on these bars; "
                "an undeclared trial count is a silent one."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "universe": list(self.universe),
            "timeframes": [t.value for t in self.timeframes],
            "hypothesis_ids": [h.hypothesis_id for h in self.hypotheses],
            "actor": self.actor,
            "profile_name": self.profile_name,
            "account_ccy": self.account_ccy,
            "initial_balance": float(self.initial_balance),
            "risk_fraction": float(self.risk_fraction),
            "gate_set_version": self.gate_set.version,
            "holdout_fraction": float(self.holdout_fraction),
            "max_evaluations": int(self.max_evaluations),
            "max_grid_points": int(self.max_grid_points),
            "max_values_per_axis": int(self.max_values_per_axis),
            "sweep_parameters": {k: list(v) for k, v in self.sweep_parameters.items()},
            "walk_forward_folds": int(self.walk_forward_folds),
            "selection_metric": self.selection_metric,
            "seed": int(self.seed),
            "max_generations": int(self.max_generations),
            "mutation_operators": list(self.mutation_operators),
            "include_prior_trials": bool(self.include_prior_trials),
            "external_prior_trials": int(self.external_prior_trials),
            "external_prior_trials_reason": self.external_prior_trials_reason,
            "fx_label": self.fx_label,
            "allow_empty_calendar": bool(self.allow_empty_calendar),
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# Candidates and the plan
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateCell:
    """One document on one instrument and timeframe: the unit of work."""

    document: StrategyDocument = field(repr=False)
    instrument: str
    timeframe: Timeframe
    origin: str
    """``seed`` or the mutation operator that produced the document."""
    hypothesis_id: str = ""
    rationale: str = ""
    parents: tuple[str, ...] = ()
    grid: ParameterGrid = field(default_factory=ParameterGrid)
    novelty: NoveltyVerdict | None = field(default=None, repr=False)

    @property
    def strategy_id(self) -> str:
        return self.document.strategy_id

    @property
    def content_hash(self) -> str:
        return self.document.content_hash()

    @property
    def generation(self) -> int:
        return 0 if self.document.mutation is None else int(self.document.mutation.generation)

    @property
    def n_trials(self) -> int:
        """Parameterisations the ladder will sweep for this cell."""
        return max(1, self.grid.full_size())

    @property
    def key(self) -> str:
        """Stable identity of this unit of work, for the checkpoint.

        Keyed on the CONTENT hash rather than the strategy id, so a renamed
        document resumes as the same cell, and on the swept axes, so widening
        the grid is correctly a different piece of work.

        The key version is part of the blob because the content hash is derived
        from the DSL schema: bumping the schema moves every content hash, so keys
        minted before and after a bump must not be silently assumed to name the
        same work. Mixing them in the blob makes them visibly different keys --
        the checkpoint re-runs the cell rather than resuming from a row that
        described a strategy under a vocabulary that no longer exists.
        """
        blob = "|".join(
            (
                strategy_key_version(),
                self.content_hash,
                self.instrument.upper(),
                self.timeframe.value,
                ",".join(self.grid.names),
                str(self.n_trials),
            )
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]

    def describe(self) -> str:
        return (
            f"{self.strategy_id} [{self.content_hash[:12]}] on {self.instrument} "
            f"{self.timeframe.value} via {self.origin} ({self.n_trials} trials)"
        )


@dataclass(frozen=True, slots=True)
class CampaignPlan:
    """The queued work and the trial accounting, fixed BEFORE anything runs."""

    cells: tuple[CandidateCell, ...] = ()
    skipped: tuple[SkippedCell, ...] = ()
    rejected_mutations: tuple[MutationProposal, ...] = field(default=(), repr=False)
    prior_trial_count: int = 0
    """Trials the LEDGER holds for the dataset versions in play."""
    dataset_versions: dict[str, str] = field(default_factory=dict)
    external_prior_trial_count: int = 0
    """Trials on these bars that the ledger cannot see, declared on the spec."""
    external_prior_trials_reason: str = ""

    @property
    def planned_trial_count(self) -> int:
        return sum(c.n_trials for c in self.cells)

    @property
    def total_prior_trial_count(self) -> int:
        """Everything tried before this campaign, from both sources."""
        return self.prior_trial_count + self.external_prior_trial_count

    @property
    def true_trial_count(self) -> int:
        """Every parameterisation this search will have tried, plus the history."""
        return self.planned_trial_count + self.total_prior_trial_count

    def external_trials_for(self, cell: CandidateCell) -> int:
        """Trials run OUTSIDE this candidate's own sweep. Never negative.

        This is the number the ladder needs and cannot compute: it is the whole
        search minus the part of it the ladder can already see.
        """
        return max(0, self.true_trial_count - cell.n_trials)

    def describe(self) -> str:
        lines = [
            f"{len(self.cells)} cells queued, {len(self.skipped)} skipped, "
            f"{len(self.rejected_mutations)} mutations refused",
            f"planned trials {self.planned_trial_count} "
            f"+ ledger prior {self.prior_trial_count} "
            f"+ declared prior {self.external_prior_trial_count} "
            f"= TRUE TRIAL COUNT {self.true_trial_count}",
        ]
        if self.external_prior_trial_count:
            lines.append(
                f"  declared prior trials: {self.external_prior_trials_reason}"
            )
        lines += [f"  {c.describe()}" for c in self.cells]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Checkpoint
# --------------------------------------------------------------------------


class CellStatus(str, Enum):
    """How a cell ended. Only two of these mean "do not do this again"."""

    COMPLETED = "completed"
    SKIPPED = "skipped"
    NO_DATA = "no_data"
    ERROR = "error"

    @property
    def is_complete(self) -> bool:
        """NO_DATA and ERROR are deliberately NOT complete.

        V1 wrote a checkpoint row for a cell that returned no data and then
        treated that row as done forever, so a temporary gap in a feed silently
        removed an instrument from every subsequent run. A cell is complete only
        when it produced a result or was deliberately declined.
        """
        return self in (CellStatus.COMPLETED, CellStatus.SKIPPED)


@dataclass(slots=True)
class CampaignCheckpoint:
    """A JSON file of one row per cell. Small, readable, and append-in-place.

    Not a database: a campaign checkpoint has to be inspectable by a person at
    3am with a text editor, and the write volume is a few hundred rows.
    """

    path: Path | None = None
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path) if self.path is not None else None
        if self.path is not None and self.path.exists():
            self.rows = dict(json.loads(self.path.read_text(encoding="utf-8")))

    @classmethod
    def in_memory(cls) -> CampaignCheckpoint:
        return cls(None)

    def record(
        self, key: str, status: CellStatus, payload: Mapping[str, Any] | None = None
    ) -> None:
        self.rows[str(key)] = {
            "status": status.value,
            "recorded_at": datetime.now(tz=UTC).isoformat(),
            **dict(payload or {}),
        }
        self._flush()

    def status(self, key: str) -> CellStatus | None:
        row = self.rows.get(str(key))
        return CellStatus(row["status"]) if row else None

    def payload(self, key: str) -> dict[str, Any]:
        return dict(self.rows.get(str(key), {}))

    def is_complete(self, key: str) -> bool:
        status = self.status(key)
        return status is not None and status.is_complete

    def incomplete_keys(self) -> tuple[str, ...]:
        return tuple(k for k in sorted(self.rows) if not self.is_complete(k))

    def _flush(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rows, indent=2, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------
# Validation seam
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CellOutcome:
    """What running one cell produced."""

    report: ValidationReport | None = None
    n_evaluations: int = 0
    engine_runs: int = 0
    error: str = ""


class CellValidator(Protocol):
    """Runs one cell through the ladder. Injected so a campaign is testable.

    The default implementation is :func:`run_cell`, which calls
    :func:`fiboki.validation.run.run_validation`. A test substitutes one that
    records the :class:`~fiboki.validation.ladder.LadderConfig` it was handed,
    which is how the external trial count's propagation is asserted rather than
    assumed.
    """

    def __call__(
        self,
        *,
        cell: CandidateCell,
        bars: BarSet,
        spec: CampaignSpec,
        ladder_config: LadderConfig,
        registry: HoldoutRegistry,
        experiment_id: str,
        notes: str,
    ) -> CellOutcome: ...


def run_cell(
    *,
    cell: CandidateCell,
    bars: BarSet,
    spec: CampaignSpec,
    ladder_config: LadderConfig,
    registry: HoldoutRegistry,
    experiment_id: str = "",
    notes: str = "",
    cache_dir: str | Path | None = None,
    fx: FxRateSource | None = None,
    fx_label: str = "",
    calendar: EconomicCalendar | None = None,
) -> CellOutcome:
    """The production validator: the real engine, through the real ladder.

    ``fx`` is forwarded rather than defaulted here. ``run_validation`` builds an
    :class:`~fiboki.core.money.IdentityFxSource` only when the instrument's quote
    currency already equals the account currency, and raises otherwise -- so
    passing ``None`` for a JPY-quoted instrument in a USD account is a refusal,
    not a silent 1.0.

    ``calendar`` is the blackout source. ``None`` means the committed official
    calendar, loaded here, so a document's declared event blackout is enforced
    by default. Before this, no calendar reached ``run_validation`` from a
    campaign and every blackout was silently skipped. ``run_validation`` then
    refuses a cell the calendar does not cover unless
    ``spec.allow_empty_calendar``. To run with no calendar at all, pass
    ``InMemoryEconomicCalendar.empty()`` with ``spec.allow_empty_calendar``:
    an explicit empty calendar is forwarded as ``None``, which
    ``run_validation`` logs loudly for any document that declares a blackout.
    """
    try:
        source = calendar if calendar is not None else load_official_calendar()
        if not source.all_events():
            source = None
        run = run_validation(
            document=cell.document,
            bars=bars.frame,
            dataset_version_id=bars.dataset_version_id,
            instrument=cell.instrument,
            timeframe=cell.timeframe,
            registry=registry,
            profile_name=spec.profile_name,
            account_ccy=spec.account_ccy,
            initial_balance=spec.initial_balance,
            risk_fraction=spec.risk_fraction,
            gate_set=spec.gate_set,
            fx=fx,
            fx_label=fx_label or spec.fx_label,
            ladder_config=ladder_config,
            holdout_fraction=spec.holdout_fraction,
            max_grid_points=spec.max_grid_points,
            max_values_per_axis=spec.max_values_per_axis,
            sweep_parameters=spec.sweep_parameters.get(cell.document.strategy_id)
            or spec.sweep_parameters.get(_root_id(cell.document)),
            cache_dir=cache_dir,
            experiment_id=experiment_id,
            actor=spec.actor,
            notes=notes,
            calendar=source,
            allow_empty_calendar=bool(spec.allow_empty_calendar),
        )
    except Exception as exc:
        return CellOutcome(error=f"{type(exc).__name__}: {exc}")
    return CellOutcome(
        report=run.report,
        n_evaluations=int(run.report.windows.get("n_evaluations", 0) or 0),
        engine_runs=int(getattr(run.evaluator, "n_engine_runs", 0)),
    )


def _root_id(document: StrategyDocument) -> str:
    """The seed a mutant descends from, for looking up per-strategy settings."""
    return document.parent_strategy_ids[0] if document.parent_strategy_ids else document.strategy_id


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


class CampaignRunner:
    """Plans a campaign, runs it, and writes everything down."""

    def __init__(
        self,
        spec: CampaignSpec,
        *,
        bars: BarSource,
        ledger: ExperimentLedger,
        registry: HoldoutRegistry,
        checkpoint: CampaignCheckpoint | None = None,
        report_dir: str | Path | None = None,
        validator: CellValidator | Callable[..., CellOutcome] | None = None,
        mutation_engine: MutationEngine | None = None,
        novelty: NoveltyIndex | None = None,
        cache_dir: str | Path | None = None,
        fx: FxRateSource | None = None,
        calendar: EconomicCalendar | None = None,
    ) -> None:
        if fx is not None and not spec.fx_label.strip():
            raise ValueError(
                "an FX source was supplied but spec.fx_label is blank. The source "
                "object cannot go in a JSON report, so the label is the only record "
                "a reader gets of how quote currencies were converted; an undeclared "
                "conversion is a silent one."
            )
        self.spec = spec
        self.fx_label = spec.fx_label
        # No source supplied, a store-backed bar source, and instruments quoted
        # in something other than the account currency: build the source from
        # the store's daily GBP crosses NOW, so a missing cross refuses the
        # campaign up front with the list of instruments to ingest, instead of
        # failing cell by cell hours in.
        store = getattr(bars, "store", None)
        if fx is None and store is not None:
            foreign = {
                get_instrument(sym).quote.upper()
                for sym in spec.universe
            } - {spec.account_ccy.upper()}
            if foreign:
                fx, built = build_research_fx_source(
                    store, quote_currencies=foreign, account_ccy=spec.account_ccy
                )
                self.fx_label = spec.fx_label or built
        self.fx = fx
        self.bars = bars
        self.ledger = ledger
        self.registry = registry
        self.checkpoint = checkpoint or CampaignCheckpoint.in_memory()
        self.report_dir = Path(report_dir) if report_dir is not None else None
        self.mutations = mutation_engine or MutationEngine(seed=spec.seed)
        self.novelty = novelty or NoveltyIndex(ledger)
        self.hypotheses = HypothesisLedger(ledger)
        self.cache_dir = cache_dir
        #: Loaded lazily, once per runner, by the default validator; see
        #: :func:`run_cell` for what ``None`` means.
        self.calendar = calendar
        self._validator = validator or (
            lambda **kw: run_cell(
                cache_dir=self.cache_dir,
                fx=self.fx,
                fx_label=self.fx_label,
                calendar=self._calendar(),
                **kw,
            )
        )
        self._bar_cache: dict[tuple[str, str], BarSet | None] = {}

    def _calendar(self) -> EconomicCalendar:
        if self.calendar is None:
            self.calendar = load_official_calendar()
        return self.calendar

    # ----------------------------------------------------------------- plan

    def plan(self, seeds: Sequence[StrategyDocument]) -> CampaignPlan:
        """Propose, filter for novelty, and fix the trial accounting.

        Nothing is evaluated here. The plan exists before any compute is spent
        precisely so the true trial count is known before the first deflation --
        a count computed as the campaign goes along would be smaller for the
        first candidate than for the last, which is the V1 error in a new form.
        """
        cells: list[CandidateCell] = []
        skipped: list[SkippedCell] = []
        population = self._population(seeds, skipped)

        budget = int(self.spec.max_evaluations)
        used = 0
        for document, origin, rationale, parents, hypothesis_id in population:
            for instrument in self.spec.universe:
                for timeframe in self.spec.timeframes:
                    cell = self._build_cell(
                        document, instrument, timeframe, origin, rationale, parents, hypothesis_id
                    )
                    if cell is None:
                        skipped.append(
                            SkippedCell(
                                key=_cell_key(document, instrument, timeframe),
                                strategy_id=document.strategy_id,
                                kind="out_of_universe",
                                instrument=instrument,
                                timeframe=timeframe.value,
                                origin=origin,
                                reason=(
                                    f"{document.strategy_id} declares universe "
                                    f"{list(document.universe)} and timeframes "
                                    f"{[t.value for t in document.timeframes]}; running it on "
                                    f"{instrument} {timeframe.value} would validate a strategy "
                                    "nobody wrote"
                                ),
                            )
                        )
                        continue

                    dataset_version = self._dataset_version(instrument, timeframe)
                    verdict = self.novelty.assess(
                        document,
                        rationale=rationale,
                        dataset_version_id=dataset_version,
                        label=rationale or document.strategy_id,
                        # This campaign's own rows are not prior work. Without
                        # this, a resumed campaign would read the experiments it
                        # wrote before the crash as exact duplicates and skip its
                        # entire population -- a resume that silently does
                        # nothing is worse than one that recomputes.
                        exclude_tags=(self.spec.campaign_id,),
                    )
                    if not verdict.should_run:
                        skipped.append(
                            SkippedCell(
                                key=cell.key,
                                strategy_id=document.strategy_id,
                                kind="non_novel",
                                instrument=instrument,
                                timeframe=timeframe.value,
                                origin=origin,
                                reason=verdict.recommendation,
                                narrative=verdict.narrative(),
                                prior_experiment_ids=tuple(
                                    p.experiment_id for p in verdict.prior_attempts
                                ),
                                detail=verdict.to_dict(),
                            )
                        )
                        continue

                    if used + cell.n_trials > budget:
                        skipped.append(
                            SkippedCell(
                                key=cell.key,
                                strategy_id=document.strategy_id,
                                kind="over_budget",
                                instrument=instrument,
                                timeframe=timeframe.value,
                                origin=origin,
                                reason=(
                                    f"{used} of a {budget}-trial budget already committed; "
                                    f"this cell needs {cell.n_trials} more. Deferred rather "
                                    "than run at a smaller grid, because shrinking the grid "
                                    "would change what was tested"
                                ),
                            )
                        )
                        continue

                    used += cell.n_trials
                    cells.append(
                        CandidateCell(
                            document=cell.document,
                            instrument=cell.instrument,
                            timeframe=cell.timeframe,
                            origin=cell.origin,
                            hypothesis_id=cell.hypothesis_id,
                            rationale=cell.rationale,
                            parents=cell.parents,
                            grid=cell.grid,
                            novelty=verdict,
                        )
                    )

        return CampaignPlan(
            cells=tuple(cells),
            skipped=tuple(skipped),
            rejected_mutations=tuple(self.mutations.rejected),
            prior_trial_count=self._prior_trial_count(),
            dataset_versions=dict(self._known_dataset_versions()),
            external_prior_trial_count=int(self.spec.external_prior_trials),
            external_prior_trials_reason=self.spec.external_prior_trials_reason,
        )

    def _population(
        self,
        seeds: Sequence[StrategyDocument],
        skipped: list[SkippedCell],
    ) -> list[tuple[StrategyDocument, str, str, tuple[str, ...], str]]:
        """Seeds plus mutations, deterministic in order."""
        out: list[tuple[StrategyDocument, str, str, tuple[str, ...], str]] = []
        seen: set[str] = set()
        for seed in seeds:
            if seed.content_hash() in seen:
                continue
            seen.add(seed.content_hash())
            out.append((seed, "seed", f"seed strategy {seed.strategy_id}", (), self._hypothesis_for(seed)))

        generation_parents = list(seeds)
        for _generation in range(max(0, self.spec.max_generations)):
            proposals: list[MutationProposal] = []
            for parent in generation_parents:
                proposals.extend(
                    self.mutations.propose(
                        parent,
                        operators=self.spec.mutation_operators or None,
                        partners=[p for p in seeds if p.content_hash() != parent.content_hash()],
                    )
                )
            children: list[StrategyDocument] = []
            for proposal in proposals:
                if not proposal.accepted or proposal.document is None:
                    skipped.append(
                        SkippedCell(
                            key=f"mutation::{proposal.operator}::{'+'.join(proposal.parent_ids)}",
                            strategy_id=proposal.parent_ids[0] if proposal.parent_ids else "",
                            kind="mutation_rejected",
                            origin=proposal.operator,
                            reason=proposal.rejection,
                            detail=proposal.to_dict(),
                        )
                    )
                    continue
                if proposal.content_hash in seen:
                    continue
                seen.add(proposal.content_hash)
                children.append(proposal.document)
                out.append(
                    (
                        proposal.document,
                        proposal.operator,
                        proposal.rationale,
                        proposal.parents,
                        self._hypothesis_for(proposal.document),
                    )
                )
            generation_parents = children
            if not children:
                break
        return out

    def _hypothesis_for(self, document: StrategyDocument) -> str:
        """The first campaign hypothesis naming this strategy, or covering it."""
        root = _root_id(document)
        for hypothesis in self.spec.hypotheses:
            if document.strategy_id in hypothesis.seed_strategy_ids or root in hypothesis.seed_strategy_ids:
                return hypothesis.hypothesis_id
        return self.spec.hypotheses[0].hypothesis_id if self.spec.hypotheses else ""

    def _build_cell(
        self,
        document: StrategyDocument,
        instrument: str,
        timeframe: Timeframe,
        origin: str,
        rationale: str,
        parents: tuple[str, ...],
        hypothesis_id: str,
    ) -> CandidateCell | None:
        if instrument.upper() not in document.universe:
            return None
        if timeframe not in document.timeframes:
            return None
        grid = ParameterGrid.from_document(
            document,
            include=self.spec.sweep_parameters.get(document.strategy_id)
            or self.spec.sweep_parameters.get(_root_id(document)),
            max_values_per_axis=self.spec.max_values_per_axis,
            max_points=self.spec.max_grid_points,
        )
        return CandidateCell(
            document=document,
            instrument=instrument.upper(),
            timeframe=timeframe,
            origin=origin,
            hypothesis_id=hypothesis_id,
            rationale=rationale,
            parents=parents,
            grid=grid,
        )

    # ------------------------------------------------------------------ run

    def run(self, plan: CampaignPlan) -> CampaignReport:
        """Run every queued cell, honouring the checkpoint, and report."""
        results: list[CellResult] = []
        skipped = list(plan.skipped)
        engine_evaluations = 0

        for cell in plan.cells:
            if self.checkpoint.is_complete(cell.key):
                # Resume: the work is done and is NOT redone. The stored result
                # is replayed into this report so a resumed run produces the
                # same document as an uninterrupted one.
                payload = self.checkpoint.payload(cell.key)
                cached = payload.get("result")
                if cached:
                    results.append(CellResult.from_dict(cached))
                continue

            barset = self._bars_for(cell.instrument, cell.timeframe)
            if barset is None:
                self.checkpoint.record(
                    cell.key,
                    CellStatus.NO_DATA,
                    {
                        "strategy_id": cell.strategy_id,
                        "instrument": cell.instrument,
                        "timeframe": cell.timeframe.value,
                        "note": (
                            "no bars were available for this cell. NOT marked complete: "
                            "it will be retried, because a missing feed is a gap in the "
                            "search, not a result about the strategy."
                        ),
                    },
                )
                skipped.append(
                    SkippedCell(
                        key=cell.key,
                        strategy_id=cell.strategy_id,
                        kind="no_data",
                        instrument=cell.instrument,
                        timeframe=cell.timeframe.value,
                        origin=cell.origin,
                        reason=(
                            "no bars available; the cell is recorded as INCOMPLETE and "
                            "will be retried on resume"
                        ),
                    )
                )
                continue

            external = plan.external_trials_for(cell)
            ladder_config = self._ladder_config(external)
            outcome = self._validator(
                cell=cell,
                bars=barset,
                spec=self.spec,
                ladder_config=ladder_config,
                registry=self.registry,
                experiment_id="",
                notes=self._cell_notes(cell, plan, external),
            )
            engine_evaluations += int(outcome.engine_runs)

            if outcome.report is None:
                self.checkpoint.record(
                    cell.key,
                    CellStatus.ERROR,
                    {"strategy_id": cell.strategy_id, "error": outcome.error},
                )
                results.append(
                    CellResult(
                        key=cell.key,
                        strategy_id=cell.strategy_id,
                        strategy_content_hash=cell.content_hash,
                        instrument=cell.instrument,
                        timeframe=cell.timeframe.value,
                        origin=cell.origin,
                        hypothesis_id=cell.hypothesis_id,
                        parents=cell.parents,
                        generation=cell.generation,
                        rationale=cell.rationale,
                        n_trials=cell.n_trials,
                        external_trial_count=external,
                        dataset_version_id=barset.dataset_version_id,
                        error=outcome.error or "the validator returned no report",
                    )
                )
                continue

            experiment = self._record_experiment(cell, barset, outcome.report, external)
            result = self._cell_result(cell, barset, outcome, external, experiment.id)
            results.append(result)
            self.checkpoint.record(
                cell.key,
                CellStatus.COMPLETED,
                {"strategy_id": cell.strategy_id, "result": result.to_dict()},
            )

        # A skip is a RESULT and goes in the ledger, once. The checkpoint guard
        # is what makes it once: a resumed campaign must not re-file a decision
        # it already recorded, or the ledger would show the same dead end being
        # declined repeatedly and the count of prior work would drift upwards.
        for skip in plan.skipped:
            if skip.kind != "non_novel" or self.checkpoint.is_complete(skip.key):
                continue
            experiment = self._record_skip(skip)
            self.checkpoint.record(
                skip.key,
                CellStatus.SKIPPED,
                {
                    "kind": skip.kind,
                    "reason": skip.reason,
                    "experiment_id": getattr(experiment, "id", ""),
                },
            )

        return self._build_report(plan, tuple(results), tuple(skipped), engine_evaluations)

    # ------------------------------------------------------------ internals

    def _ladder_config(self, external_trial_count: int) -> LadderConfig:
        """The ladder settings for one cell, carrying the campaign's true N."""
        return LadderConfig(
            min_trades=int(self.spec.gate_set.by_name("min_trades").threshold),
            selection_metric=self.spec.selection_metric,
            walk_forward_folds=self.spec.walk_forward_folds,
            stress_samples=self.spec.stress_samples,
            spa_bootstraps=self.spec.spa_bootstraps,
            external_trial_count=int(external_trial_count),
            seed=self.spec.seed,
        )

    def _cell_notes(self, cell: CandidateCell, plan: CampaignPlan, external: int) -> str:
        return (
            f"campaign {self.spec.campaign_id}: {cell.describe()}. "
            f"Deflated against a TRUE trial count of {plan.true_trial_count} "
            f"({cell.n_trials} from this candidate's own sweep plus "
            f"{external} elsewhere in the search), not against this candidate's "
            "sweep alone."
        )

    def _bars_for(self, instrument: str, timeframe: Timeframe) -> BarSet | None:
        key = (instrument.upper(), timeframe.value)
        if key not in self._bar_cache:
            try:
                self._bar_cache[key] = self.bars(instrument.upper(), timeframe)
            except Exception:
                self._bar_cache[key] = None
        return self._bar_cache[key]

    def _dataset_version(self, instrument: str, timeframe: Timeframe) -> str:
        barset = self._bars_for(instrument, timeframe)
        return barset.dataset_version_id if barset is not None else ""

    def _known_dataset_versions(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for instrument in self.spec.universe:
            for timeframe in self.spec.timeframes:
                barset = self._bars_for(instrument, timeframe)
                if barset is not None:
                    out[f"{instrument.upper()}_{timeframe.value}"] = barset.dataset_version_id
        return out

    def _prior_trial_count(self) -> int:
        """Trials the ledger already holds for the dataset versions in play.

        Counted from each prior experiment's own recorded ``raw_trial_count``,
        so a campaign run last month against the same bars is part of the search
        this campaign's results are corrected for. That is the honest reading of
        "how many things were tried before this number was the best one".
        """
        if not self.spec.include_prior_trials:
            return 0
        total = 0
        for version in set(self._known_dataset_versions().values()):
            for experiment in self.ledger.list(dataset_version_id=version):
                payload = experiment.validation_report_json or {}
                total += int(payload.get("raw_trial_count", 0) or 0)
        return total

    def _record_experiment(
        self,
        cell: CandidateCell,
        barset: BarSet,
        report: ValidationReport,
        external: int,
    ) -> Any:
        draft = ExperimentDraft(
            actor_kind=self.spec.actor_kind,
            actor_name=self.spec.actor,
            reason=(
                f"campaign {self.spec.campaign_id} / {cell.origin}: {cell.rationale}"
            ),
            hypothesis_id=cell.hypothesis_id,
            strategy_id=cell.strategy_id,
            strategy_content_hash=cell.content_hash,
            strategy_document=cell.document,
            dataset_version_id=barset.dataset_version_id,
            parameters={"grid": cell.grid.to_dict()},
            engine_config=dict(report.engine_config),
            outputs={
                "campaign_id": self.spec.campaign_id,
                "cell_key": cell.key,
                "origin": cell.origin,
                "parents": list(cell.parents),
                "generation": cell.generation,
                "n_trials": cell.n_trials,
                "external_trial_count": external,
                "novelty": cell.novelty.to_dict() if cell.novelty else None,
            },
            validation_report=report,
            tags=("campaign", self.spec.campaign_id, f"origin:{cell.origin}"),
        )
        return self.ledger.create(draft)

    def _record_skip(self, skip: SkippedCell) -> Any:
        draft = ExperimentDraft(
            actor_kind=self.spec.actor_kind,
            actor_name=self.spec.actor,
            reason=(
                f"campaign {self.spec.campaign_id}: NOT RUN -- {skip.narrative or skip.reason}"
            ),
            strategy_id=skip.strategy_id,
            dataset_version_id=str(skip.detail.get("dataset_version_id", "")),
            outcome=Outcome.ABANDONED,
            conclusion=skip.narrative or skip.reason,
            rejection_reason=skip.reason,
            outputs={"campaign_id": self.spec.campaign_id, "skip": skip.to_dict()},
            tags=("campaign", self.spec.campaign_id, "skipped", skip.kind),
        )
        return self.ledger.create(draft)

    @staticmethod
    def _cell_result(
        cell: CandidateCell,
        barset: BarSet,
        outcome: CellOutcome,
        external: int,
        experiment_id: str,
    ) -> CellResult:
        report = outcome.report
        if report is None:  # pragma: no cover - guarded by the caller
            raise ValueError("_cell_result needs a report; the error path is handled above")
        failing = report.first_failing_rung()
        deflation = report.rung(5)
        metrics = deflation.metrics if deflation is not None else {}
        sr_variance = _opt(metrics.get("sr_variance"))
        cross_trial = _opt(report.cross_trial_sharpe_variance) or None
        n_for_deflation = int(metrics.get("n_trials_used_for_deflation", 0) or 0)
        # The threshold is reported even when rung 5 was never reached. Its
        # inputs are the campaign's true N -- which is known before anything runs
        # -- and the dispersion of this candidate's trial Sharpes, which rung 1
        # measures for every candidate that gets past rung 0. Reporting it is
        # how a reader learns what a survivor would have had to clear in a
        # campaign where nothing survived.
        threshold = deflation_threshold(
            n_for_deflation or (external + cell.n_trials),
            sr_variance if sr_variance is not None else cross_trial,
        )
        return CellResult(
            key=cell.key,
            strategy_id=cell.strategy_id,
            strategy_content_hash=cell.content_hash,
            instrument=cell.instrument,
            timeframe=cell.timeframe.value,
            origin=cell.origin,
            hypothesis_id=cell.hypothesis_id,
            parents=cell.parents,
            generation=cell.generation,
            rationale=cell.rationale,
            n_trials=cell.n_trials,
            external_trial_count=external,
            verdict=report.verdict.value,
            died_at_rung=failing.label if failing is not None else "",
            reason=failing.reason if failing is not None else "",
            binding_constraint=report.binding_constraint.describe(),
            gate_values=report.gate_values(),
            n_evaluations=outcome.n_evaluations,
            engine_runs=outcome.engine_runs,
            n_trades=_opt(report.gate_values().get("min_trades")),
            deflated_sharpe_ratio=_opt(metrics.get("deflated_sharpe_ratio")),
            selected_sharpe=_opt(metrics.get("selected_sharpe")),
            sr_variance=sr_variance,
            cross_trial_sharpe_variance=cross_trial,
            n_trials_used_for_deflation=n_for_deflation,
            deflation_threshold=threshold,
            experiment_id=experiment_id,
            dataset_version_id=barset.dataset_version_id,
        )

    def _build_report(
        self,
        plan: CampaignPlan,
        results: tuple[CellResult, ...],
        skipped: tuple[SkippedCell, ...],
        engine_evaluations: int,
    ) -> CampaignReport:
        measured = [c.sr_variance for c in results if c.sr_variance]
        screened = [c.cross_trial_sharpe_variance for c in results if c.cross_trial_sharpe_variance]
        variance = max(measured) if measured else (max(screened) if screened else None)
        source = (
            "rung 5, where it is applied"
            if measured
            else ("rung 1's in-sample screen" if screened else "")
        )
        threshold = deflation_threshold(plan.true_trial_count, variance)
        note = (
            f"The deflation threshold is E[max SR] for a search of "
            f"{plan.true_trial_count} trials. "
            + (
                f"Using the largest cross-trial Sharpe variance observed in this "
                f"campaign ({variance:.6g}, measured at {source}), that threshold is "
                f"{threshold:.4f}, on the same PER-BAR basis as the candidate "
                "Sharpes (nothing here is annualised): a Sharpe at or below it is "
                "what a search this size is EXPECTED to produce from strategies "
                "with no edge at all. "
                + (
                    ""
                    if measured
                    else "No candidate survived as far as rung 5, so the threshold "
                    "is reported as the bar a survivor WOULD have had to clear, not "
                    "as a bar anything was measured against."
                )
                if threshold is not None and variance is not None
                else "No candidate reached even the in-sample screen, so the "
                "dispersion of trial Sharpes was never measured and the threshold "
                "is reported as unavailable rather than guessed."
            )
        )
        report = CampaignReport(
            campaign_id=self.spec.campaign_id,
            actor=self.spec.actor,
            notes=self.spec.notes,
            spec=self.spec.to_dict(),
            gate_set_version=self.spec.gate_set.version,
            gate_set_fingerprint=self.spec.gate_set.fingerprint(),
            dataset_versions=dict(plan.dataset_versions),
            hypotheses=tuple(h.model_dump(mode="json") for h in self.spec.hypotheses),
            attempted=results,
            skipped=skipped,
            rejected_mutations=tuple(p.to_dict() for p in plan.rejected_mutations),
            planned_trial_count=plan.planned_trial_count,
            prior_trial_count=plan.total_prior_trial_count,
            true_trial_count=plan.true_trial_count,
            n_engine_evaluations=engine_evaluations,
            n_ladder_evaluations=sum(int(c.n_evaluations) for c in results),
            campaign_deflation_threshold=threshold,
            deflation_variance_used=variance,
            deflation_note=note,
            holdout=self._holdout_state(plan),
            lineage={
                **mutation_lineage(list(self.mutations.proposals)).to_dict(),
                "trial_accounting": {
                    "planned_in_this_campaign": int(plan.planned_trial_count),
                    "prior_from_ledger": int(plan.prior_trial_count),
                    "prior_declared_externally": int(plan.external_prior_trial_count),
                    "external_reason": plan.external_prior_trials_reason,
                    "true_trial_count": int(plan.true_trial_count),
                },
            },
        )
        if self.report_dir is not None:
            report.save(self.report_dir / f"campaign_{self.spec.campaign_id}.json")
            (self.report_dir / f"campaign_{self.spec.campaign_id}.md").write_text(
                report.markdown(), encoding="utf-8"
            )
        return report

    def _holdout_state(self, plan: CampaignPlan) -> dict[str, Any]:
        state: dict[str, Any] = {"consumptions": [], "segments": []}
        for version in sorted(set(plan.dataset_versions.values())):
            try:
                segment = self.registry.segment(version)
            except Exception:
                continue
            state["segments"].append(segment.to_dict())
            state["consumptions"].extend(
                c.to_dict() for c in self.registry.consumptions(version)
            )
        state["n_consumed"] = len(state["consumptions"])
        state["unconsumed"] = state["n_consumed"] == 0
        state["note"] = (
            "No candidate reached rung 6, so the holdout segment is untouched and "
            "remains available for a future campaign."
            if state["n_consumed"] == 0
            else (
                f"{state['n_consumed']} holdout look(s) have been spent on these "
                "dataset versions. A spent look cannot be recovered."
            )
        )
        return state


def _opt(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell_key(document: StrategyDocument, instrument: str, timeframe: Timeframe) -> str:
    blob = f"{document.content_hash()}|{instrument.upper()}|{timeframe.value}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def seed_documents(path: str | Path = "research/strategies") -> tuple[StrategyDocument, ...]:
    """Load the seed strategy documents, ordered by id."""
    root = Path(path)
    found = [
        StrategyDocument.from_json(p.read_text(encoding="utf-8"))
        for p in sorted(root.glob("*.json"))
    ]
    return tuple(sorted(found, key=lambda d: d.strategy_id))


@dataclass(frozen=True, slots=True)
class StoreBarSource:
    """A :class:`BarSource` backed by a :class:`fiboki.data.store.DataStore`.

    Duck-typed so this module does not import the data platform: the campaign
    only needs something that can answer "the latest validated bars for this
    instrument and timeframe, and the version id they came from". It exposes
    ``store`` so :class:`CampaignRunner` can build the research FX source from
    the same store the bars come from.

    Bars are returned as stored, ``price_basis`` column included; BID bars are
    converted to synthetic mid, and the conversion recorded, by
    :func:`fiboki.validation.run.research_mid_frame` inside ``run_validation``.
    """

    store: Any
    kind: Any = None

    def __call__(self, instrument: str, timeframe: Timeframe) -> BarSet | None:
        try:
            frame, version = self.store.read_latest(instrument, timeframe, kind=self.kind)
        except Exception:
            return None
        if frame is None or len(frame) == 0:
            return None
        return BarSet(
            instrument=instrument.upper(),
            timeframe=timeframe,
            frame=frame,
            dataset_version_id=str(version.version_id),
        )


def store_bar_source(store: Any, *, kind: Any = None) -> StoreBarSource:
    """A :class:`StoreBarSource` over ``store``. See that class."""
    return StoreBarSource(store=store, kind=kind)
