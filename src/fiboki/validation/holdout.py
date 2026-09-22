"""The holdout registry: the structural guarantee V1 did not have.

V1 reported "out-of-sample" numbers computed on a segment that the parameter
search had already read. Nothing in the code prevented that, because nothing in
the code knew which data had been looked at. The fix is not discipline, it is a
ledger: one segment per dataset version, owned by this registry, and a
CONSUMPTION row written the moment a strategy hash is allowed to look at it.

Three properties, each enforced rather than documented:

1. **The segment is defined once.** :meth:`HoldoutRegistry.define` computes the
   final ``holdout_fraction`` of a dataset's date range and stores it. Defining
   it again with different facts raises: a holdout that can be moved is not a
   holdout.
2. **A strategy hash gets exactly one look.** :meth:`HoldoutRegistry.claim`
   writes the consumption row FIRST and returns a token. A second claim for the
   same ``(dataset_version_id, strategy_content_hash)`` raises
   :class:`HoldoutAlreadyConsumed`, and it raises whether or not the first
   evaluation finished, crashed, or produced a number anybody liked. Claiming
   before evaluating is the point: a process that dies mid-evaluation must not be
   able to retry until it gets a number it prefers.
3. **Earlier rungs cannot touch it.** :meth:`HoldoutRegistry.assert_untouched`
   refuses any research window that overlaps the segment, so the leak that made
   V1's OOS meaningless is a raised exception rather than a silent number.

Keyed on the strategy CONTENT hash, not the strategy id: renaming a strategy, or
re-registering it under a new id, must not buy a second look.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from fiboki.core.enums import Provenance
from fiboki.validation.evaluation import DateWindow

__all__ = [
    "HoldoutAlreadyConsumed",
    "HoldoutConsumption",
    "HoldoutError",
    "HoldoutLeak",
    "HoldoutRegistry",
    "HoldoutSegment",
    "HoldoutToken",
    "UnknownHoldout",
]

DEFAULT_HOLDOUT_FRACTION = 0.20


class HoldoutError(RuntimeError):
    """Base class for every way the holdout contract can be broken."""


class HoldoutAlreadyConsumed(HoldoutError):
    """A second evaluation of the same strategy on the same holdout."""

    def __init__(self, consumption: HoldoutConsumption) -> None:
        self.consumption = consumption
        super().__init__(
            f"holdout {consumption.dataset_version_id} was already consumed by "
            f"strategy {consumption.strategy_content_hash[:12]} at "
            f"{consumption.claimed_at.isoformat()} "
            f"(experiment {consumption.experiment_id or 'unrecorded'}, "
            f"actor {consumption.actor or 'unrecorded'}). "
            "A holdout evaluated twice is an in-sample number. Use a NEW dataset "
            "version, or accept the result you already have."
        )


class HoldoutLeak(HoldoutError):
    """A research window that overlaps the reserved segment."""


class UnknownHoldout(HoldoutError):
    """No segment has been defined for this dataset version."""


class Base(DeclarativeBase):
    pass


class HoldoutSegmentRow(Base):
    __tablename__ = "holdout_segment"

    dataset_version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    data_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    holdout_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    holdout_fraction: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HoldoutConsumptionRow(Base):
    __tablename__ = "holdout_consumption"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id",
            "strategy_content_hash",
            name="uq_holdout_one_look_per_strategy",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    dataset_version_id: Mapped[str] = mapped_column(String(128), index=True)
    strategy_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    strategy_id: Mapped[str] = mapped_column(String(128), default="")
    experiment_id: Mapped[str] = mapped_column(String(64), default="")
    actor: Mapped[str] = mapped_column(String(128), default="")
    code_version: Mapped[str] = mapped_column(String(64), default="")
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="")


def _utc(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class HoldoutSegment:
    """The reserved tail of one dataset version."""

    dataset_version_id: str
    data_start: pd.Timestamp
    data_end: pd.Timestamp
    holdout_start: pd.Timestamp
    holdout_fraction: float
    label: str = ""

    @property
    def window(self) -> DateWindow:
        return DateWindow(
            f"holdout::{self.dataset_version_id}", self.holdout_start, self.data_end
        )

    @property
    def research_window(self) -> DateWindow:
        """Everything the ladder is allowed to look at before rung 6."""
        return DateWindow(
            f"research::{self.dataset_version_id}", self.data_start, self.holdout_start
        )

    @property
    def provenance(self) -> Provenance:
        return Provenance.HOLDOUT

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_version_id": self.dataset_version_id,
            "data_start": self.data_start.isoformat(),
            "data_end": self.data_end.isoformat(),
            "holdout_start": self.holdout_start.isoformat(),
            "holdout_fraction": float(self.holdout_fraction),
            "label": self.label,
            "holdout_window": self.window.to_dict(),
            "research_window": self.research_window.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class HoldoutConsumption:
    """The record that a strategy has spent its one look."""

    token: str
    dataset_version_id: str
    strategy_content_hash: str
    strategy_id: str
    experiment_id: str
    actor: str
    code_version: str
    claimed_at: datetime
    outcome: dict[str, Any] | None = None
    outcome_recorded_at: datetime | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "dataset_version_id": self.dataset_version_id,
            "strategy_content_hash": self.strategy_content_hash,
            "strategy_id": self.strategy_id,
            "experiment_id": self.experiment_id,
            "actor": self.actor,
            "code_version": self.code_version,
            "claimed_at": self.claimed_at.isoformat(),
            "outcome": self.outcome,
            "outcome_recorded_at": (
                self.outcome_recorded_at.isoformat()
                if self.outcome_recorded_at is not None
                else None
            ),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class HoldoutToken:
    """Proof that a claim was recorded. Required to record an outcome."""

    token: str
    segment: HoldoutSegment
    strategy_content_hash: str

    @property
    def window(self) -> DateWindow:
        return self.segment.window


class HoldoutRegistry:
    """SQLite-backed registry of reserved segments and their consumption."""

    def __init__(self, db_path: str | Path = ":memory:", *, echo: bool = False) -> None:
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else None
        if self.db_path is None:
            url = "sqlite+pysqlite:///:memory:"
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite+pysqlite:///{self.db_path}"
        self._engine = create_engine(url, echo=echo, future=True)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, future=True)

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> HoldoutRegistry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @classmethod
    def in_memory(cls) -> HoldoutRegistry:
        return cls(":memory:")

    # ------------------------------------------------------------- segments

    def define(
        self,
        dataset_version_id: str,
        *,
        data_start: Any,
        data_end: Any,
        holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
        label: str = "",
    ) -> HoldoutSegment:
        """Reserve the final ``holdout_fraction`` of this dataset's date range.

        Idempotent for identical facts. Re-defining with a different range or a
        different fraction raises: the whole value of a holdout is that it was
        fixed BEFORE anyone saw a result.
        """
        if not 0.0 < holdout_fraction < 1.0:
            raise ValueError("holdout_fraction must be in (0, 1)")
        start, end = _utc(data_start), _utc(data_end)
        if end <= start:
            raise ValueError("data_end must be after data_start")
        span = (end - start).total_seconds()
        holdout_start = start + pd.Timedelta(seconds=span * (1.0 - holdout_fraction))
        segment = HoldoutSegment(
            dataset_version_id=str(dataset_version_id),
            data_start=start,
            data_end=end,
            holdout_start=holdout_start,
            holdout_fraction=float(holdout_fraction),
            label=str(label),
        )
        with self._session_factory() as session:
            existing = session.get(HoldoutSegmentRow, segment.dataset_version_id)
            if existing is not None:
                current = _segment_from_row(existing)
                if (
                    current.data_start != segment.data_start
                    or current.data_end != segment.data_end
                    or abs(current.holdout_fraction - segment.holdout_fraction) > 1e-12
                ):
                    raise HoldoutError(
                        f"holdout for {segment.dataset_version_id} is already defined as "
                        f"{current.window} at fraction {current.holdout_fraction}; "
                        f"refusing to redefine it as {segment.window} at fraction "
                        f"{segment.holdout_fraction}. Mint a new dataset version instead."
                    )
                return current
            session.add(
                HoldoutSegmentRow(
                    dataset_version_id=segment.dataset_version_id,
                    data_start=segment.data_start.to_pydatetime(),
                    data_end=segment.data_end.to_pydatetime(),
                    holdout_start=segment.holdout_start.to_pydatetime(),
                    holdout_fraction=segment.holdout_fraction,
                    label=segment.label,
                    created_at=datetime.now(tz=UTC),
                )
            )
            session.commit()
        return segment

    def define_from_index(
        self,
        dataset_version_id: str,
        index: pd.DatetimeIndex,
        *,
        holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
        label: str = "",
    ) -> HoldoutSegment:
        """Define a segment from a bar index (its first and last timestamps)."""
        if len(index) < 2:
            raise ValueError("need at least two timestamps to define a holdout")
        # The window is half-open, so the end must be strictly after the last bar
        # or that bar falls outside every window and is never evaluated.
        step = pd.Timedelta(index[-1] - index[-2])
        return self.define(
            dataset_version_id,
            data_start=index[0],
            data_end=pd.Timestamp(index[-1]) + step,
            holdout_fraction=holdout_fraction,
            label=label,
        )

    def segment(self, dataset_version_id: str) -> HoldoutSegment:
        with self._session_factory() as session:
            row = session.get(HoldoutSegmentRow, str(dataset_version_id))
            if row is None:
                raise UnknownHoldout(
                    f"no holdout segment defined for dataset version "
                    f"{dataset_version_id!r}; call define() before validating."
                )
            return _segment_from_row(row)

    def segments(self) -> list[HoldoutSegment]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(HoldoutSegmentRow).order_by(HoldoutSegmentRow.dataset_version_id)
            ).all()
            return [_segment_from_row(r) for r in rows]

    # ------------------------------------------------------------- leakage

    def assert_untouched(self, dataset_version_id: str, *windows: DateWindow) -> None:
        """Refuse any window that overlaps the reserved segment."""
        segment = self.segment(dataset_version_id)
        holdout = segment.window
        for window in windows:
            if window.overlaps(holdout):
                raise HoldoutLeak(
                    f"window {window} overlaps the reserved holdout {holdout}. "
                    "Every rung before RUNG 6 must run strictly inside "
                    f"{segment.research_window}."
                )

    # ------------------------------------------------------------- claiming

    def is_consumed(self, dataset_version_id: str, strategy_content_hash: str) -> bool:
        return self.consumption(dataset_version_id, strategy_content_hash) is not None

    def consumption(
        self, dataset_version_id: str, strategy_content_hash: str
    ) -> HoldoutConsumption | None:
        with self._session_factory() as session:
            row = session.scalars(
                select(HoldoutConsumptionRow).where(
                    HoldoutConsumptionRow.dataset_version_id == str(dataset_version_id),
                    HoldoutConsumptionRow.strategy_content_hash == str(strategy_content_hash),
                )
            ).first()
            return _consumption_from_row(row) if row is not None else None

    def claim(
        self,
        dataset_version_id: str,
        strategy_content_hash: str,
        *,
        strategy_id: str = "",
        experiment_id: str = "",
        actor: str = "",
        code_version: str = "",
        note: str = "",
    ) -> HoldoutToken:
        """Spend this strategy's one look at the holdout.

        The row is written BEFORE any evaluation runs. If the caller crashes, the
        look is still spent -- which is correct: the alternative is a process
        that can re-roll the holdout until it likes the answer.
        """
        segment = self.segment(dataset_version_id)
        existing = self.consumption(dataset_version_id, strategy_content_hash)
        if existing is not None:
            raise HoldoutAlreadyConsumed(existing)
        token = f"hold_{uuid.uuid4().hex}"
        with self._session_factory() as session:
            session.add(
                HoldoutConsumptionRow(
                    token=token,
                    dataset_version_id=segment.dataset_version_id,
                    strategy_content_hash=str(strategy_content_hash),
                    strategy_id=str(strategy_id),
                    experiment_id=str(experiment_id),
                    actor=str(actor),
                    code_version=str(code_version),
                    claimed_at=datetime.now(tz=UTC),
                    note=str(note),
                )
            )
            try:
                session.commit()
            except Exception:  # pragma: no cover - race with a concurrent claim
                session.rollback()
                raced = self.consumption(dataset_version_id, strategy_content_hash)
                if raced is not None:
                    raise HoldoutAlreadyConsumed(raced) from None
                raise
        return HoldoutToken(
            token=token,
            segment=segment,
            strategy_content_hash=str(strategy_content_hash),
        )

    def record_outcome(self, token: HoldoutToken | str, outcome: dict[str, Any]) -> None:
        """Attach the result of the one permitted evaluation to its claim."""
        key = token.token if isinstance(token, HoldoutToken) else str(token)
        with self._session_factory() as session:
            row = session.scalars(
                select(HoldoutConsumptionRow).where(HoldoutConsumptionRow.token == key)
            ).first()
            if row is None:
                raise HoldoutError(f"unknown holdout token {key!r}")
            if row.outcome_json is not None:
                raise HoldoutError(
                    f"holdout token {key!r} already carries an outcome; a second "
                    "outcome would mean a second evaluation."
                )
            row.outcome_json = dict(outcome)
            row.outcome_recorded_at = datetime.now(tz=UTC)
            session.commit()

    def consumptions(
        self, dataset_version_id: str | None = None
    ) -> list[HoldoutConsumption]:
        with self._session_factory() as session:
            stmt = select(HoldoutConsumptionRow).order_by(HoldoutConsumptionRow.id)
            if dataset_version_id is not None:
                stmt = stmt.where(
                    HoldoutConsumptionRow.dataset_version_id == str(dataset_version_id)
                )
            return [_consumption_from_row(r) for r in session.scalars(stmt).all()]

    def consumed_hashes(self, dataset_version_id: str) -> tuple[str, ...]:
        return tuple(c.strategy_content_hash for c in self.consumptions(dataset_version_id))


def _segment_from_row(row: HoldoutSegmentRow) -> HoldoutSegment:
    return HoldoutSegment(
        dataset_version_id=row.dataset_version_id,
        data_start=_utc(_aware(row.data_start)),
        data_end=_utc(_aware(row.data_end)),
        holdout_start=_utc(_aware(row.holdout_start)),
        holdout_fraction=float(row.holdout_fraction),
        label=row.label or "",
    )


def _consumption_from_row(row: HoldoutConsumptionRow) -> HoldoutConsumption:
    return HoldoutConsumption(
        token=row.token,
        dataset_version_id=row.dataset_version_id,
        strategy_content_hash=row.strategy_content_hash,
        strategy_id=row.strategy_id or "",
        experiment_id=row.experiment_id or "",
        actor=row.actor or "",
        code_version=row.code_version or "",
        claimed_at=_aware(row.claimed_at),
        outcome=dict(row.outcome_json) if row.outcome_json is not None else None,
        outcome_recorded_at=(
            _aware(row.outcome_recorded_at) if row.outcome_recorded_at is not None else None
        ),
        note=row.note or "",
    )
