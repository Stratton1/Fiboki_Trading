"""SYSTEM & INCIDENTS: a deduplicated incident read model with an audited ack.

Where incidents come from
-------------------------
* **The alert log** (``FIBOKI_ALERT_LOG``), the JSONL file that
  :class:`fiboki.obs.alerts.FileChannel` appends every dispatched alert to.
  Read-only. Operational events always count; trade-lifecycle events count only
  at ``error`` severity or above (a filled order is not an incident, an order
  in an unknown state is).
* **The kill-switch journal.** An activation opens an incident and the next
  deactivation resolves it. The journal is the authority for the switch, so
  ``kill_switch_activated``/``kill_switch_deactivated`` alerts are ignored here
  rather than counted twice.

Deduplication: occurrences with the same key (the alert's ``dedupe_key``,
which defaults to ``event:source``) fold into one incident while they keep
recurring; a gap longer than :data:`REOPEN_AFTER` starts a new one. An
incident's id is a hash of its key and first occurrence, so it is stable across
restarts and identical on every process that reads the same files.

Acknowledgements and notes are the only writes. They go to an append-only
JSONL file under the state directory AND to the operator audit trail, and they
never touch the alert log or the kill-switch journal. An acknowledgement is a
statement that a human has seen the incident; it does not resolve it, and a
recurrence after the acknowledgement re-opens it.

Access: reads are open like the rest of the read API; acknowledging and
annotating require the admin role, because ``tests/api/test_security.py``
requires every mutating route to be behind ``require_admin``. Letting an
operator (Tom) acknowledge needs that policy changed first, deliberately, in
that test.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure
from fiboki.api.security import Principal, require_admin

router = APIRouter(prefix="/api/system/incidents", tags=["system", "incidents"])

AdminPrincipal = Annotated[Principal, Depends(require_admin)]

__all__ = [
    "ALERT_LOG_ENV",
    "REOPEN_AFTER",
    "IncidentAnnotations",
    "IncidentView",
    "derive_incidents",
    "load_incidents",
    "router",
]

#: The alert log the obs FileChannel writes. Registered in
#: ``fiboki.api.settings.ENV_REGISTRY``; read here, never written.
ALERT_LOG_ENV = "FIBOKI_ALERT_LOG"

#: A recurrence after this much silence is a new incident, not the old one.
REOPEN_AFTER = timedelta(hours=6)

_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}

#: Events that are about the machinery rather than about one trade. Mirrors the
#: first block of :class:`fiboki.obs.alerts.AlertEvent`.
_OPERATIONAL = frozenset(
    {
        "worker_down",
        "heartbeat_stale",
        "worker_lease_contended",
        "reconciliation_divergence",
        "data_stale",
        "data_quality_defect",
        "broker_unhealthy",
        "risk_limit_breach",
        "order_rejected_repeatedly",
        "strategy_degraded",
        "strategy_halted",
        "strategy_quarantined",
        "queue_backed_up",
        "job_dead_lettered",
        "sweep_no_data_exceeded",
        "migration_drift",
        "spread_model_divergence",
    }
)
#: Owned by the kill-switch journal; the alerts are ignored to avoid a double count.
_KILL_SWITCH_ALERTS = frozenset({"kill_switch_activated", "kill_switch_deactivated"})


# ----------------------------------------------------------- annotations


class IncidentAnnotations:
    """Append-only JSONL of acknowledgements and notes. No update, no delete."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out


def annotations_for(settings: Any) -> IncidentAnnotations:
    return IncidentAnnotations(Path(settings.state_dir) / "incident_annotations.jsonl")


# ---------------------------------------------------------------- model


@dataclass(frozen=True, slots=True)
class Occurrence:
    at: datetime
    key: str
    event: str
    severity: str
    message: str
    source: str
    correlation_id: str = ""
    resolves: bool = False


@dataclass
class _Group:
    key: str
    event: str
    source: str
    occurrences: list[Occurrence] = field(default_factory=list)
    resolved_at: datetime | None = None
    resolved_by: str = ""

    @property
    def first_seen(self) -> datetime:
        return self.occurrences[0].at

    @property
    def last_seen(self) -> datetime:
        return self.occurrences[-1].at

    @property
    def incident_id(self) -> str:
        digest = hashlib.sha256(
            f"{self.key}|{self.first_seen.isoformat()}".encode()
        ).hexdigest()
        return f"inc_{digest[:16]}"

    @property
    def severity(self) -> str:
        return max(
            (o.severity for o in self.occurrences),
            key=lambda s: _SEVERITY_RANK.get(s, 1),
        )


class TimelineEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: str = Field(pattern="^(occurrence|resolved|ack|note)$")
    severity: str | None = None
    actor: str = ""
    text: str
    correlation_id: str = ""


