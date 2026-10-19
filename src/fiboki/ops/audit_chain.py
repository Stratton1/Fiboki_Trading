"""The paper runtime's immutable, hash-chained audit log.

Why another ledger
------------------
The paper runtime's journals (``journal.jsonl``, ``attempts.jsonl``,
``intents.jsonl``, ``trades.jsonl``) are durable and CRC-framed, which proves a
line is what the WRITER wrote. It does not prove the file is what the writer
LEFT: a line can be deleted, reordered or rewritten with a fresh CRC and every
reader will accept it. For a paper record that is meant to become evidence (the
maturity report, the reconciliation, a decision to consider IG demo) that is
not enough.

This log records every order intent, every venue fill, every close and
protective amend, every operator control the runtime applied and every risk
gateway decision, each as one row::

    {"schema", "seq", "at", "kind", "session_id", "payload", "prev_hash", "hash"}

where ``hash = sha256(canonical JSON of the row without "hash")`` and
``prev_hash`` is the previous row's ``hash`` (64 zeros for the first). Editing,
deleting, inserting or reordering any row breaks :meth:`PaperAuditChain.verify`
at that row and names it.

Canonical JSON: keys sorted, ``(",", ":")`` separators, UTF-8, no NaN (a NaN is
written as ``null`` before hashing, so the bytes hashed are the bytes stored).

What it is not
--------------
Not a ledger of record (the book's ``trades.jsonl`` is) and not tamper-PROOF:
someone who can write the file can rewrite the whole chain from the first
edited row onwards. It is tamper-EVIDENT against the realistic failures (a
hand edit, a partial restore, a merge of two files) and the head hash shown on
the System board lets an operator note one value that pins everything before
it. Rows are written unframed (``durable_append(frame=False)``) so that the hash
chain, not a per-line CRC an editor would simply recompute, is the integrity
mechanism; they are fsynced exactly like every other ledger.

One writer per file: the paper runtime holding the worker lease for that
ledger directory.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fiboki.core.durable import durable_append, read_payloads

__all__ = [
    "AUDIT_SCHEMA",
    "CHAIN_FILE",
    "GENESIS",
    "ChainVerification",
    "PaperAuditChain",
    "canonical_json",
    "row_hash",
]

AUDIT_SCHEMA = "fiboki.paper_audit/1"
CHAIN_FILE = "audit_chain.jsonl"
GENESIS = "0" * 64


def _plain(value: Any) -> Any:
    """JSON-native values only, NaN and inf as null, so hashing is reproducible."""
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).isoformat()
    if hasattr(value, "isoformat") and callable(value.isoformat):  # pd.Timestamp, date
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else float(value)
    try:  # numpy scalars
        item = value.item()
    except AttributeError:
        return str(value)
    return _plain(item)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def row_hash(row: Mapping[str, Any]) -> str:
    body = {k: v for k, v in row.items() if k != "hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ChainVerification:
    present: bool
    intact: bool
    rows: int
    #: The first row that does not verify (1-based ``seq`` position); ``None`` when intact.
    first_broken_seq: int | None
    reason: str
    head_hash: str | None
    first_at: str | None
    last_at: str | None
    kinds: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "intact": self.intact,
            "rows": self.rows,
            "first_broken_seq": self.first_broken_seq,
            "reason": self.reason,
            "head_hash": self.head_hash,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "kinds": dict(self.kinds),
        }


class PaperAuditChain:
    """Append-only, hash-chained rows in ``<ledger_dir>/audit_chain.jsonl``."""

    def __init__(self, path: str | Path, *, session_id: str) -> None:
        self.path = Path(path)
        self.session_id = session_id
        self._seq = 0
        self._head = GENESIS
        self._loaded = False

    def _load_tail(self) -> None:
        if self._loaded:
            return
        rows = self.rows()
        if rows:
            last = rows[-1]
            self._seq = int(last.get("seq", len(rows)))
            self._head = str(last.get("hash", GENESIS))
        self._loaded = True

    def append(self, kind: str, payload: Mapping[str, Any], *, at: Any = None) -> dict[str, Any]:
        self._load_tail()
        stamp = at if at is not None else datetime.now(tz=UTC)
        row: dict[str, Any] = {
            "schema": AUDIT_SCHEMA,
            "seq": self._seq + 1,
            "at": _plain(stamp),
            "kind": str(kind),
            "session_id": self.session_id,
            "payload": _plain(dict(payload)),
            "prev_hash": self._head,
        }
        row["hash"] = row_hash(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        durable_append(self.path, canonical_json(row), frame=False)
        self._seq = row["seq"]
        self._head = row["hash"]
        return row

    @property
    def head(self) -> str:
        self._load_tail()
        return self._head

    # -- reads ---------------------------------------------------------------

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(p) for p in read_payloads(self.path)]

    def tail(self, n: int = 100) -> list[dict[str, Any]]:
        return self.rows()[-n:][::-1]

    def signature(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return (st.st_size, st.st_mtime_ns)

    def verify(self) -> ChainVerification:
        """Walk the chain from genesis. The first row that does not verify is named."""
        if not self.path.exists():
            return ChainVerification(
                present=False, intact=True, rows=0, first_broken_seq=None,
                reason="no audit chain yet: this session has not written one",
                head_hash=None, first_at=None, last_at=None, kinds={},
            )
        try:
            rows = self.rows()
        except Exception as exc:  # a corrupt framed line / unreadable file
            return ChainVerification(
                present=True, intact=False, rows=0, first_broken_seq=1,
                reason=f"the file could not be read: {type(exc).__name__}: {exc}",
                head_hash=None, first_at=None, last_at=None, kinds={},
            )
        previous = GENESIS
        kinds: dict[str, int] = {}
        for index, row in enumerate(rows, start=1):
            kinds[str(row.get("kind"))] = kinds.get(str(row.get("kind")), 0) + 1
            problem = ""
            if row.get("seq") != index:
                problem = f"row {index} carries seq {row.get('seq')!r}: a row was removed, inserted or reordered"
            elif row.get("prev_hash") != previous:
                problem = f"row {index} does not link to row {index - 1}: prev_hash differs"
            elif row_hash(row) != row.get("hash"):
                problem = f"row {index}'s content does not match its hash: it was edited"
            if problem:
                return ChainVerification(
                    present=True, intact=False, rows=len(rows), first_broken_seq=index,
                    reason=problem, head_hash=str(rows[-1].get("hash")),
                    first_at=str(rows[0].get("at")), last_at=str(rows[-1].get("at")), kinds=kinds,
                )
            previous = str(row.get("hash"))
        return ChainVerification(
            present=True, intact=True, rows=len(rows), first_broken_seq=None,
            reason=(f"{len(rows)} row(s) verify from genesis" if rows else "empty chain"),
            head_hash=previous if rows else None,
            first_at=str(rows[0].get("at")) if rows else None,
            last_at=str(rows[-1].get("at")) if rows else None,
            kinds=kinds,
        )
