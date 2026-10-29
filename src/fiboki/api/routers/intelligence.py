"""INTELLIGENCE: agent runs, the audit ledger, research memory.

The cardinal rule from :mod:`fiboki.agents` holds at the HTTP boundary too:
nothing here can cause an order. The only write is a research note, and even
that is admin-gated and audited.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.agents.audit import GENESIS_HASH
from fiboki.agents.evals.runner import LedgerUnreadable, load_ledger
from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure
from fiboki.api.security import Principal, require_admin
from fiboki.core.enums import Provenance
from fiboki.core.tier import default_tier_path, read_tier

#: One smoke and one eval do not close the platform's 20-run acceptance protocol.
ACCEPTANCE_PROTOCOL = (
    "One smoke call and one eval of the existing ledger do not finish the "
    "20-run acceptance protocol."
)

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])

AdminPrincipal = Annotated[Principal, Depends(require_admin)]


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


class AgentRoleView(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    purpose: str
    capabilities: list[str]
    #: Enumerated so the UI can state, per role, that execution is not on the
    #: list. The absence is the feature.
    can_execute: bool = False


class AgentRunView(BaseModel):
    """One agent-audit row, newest first on the page.

    ``activity``, ``activity_label`` and ``acceptance_protocol`` repeat on
    every row: :class:`~fiboki.api.models.Page` has no slot for a page-level
    status, and a bare count would be an unlabelled number.
    """

    model_config = ConfigDict(frozen=True)

    recorded_at: datetime
    workflow: str | None = None
    workflow_reason: str | None = None
    role: str
    outcome: str
    model_id: str | None = None
    model_id_reason: str | None = None
    model_digest: str | None = None
    model_digest_reason: str | None = None
    tier: str
    tier_source: str
    in_progress: bool = False
    activity: str
    activity_label: str
    acceptance_protocol: str = ACCEPTANCE_PROTOCOL


class AuditEntryView(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    at: datetime
    action: str
    actor: str
    actor_role: str
    outcome: str
    reason: str
    target: str
    execution_mode: str
    correlation_id: str
    entry_hash: str


class AuditIntegrityView(BaseModel):
    model_config = ConfigDict(frozen=True)

    intact: bool
    entries: Figure
    first_broken_sequence: int
    detail: str


class ResearchMemoryView(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    detail: str
    structures_recorded: Figure
    rediscovery_rate: Figure


@router.get("/agents", response_model=Page[AgentRoleView])
def agents() -> Page[AgentRoleView]:
    from fiboki.agents.roles import ROLES

    items: list[AgentRoleView] = []
    for name, spec in sorted(ROLES.items(), key=lambda kv: str(kv[0])):
        caps = sorted(
            str(getattr(c, "value", c)) for c in getattr(spec, "capabilities", ()) or ()
        )
        items.append(
            AgentRoleView(
                role=str(getattr(name, "value", name)),
                purpose=(getattr(spec, "purpose", "") or getattr(spec, "description", ""))[:400],
                capabilities=caps,
                can_execute=False,
            )
        )
    return Page(
        items=items,
        total=len(items),
        source=_note(
            "live",
            "fiboki.agents.roles. No role holds an execution capability; the "
            "capability does not exist to be granted.",
        ),
    )


class AgentCandidateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    brief: str
    status: str
    can_execute: bool = False
    refusal: str


@router.get("/candidates", response_model=Page[AgentCandidateView])
def candidates(settings: SettingsDep) -> Page[AgentCandidateView]:
    """Stored reviews only. A file that claims execution is still returned as false."""
    path = Path(settings.state_dir) / "agents" / "candidates.jsonl"
    items: list[AgentCandidateView] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            items.append(
                AgentCandidateView(
                    candidate_id=str(raw.get("candidate_id") or ""),
                    brief=str(raw.get("brief") or "")[:400],
                    status=str(raw.get("status") or "awaiting_review"),
                    can_execute=False,
                    refusal=str(raw.get("refusal") or "Can execute is false."),
                )
            )
    return Page(
        items=items,
        total=len(items),
        source=_note(
            "absent" if not path.is_file() else "live",
            "Agent candidate reviews. Nothing here is submitted to paper.",
        ),
    )


def _chain_break(records: tuple) -> int | None:
    """Sequence of the first record that does not continue the hash chain."""
    previous = GENESIS_HASH
    for index, record in enumerate(records):
        if (
            record.sequence != index
            or record.previous_hash != previous
            or record.compute_hash() != record.record_hash
        ):
            return int(record.sequence) if record.sequence >= 0 else index
        previous = record.record_hash
    return None


def _research_agent_detail(database: object) -> str | None:
    """Detail text when the research heartbeat says an agent step is in progress."""
    path = database
    if path is None or not getattr(path, "is_file", lambda: False)():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(
            "select status, detail from worker_heartbeat where kind = ? "
            "order by beat_at desc limit 1",
            ("research",),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    status_text, detail = row
    text = str(detail or "")
    if str(status_text) == "working" and "agent" in text.lower():
        return text[:200]
    return None


def _optional(value: str | None, reason: str) -> tuple[str | None, str | None]:
    if value is None or value == "":
        return None, reason
    return value, None


@router.get("/runs", response_model=Page[AgentRunView])
def runs(
    settings: SettingsDep,
    limit: int = Query(100, ge=1, le=100),
) -> Page[AgentRunView]:
    """Newest agent-audit rows first.

    A missing ledger is absent, not an empty success. A broken hash chain
    returns only the intact prefix and names the sequence where it breaks.
    The handler does not write ``agent_tier.json``.
    """
    path = settings.paths.audit_ledger
    reading = read_tier(default_tier_path(settings.state_dir), settings.session_secret)
    tier = reading.tier.value
    tier_source = reading.source
    protocol = ACCEPTANCE_PROTOCOL
    extra_caveats: list[Caveat] = [
        Caveat(
            code="acceptance_protocol_open",
            severity="info",
            message=protocol,
            affects="agent_quality",
        )
    ]
    smoke_path = path.parent / "smoke.json"
    if smoke_path.is_file():
        try:
            smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            extra_caveats.append(
                Caveat(
                    code="smoke_unreadable",
                    severity="warning",
                    message=f"The smoke record could not be read: {exc}",
                    affects="agent_quality",
                )
            )
        else:
            if smoke.get("ok") is not True:
                extra_caveats.append(
                    Caveat(
                        code="smoke_failed",
                        severity="warning",
                        message=str(smoke.get("error") or "The local-model smoke call failed."),
                        affects="agent_quality",
                    )
                )
    eval_path = path.parent / "eval_report.json"
    if eval_path.is_file():
        try:
            report = json.loads(eval_path.read_text(encoding="utf-8"))
            verdict = str(report.get("verdict") or "unrecorded")
        except (OSError, json.JSONDecodeError):
            verdict = "unreadable"
        extra_caveats.append(
            Caveat(
                code="eval_verdict",
                severity="info",
                message=f"Eval harness verdict on the existing ledger: {verdict}. {protocol}",
                affects="agent_quality",
            )
        )

    def _page(
        items: list[AgentRunView],
        *,
        total: int,
        kind: str,
        detail: str,
        caveats: tuple[Caveat, ...] = (),
    ) -> Page[AgentRunView]:
        return Page(
            items=items,
            total=total,
            limit=limit,
            source=_note(kind, detail),
            caveats=caveats,
        )

    if not path.is_file():
        return _page(
            [],
            total=0,
            kind="absent",
            detail=(
                f"No agent audit ledger at {path}. No run has been recorded, "
                "which is different from every run having succeeded."
            ),
            caveats=tuple(extra_caveats),
        )
    try:
        records, _digest, chain_ok = load_ledger(path)
    except LedgerUnreadable as exc:
        return _page(
            [],
            total=0,
            kind="absent",
            detail=f"The agent audit at {path} could not be read: {exc}",
            caveats=tuple(extra_caveats),
        )

    broken = None if chain_ok else _chain_break(records)
    intact = records if broken is None else tuple(r for r in records if r.sequence < broken)
    heartbeat = _research_agent_detail(settings.paths.heartbeat_db)
    newest = intact[-1] if intact else None
    ledger_open = newest is not None and newest.tool != "workflow:end"
    working = ledger_open or heartbeat is not None
    if heartbeat is not None:
        label = heartbeat
    elif newest is None:
        label = "No intact agent run is on the ledger."
    else:
        name = newest.workflow_id or newest.role
        label = f"{name} at {newest.recorded_at.isoformat()}"
    activity = "working" if working else "idle"

    views: list[AgentRunView] = []
    for record in reversed(intact[-limit:]):
        workflow, workflow_reason = _optional(
            record.workflow_id, "This audit line has no workflow id."
        )
        model_id, model_reason = _optional(
            record.model_id, "This audit line does not record a model id."
        )
        digest, digest_reason = _optional(
            record.model_digest, "This audit line does not record a model digest."
        )
        views.append(
            AgentRunView(
                recorded_at=record.recorded_at,
                workflow=workflow,
                workflow_reason=workflow_reason,
                role=record.role,
                outcome=record.outcome.value,
                model_id=model_id,
                model_id_reason=model_reason,
                model_digest=digest,
                model_digest_reason=digest_reason,
                tier=tier,
                tier_source=tier_source,
                in_progress=working and record is newest,
                activity=activity,
                activity_label=label,
            )
        )
    caveats = list(extra_caveats)
    if broken is not None:
        caveats.append(
            Caveat(
                code="agent_audit_chain_broken",
                severity="warning",
                message=(
                    f"The agent audit hash chain breaks at sequence {broken}. "
                    "Rows at and after that sequence are not shown as intact."
                ),
                affects="agent_audit",
            )
        )
    detail = (
        f"Hash-chained agent audit at {path}. Tier {tier} from {tier_source}. "
        f"Activity: {activity}. {label}"
    )
    return _page(
        views,
        total=len(intact),
        kind="live",
        detail=detail,
        caveats=tuple(caveats),
    )


@router.get("/audit", response_model=Page[AuditEntryView])
def audit_ledger(
    platform: PlatformDep,
    limit: int = Query(100, ge=1, le=500),
) -> Page[AuditEntryView]:
    entries = platform.audit.tail(limit)
    items = [
        AuditEntryView(
            sequence=e.sequence,
            at=datetime.fromisoformat(e.at),
            action=e.action,
            actor=e.actor,
            actor_role=e.actor_role,
            outcome=e.outcome,
            reason=e.reason,
            target=e.target,
            execution_mode=e.execution_mode,
            correlation_id=e.correlation_id,
            entry_hash=e.entry_hash[:16],
        )
        for e in entries
    ]
    return Page(
        items=items,
        total=len(platform.audit),
        limit=limit,
        source=_note("live", "Append-only, hash-chained operator audit trail."),
    )


@router.get("/audit/integrity", response_model=Envelope[AuditIntegrityView])
def audit_integrity(platform: PlatformDep) -> Envelope[AuditIntegrityView]:
    intact, broken = platform.audit.verify_chain()
    view = AuditIntegrityView(
        intact=intact,
        entries=Figure(
            value=float(len(platform.audit)), provenance=Provenance.PAPER, unit="count"
        ),
        first_broken_sequence=broken,
        detail=(
            "Every entry's hash matches its predecessor."
            if intact
            else f"The chain breaks at sequence {broken}. Entries at or after that "
            "point may have been edited or removed."
        ),
    )
    return Envelope(data=view, source=_note("live", "Recomputed over the whole ledger."))


class RecallMatchView(BaseModel):
    """One prior experiment. The body is the relation, not the report."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    relation: str
    outcome: str
    created_at: datetime
    similarity: Figure
    why: str


class IncomparableExperimentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    created_at: datetime
    key_versions: dict[str, str]


class RecallView(BaseModel):
    model_config = ConfigDict(frozen=True)

    novel: bool
    recommendation: str
    matches: list[RecallMatchView]
    incomparable: list[IncomparableExperimentView]


def _ledger_file(settings: object) -> Path | None:
    raw = getattr(settings, "experiment_db", None)
    if raw is None:
        return None
    return Path(raw)


def _missing_memory(detail: str, source_detail: str) -> Envelope[ResearchMemoryView]:
    view = ResearchMemoryView(
        available=False,
        detail=detail,
        structures_recorded=Figure.missing(Provenance.BACKTEST, unit="count"),
        rediscovery_rate=Figure.missing(Provenance.BACKTEST, unit="pct"),
    )
    return Envelope(data=view, source=_note("absent", source_detail))


def _similarity(value: float) -> Figure:
    return Figure(
        value=float(value),
        provenance=Provenance.BACKTEST,
        unit="ratio",
        estimated=True,
        caveats=(
            Caveat(
                code="token_overlap",
                severity="info",
                message="Similarity is token overlap, not a traded outcome.",
                affects="similarity",
                direction="unknown",
            ),
        ),
    )


def _open_ledger(path: Path):
    from fiboki.research.experiment import ExperimentLedger

    return ExperimentLedger(path)