class IncidentView(BaseModel):
    """One deduplicated incident with its full timeline."""

    model_config = ConfigDict(frozen=True)

    id: str
    key: str
    event: str
    source: str
    title: str
    severity: str = Field(pattern="^(info|warning|error|critical)$")
    status: str = Field(pattern="^(open|acknowledged|resolved)$")
    first_seen: datetime
    last_seen: datetime
    occurrences: Figure
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    deep_link: str
    as_of: datetime
    timeline: tuple[TimelineEntry, ...]


class IncidentAckRequest(BaseModel):
    reason: str = Field(min_length=8, max_length=500)
    """What the acknowledging operator knows or has done. Audited."""


class IncidentNoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


# ------------------------------------------------------------- derivation


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def incident_title(message: str) -> str:
    """The operator sentence, without the exception chain that follows it.

    The full message stays on the incident timeline. The queue title is the
    clause before the first ``Error`` or ``Exception`` segment, capped so a
    DNS traceback cannot become the whole Command screen.
    """
    head, sep, tail = message.partition(": ")
    title = head.strip() if sep and ("Error" in tail or "Exception" in tail) else message.strip()
    if len(title) > 160:
        title = title[:157] + "..."
    return title or message


def _is_incident_worthy(event: str, severity: str) -> bool:
    if event in _KILL_SWITCH_ALERTS:
        return False
    if event in _OPERATIONAL:
        return True
    return _SEVERITY_RANK.get(severity, 1) >= _SEVERITY_RANK["error"]


def occurrences_from_alerts(rows: Iterable[dict[str, Any]]) -> list[Occurrence]:
    out: list[Occurrence] = []
    for row in rows:
        event = str(row.get("event") or "")
        severity = str(row.get("severity") or "warning").lower()
        if severity not in _SEVERITY_RANK:
            severity = "warning"
        at = _parse_time(row.get("at"))
        if not event or at is None or not _is_incident_worthy(event, severity):
            continue
        source = str(row.get("source") or "")
        out.append(
            Occurrence(
                at=at,
                key=str(row.get("dedupe_key") or f"{event}:{source}"),
                event=event,
                severity=severity,
                message=str(row.get("message") or event),
                source=source or "alert_log",
                correlation_id=str(row.get("correlation_id") or ""),
            )
        )
    return out


def occurrences_from_kill_switch(events: Iterable[Any]) -> list[Occurrence]:
    out: list[Occurrence] = []
    for ev in events:
        at = _parse_time(ev.at.to_pydatetime() if hasattr(ev.at, "to_pydatetime") else ev.at)
        if at is None:
            continue
        mode = ev.mode.value if getattr(ev, "mode", None) else None
        if ev.action == "activate":
            out.append(
                Occurrence(
                    at=at,
                    key="kill_switch",
                    event="kill_switch_activated",
                    severity="critical" if mode == "flatten" else "error",
                    message=(
                        f"Kill switch ARMED ({(mode or '?').upper()}) by {ev.operator}: "
                        f"{ev.reason}"
                    ),
                    source=f"kill_switch_journal:{ev.operator}",
                    correlation_id=str((ev.extra or {}).get("correlation_id", "")),
                )
            )
        elif ev.action == "deactivate":
            out.append(
                Occurrence(
                    at=at,
                    key="kill_switch",
                    event="kill_switch_deactivated",
                    severity="warning",
                    message=f"Kill switch disarmed by {ev.operator}: {ev.reason}",
                    source=f"kill_switch_journal:{ev.operator}",
                    correlation_id=str((ev.extra or {}).get("correlation_id", "")),
                    resolves=True,
                )
            )
    return out


