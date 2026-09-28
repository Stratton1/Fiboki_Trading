"""The experiment ledger: append-only, and structurally so.

Every piece of research Fiboki does writes a row here -- who asked for it, why,
what was tried, against which data, with which code, and what came back. The
ledger is what makes the research process auditable rather than anecdotal, and
what lets :mod:`fiboki.research.memory` answer "have we tried this already?"
before work is done rather than after it is repeated.

Append-only is enforced twice
-----------------------------
1. **At the API.** :class:`ExperimentLedger` exposes ``create``, ``get`` and
   ``list``. There is no ``update`` and no ``delete``, so no caller can reach for
   one.
2. **At the database.** SQLite triggers raise on ``UPDATE`` and ``DELETE``
   against the experiments table. A future maintainer who opens the file with
   ``sqlite3`` and tries to tidy up an embarrassing result gets an error, not a
   tidier history.

The second one is the one that matters. An API without a ``delete`` method is a
convention; a trigger is a property of the artefact. A research record that can
be quietly amended after the fact is worth nothing, because the value of the
ledger is precisely that it contains the results nobody liked.

Amending a record
-----------------
You do not. You append a new experiment whose ``parent_experiment_id`` points at
the one being corrected and whose ``reason`` says what was wrong with it. The
history then shows both the error and the correction, which is the honest record.

Keys carry the version that derived them
----------------------------------------
``strategy_content_hash``, ``structure_hash`` and ``validation_report_hash`` are
all derived keys: functions of a document AND of the algorithm that reduced it.
``StrategyDocument.semantic_payload()`` includes ``schema_version`` and
``ValidationReport.to_dict()`` includes ``report_version``, so bumping either
moves every stored key of that kind. A lookup keyed on one of them would then
find nothing and report "never tried" -- novelty says novel, lineage says the
document is unknown, a campaign resumes from scratch -- all of them wrong, and
all of them silent. Every key column therefore has a ``_key_version`` companion
(see :mod:`fiboki.core.versioned_key`, whose :func:`require_key_versions` check
runs at the bottom of this module), and a hash-keyed
:meth:`ExperimentLedger.list` REFUSES when the ledger holds keys it cannot
compare rather than answering with a silent miss.

Rows written before those columns existed carry the empty version, and are
corrected the only way an append-only ledger can be: a row in
:class:`ExperimentKeyRestatementRow` stating which version wrote them, attributed
and reasoned. Not an ``UPDATE`` -- the triggers forbid it -- and not a fresh
experiment row either, because a duplicate experiment would inflate the trial
count that the deflated Sharpe ratio is computed from, i.e. it would corrupt the
statistics to fix a bookkeeping gap.
"""
from __future__ import annotations

import builtins
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    event,
    func,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from fiboki.core.versioned_key import (
    UNSTAMPED,
    add_missing_columns,
    key_version_column,
    require_key_versions,
    resolve_key_version,
    unstamped_column_ddl,
)
from fiboki.research.structure import structural_tokens, structure_hash
from fiboki.strategy.dsl import strategy_key_version
from fiboki.validation.report import ValidationReport, code_version, report_key_version

__all__ = [
    "APPEND_ONLY_MESSAGE",
    "KEY_COLUMNS",
    "ActorKind",
    "Experiment",
    "ExperimentDraft",
    "ExperimentLedger",
    "ExperimentNotFound",
    "LedgerError",
    "LedgerKeyVersionMismatch",
    "Outcome",
    "is_append_only_violation",
]

#: Every derived-key column on ``experiment``, mapped to the callable that says
#: which version derives it now. One place, so a new key column has to declare
#: what moves it.
KEY_COLUMNS: dict[str, Any] = {
    "strategy_content_hash": strategy_key_version,
    "structure_hash": strategy_key_version,
    "validation_report_hash": report_key_version,
}


class LedgerError(RuntimeError):
    """Base class for ledger misuse."""


