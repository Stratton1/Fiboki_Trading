"""The structured trade journal: an operator's notes on a closed trade (Wave 3O).

TradeZella / Edgewonk-style: per trade, a free-text note, tags, a setup grade
(A, B or C), a list of mistakes, a screenshot link and, when the operator acted
by hand, the reason for the intervention. Annotations only: nothing here is
read by a strategy, the risk gateway, the paper runtime or a research verdict.

Storage: one SQLite file (``<ops_dir>/journal_notes.sqlite``, WAL, a busy
timeout, ``synchronous=FULL``). ``trade_note`` holds the current note per
trade with a ``version``; ``trade_note_history`` keeps every version written,
so an edit never destroys what was said before. A write carries the version it
was based on and is refused when another write landed in between (two
operators, two tabs), so a note is never silently overwritten.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fiboki.core.durable import configure_sqlite_connection

__all__ = [
    "GRADES",
    "JournalNoteStore",
    "NoteConflict",
    "NoteInvalid",
    "TradeNote",
]

GRADES = ("A", "B", "C")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_TAGS = 12
NOTE_MAX = 4000
REASON_MAX = 500
URL_MAX = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_note (
    trade_id TEXT PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    grade TEXT CHECK (grade IS NULL OR grade IN ('A','B','C')),
    mistakes_json TEXT NOT NULL DEFAULT '[]',
    screenshot_url TEXT,
    intervention_reason TEXT,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_note_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    body_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trade_note_history_trade ON trade_note_history (trade_id, version);
"""


class NoteInvalid(ValueError):
    """A field is out of bounds or malformed."""


class NoteConflict(ValueError):
    """The note changed since the version the write was based on."""

    def __init__(self, message: str, *, current: int) -> None:
        super().__init__(message)
        self.current = current


@dataclass(frozen=True, slots=True)
class TradeNote:
    trade_id: str
    note: str = ""
    tags: tuple[str, ...] = ()
    grade: str | None = None
    mistakes: tuple[str, ...] = ()
    screenshot_url: str | None = None
    intervention_reason: str | None = None
    updated_by: str = ""
    updated_at: str = ""
    version: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("extra", None)
        out["tags"] = list(self.tags)
        out["mistakes"] = list(self.mistakes)
        return out


def _slugs(values: Sequence[str] | None, what: str) -> tuple[str, ...]:
    out: list[str] = []
    for raw in values or ():
        slug = str(raw).strip().lower().replace(" ", "-")
        if not slug:
            continue
        if not _SLUG.match(slug):
            raise NoteInvalid(f"{what} {raw!r}: lower-case letters, digits, '-' or '_', at most 32 characters")
        if slug not in out:
            out.append(slug)
    if len(out) > MAX_TAGS:
        raise NoteInvalid(f"at most {MAX_TAGS} {what}s per trade")
    return tuple(out)


