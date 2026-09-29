"""The event channel's deterministic half: quarantined store, veto policy, shadow report.

An LLM (the ``event_classifier`` agent role, which holds no read tool at all)
classifies batches of recorded headlines. Its structured output is written,
through one agent write tool, into the **quarantined** append-only table this
module owns. Everything downstream of that table is deterministic code in this
file: no model is consulted, no free text is read.

======================  =====================================================
Part                    What it does
======================  =====================================================
:class:`AnnotationStore`  ``<state_dir>/events/annotations.sqlite`` (WAL).
                        ``event_annotation`` and ``scan_log`` are append-only,
                        enforced by triggers that refuse UPDATE, DELETE and
                        a colliding INSERT (the ``data/news/store.py``
                        pattern). The model's rationale is stored here as
                        quoted data and nowhere else; :class:`EventAnnotation`
                        does not carry it.
:class:`EventVetoPolicy`  Versioned constants. ``enabled=False`` by default.
                        May only say "block this NEW entry"; it has no way to
                        express a size, a stop, an exit or a kill switch.
:class:`EventVetoSource`  What the risk gateway holds (via ``RiskContext``).
                        Point-in-time: at ``t`` it sees only annotations with
                        ``available_at <= t``. Missing, unreadable or stale
                        store = NO veto plus an operator alert.
:func:`shadow_report`   Per trade: would the veto have blocked it, and would
                        the two deterministic baselines the plan names (a
                        realised-volatility spike filter and a scheduled-
                        calendar-only window)? Aggregates adverse excursion
                        and net expectancy for blocked vs kept.
======================  =====================================================

Fail-open, deliberately
-----------------------
Everywhere else in the risk path, unknown blocks. This channel is the
exception and says so: a veto can only ever remove an entry, so when its input
is missing the conservative-for-the-evidence answer is "no veto", paired with
an alert so the absence is visible. Failing closed here would let a dead
classifier (or a model host that is switched off) halt all trading, which
would hand the LLM layer an indirect kill switch. A BUG in the gateway check
itself still fails closed, like every other check.

Instrument -> bucket mapping (``instrument_buckets``)
-----------------------------------------------------
Deterministic, from ``core/instruments``: FX pairs map to their two
currencies; XAU/XAG to the metal plus the quote currency; WTI/Brent to ``OIL``
plus the quote currency; an index to its region bucket by quote currency
(USD -> INDEX_US, EUR -> INDEX_EU, GBP -> INDEX_UK, JPY -> INDEX_JP) plus that
currency when it is one of the eight. HK50 (HKD) and AU200 have no region
bucket: AU200 maps to AUD only and HK50 to nothing, so HK50 can never be
vetoed. That is a stated gap, not an oversight.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.contracts import (
    EVENT_BUCKETS,
    EVENT_TYPES,
    EventAnnotation,
    VetoAssessment,
    VetoReason,
)
from fiboki.core.enums import AssetClass
from fiboki.data.news.store import to_utc_text
from fiboki.marketstate.calendar import EconomicCalendar, ImpactLevel

__all__ = [
    "ANNOTATION_POLICY_VERSION",
    "APPEND_ONLY_MESSAGE",
    "DEFAULT_EVENT_VETO_POLICY",
    "DEFAULT_VOL_FILTER",
    "AnnotationDraft",
    "AnnotationStore",
    "AnnotationStoreError",
    "ChannelComparison",
    "EventVetoPolicy",
    "EventVetoSource",
    "GroupStats",
    "ShadowReport",
    "ShadowTradeRow",
    "VolSpikeFilter",
    "default_annotation_store_path",
    "instrument_buckets",
    "shadow_report",
]

_log = logging.getLogger("fiboki.marketstate.events")

#: Version of the annotation contract: the event vocabulary, the bucket
#: vocabulary, the classifier output schema and ``instrument_buckets``. Stamped
#: on every stored annotation, so a change to any of them is visible in the
#: data rather than silently mixing two meanings in one table.
ANNOTATION_POLICY_VERSION = "event_annotation_v1"
APPEND_ONLY_MESSAGE = "event annotation store is append-only"
SCHEMA_VERSION = 1
#: Longest rationale the store accepts, in characters (mirrors the agent schema).
MAX_RATIONALE_CHARS = 300


def default_annotation_store_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / "events" / "annotations.sqlite"


def _utc(value: datetime | pd.Timestamp, name: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        raise ValueError(f"{name}={value!r} is timezone-naive; every instant here is UTC-aware")
    return ts.tz_convert("UTC")


def _from_text(text: str) -> pd.Timestamp:
    return pd.Timestamp(
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    )


# ---------------------------------------------------------------------------
# Instrument -> exposure buckets
# ---------------------------------------------------------------------------

_CURRENCY_BUCKETS = frozenset({"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"})
_INDEX_REGION = {"USD": "INDEX_US", "EUR": "INDEX_EU", "GBP": "INDEX_UK", "JPY": "INDEX_JP"}
_ENERGY_BASES = frozenset({"WTI", "BCO"})


def instrument_buckets(symbol: str) -> frozenset[str]:
    """The event buckets whose news moves ``symbol``. Raises for an unknown symbol."""
    inst = instrument_registry.get(symbol)
    base, quote = inst.base.upper(), inst.quote.upper()
    out: set[str] = set()
    if inst.asset_class is AssetClass.INDEX:
        region = _INDEX_REGION.get(quote)
        if region is not None:
            out.add(region)
    elif inst.asset_class is AssetClass.ENERGY or base in _ENERGY_BASES:
        out.add("OIL")
    elif base in ("XAU", "XAG") or base in _CURRENCY_BUCKETS:
        out.add(base)
    if quote in _CURRENCY_BUCKETS:
        out.add(quote)
    assert out <= set(EVENT_BUCKETS)
    return frozenset(out)


# ---------------------------------------------------------------------------
# The quarantined store
# ---------------------------------------------------------------------------


class AnnotationStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AnnotationDraft:
    """What the agent write tool hands the store: validated fields, no id yet.

    The agent layer builds this, never :class:`EventAnnotation`; only this
    module turns a draft into the contract the policy consumes.
    """

    source_ids: tuple[str, ...]
    event_type: str
    currencies: tuple[str, ...]
    severity: int
    scheduled: bool
    confidence: float
    rationale: str
    observed_at: datetime
    available_at: datetime
    model_id: str
    model_digest: str
    manifest_hash: str
    policy_version: str = ANNOTATION_POLICY_VERSION


_TYPES_SQL = ",".join(f"'{t}'" for t in EVENT_TYPES)

_DDL = (
    f"""
    CREATE TABLE IF NOT EXISTS event_annotation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        annotation_id TEXT NOT NULL UNIQUE,
        source_ids_json TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN ({_TYPES_SQL})),
        currencies_json TEXT NOT NULL,
        severity INTEGER NOT NULL CHECK (severity BETWEEN 0 AND 3),
        scheduled INTEGER NOT NULL CHECK (scheduled IN (0, 1)),
        confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
        rationale TEXT NOT NULL CHECK (length(rationale) <= {MAX_RATIONALE_CHARS}),
        observed_at TEXT NOT NULL CHECK (length(observed_at) = 27),
        available_at TEXT NOT NULL CHECK (length(available_at) = 27),
        model_id TEXT NOT NULL CHECK (length(model_id) > 0),
        model_digest TEXT NOT NULL CHECK (length(model_digest) > 0),
        manifest_hash TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        batch_digest TEXT NOT NULL,
        recorded_by TEXT NOT NULL,
        workflow_id TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS event_annotation_available ON event_annotation (available_at)",
    "CREATE INDEX IF NOT EXISTS event_annotation_observed ON event_annotation (observed_at)",
    """
    CREATE TABLE IF NOT EXISTS scan_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        as_of TEXT NOT NULL CHECK (length(as_of) = 27),
        since TEXT NOT NULL CHECK (length(since) = 27),
        finished_at TEXT NOT NULL CHECK (length(finished_at) = 27),
        outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'partial', 'error')),
        n_headlines INTEGER NOT NULL,
        n_batches INTEGER NOT NULL,
        n_annotations INTEGER NOT NULL,
        truncated INTEGER NOT NULL CHECK (truncated IN (0, 1)),
        workflow_id TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)

_APPEND_ONLY_TABLES: dict[str, str] = {
    "event_annotation": "annotation_id = NEW.annotation_id OR id = NEW.id",
    "scan_log": "id = NEW.id",
    "store_meta": "key = NEW.key",
}


def _triggers() -> list[str]:
    out: list[str] = []
    for table, collision in _APPEND_ONLY_TABLES.items():
        out.append(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}'); END"
        )
        out.append(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}'); END"
        )
        out.append(
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {collision}) "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}: row exists'); END"
        )
    return out


_SELECT = (
    "SELECT annotation_id, source_ids_json, event_type, currencies_json, severity, "
    "scheduled, confidence, observed_at, available_at, model_id, model_digest, "
    "manifest_hash, policy_version FROM event_annotation"
)


def _row_to_annotation(r: Sequence[Any]) -> EventAnnotation:
    return EventAnnotation(
        annotation_id=str(r[0]),
        source_ids=tuple(json.loads(r[1])),
        event_type=str(r[2]),
        currencies=tuple(json.loads(r[3])),
        severity=int(r[4]),
        scheduled=bool(r[5]),
        confidence=float(r[6]),
        observed_at=_from_text(r[7]),
        available_at=_from_text(r[8]),
        model_id=str(r[9]),
        model_digest=str(r[10]),
        manifest_hash=str(r[11]),
        policy_version=str(r[12]),
    )


def _annotation_id(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "evt_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


class AnnotationStore:
    """Append-only SQLite store for event annotations and scan heartbeats.

    ``read_only=True`` opens an EXISTING file with ``mode=ro`` and never creates
    one: a reader that created an empty store would turn "the classifier never
    ran" into "the classifier ran and found nothing".
    """

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path).expanduser()
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise AnnotationStoreError(f"annotation store {self.path} does not exist")
            self._conn = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, timeout=30.0, isolation_level=None
            )
            row = self._conn.execute(
                "SELECT value FROM store_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None or int(row[0]) != SCHEMA_VERSION:
                raise AnnotationStoreError(
                    f"{self.path} has schema {row[0] if row else None}, this code reads "
                    f"{SCHEMA_VERSION}"
                )
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        with self._tx():
            for ddl in (*_DDL, *_triggers()):
                self._conn.execute(ddl)
            row = self._conn.execute(
                "SELECT value FROM store_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO store_meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row[0]) != SCHEMA_VERSION:
                raise AnnotationStoreError(
                    f"{self.path} has schema {row[0]}, this code writes {SCHEMA_VERSION}"
                )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AnnotationStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise AnnotationStoreError("this store was opened read-only")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    # -- writes -----------------------------------------------------------

    def append(
        self,
        drafts: Sequence[AnnotationDraft],
        *,
        batch_digest: str,
        recorded_by: str,
        workflow_id: str = "",
    ) -> tuple[EventAnnotation, ...]:
        """Validate and insert every draft in ONE transaction; all or nothing."""
        built: list[tuple[EventAnnotation, str]] = []
        for index, d in enumerate(drafts):
            if len(d.rationale) > MAX_RATIONALE_CHARS:
                raise AnnotationStoreError(
                    f"rationale is {len(d.rationale)} characters; the limit is "
                    f"{MAX_RATIONALE_CHARS}"
                )
            observed = _utc(d.observed_at, "observed_at")
            available = _utc(d.available_at, "available_at")
            payload = {
                "batch_digest": batch_digest,
                "index": index,
                "source_ids": list(d.source_ids),
                "event_type": d.event_type,
                "currencies": list(d.currencies),
                "severity": d.severity,
                "scheduled": d.scheduled,
                "confidence": d.confidence,
                "rationale": d.rationale,
                "observed_at": to_utc_text(observed.to_pydatetime()),
                "available_at": to_utc_text(available.to_pydatetime()),
                "model_digest": d.model_digest,
            }
            annotation = EventAnnotation(
                annotation_id=_annotation_id(payload),
                source_ids=tuple(d.source_ids),
                event_type=d.event_type,
                currencies=tuple(d.currencies),
                severity=int(d.severity),
                scheduled=bool(d.scheduled),
                confidence=float(d.confidence),
                observed_at=observed,
                available_at=available,
                model_id=d.model_id,
                model_digest=d.model_digest,
                manifest_hash=d.manifest_hash,
                policy_version=d.policy_version,
            )
            built.append((annotation, d.rationale))
        with self._tx() as conn:
            for a, rationale in built:
                conn.execute(
                    "INSERT INTO event_annotation (annotation_id, source_ids_json, event_type, "
                    "currencies_json, severity, scheduled, confidence, rationale, observed_at, "
                    "available_at, model_id, model_digest, manifest_hash, policy_version, "
                    "batch_digest, recorded_by, workflow_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        a.annotation_id, json.dumps(list(a.source_ids)), a.event_type,
                        json.dumps(list(a.currencies)), a.severity, int(a.scheduled),
                        a.confidence, rationale,
                        to_utc_text(a.observed_at.to_pydatetime()),
                        to_utc_text(a.available_at.to_pydatetime()),
                        a.model_id, a.model_digest, a.manifest_hash, a.policy_version,
                        batch_digest, recorded_by, workflow_id,
                    ),
                )
        return tuple(a for a, _ in built)

    def log_scan(
        self,
        *,
        as_of: datetime,
        since: datetime,
        finished_at: datetime,
        outcome: str,
        n_headlines: int,
        n_batches: int,
        n_annotations: int,
        truncated: bool,
        workflow_id: str,
        detail: str = "",
    ) -> None:
        """One heartbeat row per scan, INCLUDING scans that found nothing.

        The freshness guard reads this: "no annotation" and "no classifier" are
        different facts, and only the scan log can tell them apart.
        """
        if outcome not in ("ok", "partial", "error"):
            raise AnnotationStoreError(f"unknown scan outcome {outcome!r}")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO scan_log (as_of, since, finished_at, outcome, n_headlines, "
                "n_batches, n_annotations, truncated, workflow_id, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (to_utc_text(as_of), to_utc_text(since), to_utc_text(finished_at), outcome,
                 int(n_headlines), int(n_batches), int(n_annotations), int(bool(truncated)),
                 workflow_id, detail),
            )

    # -- reads ------------------------------------------------------------

    def annotations(
        self,
        *,
        at: datetime | pd.Timestamp | None = None,
        observed_since: datetime | pd.Timestamp | None = None,
    ) -> tuple[EventAnnotation, ...]:
        """Annotations AVAILABLE by ``at`` (``available_at <= at``), oldest first.

        ``at=None`` returns every row: that is for the shadow report, which
        applies the point-in-time rule per trade itself.
        """
        sql = _SELECT
        clauses: list[str] = []
        params: list[Any] = []
        if at is not None:
            text = to_utc_text(_utc(at, "at").to_pydatetime())
            clauses += ["available_at <= ?", "observed_at <= ?"]
            params += [text, text]
        if observed_since is not None:
            clauses.append("observed_at >= ?")
            params.append(to_utc_text(_utc(observed_since, "observed_since").to_pydatetime()))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY observed_at ASC, id ASC"
        return tuple(_row_to_annotation(r) for r in self._conn.execute(sql, params))

    def rationale(self, annotation_id: str) -> str:
        """The model's rationale, as quoted data. No decision path reads this."""
        row = self._conn.execute(
            "SELECT rationale FROM event_annotation WHERE annotation_id = ?", (annotation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(annotation_id)
        return str(row[0])

    def last_ok_scan_as_of(self, at: datetime | pd.Timestamp | None = None) -> pd.Timestamp | None:
        """``as_of`` of the newest scan with outcome ``ok`` that FINISHED by ``at``."""
        sql = "SELECT MAX(as_of) FROM scan_log WHERE outcome = 'ok'"
        params: list[Any] = []
        if at is not None:
            sql += " AND finished_at <= ?"
            params.append(to_utc_text(_utc(at, "at").to_pydatetime()))
        row = self._conn.execute(sql, params).fetchone()
        return None if row is None or row[0] is None else _from_text(row[0])

    def last_scan_as_of(self) -> pd.Timestamp | None:
        """``as_of`` of the newest scan of any outcome: where the next scan starts."""
        row = self._conn.execute("SELECT MAX(as_of) FROM scan_log").fetchone()
        return None if row is None or row[0] is None else _from_text(row[0])

    def scans(self) -> list[dict[str, Any]]:
        cols = ("as_of", "since", "finished_at", "outcome", "n_headlines", "n_batches",
                "n_annotations", "truncated", "workflow_id", "detail")
        return [
            dict(zip(cols, r, strict=True))
            for r in self._conn.execute(f"SELECT {', '.join(cols)} FROM scan_log ORDER BY id")
        ]

    def status(self) -> dict[str, Any]:
        n = self._conn.execute("SELECT COUNT(*) FROM event_annotation").fetchone()[0]
        scans = self._conn.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0]
        last = self.last_ok_scan_as_of()
        return {
            "path": str(self.path),
            "annotations": int(n),
            "scans": int(scans),
            "last_ok_scan_as_of": None if last is None else last.isoformat(),
        }


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventVetoPolicy:
    """Versioned constants of the event veto. Off by default; shadow first.

    ``max_annotation_age`` is the pipeline freshness guard: if the newest
    SUCCESSFUL scan on file is older than this at decision time, the source
    reports itself unavailable (still no veto from absence) and alerts.
    """

    version: str = "event_veto_v1"
    enabled: bool = False
    min_severity: int = 2
    min_confidence: float = 0.6
    veto_window: timedelta = timedelta(hours=2)
    require_unscheduled: bool = True
    max_annotation_age: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("an event veto policy needs a version")
        if self.min_severity not in (1, 2, 3):
            raise ValueError(
                f"min_severity={self.min_severity}: must be 1..3 (0 would veto on "
                "headlines the classifier called irrelevant)"
            )
        if not (0.0 <= self.min_confidence <= 1.0):
            raise ValueError(f"min_confidence={self.min_confidence} is outside [0, 1]")
        if self.veto_window <= timedelta(0):
            raise ValueError("veto_window must be positive")
        if self.max_annotation_age <= timedelta(0):
            raise ValueError("max_annotation_age must be positive")

    def stamp(self) -> dict[str, Any]:
        return {
            "event_veto_policy": self.version,
            "event_veto_enabled": self.enabled,
            "min_severity": self.min_severity,
            "min_confidence": self.min_confidence,
            "veto_window_s": self.veto_window.total_seconds(),
            "require_unscheduled": self.require_unscheduled,
            "max_annotation_age_s": self.max_annotation_age.total_seconds(),
            "annotation_policy_version": ANNOTATION_POLICY_VERSION,
        }

    def matches(
        self, annotation: EventAnnotation, buckets: frozenset[str], at: pd.Timestamp
    ) -> bool:
        """Would ``annotation`` veto a new entry on an instrument with ``buckets`` at ``at``?"""
        return (
            annotation.available_at <= at
            and annotation.observed_at <= at < annotation.observed_at + self.veto_window
            and annotation.event_type != "none"
            and annotation.severity >= self.min_severity
            and annotation.confidence >= self.min_confidence
            and not (self.require_unscheduled and annotation.scheduled)
            and bool(set(annotation.currencies) & buckets)
        )

    def veto_among(
        self, annotations: Iterable[EventAnnotation], instrument: str, at: datetime | pd.Timestamp
    ) -> VetoReason | None:
        """The single most severe matching annotation, chosen deterministically."""
        ts = _utc(at, "at")
        buckets = instrument_buckets(instrument)
        hits = [a for a in annotations if self.matches(a, buckets, ts)]
        if not hits:
            return None
        best = min(
            hits, key=lambda a: (-a.severity, -a.confidence, a.observed_at, a.annotation_id)
        )
        shared = tuple(b for b in EVENT_BUCKETS if b in buckets and b in best.currencies)
        return VetoReason(
            annotation_id=best.annotation_id,
            event_type=best.event_type,
            buckets=shared,
            severity=best.severity,
            confidence=best.confidence,
            observed_at=best.observed_at,
            policy_version=self.version,
        )


DEFAULT_EVENT_VETO_POLICY = EventVetoPolicy()

#: ``alert(message, context)``. ``marketstate`` may not import ``obs`` (same
#: rank), so the composition root adapts this to its alert dispatcher.
AlertSink = Callable[[str, Mapping[str, Any]], None]


class EventVetoSource:
    """The object a ``RiskContext`` carries: point-in-time, fail-open, alerting.

    Satisfies ``fiboki.risk.gateway.EventVetoProvider`` structurally; neither
    package imports the other.
    """

    def __init__(
        self,
        path: str | Path,
        policy: EventVetoPolicy = DEFAULT_EVENT_VETO_POLICY,
        *,
        alert: AlertSink | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.policy = policy
        self._alert = alert
        self._store: AnnotationStore | None = None
        self._alerted: str | None = None
        self.alerts_raised = 0

    @property
    def enabled(self) -> bool:
        return self.policy.enabled

    @property
    def policy_version(self) -> str:
        return self.policy.version

    def _raise_alert(self, detail: str, instrument: str, at: pd.Timestamp) -> None:
        key = detail.split(":", 1)[0]
        if key == self._alerted:
            return  # one alert per transition, not one per decision
        self._alerted = key
        self.alerts_raised += 1
        message = (
            f"event veto source unavailable ({detail}); NO veto is applied while it is "
            "(fail-open by design). Entries are not being screened for unscheduled events."
        )
        context = {
            "detail": detail,
            "path": str(self.path),
            "policy_version": self.policy.version,
            "enabled": self.policy.enabled,
            "instrument": instrument,
            "at": at.isoformat(),
        }
        if self._alert is None:
            _log.warning(message, extra=context)
            return
        try:
            self._alert(message, context)
        except Exception:  # an alert channel failing must not change the verdict
            _log.exception("event veto alert sink raised", extra=context)

    def assess(self, instrument: str, at: datetime | pd.Timestamp) -> VetoAssessment:
        ts = _utc(at, "at")
        instrument_buckets(instrument)  # unknown instrument raises: the gateway fails closed
        policy = self.policy
        try:
            if self._store is None:
                self._store = AnnotationStore(self.path, read_only=True)
            store = self._store
            annotations = store.annotations(at=ts, observed_since=ts - policy.veto_window)
            fresh_as_of = store.last_ok_scan_as_of(ts)
        except (AnnotationStoreError, sqlite3.Error, OSError, ValueError) as exc:
            self._store = None  # reopen on the next question
            missing = isinstance(exc, AnnotationStoreError) and "does not exist" in str(exc)
            detail = "store_missing" if missing else f"store_unreadable:{type(exc).__name__}"
            self._raise_alert(detail, instrument, ts)
            return VetoAssessment(policy.version, policy.enabled, False, None, detail)
        veto = policy.veto_among(annotations, instrument, ts)
        detail = ""
        if fresh_as_of is None:
            detail = "no_successful_scan_on_file"
        else:
            age = (ts - fresh_as_of).total_seconds()
            if age > policy.max_annotation_age.total_seconds():
                detail = f"stale:{age:.0f}s>{policy.max_annotation_age.total_seconds():.0f}s"
        if detail:
            self._raise_alert(detail, instrument, ts)
            return VetoAssessment(policy.version, policy.enabled, False, veto, detail)
        self._alerted = None
        return VetoAssessment(policy.version, policy.enabled, True, veto, "")

    def veto_for(self, instrument: str, at: datetime | pd.Timestamp) -> VetoReason | None:
        return self.assess(instrument, at).veto


# ---------------------------------------------------------------------------
# Shadow evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VolSpikeFilter:
    """Deterministic baseline 1: skip entries after a realised-volatility spike.

    At decision time ``t`` it reads only bars CLOSED by ``t`` (bars are
    left-labelled; the bar length is the smallest positive index step). A
    spike is ``mean(TR over the last short_bars) > multiple * median(TR over
    the long_bars before them)``. Too little history is NOT_EVALUATED (None),
    never "no spike".
    """

    version: str = "vol_spike_v1"
    short_bars: int = 3
    long_bars: int = 100
    multiple: float = 2.0

    def flags(self, bars: pd.DataFrame, at: pd.Timestamp) -> bool | None:
        if bars.empty or len(bars.index) < 2:
            return None
        index = pd.DatetimeIndex(bars.index)
        if index.tz is None:
            raise ValueError("vol spike filter needs a tz-aware bar index")
        steps = index.to_series().diff().dropna()
        steps = steps[steps > pd.Timedelta(0)]
        if steps.empty:
            return None
        bar = steps.min()
        closed = bars.loc[(index + bar) <= at]
        need = self.short_bars + self.long_bars + 1
        if len(closed) < need:
            return None
        window = closed.iloc[-need:]
        high = window["high"].to_numpy(dtype=float)
        low = window["low"].to_numpy(dtype=float)
        close = window["close"].to_numpy(dtype=float)
        prev = close[:-1]
        tr = [
            max(h - lo, abs(h - p), abs(lo - p))
            for h, lo, p in zip(high[1:], low[1:], prev, strict=True)
        ]
        recent = tr[-self.short_bars:]
        base = sorted(tr[: -self.short_bars])
        mid = len(base) // 2
        median = base[mid] if len(base) % 2 else 0.5 * (base[mid - 1] + base[mid])
        if median <= 0:
            return None
        # A Python bool, never numpy's: the shadow split tests ``is True``.
        return bool((sum(recent) / len(recent)) > self.multiple * median)


DEFAULT_VOL_FILTER = VolSpikeFilter()


@dataclass(frozen=True, slots=True)
class ShadowTradeRow:
    trade_id: str
    instrument: str
    entry_time: pd.Timestamp
    net_pnl: float
    #: |max adverse excursion|, in the ACCOUNT currency (as ``Trade`` records it).
    adverse_excursion: float
    #: ``VetoReason.code`` of the veto that would have blocked it, or None.
    event_veto: str | None
    #: True/False, or None when the baseline could not be evaluated.
    vol_spike: bool | None
    calendar_only: bool | None


@dataclass(frozen=True, slots=True)
class GroupStats:
    n: int
    total_net_pnl: float
    #: Mean net P&L per trade. None for an empty group: absence is not zero.
    net_expectancy: float | None
    mean_adverse_excursion: float | None

    @classmethod
    def of(cls, rows: Sequence[ShadowTradeRow]) -> GroupStats:
        if not rows:
            return cls(0, 0.0, None, None)
        total = math.fsum(r.net_pnl for r in rows)
        return cls(
            n=len(rows),
            total_net_pnl=total,
            net_expectancy=total / len(rows),
            mean_adverse_excursion=math.fsum(r.adverse_excursion for r in rows) / len(rows),
        )


@dataclass(frozen=True, slots=True)
class ChannelComparison:
    """One filter's split of the trades into would-have-been-blocked and kept."""

    name: str
    version: str
    blocked: GroupStats
    kept: GroupStats
    not_evaluated: int

    @property
    def expectancy_kept_minus_blocked(self) -> float | None:
        """Positive means the filter removed worse-than-average trades."""
        if self.blocked.net_expectancy is None or self.kept.net_expectancy is None:
            return None
        return self.kept.net_expectancy - self.blocked.net_expectancy


@dataclass(frozen=True, slots=True)
class ShadowReport:
    policy: Mapping[str, Any]
    n_trades: int
    n_annotations: int
    rows: tuple[ShadowTradeRow, ...]
    channels: tuple[ChannelComparison, ...]
    min_affected_trades: int
    caveats: tuple[str, ...] = field(default_factory=tuple)

    def channel(self, name: str) -> ChannelComparison:
        for c in self.channels:
            if c.name == name:
                return c
        raise KeyError(name)

    @property
    def sufficient(self) -> bool:
        """Enough vetoed trades to read the comparison at all (pre-registered floor)."""
        return self.channel("event_veto").blocked.n >= self.min_affected_trades


def _split(
    name: str, version: str, rows: Sequence[ShadowTradeRow], pick: Callable[[ShadowTradeRow], Any]
) -> ChannelComparison:
    blocked = [r for r in rows if pick(r) is True]
    kept = [r for r in rows if pick(r) is False]
    return ChannelComparison(
        name=name,
        version=version,
        blocked=GroupStats.of(blocked),
        kept=GroupStats.of(kept),
        not_evaluated=sum(1 for r in rows if pick(r) is None),
    )


def shadow_report(
    annotations: Iterable[EventAnnotation],
    trades: Iterable[Any],
    *,
    policy: EventVetoPolicy = DEFAULT_EVENT_VETO_POLICY,
    bars: Mapping[str, pd.DataFrame] | None = None,
    calendar: EconomicCalendar | None = None,
    vol_filter: VolSpikeFilter = DEFAULT_VOL_FILTER,
    min_affected_trades: int = 80,
) -> ShadowReport:
    """Would-have-vetoed, per trade, against the two deterministic baselines.

    ``trades`` are :class:`~fiboki.core.contracts.Trade` rows (anything with
    ``trade_id``, ``instrument``, ``entry_time``, ``net_pnl`` and
    ``max_adverse_excursion``). The decision instant is ``entry_time``. The
    veto is evaluated exactly as the gateway would, point-in-time
    (``available_at <= entry_time``), whatever ``policy.enabled`` says: this is
    the counterfactual, not the live switch.

    Baselines: ``vol_spike`` needs ``bars[instrument]``; ``calendar_only``
    (a HIGH-impact scheduled event in ``[T, T + veto_window]`` for the
    instrument's currencies) needs a ``calendar`` whose declared span covers
    the entry. Otherwise the trade is NOT_EVALUATED for that baseline and
    counted as such, never as "kept".
    """
    pool = tuple(annotations)
    window_minutes = int(policy.veto_window.total_seconds() // 60)
    coverage = calendar.coverage() if calendar is not None else None
    rows: list[ShadowTradeRow] = []
    for trade in trades:
        at = _utc(trade.entry_time, "entry_time")
        veto = policy.veto_among(pool, trade.instrument, at)
        frame = (bars or {}).get(trade.instrument)
        spike = vol_filter.flags(frame, at) if frame is not None else None
        cal: bool | None = None
        if calendar is not None and coverage is not None and coverage.covers(at, at):
            cal = bool(
                calendar.events_near(
                    trade.instrument, at, minutes_before=0, minutes_after=window_minutes,
                    min_impact=ImpactLevel.HIGH,
                )
            )
        rows.append(
            ShadowTradeRow(
                trade_id=str(trade.trade_id),
                instrument=str(trade.instrument),
                entry_time=at,
                net_pnl=float(trade.net_pnl),
                adverse_excursion=abs(float(trade.max_adverse_excursion)),
                event_veto=None if veto is None else veto.code,
                vol_spike=spike,
                calendar_only=cal,
            )
        )
    channels = (
        _split("event_veto", policy.version, rows, lambda r: r.event_veto is not None),
        _split("vol_spike_filter", vol_filter.version, rows, lambda r: r.vol_spike),
        _split("calendar_only", "calendar_high_impact_v1", rows, lambda r: r.calendar_only),
        # The plan's first baseline: take every trade. Its ``kept`` group is
        # the whole book, so every other channel reads against it.
        _split("no_veto", "none", rows, lambda r: False),
    )
    caveats = (
        "Net expectancy is mean net P&L per trade in the account currency; adverse "
        "excursion is |MAE| in the account currency as the trade ledger records it.",
        "A blocked trade's P&L is what the entry DID make; the counterfactual assumes "
        "blocking it changes nothing else (no capital, correlation or lock effects).",
        f"Fewer than {min_affected_trades} would-be-vetoed trades is below the "
        "pre-registered floor: read nothing into the comparison (ShadowReport.sufficient).",
        "The calendar-only baseline sees only the currencies the official calendar "
        "covers; entries on other currencies are 'kept' by it, not evaluated as safe.",
        "Annotations produced before the model's training cutoff by a backfill are not "
        "evidence (plan D-A3); feed forward-recorded annotations only.",
    )
    return ShadowReport(
        policy=policy.stamp(),
        n_trades=len(rows),
        n_annotations=len(pool),
        rows=tuple(rows),
        channels=channels,
        min_affected_trades=min_affected_trades,
        caveats=caveats,
    )
