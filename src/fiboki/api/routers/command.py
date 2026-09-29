"""COMMAND: the server-ranked attention queue.

"Is anything wrong, and what needs me now?" is answered here, not in the page.
Ranking is logic, so it lives in the backend with its weights in code and a
test that pins them; the UI renders ``items`` in the order given and never
re-sorts them.

Sources, all read-only and all the same readers the REST routes use:

=====================  ======================================================
category               source
=====================  ======================================================
``kill_switch``        kill-switch journal, replayed now
``limit_breach``       ``/api/trading/risk`` breaches and ``/exposure`` rows
``incident``           the incident read model (open incidents only)
``stale_worker``       the ``worker_heartbeat`` table reader
``health``             ``build_health`` checks that are not ok
``strategy_review``    lifecycle statuses (halts, quarantine, degradation,
                       candidates awaiting promotion, missing registrations)
``caveat``             warning-level caveats on the trade record
=====================  ======================================================

Score = ``SEVERITY_WEIGHT[severity] + CATEGORY_WEIGHT[category]``. Severity
dominates by construction (the smallest severity gap, 200, exceeds the largest
category spread, 90), so a critical caveat can never sit below a warning-level
incident. Ties break on the newest ``as_of`` then on ``id``, so the order is
total and deterministic.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.health import build_health
from fiboki.api.models import Page, SourceNote
from fiboki.api.provenance import Caveat, Figure

router = APIRouter(prefix="/api/command", tags=["command"])

__all__ = [
    "CATEGORY_WEIGHT",
    "SEVERITY_WEIGHT",
    "AttentionItem",
    "collect_attention",
    "rank_attention",
    "router",
]

#: How bad. The gap between adjacent severities (>= 200) is larger than the
#: whole category spread, so severity always decides first.
SEVERITY_WEIGHT: dict[str, int] = {
    "critical": 1000,
    "error": 700,
    "warning": 400,
    "info": 200,
}

#: Within one severity, what to look at first. A halt and a breach move money;
#: an incident may; a dead worker means nothing is being evaluated; a caveat is
#: a qualifier on a number.
CATEGORY_WEIGHT: dict[str, int] = {
    "kill_switch": 90,
    "limit_breach": 80,
    "incident": 70,
    "stale_worker": 60,
    "health": 50,
    "strategy_review": 40,
    "caveat": 0,
}


class AttentionItem(BaseModel):
    """One thing an operator should look at, with where to go to act on it."""

    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    severity: str = Field(pattern="^(info|warning|error|critical)$")
    title: str
    reason: str
    deep_link: str
    as_of: datetime | None
    #: The ranking score. Labelled like every number; its provenance is the
    #: deployment's execution provenance because it is derived from this
    #: deployment's state.
    score: Figure


def _digest(text: str) -> str:
    """A stable short id component. ``hash()`` is salted per process."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def score_of(category: str, severity: str) -> int:
    return SEVERITY_WEIGHT[severity] + CATEGORY_WEIGHT[category]


def rank_attention(items: Iterable[AttentionItem]) -> list[AttentionItem]:
    """Highest score first; newest ``as_of`` next; ``id`` last. Total order."""

    def key(item: AttentionItem) -> tuple[float, float, str]:
        stamp = item.as_of.timestamp() if item.as_of is not None else float("-inf")
        return (-(item.score.value or 0.0), -stamp, item.id)

    return sorted(items, key=key)


# ------------------------------------------------------------- collection


def _item(
    provenance: Any,
    *,
    id: str,
    category: str,
    severity: str,
    title: str,
    reason: str,
    deep_link: str,
    as_of: datetime | None,
) -> AttentionItem:
    return AttentionItem(
        id=id,
        category=category,
        severity=severity,
        title=title,
        reason=reason,
        deep_link=deep_link,
        as_of=as_of,
        score=Figure(
            value=float(score_of(category, severity)),
            provenance=provenance,
            unit="score",
        ),
    )


