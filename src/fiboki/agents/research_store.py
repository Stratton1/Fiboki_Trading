"""The research domain: the only place an agent's writes can land.

Every artefact an agent may create -- a hypothesis, a strategy proposal, a
mutation, an experiment design, a critique, a filed note -- is a record in this
store.  Nothing here touches market data, risk configuration, execution state
or the broker.  That is the point: the write surface available to an LLM is
this module and nothing else.

Records are append-only in spirit and in API.  There is no ``update`` method.
A revised hypothesis is a NEW hypothesis with ``supersedes`` pointing at the
old one, so the lineage of a research programme is readable end to end.  Job
results are likewise appended, never overwritten, because a re-run that
disagrees with an earlier run is a finding, not a correction.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0.0"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _rid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    created_at: datetime = Field(default_factory=_now)
    created_by: str = ""
    role: str = ""
    audit_action_id: str | None = None


# ---------------------------------------------------------------------------
# Artefacts
# ---------------------------------------------------------------------------


class Hypothesis(_Record):
    """A falsifiable claim about the market, written down BEFORE the test.

    ``falsifier`` is mandatory and non-trivial: a hypothesis with no statement
    of what would disprove it is not a hypothesis, it is a hope.
    """

    hypothesis_id: str = Field(default_factory=lambda: _rid("hyp"))
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


class StrategyProposal(_Record):
    """A DSL document an agent proposed, with its derived, verified facts."""

    proposal_id: str = Field(default_factory=lambda: _rid("prop"))
    strategy_id: str
    content_hash: str
    document: dict[str, Any]
    hypothesis_id: str | None = None
    parent_strategy_id: str | None = None
    parent_content_hash: str | None = None
    mutation_operator: str = ""
    mutation_arguments: dict[str, Any] = Field(default_factory=dict)
    generation: int = 0
    warmup_period: int = 0
    complexity_score: float = 0.0
    rationale: str = ""


class SuccessCriterion(BaseModel):
    """A pre-registered bar the result must clear.  Written before the run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    comparator: Literal[">", ">=", "<", "<=", "=="]
    threshold: float
    rationale: str = ""


class ExperimentDesign(_Record):
    """A pre-registered experiment: what will be run, and what would count."""

    experiment_id: str = Field(default_factory=lambda: _rid("exp"))
    hypothesis_id: str
    strategy_ids: tuple[str, ...]
    instruments: tuple[str, ...]
    timeframes: tuple[str, ...]
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    embargo_bars: int = Field(default=0, ge=0)
    n_folds: int = Field(default=1, ge=1, le=64)
    seed: int = 0
    n_trials_in_search: int = Field(default=1, ge=1)
    success_criteria: tuple[SuccessCriterion, ...]
    notes: str = ""


class BacktestRecord(_Record):
    """The recorded output of one deterministic backtest run.

    Trades and the equity curve are stored as plain data so every later
    inspection (drawdowns, individual trades, validation) reads the same bytes
    the metrics were computed from.
    """

    backtest_id: str = Field(default_factory=lambda: _rid("bt"))
    job_id: str = ""
    strategy_id: str
    content_hash: str = ""
    experiment_id: str | None = None
    instruments: tuple[str, ...] = ()
    timeframe: str = ""
    dataset_version_ids: tuple[str, ...] = ()
    config_fingerprint: dict[str, Any] = Field(default_factory=dict)
    data_fingerprint: dict[str, Any] = Field(default_factory=dict)
    ledger_sha256: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    trades: tuple[dict[str, Any], ...] = ()
    equity_curve: tuple[dict[str, Any], ...] = ()
    rejections: dict[str, int] = Field(default_factory=dict)
    label: str = ""

    @property
    def n_trades(self) -> int:
        return len(self.trades)


class ValidationReportRecord(_Record):
    """The verdict of the deterministic validation gates.

    ``verdict`` is computed by the worker from the checks, never by an agent.
    """

    report_id: str = Field(default_factory=lambda: _rid("val"))
    job_id: str = ""
    strategy_id: str
    backtest_id: str = ""
    experiment_id: str | None = None
    kind: str = "validation"
    checks: tuple[dict[str, Any], ...] = ()
    metrics: dict[str, Any] = Field(default_factory=dict)
    verdict: Literal["pass", "fail", "inconclusive"] = "inconclusive"
    caveats: tuple[str, ...] = ()

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(str(c.get("name")) for c in self.checks if not c.get("passed", False))


