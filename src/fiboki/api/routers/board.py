"""SYSTEM BOARD and ALERT MANAGER: every process and every alert rule, one clock.

The workstation had five system pages (services, workers, broker health, data
health, alerts), each read at a different moment, and the status bar aged the
worker heartbeat by a threshold the browser chose. Ages disagreed between
pages. Here every component is read once, aged by ONE API clock
(``server_time``), and judged stale or not by the platform's own thresholds.
The workstation renders ``status`` and ``stale``; it compares no age.

The alert manager lists every alert rule the platform has (the
:class:`~fiboki.obs.alerts.AlertEvent` taxonomy with its default severity and
the condition that raises it), each rule's state (firing when it has an open
incident, quiet when it has fired before and nothing is open, never fired),
when it last fired, and the alert history from the alert log. Rules are code:
changing one is a reviewed commit, so the manager reads them and edits none.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api import links
from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.health import build_health
from fiboki.api.market_sessions import MarketSessionView, market_sessions
from fiboki.api.models import Envelope, SourceNote
from fiboki.api.provenance import Caveat, Figure

router = APIRouter(prefix="/api/system", tags=["system"])

__all__ = ["AlertsView", "BoardRow", "BoardView", "build_board", "router"]

_RANK = {"ok": 0, "absent": 1, "degraded": 2, "down": 3}


class BoardRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    group: str = Field(pattern="^(service|data|worker|bot|broker|check|alerts)$")
    name: str
    status: str = Field(pattern="^(ok|degraded|down|absent)$")
    detail: str
    #: Age of the component's last sign of life, by the API clock.
    age: Figure
    #: The platform's verdict: this component's information is out of date.
    stale: bool
    stale_after: Figure
    deep_link: str


class BoardView(BaseModel):
    model_config = ConfigDict(frozen=True)

    server_time: datetime
    status: str = Field(pattern="^(ok|degraded|down)$")
    rows: list[BoardRow]
    counts: dict[str, Figure]
    stale: Figure
    advisory: str


def _secs(p: Any, value: float | None, reason: str = "") -> Figure:
    if value is None:
        return Figure.missing(p, unit="s", reason=reason or "No age is known.")
    return Figure(value=round(value, 1), provenance=p, unit="s")


def build_board(platform: Any, settings: Any, *, alert_log: Any = None, now: datetime | None = None) -> BoardView:
    from fiboki.api.routers import fleet as fleet_router
    from fiboki.api.routers.incidents import load_incidents

    stamp = now or datetime.now(tz=UTC)
    p = settings.provenance_for_execution()
    stale_after = float(settings.worker_heartbeat_stale_seconds)
    rows: list[BoardRow] = []
    no_threshold = Figure.missing(p, unit="s", reason="Probed on every read; no age applies.")

    # -- services and data stores, probed now
    data_names = {"database", "market_data_store", "trade_and_position_records"}
    for source in platform.data_sources():
        status = "absent" if source.kind == "absent" else ("ok" if source.healthy else "down")
        rows.append(
            BoardRow(
                id=f"service:{source.name}",
                group="data" if source.name in data_names else "service",
                name=source.name,
                status=status,
                detail=f"[{source.kind}] {source.detail}"
                + (f" ({source.latency_ms:.1f} ms)" if source.latency_ms is not None else ""),
                age=Figure(value=0.0, provenance=p, unit="s", as_of=stamp),
                stale=False,
                stale_after=no_threshold,
                deep_link=links.DATA_QUALITY if source.name in data_names else links.SYSTEM,
            )
        )

    # -- workers, aged by this clock against the platform's threshold
    beat = platform.worker_heartbeat()
    if beat.state == "absent":
        rows.append(
            BoardRow(
                id="worker:none",
                group="worker",
                name="worker",
                status="down",
                detail=f"No worker heartbeat [{beat.reason}]: {beat.detail}",
                age=_secs(p, None, "No worker has ever beaten. Not zero."),
                stale=True,
                stale_after=Figure(value=stale_after, provenance=p, unit="s"),
                deep_link=links.SYSTEM,
            )
        )
    else:
        workers = beat.workers or ()
        if not workers:
            age = beat.age_seconds
            is_stale = beat.state == "stale"
            rows.append(
                BoardRow(
                    id="worker:heartbeat_file",
                    group="worker",
                    name="heartbeat file",
                    status="down" if is_stale else "ok",
                    detail=beat.detail,
                    age=_secs(p, age),
                    stale=is_stale,
                    stale_after=Figure(value=stale_after, provenance=p, unit="s"),
                    deep_link=links.SYSTEM,
                )
            )
        for worker in workers:
            stopped = worker.status in {"stopped", "crashed"}
            is_stale = (not stopped) and worker.age_seconds >= stale_after
            rows.append(
                BoardRow(
                    id=f"worker:{worker.worker_id}",
                    group="worker",
                    name=worker.worker_id,
                    status="down" if worker.status == "crashed" or is_stale else (
                        "degraded" if stopped else "ok"
                    ),
                    detail=f"{worker.kind or 'worker'}: status {worker.status or 'unknown'}, last beat "
                    f"{worker.beat_at.isoformat()}",
                    age=Figure(value=round(worker.age_seconds, 1), provenance=p, unit="s", as_of=worker.beat_at),
                    stale=is_stale,
                    stale_after=Figure(value=stale_after, provenance=p, unit="s"),
                    deep_link=links.SYSTEM,
                )
            )

    # -- paper bots (sessions), by the fleet's own judgement
    bot_status = {"running": "ok", "idle": "ok", "finished": "ok", "halted": "degraded",
                  "stale": "down", "failed": "down"}
    for bot in fleet_router.fleet_rows(platform, settings, now=stamp):
        rows.append(
            BoardRow(
                id=f"bot:{bot.session_id}",
                group="bot",
                name=bot.label,
                status=bot_status[bot.state],
                detail=f"{bot.state.upper()}: {bot.state_reason}",
                age=bot.summary_age,
                stale=bot.state == "stale",
                stale_after=Figure.missing(
                    p, unit="s", reason="Two bars of the session's timeframe plus 300 s; replays never go stale."
                ),
                deep_link=bot.links.session,
            )
        )

    # -- broker
    state = platform.mode_state()
    configured = bool(settings.venue_url)
    guard = bool(state["guard_allowed"])
    rows.append(
        BoardRow(
            id="broker:venue",
            group="broker",
            name="broker venue",
            status="ok" if not configured or guard else "degraded",
            detail=(
                "No venue configured: in paper mode nothing should be reachable, which is correct."
                if not configured
                else ("Mode guard allows the configured venue." if guard else "Mode guard refuses: "
                      + "; ".join(state["guard_reasons"]))
            ),
            age=Figure(value=0.0, provenance=p, unit="s", as_of=stamp),
            stale=False,
            stale_after=no_threshold,
            deep_link="/system/broker-health",
        )
    )

    # -- health checks not already represented above
    report = build_health(platform, settings)
    covered = {"worker_heartbeat", "database", "paper_journal"}
    for check in report.checks:
        if check.name in covered:
            continue
        status = check.status
        if not check.critical and status == "down":
            status = "degraded"
        rows.append(
            BoardRow(
                id=f"check:{check.name}",
                group="check",
                name=check.name,
                status=status,
                detail=check.detail,
                age=Figure(value=0.0, provenance=p, unit="s", as_of=report.checked_at),
                stale=False,
                stale_after=no_threshold,
                deep_link=links.SYSTEM,
            )
        )

    # -- alerts: open incidents
    snap = load_incidents(platform, settings, alert_log=alert_log, now=stamp)
    open_incidents = [i for i in snap.incidents if i.status == "open"]
    configured_log = alert_log is not None
    worst_incident = max((i.severity for i in open_incidents), key=lambda s: ["info", "warning", "error", "critical"].index(s), default=None)
    rows.append(
        BoardRow(
            id="alerts:incidents",
            group="alerts",
            name="open incidents",
            status=(
                "absent" if not configured_log and not open_incidents
                else "down" if worst_incident in ("critical", "error")
                else "degraded" if open_incidents
                else "ok"
            ),
            detail=(
                f"{len(open_incidents)} open incident(s) not acknowledged."
                if open_incidents
                else "No open incident."
                + ("" if configured_log else " The alert log is not configured, so only kill-switch incidents are seen.")
            ),
            age=Figure(value=0.0, provenance=p, unit="s", as_of=stamp),
            stale=False,
            stale_after=no_threshold,
            deep_link=links.ALERTS,
        )
    )

    order = {"down": 0, "degraded": 1, "absent": 2, "ok": 3}
    group_order = {"worker": 0, "bot": 1, "service": 2, "data": 3, "broker": 4, "alerts": 5, "check": 6}
    rows.sort(key=lambda r: (group_order[r.group], order[r.status], r.name))
    worst = "ok"
    for row in rows:
        level = row.status if row.status != "absent" else "ok"
        if _RANK[level] > _RANK[worst]:
            worst = level
    counts = {
        status: Figure(value=float(sum(1 for r in rows if r.status == status)), provenance=p, unit="count")
        for status in ("ok", "degraded", "down", "absent")
    }
    stale_n = sum(1 for r in rows if r.stale)
    return BoardView(
        server_time=stamp,
        status=worst,
        rows=rows,
        counts=counts,
        stale=Figure(value=float(stale_n), provenance=p, unit="count"),
        advisory=(
            "" if worst == "ok" and stale_n == 0
            else "Not every component is healthy and current. Do not read a quiet screen as a quiet market."
        ),
    )


@router.get("/board", response_model=Envelope[BoardView])
def board(request: Request, platform: PlatformDep, settings: SettingsDep) -> Envelope[BoardView]:
    """Every service, worker, bot, broker, check and the incident load, one clock."""
    from fiboki.api.routers.incidents import alert_log_path

    view = build_board(platform, settings, alert_log=alert_log_path(request.app.state))
    return Envelope(
        data=view,
        source=SourceNote(
            kind="live",
            detail="Probes, worker_heartbeat, paper sessions, mode guard, health checks and the "
            "incident read model, all aged by the API clock at server_time.",
            as_of=view.server_time,
        ),
    )


# ------------------------------------------------------------- alert manager

#: What raises each operational rule, in words. A rule missing here is listed
#: with "raised by the component named in its source" rather than omitted.
_CONDITIONS: dict[str, str] = {
    "worker_down": "Heartbeat watchdog: a worker's newest beat is older than the down threshold, "
    "or an expected worker has never beaten.",
    "heartbeat_stale": "Heartbeat watchdog: a worker's newest beat is older than the stale threshold.",
    "worker_lease_contended": "A second worker tried to take a lease another holds.",
    "reconciliation_divergence": "Periodic reconciliation: the book and the venue disagree.",
    "data_stale": "A market-data feed has not produced a closed bar within its grace.",
    "data_quality_defect": "Dataset validation found a defect.",
    "broker_unhealthy": "A broker adapter health probe failed.",
    "risk_limit_breach": "The risk gateway refused an order on a limit.",
    "kill_switch_activated": "An operator armed the kill switch.",
    "kill_switch_deactivated": "An operator disarmed the kill switch.",
    "order_rejected_repeatedly": "The venue rejected orders repeatedly.",
    "strategy_degraded": "A lifecycle evaluation moved a strategy to DEGRADED.",
    "strategy_halted": "A pre-registered stopping rule fired (latched).",
    "strategy_quarantined": "A strategy was demoted into QUARANTINED.",
    "queue_backed_up": "A work queue exceeded its depth threshold.",
    "job_dead_lettered": "A job failed past its retries.",
    "sweep_no_data_exceeded": "A research sweep exceeded its no-data tolerance.",
    "migration_drift": "The store's schema revision differs from the code's.",
    "spread_model_divergence": "Quoted spreads diverged from the modelled spread.",
    "ledger_torn_tail": "A durable ledger had a torn final record (a crash mid-write).",
    "alert_test": "`fiboki alerts test`: proves a channel delivers, on demand.",
}


class AlertRuleView(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: str
    severity: str
    category: str = Field(pattern="^(operational|trade)$")
    condition: str
    state: str = Field(pattern="^(firing|quiet|never_fired)$")
    last_fired_at: datetime | None
    fired: Figure
    open_incident: str | None


class AlertHistoryRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    event: str
    severity: str
    message: str
    source: str
    link: str | None


class AlertsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    log_configured: bool
    channels: list[str]
    rules: list[AlertRuleView]
    history: list[AlertHistoryRow]
    history_total: Figure
    thresholds: dict[str, Figure]


def _parse(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


@router.get("/alerts", response_model=Envelope[AlertsView])
def alerts(
    request: Request,
    platform: PlatformDep,
    settings: SettingsDep,
    limit: int = Query(200, ge=1, le=2000),
) -> Envelope[AlertsView]:
    """Alert rules with state and last fired, plus the alert history."""
    from fiboki.api.routers.incidents import alert_log_path, load_incidents, read_alert_log
    from fiboki.obs.alerts import AlertEvent, default_severity

    path = alert_log_path(request.app.state)
    rows, caveats, detail = read_alert_log(path)
    stamp = datetime.now(tz=UTC)
    p = settings.provenance_for_execution()
    snap = load_incidents(platform, settings, alert_log=path, now=stamp)
    open_by_event = {i.event: i.id for i in snap.incidents if i.status == "open"}
    last: dict[str, datetime] = {}
    fired: dict[str, int] = {}
    history: list[AlertHistoryRow] = []
    for row in rows:
        event = str(row.get("event", ""))
        at = _parse(row.get("at"))
        if at is None:
            continue
        fired[event] = fired.get(event, 0) + 1
        if event not in last or at > last[event]:
            last[event] = at
        history.append(
            AlertHistoryRow(
                at=at,
                event=event,
                severity=str(row.get("severity", "warning")),
                message=str(row.get("message", "")),
                source=str(row.get("source", "")),
                link=None,
            )
        )
    for incident in snap.incidents:
        for h_index, h in enumerate(history):
            if h.event == incident.event and incident.first_seen <= h.at <= incident.last_seen:
                history[h_index] = h.model_copy(update={"link": links.incident(incident.id)})
    history.sort(key=lambda h: h.at, reverse=True)
    from fiboki.api.routers.incidents import _OPERATIONAL

    rules: list[AlertRuleView] = []
    for event in AlertEvent:
        name = event.value
        operational = name in _OPERATIONAL or name in _CONDITIONS
        state = "firing" if name in open_by_event else ("quiet" if name in fired else "never_fired")
        rules.append(
            AlertRuleView(
                event=name,
                severity=default_severity(event).value,
                category="operational" if operational else "trade",
                condition=_CONDITIONS.get(name, "Raised by the execution path for every trade event of this kind."),
                state=state,
                last_fired_at=last.get(name),
                fired=Figure(value=float(fired.get(name, 0)), provenance=p, unit="count"),
                open_incident=links.incident(open_by_event[name]) if name in open_by_event else None,
            )
        )
    state_order = {"firing": 0, "quiet": 1, "never_fired": 2}
    rules.sort(key=lambda r: (state_order[r.state], r.category != "operational", r.event))
    channels = ["console"]
    if path is not None:
        channels.append("file")
    if os.environ.get("FIBOKI_ALERT_WEBHOOK_URL", "").strip():
        channels.append("webhook")
    if os.environ.get("FIBOKI_TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get(
        "FIBOKI_TELEGRAM_CHAT_ID", ""
    ).strip():
        channels.append("telegram")
    thresholds = {
        "worker_stale_after": Figure(value=float(settings.worker_heartbeat_stale_seconds), provenance=p, unit="s"),
    }
    down = getattr(getattr(settings, "health", None), "worker_down_after_seconds", None)
    if isinstance(down, int | float):
        thresholds["worker_down_after"] = Figure(value=float(down), provenance=p, unit="s")
    view = AlertsView(
        log_configured=path is not None,
        channels=channels,
        rules=rules,
        history=history[:limit],
        history_total=Figure(value=float(len(history)), provenance=p, unit="count"),
        thresholds=thresholds,
    )
    return Envelope(
        data=view,
        source=SourceNote(
            kind="live" if path is not None else "absent",
            detail="Alert taxonomy from fiboki.obs.alerts; history from " + "; ".join(detail or ["no alert log"]) + ".",
            as_of=stamp,
        ),
        caveats=(
            *caveats,
            Caveat(
                code="alert_rules_are_code",
                severity="info",
                message="Rules and severities are code (fiboki/obs/alerts.py); changing one is a "
                "reviewed commit, so this screen reads them and edits none.",
                affects="rules",
                direction="unknown",
            ),
        ),
    )


class ClockView(BaseModel):
    """The API's clock and the market sessions, for the shell's session clocks."""

    model_config = ConfigDict(frozen=True)

    server_time: datetime
    sessions: list[MarketSessionView]


@router.get("/clock", response_model=Envelope[ClockView])
def clock() -> Envelope[ClockView]:
    """Cheap enough for every page: the server time and which sessions are open."""
    stamp = datetime.now(tz=UTC)
    return Envelope(
        data=ClockView(server_time=stamp, sessions=market_sessions(stamp)),
        source=SourceNote(
            kind="live",
            detail="API clock; conventional FX session hours in each city's local time, "
            "public holidays not modelled.",
            as_of=stamp,
        ),
    )