def derive_incidents(
    occurrences: Iterable[Occurrence],
    annotations: Iterable[dict[str, Any]],
    *,
    count_provenance: Any,
) -> list[IncidentView]:
    """Fold occurrences into incidents, then lay acknowledgements over them.

    Pure: same inputs, same incidents, same ids. Newest activity first.
    """
    ordered = sorted(occurrences, key=lambda o: (o.at, o.key, o.event, o.message))
    groups: list[_Group] = []
    open_by_key: dict[str, _Group] = {}
    for occ in ordered:
        group = open_by_key.get(occ.key)
        if occ.resolves:
            if group is not None and group.resolved_at is None:
                group.resolved_at = occ.at
                group.resolved_by = occ.message
                open_by_key.pop(occ.key, None)
            continue
        if (
            group is None
            or group.resolved_at is not None
            or occ.at - group.last_seen > REOPEN_AFTER
        ):
            group = _Group(key=occ.key, event=occ.event, source=occ.source)
            groups.append(group)
            open_by_key[occ.key] = group
        group.occurrences.append(occ)

    by_id: dict[str, list[dict[str, Any]]] = {}
    for record in annotations:
        by_id.setdefault(str(record.get("incident_id", "")), []).append(record)

    out: list[IncidentView] = []
    for group in groups:
        incident_id = group.incident_id
        notes = sorted(
            (r for r in by_id.get(incident_id, []) if _parse_time(r.get("at")) is not None),
            key=lambda r: _parse_time(r.get("at")),  # type: ignore[arg-type,return-value]
        )
        acks = [r for r in notes if r.get("action") == "ack"]
        last_ack = acks[-1] if acks else None
        last_ack_at = _parse_time(last_ack.get("at")) if last_ack else None

        timeline: list[TimelineEntry] = [
            TimelineEntry(
                at=o.at,
                kind="occurrence",
                severity=o.severity,
                actor=o.source,
                text=o.message,
                correlation_id=o.correlation_id,
            )
            for o in group.occurrences
        ]
        if group.resolved_at is not None:
            timeline.append(
                TimelineEntry(at=group.resolved_at, kind="resolved", text=group.resolved_by)
            )
        for record in notes:
            timeline.append(
                TimelineEntry(
                    at=_parse_time(record.get("at")),  # type: ignore[arg-type]
                    kind="ack" if record.get("action") == "ack" else "note",
                    actor=str(record.get("actor", "")),
                    text=str(record.get("text", "")),
                    correlation_id=str(record.get("correlation_id", "")),
                )
            )
        timeline.sort(key=lambda e: (e.at, e.kind))

        if group.resolved_at is not None:
            state = "resolved"
        elif last_ack_at is not None and last_ack_at >= group.last_seen:
            state = "acknowledged"
        else:
            state = "open"
        activity = max(e.at for e in timeline)
        out.append(
            IncidentView(
                id=incident_id,
                key=group.key,
                event=group.event,
                source=group.source,
                title=incident_title(group.occurrences[0].message),
                severity=group.severity,
                status=state,
                first_seen=group.first_seen,
                last_seen=group.last_seen,
                occurrences=Figure(
                    value=float(len(group.occurrences)),
                    provenance=count_provenance,
                    unit="count",
                    as_of=group.last_seen,
                ),
                acknowledged_by=str(last_ack.get("actor")) if last_ack else None,
                acknowledged_at=last_ack_at,
                resolved_at=group.resolved_at,
                deep_link=f"/system/incidents/{incident_id}",
                as_of=activity,
                timeline=tuple(timeline),
            )
        )
    out.sort(key=lambda i: (i.as_of, i.id), reverse=True)
    return out


@dataclass(frozen=True, slots=True)
class IncidentSnapshot:
    incidents: list[IncidentView]
    source: SourceNote
    caveats: tuple[Caveat, ...]


def alert_log_path(request_state: Any = None) -> Path | None:
    """The alert log this process reads: app state override, else the env."""
    override = getattr(request_state, "alert_log_path", None) if request_state else None
    if override is not None:
        return Path(override)
    raw = os.environ.get(ALERT_LOG_ENV, "").strip()
    return Path(raw) if raw else None


def load_incidents(
    platform: Any, settings: Any, *, alert_log: Path | None, now: datetime | None = None
) -> IncidentSnapshot:
    """Read every source and derive the incident list. Read-only."""
    stamp = now or datetime.now(tz=UTC)
    caveats: list[Caveat] = []
    rows: list[dict[str, Any]] = []
    detail_parts: list[str] = []
    if alert_log is None:
        caveats.append(
            Caveat(
                code="alert_log_not_configured",
                severity="warning",
                message=(
                    f"{ALERT_LOG_ENV} is not set, so dispatched alerts (dead worker, "
                    "stale data, limit breach) are not persisted anywhere this API "
                    "can read. Only kill-switch incidents are listed. An empty list "
                    "is NOT a quiet system."
                ),
                affects="incidents",
                direction="optimistic",
            )
        )
        detail_parts.append("alert log not configured")
    elif not alert_log.exists():
        detail_parts.append(f"alert log {alert_log} not yet written (no alert dispatched)")
    else:
        skipped = 0
        try:
            lines = alert_log.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            lines = []
            caveats.append(
                Caveat(
                    code="alert_log_unreadable",
                    severity="critical",
                    message=f"The alert log could not be read ({type(exc).__name__}).",
                    affects="incidents",
                    direction="optimistic",
                )
            )
        for line in lines:
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
        if skipped:
            caveats.append(
                Caveat(
                    code="alert_log_lines_skipped",
                    severity="warning",
                    message=f"{skipped} alert log line(s) did not parse and were skipped.",
                    affects="incidents",
                    direction="optimistic",
                )
            )
        detail_parts.append(f"{len(rows)} alert(s) in {alert_log.name}")
    ks_events = platform.kill_switch.history()
    detail_parts.append(f"{len(ks_events)} kill-switch journal event(s)")
    occurrences = occurrences_from_alerts(rows) + occurrences_from_kill_switch(ks_events)
    incidents = derive_incidents(
        occurrences,
        annotations_for(settings).records(),
        count_provenance=settings.provenance_for_execution(),
    )
    return IncidentSnapshot(
        incidents=incidents,
        source=SourceNote(
            kind="live",
            detail="Incidents derived from " + "; ".join(detail_parts) + ".",
            as_of=stamp,
        ),
        caveats=tuple(caveats),
    )


