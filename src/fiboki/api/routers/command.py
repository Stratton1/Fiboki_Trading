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
``limit_breach``       limit-board rows in the ``breached`` band
``limit_near``         limit-board rows in the ``critical`` / ``warn`` bands
                       (``api/limit_board.py``: the same rows the Risk bars
                       draw, so the queue and the bars cannot disagree)
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
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api import links
from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.health import build_health
from fiboki.api.limit_board import attention_rows, build_limit_board
from fiboki.api.market_sessions import MarketSessionView, market_sessions
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.paper_journal import read_unclosed_abandons
from fiboki.api.provenance import Caveat, Figure, figure

router = APIRouter(prefix="/api/command", tags=["command"])

__all__ = [
    "CATEGORY_WEIGHT",
    "SEVERITY_WEIGHT",
    "AttentionItem",
    "collect_attention",
    "current_workers",
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
    "limit_near": 75,
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


def current_workers(workers: Iterable[Any]) -> list[Any]:
    """The newest non-stopped beat of each kind.

    A replaced process leaves its last heartbeat row as ``idle``. That row is
    not the worker. Only the newest beat per kind can be called stale.
    """
    newest: dict[str, Any] = {}
    for worker in workers:
        if getattr(worker, "status", "") in {"stopped", "crashed"}:
            continue
        held = newest.get(worker.kind)
        if held is None or worker.beat_at > held.beat_at:
            newest[worker.kind] = worker
    return list(newest.values())


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
                    deep_link=links.SYSTEM,
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
                deep_link=links.RISK,
                as_of=since,
            )
        )

    # -- limits: the limit board's own bands -------------------------------
    def breaches() -> None:
        # A breach computed on the demonstration fixture is not a breach of
        # anything; the seed_fixture caveat item already says nothing is
        # measured. Raising a critical alarm from generated rows would be the
        # V1 error of a fixture that looks like a measurement. The board says
        # the same thing (``fixture: true``), so the two still agree.
        if platform.data_source == "seed":
            return
        board = build_limit_board(platform, settings)
        as_of = board.sources["risk"].as_of
        for row, severity in attention_rows(board):
            used = row.utilisation_pct.value
            if row.breached:
                title = f"{row.label}: limit breached"
            else:
                title = f"{row.label} at {used:.0f}% of its limit"
            what = f"{row.kind} " if row.kind else ""
            reason = (
                f"{what}{row.label}: {used:.1f}% of the limit used"
                if used is not None
                else f"{what}{row.label}: breached; utilisation not computable"
            )
            items.append(
                _item(
                    p,
                    id=f"limit:{row.key}",
                    category="limit_breach" if row.breached else "limit_near",
                    severity=severity,
                    title=title,
                    reason=reason + f" (band {row.band.upper()}, limit set {board.limits_version}).",
                    deep_link=row.deep_link,
                    as_of=row.value.as_of if row.value is not None and row.value.as_of else as_of,
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
                        deep_link=links.SYSTEM,
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
                    deep_link=links.SYSTEM,
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
                    deep_link=links.SYSTEM,
                    as_of=stamp,
                )
            )
        else:
            # A replaced process leaves its last row idle. That row is not the
            # worker. Only the newest beat of each kind can be stale.
            for worker in current_workers(beat.workers):
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
                            deep_link=links.SYSTEM,
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
                    deep_link=links.SYSTEM,
                    as_of=report.checked_at,
                )
            )

    # -- strategies awaiting review --------------------------------------
    def strategies() -> None:
        for status_obj in platform.lifecycle.statuses():
            state = status_obj.lifecycle.value
            link = links.lifecycle(status_obj.strategy_content_hash)
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
    def abandoned_forward() -> None:
        found = [row for book in session_books(platform, settings, p) for row in book.abandoned]
        if not found:
            return
        def _shown(fig: Figure) -> str:
            return "no data" if fig.value is None else f"{fig.value:g}"

        detail = "; ".join(
            f"{row.instrument} {row.direction} {_shown(row.size)} @ {_shown(row.entry_price)} "
            f"({row.at.isoformat()})"
            for row in found
        )
        items.append(
            _item(
                p,
                id="abandoned_forward",
                category="caveat",
                severity="warning",
                title=(
                    f"{len(found)} forward position(s) abandoned, unrealised P&L not booked"
                ),
                reason=(
                    "A restart had no snapshot to restore, so these positions died "
                    "with the previous process. Unrealised P&L was not carried and "
                    "is not estimated here: " + detail
                ),
                deep_link=links.JOURNAL,
                as_of=max(row.at for row in found),
            )
        )

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
                    deep_link=links.JOURNAL,
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
        ("forward_journal", abandoned_forward),
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