def collect_attention(
    platform: Any,
    settings: Any,
    *,
    alert_log: Any = None,
    now: datetime | None = None,
) -> tuple[list[AttentionItem], list[str]]:
    """Every attention item, ranked, plus the sources that could not be read.

    A source that raises is reported as an item of its own (``health``,
    ``error``) rather than skipped: an attention queue that silently drops a
    source is the flat-dashboard failure again.
    """
    from fiboki.api.routers import incidents as incidents_router
    from fiboki.api.routers import trading as trading_router

    stamp = now or datetime.now(tz=UTC)
    p = settings.provenance_for_execution()
    items: list[AttentionItem] = []
    failed: list[str] = []

    def guarded(name: str, fn: Any) -> None:
        try:
            fn()
        except Exception as exc:
            failed.append(name)
            items.append(
                _item(
                    p,
                    id=f"source_unreadable:{name}",
                    category="health",
                    severity="error",
                    title=f"{name} could not be read",
                    reason=f"The {name} source raised {type(exc).__name__}; its items "
                    "are missing from this queue, which is therefore incomplete.",
                    deep_link="/system",
                    as_of=stamp,
                )
            )

    # -- kill switch -----------------------------------------------------
    def kill_switch() -> None:
        state = platform.kill_switch_replayed()
        if not state.active:
            return
        mode = state.mode.value if state.mode else "unknown"
        since = state.since.to_pydatetime() if state.since is not None else None
        items.append(
            _item(
                p,
                id="kill_switch:armed",
                category="kill_switch",
                severity="critical" if mode == "flatten" else "warning",
                title=f"Kill switch ARMED ({mode.upper()})",
                reason=f"Armed by {state.operator or 'an unrecorded operator'}: "
                f"{state.reason or 'no reason recorded'}. New risk is refused.",
                deep_link="/risk",
                as_of=since,
            )
        )

    # -- limit breaches --------------------------------------------------
    def breaches() -> None:
        # A breach computed on the demonstration fixture is not a breach of
        # anything; the seed_fixture caveat item already says nothing is
        # measured. Raising a critical alarm from generated rows would be the
        # V1 error of a fixture that looks like a measurement.
        if platform.data_source == "seed":
            return
        risk = trading_router.risk_state(platform, settings)
        as_of = risk.source.as_of
        for index, text in enumerate(risk.data.breaches):
            items.append(
                _item(
                    p,
                    id=f"limit_breach:risk:{index}",
                    category="limit_breach",
                    severity="critical",
                    title="Risk limit breached",
                    reason=text,
                    deep_link="/risk",
                    as_of=as_of,
                )
            )
        exposure = trading_router.exposure(platform, settings)
        for row in exposure.items:
            if not row.breached:
                continue
            items.append(
                _item(
                    p,
                    id=f"limit_breach:{row.key}",
                    category="limit_breach",
                    severity="critical",
                    title=f"Exposure limit breached: {row.label}",
                    reason=f"{row.key} exposure {row.exposure_pct.value}% exceeds its "
                    f"{row.limit_pct.value}% limit.",
                    deep_link="/risk",
                    as_of=exposure.source.as_of,
                )
            )

    # -- incidents -------------------------------------------------------
    def incidents() -> None:
        snap = incidents_router.load_incidents(
            platform, settings, alert_log=alert_log, now=stamp
        )
        for incident in snap.incidents:
            if incident.status != "open":
                continue
            items.append(
                _item(
                    p,
                    id=f"incident:{incident.id}",
                    category="incident",
                    severity=incident.severity,
                    title=incident.title,
                    reason=f"{incident.event}: {incident.occurrences.value:.0f} "
                    f"occurrence(s), last {incident.last_seen.isoformat()}; not "
                    "acknowledged.",
                    deep_link=incident.deep_link,
                    as_of=incident.last_seen,
                )
            )
        for caveat in snap.caveats:
            if caveat.severity in ("warning", "critical"):
                items.append(
                    _item(
                        p,
                        id=f"caveat:{caveat.code}",
                        category="caveat",
                        severity=caveat.severity,
                        title=f"Incident feed: {caveat.code}",
                        reason=caveat.message,
                        deep_link="/system",
                        as_of=stamp,
                    )
                )

    # -- workers ---------------------------------------------------------
    def workers() -> None:
        beat = platform.worker_heartbeat()
        stale_after = settings.worker_heartbeat_stale_seconds
        if beat.state == "stale":
            items.append(
                _item(
                    p,
                    id="stale_worker:newest",
                    category="stale_worker",
                    severity="critical",
                    title="Worker heartbeat is stale",
                    reason=f"The newest worker beat is {beat.age_seconds:.0f}s old "
                    f"(stale at {stale_after:.0f}s). Nothing may be evaluating signals.",
                    deep_link="/system",
                    as_of=beat.newest_beat_at,
                )
            )
        elif beat.state == "absent":
            unreadable = beat.reason == "unreadable"
            items.append(
                _item(
                    p,
                    id=f"stale_worker:{beat.reason}",
                    category="stale_worker",
                    severity="critical" if unreadable else "error",
                    title="Worker liveness unreadable"
                    if unreadable
                    else "No worker has ever beaten",
                    reason=beat.detail,
                    deep_link="/system",
                    as_of=stamp,
                )
            )
        else:
            for worker in beat.workers:
                if worker.status in {"stopped", "crashed"}:
                    continue
                if worker.age_seconds >= stale_after:
                    items.append(
                        _item(
                            p,
                            id=f"stale_worker:{worker.worker_id}",
                            category="stale_worker",
                            severity="warning",
                            title=f"Worker {worker.worker_id} is stale",
                            reason=f"Last beat {worker.age_seconds:.0f}s ago while "
                            "another worker is alive.",
                            deep_link="/system",
                            as_of=worker.beat_at,
                        )
                    )

    # -- health ----------------------------------------------------------
    def health() -> None:
        report = build_health(platform, settings)
        covered = {"worker_heartbeat", "kill_switch"}
        for check in report.checks:
            if check.status == "ok" or check.name in covered:
                continue
            severity = "error" if check.status == "down" and check.critical else "warning"
            items.append(
                _item(
                    p,
                    id=f"health:{check.name}",
                    category="health",
                    severity=severity,
                    title=f"Health check {check.name} is {check.status}",
                    reason=check.detail,
                    deep_link="/system",
                    as_of=report.checked_at,
                )
            )

    # -- strategies awaiting review --------------------------------------
    def strategies() -> None:
        for status_obj in platform.lifecycle.statuses():
            state = status_obj.lifecycle.value
            link = f"/lifecycle/{status_obj.strategy_content_hash}"
            as_of = status_obj.last_evaluated_at or status_obj.entered_state_at
            sid = status_obj.strategy_id
            base = f"strategy_review:{status_obj.strategy_content_hash}"
            if status_obj.latched_halts:
                halts = ", ".join(k.value for k in status_obj.latched_halts)
                items.append(
                    _item(
                        p,
                        id=f"{base}:halt",
                        category="strategy_review",
                        severity="error",
                        title=f"{sid}: stopping rule latched",
                        reason=f"Latched halt(s) {halts}; only a named operator can "
                        "release them.",
                        deep_link=link,
                        as_of=as_of,
                    )
                )
            review = {
                "quarantined": ("error", "quarantined; cannot trade until reviewed"),
                "degraded": ("warning", "degraded; review before it is demoted further"),
                "watch": ("info", "on watch"),
                "candidate": ("info", "candidate awaiting a promotion decision"),
            }.get(state)
            if review is not None:
                severity, text = review
                items.append(
                    _item(
                        p,
                        id=f"{base}:{state}",
                        category="strategy_review",
                        severity=severity,
                        title=f"{sid}: {state}",
                        reason=f"{sid} is {text}.",
                        deep_link=link,
                        as_of=as_of,
                    )
                )
            if status_obj.missing_rule_registrations:
                missing = ", ".join(k.value for k in status_obj.missing_rule_registrations)
                items.append(
                    _item(
                        p,
                        id=f"{base}:missing_rules",
                        category="strategy_review",
                        severity="warning",
                        title=f"{sid}: stopping rules not pre-registered",
                        reason=f"No pre-registration for {missing}; those rules cannot "
                        "fire for this strategy.",
                        deep_link=link,
                        as_of=as_of,
                    )
                )

    # -- caveats on the trade record ---------------------------------------
    def record_caveats() -> None:
        for caveat in trading_router._record_caveats(platform):
            if caveat.severity not in ("warning", "critical"):
                continue
            items.append(
                _item(
                    p,
                    id=f"caveat:{caveat.code}:{_digest(caveat.message)}",
                    category="caveat",
                    severity=caveat.severity,
                    title=f"Trade record: {caveat.code}",
                    reason=caveat.message,
                    deep_link="/journal",
                    as_of=platform.journal_as_of(),
                )
            )

    for name, fn in (
        ("kill_switch_journal", kill_switch),
        ("risk_limits", breaches),
        ("incidents", incidents),
        ("worker_heartbeat", workers),
        ("health", health),
        ("lifecycle", strategies),
        ("trade_record", record_caveats),
    ):
        guarded(name, fn)
    return rank_attention(items), failed


