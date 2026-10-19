"""OPS: per-bot controls, the paper audit chain, reconciliation, maturity and advisory reports.

Wave 3O. Every judgement on these screens is made HERE (or in :mod:`fiboki.ops`
which this calls); the workstation renders what it is sent.

Controls (``/api/trading/controls``, ``/api/trading/sessions/{id}/control``)
    A bot's pause / resume / drain state, replayed from the control ledger the
    paper runtime reads every cycle. Requesting a change is admin-only (as the
    kill switch is), needs a reason, and is recorded in the API audit trail
    whether it is allowed, refused or fails. The paper runtime records the same
    act in its own hash-chained audit log when it applies it.

Nothing here places, sizes or closes an order, changes a limit or a mode.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure
from fiboki.api.routers.trading import TradeRowView, _record_caveats, _trade_view
from fiboki.api.security import Principal, require_admin, require_operator
from fiboki.core.enums import Provenance
from fiboki.ops.audit_chain import CHAIN_FILE, ChainVerification, PaperAuditChain
from fiboki.ops.controls import (
    REASON_MAX,
    REASON_MIN,
    BotControlLedger,
    BotControlState,
    ControlRecord,
    ControlRefused,
)
from fiboki.ops.jobs import OpsJob, OpsJobQueue
from fiboki.ops.reconcile import EXPLAINED as _EXPLAINED

router = APIRouter(prefix="/api/trading", tags=["ops"])

AdminPrincipal = Annotated[Principal, Depends(require_admin)]

__all__ = ["BotControlView", "ControlRecordView", "control_view", "router"]


def _note(kind: str, detail: str, as_of: datetime | None = None) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=as_of or datetime.now(tz=UTC))


def _controls_ledger(settings: Any) -> BotControlLedger:
    return BotControlLedger(settings.paths.bot_controls)


def _forward_sessions(platform: Any) -> dict[str, Any]:
    """Forward paper sessions (the only bots a control can bind), by id."""
    journal = platform.journal
    if journal is None:
        return {}
    out: dict[str, Any] = {}
    for session in journal.sessions:
        summary = platform.session_summary(session.session_id) or {}
        if session.session_id.startswith("forward-") or "forward" in str(summary.get("source", "")):
            out[session.session_id] = session
    return out


def ledger_dir_for(platform: Any, settings: Any, session_id: str) -> Path | None:
    """The runtime ledger directory of a forward session, or ``None``.

    ``summary.json`` names it (``ledger_dir``, relative to the state directory)
    since Wave 3O; an older summary is resolved by the committed convention
    ``paper_forward/<wiring_version>``. Never a path outside the state directory.
    """
    summary = platform.session_summary(session_id)
    if summary is None:
        return None
    state = Path(settings.state_dir).resolve()
    raw = summary.get("ledger_dir")
    if not raw:
        version = summary.get("wiring_version")
        if not version:
            return None
        raw = f"paper_forward/{version}"
    candidate = Path(str(raw))
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (state / candidate).resolve()
    if state not in resolved.parents:
        return None
    return resolved


# ------------------------------------------------------------------ models


class ControlRecordView(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: str
    state_before: str
    state_after: str
    actor: str
    actor_role: str
    reason: str
    at: datetime


class BotControlView(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    state: str = Field(pattern="^(active|paused|draining|stopped)$")
    #: True when the runtime will not open a new position for this bot.
    blocks_entries: bool
    #: What the state means for this bot, in one sentence, from the platform.
    meaning: str
    since: datetime | None
    actor: str | None
    reason: str | None
    allowed_actions: list[str]
    #: False for a session no control can bind (a replay, or an unknown id).
    controllable: bool
    #: What each allowed action will do to THIS bot, computed here.
    consequences: dict[str, list[str]]
    history: list[ControlRecordView]


class ControlRequest(BaseModel):
    action: str = Field(pattern="^(pause|resume|drain)$")
    """No default: the operator names the act."""
    reason: str = Field(min_length=REASON_MIN, max_length=REASON_MAX)


_MEANING = {
    "active": "Trading as its wiring says: new entries are evaluated and may be opened.",
    "paused": (
        "No new entries. Open positions keep their stops, trails, targets and time "
        "stops; nothing is closed by the pause."
    ),
    "draining": (
        "No new entries; open positions close by their own rules, then the bot "
        "stops by itself."
    ),
    "stopped": "Drained to flat. No new entries until it is resumed.",
}


def _record_view(record: ControlRecord) -> ControlRecordView:
    return ControlRecordView(
        action=record.action.value,
        state_before=record.state_before.value,
        state_after=record.state_after.value,
        actor=record.actor,
        actor_role=record.actor_role,
        reason=record.reason,
        at=record.at,
    )


def _consequences(allowed: list[str], open_positions: int | None) -> dict[str, list[str]]:
    held = (
        "The number of open positions is not known to the API."
        if open_positions is None
        else f"{open_positions} open position(s) stay open and keep their exits."
    )
    out: dict[str, list[str]] = {}
    for action in allowed:
        if action == "pause":
            out[action] = [
                "The paper runtime opens no new position for this bot from its next cycle.",
                held,
                "Signals the pause suppresses are journalled; nothing is closed.",
            ]
        elif action == "drain":
            out[action] = [
                "No new position; open ones close only by their own stop, trail, target or time rule.",
                held,
                "When the book is flat the runtime marks the bot STOPPED by itself.",
            ]
        elif action == "resume":
            out[action] = [
                "New entries are evaluated and may be opened from the next cycle.",
                "The kill switch, the risk gateway and every limit still apply.",
            ]
    return out


def control_view(
    session_id: str,
    state: BotControlState,
    records: list[ControlRecord],
    *,
    controllable: bool,
    open_positions: int | None = None,
) -> BotControlView:
    last = state.last
    allowed = state.allowed_actions() if controllable else []
    return BotControlView(
        session_id=session_id,
        state=state.state.value,
        blocks_entries=state.blocks_entries,
        meaning=_MEANING[state.state.value],
        since=None if last is None else last.at,
        actor=None if last is None else last.actor,
        reason=None if last is None else last.reason,
        allowed_actions=allowed,
        controllable=controllable,
        consequences=_consequences(allowed, open_positions),
        history=[_record_view(r) for r in reversed(records[-50:])],
    )


# ------------------------------------------------------------------ routes


@router.get("/controls", response_model=Page[BotControlView])
def controls(platform: PlatformDep, settings: SettingsDep) -> Page[BotControlView]:
    """The control state of every forward paper bot (and of any id the ledger names)."""
    ledger = _controls_ledger(settings)
    forward = _forward_sessions(platform)
    states = ledger.states()
    ids = sorted(set(forward) | set(states))
    items = []
    for sid in ids:
        state = states.get(sid) or ledger.state(sid)
        session = forward.get(sid)
        items.append(
            control_view(
                sid, state, ledger.records(sid), controllable=session is not None,
                open_positions=None if session is None else len(session.positions),
            )
        )
    return Page(
        items=items,
        total=len(items),
        limit=max(1, len(items)),
        source=_note(
            "live" if ledger.path.exists() else "absent",
            f"Replayed from {ledger.path.name}; the paper runtime reads the same file every cycle."
            if ledger.path.exists()
            else f"No control ledger at {ledger.path}: no bot has ever been paused or drained, "
            "so every bot is active.",
        ),
    )


@router.get("/sessions/{session_id}/control", response_model=Envelope[BotControlView])
def session_control(
    session_id: str, platform: PlatformDep, settings: SettingsDep
) -> Envelope[BotControlView]:
    ledger = _controls_ledger(settings)
    forward = _forward_sessions(platform)
    records = ledger.records(session_id)
    if session_id not in forward and not records:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_session",
            f"No forward paper session {session_id!r} and no control record for it.",
        )
    session = forward.get(session_id)
    view = control_view(
        session_id, ledger.state(session_id), records, controllable=session is not None,
        open_positions=None if session is None else len(session.positions),
    )
    return Envelope(data=view, source=_note("live", f"Replayed from {ledger.path.name}."))


@router.post("/sessions/{session_id}/control", response_model=Envelope[BotControlView])
def request_control(
    session_id: str,
    body: ControlRequest,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
    request: Request,
) -> Envelope[BotControlView]:
    """Pause, resume or drain ONE paper bot. Admin, a reason, and audited either way."""
    ledger = _controls_ledger(settings)
    action = f"bot.{body.action}"
    common = {
        "actor": principal.user_id,
        "actor_role": principal.role.value,
        "reason": body.reason,
        "target": session_id,
        "execution_mode": settings.execution_mode.value,
        "correlation_id": current_correlation_id(),
        "source_ip": request.client.host if request.client else "",
    }
    if session_id not in _forward_sessions(platform):
        audit.record(action, outcome="refused", detail={"error": "not a forward paper session"}, **common)
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_session",
            f"{session_id!r} is not a forward paper session; only a running paper bot "
            "can be paused, resumed or drained (a replay has already ended).",
        )
    try:
        record = ledger.request(
            session_id,
            body.action,
            actor=principal.user_id,
            actor_role=principal.role.value,
            reason=body.reason,
            correlation_id=current_correlation_id(),
        )
    except ControlRefused as exc:
        audit.record(
            action, outcome="refused", detail={"error": str(exc), "state": exc.state.value}, **common
        )
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "control_transition_refused",
            str(exc),
            context={"state": exc.state.value, "action": exc.action},
        ) from exc
    except (ValueError, OSError) as exc:
        audit.record(action, outcome="failed", detail={"error": str(exc)}, **common)
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY if isinstance(exc, ValueError) else 503,
            "control_not_recorded",
            str(exc),
        ) from exc
    audit.record(
        action,
        outcome="allowed",
        detail={"state_before": record.state_before.value, "state_after": record.state_after.value},
        **common,
    )
    return session_control(session_id, platform, settings)


# =================================================================== audit chain


#: (path, size, mtime_ns) -> verification. A chain is re-walked only when it changed.
_VERIFIED: dict[str, tuple[tuple[int, int] | None, ChainVerification]] = {}


def _verify_cached(chain: PaperAuditChain) -> ChainVerification:
    sig = chain.signature()
    key = str(chain.path)
    hit = _VERIFIED.get(key)
    if hit is not None and hit[0] == sig and sig is not None:
        return hit[1]
    result = chain.verify()
    _VERIFIED[key] = (sig, result)
    return result


class ChainIntegrityView(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    file: str
    present: bool
    intact: bool
    #: ``ok`` (verifies), ``broken`` (a row does not verify), ``absent`` (no chain).
    status: str = Field(pattern="^(ok|broken|absent)$")
    rows: Figure
    first_broken_seq: int | None
    reason: str
    head_hash: str | None
    first_at: str | None
    last_at: str | None
    kinds: dict[str, int]
    verified_at: datetime


class ChainRowView(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    at: str
    kind: str
    summary: str
    hash: str
    prev_hash: str


class ChainTailView(BaseModel):
    model_config = ConfigDict(frozen=True)

    integrity: ChainIntegrityView
    rows: list[ChainRowView]


def _integrity(session_id: str, chain: PaperAuditChain) -> ChainIntegrityView:
    result = _verify_cached(chain)
    status_ = "absent" if not result.present else ("ok" if result.intact else "broken")
    return ChainIntegrityView(
        session_id=session_id,
        file=chain.path.name,
        present=result.present,
        intact=result.intact,
        status=status_,
        rows=Figure(value=float(result.rows), provenance=Provenance.PAPER, unit="count"),
        first_broken_seq=result.first_broken_seq,
        reason=result.reason,
        head_hash=result.head_hash,
        first_at=result.first_at,
        last_at=result.last_at,
        kinds=dict(result.kinds),
        verified_at=datetime.now(tz=UTC),
    )


def _summary(row: dict[str, Any]) -> str:
    p = row.get("payload") or {}
    kind = row.get("kind")
    if kind == "risk_decision":
        decision = p.get("decision") or {}
        reasons = decision.get("reasons") or []
        verdict = "allowed" if decision.get("allowed") else "blocked"
        return f"{p.get('instrument', '')} {verdict}" + (f": {', '.join(map(str, reasons))}" if reasons else "")
    if kind == "order_intent":
        return f"{p.get('instrument', '')} {p.get('direction', '')} {p.get('size', '')} {p.get('state', '')}".strip()
    if kind == "venue_fill":
        return f"{p.get('instrument', '')} {p.get('direction', '')} {p.get('size', '')} @ {p.get('price', '')}"
    if kind == "trade_closed":
        return f"{p.get('instrument', '')} {p.get('exit_reason', '')} net {p.get('net_pnl', '')}"
    if kind == "control_applied":
        return f"{p.get('action', '')} by {p.get('actor', '')}: {p.get('reason', '')}"
    if kind == "venue_instruction":
        t = p.get("telemetry") or {}
        return f"{t.get('instrument', '')} {t.get('kind', '')} applied={t.get('applied')}"
    return ", ".join(f"{k}={p[k]}" for k in ("instrument", "reason", "state") if k in p)[:200]


def _chain_for(platform: Any, settings: Any, session_id: str) -> PaperAuditChain | None:
    directory = ledger_dir_for(platform, settings, session_id)
    if directory is None:
        return None
    return PaperAuditChain(directory / CHAIN_FILE, session_id=session_id)


@router.get("/audit-chain", response_model=Page[ChainIntegrityView])
def audit_chains(platform: PlatformDep, settings: SettingsDep) -> Page[ChainIntegrityView]:
    """Every forward bot's audit chain, verified from genesis (re-walked when it changed)."""
    items: list[ChainIntegrityView] = []
    for sid in sorted(_forward_sessions(platform)):
        chain = _chain_for(platform, settings, sid)
        if chain is not None:
            items.append(_integrity(sid, chain))
    broken = [i.session_id for i in items if i.status == "broken"]
    return Page(
        items=items,
        total=len(items),
        limit=max(1, len(items)),
        source=_note(
            "live" if items else "absent",
            (f"{len(items)} chain(s) walked from genesis by the API; "
             + (f"BROKEN: {', '.join(broken)}." if broken else "every present chain verifies."))
            if items
            else "No forward paper session, so there is no audit chain to verify.",
        ),
    )


