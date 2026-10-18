"""Operator chart drawings: annotations on the price chart, per operator and symbol.

WHAT THIS IS
------------
A drawing is pure geometry an operator placed by hand: a trend line, a ray, a
horizontal or vertical line, a rectangle, a Fibonacci retracement between two
points, a text note. Every point is anchored in ``(time UTC, price)``, never
in pixels or bar indices, so a drawing survives zoom, scroll and a timeframe
change. A Fibonacci drawing stores its two anchors only; the retracement
levels are drawn from the standard ratios by the chart, as a drawing.

WHAT THIS IS NOT
----------------
Strategy input. Nothing under ``strategy/``, ``backtest/``, ``risk/``,
``portfolio/`` or ``broker/`` reads this store, and ``tests/unit`` holds an
AST test that keeps it that way. A drawn level is an operator's note, not a
signal, a stop or a target.

STORAGE
-------
``<FIBOKI_STATE_DIR>/chart/drawings.sqlite`` (``core.paths.drawings_db``),
WAL. ``drawing_set`` holds the current set per ``(owner, symbol)`` with a
version that only ever increases; ``drawing_revision`` is append-only (SQLite
triggers refuse UPDATE and DELETE, as the experiment ledger's do) and keeps
every accepted write, so an annotation that was there yesterday can be shown
to have been there. Every write is also recorded in the API audit trail by the
route.

CONCURRENCY
-----------
Optimistic: a write names the version it was based on. ``BEGIN IMMEDIATE``
serialises writers; a write based on anything but the current version is
refused with the current set, so two tabs (or Joe and an old tab of Joe's)
cannot silently overwrite each other.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "DRAWING_KINDS",
    "MAX_DRAWINGS",
    "POINTS_PER_KIND",
    "Drawing",
    "DrawingConflict",
    "DrawingPoint",
    "DrawingStore",
    "StoredSet",
]

DrawingKind = Literal["trend", "ray", "hline", "vline", "rect", "fib", "text"]
DRAWING_KINDS: tuple[str, ...] = ("trend", "ray", "hline", "vline", "rect", "fib", "text")
#: How many anchors each kind has. The measure tool is display-only and is
#: never stored.
POINTS_PER_KIND: dict[str, int] = {
    "trend": 2,
    "ray": 2,
    "rect": 2,
    "fib": 2,
    "hline": 1,
    "vline": 1,
    "text": 1,
}
#: Per operator per symbol. A set this large is a bug or an abuse, not analysis.
MAX_DRAWINGS = 500
MAX_TEXT = 280

#: The schema revision of the stored payload. Bumped when the shape changes.
PAYLOAD_SCHEMA = "fiboki.chart.drawings/1"


class DrawingPoint(BaseModel):
    """One anchor: a UTC instant and a price. Finite, timezone-aware."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    t: datetime
    price: float = Field(allow_inf_nan=False)

    @field_validator("t")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("t must carry a timezone (UTC); a naive time is ambiguous")
        return value.astimezone(UTC)

    @field_validator("price")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("price must be finite")
        return float(value)