# ------------------------------------------------------------- money strip


class BotCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: Figure
    running: Figure
    idle: Figure
    stale: Figure
    halted: Figure
    finished: Figure
    failed: Figure


class AbandonedPositionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    position_id: str
    instrument: str
    direction: str
    size: Figure
    entry_price: Figure
    at: datetime
    note: str


class BookStrip(BaseModel):
    """One paper session, in its own currency. Never summed with another."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    strategy_id: str
    instruments: str
    currency: str
    equity: Figure
    unrealised_pnl: Figure
    open_positions: Figure
    day_pnl: Figure
    week_pnl: Figure
    pnl_day: str | None
    abandoned: list[AbandonedPositionView] = Field(default_factory=list)


class CommandSummaryView(BaseModel):
    """The money strip: the book, the fleet and the research load, all decided here."""

    model_config = ConfigDict(frozen=True)

    provenance: str
    currency: str | None
    equity: Figure
    balance: Figure
    unrealised_pnl: Figure
    day_pnl: Figure
    day_pnl_pct: Figure
    week_pnl: Figure
    open_positions: Figure
    bots: BotCounts
    research_in_flight: Figure
    kill_switch_active: bool
    kill_switch_mode: str | None
    new_risk_permitted: bool
    #: The UTC day "today" means for day P&L (the account's last bar).
    pnl_day: str | None
    #: Monday of the ISO week "week P&L" covers.
    pnl_week_start: str | None
    #: One entry per paper session in this mode. Mixed currencies stay here
    #: and are not folded into ``equity``.
    books: list[BookStrip] = Field(default_factory=list)
    server_time: datetime
    sessions: list[MarketSessionView]
    links: dict[str, str]


def _abandon_figure(value: float | None, provenance: Any, unit: str, as_of: datetime, affects: str) -> Figure:
    """The journalled size or entry. Absence stays missing. P&L is not implied."""
    caveat = Caveat(
        code="pnl_not_carried",
        severity="warning",
        message="Unrealised P&L was not carried for this abandoned position.",
        affects=affects,
        direction="unknown",
    )
    if value is None:
        missing = Figure.missing(
            provenance,
            unit=unit,
            reason="The journal did not record this number.",
        )
        return missing.model_copy(update={"as_of": as_of, "caveats": missing.caveats + (caveat,)})
    return figure(
        float(value),
        provenance,
        unit=unit,
        as_of=as_of,
        extra_caveats=(caveat,),
    )


def session_books(platform: Any, settings: Any, provenance: Any) -> list[BookStrip]:
    """Per-session figures. Day P&L uses that session's trades and currency only."""
    journal = getattr(platform, "journal", None)
    if journal is None:
        return []
    state_dir = getattr(settings, "state_dir", None)
    books: list[BookStrip] = []
    for session in journal.sessions:
        if session.provenance is not provenance:
            continue
        ccy = session.account_ccy
        as_of = session.as_of
        if as_of is None:
            day_pnl = Figure.missing(
                provenance, unit=ccy, reason="This session has no last bar, so day P&L is unknown."
            )
            week_pnl = Figure.missing(
                provenance, unit=ccy, reason="This session has no last bar, so week P&L is unknown."
            )
            pnl_day = None
        else:
            day = as_of.date()
            week_start = day - timedelta(days=day.weekday())
            day_trades = [t for t in session.trades if t.exit_time.date() == day]
            week_trades = [
                t for t in session.trades if week_start <= t.exit_time.date() <= day
            ]
            day_pnl = figure(
                round(sum(t.net_pnl for t in day_trades), 2),
                provenance,
                settings=settings,
                unit=ccy,
                as_of=as_of,
                sample_size=len(day_trades),
                affects="net_pnl",
            )
            week_pnl = figure(
                round(sum(t.net_pnl for t in week_trades), 2),
                provenance,
                settings=settings,
                unit=ccy,
                as_of=as_of,
                sample_size=len(week_trades),
                affects="net_pnl",
            )
            pnl_day = day.isoformat()
        unrealised = (
            round(session.equity - session.balance, 2)
            if session.equity is not None and session.balance is not None
            else None
        )
        name = session.session_id.removeprefix("forward-")
        journal_path = (
            state_dir / "paper_forward" / name / "journal.jsonl" if state_dir is not None else None
        )
        abandoned = (
            [
                AbandonedPositionView(
                    position_id=row.position_id,
                    instrument=row.instrument,
                    direction=row.direction,
                    size=_abandon_figure(row.size, provenance, "units", row.at, "size"),
                    entry_price=_abandon_figure(
                        row.entry_price, provenance, ccy or "", row.at, "entry_price"
                    ),
                    at=row.at,
                    note=row.note,
                )
                for row in read_unclosed_abandons(journal_path)
            ]
            if journal_path is not None
            else []
        )
        books.append(
            BookStrip(
                session_id=session.session_id,
                strategy_id=session.strategy_id,
                instruments=session.instrument,
                currency=ccy,
                equity=figure(
                    session.equity,
                    provenance,
                    settings=settings,
                    unit=ccy,
                    as_of=as_of,
                    affects="equity",
                    sample_size=len(session.trades),
                ),
                unrealised_pnl=Figure(
                    value=unrealised,
                    provenance=provenance,
                    unit=ccy,
                    as_of=as_of,
                    estimated=unrealised is not None,
                ),
                open_positions=Figure(
                    value=float(len(session.positions)),
                    provenance=provenance,
                    unit="count",
                    as_of=as_of,
                ),
                day_pnl=day_pnl,
                week_pnl=week_pnl,
                pnl_day=pnl_day,
                abandoned=abandoned,
            )
        )
    return books


