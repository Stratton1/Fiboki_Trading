"""The agent tool registry: the complete surface an LLM can reach.

Every tool declares, as data:

* a name and a description the model actually sees,
* a pydantic INPUT schema and a pydantic OUTPUT schema (both validated -- a
  handler that returns something off-schema is a bug caught here, not a
  hallucination passed downstream),
* the single :class:`~fiboki.agents.capabilities.Capability` it requires,
* whether it mutates state, and if so which RESEARCH domain it writes to,
* a cost and rate budget.

The cardinal rule, enforced here
--------------------------------
There is **no tool** that places an order, sizes a position, changes a risk
limit, deactivates a kill switch, enables an execution mode, routes to a
broker, or writes to market data.  Three mechanisms keep it that way:

1.  :class:`WriteDomain` enumerates the only places a write may land.  Every
    member is a research artefact or the job queue.  A tool that wants to write
    anywhere else cannot describe itself.
2.  :meth:`ToolRegistry.register` re-runs the capability guard from
    :mod:`fiboki.agents.capabilities` on the tool's declared capability, and
    refuses any tool whose ``mutates`` flag disagrees with its write domain.
3.  The engine-running tools (``run_backtest`` and friends) do not run the
    engine.  They submit a job to a queue that a deterministic worker drains.
    The agent's output is a payload; the execution is the platform's.

``search_web`` and ``fetch_research`` are declared as interfaces with a stub
implementation.  No test touches a network.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from fiboki.agents.capabilities import Capability, assert_no_execution_capability
from fiboki.agents.orchestrator import JobSpec, JobTicket, JobType
from fiboki.agents.research_store import (
    Critique,
    ExperimentDesign,
    Hypothesis,
    Objection,
    ResearchNote,
    ResearchStore,
    StrategyProposal,
    SuccessCriterion,
)
from fiboki.agents.sandbox import SandboxRejection, validate_strategy_payload
from fiboki.backtest.metrics import max_drawdown
from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.data.integrity import IntegrityConfig
from fiboki.data.integrity import validate as validate_integrity
from fiboki.data.telemetry import TelemetryReader, slippage_summary
from fiboki.marketstate.features import FeatureEngine, FeatureError
from fiboki.marketstate.regime import RegimeAxis, RegimeClassifier, RegimeError
from fiboki.strategy.dsl import StrategyDocument
from fiboki.strategy.registry import StrategyRegistry
from fiboki.validation.gates import GATE_SET_V2

# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


class WriteDomain(str, Enum):
    """The only destinations a tool may write to.

    Every member is either a research artefact or the job queue.  There is no
    ``MARKET_DATA``, no ``RISK_CONFIG``, no ``EXECUTION_STATE``, no ``BROKER``.
    """

    NONE = "none"
    RESEARCH_HYPOTHESIS = "research:hypothesis"
    RESEARCH_STRATEGY_PROPOSAL = "research:strategy_proposal"
    RESEARCH_EXPERIMENT = "research:experiment"
    RESEARCH_CRITIQUE = "research:critique"
    RESEARCH_NOTE = "research:note"
    JOB_QUEUE = "queue:jobs"


@dataclass(frozen=True, slots=True)
class ToolBudget:
    """What one tool call is allowed to cost.

    ``max_rows_returned`` is a correctness control as much as a cost one: a
    tool that hands a model 40,000 bars has not given it information, it has
    given it a context-window problem.
    """

    max_calls_per_job: int = 20
    max_calls_per_minute: int = 60
    estimated_cost_usd: float = 0.0
    max_rows_returned: int = 500
    typical_wall_ms: float = 25.0


@runtime_checkable
class ToolHandler(Protocol):
    def __call__(self, ctx: ToolContext, inputs: Any) -> BaseModel: ...


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool, fully described."""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    capability: Capability
    write_domain: WriteDomain
    mutates: bool
    budget: ToolBudget
    handler: ToolHandler

    def describe(self) -> dict[str, Any]:
        """The model-facing description, including its JSON schemas."""
        return {
            "name": self.name,
            "description": self.description,
            "capability": self.capability.value,
            "mutates": self.mutates,
            "write_domain": self.write_domain.value,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
            "budget": {
                "max_calls_per_job": self.budget.max_calls_per_job,
                "max_calls_per_minute": self.budget.max_calls_per_minute,
                "estimated_cost_usd": self.budget.estimated_cost_usd,
                "max_rows_returned": self.budget.max_rows_returned,
            },
        }


class ToolRegistrationError(RuntimeError):
    """A tool declared something the registry refuses to accept."""


class ToolRegistry:
    """The set of tools that exist.  Registration is where policy is enforced."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ToolRegistrationError(f"tool {spec.name!r} is already registered")
        if not isinstance(spec.capability, Capability):
            raise ToolRegistrationError(
                f"{spec.name}: capability must be a Capability member, got "
                f"{type(spec.capability).__name__}"
            )
        # Belt and braces: re-run the execution guard against the declared
        # capability, so a tool cannot be the place a forbidden one appears.
        assert_no_execution_capability([spec.capability])
        if not isinstance(spec.write_domain, WriteDomain):
            raise ToolRegistrationError(f"{spec.name}: write_domain must be a WriteDomain")
        writes = spec.write_domain is not WriteDomain.NONE
        if writes != spec.mutates:
            raise ToolRegistrationError(
                f"{spec.name}: mutates={spec.mutates} disagrees with write_domain="
                f"{spec.write_domain.value}. A tool must be honest about whether it "
                "changes anything."
            )
        if spec.mutates and not spec.capability.is_write and spec.capability is not Capability.SUBMIT_JOB:
            raise ToolRegistrationError(
                f"{spec.name}: mutating tools need a write: or submit: capability, "
                f"not {spec.capability.value}"
            )
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"unknown tool {name!r}; registered: {self.names()}")
        return self._tools[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def all(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[n] for n in self.names())

    def capabilities(self) -> frozenset[Capability]:
        return frozenset(t.capability for t in self.all())

    def for_capabilities(self, granted: frozenset[Capability]) -> tuple[ToolSpec, ...]:
        return tuple(t for t in self.all() if t.capability in granted)

    def describe_all(self) -> list[dict[str, Any]]:
        return [t.describe() for t in self.all()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


# ---------------------------------------------------------------------------
# The services a tool may touch
# ---------------------------------------------------------------------------


@runtime_checkable
class BarSource(Protocol):
    """Read-only market data.  There is no ``write`` on this protocol."""

    def load(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> tuple[pd.DataFrame, str]:
        """Return ``(frame, dataset_version_id)``.  Absence must raise."""


class InMemoryBarSource:
    """Bars held in memory, keyed by ``(INSTRUMENT, timeframe)``.

    Used by research fixtures and by tests.  A missing key raises rather than
    returning an empty frame -- V1's habit of conflating "absent" with "empty"
    is exactly what the data platform refuses, and the agent layer inherits it.
    """

    def __init__(self, frames: Mapping[tuple[str, str], pd.DataFrame] | None = None) -> None:
        self._frames: dict[tuple[str, str], pd.DataFrame] = {
            (k[0].upper(), str(k[1])): v for k, v in (frames or {}).items()
        }

    def add(self, instrument: str, timeframe: Timeframe, frame: pd.DataFrame) -> None:
        self._frames[(instrument.upper(), timeframe.value)] = frame

    def load(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> tuple[pd.DataFrame, str]:
        key = (instrument.upper(), timeframe.value)
        if key not in self._frames:
            raise KeyError(
                f"no bars for {key[0]} {key[1]} in this source. This is an absence, "
                "not an empty result: do not record it as a completed no-data outcome."
            )
        frame = self._frames[key]
        if start is not None:
            frame = frame.loc[pd.Timestamp(start, tz="UTC") :]
        if end is not None:
            frame = frame.loc[: pd.Timestamp(end, tz="UTC")]
        return frame, f"inmem:{key[0]}:{key[1]}:{len(frame)}"


class DataStoreBarSource:
    """Reads the real versioned parquet store.  Read-only by construction."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def load(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> tuple[pd.DataFrame, str]:
        frame, version = self._store.read_latest(
            instrument, timeframe, start=start, end=end
        )
        return frame, version.version_id


@dataclass(frozen=True, slots=True)
class PositionView:
    """A read-only projection of an open position.  Carries no mutators."""

    instrument: str
    direction: str
    size: float
    entry_price: float
    mark_price: float
    unrealised_pnl: float
    strategy_id: str = ""


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """What the agent may know about the book.  It cannot change any of it."""

    as_of: datetime
    account_ccy: str
    equity: float
    balance: float
    margin_used: float
    positions: tuple[PositionView, ...] = ()
    mode: str = "paper"


@runtime_checkable
class PortfolioProvider(Protocol):
    def snapshot(self) -> PortfolioSnapshot: ...


class StaticPortfolioProvider:
    """A fixed snapshot.  The default, and deliberately inert."""

    def __init__(self, snapshot: PortfolioSnapshot | None = None) -> None:
        self._snapshot = snapshot or PortfolioSnapshot(
            as_of=datetime(2026, 1, 5, tzinfo=UTC),
            account_ccy="GBP",
            equity=0.0,
            balance=0.0,
            margin_used=0.0,
        )

    def snapshot(self) -> PortfolioSnapshot:
        return self._snapshot


@runtime_checkable
class WebSearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> tuple[dict[str, Any], ...]: ...
    def fetch(self, url: str) -> dict[str, Any]: ...


class StubWebSearch:
    """Interface-only web access.

    The brief calls for ``search_web`` / ``fetch_research`` as INTERFACES.  This
    implementation performs no I/O and says so in its output, so a model that
    receives an empty result cannot mistake it for "nothing has been published
    on this".  Wiring a real provider is a deployment decision, not a test one.
    """

    configured = False

    def search(self, query: str, max_results: int) -> tuple[dict[str, Any], ...]:
        return ()

    def fetch(self, url: str) -> dict[str, Any]:
        return {"url": url, "status": "not_fetched", "content": ""}


