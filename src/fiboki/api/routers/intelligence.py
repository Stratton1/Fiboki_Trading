"""INTELLIGENCE: agent runs, the audit ledger, research memory.

The cardinal rule from :mod:`fiboki.agents` holds at the HTTP boundary too:
nothing here can cause an order. The only write is a research note, and even
that is admin-gated and audited.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Figure
from fiboki.api.security import Principal, require_admin
from fiboki.core.enums import Provenance

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


@router.get("/runs", response_model=Page[dict])
def runs(settings: SettingsDep) -> Page[dict]:
    return Page(
        items=[],
        total=0,
        source=_note(
            "absent",
            "No agent orchestrator is attached to this deployment. No run has "
            "been recorded, which is different from every run having succeeded.",
        ),
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


@router.get("/research-memory", response_model=Envelope[ResearchMemoryView])
def research_memory(settings: SettingsDep) -> Envelope[ResearchMemoryView]:
    if settings.experiment_db is None:
        view = ResearchMemoryView(
            available=False,
            detail="Research memory is backed by the experiment ledger, which is "
            "not configured. 'Have we tried this already?' cannot be answered on "
            "this deployment, so assume yes and check before running.",
            structures_recorded=Figure.missing(Provenance.BACKTEST, unit="count"),
            rediscovery_rate=Figure.missing(Provenance.BACKTEST, unit="pct"),
        )
        return Envelope(data=view, source=_note("absent", "No experiment ledger."))
    raise ApiError(
        status.HTTP_501_NOT_IMPLEMENTED,
        "research_memory_not_wired",
        "An experiment ledger is configured but recall is not exposed over HTTP yet.",
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