def command_summary(platform: Any, settings: Any, *, now: datetime | None = None) -> CommandSummaryView:
    from fiboki.api.routers import fleet as fleet_router
    from fiboki.api.routers import trading as trading_router

    stamp = now or datetime.now(tz=UTC)
    p = settings.provenance_for_execution()
    portfolio = trading_router.portfolio(platform, settings).data
    risk = trading_router.risk_state(platform, settings).data
    account = trading_router._mode_account(platform, p)
    # A mixed-currency journal has an account and no currency. Labelling the
    # missing total with the GBP fallback would present one book's unit as the
    # whole strip.
    if account is not None and account.account_ccy:
        ccy = account.account_ccy
    elif account is not None:
        ccy = None
    else:
        ccy = portfolio.equity.unit or None
    as_of = account.as_of if account is not None else None
    starting = account.initial_balance if account is not None else None
    executed, _ = platform.trades(provenance={p}, limit=100_000)
    reason = (
        "No paper account exists (the trade record is "
        f"{platform.data_source}), so day and week P&L are unknown, not zero."
    )
    if account is not None and account.account_ccy and as_of is not None:
        day = as_of.date()
        week_start = day - timedelta(days=day.weekday())
        day_trades = [t for t in executed if t.exit_time.date() == day]
        week_trades = [t for t in executed if week_start <= t.exit_time.date() <= day]
        day_value = round(sum(t.net_pnl for t in day_trades), 2)
        day_pnl = figure(
            day_value, p, settings=settings, unit=ccy or "", as_of=as_of,
            sample_size=len(day_trades), affects="net_pnl",
        )
        week_pnl = figure(
            round(sum(t.net_pnl for t in week_trades), 2), p, settings=settings,
            unit=ccy or "", as_of=as_of, sample_size=len(week_trades), affects="net_pnl",
        )
        day_pct = Figure(
            value=round(day_value / starting * 100.0, 3) if starting else None,
            provenance=p, unit="pct", as_of=as_of, sample_size=len(day_trades),
        )
        pnl_day, pnl_week = day.isoformat(), week_start.isoformat()
    else:
        if account is not None and not account.account_ccy:
            reason = (
                "The paper sessions are in different currencies, so day and week "
                "P&L are reported on each book and not summed."
            )
        day_pnl = Figure.missing(p, unit=ccy or "", reason=reason)
        week_pnl = Figure.missing(p, unit=ccy or "", reason=reason)
        day_pct = Figure.missing(p, unit="pct", reason=reason)
        pnl_day = pnl_week = None

    rows = fleet_router.fleet_rows(platform, settings, now=stamp)
    journal_as_of = platform.journal_as_of()

    def count(state: str | None) -> Figure:
        if platform.journal is None:
            return Figure.missing(
                p, unit="count", reason="No paper journal: no bot has run. Not an idle fleet."
            )
        n = len(rows) if state is None else sum(1 for r in rows if r.state == state)
        return Figure(value=float(n), provenance=p, unit="count", as_of=journal_as_of)

    ks = platform.kill_switch_replayed()
    return CommandSummaryView(
        provenance=p.value,
        currency=ccy,
        equity=portfolio.equity,
        balance=portfolio.balance,
        unrealised_pnl=portfolio.unrealised_pnl,
        day_pnl=day_pnl,
        day_pnl_pct=day_pct,
        week_pnl=week_pnl,
        open_positions=portfolio.open_positions,
        bots=BotCounts(
            total=count(None),
            running=count("running"),
            idle=count("idle"),
            stale=count("stale"),
            halted=count("halted"),
            finished=count("finished"),
            failed=count("failed"),
        ),
        research_in_flight=Figure.missing(
            p,
            unit="count",
            reason="No research orchestrator is attached to this deployment, so jobs in "
            "flight are unknown. Not zero.",
        ),
        kill_switch_active=bool(ks.active),
        kill_switch_mode=ks.mode.value if ks.mode else None,
        new_risk_permitted=risk.new_risk_permitted,
        pnl_day=pnl_day,
        pnl_week_start=pnl_week,
        books=session_books(platform, settings, p),
        server_time=stamp,
        sessions=market_sessions(stamp),
        links={
            "equity": "/trading/portfolio",
            "positions": links.FLEET,
            "bots": links.FLEET,
            "day_pnl": links.JOURNAL,
            "research": "/research",
            "risk": links.RISK,
        },
    )


