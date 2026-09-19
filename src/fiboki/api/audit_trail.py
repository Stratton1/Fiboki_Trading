"""Append-only, hash-chained record of every mutating API call.

Scope note: :mod:`fiboki.agents.audit` records what an *agent* did. This records
what a *human operator* did through HTTP, which is a different question with a
different retention need, so it is a separate ledger rather than a widened one.

Every entry is sealed with ``sha256(previous_hash + canonical_payload)``, so a
deleted or edited row breaks verification at that point. Refusals are recorded
as loudly as successes: an attempted kill-switch disarm by a non-admin is
exactly the row an incident review needs, and V1 wrote neither.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["ApiAuditTrail", "AuditEntry"]

GENESIS = "0" * 64


def _canonical(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(k): _canonical(payload[k]) for k in sorted(payload)}
    if isinstance(payload, list | tuple):
        return [_canonical(v) for v in payload]
    if isinstance(payload, str | int | float | bool | type(None)):
        return payload
    return str(payload)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    action: str
    """Stable verb, e.g. ``killswitch.activate``."""
    actor: str
    actor_role: str
    outcome: str
    """``allowed`` | ``refused`` | ``failed``."""
    reason: str
    """Why the operator says they did it. Mandatory for capital-affecting acts."""
    target: str = ""
    execution_mode: str = ""
    correlation_id: str = ""
    source_ip: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    sequence: int = 0
    previous_hash: str = GENESIS
    entry_hash: str = ""

    def payload(self, *, with_hash: bool) -> dict[str, Any]:
        data = asdict(self)
        if not with_hash:
            data.pop("entry_hash", None)
        return _canonical(data)

    def compute_hash(self) -> str:
        blob = json.dumps(
            self.payload(with_hash=False), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256((self.previous_hash + blob).encode("utf-8")).hexdigest()


class ApiAuditTrail:
    """JSONL ledger. Opens for append only; there is no update or delete path."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------- write

    def append(self, entry: AuditEntry) -> AuditEntry:
        with self._lock:
            tail = self._tail()
            sealed = AuditEntry(
                **{
                    **{
                        k: v
                        for k, v in asdict(entry).items()
                        if k not in {"sequence", "previous_hash", "entry_hash"}
                    },
                    "sequence": (tail.sequence + 1) if tail else 1,
                    "previous_hash": tail.entry_hash if tail else GENESIS,
                }
            )
            sealed = AuditEntry(
                **{**asdict(sealed), "entry_hash": sealed.compute_hash()}
            )
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(asdict(sealed), sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
                fh.flush()
                os.fsync(fh.fileno())
            return sealed

    def record(
        self,
        action: str,
        *,
        actor: str,
        actor_role: str,
        outcome: str,
        reason: str,
        target: str = "",
        execution_mode: str = "",
        correlation_id: str = "",
        source_ip: str = "",
        detail: dict[str, Any] | None = None,
    ) -> AuditEntry:
        return self.append(
            AuditEntry(
                action=action,
                actor=actor,
                actor_role=actor_role,
                outcome=outcome,
                reason=reason,
                target=target,
                execution_mode=execution_mode,
                correlation_id=correlation_id,
                source_ip=source_ip,
                detail=dict(detail or {}),
            )
        )

    # -------------------------------------------------------------- read

    def _iter_raw(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)

    def entries(self) -> list[AuditEntry]:
        return [AuditEntry(**raw) for raw in self._iter_raw()]

    def tail(self, n: int = 100) -> list[AuditEntry]:
        rows = self.entries()
        return rows[-n:][::-1]

    def _tail(self) -> AuditEntry | None:
        rows = self.entries()
        return rows[-1] if rows else None

    def verify_chain(self) -> tuple[bool, int]:
        """Return ``(intact, first_broken_sequence)``. ``0`` means intact."""
        previous = GENESIS
        for index, entry in enumerate(self.entries(), start=1):
            if entry.sequence != index or entry.previous_hash != previous:
                return False, entry.sequence
            if entry.compute_hash() != entry.entry_hash:
                return False, entry.sequence
            previous = entry.entry_hash
        return True, 0

    def __len__(self) -> int:
        return sum(1 for _ in self._iter_raw())
