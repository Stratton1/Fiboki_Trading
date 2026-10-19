"""ALERTS: price and level alert rules and the notification history (NTF).

``GET /api/alerts/rules`` · ``POST /api/alerts/rules`` · ``PUT/DELETE /api/alerts/rules/{id}``
    The signed-in operator's rules (``obs/price_alerts.py``): instrument, kind
    (above, below, cross), level, side and mode. Versioned (a stale PUT is a
    409), every write audited and kept as a revision. Editing a rule re-arms it.
``GET /api/alerts/notifications`` · ``POST /api/alerts/notifications/read``
    What fired, newest first, with the server-side read marker: how many are
    unread is the API's count, and marking read moves the marker forward only.

Rules are EVALUATED by a separate process over recorded quotes (the quote
recorder after each poll, or ``fiboki alerts price-evaluate``), never by the API
and never by the browser; both responses carry the evaluator's last run and the
API's verdict on it, so an alert that cannot fire because nothing is evaluating
says so. A fired alert is a message to a human. It cannot place, size or close
anything: this router has no path to the execution layer.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AnyPrincipal, AuditDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.grid_format import Tone, tones_for
from fiboki.api.models import Envelope, SourceNote
from fiboki.api.provenance import Figure
from fiboki.api.routers.monitors import known_symbol
from fiboki.api.routers.workspace import record_write
from fiboki.api.security import Principal, Role, require_operator
from fiboki.core.enums import Provenance
from fiboki.core.paths import resolve_paths
from fiboki.obs.price_alerts import (
    AlertConflict,
    Notification,
    PriceAlertStore,
    PriceRule,
    RuleSpec,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
OperatorPrincipal = Annotated[Principal, Depends(require_operator)]

#: The evaluator runs on every recorder poll (30 s by default): six missed
#: polls is an evaluator (or recorder) that is not running.
EVALUATOR_STALE_AFTER_S = 180.0


def store_for(settings: Any) -> PriceAlertStore:
    return PriceAlertStore(resolve_paths({"FIBOKI_STATE_DIR": str(settings.state_dir)}).price_alerts_db)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _unavailable(exc: Exception) -> ApiError:
    return ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "alerts_unavailable",
                    "The price alert store could not be read or written. Nothing was changed.",
                    context={"error_class": type(exc).__name__})


class EvaluatorView(BaseModel):
    model_config = ConfigDict(frozen=True)

    last_run_at: datetime | None
    source: str
    state: Literal["live", "stale", "never"]
    detail: str


class PriceRuleView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    instrument: str
    kind: Literal["above", "below", "cross"]
    side: Literal["mid", "bid", "ask"]
    #: The level the operator typed (configuration, not a measurement).
    level: Figure
    mode: Literal["once", "rearm"]
    note: str
    enabled: bool
    armed: bool
    version: int
    created_at: datetime
    updated_at: datetime
    last_side: str | None
    last_price: Figure
    last_quote_at: datetime | None
    fired_count: Figure
    last_fired_at: datetime | None
    #: ``armed``, ``fired`` (once and done), ``disabled``.
    state: Literal["armed", "fired", "disabled"]
    chart_href: str


class AlertRulesView(BaseModel):
    model_config = ConfigDict(frozen=True)

    rules: list[PriceRuleView]
    evaluator: EvaluatorView
    can_edit: bool


class RuleWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: str
    kind: Literal["above", "below", "cross"]
    level: float = Field(gt=0, allow_inf_nan=False)
    side: Literal["mid", "bid", "ask"] = "mid"
    mode: Literal["once", "rearm"] = "once"
    note: str = Field(default="", max_length=200)
    enabled: bool = True


class RuleUpdate(RuleWrite):
    base_version: int = Field(ge=1)


class NotificationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    rule_id: str
    instrument: str
    kind: str
    side: str
    level: Figure
    price: Figure
    quote_time: datetime
    fired_at: datetime
    message: str
    read: bool
    tones: dict[str, Tone]
    chart_href: str


class NotificationsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[NotificationView]
    total: int
    unread: int
    read_through: int
    evaluator: EvaluatorView


class MarkRead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    through_seq: int = Field(ge=0)


def _evaluator(store: PriceAlertStore, now: datetime) -> EvaluatorView:
    st = store.evaluator_state()
    if st.at is None:
        return EvaluatorView(last_run_at=None, source="", state="never",
                             detail="No evaluator has run: rules exist but nothing evaluates them. Start the quote "
                             "recorder (it evaluates after each poll) or run fiboki alerts price-evaluate.")
    age = max(0.0, (now - st.at).total_seconds())
    if age > EVALUATOR_STALE_AFTER_S:
        return EvaluatorView(last_run_at=st.at, source=st.source, state="stale",
                             detail=f"The evaluator last ran {age:.0f} s ago (stale after "
                             f"{EVALUATOR_STALE_AFTER_S:.0f} s): alerts are not being checked.")
    return EvaluatorView(last_run_at=st.at, source=st.source, state="live",
                         detail=f"Evaluated {age:.0f} s ago ({st.quotes_seen} quote(s), {st.rules_evaluated} rule(s)).")


def _level(value: float | None, as_of: datetime | None = None) -> Figure:
    if value is None:
        return Figure.missing(Provenance.BROKER_DEMO, reason="Not yet observed.")
    return Figure(value=value, provenance=Provenance.BROKER_DEMO, as_of=as_of)


def _rule_view(r: PriceRule) -> PriceRuleView:
    state: Literal["armed", "fired", "disabled"] = (
        "disabled" if not r.spec.enabled else ("armed" if r.armed else "fired")
    )
    return PriceRuleView(
        id=r.id, instrument=r.spec.instrument, kind=r.spec.kind, side=r.spec.side, level=_level(r.spec.level),
        mode=r.spec.mode, note=r.spec.note, enabled=r.spec.enabled, armed=r.armed, version=r.version,
        created_at=r.created_at, updated_at=r.updated_at, last_side=r.last_side,
        last_price=_level(r.last_price, r.last_quote_at), last_quote_at=r.last_quote_at,
        fired_count=Figure(value=float(r.fired_count), provenance=Provenance.BROKER_DEMO, unit="count"),
        last_fired_at=r.last_fired_at, state=state, chart_href=f"/markets/{r.spec.instrument}",
    )


def _source(store: PriceAlertStore, now: datetime) -> SourceNote:
    return SourceNote(kind="live", detail=f"Price alert store at {store.path}; rules are evaluated over recorded "
                      "OANDA practice quotes by a separate process, never by the API.", as_of=now)


def _rules_envelope(store: PriceAlertStore, principal: Principal) -> Envelope[AlertRulesView]:
    now = _now()
    try:
        rules = store.rules(principal.user_id)
        evaluator = _evaluator(store, now)
    except sqlite3.Error as exc:
        raise _unavailable(exc) from exc
    return Envelope(
        data=AlertRulesView(rules=[_rule_view(r) for r in rules], evaluator=evaluator,
                            can_edit=principal.role.satisfies(Role.OPERATOR)),
        source=_source(store, now),
    )


@router.get("/rules", response_model=Envelope[AlertRulesView])
def list_rules(settings: SettingsDep, principal: AnyPrincipal) -> Envelope[AlertRulesView]:
    """The operator's price and level alert rules, and whether anything is evaluating them."""
    return _rules_envelope(store_for(settings), principal)