class Objection(BaseModel):
    """One specific, falsifiable objection to a candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: str = Field(min_length=20)
    #: The concrete test that would settle it.  Generic caution is not an
    #: objection, so this field is mandatory and long enough to name a test.
    decisive_test: str = Field(min_length=20)
    severity: Literal["fatal", "major", "minor"]
    evidence: str = ""


class Critique(_Record):
    """An adversarial review whose job is to DISPROVE, not to hedge."""

    critique_id: str = Field(default_factory=lambda: _rid("crit"))
    target_kind: Literal["strategy", "backtest", "validation_report", "experiment"]
    target_id: str
    objections: tuple[Objection, ...] = Field(min_length=1)
    verdict: Literal["reject", "revise", "survives_this_attack"]
    summary: str = ""


class ResearchNote(_Record):
    """A librarian's filing: what was learned, and what it is linked to."""

    note_id: str = Field(default_factory=lambda: _rid("note"))
    title: str = Field(min_length=6, max_length=200)
    body: str = Field(min_length=20)
    tags: tuple[str, ...] = ()
    links: dict[str, str] = Field(default_factory=dict)
    supersedes: str | None = None


ANY_RECORD = (
    Hypothesis
    | StrategyProposal
    | ExperimentDesign
    | BacktestRecord
    | ValidationReportRecord
    | Critique
    | ResearchNote
)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

_COLLECTIONS: dict[str, tuple[type[BaseModel], str]] = {
    "hypotheses": (Hypothesis, "hypothesis_id"),
    "proposals": (StrategyProposal, "proposal_id"),
    "experiments": (ExperimentDesign, "experiment_id"),
    "backtests": (BacktestRecord, "backtest_id"),
    "validation_reports": (ValidationReportRecord, "report_id"),
    "critiques": (Critique, "critique_id"),
    "notes": (ResearchNote, "note_id"),
}