@router.get("/research-memory", response_model=Envelope[ResearchMemoryView])
def research_memory(settings: SettingsDep) -> Envelope[ResearchMemoryView]:
    path = _ledger_file(settings)
    if path is None:
        return _missing_memory(
            "Research memory is backed by the experiment ledger, which is "
            "not configured. 'Have we tried this already?' cannot be answered on "
            "this deployment, so assume yes and check before running.",
            "No experiment ledger.",
        )
    if not path.is_file():
        return _missing_memory(
            "An experiment ledger path is configured, but the file is not on disk. "
            "Recall cannot be answered.",
            "Experiment ledger path is configured and the file is missing.",
        )
    from fiboki.research.memory import _keys_comparable
    from fiboki.strategy.dsl import strategy_key_version

    expected = strategy_key_version()
    hashes: set[str] = set()
    with _open_ledger(path) as ledger:
        for exp in ledger.list(allow_stale_keys=True):
            if not _keys_comparable(exp, expected):
                continue
            if exp.structure_hash:
                hashes.add(exp.structure_hash)
    view = ResearchMemoryView(
        available=True,
        detail=(
            "Structures are distinct comparable structure hashes. Rediscovery "
            "rate is not stored on the ledger, so it is absent rather than inferred."
        ),
        structures_recorded=Figure(
            value=float(len(hashes)),
            provenance=Provenance.BACKTEST,
            unit="count",
        ),
        rediscovery_rate=Figure.missing(
            Provenance.BACKTEST,
            unit="pct",
            reason=(
                "The ledger does not store a rediscovery relation. Keyword overlap "
                "is not that rate."
            ),
        ),
    )
    return Envelope(
        data=view,
        source=_note("mixed", "Counted from the experiment ledger. No holdout was read."),
    )


