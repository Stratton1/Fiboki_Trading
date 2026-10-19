"""Price and level alerts over RECORDED quotes: rules, evaluation, notifications.

WHAT THIS IS
------------
An operator's note to themselves: "tell me when EURUSD trades above 1.0900",
"when GBPUSD crosses 1.2700". A rule names an instrument, a level, a side of
the book (mid, bid or ask) and a kind:

* ``above``  fires when the price is AT OR ABOVE the level and was not (or had
  not been seen) the last time; a rule created while the price is already
  above fires on the first quote it sees, which is what "alert me when it is
  above" means.
* ``below``  fires when the price is strictly BELOW the level and was not.
* ``cross``  fires when the price moves from one side of the level to the
  other between two observed quotes; it needs one quote to know a side and
  never fires on its first.

``mode="once"`` disarms the rule after it fires; ``"rearm"`` keeps it armed, so
``above``/``below`` fire again after the price has gone back. Editing a rule
re-arms it and forgets the side it last saw.

WHAT THIS IS NOT
----------------
An order, a stop or a signal. Nothing here imports ``broker``, ``risk``,
``portfolio`` or ``strategy`` (``tests/unit/test_workspace_store_isolation.py``
parses the module), and nothing in those packages imports this one. A fired
alert writes a NOTIFICATION row for a human; that is all it can do.

EVALUATION
----------
:meth:`PriceAlertStore.evaluate` is pure with respect to its inputs: the same
quotes in the same order give the same notifications. It only considers quotes
newer than the rule's creation and newer than the last quote it already
evaluated for that rule, so feeding it the same quotes twice (a restart, a
catch-up run over the log) never fires twice. It runs inside one
``BEGIN IMMEDIATE`` transaction, so an edit from the API and an evaluation
cannot interleave. The evaluator is a separate process: the quote recorder
calls it after each recorded quote (``fiboki quotes record``), and
``fiboki alerts price-evaluate`` catches up from the log. The API never runs it.

What the recorder does not see, this cannot alert on: moves between polls
(default 30 s) are invisible, and a crossing inside one poll is detected only
if the polled price ended on the other side.

STORAGE
-------
``<FIBOKI_STATE_DIR>/alerts/price_alerts.sqlite`` (``core.paths.price_alerts_db``),
WAL. ``rule_revision`` and ``notification`` are append-only (SQLite triggers
refuse UPDATE and DELETE); the per-operator read marker is the highest
notification sequence the operator has marked read.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fiboki.data.recorder import QuoteRecord

__all__ = [
    "MAX_RULES",
    "AlertConflict",
    "EvaluatorState",
    "Notification",
    "PriceAlertStore",
    "PriceRule",
    "RuleSpec",
]

Kind = Literal["above", "below", "cross"]
Side = Literal["mid", "bid", "ask"]
Mode = Literal["once", "rearm"]
KINDS: tuple[str, ...] = ("above", "below", "cross")
SIDES: tuple[str, ...] = ("mid", "bid", "ask")
MODES: tuple[str, ...] = ("once", "rearm")
#: Per operator. More is a bug or an abuse, not monitoring.
MAX_RULES = 200
MAX_NOTE = 200


@dataclass(frozen=True, slots=True)
class RuleSpec:
    instrument: str
    kind: Kind
    level: float
    side: Side = "mid"
    mode: Mode = "once"
    note: str = ""
    enabled: bool = True

    def checked(self) -> RuleSpec:
        if self.kind not in KINDS:
            raise ValueError(f"kind is one of {', '.join(KINDS)}")
        if self.side not in SIDES:
            raise ValueError(f"side is one of {', '.join(SIDES)}")
        if self.mode not in MODES:
            raise ValueError(f"mode is one of {', '.join(MODES)}")
        if not (self.level > 0) or self.level != self.level or self.level == float("inf"):
            raise ValueError("level must be a positive, finite price")
        if len(self.note) > MAX_NOTE or any(ord(c) < 32 for c in self.note):
            raise ValueError(f"a note is at most {MAX_NOTE} printable characters")
        return replace(self, instrument=self.instrument.strip().upper(), level=float(self.level))


@dataclass(frozen=True, slots=True)
class PriceRule:
    id: str
    owner: str
    spec: RuleSpec
    version: int
    created_at: datetime
    updated_at: datetime
    armed: bool
    last_side: str | None
    last_price: float | None
    last_quote_at: datetime | None
    fired_count: int
    last_fired_at: datetime | None


@dataclass(frozen=True, slots=True)
class Notification:
    seq: int
    owner: str
    rule_id: str
    instrument: str
    kind: str
    side: str
    level: float
    price: float
    quote_time: datetime
    fired_at: datetime
    message: str


@dataclass(frozen=True, slots=True)
class EvaluatorState:
    at: datetime | None
    source: str
    quotes_seen: int
    rules_evaluated: int
    fired: int
    error: str


class AlertConflict(Exception):
    def __init__(self, current: PriceRule | None, base_version: int) -> None:
        super().__init__(
            f"rule is at version {current.version if current else 0}, not {base_version}"
        )
        self.current = current
        self.base_version = base_version


_DDL = (
    """
    CREATE TABLE IF NOT EXISTS rule (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        instrument TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('above', 'below', 'cross')),
        side TEXT NOT NULL CHECK (side IN ('mid', 'bid', 'ask')),
        level REAL NOT NULL CHECK (level > 0),
        mode TEXT NOT NULL CHECK (mode IN ('once', 'rearm')),
        note TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        armed INTEGER NOT NULL DEFAULT 1,
        last_side TEXT,
        last_price REAL,
        last_quote_at TEXT,
        fired_count INTEGER NOT NULL DEFAULT 0,
        last_fired_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS rule_owner ON rule (owner, deleted)",
    """
    CREATE TABLE IF NOT EXISTS rule_revision (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id TEXT NOT NULL,
        owner TEXT NOT NULL,
        version INTEGER NOT NULL,
        action TEXT NOT NULL,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        spec_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notification (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        owner TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        instrument TEXT NOT NULL,
        kind TEXT NOT NULL,
        side TEXT NOT NULL,
        level REAL NOT NULL,
        price REAL NOT NULL,
        quote_time TEXT NOT NULL,
        fired_at TEXT NOT NULL,
        message TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS notification_owner ON notification (owner, seq)",
    """
    CREATE TABLE IF NOT EXISTS read_marker (
        owner TEXT PRIMARY KEY,
        through_seq INTEGER NOT NULL,
        at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evaluator_state (
        key TEXT PRIMARY KEY,
        at TEXT NOT NULL,
        source TEXT NOT NULL,
        quotes_seen INTEGER NOT NULL,
        rules_evaluated INTEGER NOT NULL,
        fired INTEGER NOT NULL,
        error TEXT NOT NULL DEFAULT ''
    )
    """,
    *(
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_{verb}
        BEFORE {verb.upper()} ON {table}
        BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END
        """
        for table in ("rule_revision", "notification")
        for verb in ("update", "delete")
    ),
)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("every stored instant is timezone-aware UTC")
    return value.astimezone(UTC).isoformat()