@router.get("/summary", response_model=Envelope[CommandSummaryView])
def summary(platform: PlatformDep, settings: SettingsDep) -> Envelope[CommandSummaryView]:
    """Equity, day and week P&L, positions, bots and research load. Server-computed."""
    from fiboki.api.routers import trading as trading_router

    view = command_summary(platform, settings)
    caveats: list[Caveat] = list(trading_router._record_caveats(platform))
    abandoned = [row for book in view.books for row in book.abandoned]
    if abandoned:
        caveats.append(
            Caveat(
                code="positions_abandoned",
                severity="warning",
                message=(
                    f"{len(abandoned)} forward position(s) were abandoned at a restart "
                    "and their unrealised P&L was not carried. No estimate is shown."
                ),
                affects="equity",
                direction="unknown",
            )
        )
    caveats.append(
        Caveat(
            code="day_is_last_bar",
            severity="info",
            message="Day and week P&L count closed trades by the UTC date of the account's "
            "last bar (a replayed journal's 'today' is its last replayed day), not the wall "
            "clock. Unrealised P&L is not included in either.",
            affects="day_pnl",
            direction="unknown",
        )
    )
    return Envelope(
        data=view,
        source=SourceNote(
            kind="seed" if platform.data_source == "seed" else "live",
            detail="Portfolio, risk state, paper sessions and the kill-switch journal, read now. "
            + platform.trade_record_detail(),
            as_of=platform.journal_as_of() or view.server_time,
        ),
        caveats=tuple(caveats),
    )
