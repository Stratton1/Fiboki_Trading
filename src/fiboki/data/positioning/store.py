"""Append-only positioning snapshots, and their point-in-time read.

One SQLite file (``<state_dir>/positioning/snapshots.sqlite``, WAL). The
pattern is the headline store's (``data/news/store.py``), re-stated here for
a different record:

* ``snapshot``: one row per observation of a positioning book or outlook.
  ``vendor_time`` is the instant the source says the snapshot describes (an
  OANDA book's ``time``), when it gives one; ``observed_at`` is OUR clock at
  the poll that first held it. **Availability is ``observed_at``.** A vendor
  may publish a book stamped 12:00 at 12:20; reading it at 12:00 in a
  backtest would be look-ahead, so :meth:`PositioningStore.as_of` filters on
  ``observed_at`` alone.
* Dedupe: a later poll that returns the same ``(source, instrument, kind,
  vendor_time, content)`` is a duplicate, not a row. A source with no vendor
  time (Myfxbook) gets one row per poll, because "at observed_at the outlook
  read X" is itself the fact being recorded.
* ``poll_log``: one row per poll with per-client outcomes, so capture gaps
  are visible.

Append-only in the database: triggers refuse ``UPDATE`` and ``DELETE``, and a
``BEFORE INSERT`` trigger refuses a colliding insert (``INSERT OR REPLACE``
deletes without firing delete triggers). There is no backfill path: history
before the first poll is not claimed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "APPEND_ONLY_MESSAGE",
    "PositioningSnapshot",
    "PositioningStore",
    "PositioningStoreError",
    "StoredSnapshot",
    "default_positioning_path",
]

APPEND_ONLY_MESSAGE = "positioning store is append-only"
SCHEMA_VERSION = 1


class PositioningStoreError(RuntimeError):
    pass


def default_positioning_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / "positioning" / "snapshots.sqlite"


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise PositioningStoreError(f"naive datetime {value!r}; every stored instant is UTC-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _from_text(text: str | None) -> datetime | None:
    if text is None:
        return None
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PositioningSnapshot:
    """What a client hands the store: everything except our clock."""

    source: str
    instrument: str
    kind: str
    vendor_time: datetime | None
    payload: Mapping[str, Any]

    @property
    def content_hash(self) -> str:
        body = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredSnapshot:
    id: int
    source: str
    instrument: str
    kind: str
    vendor_time: datetime | None
    observed_at: datetime
    payload: dict[str, Any]
    content_hash: str

    @property
    def available_at(self) -> datetime:
        return self.observed_at


_DDL = (
    """
    CREATE TABLE IF NOT EXISTS snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        instrument TEXT NOT NULL,
        kind TEXT NOT NULL,
        vendor_time TEXT,
        observed_at TEXT NOT NULL CHECK (length(observed_at) = 27),
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        dedupe_key TEXT NOT NULL UNIQUE
    )
    """,
    "CREATE INDEX IF NOT EXISTS snapshot_pit ON snapshot (source, instrument, kind, observed_at, id)",
    """
    CREATE TABLE IF NOT EXISTS poll_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        outcomes_json TEXT NOT NULL
    )
    """,
    "CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)

_COLLISION = {
    "snapshot": "dedupe_key = NEW.dedupe_key OR id = NEW.id",
    "poll_log": "id = NEW.id",
    "store_meta": "key = NEW.key",
}


def _triggers() -> list[str]:
    out = []
    for table, collision in _COLLISION.items():
        out += [
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}'); END",
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}'); END",
            f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {collision}) "
            f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}: row exists'); END",
        ]
    return out


class PositioningStore:
    """Append-only SQLite snapshot store with a point-in-time read."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        with self._tx():
            for ddl in (*_DDL, *_triggers()):
                self._conn.execute(ddl)
            row = self._conn.execute("SELECT value FROM store_meta WHERE key='schema_version'").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO store_meta (key, value) VALUES ('schema_version', ?)",
                                   (str(SCHEMA_VERSION),))
            elif int(row[0]) != SCHEMA_VERSION:
                raise PositioningStoreError(f"{self.path} has schema {row[0]}, code writes {SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PositioningStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def record(self, snapshots: Iterable[PositioningSnapshot], observed_at: datetime) -> dict[str, int]:
        """Append unseen snapshots stamped ``observed_at``; one transaction."""
        stamp = _utc_text(observed_at)
        counts = {"new": 0, "duplicate": 0}
        with self._tx() as conn:
            for s in snapshots:
                vt = None if s.vendor_time is None else _utc_text(s.vendor_time)
                ch = s.content_hash
                # With a vendor time, identity is the content at that time; without
                # one, each poll's reading is its own fact.
                basis = f"v:{vt}:{ch}" if vt is not None else f"o:{stamp}"
                key = hashlib.sha256(
                    f"{s.source}|{s.instrument}|{s.kind}|{basis}".encode()
                ).hexdigest()
                if conn.execute("SELECT 1 FROM snapshot WHERE dedupe_key = ?", (key,)).fetchone():
                    counts["duplicate"] += 1
                    continue
                conn.execute(
                    "INSERT INTO snapshot (source, instrument, kind, vendor_time, observed_at, "
                    "payload_json, content_hash, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (s.source, s.instrument, s.kind, vt, stamp,
                     json.dumps(s.payload, sort_keys=True, default=str), ch, key),
                )
                counts["new"] += 1
        return counts

    def log_poll(self, started_at: datetime, outcomes: Mapping[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute("INSERT INTO poll_log (started_at, outcomes_json) VALUES (?, ?)",
                         (_utc_text(started_at), json.dumps(outcomes, sort_keys=True, default=str)))

    def as_of(self, source: str, instrument: str, kind: str, when: datetime) -> StoredSnapshot | None:
        """The latest snapshot first observed at or before ``when``, or None.

        None means nothing had been observed by then; it is not a zero
        position. ``when`` is mandatory: there is no "latest" mode.
        """
        if when is None:
            raise PositioningStoreError("as_of needs an instant")
        row = self._conn.execute(
            "SELECT id, source, instrument, kind, vendor_time, observed_at, payload_json, "
            "content_hash FROM snapshot WHERE source = ? AND instrument = ? AND kind = ? "
            "AND observed_at <= ? ORDER BY observed_at DESC, id DESC LIMIT 1",
            (source, instrument, kind, _utc_text(when)),
        ).fetchone()
        return None if row is None else _row(row)

    def history(self, source: str, instrument: str, kind: str, *, since: datetime,
                as_of: datetime) -> tuple[StoredSnapshot, ...]:
        """Snapshots first observed in ``[since, as_of]``, oldest first."""
        if since > as_of:
            raise PositioningStoreError(f"since {since} is after as_of {as_of}")
        rows = self._conn.execute(
            "SELECT id, source, instrument, kind, vendor_time, observed_at, payload_json, "
            "content_hash FROM snapshot WHERE source = ? AND instrument = ? AND kind = ? "
            "AND observed_at >= ? AND observed_at <= ? ORDER BY observed_at, id",
            (source, instrument, kind, _utc_text(since), _utc_text(as_of)),
        ).fetchall()
        return tuple(_row(r) for r in rows)

    def status(self) -> dict[str, Any]:
        conn = self._conn
        per = {
            f"{s}/{k}": {"rows": n, "instruments": i, "first_observed_at": a, "last_observed_at": b}
            for s, k, n, i, a, b in conn.execute(
                "SELECT source, kind, COUNT(*), COUNT(DISTINCT instrument), MIN(observed_at), "
                "MAX(observed_at) FROM snapshot GROUP BY source, kind ORDER BY source, kind")
        }
        polls = conn.execute("SELECT COUNT(*), MAX(started_at) FROM poll_log").fetchone()
        return {"per_source": per, "polls": polls[0], "last_poll": polls[1]}


def _row(r: tuple[Any, ...]) -> StoredSnapshot:
    observed = _from_text(r[5])
    assert observed is not None
    return StoredSnapshot(id=int(r[0]), source=r[1], instrument=r[2], kind=r[3],
                          vendor_time=_from_text(r[4]), observed_at=observed,
                          payload=json.loads(r[6]), content_hash=r[7])
