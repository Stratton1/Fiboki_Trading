"""Append-only audit ledger. Nothing an agent does may be invisible.

Every agent action -- every tool call, every model call, every refusal -- lands
here before anything downstream is allowed to believe it happened.  A record
carries the agent and its role, the tool, the FULL inputs and outputs, the
reason and prompt that produced it, its parent action, the model and model
version, token and cost accounting, wall time, and the outcome.

Append-only is enforced at the repository layer, three ways:

1.  :class:`AuditRecord` is a frozen dataclass; an existing record cannot be
    edited in place.
2.  The ledger interface has ``append`` and readers.  There is no ``update``,
    no ``delete`` and no ``truncate``.  A test enumerates the public surface
    and fails if one appears.
3.  Records are hash-chained: each one commits to the previous record's hash,
    so a record edited or removed from a persisted ledger is detectable by
    :meth:`AuditLedger.verify_chain` rather than merely discouraged.

The JSONL implementation only ever opens its file in append mode.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, runtime_checkable

GENESIS_HASH = "0" * 64


class Outcome(str, Enum):
    """What happened.  ``DENIED`` is as important a record as ``OK``."""

    OK = "ok"
    DENIED = "denied"
    ERROR = "error"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


class ActionKind(str, Enum):
    TOOL_CALL = "tool_call"
    MODEL_CALL = "model_call"
    JOB_SUBMIT = "job_submit"
    JOB_RUN = "job_run"
    WORKFLOW_STEP = "workflow_step"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _canonical(payload: Any) -> Any:
    """JSON-safe, order-stable projection of arbitrary tool input/output."""
    if payload is None or isinstance(payload, bool | int | float | str):
        return payload
    if isinstance(payload, Mapping):
        return {str(k): _canonical(payload[k]) for k in sorted(payload, key=str)}
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    if isinstance(payload, Sequence):
        return [_canonical(v) for v in payload]
    if isinstance(payload, Enum):
        return payload.value
    if isinstance(payload, datetime):
        return payload.isoformat()
    to_dump = getattr(payload, "model_dump", None)
    if callable(to_dump):
        return _canonical(to_dump(mode="json"))
    return repr(payload)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One immutable line in the ledger."""

    agent_id: str
    role: str
    kind: ActionKind
    tool: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    reason: str
    outcome: Outcome

    prompt: str = ""
    parent_action_id: str | None = None
    workflow_id: str | None = None
    model: str = ""
    model_version: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    wall_ms: float = 0.0
    error: str = ""
    capability: str = ""

    action_id: str = field(default_factory=lambda: f"act_{uuid.uuid4().hex[:16]}")
    recorded_at: datetime = field(default_factory=_now)
    sequence: int = -1
    previous_hash: str = GENESIS_HASH
    record_hash: str = ""

    # -- hashing ----------------------------------------------------------

    def payload(self, *, with_hash: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action_id": self.action_id,
            "sequence": self.sequence,
            "recorded_at": self.recorded_at.isoformat(),
            "agent_id": self.agent_id,
            "role": self.role,
            "kind": self.kind.value,
            "tool": self.tool,
            "capability": self.capability,
            "inputs": _canonical(self.inputs),
            "outputs": _canonical(self.outputs),
            "reason": self.reason,
            "prompt": self.prompt,
            "parent_action_id": self.parent_action_id,
            "workflow_id": self.workflow_id,
            "model": self.model,
            "model_version": self.model_version,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "wall_ms": self.wall_ms,
            "outcome": self.outcome.value,
            "error": self.error,
            "previous_hash": self.previous_hash,
        }
        if with_hash:
            data["record_hash"] = self.record_hash
        return data

    def compute_hash(self) -> str:
        blob = json.dumps(
            self.payload(with_hash=False),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def sealed(self, *, sequence: int, previous_hash: str) -> AuditRecord:
        """Return this record positioned in the chain, with its hash computed."""
        staged = replace(
            self, sequence=sequence, previous_hash=previous_hash, record_hash=""
        )
        return replace(staged, record_hash=staged.compute_hash())

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> AuditRecord:
        return cls(
            agent_id=str(raw["agent_id"]),
            role=str(raw["role"]),
            kind=ActionKind(raw["kind"]),
            tool=str(raw["tool"]),
            inputs=dict(raw.get("inputs") or {}),
            outputs=dict(raw.get("outputs") or {}),
            reason=str(raw.get("reason", "")),
            outcome=Outcome(raw["outcome"]),
            prompt=str(raw.get("prompt", "")),
            parent_action_id=raw.get("parent_action_id"),
            workflow_id=raw.get("workflow_id"),
            model=str(raw.get("model", "")),
            model_version=str(raw.get("model_version", "")),
            provider=str(raw.get("provider", "")),
            prompt_tokens=int(raw.get("prompt_tokens", 0)),
            completion_tokens=int(raw.get("completion_tokens", 0)),
            cost_usd=float(raw.get("cost_usd", 0.0)),
            wall_ms=float(raw.get("wall_ms", 0.0)),
            error=str(raw.get("error", "")),
            capability=str(raw.get("capability", "")),
            action_id=str(raw["action_id"]),
            recorded_at=datetime.fromisoformat(str(raw["recorded_at"])),
            sequence=int(raw.get("sequence", -1)),
            previous_hash=str(raw.get("previous_hash", GENESIS_HASH)),
            record_hash=str(raw.get("record_hash", "")),
        )


class ChainError(RuntimeError):
    """The persisted ledger no longer hashes to itself."""


@runtime_checkable
class AuditLedgerProtocol(Protocol):
    """The only operations a ledger offers.  Note what is missing."""

    def append(self, record: AuditRecord) -> AuditRecord: ...
    def records(self) -> tuple[AuditRecord, ...]: ...
    def verify_chain(self) -> bool: ...
    def __len__(self) -> int: ...


class AuditLedger:
    """In-memory append-only ledger with a hash chain.

    Deliberately minimal public surface: ``append`` plus readers.  No update,
    no delete, no truncate -- ``tests/unit/test_agents_audit.py`` asserts that
    the surface stays that way.
    """

    #: The complete set of public methods.  Enforced by test, not by comment.
    PUBLIC_SURFACE: tuple[str, ...] = (
        "append",
        "by_workflow",
        "children_of",
        "records",
        "tail",
        "total_cost_usd",
        "verify_chain",
    )

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    # -- writing ----------------------------------------------------------

    def append(self, record: AuditRecord) -> AuditRecord:
        sealed = record.sealed(
            sequence=len(self._records),
            previous_hash=self._records[-1].record_hash if self._records else GENESIS_HASH,
        )
        self._records.append(sealed)
        return sealed

    # -- reading ----------------------------------------------------------

    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def tail(self, n: int = 10) -> tuple[AuditRecord, ...]:
        return tuple(self._records[-n:])

    def by_workflow(self, workflow_id: str) -> tuple[AuditRecord, ...]:
        return tuple(r for r in self._records if r.workflow_id == workflow_id)

    def children_of(self, action_id: str) -> tuple[AuditRecord, ...]:
        return tuple(r for r in self._records if r.parent_action_id == action_id)

    def total_cost_usd(self) -> float:
        return round(sum(r.cost_usd for r in self._records), 10)

    def verify_chain(self) -> bool:
        return _verify(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[AuditRecord]:
        return iter(self._records)


class JsonlAuditLedger(AuditLedger):
    """Durable ledger.  The file is only ever opened for APPEND.

    Reading is done with a separate read-only handle, so there is no code path
    in this class that can truncate or rewrite the file.
    """

    PUBLIC_SURFACE: tuple[str, ...] = (*AuditLedger.PUBLIC_SURFACE, "path", "reload")

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.reload()

    def append(self, record: AuditRecord) -> AuditRecord:
        sealed = super().append(record)
        line = json.dumps(sealed.payload(), sort_keys=True, separators=(",", ":"))
        # Append mode, flush and fsync: a crash mid-workflow must not lose the
        # record of what the agent had already done.
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return sealed

    def reload(self) -> None:
        """Re-read the file.  Replaces the in-memory view; never writes."""
        loaded: list[AuditRecord] = []
        with open(self.path, encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    loaded.append(AuditRecord.from_payload(json.loads(text)))
                except (ValueError, KeyError) as exc:
                    raise ChainError(f"{self.path}:{lineno}: unreadable record: {exc}") from exc
        self._records = loaded


def _verify(records: Sequence[AuditRecord]) -> bool:
    previous = GENESIS_HASH
    for i, record in enumerate(records):
        if record.sequence != i or record.previous_hash != previous:
            return False
        if record.compute_hash() != record.record_hash:
            return False
        previous = record.record_hash
    return True


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------


class AuditedAction:
    """Context manager that times an action and appends exactly one record.

    Used by :class:`fiboki.agents.session.AgentSession` so that a tool call
    that raises is recorded just as faithfully as one that succeeds.
    """

    def __init__(
        self,
        ledger: AuditLedger,
        *,
        agent_id: str,
        role: str,
        kind: ActionKind,
        tool: str,
        inputs: Mapping[str, Any],
        reason: str,
        prompt: str = "",
        parent_action_id: str | None = None,
        workflow_id: str | None = None,
        model: str = "",
        model_version: str = "",
        provider: str = "",
        capability: str = "",
    ) -> None:
        self._ledger = ledger
        self._base: dict[str, Any] = {
            "agent_id": agent_id,
            "role": role,
            "kind": kind,
            "tool": tool,
            "inputs": dict(inputs),
            "reason": reason,
            "prompt": prompt,
            "parent_action_id": parent_action_id,
            "workflow_id": workflow_id,
            "model": model,
            "model_version": model_version,
            "provider": provider,
            "capability": capability,
        }
        self.outputs: dict[str, Any] = {}
        self.outcome: Outcome = Outcome.OK
        self.error: str = ""
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.cost_usd: float = 0.0
        self.record: AuditRecord | None = None
        self._started = 0.0

    def __enter__(self) -> AuditedAction:
        self._started = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None:
            self.outcome = _outcome_for(exc)
            self.error = f"{type(exc).__name__}: {exc}"
            self.outputs = {}
        self.record = self._ledger.append(
            AuditRecord(
                outputs=self.outputs,
                outcome=self.outcome,
                error=self.error,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                cost_usd=self.cost_usd,
                wall_ms=round((time.perf_counter() - self._started) * 1000.0, 4),
                **self._base,
            )
        )
        # Returns None: this context manager records exceptions, never swallows them.


def _outcome_for(exc: BaseException) -> Outcome:
    if isinstance(exc, PermissionError):
        return Outcome.DENIED
    if isinstance(exc, TimeoutError):
        return Outcome.TIMEOUT
    if isinstance(exc, ValueError):
        return Outcome.REJECTED
    return Outcome.ERROR


__all__ = [
    "GENESIS_HASH",
    "ActionKind",
    "AuditLedger",
    "AuditLedgerProtocol",
    "AuditRecord",
    "AuditedAction",
    "ChainError",
    "JsonlAuditLedger",
    "Outcome",
]
