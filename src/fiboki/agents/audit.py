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

Inter-process safety
--------------------
The API process and the research worker may both hold a
:class:`JsonlAuditLedger` on the same file.  Each instance caches the chain it
has seen, so without coordination two writers would each seal a record on top
of the same tail and fork the chain.  :meth:`JsonlAuditLedger.append`
therefore holds an exclusive ``fcntl.flock`` on a sidecar ``<path>.lock`` across
read-tail, append and fsync; adopts, after verifying them, any records another
writer appended since this instance last looked; and refuses with
:class:`AuditChainForkError` if the file no longer continues the chain this
instance holds.  Pattern after Vibe-Trading ``governance/ledger.py`` (MIT),
written fresh.  ``fcntl`` makes this POSIX-only (macOS and Linux).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Protocol, runtime_checkable

GENESIS_HASH = "0" * 64

#: ``AuditRecord.model_id`` for an action no model took part in: a workflow
#: boundary, or a tool call a session made before it had asked any model.
#: Distinct from ``None``, which means the field was never recorded.
NO_MODEL = "none"

#: Provenance fields that are hashed only when present.  See AuditRecord.
_OPTIONAL_PROVENANCE: tuple[str, ...] = ("model_id", "model_digest", "manifest_hash")


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


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
    #: Provenance of the run, added in Wave 2.  ``None`` means "not recorded"
    #: (every record written before the fields existed) and is left OUT of the
    #: hashed payload, so those records still hash to what they always did.
    #: ``model_id`` is the model that produced this action, or :data:`NO_MODEL`
    #: for an action no model was involved in; ``model_digest`` pins its
    #: weights where the provider can; ``manifest_hash`` is the
    #: :class:`~fiboki.agents.manifest.RunManifest` hash of the prompts, tool
    #: schemas and library versions in force.
    model_id: str | None = None
    model_digest: str | None = None
    manifest_hash: str | None = None

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
        for key in _OPTIONAL_PROVENANCE:
            value = getattr(self, key)
            if value is not None:
                data[key] = value
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
            model_id=_optional_str(raw.get("model_id")),
            model_digest=_optional_str(raw.get("model_digest")),
            manifest_hash=_optional_str(raw.get("manifest_hash")),
            action_id=str(raw["action_id"]),
            recorded_at=datetime.fromisoformat(str(raw["recorded_at"])),
            sequence=int(raw.get("sequence", -1)),
            previous_hash=str(raw.get("previous_hash", GENESIS_HASH)),
            record_hash=str(raw.get("record_hash", "")),
        )


class ChainError(RuntimeError):
    """The persisted ledger no longer hashes to itself."""