@router.get("/sessions/{session_id}/audit-chain", response_model=Envelope[ChainTailView])
def session_audit_chain(
    session_id: str, platform: PlatformDep, settings: SettingsDep, limit: int = 100
) -> Envelope[ChainTailView]:
    chain = _chain_for(platform, settings, session_id)
    if chain is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_session",
                       f"No forward paper session {session_id!r} with a ledger directory.")
    integrity = _integrity(session_id, chain)
    rows = [
        ChainRowView(
            seq=int(r.get("seq", 0)), at=str(r.get("at", "")), kind=str(r.get("kind", "")),
            summary=_summary(r), hash=str(r.get("hash", "")), prev_hash=str(r.get("prev_hash", "")),
        )
        for r in (chain.tail(max(1, min(int(limit), 500))) if integrity.present else [])
    ]
    return Envelope(
        data=ChainTailView(integrity=integrity, rows=rows),
        source=_note("live" if integrity.present else "absent", integrity.reason),
    )


# ================================================================ reconciliation


OperatorPrincipal = Annotated[Principal, Depends(require_operator)]


class OpsJobView(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    kind: str
    status: str = Field(pattern="^(queued|running|succeeded|failed)$")
    requested_by: str
    reason: str
    requested_at: datetime
    updated_at: datetime
    error: str
    #: How the job will run: the API never runs it (``fiboki ops jobs`` does).
    runner_note: str


class DivergenceView(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = Field(pattern="^(field|paper_only|backtest_only)$")
    instrument: str
    direction: str
    entry_time: datetime
    field: str
    paper: Figure | None
    backtest: Figure | None
    paper_text: str | None
    backtest_text: str | None
    delta: Figure | None
    reason: str
    explained: bool
    detail: str


class ReconciliationReportView(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = Field(pattern="^(match|diverged|not_evaluated)$")
    status_reason: str
    window_start: datetime | None
    window_end: datetime | None
    paper_trades: Figure
    backtest_trades: Figure
    matched: Figure
    matched_identical: Figure
    paper_only: Figure
    backtest_only: Figure
    outside_window: Figure
    explained: Figure
    unexplained: Figure
    first_divergence_at: datetime | None
    divergences: list[DivergenceView]
    reasons: dict[str, int]
    approximations: list[str]
    provenance: dict[str, Any]
    digest: str
    generated_at: datetime | None
    job_id: str | None


class ReconciliationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    job: OpsJobView | None
    report: ReconciliationReportView | None


class ReconcileRequest(BaseModel):
    reason: str = Field(min_length=REASON_MIN, max_length=REASON_MAX)


_RUNNER_NOTE = (
    "Run by `fiboki ops jobs` (a separate process; the API never runs a backtest). "
    "Until it runs, the job stays queued."
)


def _job_view(job: OpsJob) -> OpsJobView:
    return OpsJobView(
        job_id=job.job_id, kind=job.kind, status=job.status, requested_by=job.requested_by,
        reason=job.reason, requested_at=datetime.fromisoformat(job.requested_at),
        updated_at=datetime.fromisoformat(job.updated_at), error=job.error, runner_note=_RUNNER_NOTE,
    )


def _count(n: Any, p: Provenance = Provenance.PAPER) -> Figure:
    return Figure(value=float(n or 0), provenance=p, unit="count")


def _value(v: Any, p: Provenance, unit: str) -> tuple[Figure | None, str | None]:
    if v is None:
        return None, None
    if isinstance(v, int | float):
        return Figure(value=float(v), provenance=p, unit=unit), None
    return None, str(v)


_FIELD_UNITS = {"size": "units", "entry_price": "", "exit_price": ""}


def report_view(body: dict[str, Any]) -> ReconciliationReportView:
    r = body["report"]
    ccy = str((r.get("provenance") or {}).get("account_ccy") or "")
    divs: list[DivergenceView] = []
    reasons: dict[str, int] = {}
    for d in r.get("divergences") or []:
        unit = _FIELD_UNITS.get(d["field"], ccy)
        pf, pt = _value(d.get("paper"), Provenance.PAPER, unit)
        bf, btx = _value(d.get("backtest"), Provenance.BACKTEST, unit)
        delta = d.get("delta")
        reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        divs.append(DivergenceView(
            kind=d["kind"], instrument=d["instrument"], direction=d["direction"],
            entry_time=datetime.fromisoformat(d["entry_time"]), field=d["field"],
            paper=pf, backtest=bf, paper_text=pt, backtest_text=btx,
            delta=None if delta is None else Figure(value=float(delta), provenance=Provenance.PAPER, unit=unit,
                                                    estimated=True),
            reason=d["reason"], explained=d["reason"] in _EXPLAINED, detail=d["detail"],
        ))

    def dt(v: Any) -> datetime | None:
        return None if not v else datetime.fromisoformat(str(v))

    return ReconciliationReportView(
        status=r["status"], status_reason=r["status_reason"],
        window_start=dt(r.get("window_start")), window_end=dt(r.get("window_end")),
        paper_trades=_count(r.get("paper_trades")),
        backtest_trades=_count(r.get("backtest_trades"), Provenance.BACKTEST),
        matched=_count(r.get("matched")), matched_identical=_count(r.get("matched_identical")),
        paper_only=_count(r.get("paper_only")),
        backtest_only=_count(r.get("backtest_only"), Provenance.BACKTEST),
        outside_window=_count(r.get("outside_window")),
        explained=_count(r.get("explained")), unexplained=_count(r.get("unexplained")),
        first_divergence_at=dt(r.get("first_divergence_at")),
        divergences=divs, reasons=reasons,
        approximations=list(r.get("approximations") or []),
        provenance=dict(r.get("provenance") or {}),
        digest=str(body.get("digest", "")), generated_at=dt(body.get("generated_at")),
        job_id=body.get("job_id"),
    )




@router.get("/sessions/{session_id}/reconciliation", response_model=Envelope[ReconciliationView])
def session_reconciliation(
    session_id: str, platform: PlatformDep, settings: SettingsDep
) -> Envelope[ReconciliationView]:
    if session_id not in _forward_sessions(platform):
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_session",
                       f"No forward paper session {session_id!r}: only a forward session has a paper "
                       "record to reconcile.")
    queue = OpsJobQueue(settings.paths.ops_dir)
    jobs = queue.for_session(session_id)
    body = queue.latest_result(session_id)
    report = report_view(body) if body else None
    view = ReconciliationView(session_id=session_id, job=_job_view(jobs[-1]) if jobs else None, report=report)
    if report is None:
        detail = ("No reconciliation has run for this session. Request one; `fiboki ops jobs` runs it."
                  if not jobs else f"Latest job {jobs[-1].job_id} is {jobs[-1].status}. " + _RUNNER_NOTE)
        return Envelope(data=view, source=_note("absent", detail))
    return Envelope(
        data=view,
        source=_note("live", f"Report {report.job_id} ({report.digest[:12]}), written by the ops job runner.",
                     report.generated_at),
    )


@router.post("/sessions/{session_id}/reconciliation", response_model=Envelope[ReconciliationView],
             status_code=status.HTTP_202_ACCEPTED)
def request_reconciliation(
    session_id: str,
    body: ReconcileRequest,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
    request: Request,
) -> Envelope[ReconciliationView]:
    """Queue a reconciliation. Recorded and audited; run by a separate process."""
    if session_id not in _forward_sessions(platform):
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_session", f"No forward paper session {session_id!r}.")
    job = OpsJobQueue(settings.paths.ops_dir).request(
        "reconcile", session_id, requested_by=principal.user_id, reason=body.reason
    )
    audit.record(
        "ops.reconcile.request", actor=principal.user_id, actor_role=principal.role.value,
        outcome="allowed", reason=body.reason, target=session_id,
        execution_mode=settings.execution_mode.value, correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "", detail={"job_id": job.job_id},
    )
    return session_reconciliation(session_id, platform, settings)


# ===================================================================== maturity


class MaturityItemView(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    status: str = Field(pattern="^(pass|fail|not_evaluated)$")
    value: Figure
    threshold: Figure
    detail: str


class DrawdownBandView(BaseModel):
    model_config = ConfigDict(frozen=True)

    horizon_trades: int
    source_trades: int
    n_paths: int
    seed: int
    risk_fraction: float
    p5: Figure
    p50: Figure
    p95: Figure
    source: str


class MaturityView(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    criteria_version: str
    #: True only when every item passes. ADVISORY: nothing reads it.
    ready: bool
    verdict: str = Field(pattern="^(ready_to_consider|not_ready|not_evaluated)$")
    items: list[MaturityItemView]
    band: DrawdownBandView | None
    paper_max_drawdown_pct: Figure
    note: str
    notes: list[str]


@router.get("/sessions/{session_id}/maturity", response_model=Envelope[MaturityView])
def session_maturity(
    session_id: str, platform: PlatformDep, settings: SettingsDep
) -> Envelope[MaturityView]:
    """The advisory 'ready to consider for IG demo' checklist. It enables nothing."""
    from fiboki.api.ops_sources import reference_backtest, session_span
    from fiboki.ops.maturity import maturity_report

    session = _forward_sessions(platform).get(session_id)
    if session is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_session", f"No forward paper session {session_id!r}.")
    summary = platform.session_summary(session_id) or {}
    ledger = ledger_dir_for(platform, settings, session_id)
    first, last = session_span(ledger)
    days = None if first is None or last is None else (last - first).total_seconds() / 86_400.0
    ref = reference_backtest(settings, session.strategy_id)
    paper = [
        {"instrument": t.instrument, "size": t.size, "net_pnl": t.net_pnl, "costs": t.costs,
         "exit_time": t.exit_time.isoformat()}
        for t in session.trades
    ]
    body = OpsJobQueue(settings.paths.ops_dir).latest_result(session_id)
    chain = _chain_for(platform, settings, session_id)
    report = maturity_report(
        paper_trades=paper,
        initial_balance=session.initial_balance,
        risk_fraction=summary.get("risk_fraction"),
        days_live=days,
        backtest_r_multiples=ref.r_multiples or None,
        backtest_trades=ref.trades or None,
        backtest_label=ref.label if ref.available else (ref.reason or ref.label),
        reconciliation=(body or {}).get("report"),
        latest_paper_trade_at=max((p["exit_time"] for p in paper), default=None),
        chain=None if chain is None else _verify_cached(chain).as_dict(),
    )
    p = Provenance.PAPER
    unit_of = {"count": "count", "days": "days", "pct": "pct", "ratio": "ratio"}

    def fig(v: float | None, unit: str, prov: Provenance = p, reason: str = "") -> Figure:
        return Figure(value=v, provenance=prov, unit=unit) if v is not None else Figure.missing(prov, unit=unit, reason=reason)

    items = [
        MaturityItemView(
            key=i.key, label=i.label, status=i.status,
            value=fig(i.value, unit_of.get(i.unit, i.unit), reason=i.detail),
            threshold=fig(i.threshold, unit_of.get(i.unit, i.unit), Provenance.BACKTEST if i.key == "drawdown_in_band" else p,
                          reason="No threshold for this item."),
            detail=i.detail,
        )
        for i in report.items
    ]
    band = None
    if report.band is not None:
        b = report.band
        band = DrawdownBandView(
            horizon_trades=b.horizon_trades, source_trades=b.source_trades, n_paths=b.n_paths, seed=b.seed,
            risk_fraction=b.risk_fraction,
            p5=Figure(value=round(b.p5, 3), provenance=Provenance.BACKTEST, unit="pct", estimated=True),
            p50=Figure(value=round(b.p50, 3), provenance=Provenance.BACKTEST, unit="pct", estimated=True),
            p95=Figure(value=round(b.p95, 3), provenance=Provenance.BACKTEST, unit="pct", estimated=True),
            source=ref.label,
        )
    view = MaturityView(
        session_id=session_id, criteria_version=report.criteria.version, ready=report.ready,
        verdict=report.verdict, items=items, band=band,
        paper_max_drawdown_pct=fig(None if report.paper_max_drawdown_pct is None else round(report.paper_max_drawdown_pct, 3),
                                   "pct", reason="The session's initial balance is not known."),
        note=report.note, notes=list(report.notes),
    )
    return Envelope(
        data=view,
        source=_note("live", f"Paper session {session_id}; reference {ref.label}; criteria "
                     f"{report.criteria.version} (advisory)."),
    )


# ============================================================ self-correlation

_NON_PAPER = {Provenance.BACKTEST, Provenance.OUT_OF_SAMPLE, Provenance.WALKFORWARD, Provenance.HOLDOUT}


def _strategy_returns(platform: Any, settings: Any, strategy_id: str, cache: dict[str, Any]) -> tuple[Any, str, Provenance]:
    """A strategy's reference daily returns: its latest current stored backtest,
    else (the seed deployment) its non-paper journal trades. ``(series, label, provenance)``."""
    import pandas as pd

    from fiboki.api.ops_sources import reference_backtest
    from fiboki.ops.analytics import daily_returns_from_trades

    if strategy_id in cache:
        return cache[strategy_id]
    ref = reference_backtest(settings, strategy_id)
    if ref.available:
        out = (daily_returns_from_trades(ref.trades, ref.initial_equity or 10_000.0), ref.label, Provenance.BACKTEST)
    else:
        rows, _ = platform.trades(strategy_id=strategy_id, provenance=_NON_PAPER, limit=100_000)
        trades = [{"exit_time": t.exit_time, "net_pnl": t.net_pnl} for t in rows]
        prov = rows[0].provenance if rows else Provenance.BACKTEST
        out = (
            daily_returns_from_trades(trades, 10_000.0) if trades else pd.Series([], dtype=float),
            f"{len(rows)} {prov.value} journal trade(s)" if rows else (ref.reason or "no trade record"),
            prov,
        )
    cache[strategy_id] = out
    return out


class SelfCorrelationCellView(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    bot_strategy_id: str
    #: ``backtest``: the candidate's backtest against the bot strategy's
    #: backtest; ``paper``: against the bot's own paper returns.
    basis: str = Field(pattern="^(backtest|paper)$")
    rho: Figure
    n_common: int
    flagged: bool
    same_strategy: bool


class CandidateCorrelationRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    source: str
    cells: list[SelfCorrelationCellView]
    max_abs_rho: Figure
    flagged: bool
    flag_reason: str


class SelfCorrelationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    threshold: float
    min_common_days: int
    bots: list[str]
    rows: list[CandidateCorrelationRow]
    method: str


@router.get("/candidates/correlation", response_model=Envelope[SelfCorrelationView])
def candidate_self_correlation(platform: PlatformDep, settings: SettingsDep) -> Envelope[SelfCorrelationView]:
    """Each candidate's daily-return correlation with each paper bot. Advisory flag above 0.7."""
    from fiboki.ops.analytics import (
        MIN_COMMON_DAYS,
        SELF_CORRELATION_FLAG,
        daily_returns_from_trades,
        pairwise,
    )

    cache: dict[str, Any] = {}
    bots = _forward_sessions(platform)
    bot_paper = {
        sid: daily_returns_from_trades(
            [{"exit_time": t.exit_time, "net_pnl": t.net_pnl} for t in s.trades], s.initial_balance
        )
        for sid, s in bots.items()
    }
    rows: list[CandidateCorrelationRow] = []
    for doc in platform.strategy_documents():
        cand, label, cprov = _strategy_returns(platform, settings, doc.strategy_id, cache)
        cells: list[SelfCorrelationCellView] = []
        for sid in sorted(bots):
            bot_strategy = bots[sid].strategy_id
            same = bot_strategy == doc.strategy_id
            bt, _bl, bprov = _strategy_returns(platform, settings, bot_strategy, cache)
            for basis, other, prov in (("backtest", bt, cprov), ("paper", bot_paper[sid], Provenance.PAPER)):
                pair = pairwise(cand, other)
                rho = (
                    Figure(value=round(pair.rho, 4), provenance=prov, unit="ratio", sample_size=pair.n_common)
                    if pair.rho is not None
                    else Figure.missing(prov, unit="ratio", reason=f"{pair.n_common} common day(s); "
                                        f"{MIN_COMMON_DAYS} with dispersion on both sides are needed.")
                )
                cells.append(SelfCorrelationCellView(
                    session_id=sid, bot_strategy_id=bot_strategy, basis=basis, rho=rho, n_common=pair.n_common,
                    flagged=bool(pair.rho is not None and abs(pair.rho) > SELF_CORRELATION_FLAG and not same),
                    same_strategy=same,
                ))
        measured = [abs(c.rho.value) for c in cells if c.rho.value is not None and not c.same_strategy]
        worst = max(measured) if measured else None
        flagged = any(c.flagged for c in cells)
        top = max((c for c in cells if c.flagged), key=lambda c: abs(c.rho.value or 0), default=None)
        rows.append(CandidateCorrelationRow(
            strategy_id=doc.strategy_id, source=label, cells=cells,
            max_abs_rho=Figure(value=round(worst, 4), provenance=cprov, unit="ratio")
            if worst is not None else Figure.missing(cprov, unit="ratio", reason="No measurable pair."),
            flagged=flagged,
            flag_reason=(
                f"|rho| {abs(top.rho.value or 0):.2f} with {top.session_id} ({top.basis} basis) is above "
                f"{SELF_CORRELATION_FLAG}: it would largely re-buy risk the fleet already holds."
                if top is not None else
                ("No pair above the threshold." if measured else "Not measurable: no overlap with the fleet.")
            ),
        ))
    view = SelfCorrelationView(
        threshold=SELF_CORRELATION_FLAG, min_common_days=MIN_COMMON_DAYS, bots=sorted(bots), rows=rows,
        method=(
            "Pearson correlation of daily returns of the closed-trade balance curve (17:00 New York days, "
            "zero on days without an exit), on common days only. Candidate returns are its latest current "
            "stored backtest; each bot is compared on its strategy's backtest and on its own paper record. "
            "Advisory: nothing reads the flag."
        ),
    )
    return Envelope(data=view, source=_note("live" if rows else "absent",
                                            f"{len(rows)} candidate(s) against {len(bots)} paper bot(s)."))


# ============================================================ risk calculator



class SizeRowView(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument: str
    entry: Figure
    stop: Figure
    stop_pips: float
    size: Figure
    risk_amount: Figure
    notional: Figure
    leverage_cap: Figure
    leverage_binding: bool
    size_from_risk: Figure
    rejection: str | None


class RuinView(BaseModel):
    model_config = ConfigDict(frozen=True)

    probability_pct: Figure
    n_paths: int
    seed: int
    horizon_trades: int
    block_length: float
    ruin_drawdown_pct: float
    final_multiple_p5: Figure
    final_multiple_p50: Figure
    final_multiple_p95: Figure
    max_drawdown_p50: Figure
    max_drawdown_p95: Figure
    max_drawdown_p99: Figure
    mean_r: Figure
    win_rate_pct: Figure
    source: str


class RiskCalculatorView(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_size: float
    account_ccy: str
    risk_pct: float
    sizing_policy: str
    profile: str
    ruin: RuinView | None
    ruin_absent: str | None
    sizes: list[SizeRowView]
    method: str


def _parse_entries(raw: str) -> list[Any]:
    from fiboki.ops.risk_calc import SizeRequest

    out = []
    for part in [p for p in raw.split(",") if p.strip()][:12]:
        bits = part.strip().split(":")
        if len(bits) not in (3, 4):
            raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_entries",
                           f"{part!r}: each entry is SYMBOL:ENTRY:STOP_PIPS[:FX_QUOTE_TO_ACCOUNT].")
        try:
            out.append(SizeRequest(bits[0].upper(), float(bits[1]), float(bits[2]),
                                   float(bits[3]) if len(bits) == 4 else None))
        except ValueError as exc:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_entries", f"{part!r}: {exc}") from exc
    return out


@router.get("/risk/calculator", response_model=Envelope[RiskCalculatorView])
def risk_calculator(
    platform: PlatformDep,
    settings: SettingsDep,
    account_size: float = Query(..., gt=0, le=1e9),
    account_ccy: str = Query("USD", min_length=3, max_length=3),
    risk_pct: float = Query(..., gt=0, le=5.0, description="Risk per trade, percent of equity."),
    ruin_pct: float = Query(50.0, gt=0, lt=100, description="Drawdown from peak that counts as ruin."),
    strategy_id: str | None = Query(None),
    r_multiples: str | None = Query(None, description="Comma-separated R multiples (overrides strategy_id)."),
    entries: str = Query("", description="SYMBOL:ENTRY:STOP_PIPS[:FX], comma-separated, up to 12."),
    sizing_policy: str = Query("fixed_fractional_v2", pattern="^fixed_fractional_v[12]$"),
    profile: str = Query("OANDA_REALISTIC"),
    paths: int = Query(5_000, ge=100, le=20_000),
    seed: int = Query(20261019, ge=0, le=2**32 - 1),
) -> Envelope[RiskCalculatorView]:
    """Position size per instrument by size_trade, and a seeded Monte Carlo risk of ruin. Advisory."""
    from fiboki.api.ops_sources import reference_backtest
    from fiboki.ops.risk_calc import position_sizes, risk_of_ruin
    from fiboki.sim.profiles import get_profile

    try:
        get_profile(profile)
    except Exception as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "unknown_profile", str(exc)) from exc
    ccy = account_ccy.upper()
    rf = risk_pct / 100.0
    caveats: list[Caveat] = []
    rows = position_sizes(_parse_entries(entries), account_size=account_size, account_ccy=ccy, risk_fraction=rf,
                          policy_id=sizing_policy, profile_name=profile)
    P = Provenance.BACKTEST

    def f(v: float | None, unit: str, reason: str = "Not sized; see the rejection.") -> Figure:
        return Figure(value=v, provenance=P, unit=unit, estimated=True) if v is not None else Figure.missing(P, unit=unit, reason=reason)

    sizes = [
        SizeRowView(
            instrument=r.instrument, entry=f(r.entry, ""), stop=f(r.stop, ""), stop_pips=r.stop_pips,
            size=f(r.size, "units"), risk_amount=f(r.risk_amount, ccy), notional=f(r.notional, ccy),
            leverage_cap=f(r.leverage_cap, "x", "Unknown instrument."), leverage_binding=r.leverage_binding,
            size_from_risk=f(r.size_from_risk, "units"), rejection=r.rejection,
        )
        for r in rows
    ]
    series: list[float] = []
    source = ""
    absent: str | None = None
    if r_multiples:
        try:
            series = [float(x) for x in r_multiples.split(",") if x.strip()]
        except ValueError as exc:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_r_multiples", str(exc)) from exc
        source = f"{len(series)} R multiple(s) supplied by the operator"
        caveats.append(Caveat(code="user_supplied_distribution", severity="warning",
                              message="The trade-return distribution was typed in by the operator, not measured: "
                              "the ruin figures describe that hypothesis, not any strategy's record.",
                              affects="ruin", direction="unknown"))
    elif strategy_id:
        ref = reference_backtest(settings, strategy_id)
        series, source = ref.r_multiples, ref.label
        if not series:
            absent = ref.reason or f"{ref.label} has no trade with a recorded stop, so no R multiple."
    else:
        absent = "Choose a strategy (its latest stored backtest) or type a list of R multiples."
    ruin = None
    if series and absent is None:
        try:
            res = risk_of_ruin(series, risk_fraction=rf, ruin_drawdown_pct=ruin_pct, n_paths=paths, seed=seed)
        except ValueError as exc:
            absent = str(exc)
        else:
            ruin = RuinView(
                probability_pct=Figure(value=round(res.probability * 100.0, 3), provenance=P, unit="pct",
                                       sample_size=res.n_paths, estimated=True),
                n_paths=res.n_paths, seed=res.seed, horizon_trades=res.horizon_trades,
                block_length=round(res.block_length, 3), ruin_drawdown_pct=res.ruin_drawdown_pct,
                final_multiple_p5=f(round(res.final_multiple_p5, 4), "x"),
                final_multiple_p50=f(round(res.final_multiple_p50, 4), "x"),
                final_multiple_p95=f(round(res.final_multiple_p95, 4), "x"),
                max_drawdown_p50=f(round(res.max_dd_p50, 3), "pct"),
                max_drawdown_p95=f(round(res.max_dd_p95, 3), "pct"),
                max_drawdown_p99=f(round(res.max_dd_p99, 3), "pct"),
                mean_r=f(round(res.mean_r, 4), "R"), win_rate_pct=f(round(res.win_rate * 100.0, 2), "pct"),
                source=source,
            )
    view = RiskCalculatorView(
        account_size=account_size, account_ccy=ccy, risk_pct=risk_pct, sizing_policy=sizing_policy, profile=profile,
        ruin=ruin, ruin_absent=absent, sizes=sizes,
        method=(
            "Sizes: portfolio.sizing.size_trade (the paper runtime's one sizing call), long entry, stop the given "
            "pips below, instrument leverage cap. Ruin: stats.bootstrap.resample_with_compounding, stationary "
            "bootstrap of the R multiples, re-sized off running equity, ruin = a drawdown from the peak deeper "
            "than the chosen level; horizon = the number of R multiples."
        ),
    )
    return Envelope(data=view, source=_note("live", "Computed on request; nothing is stored."),
                    caveats=tuple(caveats))


# ============================================================ portfolio weights


class AllocationMemberView(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    kind: str = Field(pattern="^(strategy|bot)$")
    in_fleet: bool
    source: str
    days: int
    annual_vol_pct: Figure


class SchemeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    scheme: str
    label: str
    weights: dict[str, Figure]
    annual_return_pct: Figure
    annual_vol_pct: Figure
    sharpe: Figure
    max_drawdown_pct: Figure
    effective_n: Figure


class AllocationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    basis: str = Field(pattern="^(backtest|paper)$")
    members: list[AllocationMemberView]
    common_days: int
    schemes: list[SchemeView]
    absent_reason: str | None
    method: str


_SCHEME_LABEL = {
    "equal_weight": "Equal weight (reference)",
    "equal_risk": "Equal risk contribution",
    "inverse_vol": "Inverse volatility",
    "min_correlation": "Minimum correlation",
}


@router.get("/allocation", response_model=Envelope[AllocationView])
def allocation(
    platform: PlatformDep,
    settings: SettingsDep,
    basis: str = Query("backtest", pattern="^(backtest|paper)$"),
) -> Envelope[AllocationView]:
    """Equal-risk, inverse-vol and min-correlation weights with their portfolio stats. Display only."""
    import math

    from fiboki.ops.analytics import DAYS_PER_YEAR, daily_returns_from_trades, portfolio_weights

    bots = _forward_sessions(platform)
    fleet_strategies = {s.strategy_id for s in bots.values()}
    returns: dict[str, Any] = {}
    members: list[tuple[str, str, bool, str]] = []
    prov = Provenance.BACKTEST if basis == "backtest" else Provenance.PAPER
    if basis == "backtest":
        cache: dict[str, Any] = {}
        for doc in platform.strategy_documents():
            series, label, prov_ = _strategy_returns(platform, settings, doc.strategy_id, cache)
            if len(series):
                returns[doc.strategy_id] = series
                members.append((doc.strategy_id, "strategy", doc.strategy_id in fleet_strategies, label))
    else:
        for sid, s in sorted(bots.items()):
            series = daily_returns_from_trades(
                [{"exit_time": t.exit_time, "net_pnl": t.net_pnl} for t in s.trades], s.initial_balance
            )
            if len(series):
                returns[sid] = series
                members.append((sid, "bot", True, f"{len(s.trades)} paper trade(s)"))
    schemes, _names, days, reason = portfolio_weights(returns)

    def fig(v: float | None, unit: str) -> Figure:
        return Figure(value=v, provenance=prov, unit=unit, estimated=True, sample_size=days or None) if v is not None \
            else Figure.missing(prov, unit=unit, reason="Not computable on these days.")

    member_views = []
    for key, kind, in_fleet, label in members:
        s = returns[key]
        vol = float(s.std(ddof=1)) * math.sqrt(DAYS_PER_YEAR) * 100.0 if len(s) > 1 else None
        member_views.append(AllocationMemberView(
            key=key, kind=kind, in_fleet=in_fleet, source=label, days=int(len(s)),
            annual_vol_pct=fig(None if vol is None else round(vol, 4), "pct"),
        ))
    scheme_views = [
        SchemeView(
            scheme=r.scheme, label=_SCHEME_LABEL.get(r.scheme, r.scheme),
            weights={k: fig(round(w * 100.0, 4), "pct") for k, w in r.weights.items()},
            annual_return_pct=fig(r.annual_return_pct, "pct"), annual_vol_pct=fig(r.annual_vol_pct, "pct"),
            sharpe=fig(r.sharpe, "ratio"), max_drawdown_pct=fig(r.max_drawdown_pct, "pct"),
            effective_n=fig(r.effective_n, "count"),
        )
        for r in schemes
    ]
    view = AllocationView(
        basis=basis, members=member_views, common_days=days, schemes=scheme_views,
        absent_reason=reason or None,
        method=(
            "Daily returns of each member's closed-trade balance curve (17:00 New York days, zero on days "
            "without an exit), on the days every member has a value. Equal risk: equal risk contribution "
            "from the sample covariance (fixed-point iteration); inverse vol: weights proportional to 1/sigma; "
            "minimum correlation: long-only weights minimising w'Cw over the correlation matrix C. Statistics annualised at 252 days. "
            "DISPLAY ONLY: the paper runtime's portfolio construction decides sizes and reads none of this."
        ),
    )
    return Envelope(
        data=view,
        source=_note("live" if scheme_views else "absent",
                     f"{len(member_views)} member(s) on the {basis} basis; {days} common day(s)."),
    )


# ============================================================ structured journal


class TradeNoteView(BaseModel):
    model_config = ConfigDict(frozen=True)

    trade_id: str
    note: str
    tags: list[str]
    grade: str | None = Field(default=None, pattern="^(A|B|C)$")
    mistakes: list[str]
    screenshot_url: str | None
    intervention_reason: str | None
    updated_by: str
    updated_at: datetime | None
    #: 0 = no note yet. A PUT names the version it was based on.
    version: int


class TradeNoteDetailView(BaseModel):
    model_config = ConfigDict(frozen=True)

    note: TradeNoteView
    history: list[dict[str, Any]]


class TradeNoteRequest(BaseModel):
    note: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    grade: str | None = Field(default=None, pattern="^(A|B|C)?$")
    mistakes: list[str] = Field(default_factory=list, max_length=12)
    screenshot_url: str | None = Field(default=None, max_length=500)
    intervention_reason: str | None = Field(default=None, max_length=500)
    expected_version: int = Field(ge=0)


class JournalVocabularyView(BaseModel):
    model_config = ConfigDict(frozen=True)

    tags: dict[str, int]
    mistakes: dict[str, int]
    grades: dict[str, int]
    suggested_mistakes: list[str]


#: A starter vocabulary the editor offers; any slug is accepted.
SUGGESTED_MISTAKES = [
    "early-exit", "late-entry", "moved-stop", "oversized", "chased", "ignored-news", "revenge", "no-plan",
    "manual-override",
]


def _notes(settings: Any) -> Any:
    from fiboki.ops.journal_notes import JournalNoteStore

    return JournalNoteStore(Path(settings.paths.ops_dir) / "journal_notes.sqlite")


def _note_view(n: Any) -> TradeNoteView:
    return TradeNoteView(
        trade_id=n.trade_id, note=n.note, tags=list(n.tags), grade=n.grade, mistakes=list(n.mistakes),
        screenshot_url=n.screenshot_url, intervention_reason=n.intervention_reason, updated_by=n.updated_by,
        updated_at=datetime.fromisoformat(n.updated_at) if n.updated_at else None, version=n.version,
    )


def _trade_exists(platform: Any, trade_id: str) -> bool:
    rows, _ = platform.trades(limit=1_000_000)
    return any(r.trade_id == trade_id for r in rows)


@router.get("/journal/notes", response_model=Page[TradeNoteView])
def journal_notes(
    settings: SettingsDep,
    tag: str | None = None,
    grade: str | None = Query(None, pattern="^(A|B|C)$"),
    mistake: str | None = None,
    intervened: bool | None = None,
) -> Page[TradeNoteView]:
    store = _notes(settings)
    rows = store.matching(tag=tag, grade=grade, mistake=mistake, intervened=intervened)
    return Page(
        items=[_note_view(n) for n in rows], total=len(rows), limit=max(1, len(rows)),
        source=_note("live" if store.path.exists() else "absent",
                     "Operator journal notes (annotations; nothing trades on them)." if store.path.exists()
                     else "No journal note has been written yet."),
    )


@router.get("/journal/vocabulary", response_model=Envelope[JournalVocabularyView])
def journal_vocabulary(settings: SettingsDep) -> Envelope[JournalVocabularyView]:
    vocab = _notes(settings).vocabulary()
    return Envelope(data=JournalVocabularyView(**vocab, suggested_mistakes=SUGGESTED_MISTAKES),
                    source=_note("live", "Tags, mistakes and grades in use, with counts."))


@router.get("/journal/notes/{trade_id}", response_model=Envelope[TradeNoteDetailView])
def journal_note(trade_id: str, settings: SettingsDep) -> Envelope[TradeNoteDetailView]:
    from fiboki.ops.journal_notes import TradeNote

    store = _notes(settings)
    note = store.get(trade_id) or TradeNote(trade_id=trade_id)
    return Envelope(data=TradeNoteDetailView(note=_note_view(note), history=store.history(trade_id)),
                    source=_note("live" if note.version else "absent",
                                 f"Version {note.version}." if note.version else "No note on this trade yet."))


@router.put("/journal/notes/{trade_id}", response_model=Envelope[TradeNoteDetailView])
def put_journal_note(
    trade_id: str,
    body: TradeNoteRequest,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
    request: Request,
) -> Envelope[TradeNoteDetailView]:
    """Write a trade's journal note (operator role). Versioned, history kept, audited."""
    from fiboki.ops.journal_notes import NoteConflict, NoteInvalid, validate

    common = {
        "actor": principal.user_id, "actor_role": principal.role.value,
        "reason": (body.intervention_reason or "journal note")[:500], "target": trade_id,
        "execution_mode": settings.execution_mode.value, "correlation_id": current_correlation_id(),
        "source_ip": request.client.host if request.client else "",
    }
    if not _trade_exists(platform, trade_id):
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_trade", f"No closed trade {trade_id!r} on this platform.")
    try:
        note = validate(trade_id, note=body.note, tags=body.tags, grade=body.grade or None, mistakes=body.mistakes,
                        screenshot_url=body.screenshot_url, intervention_reason=body.intervention_reason)
        saved = _notes(settings).put(note, actor=principal.user_id, expected_version=body.expected_version)
    except NoteConflict as exc:
        audit.record("journal.note", outcome="refused", detail={"error": str(exc)}, **common)
        raise ApiError(status.HTTP_409_CONFLICT, "note_version_conflict", str(exc),
                       context={"current_version": exc.current}) from exc
    except NoteInvalid as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "note_invalid", str(exc)) from exc
    audit.record("journal.note", outcome="allowed",
                 detail={"version": saved.version, "grade": saved.grade, "tags": list(saved.tags),
                         "intervention": bool(saved.intervention_reason)}, **common)
    return journal_note(trade_id, settings)


@router.get("/journal/trades", response_model=Page[TradeRowView])
def journal_trades(
    platform: PlatformDep,
    settings: SettingsDep,
    tag: str | None = None,
    grade: str | None = Query(None, pattern="^(A|B|C)$"),
    mistake: str | None = None,
    intervened: bool | None = None,
    provenance: Annotated[list[Provenance] | None, Query()] = None,
    strategy_id: str | None = None,
    instrument: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page[TradeRowView]:
    """``/api/trading/trades`` restricted, SERVER-SIDE, to trades whose journal note matches."""

    wanted = {n.trade_id for n in _notes(settings).matching(tag=tag, grade=grade, mistake=mistake,
                                                            intervened=intervened)}
    rows, _ = platform.trades(provenance=set(provenance) if provenance else None, strategy_id=strategy_id,
                              instrument=instrument, limit=1_000_000)
    hits = [r for r in rows if r.trade_id in wanted]
    page = hits[offset: offset + limit]
    items = [_trade_view(r, settings, platform.session_charged_costs(r.session_id)
                         if getattr(r, "session_id", None) else None)
             for r in page]
    filters = ", ".join(f"{k}={v}" for k, v in (("tag", tag), ("grade", grade), ("mistake", mistake),
                                                 ("intervened", intervened)) if v is not None) or "none"
    return Page(
        items=items, total=len(hits), offset=offset, limit=limit,
        source=_note(platform.data_source, f"Trade record filtered by journal notes ({filters}). "
                     + platform.trade_record_detail(), platform.journal_as_of()),
        caveats=_record_caveats(platform),
    )