class ResearchStore:
    """Append-only, in-memory research domain with optional JSONL durability.

    Note the API: ``add_*`` and ``get_*``/``list_*``.  There is no ``update``
    and no ``delete``.  Superseding a record is an explicit new record that
    points back, which is what makes a research trail auditable months later.
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        self._data: dict[str, dict[str, BaseModel]] = {k: {} for k in _COLLECTIONS}
        self.directory = Path(directory) if directory is not None else None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._load()

    # -- persistence ------------------------------------------------------

    def _path(self, collection: str) -> Path:
        assert self.directory is not None
        return self.directory / f"{collection}.jsonl"

    def _load(self) -> None:
        for collection, (model, id_field) in _COLLECTIONS.items():
            path = self._path(collection)
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    record = model.model_validate(json.loads(text))
                    self._data[collection][str(getattr(record, id_field))] = record

    def _persist(self, collection: str, record: BaseModel) -> None:
        if self.directory is None:
            return
        with open(self._path(collection), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")

    def _add(self, collection: str, record: BaseModel) -> BaseModel:
        _model, id_field = _COLLECTIONS[collection]
        key = str(getattr(record, id_field))
        if key in self._data[collection]:
            raise ValueError(
                f"{collection} already contains {key!r}. The research store is "
                "append-only: supersede the record rather than rewriting it."
            )
        self._data[collection][key] = record
        self._persist(collection, record)
        return record

    # -- writes -----------------------------------------------------------

    def add_hypothesis(self, record: Hypothesis) -> Hypothesis:
        return self._add("hypotheses", record)  # type: ignore[return-value]

    def add_proposal(self, record: StrategyProposal) -> StrategyProposal:
        return self._add("proposals", record)  # type: ignore[return-value]

    def add_experiment(self, record: ExperimentDesign) -> ExperimentDesign:
        return self._add("experiments", record)  # type: ignore[return-value]

    def add_backtest(self, record: BacktestRecord) -> BacktestRecord:
        return self._add("backtests", record)  # type: ignore[return-value]

    def add_validation_report(self, record: ValidationReportRecord) -> ValidationReportRecord:
        return self._add("validation_reports", record)  # type: ignore[return-value]

    def add_critique(self, record: Critique) -> Critique:
        return self._add("critiques", record)  # type: ignore[return-value]

    def add_note(self, record: ResearchNote) -> ResearchNote:
        return self._add("notes", record)  # type: ignore[return-value]

    # -- reads ------------------------------------------------------------

    def get(self, collection: str, record_id: str) -> BaseModel:
        if collection not in self._data:
            raise KeyError(f"unknown collection {collection!r}")
        if record_id not in self._data[collection]:
            raise KeyError(f"{collection}: no record {record_id!r}")
        return self._data[collection][record_id]

    def get_hypothesis(self, record_id: str) -> Hypothesis:
        return self.get("hypotheses", record_id)  # type: ignore[return-value]

    def get_proposal(self, record_id: str) -> StrategyProposal:
        return self.get("proposals", record_id)  # type: ignore[return-value]

    def get_experiment(self, record_id: str) -> ExperimentDesign:
        return self.get("experiments", record_id)  # type: ignore[return-value]

    def get_backtest(self, record_id: str) -> BacktestRecord:
        return self.get("backtests", record_id)  # type: ignore[return-value]

    def get_validation_report(self, record_id: str) -> ValidationReportRecord:
        return self.get("validation_reports", record_id)  # type: ignore[return-value]

    def list(self, collection: str) -> tuple[BaseModel, ...]:
        """Insertion-stable, time-ordered view of one collection."""
        if collection not in self._data:
            raise KeyError(f"unknown collection {collection!r}")
        return tuple(
            sorted(
                self._data[collection].values(),
                key=lambda r: (getattr(r, "created_at"), repr(r)),  # noqa: B009
            )
        )

    def backtests_for(self, strategy_id: str) -> tuple[BacktestRecord, ...]:
        return tuple(
            r
            for r in self.list("backtests")
            if isinstance(r, BacktestRecord) and r.strategy_id == strategy_id
        )

    def latest_backtest_for(self, strategy_id: str) -> BacktestRecord | None:
        found = self.backtests_for(strategy_id)
        return found[-1] if found else None

    def validation_reports_for(self, strategy_id: str) -> tuple[ValidationReportRecord, ...]:
        return tuple(
            r
            for r in self.list("validation_reports")
            if isinstance(r, ValidationReportRecord) and r.strategy_id == strategy_id
        )

    def proposals_for(self, strategy_id: str) -> tuple[StrategyProposal, ...]:
        return tuple(
            r
            for r in self.list("proposals")
            if isinstance(r, StrategyProposal) and r.strategy_id == strategy_id
        )

    def search_notes(
        self,
        query: str = "",
        *,
        tags: Sequence[str] = (),
        limit: int = 20,
    ) -> tuple[ResearchNote, ...]:
        """Substring + tag search.  Deterministic ordering, newest first."""
        needle = query.lower().strip()
        wanted = {t.lower() for t in tags}
        hits: list[tuple[int, ResearchNote]] = []
        for note in self.list("notes"):
            if not isinstance(note, ResearchNote):  # pragma: no cover - typed store
                continue
            note_tags = {t.lower() for t in note.tags}
            if wanted and not (wanted & note_tags):
                continue
            score = 0
            if needle:
                score += 3 * note.title.lower().count(needle)
                score += note.body.lower().count(needle)
                if score == 0:
                    continue
            hits.append((score, note))
        # Best match first; ties broken by most recent, then by id for full
        # determinism (two notes filed in the same microsecond must not swap).
        hits.sort(key=lambda pair: (-pair[0], -pair[1].created_at.timestamp(), pair[1].note_id))
        return tuple(note for _score, note in hits[:limit])

    def counts(self) -> dict[str, int]:
        return {name: len(values) for name, values in sorted(self._data.items())}

    def all_records(self) -> Iterable[BaseModel]:
        for collection in sorted(self._data):
            yield from self.list(collection)


__all__ = [
    "SCHEMA_VERSION",
    "BacktestRecord",
    "Critique",
    "ExperimentDesign",
    "Hypothesis",
    "Objection",
    "ResearchNote",
    "ResearchStore",
    "StrategyProposal",
    "SuccessCriterion",
    "ValidationReportRecord",
]
