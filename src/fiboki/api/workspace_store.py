"""Operator workspace documents: named layouts, watchlists and grid column sets.

WHAT THIS IS
------------
Display state an operator chose by hand, kept on the server so it follows the
operator from one browser to the next: how the workspace screen is tiled, which
symbols a watchlist holds, which columns a grid shows. Each document is keyed by
``(owner, kind, name)``.

WHAT THIS IS NOT
----------------
Strategy, risk or execution input. Nothing under ``strategy/``, ``backtest/``,
``risk/``, ``portfolio/`` or ``broker/`` reads this store; ``tests/unit/
test_workspace_store_isolation.py`` parses the tree and keeps it that way. A
watchlist is a list of symbols to look at, not a trading universe.

STORAGE
-------
``<FIBOKI_STATE_DIR>/workspace/workspace.sqlite`` (``core.paths.workspace_db``),
WAL. ``document`` holds the current version of each document; a version only
ever increases, a delete is a tombstone version (so a stale tab cannot
resurrect a deleted layout by writing version 1 again). ``document_revision`` is
append-only (SQLite triggers refuse UPDATE and DELETE) and keeps every accepted
write with its actor and time: the audit trail of who changed a layout, when.
Every write is also recorded in the API audit trail by the route.

CONCURRENCY
-----------
Optimistic, as the drawing store: a write names the version it was based on
(``0`` creates). ``BEGIN IMMEDIATE`` serialises writers; a write based on
anything but the current version raises :class:`DocumentConflict` carrying the
current document, which the route turns into a 409. Two tabs cannot silently
overwrite each other.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "DOCUMENT_KINDS",
    "MAX_DOCUMENTS_PER_KIND",
    "NAME_PATTERN",
    "DocumentConflict",
    "Revision",
    "StoredDocument",
    "WorkspaceStore",
]

#: The document kinds this store holds. A new kind is a reviewed change.
DOCUMENT_KINDS: tuple[str, ...] = ("layout", "watchlist", "column_sets")
#: Per operator per kind. More than this is a bug or an abuse, not a workspace.
MAX_DOCUMENTS_PER_KIND = 100
#: Names an operator types: letters, digits, space, dash, underscore, dot.
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}$"
_NAME = re.compile(NAME_PATTERN)
#: A stored payload larger than this is refused (a layout is a few kB).
MAX_PAYLOAD_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class StoredDocument:
    owner: str
    kind: str
    name: str
    version: int
    updated_at: datetime | None
    updated_by: str
    deleted: bool
    payload: dict[str, Any] | None

    @property
    def exists(self) -> bool:
        return self.version > 0 and not self.deleted


@dataclass(frozen=True, slots=True)
class Revision:
    version: int
    action: str
    at: datetime
    actor: str
    payload: dict[str, Any] | None


class DocumentConflict(Exception):
    """A write named a version that is not the current one."""

    def __init__(self, current: StoredDocument, base_version: int) -> None:
        super().__init__(
            f"{current.kind} {current.name!r} is at version {current.version}, not {base_version}"
        )
        self.current = current
        self.base_version = base_version


_DDL = (
    """
    CREATE TABLE IF NOT EXISTS document (
        owner TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0,
        payload_json TEXT,
        PRIMARY KEY (owner, kind, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_revision (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        owner TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        version INTEGER NOT NULL,
        action TEXT NOT NULL,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        payload_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS document_revision_key ON document_revision (owner, kind, name, version)",
    """
    CREATE TRIGGER IF NOT EXISTS document_revision_no_update
    BEFORE UPDATE ON document_revision
    BEGIN SELECT RAISE(ABORT, 'document_revision is append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS document_revision_no_delete
    BEFORE DELETE ON document_revision
    BEGIN SELECT RAISE(ABORT, 'document_revision is append-only'); END
    """,
)


def _check(kind: str, name: str) -> None:
    if kind not in DOCUMENT_KINDS:
        raise ValueError(f"unknown document kind {kind!r}")
    if not _NAME.match(name):
        raise ValueError(
            "a name is 1 to 48 characters: letters, digits, space, '.', '-' or '_', "
            "starting with a letter or digit"
        )


def _encode(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"a workspace document is at most {MAX_PAYLOAD_BYTES // 1024} KiB")
    return text


class WorkspaceStore:
    """The workspace documents, one SQLite file. Opened per request; cheap."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if not write and not self.path.exists():
            # A read never creates the file: an absent store is "nothing saved
            # yet", served as empty, never as an error.
            conn = sqlite3.connect(":memory:")
            try:
                yield conn
            finally:
                conn.close()
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
    def _row(conn: sqlite3.Connection, owner: str, kind: str, name: str) -> StoredDocument:
        try:
            row = conn.execute(
                "SELECT version, updated_at, updated_by, deleted, payload_json FROM document "
                "WHERE owner = ? AND kind = ? AND name = ?",
                (owner, kind, name),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None  # the read-only in-memory stand-in has no table
        if row is None:
            return StoredDocument(owner, kind, name, 0, None, "", False, None)
        version, updated_at, updated_by, deleted, payload = row
        return StoredDocument(
            owner=owner,
            kind=kind,
            name=name,
            version=int(version),
            updated_at=datetime.fromisoformat(updated_at),
            updated_by=str(updated_by),
            deleted=bool(deleted),
            payload=None if payload is None else json.loads(payload),
        )

    # -- reads -------------------------------------------------------------

    def get(self, owner: str, kind: str, name: str) -> StoredDocument:
        _check(kind, name)
        with self._connect(write=False) as conn:
            return self._row(conn, owner, kind, name)

    def list(self, owner: str, kind: str) -> list[StoredDocument]:
        """Every live (not deleted) document of a kind, by name."""
        if kind not in DOCUMENT_KINDS:
            raise ValueError(f"unknown document kind {kind!r}")
        with self._connect(write=False) as conn:
            try:
                names = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM document WHERE owner = ? AND kind = ? AND deleted = 0 "
                        "ORDER BY name COLLATE NOCASE",
                        (owner, kind),
                    ).fetchall()
                ]
            except sqlite3.OperationalError:
                return []
            return [self._row(conn, owner, kind, n) for n in names]

    def revisions(self, owner: str, kind: str, name: str, *, limit: int = 50) -> list[Revision]:
        """Accepted writes, newest first."""
        _check(kind, name)
        with self._connect(write=False) as conn:
            try:
                rows = conn.execute(
                    "SELECT version, action, at, actor, payload_json FROM document_revision "
                    "WHERE owner = ? AND kind = ? AND name = ? ORDER BY seq DESC LIMIT ?",
                    (owner, kind, name, int(limit)),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            Revision(
                version=int(v),
                action=str(a),
                at=datetime.fromisoformat(at),
                actor=str(who),
                payload=None if p is None else json.loads(p),
            )
            for v, a, at, who, p in rows
        ]

    # -- writes ------------------------------------------------------------

    def put(
        self,
        owner: str,
        kind: str,
        name: str,
        *,
        base_version: int,
        payload: dict[str, Any] | None,
        actor: str,
        action: str = "put",
    ) -> StoredDocument:
        """Write a new version (``payload=None`` writes a tombstone).

        ``base_version`` must equal the current version: ``0`` for a document
        that has never existed, and the tombstone's version to re-create a
        deleted one. Anything else raises :class:`DocumentConflict`.
        """
        _check(kind, name)
        text = None if payload is None else _encode(payload)
        now = datetime.now(tz=UTC)
        with self._connect(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._row(conn, owner, kind, name)
                if current.version != base_version:
                    conn.execute("ROLLBACK")
                    raise DocumentConflict(current, base_version)
                if payload is None and not current.exists:
                    conn.execute("ROLLBACK")
                    raise LookupError(f"{kind} {name!r} does not exist")
                if payload is not None and not current.exists:
                    live = conn.execute(
                        "SELECT COUNT(*) FROM document WHERE owner = ? AND kind = ? AND deleted = 0",
                        (owner, kind),
                    ).fetchone()[0]
                    if live >= MAX_DOCUMENTS_PER_KIND:
                        conn.execute("ROLLBACK")
                        raise ValueError(f"at most {MAX_DOCUMENTS_PER_KIND} {kind} documents")
                version = current.version + 1
                deleted = 1 if payload is None else 0
                conn.execute(
                    "INSERT INTO document (owner, kind, name, version, updated_at, updated_by, "
                    "deleted, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (owner, kind, name) DO UPDATE SET version = excluded.version, "
                    "updated_at = excluded.updated_at, updated_by = excluded.updated_by, "
                    "deleted = excluded.deleted, payload_json = excluded.payload_json",
                    (owner, kind, name, version, now.isoformat(), actor, deleted, text),
                )
                conn.execute(
                    "INSERT INTO document_revision (owner, kind, name, version, action, at, "
                    "actor, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (owner, kind, name, version, action, now.isoformat(), actor, text),
                )
                conn.execute("COMMIT")
            except (DocumentConflict, LookupError, ValueError):
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return StoredDocument(
            owner, kind, name, version, now, actor, payload is None,
            None if text is None else json.loads(text),
        )