def _url(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if len(text) > URL_MAX:
        raise NoteInvalid(f"the screenshot link is longer than {URL_MAX} characters")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise NoteInvalid("the screenshot link must be an http(s) URL with a host")
    return text


def validate(
    trade_id: str,
    *,
    note: str = "",
    tags: Sequence[str] | None = None,
    grade: str | None = None,
    mistakes: Sequence[str] | None = None,
    screenshot_url: str | None = None,
    intervention_reason: str | None = None,
) -> TradeNote:
    if not trade_id or len(trade_id) > 200:
        raise NoteInvalid("a note must name the trade it annotates")
    text = (note or "").strip()
    if len(text) > NOTE_MAX:
        raise NoteInvalid(f"the note is longer than {NOTE_MAX} characters")
    g = (grade or "").strip().upper() or None
    if g is not None and g not in GRADES:
        raise NoteInvalid(f"the setup grade is one of {', '.join(GRADES)} (or none)")
    reason = (intervention_reason or "").strip() or None
    if reason is not None and len(reason) > REASON_MAX:
        raise NoteInvalid(f"the intervention reason is longer than {REASON_MAX} characters")
    return TradeNote(
        trade_id=trade_id, note=text, tags=_slugs(tags, "tag"), grade=g,
        mistakes=_slugs(mistakes, "mistake"), screenshot_url=_url(screenshot_url),
        intervention_reason=reason,
    )


class JournalNoteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            configure_sqlite_connection(conn)
            conn.executescript(_SCHEMA)
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row(r: Sequence[Any]) -> TradeNote:
        return TradeNote(
            trade_id=r[0], note=r[1], tags=tuple(json.loads(r[2])), grade=r[3], mistakes=tuple(json.loads(r[4])),
            screenshot_url=r[5], intervention_reason=r[6], updated_by=r[7], updated_at=r[8], version=int(r[9]),
        )

    _COLS = ("trade_id, note, tags_json, grade, mistakes_json, screenshot_url, intervention_reason, "
             "updated_by, updated_at, version")

    def get(self, trade_id: str) -> TradeNote | None:
        if not self.path.exists():
            return None
        with self._conn() as c:
            r = c.execute(f"SELECT {self._COLS} FROM trade_note WHERE trade_id = ?", (trade_id,)).fetchone()
        return None if r is None else self._row(r)

    def all(self) -> list[TradeNote]:
        if not self.path.exists():
            return []
        with self._conn() as c:
            rows = c.execute(f"SELECT {self._COLS} FROM trade_note ORDER BY updated_at DESC, trade_id").fetchall()
        return [self._row(r) for r in rows]

    def history(self, trade_id: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._conn() as c:
            rows = c.execute(
                "SELECT version, body_json, actor, at FROM trade_note_history WHERE trade_id = ? ORDER BY version",
                (trade_id,),
            ).fetchall()
        return [{"version": v, "body": json.loads(b), "actor": a, "at": t} for v, b, a, t in rows]

    def put(self, note: TradeNote, *, actor: str, expected_version: int) -> TradeNote:
        """Write ``note`` if the stored version is ``expected_version`` (0 = no note yet)."""
        if not actor:
            raise NoteInvalid("a note must name its author")
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                cur = c.execute("SELECT version FROM trade_note WHERE trade_id = ?", (note.trade_id,)).fetchone()
                current = int(cur[0]) if cur else 0
                if current != int(expected_version):
                    raise NoteConflict(
                        f"the note on {note.trade_id} is at version {current}; this edit was based on "
                        f"{expected_version}. Reload it before saving, so nothing is overwritten unseen.",
                        current=current,
                    )
                stamp = datetime.now(tz=UTC).isoformat()
                saved = TradeNote(**{**asdict(note), "updated_by": actor, "updated_at": stamp, "version": current + 1})
                c.execute(
                    "INSERT INTO trade_note (trade_id, note, tags_json, grade, mistakes_json, screenshot_url, "
                    "intervention_reason, updated_by, updated_at, version) VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(trade_id) DO UPDATE SET note=excluded.note, tags_json=excluded.tags_json, "
                    "grade=excluded.grade, mistakes_json=excluded.mistakes_json, "
                    "screenshot_url=excluded.screenshot_url, intervention_reason=excluded.intervention_reason, "
                    "updated_by=excluded.updated_by, updated_at=excluded.updated_at, version=excluded.version",
                    (saved.trade_id, saved.note, json.dumps(list(saved.tags)), saved.grade,
                     json.dumps(list(saved.mistakes)), saved.screenshot_url, saved.intervention_reason,
                     saved.updated_by, saved.updated_at, saved.version),
                )
                c.execute(
                    "INSERT INTO trade_note_history (trade_id, version, body_json, actor, at) VALUES (?,?,?,?,?)",
                    (saved.trade_id, saved.version, json.dumps(saved.as_dict(), sort_keys=True), actor, stamp),
                )
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return saved

    def matching(
        self,
        *,
        tag: str | None = None,
        grade: str | None = None,
        mistake: str | None = None,
        intervened: bool | None = None,
    ) -> list[TradeNote]:
        """Notes matching every given filter (server-side; the workstation does not filter)."""
        out = []
        for n in self.all():
            if tag and tag.lower() not in n.tags:
                continue
            if grade and (n.grade or "") != grade.upper():
                continue
            if mistake and mistake.lower() not in n.mistakes:
                continue
            if intervened is not None and bool(n.intervention_reason) != intervened:
                continue
            out.append(n)
        return out

    def vocabulary(self) -> dict[str, dict[str, int]]:
        tags: dict[str, int] = {}
        mistakes: dict[str, int] = {}
        grades: dict[str, int] = {}
        for n in self.all():
            for t in n.tags:
                tags[t] = tags.get(t, 0) + 1
            for m in n.mistakes:
                mistakes[m] = mistakes.get(m, 0) + 1
            if n.grade:
                grades[n.grade] = grades.get(n.grade, 0) + 1
        return {"tags": dict(sorted(tags.items())), "mistakes": dict(sorted(mistakes.items())),
                "grades": dict(sorted(grades.items()))}