class LedgerKeyVersionMismatch(LedgerError):
    """A hash-keyed lookup against rows whose keys were derived differently.

    The ledger refuses the query rather than answering it, because the honest
    answer is "I cannot tell". Returning the empty set instead is what made this
    class of bug dangerous: "no experiment ran this strategy" and "this strategy's
    key moved" are indistinguishable in a result set, and only one of them means
    the work has not been done.
    """

    def __init__(
        self, key_column: str, expected: str, found: Mapping[str, int]
    ) -> None:
        self.key_column = str(key_column)
        self.expected = str(expected)
        self.found = dict(found)
        summary = ", ".join(
            f"{count} row(s) under {version or '<unstamped>'}"
            for version, count in sorted(found.items())
        )
        super().__init__(
            f"cannot look up {key_column} at version {expected!r}: this ledger holds "
            f"{summary}. Those keys and this one are not comparable, so a non-match "
            "would mean 'derived differently', not 'never tried' -- and reporting it "
            "as the latter is how a rediscovery, a lost lineage or a wrongly-resumed "
            "campaign happens silently. Restate the unstamped rows with "
            "ExperimentLedger.restate_key_versions() if you can show which version "
            "wrote them; pass allow_stale_keys=True only if you actively want a "
            "possibly-incomplete answer and will say so to whoever reads it."
        )


class ExperimentNotFound(LedgerError):
    def __init__(self, experiment_id: str) -> None:
        super().__init__(f"no experiment {experiment_id!r} in this ledger")
        self.experiment_id = experiment_id


class ActorKind(str, Enum):
    """Who initiated the work. Agents and humans are told apart on purpose."""

    HUMAN = "human"
    AGENT = "agent"
    SCHEDULE = "schedule"


class Outcome(str, Enum):
    PENDING = "pending"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    ABANDONED = "abandoned"
    ERROR = "error"


class Base(DeclarativeBase):
    pass