class AuditChainForkError(ChainError):
    """This instance's view of the chain no longer matches the file on disk.

    Raised by :meth:`JsonlAuditLedger.append` instead of writing, when the
    record this instance believes is the tail is not where it should be on
    disk, or when records another writer appended do not continue it.  Sealing
    a record on top of a tail the file does not hold would fork the chain.
    """


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

    Safe for several instances, in one process or several, on the same path:
    every append holds an exclusive ``flock`` on ``<path>.lock`` (a sidecar that
    is never written), re-reads the on-disk tail, catches up on records other
    writers appended (verifying that they continue this instance's chain) and
    only then seals and writes.  A file that no longer continues this
    instance's chain is refused with :class:`AuditChainForkError`.

    Verification cost: :meth:`reload` runs the full :meth:`verify_chain`; each
    :meth:`append` checks only the cached tail plus any records appended since,
    so steady-state appends are O(1) in the length of the ledger.  A ledger
    whose loaded chain failed verification refuses every append: extending a
    chain already known to be broken would bury the break under valid records.
    """

    PUBLIC_SURFACE: tuple[str, ...] = (*AuditLedger.PUBLIC_SURFACE, "path", "reload")

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._mutex = threading.Lock()
        #: Bytes of the file this instance has read or written: the position
        #: at which its cached tail record ends.
        self._offset = 0
        #: Result of the full verification run by the last reload.
        self._loaded_chain_ok = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.reload()

    # -- locking ----------------------------------------------------------

    @contextmanager
    def _file_lock(self, *, exclusive: bool) -> Iterator[None]:
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # -- writing ----------------------------------------------------------

    def append(self, record: AuditRecord) -> AuditRecord:
        with self._mutex, self._file_lock(exclusive=True):
            self._catch_up()
            sealed = record.sealed(
                sequence=len(self._records),
                previous_hash=self._records[-1].record_hash if self._records else GENESIS_HASH,
            )
            line = (
                json.dumps(sealed.payload(), sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            created = not self.path.exists()
            # Append mode, flush and fsync: a crash mid-workflow must not lose the
            # record of what the agent had already done.
            with open(self.path, "ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            if created:
                # The new directory entry must survive a crash too.
                _fsync_directory(self.path.parent)
            # Only a record that is durably on disk joins the in-memory chain.
            self._records.append(sealed)
            self._offset += len(line)
        return sealed

    def _catch_up(self) -> None:
        """Bring the cached chain level with the file, or refuse.  Lock held."""
        if not self._loaded_chain_ok:
            raise ChainError(
                f"{self.path}: the chain loaded from disk failed verification; "
                "refusing to append to a ledger known to be broken"
            )
        tail_hash = self._records[-1].record_hash if self._records else GENESIS_HASH
        size = self.path.stat().st_size if self.path.exists() else 0
        if size < self._offset:
            raise AuditChainForkError(
                f"{self.path}: file is {size} bytes but this instance has seen "
                f"{self._offset}; it was truncated or replaced"
            )
        if size == 0:
            return
        with open(self.path, "rb") as handle:
            if self._records:
                # The line ending where this instance stopped reading must be
                # its own cached tail.
                on_disk = _line_ending_at(handle, self._offset, self.path)
                try:
                    disk_hash = json.loads(on_disk).get("record_hash")
                except ValueError as exc:
                    raise AuditChainForkError(
                        f"{self.path}: unreadable record where this instance's tail "
                        f"should be: {exc}"
                    ) from exc
                if disk_hash != tail_hash:
                    raise AuditChainForkError(
                        f"{self.path}: on-disk record at this instance's tail has hash "
                        f"{disk_hash!r}, expected {tail_hash!r}; the file no longer "
                        "holds the chain this instance extends"
                    )
            if size == self._offset:
                return
            handle.seek(self._offset)
            blob = handle.read(size - self._offset)
        if not blob.endswith(b"\n"):
            raise AuditChainForkError(
                f"{self.path}: the file does not end at a record boundary; a writer "
                "died mid-record or the file was edited"
            )
        try:
            fresh = _parse_lines(blob, self.path)
        except ChainError as exc:
            raise AuditChainForkError(str(exc)) from exc
        previous, sequence = tail_hash, len(self._records)
        for rec in fresh:
            if (
                rec.sequence != sequence
                or rec.previous_hash != previous
                or rec.compute_hash() != rec.record_hash
            ):
                raise AuditChainForkError(
                    f"{self.path}: record {rec.action_id} appended by another writer "
                    f"does not continue this chain at sequence {sequence}"
                )
            previous, sequence = rec.record_hash, sequence + 1
        self._records.extend(fresh)
        self._offset = size

    # -- reading ----------------------------------------------------------

    def reload(self) -> None:
        """Re-read the file and run the full chain verification.  Never writes.

        A broken chain loads (so it can be inspected and reported) but is
        remembered as broken, and :meth:`append` then refuses.
        """
        with self._mutex, self._file_lock(exclusive=False):
            with open(self.path, "rb") as handle:
                blob = handle.read()
            self._records = _parse_lines(blob, self.path)
            self._offset = len(blob)
            self._loaded_chain_ok = self.verify_chain()


def _parse_lines(blob: bytes, path: Path) -> list[AuditRecord]:
    loaded: list[AuditRecord] = []
    for lineno, line in enumerate(blob.split(b"\n"), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            loaded.append(AuditRecord.from_payload(json.loads(text)))
        except (ValueError, KeyError) as exc:
            raise ChainError(f"{path}:{lineno}: unreadable record: {exc}") from exc
    return loaded


def _line_ending_at(handle: IO[bytes], end: int, path: Path) -> bytes:
    """The line whose terminating newline is the byte at ``end - 1``.

    Reads backwards in chunks, so the cost is the length of one record, not of
    the file.
    """
    handle.seek(end - 1)
    if handle.read(1) != b"\n":
        raise AuditChainForkError(
            f"{path}: byte {end - 1} is not a record boundary; the file was edited "
            "or replaced"
        )
    parts: list[bytes] = []
    cursor = end - 1
    while cursor > 0:
        step = min(8192, cursor)
        cursor -= step
        handle.seek(cursor)
        chunk = handle.read(step)
        cut = chunk.rfind(b"\n")
        if cut >= 0:
            parts.append(chunk[cut + 1 :])
            break
        parts.append(chunk)
    return b"".join(reversed(parts))


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
        model_id: str | None = None,
        model_digest: str | None = None,
        manifest_hash: str | None = None,
    ) -> None:
        self._ledger = ledger
        #: Settable inside the block: the digest may only be known once the
        #: provider has been asked for its fingerprint.
        self.model_id = model_id
        self.model_digest = model_digest
        self.manifest_hash = manifest_hash
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
                model_id=self.model_id,
                model_digest=self.model_digest,
                manifest_hash=self.manifest_hash,
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
    "NO_MODEL",
    "ActionKind",
    "AuditChainForkError",
    "AuditLedger",
    "AuditLedgerProtocol",
    "AuditRecord",
    "AuditedAction",
    "ChainError",
    "JsonlAuditLedger",
    "Outcome",
]