def _dt(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def _price(q: QuoteRecord, side: str) -> float:
    return {"mid": q.mid, "bid": q.bid, "ask": q.ask}[side]


def _side(price: float, level: float) -> str:
    return "above" if price >= level else "below"


def _fmt(value: float) -> str:
    return f"{value:.6g}"


class PriceAlertStore:
    """Rules, notifications and the read marker, one SQLite file. Opened per call; cheap."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if not write and not self.path.exists():
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
    def _rule(row: tuple) -> PriceRule:
        (rid, owner, instrument, kind, side, level, mode, note, enabled, _deleted, version, created,
         updated, armed, last_side, last_price, last_quote_at, fired_count, last_fired_at) = row
        return PriceRule(
            id=rid, owner=owner,
            spec=RuleSpec(instrument=instrument, kind=kind, level=float(level), side=side, mode=mode,
                          note=note, enabled=bool(enabled)),
            version=int(version), created_at=datetime.fromisoformat(created),
            updated_at=datetime.fromisoformat(updated), armed=bool(armed), last_side=last_side,
            last_price=None if last_price is None else float(last_price), last_quote_at=_dt(last_quote_at),
            fired_count=int(fired_count), last_fired_at=_dt(last_fired_at),
        )

    _COLS = ("id, owner, instrument, kind, side, level, mode, note, enabled, deleted, version, created_at, "
             "updated_at, armed, last_side, last_price, last_quote_at, fired_count, last_fired_at")

    def _get(self, conn: sqlite3.Connection, owner: str, rule_id: str) -> PriceRule | None:
        try:
            row = conn.execute(
                f"SELECT {self._COLS} FROM rule WHERE id = ? AND owner = ? AND deleted = 0", (rule_id, owner)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return None if row is None else self._rule(row)

    # -- rules ------------------------------------------------------------

    def rules(self, owner: str) -> list[PriceRule]:
        with self._connect(write=False) as conn:
            try:
                rows = conn.execute(
                    f"SELECT {self._COLS} FROM rule WHERE owner = ? AND deleted = 0 ORDER BY instrument, created_at, id",
                    (owner,),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [self._rule(r) for r in rows]

    def get(self, owner: str, rule_id: str) -> PriceRule | None:
        with self._connect(write=False) as conn:
            return self._get(conn, owner, rule_id)

    def _revision(self, conn: sqlite3.Connection, rule: PriceRule, action: str, actor: str, at: datetime) -> None:
        spec = rule.spec
        conn.execute(
            "INSERT INTO rule_revision (rule_id, owner, version, action, at, actor, spec_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rule.id, rule.owner, rule.version, action, _iso(at), actor,
             json.dumps({"instrument": spec.instrument, "kind": spec.kind, "side": spec.side, "level": spec.level,
                         "mode": spec.mode, "note": spec.note, "enabled": spec.enabled}, sort_keys=True)),
        )

    def create(self, owner: str, spec: RuleSpec, *, actor: str, now: datetime | None = None) -> PriceRule:
        spec = spec.checked()
        at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        rid = "pa_" + secrets.token_hex(6)
        with self._connect(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                live = conn.execute("SELECT COUNT(*) FROM rule WHERE owner = ? AND deleted = 0", (owner,)).fetchone()[0]
                if live >= MAX_RULES:
                    raise ValueError(f"at most {MAX_RULES} alert rules per operator")
                conn.execute(
                    "INSERT INTO rule (id, owner, instrument, kind, side, level, mode, note, enabled, version, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (rid, owner, spec.instrument, spec.kind, spec.side, spec.level, spec.mode, spec.note,
                     int(spec.enabled), _iso(at), _iso(at)),
                )
                rule = self._get(conn, owner, rid)
                assert rule is not None
                self._revision(conn, rule, "create", actor, at)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return rule

    def update(self, owner: str, rule_id: str, spec: RuleSpec, *, base_version: int, actor: str,
               now: datetime | None = None) -> PriceRule:
        """Replace a rule's spec. Re-arms it and forgets the side it last saw. 409 on stale."""
        spec = spec.checked()
        at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        with self._connect(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._get(conn, owner, rule_id)
                if current is None or current.version != base_version:
                    raise AlertConflict(current, base_version)
                conn.execute(
                    "UPDATE rule SET instrument = ?, kind = ?, side = ?, level = ?, mode = ?, note = ?, enabled = ?, "
                    "version = version + 1, updated_at = ?, armed = 1, last_side = NULL, last_price = NULL "
                    "WHERE id = ? AND owner = ?",
                    (spec.instrument, spec.kind, spec.side, spec.level, spec.mode, spec.note, int(spec.enabled),
                     _iso(at), rule_id, owner),
                )
                rule = self._get(conn, owner, rule_id)
                assert rule is not None
                self._revision(conn, rule, "update", actor, at)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return rule

    def delete(self, owner: str, rule_id: str, *, base_version: int, actor: str, now: datetime | None = None) -> None:
        at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        with self._connect(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._get(conn, owner, rule_id)
                if current is None or current.version != base_version:
                    raise AlertConflict(current, base_version)
                conn.execute("UPDATE rule SET deleted = 1, version = version + 1, updated_at = ? WHERE id = ?",
                             (_iso(at), rule_id))
                self._revision(conn, replace(current, version=current.version + 1), "delete", actor, at)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    # -- evaluation ---------------------------------------------------------

    def evaluate(self, quotes: Iterable[QuoteRecord], *, now: datetime | None = None,
                 source: str = "evaluator") -> list[Notification]:
        """Evaluate every armed, enabled rule against these quotes; write and return what fired."""
        fired_at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        by: dict[str, list[QuoteRecord]] = {}
        seen = 0
        for q in quotes:
            by.setdefault(q.instrument.upper(), []).append(q)
            seen += 1
        for rows in by.values():
            rows.sort(key=lambda r: (r.timestamp, r.sequence))
        out: list[Notification] = []
        evaluated = 0
        with self._connect(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rules = [self._rule(r) for r in conn.execute(
                    f"SELECT {self._COLS} FROM rule WHERE deleted = 0 AND enabled = 1 AND armed = 1 "
                    "ORDER BY created_at, id"
                ).fetchall()] if by else []
                for rule in rules:
                    quotes_for = by.get(rule.spec.instrument)
                    if not quotes_for:
                        continue
                    evaluated += 1
                    armed, last_side, last_price, last_at = True, rule.last_side, rule.last_price, rule.last_quote_at
                    fired_count, last_fired = rule.fired_count, rule.last_fired_at
                    for q in quotes_for:
                        if q.timestamp < rule.created_at or (last_at is not None and q.timestamp <= last_at):
                            continue
                        price = _price(q, rule.spec.side)
                        side = _side(price, rule.spec.level)
                        kind = rule.spec.kind
                        fire = (
                            (kind == "above" and side == "above" and last_side != "above")
                            or (kind == "below" and side == "below" and last_side != "below")
                            or (kind == "cross" and last_side is not None and side != last_side)
                        )
                        last_side, last_price, last_at = side, price, q.timestamp
                        if not fire:
                            continue
                        verb = {"above": "at or above", "below": "below", "cross": f"crossed to {side}"}[kind]
                        message = (f"{rule.spec.instrument} {rule.spec.side} {_fmt(price)} {verb} "
                                   f"{_fmt(rule.spec.level)}" + (f" — {rule.spec.note}" if rule.spec.note else ""))
                        cur = conn.execute(
                            "INSERT INTO notification (owner, rule_id, instrument, kind, side, level, price, quote_time, "
                            "fired_at, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (rule.owner, rule.id, rule.spec.instrument, kind, rule.spec.side, rule.spec.level, price,
                             _iso(q.timestamp), _iso(fired_at), message),
                        )
                        out.append(Notification(int(cur.lastrowid or 0), rule.owner, rule.id, rule.spec.instrument,
                                                kind, rule.spec.side, rule.spec.level, price, q.timestamp, fired_at,
                                                message))
                        fired_count += 1
                        last_fired = fired_at
                        if rule.spec.mode == "once":
                            armed = False
                            break
                    conn.execute(
                        "UPDATE rule SET armed = ?, last_side = ?, last_price = ?, last_quote_at = ?, fired_count = ?, "
                        "last_fired_at = ? WHERE id = ? AND version = ?",
                        (int(armed), last_side, last_price, None if last_at is None else _iso(last_at), fired_count,
                         None if last_fired is None else _iso(last_fired), rule.id, rule.version),
                    )
                conn.execute(
                    "INSERT INTO evaluator_state (key, at, source, quotes_seen, rules_evaluated, fired, error) "
                    "VALUES ('last', ?, ?, ?, ?, ?, '') ON CONFLICT (key) DO UPDATE SET at = excluded.at, "
                    "source = excluded.source, quotes_seen = excluded.quotes_seen, "
                    "rules_evaluated = excluded.rules_evaluated, fired = excluded.fired, error = ''",
                    (_iso(fired_at), source, seen, evaluated, len(out)),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return out

    def evaluator_state(self) -> EvaluatorState:
        with self._connect(write=False) as conn:
            try:
                row = conn.execute(
                    "SELECT at, source, quotes_seen, rules_evaluated, fired, error FROM evaluator_state WHERE key = 'last'"
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
        if row is None:
            return EvaluatorState(None, "", 0, 0, 0, "")
        at, source, q, r, f, err = row
        return EvaluatorState(datetime.fromisoformat(at), source, int(q), int(r), int(f), str(err))

    # -- notifications --------------------------------------------------------

    def notifications(self, owner: str, *, limit: int = 100, before: int | None = None) -> list[Notification]:
        """Newest first."""
        sql = ("SELECT seq, owner, rule_id, instrument, kind, side, level, price, quote_time, fired_at, message "
               "FROM notification WHERE owner = ?")
        params: list[object] = [owner]
        if before is not None:
            sql += " AND seq < ?"
            params.append(int(before))
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(int(limit))
        with self._connect(write=False) as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        return [Notification(int(s), o, rid, ins, k, sd, float(lv), float(p), datetime.fromisoformat(qt),
                             datetime.fromisoformat(fa), m) for s, o, rid, ins, k, sd, lv, p, qt, fa, m in rows]

    def counts(self, owner: str) -> tuple[int, int, int]:
        """``(total, unread, read_through_seq)`` for an operator."""
        with self._connect(write=False) as conn:
            try:
                total = conn.execute("SELECT COUNT(*) FROM notification WHERE owner = ?", (owner,)).fetchone()[0]
                row = conn.execute("SELECT through_seq FROM read_marker WHERE owner = ?", (owner,)).fetchone()
                through = int(row[0]) if row else 0
                unread = conn.execute("SELECT COUNT(*) FROM notification WHERE owner = ? AND seq > ?",
                                      (owner, through)).fetchone()[0]
            except sqlite3.OperationalError:
                return 0, 0, 0
        return int(total), int(unread), through

    def mark_read(self, owner: str, through_seq: int, *, now: datetime | None = None) -> int:
        """Move the read marker forward (never back) to ``through_seq``, capped at the newest; returns it."""
        at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        with self._connect(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                newest = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM notification WHERE owner = ?",
                                      (owner,)).fetchone()[0]
                row = conn.execute("SELECT through_seq FROM read_marker WHERE owner = ?", (owner,)).fetchone()
                current = int(row[0]) if row else 0
                target = max(current, min(int(through_seq), int(newest)))
                conn.execute(
                    "INSERT INTO read_marker (owner, through_seq, at) VALUES (?, ?, ?) ON CONFLICT (owner) "
                    "DO UPDATE SET through_seq = excluded.through_seq, at = excluded.at",
                    (owner, target, _iso(at)),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return target