@router.get("/research-memory/recall", response_model=Envelope[RecallView])
def research_memory_recall(
    settings: SettingsDep,
    q: str = Query(default=""),
) -> Envelope[RecallView]:
    if not q.strip():
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "recall_query_empty",
            "Recall needs a text query. An empty query is not evidence of novelty.",
        )
    path = _ledger_file(settings)
    if path is None or not path.is_file():
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "research_memory_absent",
            "Research memory has no experiment ledger to recall from.",
        )
    from fiboki.research.memory import RecallResult, ResearchMemory, _keys_comparable
    from fiboki.strategy.dsl import strategy_key_version

    expected = strategy_key_version()
    with _open_ledger(path) as ledger:
        result = ResearchMemory(ledger).recall(text=q.strip())
        stale = [
            exp
            for exp in ledger.list(allow_stale_keys=True)
            if not _keys_comparable(exp, expected)
        ]
    by_id = {exp.id: exp for exp in result.incomparable}
    for exp in stale:
        by_id.setdefault(exp.id, exp)
    merged = RecallResult(
        query=result.query,
        matches=result.matches,
        fingerprint=result.fingerprint,
        threshold=result.threshold,
        incomparable=tuple(by_id.values()),
    )
    view = RecallView(
        novel=merged.is_novel,
        recommendation=merged.recommendation(),
        matches=[
            RecallMatchView(
                experiment_id=match.experiment.id,
                relation=match.relation.value,
                outcome=match.experiment.outcome.value,
                created_at=match.experiment.created_at,
                similarity=_similarity(match.similarity),
                why=match.why[:400],
            )
            for match in merged.matches
        ],
        incomparable=[
            IncomparableExperimentView(
                experiment_id=exp.id,
                created_at=exp.created_at,
                key_versions={
                    "strategy_content_hash": exp.key_version("strategy_content_hash"),
                    "structure_hash": exp.key_version("structure_hash"),
                },
            )
            for exp in merged.incomparable
        ],
    )
    return Envelope(
        data=view,
        source=_note(
            "mixed",
            "Recall over the experiment ledger. Match rows omit the validation report.",
        ),
    )


class ResearchNoteRequest(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=4000)
    reason: str = Field(min_length=8, max_length=500)


@router.post("/research-memory/notes", response_model=Envelope[dict])
def add_research_note(
    body: ResearchNoteRequest,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
    request: Request,
) -> Envelope[dict]:
    audit.record(
        "research_memory.note",
        actor=principal.user_id,
        actor_role=principal.role.value,
        outcome="allowed",
        reason=body.reason,
        target=body.subject,
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "",
        detail={"body": body.body[:1000]},
    )
    return Envelope(
        data={"recorded": True},
        source=_note("live", "Written to the operator audit trail."),
    )