@router.get("/attention", response_model=Page[AttentionItem])
def attention(request: Request, platform: PlatformDep, settings: SettingsDep) -> Page[AttentionItem]:
    """What needs a human, most urgent first. The order IS the ranking."""
    from fiboki.api.routers.incidents import alert_log_path

    stamp = datetime.now(tz=UTC)
    items, failed = collect_attention(
        platform, settings, alert_log=alert_log_path(request.app.state), now=stamp
    )
    caveats: tuple[Caveat, ...] = (
        Caveat(
            code="attention_ranking",
            severity="info",
            message="Ranked server-side: severity weight plus category weight, then "
            "newest first. Render in the order given; do not re-sort.",
            affects="order",
            direction="unknown",
        ),
    )
    if failed:
        caveats += (
            Caveat(
                code="attention_incomplete",
                severity="critical",
                message="These sources could not be read, so this queue is "
                "incomplete: " + ", ".join(failed) + ".",
                affects="items",
                direction="optimistic",
            ),
        )
    return Page[AttentionItem](
        items=items,
        total=len(items),
        limit=len(items) or 1,
        source=SourceNote(
            kind="mixed",
            detail="Kill-switch journal, risk limits, incidents, worker heartbeat, "
            "health checks, lifecycle store and trade-record caveats, read now.",
            as_of=stamp,
        ),
        caveats=caveats,
    )