# ----------------------------------------------------------------- routes


def _snapshot(request: Request, platform: Any, settings: Any) -> IncidentSnapshot:
    return load_incidents(platform, settings, alert_log=alert_log_path(request.app.state))


@router.get("", response_model=Page[IncidentView])
def incidents(
    request: Request,
    platform: PlatformDep,
    settings: SettingsDep,
    status_filter: str | None = Query(
        None, alias="status", pattern="^(open|acknowledged|resolved)$"
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page[IncidentView]:
    snap = _snapshot(request, platform, settings)
    rows = snap.incidents
    if status_filter:
        rows = [i for i in rows if i.status == status_filter]
    return Page[IncidentView](
        items=rows[offset : offset + limit],
        total=len(rows),
        offset=offset,
        limit=limit,
        source=snap.source,
        caveats=snap.caveats,
    )


def _find(request: Request, platform: Any, settings: Any, incident_id: str) -> IncidentView:
    for incident in _snapshot(request, platform, settings).incidents:
        if incident.id == incident_id:
            return incident
    raise ApiError(
        status.HTTP_404_NOT_FOUND,
        "incident_not_found",
        f"No incident {incident_id!r} is derivable from the alert log or the "
        "kill-switch journal.",
    )


@router.get("/{incident_id}", response_model=Envelope[IncidentView])
def incident(
    incident_id: str, request: Request, platform: PlatformDep, settings: SettingsDep
) -> Envelope[IncidentView]:
    snap = _snapshot(request, platform, settings)
    found = next((i for i in snap.incidents if i.id == incident_id), None)
    if found is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "incident_not_found",
            f"No incident {incident_id!r} is derivable from the alert log or the "
            "kill-switch journal.",
        )
    return Envelope[IncidentView](data=found, source=snap.source, caveats=snap.caveats)


def _annotate(
    *,
    action: str,
    incident_id: str,
    text: str,
    request: Request,
    platform: Any,
    settings: Any,
    audit: Any,
    principal: Principal,
) -> Envelope[IncidentView]:
    audit_action = f"incident.{action}"

    def record(outcome: str, detail: dict[str, Any] | None = None) -> None:
        audit.record(
            audit_action,
            actor=principal.user_id,
            actor_role=principal.role.value,
            outcome=outcome,
            reason=text,
            target=incident_id,
            execution_mode=settings.execution_mode.value,
            correlation_id=current_correlation_id(),
            source_ip=request.client.host if request.client else "",
            detail=detail or {},
        )

    try:
        current = _find(request, platform, settings, incident_id)
    except ApiError:
        record("failed", {"error": "incident_not_found"})
        raise
    if action == "ack" and current.status == "acknowledged":
        record("refused", {"error": "already_acknowledged"})
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "incident_already_acknowledged",
            f"Incident {incident_id} was already acknowledged by "
            f"{current.acknowledged_by} and has not recurred since. Add a note instead.",
        )
    annotations_for(settings).append(
        {
            "incident_id": incident_id,
            "action": action,
            "actor": principal.user_id,
            "actor_role": principal.role.value,
            "text": text,
            "at": datetime.now(tz=UTC).isoformat(),
            "correlation_id": current_correlation_id(),
        }
    )
    record("allowed", {"status_before": current.status})
    snap = _snapshot(request, platform, settings)
    updated = next(i for i in snap.incidents if i.id == incident_id)
    return Envelope[IncidentView](data=updated, source=snap.source, caveats=snap.caveats)


@router.post("/{incident_id}/ack", response_model=Envelope[IncidentView])
def acknowledge(
    incident_id: str,
    body: IncidentAckRequest,
    request: Request,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
) -> Envelope[IncidentView]:
    """Record that a named human has seen this incident. Does not resolve it."""
    return _annotate(
        action="ack",
        incident_id=incident_id,
        text=body.reason,
        request=request,
        platform=platform,
        settings=settings,
        audit=audit,
        principal=principal,
    )


@router.post("/{incident_id}/note", response_model=Envelope[IncidentView])
def annotate(
    incident_id: str,
    body: IncidentNoteRequest,
    request: Request,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
) -> Envelope[IncidentView]:
    """Append a note to the incident's timeline. Audited; never edits anything."""
    return _annotate(
        action="note",
        incident_id=incident_id,
        text=body.text,
        request=request,
        platform=platform,
        settings=settings,
        audit=audit,
        principal=principal,
    )