class Drawing(BaseModel):
    """One operator annotation. Geometry and a label, nothing executable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: DrawingKind
    points: list[DrawingPoint] = Field(min_length=1, max_length=2)
    #: ``None``: shown on every timeframe; otherwise only on that timeframe.
    timeframe: str | None = Field(default=None, pattern=r"^(M1|M5|M15|M30|H1|H4|D1)$")
    text: str = Field(default="", max_length=MAX_TEXT)
    locked: bool = False
    hidden: bool = False
    #: Fibonacci only: also draw the 1.272 and 1.618 extensions.
    fib_extensions: bool = False

    @model_validator(mode="after")
    def _shape(self) -> Drawing:
        want = POINTS_PER_KIND[self.kind]
        if len(self.points) != want:
            raise ValueError(f"a {self.kind} drawing has {want} point(s), not {len(self.points)}")
        if self.kind == "text" and not self.text.strip():
            raise ValueError("a text note needs text")
        if self.fib_extensions and self.kind != "fib":
            raise ValueError("fib_extensions applies to a Fibonacci drawing only")
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in self.text):
            raise ValueError("text may not contain control characters")
        return self


@dataclass(frozen=True, slots=True)
class StoredSet:
    owner: str
    symbol: str
    version: int
    updated_at: datetime | None
    updated_by: str
    drawings: tuple[Drawing, ...]


class DrawingConflict(Exception):
    """A write named a version that is not the current one."""

    def __init__(self, current: StoredSet, base_version: int) -> None:
        super().__init__(
            f"drawings for {current.symbol} are at version {current.version}, "
            f"not {base_version}"
        )
        self.current = current
        self.base_version = base_version


_DDL = (
    """
    CREATE TABLE IF NOT EXISTS drawing_set (
        owner TEXT NOT NULL,
        symbol TEXT NOT NULL,
        version INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (owner, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS drawing_revision (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        owner TEXT NOT NULL,
        symbol TEXT NOT NULL,
        version INTEGER NOT NULL,
        action TEXT NOT NULL,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS drawing_revision_key ON drawing_revision (owner, symbol, version)",
    """
    CREATE TRIGGER IF NOT EXISTS drawing_revision_no_update
    BEFORE UPDATE ON drawing_revision
    BEGIN SELECT RAISE(ABORT, 'drawing_revision is append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS drawing_revision_no_delete
    BEFORE DELETE ON drawing_revision
    BEGIN SELECT RAISE(ABORT, 'drawing_revision is append-only'); END
    """,
)


def _payload(drawings: tuple[Drawing, ...] | list[Drawing]) -> str:
    return json.dumps(
        {
            "schema": PAYLOAD_SCHEMA,
            "drawings": [d.model_dump(mode="json") for d in drawings],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse(payload: str) -> tuple[Drawing, ...]:
    raw = json.loads(payload)
    if not isinstance(raw, dict) or raw.get("schema") != PAYLOAD_SCHEMA:
        raise ValueError("stored drawing payload has an unknown schema")
    return tuple(Drawing.model_validate(d) for d in raw.get("drawings") or [])


class DrawingStore:
    """The drawing sets, one SQLite file. Opened per request; cheap."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if not write and not self.path.exists():
            # A read must not create the file: an absent store is "nobody has
            # drawn yet", served as an empty set at version 0.
            yield sqlite3.connect(":memory:")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            for ddl in _DDL:
                conn.execute(ddl)
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row(conn: sqlite3.Connection, owner: str, symbol: str) -> StoredSet:
        try:
            row = conn.execute(
                "SELECT version, updated_at, updated_by, payload_json FROM drawing_set "
                "WHERE owner = ? AND symbol = ?",
                (owner, symbol),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None  # the read-only in-memory stand-in has no table
        if row is None:
            return StoredSet(owner, symbol, 0, None, "", ())
        version, updated_at, updated_by, payload = row
        return StoredSet(
            owner=owner,
            symbol=symbol,
            version=int(version),
            updated_at=datetime.fromisoformat(updated_at),
            updated_by=str(updated_by),
            drawings=_parse(payload),
        )

    def get(self, owner: str, symbol: str) -> StoredSet:
        with self._connect(write=False) as conn:
            return self._row(conn, owner, symbol)

    def others(self, owner: str, symbol: str) -> list[StoredSet]:
        """Every other operator's non-empty set for this symbol, by owner."""
        with self._connect(write=False) as conn:
            try:
                owners = [
                    r[0]
                    for r in conn.execute(
                        "SELECT owner FROM drawing_set WHERE symbol = ? AND owner != ? "
                        "ORDER BY owner",
                        (symbol, owner),
                    ).fetchall()
                ]
            except sqlite3.OperationalError:
                return []
            sets = [self._row(conn, o, symbol) for o in owners]
        return [s for s in sets if s.drawings]

    def put(
        self,
        owner: str,
        symbol: str,
        *,
        base_version: int,
        drawings: list[Drawing],
        actor: str,
        action: str = "put",
    ) -> StoredSet:
        ids = [d.id for d in drawings]
        if len(set(ids)) != len(ids):
            raise ValueError("drawing ids must be unique within a set")
        if len(drawings) > MAX_DRAWINGS:
            raise ValueError(f"at most {MAX_DRAWINGS} drawings per symbol")
        now = datetime.now(tz=UTC)
        payload = _payload(drawings)
        with self._connect(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._row(conn, owner, symbol)
                if current.version != base_version:
                    conn.execute("ROLLBACK")
                    raise DrawingConflict(current, base_version)
                version = current.version + 1
                conn.execute(
                    "INSERT INTO drawing_set (owner, symbol, version, updated_at, updated_by, "
                    "payload_json) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (owner, symbol) DO "
                    "UPDATE SET version = excluded.version, updated_at = excluded.updated_at, "
                    "updated_by = excluded.updated_by, payload_json = excluded.payload_json",
                    (owner, symbol, version, now.isoformat(), actor, payload),
                )
                conn.execute(
                    "INSERT INTO drawing_revision (owner, symbol, version, action, at, actor, "
                    "payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (owner, symbol, version, action, now.isoformat(), actor, payload),
                )
                conn.execute("COMMIT")
            except DrawingConflict:
                raise
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return StoredSet(owner, symbol, version, now, actor, tuple(drawings))

    def revisions(self, owner: str, symbol: str) -> list[tuple[int, str, str]]:
        """``(version, action, actor)`` for every accepted write, oldest first."""
        with self._connect(write=False) as conn:
            try:
                rows = conn.execute(
                    "SELECT version, action, actor FROM drawing_revision "
                    "WHERE owner = ? AND symbol = ? ORDER BY seq",
                    (owner, symbol),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [(int(v), str(a), str(who)) for v, a, who in rows]