@dataclass(frozen=True, slots=True)
class ToolContext:
    """The services available to tool handlers.  Injected, never global."""

    research: ResearchStore
    strategies: StrategyRegistry
    bars: BarSource | None = None
    portfolio: PortfolioProvider = field(default_factory=StaticPortfolioProvider)
    web: WebSearchProvider = field(default_factory=StubWebSearch)
    telemetry_dir: Path | None = None
    submit_job: Any = None
    agent_id: str = "unknown"
    role: str = "unknown"
    default_queue: str = "research"

    def require_bars(self) -> BarSource:
        if self.bars is None:
            raise ToolExecutionError(
                "no market-data source is wired into this agent context; refusing to "
                "invent bars"
            )
        return self.bars

    def require_submitter(self) -> Any:
        if self.submit_job is None:
            raise ToolExecutionError(
                "no job submitter is wired into this agent context; an agent cannot "
                "run the engine itself, so with no queue there is nothing to do"
            )
        return self.submit_job


class ToolExecutionError(RuntimeError):
    """A tool could not honestly produce an answer."""


# ---------------------------------------------------------------------------
# Shared schema pieces
# ---------------------------------------------------------------------------


class _In(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _tf(value: str) -> Timeframe:
    try:
        return Timeframe(value)
    except ValueError as exc:
        raise ToolExecutionError(
            f"unknown timeframe {value!r}; registered: {[t.value for t in Timeframe]}"
        ) from exc


def _finite(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# READ TOOLS
# ---------------------------------------------------------------------------


class QueryMarketDataIn(_In):
    instrument: str
    timeframe: str
    start: str | None = None
    end: str | None = None
    tail_bars: int = Field(default=0, ge=0, le=500)


class BarRow(_Out):
    timestamp: str
    open: float
    high: float
    low: float
    close: float


class QueryMarketDataOut(_Out):
    instrument: str
    timeframe: str
    dataset_version_id: str
    n_bars: int
    first_timestamp: str | None
    last_timestamp: str | None
    first_close: float | None
    last_close: float | None
    min_low: float | None
    max_high: float | None
    mean_log_return: float | None
    stdev_log_return: float | None
    annualised_volatility_pct: float | None
    median_bar_range_pips: float | None
    tail: tuple[BarRow, ...] = ()
    truncated: bool = False
    caveats: tuple[str, ...] = ()


def _query_market_data(ctx: ToolContext, inputs: QueryMarketDataIn) -> QueryMarketDataOut:
    timeframe = _tf(inputs.timeframe)
    frame, version_id = ctx.require_bars().load(
        inputs.instrument, timeframe, start=inputs.start, end=inputs.end
    )
    if frame.empty:
        raise ToolExecutionError(
            f"{inputs.instrument} {timeframe.value}: the source returned zero rows for "
            "the requested window. An empty window is a fact about the request, not "
            "about the market; narrow or widen it deliberately."
        )
    close = frame["close"].to_numpy(dtype=float)
    log_returns = np.diff(np.log(close)) if len(close) > 1 else np.array([])
    instrument = get_instrument(inputs.instrument)
    bar_range = (frame["high"] - frame["low"]).to_numpy(dtype=float)
    tail_rows: tuple[BarRow, ...] = ()
    if inputs.tail_bars:
        tail = frame.iloc[-inputs.tail_bars :]
        tail_rows = tuple(
            BarRow(
                timestamp=str(ts),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            for ts, row in tail.iterrows()
        )
    caveats: list[str] = []
    if "volume" not in frame.columns:
        caveats.append("no volume column: volume-dependent reasoning is unavailable")
    elif float(frame["volume"].sum()) == 0.0:
        caveats.append("volume is identically zero; treat it as absent, not as low")
    return QueryMarketDataOut(
        instrument=inputs.instrument.upper(),
        timeframe=timeframe.value,
        dataset_version_id=version_id,
        n_bars=len(frame),
        first_timestamp=str(frame.index[0]),
        last_timestamp=str(frame.index[-1]),
        first_close=_finite(close[0]),
        last_close=_finite(close[-1]),
        min_low=_finite(frame["low"].min()),
        max_high=_finite(frame["high"].max()),
        mean_log_return=_finite(log_returns.mean()) if log_returns.size else None,
        stdev_log_return=_finite(log_returns.std(ddof=1)) if log_returns.size > 1 else None,
        annualised_volatility_pct=(
            _finite(log_returns.std(ddof=1) * math.sqrt(timeframe.bars_per_year) * 100.0)
            if log_returns.size > 1
            else None
        ),
        median_bar_range_pips=_finite(np.median(bar_range) / instrument.pip_size),
        tail=tail_rows,
        truncated=bool(inputs.tail_bars) and len(frame) > inputs.tail_bars,
        caveats=tuple(caveats),
    )


class QueryRegimeIn(_In):
    instrument: str
    timeframe: str
    start: str | None = None
    end: str | None = None
    as_of: str | None = None
    include_distribution: bool = True


class AxisDistribution(_Out):
    axis: str
    shares: dict[str, float]


class QueryRegimeOut(_Out):
    instrument: str
    timeframe: str
    dataset_version_id: str
    as_of: str
    regime_key: str
    axes: dict[str, str]
    is_known: bool
    n_bars_used: int
    warmup_bars: int
    classifier_fingerprint: str
    feature_fingerprint: str
    distribution: tuple[AxisDistribution, ...] = ()
    notes: tuple[str, ...] = ()


def _query_regime(ctx: ToolContext, inputs: QueryRegimeIn) -> QueryRegimeOut:
    """Read the platform's regime classification. The agent does not define it.

    Classification is delegated wholesale to
    :mod:`fiboki.marketstate` -- the feature engine computes the causal inputs
    and :class:`~fiboki.marketstate.regime.RegimeClassifier` labels them. The
    agent layer deliberately owns NO regime logic of its own: a second
    classifier here would be a second answer to the same question, and research
    conclusions would start depending on which one a tool happened to call.
    """
    timeframe = _tf(inputs.timeframe)
    frame, version_id = ctx.require_bars().load(
        inputs.instrument, timeframe, start=inputs.start, end=inputs.end
    )
    engine = FeatureEngine(timeframe=timeframe, instrument=inputs.instrument.upper())
    if len(frame) <= engine.warmup:
        raise ToolExecutionError(
            f"{inputs.instrument} {timeframe.value}: {len(frame)} bars is below the "
            f"{engine.warmup} the feature engine needs; a regime read on unwarmed "
            "features is a label with no evidence behind it"
        )
    try:
        features = engine.compute(frame)
        series = RegimeClassifier().classify(features)
    except (FeatureError, RegimeError) as exc:
        raise ToolExecutionError(f"regime classification failed: {exc}") from exc

    when = pd.Timestamp(inputs.as_of, tz="UTC") if inputs.as_of else frame.index[-1]
    if len(series.frame) <= series.warmup:
        raise ToolExecutionError(
            f"{inputs.instrument} {timeframe.value}: the classifier needs "
            f"{series.warmup} bars and the window holds {len(series.frame)}"
        )
    ready_from = series.frame.index[series.warmup]
    if when < ready_from:
        raise ToolExecutionError(
            f"as_of {when} precedes the first warm bar {ready_from}; the label there "
            "would be built from partially-warm features"
        )
    try:
        vector = series.at(when)
    except KeyError as exc:
        raise ToolExecutionError(str(exc)) from exc

    distribution: tuple[AxisDistribution, ...] = ()
    if inputs.include_distribution:
        distribution = tuple(
            AxisDistribution(
                axis=axis.value,
                shares={
                    str(k): round(float(v), 6)
                    for k, v in series.axis_distribution(axis).head(6).items()
                },
            )
            for axis in RegimeAxis
        )
    return QueryRegimeOut(
        instrument=inputs.instrument.upper(),
        timeframe=timeframe.value,
        dataset_version_id=version_id,
        as_of=str(when),
        regime_key=vector.key,
        axes=vector.to_dict(),
        is_known=vector.is_known,
        n_bars_used=len(frame),
        warmup_bars=series.warmup,
        classifier_fingerprint=series.fingerprint,
        feature_fingerprint=features.fingerprint,
        distribution=distribution,
        notes=tuple(series.notes) + tuple(features.notes),
    )


class QueryExperimentHistoryIn(_In):
    strategy_id: str | None = None
    hypothesis_id: str | None = None
    limit: int = Field(default=25, ge=1, le=200)


class ExperimentSummary(_Out):
    experiment_id: str
    hypothesis_id: str
    strategy_ids: tuple[str, ...]
    instruments: tuple[str, ...]
    timeframes: tuple[str, ...]
    train_window: str
    test_window: str
    n_folds: int
    seed: int
    success_criteria: tuple[str, ...]
    created_at: str


class QueryExperimentHistoryOut(_Out):
    experiments: tuple[ExperimentSummary, ...]
    n_total: int
    truncated: bool


def _query_experiment_history(
    ctx: ToolContext, inputs: QueryExperimentHistoryIn
) -> QueryExperimentHistoryOut:
    rows = [r for r in ctx.research.list("experiments") if isinstance(r, ExperimentDesign)]
    if inputs.strategy_id:
        rows = [r for r in rows if inputs.strategy_id in r.strategy_ids]
    if inputs.hypothesis_id:
        rows = [r for r in rows if r.hypothesis_id == inputs.hypothesis_id]
    total = len(rows)
    selected = rows[-inputs.limit :]
    return QueryExperimentHistoryOut(
        experiments=tuple(
            ExperimentSummary(
                experiment_id=r.experiment_id,
                hypothesis_id=r.hypothesis_id,
                strategy_ids=r.strategy_ids,
                instruments=r.instruments,
                timeframes=r.timeframes,
                train_window=f"{r.train_start}..{r.train_end}",
                test_window=f"{r.test_start}..{r.test_end}",
                n_folds=r.n_folds,
                seed=r.seed,
                success_criteria=tuple(
                    f"{c.metric} {c.comparator} {c.threshold}" for c in r.success_criteria
                ),
                created_at=r.created_at.isoformat(),
            )
            for r in selected
        ),
        n_total=total,
        truncated=total > len(selected),
    )


class QueryResearchMemoryIn(_In):
    query: str = ""
    tags: tuple[str, ...] = ()
    limit: int = Field(default=10, ge=1, le=100)


class NoteSummary(_Out):
    note_id: str
    title: str
    body: str
    tags: tuple[str, ...]
    links: dict[str, str]
    created_at: str


class QueryResearchMemoryOut(_Out):
    notes: tuple[NoteSummary, ...]
    n_matched: int


def _query_research_memory(
    ctx: ToolContext, inputs: QueryResearchMemoryIn
) -> QueryResearchMemoryOut:
    notes = ctx.research.search_notes(inputs.query, tags=inputs.tags, limit=inputs.limit)
    return QueryResearchMemoryOut(
        notes=tuple(
            NoteSummary(
                note_id=n.note_id,
                title=n.title,
                body=n.body,
                tags=n.tags,
                links=n.links,
                created_at=n.created_at.isoformat(),
            )
            for n in notes
        ),
        n_matched=len(notes),
    )


class QueryStrategyIn(_In):
    strategy_id: str
    include_document: bool = False


class QueryStrategyOut(_Out):
    strategy_id: str
    name: str
    family: str
    direction: str
    hypothesis: str
    universe: tuple[str, ...]
    timeframes: tuple[str, ...]
    content_hash: str
    complexity_score: float
    n_rules: int
    stop_kind: str
    n_take_profit_legs: int
    take_profit_allocation: float
    trailing_kind: str
    parameters: tuple[str, ...]
    parent_strategy_ids: tuple[str, ...]
    health_issues: tuple[str, ...]
    document: dict[str, Any] | None = None


def _query_strategy(ctx: ToolContext, inputs: QueryStrategyIn) -> QueryStrategyOut:
    try:
        doc = ctx.strategies.get(inputs.strategy_id)
    except KeyError as exc:
        raise ToolExecutionError(str(exc)) from exc
    report = ctx.strategies.health_check()
    issues = tuple(
        f"{i.severity}:{i.code}: {i.detail}"
        for i in report.issues
        if i.strategy_id == inputs.strategy_id
    )
    return QueryStrategyOut(
        strategy_id=doc.strategy_id,
        name=doc.name,
        family=doc.family.value,
        direction=doc.direction.value,
        hypothesis=doc.hypothesis,
        universe=doc.universe,
        timeframes=tuple(t.value for t in doc.timeframes),
        content_hash=doc.content_hash(),
        complexity_score=doc.complexity_score,
        n_rules=len(doc.all_rules()),
        stop_kind=doc.stop.kind,
        n_take_profit_legs=len(doc.take_profits),
        take_profit_allocation=round(sum(leg.allocation for leg in doc.take_profits), 6),
        trailing_kind=doc.trailing.kind if doc.trailing else "none",
        parameters=tuple(sorted(doc.parameters)),
        parent_strategy_ids=doc.parent_strategy_ids,
        health_issues=issues,
        document=doc.model_dump(mode="json") if inputs.include_document else None,
    )


class CompareCandidatesIn(_In):
    strategy_ids: tuple[str, ...] = Field(min_length=2, max_length=50)
    rank_by: str = "sharpe"
    min_trades: int = Field(default=80, ge=0)


class CandidateRow(_Out):
    strategy_id: str
    backtest_id: str | None
    n_trades: int
    metrics: dict[str, float | None]
    rank_value: float | None
    meets_min_trades: bool
    note: str = ""


class CompareCandidatesOut(_Out):
    rank_by: str
    min_trades: int
    rows: tuple[CandidateRow, ...]
    ranking: tuple[str, ...]
    excluded: tuple[str, ...]
    caveats: tuple[str, ...]


#: The trade floor, read from the platform's canonical promotion gates rather
#: than restated here.  A comparison made below it is a comparison of noise, and
#: the tool says so rather than leaving it implied.
MIN_TRADES_FOR_PROMOTION = int(GATE_SET_V2.by_name("min_trades").threshold)

_COMPARISON_METRICS = (
    "sharpe",
    "sortino",
    "calmar",
    "cagr_pct",
    "max_drawdown_pct",
    "profit_factor",
    "expectancy",
    "win_rate_pct",
    "total_costs",
)


def _compare_candidates(ctx: ToolContext, inputs: CompareCandidatesIn) -> CompareCandidatesOut:
    rows: list[CandidateRow] = []
    excluded: list[str] = []
    caveats: list[str] = []
    for sid in inputs.strategy_ids:
        record = ctx.research.latest_backtest_for(sid)
        if record is None:
            rows.append(
                CandidateRow(
                    strategy_id=sid,
                    backtest_id=None,
                    n_trades=0,
                    metrics={},
                    rank_value=None,
                    meets_min_trades=False,
                    note="no recorded backtest; absence is not a zero score",
                )
            )
            excluded.append(sid)
            continue
        metrics = {k: _finite(record.metrics.get(k)) for k in _COMPARISON_METRICS}
        rank_value = _finite(record.metrics.get(inputs.rank_by))
        meets = record.n_trades >= inputs.min_trades
        if not meets:
            excluded.append(sid)
        rows.append(
            CandidateRow(
                strategy_id=sid,
                backtest_id=record.backtest_id,
                n_trades=record.n_trades,
                metrics=metrics,
                rank_value=rank_value,
                meets_min_trades=meets,
                note="" if meets else f"{record.n_trades} trades < {inputs.min_trades}",
            )
        )
    rankable = [r for r in rows if r.rank_value is not None and r.meets_min_trades]
    lower_is_better = inputs.rank_by in ("max_drawdown_pct", "total_costs", "ulcer")
    rankable.sort(
        key=lambda r: (r.rank_value if lower_is_better else -float(r.rank_value or 0.0), r.strategy_id)
    )
    if inputs.min_trades < MIN_TRADES_FOR_PROMOTION:
        caveats.append(
            f"min_trades={inputs.min_trades} is below the platform's promotion "
            f"floor of {MIN_TRADES_FOR_PROMOTION} ({GATE_SET_V2.version}); a "
            "ranking made below it is a ranking of sampling noise"
        )
    if not rankable:
        caveats.append("nothing is rankable: no candidate has both the metric and the trades")
    caveats.append(
        "these are in-sample backtest metrics unless a validation report says otherwise; "
        "ranking them is selection, and selection needs a multiple-testing correction"
    )
    return CompareCandidatesOut(
        rank_by=inputs.rank_by,
        min_trades=inputs.min_trades,
        rows=tuple(rows),
        ranking=tuple(r.strategy_id for r in rankable),
        excluded=tuple(sorted(set(excluded))),
        caveats=tuple(caveats),
    )


class InspectTradeIn(_In):
    backtest_id: str
    trade_index: int | None = None
    trade_id: str | None = None
    worst_n: int = Field(default=0, ge=0, le=50)


class TradeView(_Out):
    index: int
    trade_id: str
    instrument: str
    direction: str
    size: float
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    exit_reason: str
    gross_pnl: float
    net_pnl: float
    spread_cost: float
    commission: float
    slippage_cost: float
    financing_cost: float
    bars_held: int
    max_adverse_excursion: float
    max_favourable_excursion: float


class InspectTradeOut(_Out):
    backtest_id: str
    strategy_id: str
    n_trades: int
    trades: tuple[TradeView, ...]
    selection: str


def _trade_view(index: int, raw: Mapping[str, Any]) -> TradeView:
    return TradeView(
        index=index,
        trade_id=str(raw.get("trade_id", "")),
        instrument=str(raw.get("instrument", "")),
        direction=str(raw.get("direction", "")),
        size=float(raw.get("size", 0.0)),
        entry_price=float(raw.get("entry_price", 0.0)),
        exit_price=float(raw.get("exit_price", 0.0)),
        entry_time=str(raw.get("entry_time", "")),
        exit_time=str(raw.get("exit_time", "")),
        exit_reason=str(raw.get("exit_reason", "")),
        gross_pnl=float(raw.get("gross_pnl", 0.0)),
        net_pnl=float(raw.get("net_pnl", 0.0)),
        spread_cost=float(raw.get("spread_cost", 0.0)),
        commission=float(raw.get("commission", 0.0)),
        slippage_cost=float(raw.get("slippage_cost", 0.0)),
        financing_cost=float(raw.get("financing_cost", 0.0)),
        bars_held=int(raw.get("bars_held", 0)),
        max_adverse_excursion=float(raw.get("max_adverse_excursion", 0.0)),
        max_favourable_excursion=float(raw.get("max_favourable_excursion", 0.0)),
    )


def _inspect_trade(ctx: ToolContext, inputs: InspectTradeIn) -> InspectTradeOut:
    try:
        record = ctx.research.get_backtest(inputs.backtest_id)
    except KeyError as exc:
        raise ToolExecutionError(str(exc)) from exc
    trades = list(record.trades)
    if inputs.trade_id is not None:
        matches = [
            _trade_view(i, t) for i, t in enumerate(trades) if t.get("trade_id") == inputs.trade_id
        ]
        if not matches:
            raise ToolExecutionError(f"{inputs.backtest_id}: no trade {inputs.trade_id!r}")
        selection = f"trade_id={inputs.trade_id}"
        chosen = tuple(matches)
    elif inputs.trade_index is not None:
        if not 0 <= inputs.trade_index < len(trades):
            raise ToolExecutionError(
                f"{inputs.backtest_id}: trade_index {inputs.trade_index} out of range "
                f"(0..{len(trades) - 1})"
            )
        selection = f"trade_index={inputs.trade_index}"
        chosen = (_trade_view(inputs.trade_index, trades[inputs.trade_index]),)
    elif inputs.worst_n:
        order = sorted(range(len(trades)), key=lambda i: float(trades[i].get("net_pnl", 0.0)))
        selection = f"worst_{inputs.worst_n}_by_net_pnl"
        chosen = tuple(_trade_view(i, trades[i]) for i in order[: inputs.worst_n])
    else:
        raise ToolExecutionError(
            "inspect_trade needs one of trade_id, trade_index or worst_n; it will not "
            "dump an entire ledger into a context window"
        )
    return InspectTradeOut(
        backtest_id=record.backtest_id,
        strategy_id=record.strategy_id,
        n_trades=len(trades),
        trades=chosen,
        selection=selection,
    )


class InspectDrawdownIn(_In):
    backtest_id: str
    top_n: int = Field(default=3, ge=1, le=20)
    include_trades: bool = True
    max_trades_per_episode: int = Field(default=10, ge=0, le=50)


class DrawdownEpisode(_Out):
    rank: int
    peak_time: str
    trough_time: str
    recovery_time: str | None
    depth_pct: float
    depth_abs: float
    duration_bars: int
    recovery_bars: int | None
    trades_in_episode: int
    worst_trades: tuple[TradeView, ...] = ()


class InspectDrawdownOut(_Out):
    #: Depths are reported as POSITIVE magnitudes throughout. A reader
    #: comparing two drawdowns should never have to work out a sign convention.
    backtest_id: str
    strategy_id: str
    max_drawdown_pct: float
    max_drawdown_abs: float
    episodes: tuple[DrawdownEpisode, ...]
    caveats: tuple[str, ...]


def _inspect_drawdown(ctx: ToolContext, inputs: InspectDrawdownIn) -> InspectDrawdownOut:
    try:
        record = ctx.research.get_backtest(inputs.backtest_id)
    except KeyError as exc:
        raise ToolExecutionError(str(exc)) from exc
    if not record.equity_curve:
        raise ToolExecutionError(
            f"{inputs.backtest_id} has no stored equity curve; a drawdown computed "
            "from trade P&L alone ignores open-position mark-to-market"
        )
    times = [str(row["timestamp"]) for row in record.equity_curve]
    equity = np.array([float(row["equity"]) for row in record.equity_curve], dtype=float)
    depth_abs, depth_pct = max_drawdown(equity)

    running_peak = np.maximum.accumulate(equity)
    underwater = equity < running_peak
    episodes: list[dict[str, Any]] = []
    start: int | None = None
    for i, wet in enumerate(underwater):
        if wet and start is None:
            start = i - 1 if i > 0 else 0
        elif not wet and start is not None:
            episodes.append({"start": start, "end": i})
            start = None
    if start is not None:
        episodes.append({"start": start, "end": None})

    scored: list[tuple[float, dict[str, Any]]] = []
    for ep in episodes:
        s = int(ep["start"])
        e = int(ep["end"]) if ep["end"] is not None else len(equity) - 1
        segment = equity[s : e + 1]
        peak = float(segment[0])
        trough_offset = int(np.argmin(segment))
        trough = float(segment[trough_offset])
        drop_pct = (peak - trough) / peak * 100.0 if peak > 0 else 0.0
        scored.append((drop_pct, {**ep, "s": s, "e": e, "trough": s + trough_offset,
                                  "peak_value": peak, "trough_value": trough,
                                  "drop_pct": drop_pct}))
    scored.sort(key=lambda row: (-row[0], row[1]["s"]))

    trades = list(record.trades)
    out_episodes: list[DrawdownEpisode] = []
    for rank, (_drop, ep) in enumerate(scored[: inputs.top_n], start=1):
        peak_time = times[ep["s"]]
        trough_time = times[ep["trough"]]
        recovered = ep["end"] is not None
        recovery_time = times[ep["e"]] if recovered else None
        in_window = [
            (i, t)
            for i, t in enumerate(trades)
            if peak_time <= str(t.get("exit_time", "")) <= times[ep["e"]]
        ]
        worst: tuple[TradeView, ...] = ()
        if inputs.include_trades and inputs.max_trades_per_episode:
            ordered = sorted(in_window, key=lambda pair: float(pair[1].get("net_pnl", 0.0)))
            worst = tuple(
                _trade_view(i, t) for i, t in ordered[: inputs.max_trades_per_episode]
            )
        out_episodes.append(
            DrawdownEpisode(
                rank=rank,
                peak_time=peak_time,
                trough_time=trough_time,
                recovery_time=recovery_time,
                depth_pct=round(float(ep["drop_pct"]), 6),
                depth_abs=round(float(ep["peak_value"] - ep["trough_value"]), 6),
                duration_bars=int(ep["trough"] - ep["s"]),
                recovery_bars=int(ep["e"] - ep["trough"]) if recovered else None,
                trades_in_episode=len(in_window),
                worst_trades=worst,
            )
        )
    caveats = [
        "drawdown is measured on the mark-to-market equity curve, not on closed trades",
        "depths are reported as positive magnitudes",
    ]
    if scored and scored[0][1]["end"] is None:
        caveats.append("the worst episode had not recovered by the end of the sample")
    return InspectDrawdownOut(
        backtest_id=record.backtest_id,
        strategy_id=record.strategy_id,
        max_drawdown_pct=round(abs(float(depth_pct)), 6),
        max_drawdown_abs=round(abs(float(depth_abs)), 6),
        episodes=tuple(out_episodes),
        caveats=tuple(caveats),
    )


class InspectValidationReportIn(_In):
    report_id: str | None = None
    strategy_id: str | None = None


class InspectValidationReportOut(_Out):
    report_id: str
    strategy_id: str
    backtest_id: str
    kind: str
    verdict: str
    checks: tuple[dict[str, Any], ...]
    failed_checks: tuple[str, ...]
    metrics: dict[str, Any]
    caveats: tuple[str, ...]
    created_at: str


def _inspect_validation_report(
    ctx: ToolContext, inputs: InspectValidationReportIn
) -> InspectValidationReportOut:
    if inputs.report_id:
        try:
            record = ctx.research.get_validation_report(inputs.report_id)
        except KeyError as exc:
            raise ToolExecutionError(str(exc)) from exc
    elif inputs.strategy_id:
        found = ctx.research.validation_reports_for(inputs.strategy_id)
        if not found:
            raise ToolExecutionError(
                f"no validation report for {inputs.strategy_id!r}. Absence of a report "
                "is not a passing report."
            )
        record = found[-1]
    else:
        raise ToolExecutionError("inspect_validation_report needs report_id or strategy_id")
    return InspectValidationReportOut(
        report_id=record.report_id,
        strategy_id=record.strategy_id,
        backtest_id=record.backtest_id,
        kind=record.kind,
        verdict=record.verdict,
        checks=record.checks,
        failed_checks=record.failed_checks,
        metrics=record.metrics,
        caveats=record.caveats,
        created_at=record.created_at.isoformat(),
    )


class QueryPortfolioIn(_In):
    group_by: Literal["instrument", "asset_class", "strategy"] = "instrument"


class ExposureRow(_Out):
    key: str
    net_size: float
    gross_size: float
    net_notional: float
    gross_notional: float
    n_positions: int


class QueryPortfolioOut(_Out):
    as_of: str
    mode: str
    account_ccy: str
    equity: float
    balance: float
    margin_used: float
    free_margin: float
    n_positions: int
    gross_notional: float
    net_notional: float
    unrealised_pnl: float
    largest_position: str | None
    exposures: tuple[ExposureRow, ...]
    read_only: bool = True


def _query_portfolio(ctx: ToolContext, inputs: QueryPortfolioIn) -> QueryPortfolioOut:
    snap = ctx.portfolio.snapshot()
    buckets: dict[str, dict[str, float]] = {}
    gross = 0.0
    net = 0.0
    unrealised = 0.0
    largest: tuple[float, str] | None = None
    for pos in snap.positions:
        instrument = get_instrument(pos.instrument)
        sign = 1.0 if pos.direction.lower() == "long" else -1.0
        notional = pos.size * instrument.contract_size * pos.mark_price
        gross += abs(notional)
        net += sign * notional
        unrealised += pos.unrealised_pnl
        if largest is None or abs(notional) > largest[0]:
            largest = (abs(notional), pos.instrument)
        if inputs.group_by == "instrument":
            key = pos.instrument
        elif inputs.group_by == "asset_class":
            key = instrument.asset_class.value
        else:
            key = pos.strategy_id or "unattributed"
        row = buckets.setdefault(
            key,
            {"net_size": 0.0, "gross_size": 0.0, "net_notional": 0.0,
             "gross_notional": 0.0, "n": 0.0},
        )
        row["net_size"] += sign * pos.size
        row["gross_size"] += pos.size
        row["net_notional"] += sign * notional
        row["gross_notional"] += abs(notional)
        row["n"] += 1
    return QueryPortfolioOut(
        as_of=snap.as_of.isoformat(),
        mode=snap.mode,
        account_ccy=snap.account_ccy,
        equity=snap.equity,
        balance=snap.balance,
        margin_used=snap.margin_used,
        free_margin=snap.equity - snap.margin_used,
        n_positions=len(snap.positions),
        gross_notional=round(gross, 6),
        net_notional=round(net, 6),
        unrealised_pnl=round(unrealised, 6),
        largest_position=largest[1] if largest else None,
        exposures=tuple(
            ExposureRow(
                key=key,
                net_size=round(v["net_size"], 6),
                gross_size=round(v["gross_size"], 6),
                net_notional=round(v["net_notional"], 6),
                gross_notional=round(v["gross_notional"], 6),
                n_positions=int(v["n"]),
            )
            for key, v in sorted(buckets.items())
        ),
    )


class QueryExecutionTelemetryIn(_In):
    strategy_id: str | None = None
    instrument: str | None = None
    group_by: Literal["instrument", "strategy_id", "market_regime"] = "instrument"
    limit: int = Field(default=50, ge=1, le=500)


class TelemetryRow(_Out):
    key: str
    values: dict[str, float | None]


class QueryExecutionTelemetryOut(_Out):
    n_records: int
    n_after_filter: int
    group_by: str
    rows: tuple[TelemetryRow, ...]
    read_errors: int
    caveats: tuple[str, ...]


def _query_execution_telemetry(
    ctx: ToolContext, inputs: QueryExecutionTelemetryIn
) -> QueryExecutionTelemetryOut:
    if ctx.telemetry_dir is None:
        raise ToolExecutionError(
            "no execution-telemetry directory is wired into this context; there is "
            "nothing to read and a zero-row answer would be a lie"
        )
    reader = TelemetryReader(ctx.telemetry_dir)
    frame, report = reader.frame()
    n_total = len(frame)
    if frame.empty:
        return QueryExecutionTelemetryOut(
            n_records=0,
            n_after_filter=0,
            group_by=inputs.group_by,
            rows=(),
            read_errors=len(getattr(report, "errors", ()) or ()),
            caveats=("the telemetry log is empty; no execution has been recorded yet",),
        )
    if inputs.strategy_id:
        frame = frame[frame["strategy_id"] == inputs.strategy_id]
    if inputs.instrument:
        frame = frame[frame["instrument"] == inputs.instrument.upper()]
    if frame.empty:
        return QueryExecutionTelemetryOut(
            n_records=n_total,
            n_after_filter=0,
            group_by=inputs.group_by,
            rows=(),
            read_errors=len(getattr(report, "errors", ()) or ()),
            caveats=("the filter matched no telemetry records",),
        )
    summary = slippage_summary(frame, by=inputs.group_by)
    rows: list[TelemetryRow] = []
    for key, record in summary.head(inputs.limit).iterrows():
        rows.append(
            TelemetryRow(
                key=str(key),
                values={str(c): _finite(record[c]) for c in summary.columns},
            )
        )
    return QueryExecutionTelemetryOut(
        n_records=n_total,
        n_after_filter=len(frame),
        group_by=inputs.group_by,
        rows=tuple(rows),
        read_errors=len(getattr(report, "errors", ()) or ()),
        caveats=(
            "realised slippage is measured against the price requested at decision "
            "time; it says nothing about the fills a larger size would have received",
        ),
    )


class QueryDataQualityIn(_In):
    instrument: str
    timeframe: str
    start: str | None = None
    end: str | None = None


class DefectSummary(_Out):
    code: str
    severity: str
    count: int
    message: str


class QueryDataQualityOut(_Out):
    instrument: str
    timeframe: str
    dataset_version_id: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    quality: str
    is_clean: bool
    worst_severity: str
    defects: tuple[DefectSummary, ...]
    n_gaps: int
    checks_run: tuple[str, ...]


def _query_data_quality(ctx: ToolContext, inputs: QueryDataQualityIn) -> QueryDataQualityOut:
    timeframe = _tf(inputs.timeframe)
    frame, version_id = ctx.require_bars().load(
        inputs.instrument, timeframe, start=inputs.start, end=inputs.end
    )
    report = validate_integrity(frame, config=IntegrityConfig())
    return QueryDataQualityOut(
        instrument=inputs.instrument.upper(),
        timeframe=timeframe.value,
        dataset_version_id=version_id,
        row_count=report.row_count,
        first_timestamp=str(report.first_timestamp) if report.first_timestamp else None,
        last_timestamp=str(report.last_timestamp) if report.last_timestamp else None,
        quality=report.quality.value,
        is_clean=report.is_clean,
        worst_severity=report.worst_severity.value,
        defects=tuple(
            DefectSummary(
                code=d.code.value,
                severity=d.severity.value,
                count=d.count,
                message=d.message,
            )
            for d in report.defects
        ),
        n_gaps=len(report.gaps),
        checks_run=tuple(report.checks_run),
    )


# ---------------------------------------------------------------------------
# EXTERNAL (interface only)
# ---------------------------------------------------------------------------


class SearchWebIn(_In):
    query: str = Field(min_length=3, max_length=500)
    max_results: int = Field(default=5, ge=1, le=25)


class WebResult(_Out):
    title: str
    url: str
    snippet: str


class SearchWebOut(_Out):
    query: str
    results: tuple[WebResult, ...]
    provider_configured: bool
    note: str


def _search_web(ctx: ToolContext, inputs: SearchWebIn) -> SearchWebOut:
    configured = bool(getattr(ctx.web, "configured", False))
    raw = ctx.web.search(inputs.query, inputs.max_results) if configured else ()
    return SearchWebOut(
        query=inputs.query,
        results=tuple(
            WebResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                snippet=str(r.get("snippet", "")),
            )
            for r in raw
        ),
        provider_configured=configured,
        note=(
            "results from the configured provider"
            if configured
            else "NO web provider is configured. An empty result means 'not searched', "
            "not 'nothing exists'. Do not treat this as evidence either way."
        ),
    )


class FetchResearchIn(_In):
    url: str = Field(min_length=8, max_length=2000)


class FetchResearchOut(_Out):
    url: str
    status: str
    content: str
    provider_configured: bool
    note: str


def _fetch_research(ctx: ToolContext, inputs: FetchResearchIn) -> FetchResearchOut:
    configured = bool(getattr(ctx.web, "configured", False))
    raw = ctx.web.fetch(inputs.url) if configured else {"status": "not_fetched", "content": ""}
    return FetchResearchOut(
        url=inputs.url,
        status=str(raw.get("status", "not_fetched")),
        content=str(raw.get("content", ""))[:20_000],
        provider_configured=configured,
        note=(
            "fetched by the configured provider"
            if configured
            else "NO fetch provider is configured; nothing was retrieved"
        ),
    )


# ---------------------------------------------------------------------------
# PROPOSAL TOOLS -- research domain writes only
# ---------------------------------------------------------------------------


class CreateHypothesisIn(_In):
    title: str = Field(min_length=8, max_length=200)
    statement: str = Field(min_length=40)
    rationale: str = Field(min_length=40)
    testable_prediction: str = Field(min_length=20)
    falsifier: str = Field(min_length=20)
    instruments: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    prior_belief: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: tuple[str, ...] = ()
    supersedes: str | None = None


class CreateHypothesisOut(_Out):
    hypothesis_id: str
    title: str
    created_at: str


def _create_hypothesis(ctx: ToolContext, inputs: CreateHypothesisIn) -> CreateHypothesisOut:
    unknown = [s for s in inputs.instruments if not _instrument_known(s)]
    if unknown:
        raise ToolExecutionError(
            f"hypothesis names unregistered instruments {unknown}; register them in "
            "core/instruments.py rather than hypothesising about a symbol with no "
            "contract spec"
        )
    for tf in inputs.timeframes:
        _tf(tf)
    record = ctx.research.add_hypothesis(
        Hypothesis(
            title=inputs.title,
            statement=inputs.statement,
            rationale=inputs.rationale,
            testable_prediction=inputs.testable_prediction,
            falsifier=inputs.falsifier,
            instruments=tuple(s.upper() for s in inputs.instruments),
            timeframes=inputs.timeframes,
            prior_belief=inputs.prior_belief,
            tags=inputs.tags,
            supersedes=inputs.supersedes,
            created_by=ctx.agent_id,
            role=ctx.role,
        )
    )
    return CreateHypothesisOut(
        hypothesis_id=record.hypothesis_id,
        title=record.title,
        created_at=record.created_at.isoformat(),
    )


def _instrument_known(symbol: str) -> bool:
    try:
        get_instrument(symbol)
    except KeyError:
        return False
    return True


class CreateStrategyIn(_In):
    document: dict[str, Any]
    hypothesis_id: str | None = None
    rationale: str = ""


class CreateStrategyOut(_Out):
    proposal_id: str
    strategy_id: str
    content_hash: str
    warmup_period: int
    complexity_score: float
    n_rules: int
    duplicate_of: str | None = None


def _create_strategy(ctx: ToolContext, inputs: CreateStrategyIn) -> CreateStrategyOut:
    """Validate and register an agent-proposed DSL document.

    Anything that is not a valid document is refused at the sandbox boundary:
    unknown fields, code-bearing strings, schemas that do not validate, and
    documents that do not compile.
    """
    try:
        accepted = validate_strategy_payload(inputs.document)
    except SandboxRejection as exc:
        raise ToolExecutionError(
            f"proposal refused at the sandbox boundary: {exc}"
        ) from exc

    existing = ctx.strategies.by_hash(accepted.content_hash)
    if existing is not None and existing.strategy_id != accepted.strategy_id:
        raise ToolExecutionError(
            f"this document is semantically identical to {existing.strategy_id!r} "
            f"(hash {accepted.content_hash[:12]}); registering it again would "
            "double-count it in every ranking"
        )
    ctx.strategies.register(accepted.document, replace=True)
    record = ctx.research.add_proposal(
        StrategyProposal(
            strategy_id=accepted.strategy_id,
            content_hash=accepted.content_hash,
            document=accepted.document.model_dump(mode="json"),
            hypothesis_id=inputs.hypothesis_id,
            warmup_period=accepted.warmup_period,
            complexity_score=accepted.complexity_score,
            rationale=inputs.rationale,
            created_by=ctx.agent_id,
            role=ctx.role,
        )
    )
    return CreateStrategyOut(
        proposal_id=record.proposal_id,
        strategy_id=accepted.strategy_id,
        content_hash=accepted.content_hash,
        warmup_period=accepted.warmup_period,
        complexity_score=accepted.complexity_score,
        n_rules=len(accepted.document.all_rules()),
        duplicate_of=None,
    )


class MutationOperator(str, Enum):
    """The complete, enumerated set of edits a mutation agent may propose.

    A mutation is a NAMED transformation with typed arguments, not a free-form
    patch.  An agent cannot write an arbitrary field, because there is no
    operator that accepts a field path.
    """

    SCALE_STOP = "scale_stop"
    SET_STOP_VALUE = "set_stop_value"
    ADD_TAKE_PROFIT_LEG = "add_take_profit_leg"
    REMOVE_TAKE_PROFIT_LEG = "remove_take_profit_leg"
    SET_BREAKEVEN_TRAILING = "set_breakeven_trailing"
    CLEAR_TRAILING = "clear_trailing"
    SET_MAX_BARS_IN_TRADE = "set_max_bars_in_trade"
    SET_COOLDOWN_BARS = "set_cooldown_bars"
    SET_MAX_CONCURRENT_POSITIONS = "set_max_concurrent_positions"
    RESTRICT_UNIVERSE = "restrict_universe"
    RESTRICT_TIMEFRAMES = "restrict_timeframes"
    SET_REGIME_GATE_BOUNDS = "set_regime_gate_bounds"
    DROP_REGIME_RULE = "drop_regime_rule"
    DROP_FILTER_RULE = "drop_filter_rule"


class MutateStrategyIn(_In):
    parent_strategy_id: str
    new_strategy_id: str
    operator: MutationOperator
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=4000)
    name_suffix: str = Field(default="variant", max_length=40)


class MutateStrategyOut(_Out):
    proposal_id: str
    strategy_id: str
    parent_strategy_id: str
    parent_content_hash: str
    content_hash: str
    operator: str
    generation: int
    complexity_delta: float
    warmup_period: int


def _mutate_document(
    doc: StrategyDocument,
    operator: MutationOperator,
    args: Mapping[str, Any],
    *,
    new_strategy_id: str,
    name_suffix: str,
    rationale: str,
) -> dict[str, Any]:
    """Apply one enumerated operator to a dumped document.  Pure data in, out."""
    data = doc.model_dump(mode="json")
    data.pop("complexity_score", None)

    def _need(key: str) -> Any:
        if key not in args:
            raise ToolExecutionError(f"{operator.value} requires argument {key!r}")
        return args[key]

    if operator is MutationOperator.SCALE_STOP:
        factor = float(_need("factor"))
        if not 0.1 <= factor <= 10.0:
            raise ToolExecutionError("scale_stop factor must be within [0.1, 10.0]")
        data["stop"]["value"] = round(float(data["stop"]["value"]) * factor, 10)
    elif operator is MutationOperator.SET_STOP_VALUE:
        value = float(_need("value"))
        if value <= 0:
            raise ToolExecutionError("a stop value must be positive")
        data["stop"]["value"] = value
    elif operator is MutationOperator.ADD_TAKE_PROFIT_LEG:
        leg = {
            "kind": "r_multiple",
            "value": float(_need("r_multiple")),
            "allocation": float(_need("allocation")),
            "atr": None,
            "level": None,
            "label": str(args.get("label", "")),
        }
        data["take_profits"] = [*data.get("take_profits", []), leg]
    elif operator is MutationOperator.REMOVE_TAKE_PROFIT_LEG:
        index = int(_need("index"))
        legs = list(data.get("take_profits", []))
        if not 0 <= index < len(legs):
            raise ToolExecutionError(f"take-profit index {index} out of range")
        legs.pop(index)
        data["take_profits"] = legs
    elif operator is MutationOperator.SET_BREAKEVEN_TRAILING:
        data["trailing"] = {
            "kind": "breakeven_after_r",
            "value": 0.0,
            "activate_after_r": float(_need("activate_after_r")),
            "atr": None,
            "level": None,
        }
    elif operator is MutationOperator.CLEAR_TRAILING:
        data["trailing"] = None
    elif operator is MutationOperator.SET_MAX_BARS_IN_TRADE:
        bars = args.get("bars")
        data["position_management"]["max_bars_in_trade"] = (
            None if bars is None else int(bars)
        )
    elif operator is MutationOperator.SET_COOLDOWN_BARS:
        data["position_management"]["cooldown_bars_after_exit"] = int(_need("bars"))
    elif operator is MutationOperator.SET_MAX_CONCURRENT_POSITIONS:
        data["position_management"]["max_concurrent_positions"] = int(_need("value"))
    elif operator is MutationOperator.RESTRICT_UNIVERSE:
        wanted = [str(s).upper() for s in _need("instruments")]
        outside = [s for s in wanted if s not in doc.universe]
        if outside:
            raise ToolExecutionError(
                f"restrict_universe may only narrow the parent universe; {outside} "
                "are not in it. Widening a universe is a new strategy, not a mutation."
            )
        data["universe"] = wanted
    elif operator is MutationOperator.RESTRICT_TIMEFRAMES:
        wanted = [str(s) for s in _need("timeframes")]
        allowed = {t.value for t in doc.timeframes}
        outside = [s for s in wanted if s not in allowed]
        if outside:
            raise ToolExecutionError(
                f"restrict_timeframes may only narrow the parent set; {outside} are "
                "not in it"
            )
        data["timeframes"] = wanted
    elif operator is MutationOperator.SET_REGIME_GATE_BOUNDS:
        index = int(args.get("index", 0))
        rules = list(data.get("regime", []))
        if not 0 <= index < len(rules):
            raise ToolExecutionError(f"regime rule index {index} out of range")
        if rules[index].get("op") != "regime_gate":
            raise ToolExecutionError(f"regime rule {index} is not a regime_gate")
        if "min_value" in args:
            rules[index]["min_value"] = (
                None if args["min_value"] is None else float(args["min_value"])
            )
        if "max_value" in args:
            rules[index]["max_value"] = (
                None if args["max_value"] is None else float(args["max_value"])
            )
        data["regime"] = rules
    elif operator is MutationOperator.DROP_REGIME_RULE:
        index = int(_need("index"))
        rules = list(data.get("regime", []))
        if not 0 <= index < len(rules):
            raise ToolExecutionError(f"regime rule index {index} out of range")
        rules.pop(index)
        data["regime"] = rules
    elif operator is MutationOperator.DROP_FILTER_RULE:
        index = int(_need("index"))
        rules = list(data.get("filters", []))
        if not 0 <= index < len(rules):
            raise ToolExecutionError(f"filter index {index} out of range")
        rules.pop(index)
        data["filters"] = rules
    else:  # pragma: no cover - the enum is exhaustive
        raise ToolExecutionError(f"unhandled operator {operator}")

    generation = (doc.mutation.generation + 1) if doc.mutation else 1
    data["strategy_id"] = new_strategy_id
    data["name"] = f"{doc.name} ({name_suffix})"[:120]
    data["parent_strategy_ids"] = [doc.strategy_id]
    data["mutation"] = {
        "parent_hash": doc.content_hash(),
        "operator": operator.value,
        "description": rationale[:2000],
        "generation": generation,
    }
    return data


def _mutate_strategy(ctx: ToolContext, inputs: MutateStrategyIn) -> MutateStrategyOut:
    try:
        parent = ctx.strategies.get(inputs.parent_strategy_id)
    except KeyError as exc:
        raise ToolExecutionError(str(exc)) from exc
    if inputs.new_strategy_id == parent.strategy_id:
        raise ToolExecutionError(
            "a mutation produces a NEW strategy id; overwriting the parent would "
            "destroy the lineage that makes a search auditable"
        )
    mutated = _mutate_document(
        parent,
        inputs.operator,
        inputs.arguments,
        new_strategy_id=inputs.new_strategy_id,
        name_suffix=inputs.name_suffix,
        rationale=inputs.rationale,
    )
    try:
        accepted = validate_strategy_payload(mutated)
    except SandboxRejection as exc:
        raise ToolExecutionError(f"mutation produced an invalid document: {exc}") from exc
    if accepted.content_hash == parent.content_hash():
        raise ToolExecutionError(
            f"{inputs.operator.value} left the strategy semantically unchanged "
            "(identical content hash); a no-op mutation must not enter the population"
        )
    existing = ctx.strategies.by_hash(accepted.content_hash)
    if existing is not None and existing.strategy_id != accepted.strategy_id:
        raise ToolExecutionError(
            f"the mutated document is identical to {existing.strategy_id!r}; this "
            "branch of the search has already been explored"
        )
    ctx.strategies.register(accepted.document, replace=True)
    record = ctx.research.add_proposal(
        StrategyProposal(
            strategy_id=accepted.strategy_id,
            content_hash=accepted.content_hash,
            document=accepted.document.model_dump(mode="json"),
            parent_strategy_id=parent.strategy_id,
            parent_content_hash=parent.content_hash(),
            mutation_operator=inputs.operator.value,
            mutation_arguments=dict(inputs.arguments),
            generation=accepted.document.mutation.generation if accepted.document.mutation else 1,
            warmup_period=accepted.warmup_period,
            complexity_score=accepted.complexity_score,
            rationale=inputs.rationale,
            created_by=ctx.agent_id,
            role=ctx.role,
        )
    )
    return MutateStrategyOut(
        proposal_id=record.proposal_id,
        strategy_id=accepted.strategy_id,
        parent_strategy_id=parent.strategy_id,
        parent_content_hash=parent.content_hash(),
        content_hash=accepted.content_hash,
        operator=inputs.operator.value,
        generation=record.generation,
        complexity_delta=round(accepted.complexity_score - parent.complexity_score, 6),
        warmup_period=accepted.warmup_period,
    )


class SuccessCriterionIn(_In):
    metric: str
    comparator: Literal[">", ">=", "<", "<=", "=="]
    threshold: float
    rationale: str = ""


class DesignExperimentIn(_In):
    hypothesis_id: str
    strategy_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    instruments: tuple[str, ...] = Field(min_length=1, max_length=80)
    timeframes: tuple[str, ...] = Field(min_length=1, max_length=8)
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    embargo_bars: int = Field(default=0, ge=0, le=5000)
    n_folds: int = Field(default=1, ge=1, le=64)
    seed: int = Field(default=0, ge=0)
    n_trials_in_search: int = Field(default=1, ge=1)
    success_criteria: tuple[SuccessCriterionIn, ...] = Field(min_length=1, max_length=20)
    notes: str = ""


class DesignExperimentOut(_Out):
    experiment_id: str
    hypothesis_id: str
    strategy_ids: tuple[str, ...]
    n_criteria: int
    pre_registered_at: str


def _design_experiment(ctx: ToolContext, inputs: DesignExperimentIn) -> DesignExperimentOut:
    ctx.research.get_hypothesis(inputs.hypothesis_id)  # raises if unknown
    for sid in inputs.strategy_ids:
        if sid not in ctx.strategies:
            raise ToolExecutionError(f"experiment names unregistered strategy {sid!r}")
    unknown = [s for s in inputs.instruments if not _instrument_known(s)]
    if unknown:
        raise ToolExecutionError(f"experiment names unregistered instruments {unknown}")
    for tf in inputs.timeframes:
        _tf(tf)
    train_start = pd.Timestamp(inputs.train_start, tz="UTC")
    train_end = pd.Timestamp(inputs.train_end, tz="UTC")
    test_start = pd.Timestamp(inputs.test_start, tz="UTC")
    test_end = pd.Timestamp(inputs.test_end, tz="UTC")
    if train_start >= train_end:
        raise ToolExecutionError("train_start must precede train_end")
    if test_start >= test_end:
        raise ToolExecutionError("test_start must precede test_end")
    if test_start < train_end:
        raise ToolExecutionError(
            "the test window starts before the train window ends. Overlapping windows "
            "leak the answer into the question; shift the test window forward and use "
            "embargo_bars for the label-span overlap."
        )
    record = ctx.research.add_experiment(
        ExperimentDesign(
            hypothesis_id=inputs.hypothesis_id,
            strategy_ids=inputs.strategy_ids,
            instruments=tuple(s.upper() for s in inputs.instruments),
            timeframes=inputs.timeframes,
            train_start=inputs.train_start,
            train_end=inputs.train_end,
            test_start=inputs.test_start,
            test_end=inputs.test_end,
            embargo_bars=inputs.embargo_bars,
            n_folds=inputs.n_folds,
            seed=inputs.seed,
            n_trials_in_search=inputs.n_trials_in_search,
            success_criteria=tuple(
                SuccessCriterion(
                    metric=c.metric,
                    comparator=c.comparator,
                    threshold=c.threshold,
                    rationale=c.rationale,
                )
                for c in inputs.success_criteria
            ),
            notes=inputs.notes,
            created_by=ctx.agent_id,
            role=ctx.role,
        )
    )
    return DesignExperimentOut(
        experiment_id=record.experiment_id,
        hypothesis_id=record.hypothesis_id,
        strategy_ids=record.strategy_ids,
        n_criteria=len(record.success_criteria),
        pre_registered_at=record.created_at.isoformat(),
    )


class ObjectionIn(_In):
    claim: str = Field(min_length=20)
    decisive_test: str = Field(min_length=20)
    severity: Literal["fatal", "major", "minor"]
    evidence: str = ""


class RecordCritiqueIn(_In):
    target_kind: Literal["strategy", "backtest", "validation_report", "experiment"]
    target_id: str
    objections: tuple[ObjectionIn, ...] = Field(min_length=1, max_length=20)
    verdict: Literal["reject", "revise", "survives_this_attack"]
    summary: str = ""


class RecordCritiqueOut(_Out):
    critique_id: str
    target_id: str
    verdict: str
    n_objections: int
    n_fatal: int


#: Phrases that are caution, not objections.  The critic's job is falsifiable
#: attack; a critique made only of these is refused.
_VAGUE_PHRASES = (
    "past performance",
    "be careful",
    "may not generalise",
    "may not generalize",
    "results could vary",
    "more research is needed",
    "no guarantee",
)


def _record_critique(ctx: ToolContext, inputs: RecordCritiqueIn) -> RecordCritiqueOut:
    for i, obj in enumerate(inputs.objections):
        residue = obj.decisive_test.lower()
        for phrase in _VAGUE_PHRASES:
            residue = residue.replace(phrase, " ")
        if len(residue.strip()) < 15:
            raise ToolExecutionError(
                f"objection {i}'s decisive_test is generic caution ({obj.decisive_test!r}), "
                "not a measurement. Name the test whose outcome would settle the claim."
            )
        if obj.decisive_test.strip().lower() == obj.claim.strip().lower():
            raise ToolExecutionError(
                f"objection {i} restates its claim as its test; a decisive test must "
                "describe a measurement whose outcome would settle the claim"
            )
    record = ctx.research.add_critique(
        Critique(
            target_kind=inputs.target_kind,
            target_id=inputs.target_id,
            objections=tuple(
                Objection(
                    claim=o.claim,
                    decisive_test=o.decisive_test,
                    severity=o.severity,
                    evidence=o.evidence,
                )
                for o in inputs.objections
            ),
            verdict=inputs.verdict,
            summary=inputs.summary,
            created_by=ctx.agent_id,
            role=ctx.role,
        )
    )
    return RecordCritiqueOut(
        critique_id=record.critique_id,
        target_id=record.target_id,
        verdict=record.verdict,
        n_objections=len(record.objections),
        n_fatal=sum(1 for o in record.objections if o.severity == "fatal"),
    )


class FileResearchNoteIn(_In):
    title: str = Field(min_length=6, max_length=200)
    body: str = Field(min_length=20)
    tags: tuple[str, ...] = ()
    links: dict[str, str] = Field(default_factory=dict)
    supersedes: str | None = None


class FileResearchNoteOut(_Out):
    note_id: str
    title: str
    n_links: int
    created_at: str


def _file_research_note(ctx: ToolContext, inputs: FileResearchNoteIn) -> FileResearchNoteOut:
    record = ctx.research.add_note(
        ResearchNote(
            title=inputs.title,
            body=inputs.body,
            tags=inputs.tags,
            links=dict(inputs.links),
            supersedes=inputs.supersedes,
            created_by=ctx.agent_id,
            role=ctx.role,
        )
    )
    return FileResearchNoteOut(
        note_id=record.note_id,
        title=record.title,
        n_links=len(record.links),
        created_at=record.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# JOB SUBMISSION TOOLS -- the agent queues work; a worker runs it
# ---------------------------------------------------------------------------


class JobTicketOut(_Out):
    job_id: str
    queue: str
    job_type: str
    status: str
    idempotency_key: str
    deduplicated: bool
    note: str = (
        "Queued. A deterministic worker executes this; the agent that submitted it "
        "does not run the engine and cannot alter the result."
    )


def _ticket_out(ticket: JobTicket) -> JobTicketOut:
    return JobTicketOut(
        job_id=ticket.job_id,
        queue=ticket.queue,
        job_type=ticket.job_type,
        status=ticket.status,
        idempotency_key=ticket.idempotency_key,
        deduplicated=ticket.deduplicated,
    )


def _submit(ctx: ToolContext, job_type: JobType, payload: Mapping[str, Any], queue: str) -> JobTicketOut:
    submitter = ctx.require_submitter()
    spec = JobSpec(
        job_type=job_type,
        queue=queue or ctx.default_queue,
        payload=dict(payload),
        submitted_by=ctx.agent_id,
        role=ctx.role,
        required_capabilities=frozenset({Capability.SUBMIT_JOB}),
        trigger="agent",
    )
    return _ticket_out(submitter(spec))


class RunBacktestIn(_In):
    strategy_id: str
    instrument: str
    timeframe: str
    start: str | None = None
    end: str | None = None
    initial_balance: float = Field(default=10_000.0, gt=0)
    risk_fraction: float = Field(default=0.01, gt=0.0, le=0.05)
    account_ccy: str = "USD"
    profile: str = "ig_realistic"
    experiment_id: str | None = None
    label: str = ""
    queue: str = "research"


def _run_backtest(ctx: ToolContext, inputs: RunBacktestIn) -> JobTicketOut:
    _tf(inputs.timeframe)
    return _submit(
        ctx,
        JobType.BACKTEST,
        inputs.model_dump(exclude={"queue"}),
        inputs.queue,
    )


class RunValidationIn(_In):
    """Note what an agent CANNOT set here: the thresholds.

    There is no ``min_trades`` and no ``dsr_threshold`` field, because the bars
    are the platform's (``GATE_SET_V2``) and moving them is not a research
    decision. An agent supplies the honest trial count and the seed; the gates
    decide.
    """

    backtest_id: str
    #: How many things the search actually tried. Understating it inflates the
    #: deflated Sharpe, which is the one number that corrects for the search.
    n_trials_in_search: int = Field(default=1, ge=1)
    bootstrap_samples: int = Field(default=500, ge=100, le=5000)
    seed: int = Field(default=0, ge=0)
    experiment_id: str | None = None
    queue: str = "research"


def _run_validation(ctx: ToolContext, inputs: RunValidationIn) -> JobTicketOut:
    return _submit(ctx, JobType.VALIDATION, inputs.model_dump(exclude={"queue"}), inputs.queue)


class RunWalkforwardIn(_In):
    strategy_id: str
    instrument: str
    timeframe: str
    n_folds: int = Field(default=4, ge=2, le=24)
    embargo_bars: int = Field(default=0, ge=0, le=5000)
    start: str | None = None
    end: str | None = None
    initial_balance: float = Field(default=10_000.0, gt=0)
    risk_fraction: float = Field(default=0.01, gt=0.0, le=0.05)
    account_ccy: str = "USD"
    experiment_id: str | None = None
    queue: str = "research"


def _run_walkforward(ctx: ToolContext, inputs: RunWalkforwardIn) -> JobTicketOut:
    _tf(inputs.timeframe)
    return _submit(ctx, JobType.WALKFORWARD, inputs.model_dump(exclude={"queue"}), inputs.queue)


class RunAblationIn(_In):
    strategy_id: str
    instrument: str
    timeframe: str
    components: tuple[
        Literal["regime", "filters", "confirmation", "trailing", "take_profits", "sessions"], ...
    ] = ("regime", "filters", "confirmation", "trailing", "take_profits")
    start: str | None = None
    end: str | None = None
    initial_balance: float = Field(default=10_000.0, gt=0)
    risk_fraction: float = Field(default=0.01, gt=0.0, le=0.05)
    account_ccy: str = "USD"
    experiment_id: str | None = None
    queue: str = "research"


def _run_ablation(ctx: ToolContext, inputs: RunAblationIn) -> JobTicketOut:
    _tf(inputs.timeframe)
    return _submit(ctx, JobType.ABLATION, inputs.model_dump(exclude={"queue"}), inputs.queue)


class RunSensitivityIn(_In):
    backtest_id: str
    n_samples: int = Field(default=200, ge=20, le=2000)
    seed: int = Field(default=0, ge=0)
    experiment_id: str | None = None
    queue: str = "research"


def _run_sensitivity(ctx: ToolContext, inputs: RunSensitivityIn) -> JobTicketOut:
    return _submit(ctx, JobType.SENSITIVITY, inputs.model_dump(exclude={"queue"}), inputs.queue)


# ---------------------------------------------------------------------------
# The default registry
# ---------------------------------------------------------------------------

_READ_BUDGET = ToolBudget(max_calls_per_job=40, max_calls_per_minute=120, max_rows_returned=500)
_WRITE_BUDGET = ToolBudget(max_calls_per_job=10, max_calls_per_minute=30, max_rows_returned=1)
_JOB_BUDGET = ToolBudget(max_calls_per_job=8, max_calls_per_minute=20, max_rows_returned=1)
_EXTERNAL_BUDGET = ToolBudget(
    max_calls_per_job=6, max_calls_per_minute=10, estimated_cost_usd=0.002, max_rows_returned=25
)


def build_registry() -> ToolRegistry:
    """Construct the complete tool registry.

    Read this function as the answer to "what can an autonomous agent in Fiboki
    actually DO".  Twelve read tools, six research-domain writes, five job
    submissions, two external interfaces.  No execution.
    """
    registry = ToolRegistry()

    def add(
        name: str,
        description: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        capability: Capability,
        handler: Any,
        *,
        write_domain: WriteDomain = WriteDomain.NONE,
        budget: ToolBudget = _READ_BUDGET,
    ) -> None:
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                input_model=input_model,
                output_model=output_model,
                capability=capability,
                write_domain=write_domain,
                mutates=write_domain is not WriteDomain.NONE,
                budget=budget,
                handler=handler,
            )
        )

    # -- reads ------------------------------------------------------------
    add(
        "query_market_data",
        "Summarise a bar window: coverage, price extremes, return moments, realised "
        "volatility and an optional tail of bars. Returns the dataset version id so "
        "any conclusion can be traced to exact bytes.",
        QueryMarketDataIn, QueryMarketDataOut, Capability.READ_MARKET_DATA, _query_market_data,
    )
    add(
        "query_regime",
        "Read the platform's regime classification for an instrument: the five-axis "
        "regime vector (direction, volatility, persistence, liquidity, stress), its "
        "distribution over the window, and the classifier fingerprint that produced "
        "it. The agent reads this classification; it does not define it.",
        QueryRegimeIn, QueryRegimeOut, Capability.READ_REGIME, _query_regime,
    )
    add(
        "query_experiment_history",
        "List pre-registered experiments, optionally filtered by strategy or "
        "hypothesis, including their success criteria and windows.",
        QueryExperimentHistoryIn, QueryExperimentHistoryOut,
        Capability.READ_EXPERIMENTS, _query_experiment_history,
    )
    add(
        "query_research_memory",
        "Search filed research notes by text and tags. This is the institutional "
        "memory of what has already been tried and what it showed.",
        QueryResearchMemoryIn, QueryResearchMemoryOut,
        Capability.READ_RESEARCH_MEMORY, _query_research_memory,
    )
    add(
        "query_strategy",
        "Read one registered strategy: its hypothesis, rules, exits, complexity, "
        "content hash and any registry health issues.",
        QueryStrategyIn, QueryStrategyOut, Capability.READ_STRATEGY, _query_strategy,
    )
    add(
        "compare_candidates",
        "Compare recorded backtest metrics across candidates, excluding any below the "
        "minimum trade count, and state the multiple-testing caveat.",
        CompareCandidatesIn, CompareCandidatesOut, Capability.READ_EXPERIMENTS, _compare_candidates,
    )
    add(
        "inspect_trade",
        "Read individual trades from a recorded backtest by id, index, or the worst N "
        "by net P&L. Refuses to dump a whole ledger.",
        InspectTradeIn, InspectTradeOut, Capability.READ_TRADE_LEDGER, _inspect_trade,
    )
    add(
        "inspect_drawdown",
        "Decompose the equity curve into drawdown episodes, ranked by depth, with the "
        "worst trades inside each one.",
        InspectDrawdownIn, InspectDrawdownOut, Capability.READ_TRADE_LEDGER, _inspect_drawdown,
    )
    add(
        "inspect_validation_report",
        "Read a deterministic validation report: every gate, its value, its threshold, "
        "the verdict and the stated caveats.",
        InspectValidationReportIn, InspectValidationReportOut,
        Capability.READ_VALIDATION_REPORT, _inspect_validation_report,
    )
    add(
        "query_portfolio",
        "Read-only portfolio snapshot: equity, margin, open positions and exposure "
        "grouped by instrument, asset class or strategy.",
        QueryPortfolioIn, QueryPortfolioOut, Capability.READ_PORTFOLIO, _query_portfolio,
    )
    add(
        "query_execution_telemetry",
        "Summarise recorded execution telemetry: realised slippage, fill ratios and "
        "latency, grouped by instrument, strategy or regime.",
        QueryExecutionTelemetryIn, QueryExecutionTelemetryOut,
        Capability.READ_EXECUTION_TELEMETRY, _query_execution_telemetry,
    )
    add(
        "query_data_quality",
        "Run the integrity checker over a bar window and return its defects, gaps and "
        "quality verdict. Detects; never repairs.",
        QueryDataQualityIn, QueryDataQualityOut, Capability.READ_DATA_QUALITY, _query_data_quality,
    )

    # -- external (interface only) ---------------------------------------
    add(
        "search_web",
        "INTERFACE ONLY. Search external literature. With no provider configured this "
        "returns nothing and says so; an empty result is not evidence of absence.",
        SearchWebIn, SearchWebOut, Capability.READ_EXTERNAL_WEB, _search_web,
        budget=_EXTERNAL_BUDGET,
    )
    add(
        "fetch_research",
        "INTERFACE ONLY. Fetch one external document by URL. Inert without a "
        "configured provider.",
        FetchResearchIn, FetchResearchOut, Capability.READ_EXTERNAL_WEB, _fetch_research,
        budget=_EXTERNAL_BUDGET,
    )

    # -- research-domain writes ------------------------------------------
    add(
        "create_hypothesis",
        "Record a falsifiable hypothesis BEFORE testing it. Requires a testable "
        "prediction and an explicit falsifier.",
        CreateHypothesisIn, CreateHypothesisOut, Capability.WRITE_HYPOTHESIS, _create_hypothesis,
        write_domain=WriteDomain.RESEARCH_HYPOTHESIS, budget=_WRITE_BUDGET,
    )
    add(
        "create_strategy",
        "Register a strategy DSL document. The document is validated against the "
        "schema and compiled; unknown fields, code-bearing strings and documents that "
        "do not compile are refused.",
        CreateStrategyIn, CreateStrategyOut, Capability.WRITE_STRATEGY_PROPOSAL, _create_strategy,
        write_domain=WriteDomain.RESEARCH_STRATEGY_PROPOSAL, budget=_WRITE_BUDGET,
    )
    add(
        "mutate_strategy",
        "Derive a new strategy from a parent by applying ONE named mutation operator "
        "with typed arguments. There is no operator that writes an arbitrary field.",
        MutateStrategyIn, MutateStrategyOut, Capability.WRITE_STRATEGY_MUTATION, _mutate_strategy,
        write_domain=WriteDomain.RESEARCH_STRATEGY_PROPOSAL, budget=_WRITE_BUDGET,
    )
    add(
        "design_experiment",
        "Pre-register an experiment: windows, folds, embargo, seed, search size and "
        "the success criteria that will decide it. Overlapping train/test windows are "
        "refused.",
        DesignExperimentIn, DesignExperimentOut,
        Capability.WRITE_EXPERIMENT_DESIGN, _design_experiment,
        write_domain=WriteDomain.RESEARCH_EXPERIMENT, budget=_WRITE_BUDGET,
    )
    add(
        "record_critique",
        "File an adversarial critique. Every objection must carry a decisive test; "
        "generic caution is refused.",
        RecordCritiqueIn, RecordCritiqueOut, Capability.WRITE_CRITIQUE, _record_critique,
        write_domain=WriteDomain.RESEARCH_CRITIQUE, budget=_WRITE_BUDGET,
    )
    add(
        "file_research_note",
        "File a research note into institutional memory, linked to the artefacts it "
        "concerns.",
        FileResearchNoteIn, FileResearchNoteOut, Capability.WRITE_RESEARCH_NOTE,
        _file_research_note,
        write_domain=WriteDomain.RESEARCH_NOTE, budget=_WRITE_BUDGET,
    )

    # -- queued jobs ------------------------------------------------------
    add(
        "run_backtest",
        "QUEUE a backtest. The agent does not run the engine: a deterministic worker "
        "drains the queue and records the result.",
        RunBacktestIn, JobTicketOut, Capability.SUBMIT_JOB, _run_backtest,
        write_domain=WriteDomain.JOB_QUEUE, budget=_JOB_BUDGET,
    )
    add(
        "run_validation",
        "QUEUE the platform's promotion gates (GATE_SET_V2) over a recorded "
        "backtest. Gates a single backtest cannot answer are reported as "
        "NOT_EVALUATED, which blocks promotion; the thresholds are not yours to set.",
        RunValidationIn, JobTicketOut, Capability.SUBMIT_JOB, _run_validation,
        write_domain=WriteDomain.JOB_QUEUE, budget=_JOB_BUDGET,
    )
    add(
        "run_walkforward",
        "QUEUE a sequential walk-forward with an embargo between folds.",
        RunWalkforwardIn, JobTicketOut, Capability.SUBMIT_JOB, _run_walkforward,
        write_domain=WriteDomain.JOB_QUEUE, budget=_JOB_BUDGET,
    )
    add(
        "run_ablation",
        "QUEUE an ablation study: re-run with each declared component removed to see "
        "which ones actually carry the result.",
        RunAblationIn, JobTicketOut, Capability.SUBMIT_JOB, _run_ablation,
        write_domain=WriteDomain.JOB_QUEUE, budget=_JOB_BUDGET,
    )
    add(
        "run_sensitivity",
        "QUEUE an execution-assumption sensitivity sweep (spread, slippage, delay, "
        "missed fills, window) over a recorded backtest.",
        RunSensitivityIn, JobTicketOut, Capability.SUBMIT_JOB, _run_sensitivity,
        write_domain=WriteDomain.JOB_QUEUE, budget=_JOB_BUDGET,
    )
    return registry


#: The registry the platform uses.  Built once at import; tests enumerate it.
REGISTRY: ToolRegistry = build_registry()


__all__ = [
    "MIN_TRADES_FOR_PROMOTION",
    "REGISTRY",
    "BarSource",
    "DataStoreBarSource",
    "InMemoryBarSource",
    "MutationOperator",
    "PortfolioProvider",
    "PortfolioSnapshot",
    "PositionView",
    "StaticPortfolioProvider",
    "StubWebSearch",
    "ToolBudget",
    "ToolContext",
    "ToolExecutionError",
    "ToolRegistrationError",
    "ToolRegistry",
    "ToolSpec",
    "WebSearchProvider",
    "WriteDomain",
    "build_registry",
]
