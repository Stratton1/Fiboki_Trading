"""SYSTEM: services, workers, data health, broker health, queues, settings —
and the kill switch.

Three deliberate refusals live here:

* ``POST /api/system/execution-mode`` always returns 403 and enumerates the
  controls. There is no code path from HTTP to LIVE.
* The kill switch takes PAUSE and FLATTEN as distinct values with no default,
  mirroring :meth:`fiboki.risk.killswitch.KillSwitch.activate`. V1's control was
  a bare icon button with no confirmation that could not be armed in paper mode
  at all — the mode you are in when you most need to practise using it.
* Disarming requires an admin, a typed reason, and is audited whether it
  succeeds or fails.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.health import HealthReport, build_health
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, ExecutionModeBanner, Page, SourceNote
from fiboki.api.provenance import Figure
from fiboki.api.security import Principal, require_admin
from fiboki.core.enums import Provenance
from fiboki.risk.killswitch import KillSwitchMode

router = APIRouter(prefix="/api/system", tags=["system"])

AdminPrincipal = Annotated[Principal, Depends(require_admin)]


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


# ------------------------------------------------------------- models


class ServiceRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: str
    detail: str
    healthy: bool
    latency: Figure


class WorkerRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    state: str = Field(pattern="^(running|stale|never_started|stopped)$")
    heartbeat_age: Figure
    detail: str


class KillSwitchView(BaseModel):
    model_config = ConfigDict(frozen=True)

    active: bool
    mode: str | None
    operator: str | None
    reason: str | None
    since: datetime | None
    #: True in EVERY execution mode. Practising the control in paper is the
    #: point; V1 disabled it in paper and left operators to learn it live.
    armable: bool = True
    blocks_new_risk: bool
    requires_flatten: bool
    open_positions: int
    #: Enumerated for the shared confirm dialog, server-side, per mode.
    consequences: dict[str, list[str]]


class KillSwitchRequest(BaseModel):
    mode: str = Field(pattern="^(pause|flatten)$")
    """No default. The operator chooses PAUSE or FLATTEN explicitly."""
    reason: str = Field(min_length=8, max_length=500)
    """A kill switch with no recorded reason is an unexplained outage later."""
    confirm_phrase: str = ""


class KillSwitchDisarmRequest(BaseModel):
    reason: str = Field(min_length=8, max_length=500)


class BrokerHealthView(BaseModel):
    model_config = ConfigDict(frozen=True)

    configured: bool
    venue_url_host: str
    mode: str
    guard_allowed: bool
    controls: dict[str, bool]
    reasons: list[str]
    detail: str


class SettingsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_mode: str
    live_execution_compiled_in: bool
    allowed_origin_count: int
    cookie_secure: bool
    cookie_samesite: str
    session_ttl_seconds: int
    limits_version: str
    limits: dict[str, Any]
    realism_models: dict[str, str]
    data_root_configured: bool
    experiment_db_configured: bool
    build_sha: str | None


# -------------------------------------------------------------- routes


@router.get("/health", response_model=HealthReport)
def health(platform: PlatformDep, settings: SettingsDep) -> HealthReport:
    return build_health(platform, settings)


@router.get("/services", response_model=Page[ServiceRow])
def services(platform: PlatformDep) -> Page[ServiceRow]:
    rows = [
        ServiceRow(
            name=s.name,
            kind=s.kind,
            detail=s.detail,
            healthy=s.healthy,
            latency=Figure(
                value=s.latency_ms,
                provenance=Provenance.BROKER_LIVE
                if s.name == "broker"
                else Provenance.PAPER,
                unit="ms",
            ),
        )
        for s in platform.data_sources()
    ]
    return Page(
        items=rows,
        total=len(rows),
        source=_note("mixed", "Measured at request time, one probe per source."),
    )


@router.get("/workers", response_model=Page[WorkerRow])
def workers(platform: PlatformDep, settings: SettingsDep) -> Page[WorkerRow]:
    age = platform.worker_heartbeat_age_seconds()
    if age is None:
        state, detail = (
            "never_started",
            "No heartbeat file has ever been written. This is NOT the same as a "
            "worker that is running with nothing to do.",
        )
    elif age > settings.worker_heartbeat_stale_seconds:
        state, detail = ("stale", f"Last beat {age:.0f}s ago; stale threshold is "
                                  f"{settings.worker_heartbeat_stale_seconds:.0f}s.")
    else:
        state, detail = ("running", f"Last beat {age:.0f}s ago.")
    rows = [
        WorkerRow(
            name="paper_engine",
            state=state,
            heartbeat_age=Figure(
                value=age,
                provenance=settings.provenance_for_execution(),
                unit="s",
            ),
            detail=detail,
        )
    ]
    return Page(
        items=rows,
        total=len(rows),
        source=_note("live", "Heartbeat file mtime, read at request time."),
    )


@router.get("/data-health", response_model=Page[ServiceRow])
def data_health(platform: PlatformDep) -> Page[ServiceRow]:
    rows = [
        ServiceRow(
            name=s.name,
            kind=s.kind,
            detail=s.detail,
            healthy=s.healthy,
            latency=Figure(value=s.latency_ms, provenance=Provenance.PAPER, unit="ms"),
        )
        for s in platform.data_sources()
        if s.name in {"database", "market_data_store", "trade_and_position_records"}
    ]
    return Page(items=rows, total=len(rows), source=_note("mixed", "Probed at request time."))


@router.get("/broker-health", response_model=Envelope[BrokerHealthView])
def broker_health(platform: PlatformDep, settings: SettingsDep) -> Envelope[BrokerHealthView]:
    state = platform.mode_state()
    from fiboki.broker.mode_guard import parse_host

    host = parse_host(settings.venue_url)
    configured = bool(settings.venue_url)
    view = BrokerHealthView(
        configured=configured,
        venue_url_host=host,
        mode=state["mode"],
        guard_allowed=bool(state["guard_allowed"]),
        controls=dict(state["guard_controls"]),
        reasons=list(state["guard_reasons"]),
        detail=(
            "No venue is configured. In paper mode this is expected and correct: "
            "nothing should be reachable."
            if not configured
            else f"Venue host {host!r} evaluated by the mode guard."
        ),
    )
    return Envelope(data=view, source=_note("live", "fiboki.broker.mode_guard, evaluated now."))


@router.get("/execution-mode", response_model=Envelope[ExecutionModeBanner])
def execution_mode(platform: PlatformDep, settings: SettingsDep) -> Envelope[ExecutionModeBanner]:
    """The single source of truth behind the sticky banner.

    Every page reads this. V1 had 14 of 19 pages that never asked what mode
    they were in, and a bot-creation dialog that said "Paper trading only" as a
    literal regardless of the answer.
    """
    mode = settings.execution_mode
    ks = platform.kill_switch.state
    severity = "danger" if mode.touches_real_money else (
        "caution" if mode.touches_broker else "info"
    )
    headline = {
        "backtest": "BACKTEST — simulation only",
        "paper": "PAPER — simulated fills, no venue contacted",
        "shadow": "SHADOW — mirroring to a venue, orders not sent",
        "demo": "DEMO — orders are reaching a broker demo account",
        "live": "LIVE — orders are reaching a real-money account",
    }[mode.value]
    detail = {
        "backtest": "Nothing on this deployment can place an order.",
        "paper": "Fills are simulated against recorded executable prices. No "
                 "broker credential is in use.",
        "shadow": "Signals are sent to the venue's pricing but no order is "
                  "submitted. Divergence is being recorded.",
        "demo": "Real orders are submitted to a demo venue. Money is not at "
                "risk; the execution path is otherwise the live one.",
        "live": "Real orders are reaching a real-money account. Every control "
                "on this page moves real capital.",
    }[mode.value]
    banner = ExecutionModeBanner(
        mode=mode,
        provenance=settings.provenance_for_execution(),
        touches_broker=mode.touches_broker,
        touches_real_money=mode.touches_real_money,
        severity=severity,
        headline=headline,
        detail=detail,
        live_execution_compiled_in=bool(platform.mode_state()["live_compiled_in"]),
        kill_switch_active=ks.active,
        kill_switch_mode=ks.mode.value if ks.mode else None,
        as_of=datetime.now(tz=UTC),
    )
    return Envelope(data=banner, source=_note("live", "Process configuration, read at start."))


@router.post("/execution-mode", status_code=status.HTTP_403_FORBIDDEN)
def set_execution_mode(
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
    request: Request,
) -> None:
    """Always refuses. Documented, discoverable, and audited.

    An endpoint that could switch this deployment to LIVE would be a single
    reviewable string away from moving real money. It does not exist, and this
    handler exists so that asking gets an explanation rather than a 404 that
    invites someone to add one.
    """
    audit.record(
        "execution_mode.change_refused",
        actor=principal.user_id,
        actor_role=principal.role.value,
        outcome="refused",
        reason="execution mode is a deploy-time control",
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "",
    )
    raise ApiError(
        status.HTTP_403_FORBIDDEN,
        "execution_mode_is_not_an_api_surface",
        "Execution mode is a deploy-time control and cannot be changed over "
        "HTTP by any role. Reaching LIVE requires all five independent controls "
        "in fiboki.broker.mode_guard, the first of which is a source constant "
        "that needs a code change, a review and a deploy.",
        context={"controls": platform.live_controls()},
    )


@router.get("/kill-switch", response_model=Envelope[KillSwitchView])
def kill_switch(platform: PlatformDep, settings: SettingsDep) -> Envelope[KillSwitchView]:
    state = platform.kill_switch.state
    open_positions = len(platform.positions())
    mode = settings.execution_mode.value
    consequences = {
        "pause": [
            "No new positions will be opened and no position will be increased.",
            "Reducing, closing and protective stop amendments stay permitted.",
            "Open positions are LEFT OPEN and keep their market exposure.",
            f"{open_positions} position(s) are currently open in {mode} mode.",
            "Strategy evaluation continues; only risk-adding orders are refused.",
        ],
        "flatten": [
            "No new positions, and every open position is queued to be closed.",
            f"{open_positions} closing intent(s) will be produced, ordered "
            "deterministically so a restart mid-flatten resumes in sequence.",
            "Closing at market accepts whatever spread is available now.",
            "This cannot be downgraded to PAUSE; re-arming requires an explicit "
            "second operator action.",
            f"In {mode} mode these closes are "
            + (
                "simulated, not sent to a venue."
                if not settings.execution_mode.touches_broker
                else "submitted to the venue."
            ),
        ],
    }
    view = KillSwitchView(
        active=state.active,
        mode=state.mode.value if state.mode else None,
        operator=state.operator,
        reason=state.reason,
        since=state.since.to_pydatetime() if state.since is not None else None,
        armable=True,
        blocks_new_risk=state.blocks_new_risk,
        requires_flatten=state.requires_flatten,
        open_positions=open_positions,
        consequences=consequences,
    )
    return Envelope(
        data=view,
        source=_note("live", f"Replayed from {settings.killswitch_path.name}."),
    )


@router.post("/kill-switch/arm", response_model=Envelope[KillSwitchView])
def arm_kill_switch(
    body: KillSwitchRequest,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
    request: Request,
) -> Envelope[KillSwitchView]:
    mode = KillSwitchMode(body.mode)
    try:
        platform.kill_switch.activate(
            mode,
            operator=principal.user_id,
            reason=body.reason,
            at=pd.Timestamp.now(tz="UTC"),
            positions_open=len(platform.positions()),
            extra={"correlation_id": current_correlation_id()},
        )
    except ValueError as exc:
        audit.record(
            "killswitch.arm",
            actor=principal.user_id,
            actor_role=principal.role.value,
            outcome="failed",
            reason=body.reason,
            target=body.mode,
            execution_mode=settings.execution_mode.value,
            correlation_id=current_correlation_id(),
            source_ip=request.client.host if request.client else "",
            detail={"error": str(exc)},
        )
        raise ApiError(
            status.HTTP_409_CONFLICT, "kill_switch_transition_refused", str(exc)
        ) from exc

    audit.record(
        "killswitch.arm",
        actor=principal.user_id,
        actor_role=principal.role.value,
        outcome="allowed",
        reason=body.reason,
        target=body.mode,
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "",
        detail={"positions_open": len(platform.positions())},
    )
    return kill_switch(platform, settings)


@router.post("/kill-switch/disarm", response_model=Envelope[KillSwitchView])
def disarm_kill_switch(
    body: KillSwitchDisarmRequest,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
    request: Request,
) -> Envelope[KillSwitchView]:
    try:
        platform.kill_switch.deactivate(
            operator=principal.user_id,
            reason=body.reason,
            extra={"correlation_id": current_correlation_id()},
        )
    except ValueError as exc:
        audit.record(
            "killswitch.disarm",
            actor=principal.user_id,
            actor_role=principal.role.value,
            outcome="failed",
            reason=body.reason,
            execution_mode=settings.execution_mode.value,
            correlation_id=current_correlation_id(),
            source_ip=request.client.host if request.client else "",
            detail={"error": str(exc)},
        )
        raise ApiError(
            status.HTTP_409_CONFLICT, "kill_switch_not_active", str(exc)
        ) from exc
    audit.record(
        "killswitch.disarm",
        actor=principal.user_id,
        actor_role=principal.role.value,
        outcome="allowed",
        reason=body.reason,
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "",
    )
    return kill_switch(platform, settings)


@router.get("/kill-switch/history", response_model=Page[dict])
def kill_switch_history(platform: PlatformDep, limit: int = Query(50, le=500)) -> Page[dict]:
    events = platform.kill_switch.history()[-limit:][::-1]
    items = [
        {
            "action": e.action,
            "mode": e.mode.value if e.mode else None,
            "operator": e.operator,
            "reason": e.reason,
            "at": e.at.isoformat(),
            "positions_open": e.positions_open,
        }
        for e in events
    ]
    return Page(
        items=items,
        total=len(items),
        limit=limit,
        source=_note("live", "Append-only kill-switch journal."),
    )


@router.get("/settings", response_model=Envelope[SettingsView])
def settings_view(platform: PlatformDep, settings: SettingsDep) -> Envelope[SettingsView]:
    view = SettingsView(
        execution_mode=settings.execution_mode.value,
        live_execution_compiled_in=bool(platform.mode_state()["live_compiled_in"]),
        allowed_origin_count=len(settings.allowed_origins),
        cookie_secure=settings.cookie_secure,
        cookie_samesite=settings.cookie_samesite,
        session_ttl_seconds=settings.session_ttl_seconds,
        limits_version=platform.limits.version,
        limits=platform.limits.as_dict(),
        realism_models={
            "slippage": settings.slippage_model,
            "spread": settings.spread_model,
            "financing": settings.financing_model,
            "fx_conversion": settings.fx_conversion_model,
        },
        data_root_configured=settings.data_root is not None,
        experiment_db_configured=settings.experiment_db is not None,
        build_sha=settings.build_sha or None,
    )
    return Envelope(data=view, source=_note("live", "Process configuration."))


@router.get("/queues", response_model=Page[dict])
def queues(platform: PlatformDep) -> Page[dict]:
    """Queue depths. Reported as unavailable rather than as zero when absent.

    A zero-depth queue and an unreachable orchestrator are opposite facts. V1
    rendered both as "0".
    """
    rows = [
        {
            "name": name,
            "depth": None,
            "available": False,
            "detail": "No orchestrator is attached to this deployment, so queue "
                      "depth is unknown. This is not a depth of zero.",
        }
        for name in ("research", "backtest", "validation", "execution")
    ]
    return Page(items=rows, total=len(rows), source=_note("absent", "No orchestrator attached."))