def _spec(body: RuleWrite) -> RuleSpec:
    return RuleSpec(instrument=known_symbol(body.instrument), kind=body.kind, level=body.level, side=body.side,
                    mode=body.mode, note=body.note, enabled=body.enabled)


def _audit(audit: Any, *, action: str, principal: Principal, request: Request, settings: Any, target: str,
           outcome: str, reason: str, detail: dict[str, Any]) -> None:
    record_write(audit, event=f"alerts.price_rule.{action}", principal=principal, request=request, settings=settings,
                 target=target, outcome=outcome, reason=reason, detail=detail)


@router.post("/rules", response_model=Envelope[AlertRulesView], status_code=status.HTTP_201_CREATED)
def create_rule(body: RuleWrite, request: Request, settings: SettingsDep, audit: AuditDep,
                principal: OperatorPrincipal) -> Envelope[AlertRulesView]:
    """Create a rule (armed). Audited. Evaluation starts with the next recorded quote after now."""
    store = store_for(settings)
    spec = _spec(body)
    try:
        rule = store.create(principal.user_id, spec, actor=principal.user_id)
    except ValueError as exc:
        _audit(audit, action="create", principal=principal, request=request, settings=settings,
               target=spec.instrument, outcome="refused", reason=str(exc), detail={})
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_alert_rule", str(exc)) from exc
    except sqlite3.Error as exc:
        raise _unavailable(exc) from exc
    _audit(audit, action="create", principal=principal, request=request, settings=settings, target=rule.id,
           outcome="allowed", reason="create",
           detail={"instrument": spec.instrument, "kind": spec.kind, "level": spec.level, "side": spec.side})
    return _rules_envelope(store, principal)


