"""Agent research artefacts, stored in the PLATFORM's append-only ledger.

This module used to live at ``fiboki/agents/research_store.py`` with its own
JSONL persistence, its own append-only convention and its own note search. That
was a second research store standing beside
:mod:`fiboki.research.experiment` and :mod:`fiboki.research.memory`, which is an
architecture defect with three concrete costs:

* **Two append-only guarantees, one of them weaker.** The ledger enforces it with
  SQLite triggers -- a property of the artefact. The JSONL store enforced it by
  not having an ``update`` method, which is a property of the API and survives
  exactly as long as nobody opens the file.
* **Agent work was invisible to research memory.** ``ResearchMemory`` answers
  "have we tried this already?" from the ledger. Anything an agent proposed lived
  somewhere else, so the answer was wrong by construction.
* **Two lineages.** ``research.lineage`` walks from a live candidate back to raw
  bytes through the ledger. A chain that passes through an agent proposal broke.

So the artefacts now live in the SAME database as the experiment ledger, under
the same triggers, and the store exposes the ledger and the research memory it
is built on. The API is unchanged -- ``add_*`` / ``get_*`` / ``list`` and
deliberately no ``update`` or ``delete`` -- because the API was never the
problem.

Records are append-only in spirit and in API. A revised hypothesis is a NEW
hypothesis with ``supersedes`` pointing at the old one, so the lineage of a
research programme is readable end to end. Job results are likewise appended,
never overwritten, because a re-run that disagrees with an earlier run is a
finding, not a correction.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import DateTime, String, Text, select, text
from sqlalchemy.orm import Mapped, mapped_column

from fiboki.core.versioned_key import (
    UNSTAMPED,
    add_missing_columns,
    key_version_column,
    require_key_versions,
    unstamped_column_ddl,
)
from fiboki.research.experiment import (
    APPEND_ONLY_MESSAGE,
    Base,
    ExperimentDraft,
    ExperimentLedger,
)
from fiboki.research.memory import ResearchMemory

#: The record-payload schema these artefacts are stored under. Stamped onto every
#: row's ``content_hash_key_version``, so a stored hash says which payload shape
#: produced it rather than leaving a reader to assume.
SCHEMA_VERSION = "1.0.0"


def artefact_key_version() -> str:
    """The version stamped beside a stored artefact ``content_hash``.

    Lives here, beside the schema it names, rather than in ``core/``: see
    :mod:`fiboki.core.versioned_key` for why the values are not centralised.
    """
    return f"artefact:{SCHEMA_VERSION}"



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
    #: Which generation of ``backtest/engine.py`` produced these numbers.
    #: EMPTY means the record predates the stamp, which places it before the
    #: exit-vocabulary change by construction -- nothing could have written an
    #: empty stamp afterwards. :meth:`ResearchStore.add_backtest` fills it in on
    #: the way in, so a caller cannot forget; see ``fiboki.backtest.version``.
    engine_version: str = ""
    #: The exit policy the run was executed under, as
    #: :meth:`fiboki.backtest.exits.ExitPolicy.fingerprint` renders it. Two runs
    #: with the same engine version and different policies are still different
    #: strategies, and the fingerprint is how a reader sees that.
    exit_policy_fingerprint: dict[str, Any] = Field(default_factory=dict)
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


#: The closed claim vocabulary of a forecast. None of it can read as an order:
#: it names where price will be relative to where it was, not what to do.
ForecastDirection = Literal["higher", "lower", "range"]
#: Absolute realised move in units of ATR at the start of the horizon.
MagnitudeBucket = Literal["<0.5atr", "0.5-1atr", "1-2atr", ">2atr"]
#: ``range`` is a DIRECTIONAL claim that landed inside the range band: scored
#: as the claimed event not occurring, never as a push. See
#: :mod:`fiboki.research.forecasts`.
ForecastOutcome = Literal["hit", "miss", "range", "not_evaluable"]


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC, got {value!r}")


class Forecast(_Record):
    """A pre-registered, scoreable claim about one instrument over one horizon.

    Written by an agent BEFORE the horizon starts, scored by
    :func:`fiboki.research.forecasts.score_due_forecasts` after it ends, and
    never edited: the store is append-only by trigger. It is not a signal and
    nothing downstream of the research store reads it as one; its only
    consumer is the scorer.

    ``atr_period`` and ``range_band_atr`` are stamped from the forecast policy
    at write time, so the unit a forecast is scored in is fixed with the claim
    rather than read from whatever the policy says on scoring day.
    ``provenance`` records whether the forecast was written before its horizon
    started (``forward``) or reconstructed after it (``backfill``); only forward
    forecasts are evidence about a model (plan decision D-A3).
    """

    forecast_id: str = Field(default_factory=lambda: _rid("fc"))
    instrument: str = Field(min_length=3, max_length=32)
    timeframe: Literal["H1", "H4", "D1"]
    horizon_start: datetime
    horizon_end: datetime
    direction: ForecastDirection
    magnitude_bucket: MagnitudeBucket | None = None
    probability: float = Field(ge=0.5, le=0.95)
    invalid_if: str = Field(min_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    reason: str = Field(min_length=20)
    model_id: str = ""
    provenance: Literal["forward", "backfill"]
    atr_period: int = Field(ge=1)
    range_band_atr: float = Field(gt=0.0)
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _horizon_is_a_real_interval(self) -> Forecast:
        _require_aware(self.horizon_start, "horizon_start")
        _require_aware(self.horizon_end, "horizon_end")
        if self.horizon_end <= self.horizon_start:
            raise ValueError("horizon_end must be strictly after horizon_start")
        return self


class ForecastScore(_Record):
    """The deterministic verdict on one :class:`Forecast`.

    ``score_id`` is DERIVED from ``forecast_id`` (:meth:`id_for`), so the
    store's primary key makes a second score for the same forecast impossible
    rather than merely unlikely: re-running the scorer, or two scorers racing,
    cannot double-count a forecast.

    A forecast whose bars are missing is ``not_evaluable`` with a reason and no
    Brier or log score; it is recorded, never skipped.
    """

    score_id: str
    forecast_id: str
    instrument: str
    timeframe: str
    forecaster: str = ""
    forecaster_role: str = ""
    model_id: str = ""
    provenance: Literal["forward", "backfill"]
    claimed_direction: ForecastDirection
    claimed_magnitude_bucket: MagnitudeBucket | None = None
    probability: float = Field(ge=0.5, le=0.95)
    outcome: ForecastOutcome
    realised_direction: ForecastDirection | None = None
    realised_move_atr: float | None = None
    realised_magnitude_bucket: MagnitudeBucket | None = None
    magnitude_hit: bool | None = None
    atr_at_start: float | None = None
    start_price: float | None = None
    end_price: float | None = None
    start_bar_closed_at: datetime | None = None
    end_bar_closed_at: datetime | None = None
    dataset_version_id: str = ""
    brier: float | None = None
    log_score: float | None = None
    not_evaluable_reason: str = ""
    scorer_version: str = Field(min_length=1)

    @staticmethod
    def id_for(forecast_id: str) -> str:
        return f"fscore_{forecast_id}"

    @model_validator(mode="after")
    def _consistent(self) -> ForecastScore:
        if self.score_id != self.id_for(self.forecast_id):
            raise ValueError(
                f"score_id must be {self.id_for(self.forecast_id)!r}: one forecast, "
                "one score, enforced by the primary key"
            )
        if self.outcome == "not_evaluable":
            if not self.not_evaluable_reason:
                raise ValueError("a not_evaluable score must say why")
            if self.brier is not None or self.log_score is not None:
                raise ValueError("a not_evaluable score carries no Brier or log score")
        else:
            if self.brier is None or self.log_score is None:
                raise ValueError("an evaluable score must carry Brier and log score")
            if self.realised_direction is None or self.realised_move_atr is None:
                raise ValueError("an evaluable score must carry the realised move")
        return self


ANY_RECORD = (
    Hypothesis
    | StrategyProposal
    | ExperimentDesign
    | BacktestRecord
    | ValidationReportRecord
    | Critique
    | ResearchNote
    | Forecast
    | ForecastScore
)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_COLLECTIONS: dict[str, tuple[type[BaseModel], str]] = {
    "hypotheses": (Hypothesis, "hypothesis_id"),
    "proposals": (StrategyProposal, "proposal_id"),
    "experiments": (ExperimentDesign, "experiment_id"),
    "backtests": (BacktestRecord, "backtest_id"),
    "validation_reports": (ValidationReportRecord, "report_id"),
    "critiques": (Critique, "critique_id"),
    "notes": (ResearchNote, "note_id"),
    "forecasts": (Forecast, "forecast_id"),
    "forecast_scores": (ForecastScore, "score_id"),
}


class ArtefactRow(Base):
    """One agent artefact, in the experiment ledger's own database.

    The payload is stored whole rather than shredded into columns: the record
    types are pydantic models that evolve, and a column per field would turn
    every schema change into a migration of data nobody is allowed to rewrite.
    The columns that DO exist are the ones something queries or joins on.
    """

    __tablename__ = "research_artefact"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(64), default="")
    strategy_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    #: Which record schema the hash in ``content_hash`` was computed over.
    #: :data:`SCHEMA_VERSION` used to be declared here and written nowhere, which
    #: made it decoration: a stored hash could not say which payload shape produced
    #: it, so a schema change would move every hash and every lookup would miss in
    #: silence. See :mod:`fiboki.core.versioned_key`.
    content_hash_key_version: Mapped[str] = mapped_column(
        String(32), default=UNSTAMPED, index=True
    )
    payload_json: Mapped[str] = mapped_column(Text)


#: Same guarantee as the experiment table, and for the same reason: an API
#: without a delete is a convention, a trigger is a property of the file.
_ARTEFACT_TRIGGERS = (
    f"""
    CREATE TRIGGER IF NOT EXISTS research_artefact_no_update
    BEFORE UPDATE ON research_artefact
    BEGIN
        SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
    END;
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS research_artefact_no_delete
    BEFORE DELETE ON research_artefact
    BEGIN
        SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
    END;
    """,
)

#: Checked at import: this module's table shares the experiment ledger's
#: ``Base``, so the check covers both by the time anything opens a store.
require_key_versions(Base.metadata)


class ResearchStore:
    """Append-only research domain, sharing the platform ledger's database.

    Note the API: ``add_*`` and ``get_*``/``list_*``. There is no ``update`` and
    no ``delete``. Superseding a record is an explicit new record that points
    back, which is what makes a research trail auditable months later.
    """

    def __init__(
        self,
        directory: str | Path | None = None,
        *,
        ledger: ExperimentLedger | None = None,
    ) -> None:
        self.directory = Path(directory) if directory is not None else None
        if ledger is not None:
            self.ledger = ledger
        elif self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.ledger = ExperimentLedger(self.directory / "research.sqlite")
        else:
            self.ledger = ExperimentLedger.in_memory()
        self._engine = self.ledger.engine
        Base.metadata.create_all(self._engine)
        with self._engine.begin() as conn:
            for ddl in _ARTEFACT_TRIGGERS:
                conn.execute(text(ddl))
        self.columns_added = add_missing_columns(
            self._engine,
            ArtefactRow.__tablename__,
            {key_version_column("content_hash"): unstamped_column_ddl(32)},
        )
        self.memory = ResearchMemory(self.ledger)

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self.ledger.close()

    def __enter__(self) -> ResearchStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals --------------------------------------------------------

    def _session(self):
        return self.ledger._session_factory()

    def _add(self, collection: str, record: BaseModel) -> BaseModel:
        _model, id_field = _COLLECTIONS[collection]
        key = str(getattr(record, id_field))
        with self._session() as session:
            if session.get(ArtefactRow, key) is not None:
                raise ValueError(
                    f"{collection} already contains {key!r}. The research store is "
                    "append-only: supersede the record rather than rewriting it."
                )
            session.add(
                ArtefactRow(
                    id=key,
                    collection=collection,
                    created_at=getattr(record, "created_at", _now()),
                    created_by=str(getattr(record, "created_by", "") or ""),
                    role=str(getattr(record, "role", "") or ""),
                    strategy_id=str(getattr(record, "strategy_id", "") or ""),
                    content_hash=(artefact_hash := str(getattr(record, "content_hash", "") or "")),
                    content_hash_key_version=(
                        artefact_key_version() if artefact_hash else UNSTAMPED
                    ),
                    payload_json=json.dumps(
                        record.model_dump(mode="json"), sort_keys=True
                    ),
                )
            )
            session.commit()
        return record

    @staticmethod
    def _hydrate(row: ArtefactRow) -> BaseModel:
        model, _id_field = _COLLECTIONS[row.collection]
        return model.model_validate(json.loads(row.payload_json))

    # -- writes -----------------------------------------------------------

    def add_hypothesis(self, record: Hypothesis) -> Hypothesis:
        return self._add("hypotheses", record)  # type: ignore[return-value]

    def add_proposal(self, record: StrategyProposal) -> StrategyProposal:
        return self._add("proposals", record)  # type: ignore[return-value]

    def add_experiment(self, record: ExperimentDesign) -> ExperimentDesign:
        return self._add("experiments", record)  # type: ignore[return-value]

    def add_backtest(self, record: BacktestRecord) -> BacktestRecord:
        """File a backtest, stamping the engine generation that produced it.

        Stamped HERE rather than at every call site: a caller who forgets writes
        a record that later looks pre-change, and a result quietly mislabelled
        as stale is as bad as one quietly mislabelled as current.
        """
        from fiboki.backtest.version import ENGINE_VERSION

        if not record.engine_version:
            record = record.model_copy(update={"engine_version": ENGINE_VERSION})
        return self._add("backtests", record)  # type: ignore[return-value]

    # -- supersession -----------------------------------------------------

    #: The tag every supersession note carries, so the sweep can find its own
    #: earlier work and stay idempotent.
    SUPERSEDED_TAG = "superseded_by_engine_version"

    def superseded_backtest_ids(self) -> frozenset[str]:
        """Backtest ids that a supersession note points at."""
        return frozenset(
            str(note.supersedes)
            for note in self.list("notes")
            if isinstance(note, ResearchNote)
            and note.supersedes
            and self.SUPERSEDED_TAG in note.tags
        )

    def sweep_superseded_backtests(
        self,
        *,
        engine_version: str | None = None,
        swept_by: str = "engine_version_sweep",
        dry_run: bool = False,
    ) -> tuple[ResearchNote, ...]:
        """Mark every stored backtest from an older engine generation.

        **Nothing is deleted and nothing is rewritten.** The store is append-only
        by SQLite trigger, not by convention, so a sweep that "invalidated" a
        record by editing it could not run at all -- and should not: a result
        that was quoted in a decision has to remain readable alongside the reason
        it should not have been. The sweep appends one :class:`ResearchNote` per
        stale record, with ``supersedes`` pointing at it and the reason spelled
        out, which is exactly the mechanism this package already uses for a
        revised hypothesis.

        Idempotent: a record that already carries a supersession note is skipped,
        so the sweep can run on every deploy.

        ``dry_run`` returns the notes it WOULD write without writing them, for an
        operator who wants to see the blast radius before it lands.
        """
        from fiboki.backtest.version import ENGINE_VERSION, supersession_reason

        current = engine_version or ENGINE_VERSION
        already = self.superseded_backtest_ids()
        written: list[ResearchNote] = []
        for record in self.list("backtests"):
            if not isinstance(record, BacktestRecord):  # pragma: no cover - typed
                continue
            if record.engine_version == current:
                continue
            if record.backtest_id in already:
                continue
            note = ResearchNote(
                title=f"Superseded backtest {record.backtest_id}",
                body=(
                    f"{record.strategy_id}: {supersession_reason(record.engine_version)}"
                ),
                tags=(self.SUPERSEDED_TAG, record.strategy_id),
                links={
                    "backtest_id": record.backtest_id,
                    "strategy_id": record.strategy_id,
                    "engine_version_found": record.engine_version or "(unstamped)",
                    "engine_version_required": current,
                },
                supersedes=record.backtest_id,
                created_by=swept_by,
                role="maintenance",
            )
            if not dry_run:
                self.add_note(note)
            written.append(note)
        return tuple(written)

    def add_validation_report(self, record: ValidationReportRecord) -> ValidationReportRecord:
        return self._add("validation_reports", record)  # type: ignore[return-value]

    def add_critique(self, record: Critique) -> Critique:
        return self._add("critiques", record)  # type: ignore[return-value]

    def add_note(self, record: ResearchNote) -> ResearchNote:
        return self._add("notes", record)  # type: ignore[return-value]

    def add_forecast(self, record: Forecast) -> Forecast:
        return self._add("forecasts", record)  # type: ignore[return-value]

    def add_forecast_score(self, record: ForecastScore) -> ForecastScore:
        """File a score. A second score for the same forecast raises ValueError."""
        return self._add("forecast_scores", record)  # type: ignore[return-value]

    def record_experiment(self, draft: ExperimentDraft):
        """Append a row to the PLATFORM experiment ledger.

        The bridge that makes the migration real rather than cosmetic: an agent's
        pre-registered experiment becomes an experiment the rest of the platform
        can see -- ``ResearchMemory`` will answer "have we tried this already?"
        with it, and ``research.lineage`` can walk through it.
        """
        return self.ledger.create(draft)

    # -- reads ------------------------------------------------------------

    def get(self, collection: str, record_id: str) -> BaseModel:
        if collection not in _COLLECTIONS:
            raise KeyError(f"unknown collection {collection!r}")
        with self._session() as session:
            row = session.get(ArtefactRow, str(record_id))
            if row is None or row.collection != collection:
                raise KeyError(f"{collection}: no record {record_id!r}")
            return self._hydrate(row)

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

    def get_forecast(self, record_id: str) -> Forecast:
        return self.get("forecasts", record_id)  # type: ignore[return-value]

    def forecasts(self) -> tuple[Forecast, ...]:
        return tuple(r for r in self.list("forecasts") if isinstance(r, Forecast))

    def forecast_scores(self) -> tuple[ForecastScore, ...]:
        return tuple(r for r in self.list("forecast_scores") if isinstance(r, ForecastScore))

    def list(self, collection: str) -> tuple[BaseModel, ...]:
        """Insertion-stable, time-ordered view of one collection."""
        if collection not in _COLLECTIONS:
            raise KeyError(f"unknown collection {collection!r}")
        with self._session() as session:
            rows = session.scalars(
                select(ArtefactRow)
                .where(ArtefactRow.collection == collection)
                .order_by(ArtefactRow.created_at, ArtefactRow.id)
            ).all()
        records = [self._hydrate(r) for r in rows]
        # Ties inside one timestamp are broken by the record's own canonical
        # form, matching the JSONL store this replaced: two records filed in the
        # same microsecond must not swap between reads.
        return tuple(sorted(records, key=lambda r: (_created(r), repr(r))))

    def backtests_for(
        self, strategy_id: str, *, include_superseded: bool = True
    ) -> tuple[BacktestRecord, ...]:
        """Every backtest for this strategy, oldest first.

        ``include_superseded`` defaults to True because the ledger's job is to
        show what was run, including the runs whose numbers are no longer
        comparable. Pass False when the answer will be QUOTED: a ranking, a
        promotion decision, a report.
        """
        found = tuple(
            r
            for r in self.list("backtests")
            if isinstance(r, BacktestRecord) and r.strategy_id == strategy_id
        )
        if include_superseded:
            return found
        stale = self.superseded_backtest_ids()
        return tuple(r for r in found if r.backtest_id not in stale)

    def latest_backtest_for(
        self, strategy_id: str, *, include_superseded: bool = True
    ) -> BacktestRecord | None:
        found = self.backtests_for(
            strategy_id, include_superseded=include_superseded
        )
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
        """Substring + tag search. Deterministic ordering, newest first.

        Kept alongside :attr:`memory`, which answers a different question:
        ``ResearchMemory.recall`` matches a STRATEGY against prior experiments by
        structure, while this matches free text against filed notes. Collapsing
        the two would lose the structural match, which is the one that catches a
        rediscovery.
        """
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
        hits.sort(key=lambda pair: (-pair[0], -pair[1].created_at.timestamp(), pair[1].note_id))
        return tuple(note for _score, note in hits[:limit])

    def counts(self) -> dict[str, int]:
        with self._session() as session:
            rows = session.scalars(select(ArtefactRow.collection)).all()
        counts = {name: 0 for name in _COLLECTIONS}
        for name in rows:
            counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items()))

    def all_records(self) -> Iterable[BaseModel]:
        for collection in sorted(_COLLECTIONS):
            yield from self.list(collection)


def _created(record: BaseModel) -> datetime:
    value = getattr(record, "created_at", None)
    return value if isinstance(value, datetime) else _now()


__all__ = [
    "SCHEMA_VERSION",
    "ArtefactRow",
    "BacktestRecord",
    "Critique",
    "ExperimentDesign",
    "Forecast",
    "ForecastDirection",
    "ForecastOutcome",
    "ForecastScore",
    "Hypothesis",
    "MagnitudeBucket",
    "Objection",
    "ResearchNote",
    "ResearchStore",
    "StrategyProposal",
    "SuccessCriterion",
    "ValidationReportRecord",
]