class ExperimentRow(Base):
    __tablename__ = "experiment"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    parent_experiment_id: Mapped[str] = mapped_column(String(64), default="", index=True)

    actor_kind: Mapped[str] = mapped_column(String(16), index=True)
    actor_name: Mapped[str] = mapped_column(String(128), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    hypothesis_id: Mapped[str] = mapped_column(String(128), default="", index=True)

    strategy_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    strategy_content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    #: Version of the algorithm that derived ``strategy_content_hash``. Empty when
    #: there is no hash to version, or when the row predates this column.
    strategy_content_hash_key_version: Mapped[str] = mapped_column(
        String(32), default=UNSTAMPED, index=True
    )
    strategy_version: Mapped[str] = mapped_column(String(32), default="")
    structure_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    structure_hash_key_version: Mapped[str] = mapped_column(
        String(32), default=UNSTAMPED, index=True
    )
    structure_tokens_json: Mapped[list] = mapped_column(JSON, default=list)
    strategy_document_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    code_version: Mapped[str] = mapped_column(String(64), default="")
    dataset_version_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    engine_config_json: Mapped[dict] = mapped_column(JSON, default=dict)

    outputs_json: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_report_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_report_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    validation_report_hash_key_version: Mapped[str] = mapped_column(
        String(32), default=UNSTAMPED, index=True
    )

    outcome: Mapped[str] = mapped_column(String(24), default=Outcome.PENDING.value, index=True)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    rejected_at_rung: Mapped[str] = mapped_column(String(64), default="")
    tags_json: Mapped[list] = mapped_column(JSON, default=list)


class ExperimentKeyRestatementRow(Base):
    """"Experiment X's stored keys were derived under version V", attributed.

    The migration path for a ledger file written before the key-version columns
    existed. Those rows cannot be UPDATEd (the triggers refuse) and must not be
    re-appended as experiments (a duplicate experiment row inflates the trial
    count that deflation divides by, so fixing bookkeeping would corrupt the
    statistics). A restatement is neither: the experiment row is untouched and
    still says exactly what it said, and a separate, attributed row records what
    we have since established about how it was written.

    Its own table, not a column, so that the assertion carries WHO made it and on
    what evidence -- the same standard ``ExperimentDraft.reason`` holds a piece of
    research to.
    """

    __tablename__ = "experiment_key_restatement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(String(64), index=True)
    key_column: Mapped[str] = mapped_column(String(64), default="", index=True)
    key_value: Mapped[str] = mapped_column(String(64), default="")
    key_version: Mapped[str] = mapped_column(String(32), default="")
    stated_by: Mapped[str] = mapped_column(String(128), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


#: Raised by the triggers below. The message is the first thing a confused
#: maintainer will read, so it explains the rule rather than just refusing.
APPEND_ONLY_MESSAGE = (
    "fiboki experiment ledger is append-only: append a correcting experiment "
    "whose parent_experiment_id points at this one"
)


def is_append_only_violation(exc: BaseException) -> bool:
    """True when a database error came from the append-only triggers.

    The guarantee lives in the database, so the error that enforces it is a
    driver error rather than one of ours. This is how a caller -- or a test --
    tells that specific refusal apart from an unrelated SQL failure.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if APPEND_ONLY_MESSAGE in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False

_TRIGGERS = (
    f"""
    CREATE TRIGGER IF NOT EXISTS experiment_no_update
    BEFORE UPDATE ON experiment
    BEGIN
        SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
    END;
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS experiment_no_delete
    BEFORE DELETE ON experiment
    BEGIN
        SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
    END;
    """,
)

#: Enforced at import time: adding a derived-key column to this schema without a
#: version companion makes the module fail to import. This is the third time the
#: same bug has been found in this codebase, so the guard is structural rather
#: than a code-review convention.
require_key_versions(Base.metadata)


# --------------------------------------------------------------------------
# Domain objects
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExperimentDraft:
    """What a caller supplies. The ledger fills in the rest."""

    actor_kind: ActorKind
    actor_name: str
    reason: str
    """WHY this was run, in the initiator's own words. A prompt, a ticket, a
    hypothesis restated. An experiment with no reason cannot be learned from."""

    hypothesis_id: str = ""
    parent_experiment_id: str = ""
    strategy_id: str = ""
    strategy_content_hash: str = ""
    strategy_version: str = ""
    strategy_document: Any = field(default=None, repr=False)
    dataset_version_id: str = ""
    code_version: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    engine_config: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    validation_report: ValidationReport | None = field(default=None, repr=False)
    outcome: Outcome = Outcome.PENDING
    conclusion: str = ""
    rejection_reason: str = ""
    tags: Sequence[str] = ()

    def __post_init__(self) -> None:
        if not str(self.actor_name).strip():
            raise ValueError(
                "actor_name is mandatory: an experiment nobody is named for "
                "cannot be followed up, and 'the pipeline' is not an actor"
            )
        if not str(self.reason).strip():
            raise ValueError(
                "reason is mandatory: the ledger exists so a later reader can "
                "tell WHY this was tried, not merely that it was"
            )


@dataclass(frozen=True, slots=True)
class Experiment:
    """A written, immutable research record."""

    id: str
    created_at: datetime
    actor_kind: ActorKind
    actor_name: str
    reason: str
    parent_experiment_id: str = ""
    hypothesis_id: str = ""
    strategy_id: str = ""
    strategy_content_hash: str = ""
    strategy_version: str = ""
    structure_hash: str = ""
    structure_tokens: tuple[str, ...] = ()
    strategy_document: dict[str, Any] | None = field(default=None, repr=False)
    code_version: str = ""
    dataset_version_id: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    engine_config: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    validation_report_json: dict[str, Any] | None = field(default=None, repr=False)
    validation_report_hash: str = ""
    outcome: Outcome = Outcome.PENDING
    conclusion: str = ""
    rejection_reason: str = ""
    rejected_at_rung: str = ""
    tags: tuple[str, ...] = ()
    key_versions: dict[str, str] = field(default_factory=dict)
    """``key column -> version of the algorithm that derived it``, resolved from
    the row's own columns or from a restatement. An empty or absent entry means
    nobody has said, and a comparison against that key proves nothing."""

    def key_version(self, key_column: str) -> str:
        return str(self.key_versions.get(str(key_column), UNSTAMPED))

    def key_is_comparable(self, key_column: str, expected: str) -> bool:
        """Can this row's ``key_column`` be compared with a key derived under ``expected``?

        A row with no key of that kind has nothing to compare and is not an
        obstacle; a row with a key and no version is, because the whole point of
        the version is that its absence is not evidence of sameness.
        """
        stored = getattr(self, key_column, "")
        if not stored:
            return True
        return self.key_version(key_column) == str(expected)

    @property
    def validation_report(self) -> ValidationReport | None:
        if self.validation_report_json is None:
            return None
        return ValidationReport.from_dict(self.validation_report_json)

    @property
    def short_id(self) -> str:
        return self.id[:12]

    def describe(self) -> str:
        """One line an operator can read in a list."""
        where = f" at {self.rejected_at_rung}" if self.rejected_at_rung else ""
        tail = self.rejection_reason or self.conclusion
        return (
            f"{self.short_id} {self.created_at.date()} "
            f"{self.actor_kind.value}:{self.actor_name} "
            f"{self.strategy_id or '-'} -> {self.outcome.value}{where}"
            + (f" -- {tail}" if tail else "")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "parent_experiment_id": self.parent_experiment_id,
            "actor_kind": self.actor_kind.value,
            "actor_name": self.actor_name,
            "reason": self.reason,
            "hypothesis_id": self.hypothesis_id,
            "strategy_id": self.strategy_id,
            "strategy_content_hash": self.strategy_content_hash,
            "strategy_version": self.strategy_version,
            "structure_hash": self.structure_hash,
            "structure_tokens": list(self.structure_tokens),
            "code_version": self.code_version,
            "dataset_version_id": self.dataset_version_id,
            "parameters": dict(self.parameters),
            "engine_config": dict(self.engine_config),
            "outputs": dict(self.outputs),
            "validation_report_hash": self.validation_report_hash,
            "outcome": self.outcome.value,
            "conclusion": self.conclusion,
            "rejection_reason": self.rejection_reason,
            "rejected_at_rung": self.rejected_at_rung,
            "tags": list(self.tags),
            "key_versions": dict(sorted(self.key_versions.items())),
        }


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------


class ExperimentLedger:
    """Append-only SQLite repository of experiments.

    ``create`` / ``get`` / ``list`` and nothing else. See the module docstring
    for why there is no ``update``.
    """

    def __init__(self, db_path: str | Path = ":memory:", *, echo: bool = False) -> None:
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else None
        if self.db_path is None:
            url = "sqlite+pysqlite:///:memory:"
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite+pysqlite:///{self.db_path}"
        self._engine = create_engine(url, echo=echo, future=True)

        @event.listens_for(self._engine, "connect")
        def _enforce_foreign_keys(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        Base.metadata.create_all(self._engine)
        with self._engine.begin() as conn:
            for ddl in _TRIGGERS:
                conn.execute(text(ddl))
        # A ledger file written before the key-version columns existed acquires
        # them here. ADD COLUMN is DDL, so the append-only triggers -- which fire
        # on UPDATE and DELETE -- do not object, and no stored row is rewritten:
        # existing rows take the UNSTAMPED default, which is the truth about them.
        self.columns_added = add_missing_columns(
            self._engine,
            ExperimentRow.__tablename__,
            {key_version_column(name): unstamped_column_ddl(32) for name in KEY_COLUMNS},
        )
        self._session_factory = sessionmaker(bind=self._engine, future=True)

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> ExperimentLedger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @classmethod
    def in_memory(cls) -> ExperimentLedger:
        return cls(":memory:")

    @property
    def engine(self):  # pragma: no cover - accessor used by lineage / tests
        return self._engine

    # --------------------------------------------------------------- write

    def create(self, draft: ExperimentDraft) -> Experiment:
        """Append one experiment. The only write this class performs."""
        doc = draft.strategy_document
        tokens = tuple(structural_tokens(doc)) if doc is not None else ()
        s_hash = structure_hash(doc) if doc is not None else ""
        content_hash = draft.strategy_content_hash
        if not content_hash and doc is not None and hasattr(doc, "content_hash"):
            content_hash = str(doc.content_hash())
        strategy_id = draft.strategy_id or (
            str(getattr(doc, "strategy_id", "")) if doc is not None else ""
        )

        report = draft.validation_report
        report_json = report.to_dict() if report is not None else None
        report_hash = report.content_hash() if report is not None else ""
        outcome = draft.outcome
        rejection = draft.rejection_reason
        rung = ""
        if report is not None:
            if outcome is Outcome.PENDING:
                outcome = (
                    Outcome.PROMOTED
                    if report.verdict.promotable
                    else (
                        Outcome.INCONCLUSIVE
                        if report.verdict.value == "incomplete"
                        else Outcome.REJECTED
                    )
                )
            if not rejection and not report.verdict.promotable:
                rejection = report.binding_constraint.describe()
            failing = report.first_failing_rung()
            rung = failing.label if failing is not None else ""

        row_id = f"exp_{uuid.uuid4().hex}"
        created = datetime.now(tz=UTC)
        with self._session_factory() as session:
            if draft.parent_experiment_id:
                parent = session.get(ExperimentRow, draft.parent_experiment_id)
                if parent is None:
                    raise ExperimentNotFound(draft.parent_experiment_id)
            session.add(
                ExperimentRow(
                    id=row_id,
                    created_at=created,
                    parent_experiment_id=draft.parent_experiment_id,
                    actor_kind=draft.actor_kind.value,
                    actor_name=draft.actor_name,
                    reason=draft.reason,
                    hypothesis_id=draft.hypothesis_id,
                    strategy_id=strategy_id,
                    strategy_content_hash=content_hash,
                    # Stamped only where there is a key to stamp: a version beside
                    # an empty hash asserts something about nothing.
                    strategy_content_hash_key_version=(
                        strategy_key_version() if content_hash else UNSTAMPED
                    ),
                    strategy_version=draft.strategy_version,
                    structure_hash=s_hash,
                    structure_hash_key_version=(
                        strategy_key_version() if s_hash else UNSTAMPED
                    ),
                    structure_tokens_json=list(tokens),
                    strategy_document_json=_document_json(doc),
                    code_version=draft.code_version or code_version(),
                    dataset_version_id=draft.dataset_version_id,
                    parameters_json=_jsonable(draft.parameters),
                    engine_config_json=_jsonable(draft.engine_config),
                    outputs_json=_jsonable(draft.outputs),
                    validation_report_json=report_json,
                    validation_report_hash=report_hash,
                    validation_report_hash_key_version=(
                        report_key_version() if report_hash else UNSTAMPED
                    ),
                    outcome=outcome.value,
                    conclusion=draft.conclusion,
                    rejection_reason=rejection,
                    rejected_at_rung=rung,
                    tags_json=list(draft.tags),
                )
            )
            session.commit()
        return self.get(row_id)

    # ---------------------------------------------------------------- read

    def get(self, experiment_id: str) -> Experiment:
        restated = self._restatements()
        with self._session_factory() as session:
            row = session.get(ExperimentRow, str(experiment_id))
            if row is None:
                raise ExperimentNotFound(str(experiment_id))
            return _from_row(row, restated.get(row.id))

    # -------------------------------------------------------- key versions

    def _restatements(self) -> dict[str, dict[str, str]]:
        """``experiment id -> {key column: restated version}``."""
        out: dict[str, dict[str, str]] = {}
        with self._session_factory() as session:
            rows = session.scalars(
                select(ExperimentKeyRestatementRow).order_by(
                    ExperimentKeyRestatementRow.id
                )
            ).all()
        for row in rows:
            if not row.key_version:
                continue
            out.setdefault(row.experiment_id, {})[row.key_column] = row.key_version
        return out

    def key_version_census(self, key_column: str) -> dict[str, int]:
        """``version -> row count`` over every row that HAS a key of this kind.

        Rows with no key are excluded: they are not evidence either way. This is
        the audit an operator runs before trusting a hash lookup.
        """
        self._require_known_key(key_column)
        key = getattr(ExperimentRow, key_column)
        stamp = getattr(ExperimentRow, key_version_column(key_column))
        census: dict[str, int] = {}
        # Two aggregate queries rather than a full materialisation: this runs on
        # every keyed lookup, including inside the ancestry walk, and paying for
        # the whole ledger per hop would make the guard the reason nobody keeps it.
        with self._session_factory() as session:
            for version, count in session.execute(
                select(stamp, func.count())
                .where(key != "", stamp != UNSTAMPED)
                .group_by(stamp)
            ).all():
                census[str(version)] = census.get(str(version), 0) + int(count)
            unstamped_ids = session.scalars(
                select(ExperimentRow.id).where(key != "", stamp == UNSTAMPED)
            ).all()
        if unstamped_ids:
            said = self._restatements()
            for experiment_id in unstamped_ids:
                version = said.get(experiment_id, {}).get(key_column, UNSTAMPED)
                census[version] = census.get(version, 0) + 1
        return dict(sorted(census.items()))

    def assert_keys_comparable(
        self, key_column: str, key_version: str | None = None
    ) -> None:
        """Raise unless every stored key of this kind was derived under ``key_version``."""
        expected = self._expected_key_version(key_column, key_version)
        census = self.key_version_census(key_column)
        foreign = {v: n for v, n in census.items() if v != expected}
        if foreign:
            raise LedgerKeyVersionMismatch(key_column, expected, foreign)

    def restate_key_versions(
        self,
        key_version: str,
        *,
        stated_by: str,
        reason: str,
        key_columns: Sequence[str] | None = None,
    ) -> int:
        """State which version derived the UNSTAMPED keys already in this ledger.

        The migration for a ledger file that predates the version columns. Writes
        one restatement row per (experiment, key column) pair that has a key but no
        version, and returns how many. Idempotent: a pair that already carries a
        version, from its own column or from an earlier restatement, is left alone.
        """
        version = str(key_version).strip()
        if not version:
            raise ValueError(
                "key_version is mandatory: restating a key as UNSTAMPED states "
                "nothing and leaves the lookup untrustworthy"
            )
        if not str(stated_by).strip() or not str(reason).strip():
            raise ValueError(
                "stated_by and reason are mandatory: a restatement asserts how a "
                "stored key was derived, and this ledger does not accept "
                "unattributed assertions -- see ExperimentDraft.reason"
            )
        columns = tuple(KEY_COLUMNS) if key_columns is None else tuple(key_columns)
        for name in columns:
            self._require_known_key(name)
        known = self._restatements()
        written = 0
        now = datetime.now(tz=UTC)
        with self._session_factory() as session:
            rows = session.scalars(select(ExperimentRow).order_by(ExperimentRow.id)).all()
            for row in rows:
                said = known.get(row.id, {})
                for name in columns:
                    value = getattr(row, name, "") or ""
                    if not value:
                        continue
                    if getattr(row, key_version_column(name), "") or said.get(name):
                        continue
                    session.add(
                        ExperimentKeyRestatementRow(
                            experiment_id=row.id,
                            key_column=name,
                            key_value=value,
                            key_version=version,
                            stated_by=str(stated_by),
                            reason=str(reason),
                            created_at=now,
                        )
                    )
                    written += 1
            session.commit()
        return written

    @staticmethod
    def _require_known_key(key_column: str) -> None:
        if str(key_column) not in KEY_COLUMNS:
            raise LedgerError(
                f"{key_column!r} is not a versioned key column; known columns are "
                f"{sorted(KEY_COLUMNS)}"
            )

    @staticmethod
    def _expected_key_version(key_column: str, key_version: str | None) -> str:
        ExperimentLedger._require_known_key(key_column)
        if key_version is not None:
            return str(key_version)
        return str(KEY_COLUMNS[str(key_column)]())

    # ---------------------------------------------------------------- query

    def list(
        self,
        *,
        strategy_content_hash: str | None = None,
        structure_hash: str | None = None,
        strategy_id: str | None = None,
        hypothesis_id: str | None = None,
        dataset_version_id: str | None = None,
        actor_name: str | None = None,
        outcome: Outcome | None = None,
        parent_experiment_id: str | None = None,
        limit: int | None = None,
        allow_stale_keys: bool = False,
    ) -> list[Experiment]:
        """Every experiment matching the filters, oldest first.

        Oldest first because the ledger is read as a history: "what did we try,
        and what happened next" only makes sense in order.

        A filter on a derived key (``strategy_content_hash``, ``structure_hash``)
        first checks that every stored key of that kind is comparable with the one
        being asked about, and raises :class:`LedgerKeyVersionMismatch` if not.
        The unfiltered listing is never refused -- reading the history is always
        legitimate, and it is what the audit and the restatement migration use --
        so ``allow_stale_keys=True`` is only ever needed to force a keyed lookup
        whose answer the caller accepts as possibly incomplete.
        """
        if not allow_stale_keys:
            if strategy_content_hash is not None:
                self.assert_keys_comparable("strategy_content_hash")
            if structure_hash is not None:
                self.assert_keys_comparable("structure_hash")
        restated = self._restatements()
        with self._session_factory() as session:
            stmt = select(ExperimentRow).order_by(
                ExperimentRow.created_at, ExperimentRow.id
            )
            if strategy_content_hash is not None:
                stmt = stmt.where(
                    ExperimentRow.strategy_content_hash == str(strategy_content_hash)
                )
            if structure_hash is not None:
                stmt = stmt.where(ExperimentRow.structure_hash == str(structure_hash))
            if strategy_id is not None:
                stmt = stmt.where(ExperimentRow.strategy_id == str(strategy_id))
            if hypothesis_id is not None:
                stmt = stmt.where(ExperimentRow.hypothesis_id == str(hypothesis_id))
            if dataset_version_id is not None:
                stmt = stmt.where(
                    ExperimentRow.dataset_version_id == str(dataset_version_id)
                )
            if actor_name is not None:
                stmt = stmt.where(ExperimentRow.actor_name == str(actor_name))
            if outcome is not None:
                stmt = stmt.where(ExperimentRow.outcome == outcome.value)
            if parent_experiment_id is not None:
                stmt = stmt.where(
                    ExperimentRow.parent_experiment_id == str(parent_experiment_id)
                )
            if limit is not None:
                stmt = stmt.limit(int(limit))
            return [
                _from_row(r, restated.get(r.id)) for r in session.scalars(stmt).all()
            ]

    def children(self, experiment_id: str) -> builtins.list[Experiment]:
        # ``builtins.list`` spelled out: inside this class body the bare name
        # ``list`` resolves to the query method above, not to the builtin, so an
        # unqualified annotation names a function and every caller that iterates
        # the result is flagged.
        return self.list(parent_experiment_id=str(experiment_id))

    def count(self) -> int:
        with self._session_factory() as session:
            return len(session.scalars(select(ExperimentRow.id)).all())

    def __len__(self) -> int:
        return self.count()


def _document_json(doc: Any) -> dict[str, Any] | None:
    if doc is None:
        return None
    if hasattr(doc, "model_dump"):
        return doc.model_dump(mode="json")
    if isinstance(doc, Mapping):
        return dict(doc)
    return None


def _jsonable(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    return json.loads(json.dumps(dict(payload), default=str, sort_keys=True))


def _from_row(
    row: ExperimentRow, restated: Mapping[str, str] | None = None
) -> Experiment:
    """Build the domain object, resolving each key's version.

    ``restated`` maps key column to a version stated for THIS experiment by a
    restatement row, and is consulted only where the row's own column is empty.
    """
    said = dict(restated or {})
    key_versions = {
        name: resolve_key_version(
            getattr(row, key_version_column(name), UNSTAMPED), said.get(name)
        )
        for name in KEY_COLUMNS
    }
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return Experiment(
        id=row.id,
        created_at=created,
        actor_kind=ActorKind(row.actor_kind),
        actor_name=row.actor_name,
        reason=row.reason or "",
        parent_experiment_id=row.parent_experiment_id or "",
        hypothesis_id=row.hypothesis_id or "",
        strategy_id=row.strategy_id or "",
        strategy_content_hash=row.strategy_content_hash or "",
        strategy_version=row.strategy_version or "",
        structure_hash=row.structure_hash or "",
        structure_tokens=tuple(row.structure_tokens_json or ()),
        strategy_document=dict(row.strategy_document_json)
        if row.strategy_document_json
        else None,
        code_version=row.code_version or "",
        dataset_version_id=row.dataset_version_id or "",
        parameters=dict(row.parameters_json or {}),
        engine_config=dict(row.engine_config_json or {}),
        outputs=dict(row.outputs_json or {}),
        validation_report_json=dict(row.validation_report_json)
        if row.validation_report_json
        else None,
        validation_report_hash=row.validation_report_hash or "",
        outcome=Outcome(row.outcome),
        conclusion=row.conclusion or "",
        rejection_reason=row.rejection_reason or "",
        rejected_at_rung=row.rejected_at_rung or "",
        tags=tuple(row.tags_json or ()),
        key_versions=key_versions,
    )