def _conflict(conflict: AlertConflict, base: int) -> ApiError:
    if conflict.current is None:
        return ApiError(status.HTTP_404_NOT_FOUND, "alert_rule_not_found", "No such alert rule (deleted elsewhere?).")
    return ApiError(status.HTTP_409_CONFLICT, "alert_rule_conflict",
                    f"This rule changed elsewhere (now version {conflict.current.version}, you edited version "
                    f"{base}). Nothing was overwritten; reload.",
                    context={"current_version": conflict.current.version, "base_version": base})


@router.put("/rules/{rule_id}", response_model=Envelope[AlertRulesView])
def update_rule(rule_id: str, body: RuleUpdate, request: Request, settings: SettingsDep, audit: AuditDep,
                principal: OperatorPrincipal) -> Envelope[AlertRulesView]:
    """Replace a rule's spec (re-arms it). ``base_version`` must be current: 409 otherwise."""
    store = store_for(settings)
    spec = _spec(body)
    try:
        store.update(principal.user_id, rule_id, spec, base_version=body.base_version, actor=principal.user_id)
    except AlertConflict as conflict:
        _audit(audit, action="update", principal=principal, request=request, settings=settings, target=rule_id,
               outcome="refused", reason="stale base version", detail={"base_version": body.base_version})
        raise _conflict(conflict, body.base_version) from conflict
    except ValueError as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_alert_rule", str(exc)) from exc
    except sqlite3.Error as exc:
        raise _unavailable(exc) from exc
    _audit(audit, action="update", principal=principal, request=request, settings=settings, target=rule_id,
           outcome="allowed", reason="update", detail={"level": spec.level, "kind": spec.kind})
    return _rules_envelope(store, principal)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_rule(rule_id: str, request: Request, settings: SettingsDep, audit: AuditDep,
                principal: OperatorPrincipal, base_version: int = Query(..., ge=1)) -> Response:
    """Delete a rule (its revisions and the notifications it raised are kept)."""
    store = store_for(settings)
    try:
        store.delete(principal.user_id, rule_id, base_version=base_version, actor=principal.user_id)
    except AlertConflict as conflict:
        _audit(audit, action="delete", principal=principal, request=request, settings=settings, target=rule_id,
               outcome="refused", reason="stale base version", detail={"base_version": base_version})
        raise _conflict(conflict, base_version) from conflict
    except sqlite3.Error as exc:
        raise _unavailable(exc) from exc
    _audit(audit, action="delete", principal=principal, request=request, settings=settings, target=rule_id,
           outcome="allowed", reason="delete", detail={})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _notifications(store: PriceAlertStore, principal: Principal, limit: int, before: int | None) -> Envelope[NotificationsView]:
    now = _now()
    try:
        rows: list[Notification] = store.notifications(principal.user_id, limit=limit, before=before)
        total, unread, through = store.counts(principal.user_id)
        evaluator = _evaluator(store, now)
    except sqlite3.Error as exc:
        raise _unavailable(exc) from exc
    items = []
    for n in rows:
        read = n.seq <= through
        items.append(NotificationView(
            seq=n.seq, rule_id=n.rule_id, instrument=n.instrument, kind=n.kind, side=n.side,
            level=_level(n.level), price=_level(n.price, n.quote_time), quote_time=n.quote_time, fired_at=n.fired_at,
            message=n.message, read=read, tones=tones_for("notifications", {"read_state": "read" if read else "unread"}),
            chart_href=f"/markets/{n.instrument}",
        ))
    return Envelope(data=NotificationsView(items=items, total=total, unread=unread, read_through=through,
                                           evaluator=evaluator), source=_source(store, now))


@router.get("/notifications", response_model=Envelope[NotificationsView])
def notifications(settings: SettingsDep, principal: AnyPrincipal, limit: int = Query(100, ge=1, le=500),
                  before: int | None = Query(None, ge=1)) -> Envelope[NotificationsView]:
    """What fired, newest first, with the API's unread count and read marker."""
    return _notifications(store_for(settings), principal, limit, before)


@router.post("/notifications/read", response_model=Envelope[NotificationsView])
def mark_read(body: MarkRead, request: Request, settings: SettingsDep, audit: AuditDep,
              principal: OperatorPrincipal) -> Envelope[NotificationsView]:
    """Move the operator's read marker forward to ``through_seq`` (never back)."""
    store = store_for(settings)
    try:
        through = store.mark_read(principal.user_id, body.through_seq)
    except sqlite3.Error as exc:
        raise _unavailable(exc) from exc
    _audit(audit, action="read", principal=principal, request=request, settings=settings, target=principal.user_id,
           outcome="allowed", reason="mark read", detail={"through_seq": through})
    return _notifications(store, principal, 100, None)
