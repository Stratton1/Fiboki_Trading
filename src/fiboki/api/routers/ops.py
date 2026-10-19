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

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Figure
from fiboki.api.security import Principal, require_admin
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
