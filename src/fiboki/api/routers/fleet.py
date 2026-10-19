"""FLEET: one row per paper bot, and P&L attributed by strategy and instrument.

A *bot* is one paper session the journal holds: a forward session (the FX
paper-forward service, the 24/7 crypto one) or a replay. Everything the fleet
screen shows about a bot is decided here, from what the session persisted:

* ``state`` -- ``running`` (a forward session that wrote its summary within its
  expected cadence), ``idle`` (a forward FX session over the closed FX
  weekend), ``stale`` (a forward session silent past its cadence while its
  market is open), ``halted`` (the kill switch is armed, or the strategy has a
  latched halt or is quarantined), ``finished`` (a replay that ran to its end)
  or ``failed`` (a replay whose worker exited non-zero);
* ``health`` -- ``ok`` / ``warn`` / ``critical``, with the reason;
* the last gateway decision (``telemetry.jsonl``), the open position, the
  unrealised, day and realised P&L, each a Figure with the session's
  provenance.

"Today" for a session is the UTC date of its last bar, the rule
``GET /api/trading/risk`` applies: a replay that ended in 2025 has no trades
"today", and 0.00 would read as a calm day rather than as not applicable.

The workstation renders these rows. It computes none of them.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api import links
from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.market_sessions import fx_week_open
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure, figure
from fiboki.core import instruments as instrument_registry

router = APIRouter(prefix="/api/trading", tags=["trading"])

__all__ = [
    "AttributionRow",
    "AttributionView",
    "FleetRowView",
    "attribution_view",
    "fleet_rows",
    "router",
]

_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
#: A forward session is stale when its summary is older than this many bars
#: plus the grace below. Two bars: one missed close is a late candle; two is
#: a silent worker.
STALE_BARS = 2
STALE_GRACE_S = 300.0


class LinkSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy: str
    chart: str | None
    journal: str
    lifecycle: str | None
    session: str


class LastSignalView(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    outcome: str = Field(pattern="^(accepted|blocked)$")
    kind: str
    reason: str
    instrument: str | None


class OpenPositionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    position_id: str
    instrument: str
    direction: str
    size: Figure
    entry_price: Figure
    mark_price: Figure
    unrealised_pnl: Figure
    chart: str


class FleetRowView(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    label: str
    strategy_id: str
    instruments: list[str]
    timeframe: str | None
    asset_class: str
    kind: str = Field(pattern="^(forward|replay)$")
    provenance: str
    state: str = Field(pattern="^(running|idle|stale|halted|finished|failed)$")
    state_reason: str
    health: str = Field(pattern="^(ok|warn|critical)$")
    health_reason: str
    last_signal: LastSignalView | None
    last_signal_absent: str | None
    open_positions: Figure
    positions: list[OpenPositionView]
    unrealised_pnl: Figure
    day_pnl: Figure
    realised_pnl: Figure
    closed_trades: Figure
    equity: Figure
    #: Seconds since the session last wrote its summary, by the API clock.
    summary_age: Figure
    as_of: datetime | None
    written_at: datetime
    lifecycle: str | None
    content_hash: str | None
    #: The bot's operator control state (``ops/controls``): ``active``,
    #: ``paused``, ``draining`` or ``stopped``; ``None`` for a replay, which no
    #: control can bind.
    control_state: str | None = None
    #: Who set it and why, in one line; ``None`` when never controlled.
    control_note: str | None = None
    links: LinkSet


class AttributionRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    trades: Figure
    wins: Figure
    win_rate: Figure
    gross_pnl: Figure
    costs: Figure
    net_pnl: Figure
    day_pnl: Figure
    unrealised_pnl: Figure
    #: This row's share of the absolute net P&L of every row, in percent.
    share_of_abs_pct: Figure
    link: str
    chart: str | None


class AttributionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    provenance: str
    currency: str | None
    by_strategy: list[AttributionRow]
    by_instrument: list[AttributionRow]
    total_net_pnl: Figure


# ------------------------------------------------------------------ helpers


def _asset_class(symbols: list[str]) -> str:
    classes = set()
    for sym in symbols:
        try:
            inst = instrument_registry.get(sym)
        except Exception:
            classes.add("unknown")
            continue
        value = inst.asset_class.value
        classes.add("fx" if value in ("fx_major", "fx_cross") else value)
    if not classes:
        return "unknown"
    return classes.pop() if len(classes) == 1 else "mixed"


def _trades_24_7(symbols: list[str]) -> bool:
    for sym in symbols:
        try:
            if instrument_registry.get(sym).trading_hours != "crypto_24_7":
                return False
        except Exception:
            return False
    return bool(symbols)


def _last_signal(platform: Any, session_id: str) -> LastSignalView | None:
    rows = platform.session_telemetry(session_id)
    best: tuple[datetime, dict[str, Any]] | None = None
    for row in rows:
        raw = row.get("decided_at")
        try:
            at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if best is None or at > best[0]:
            best = (at, row)
    if best is None:
        return None
    at, row = best
    decision = row.get("decision") or {}
    extra = row.get("extra") or {}
    reasons = [str(r) for r in decision.get("reasons") or []]
    kind = str(extra.get("request_kind", "open"))
    allowed = bool(decision.get("allowed"))
    return LastSignalView(
        at=at.astimezone(UTC),
        outcome="accepted" if allowed else "blocked",
        kind=kind,
        reason=", ".join(reasons) if reasons else f"{kind} accepted by the gateway",
        instrument=str(row["instrument"]) if row.get("instrument") else None,
    )


def _parse_stamp(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _day(session: Any) -> Any:
    return session.as_of.date() if session.as_of is not None else None


def fleet_rows(platform: Any, settings: Any, *, now: datetime | None = None) -> list[FleetRowView]:
    journal = platform.journal
    if journal is None:
        return []
    stamp = now or datetime.now(tz=UTC)
    ks = platform.kill_switch_replayed()
    lifecycle: dict[str, Any] = {}
    try:
        for status_obj in platform.lifecycle.statuses():
            lifecycle[status_obj.strategy_id] = status_obj
    except Exception:  # the fleet is reportable without the ladder
        lifecycle = {}
    rows: list[FleetRowView] = []
    try:
        from fiboki.ops.controls import BotControlLedger

        control_states = BotControlLedger(settings.paths.bot_controls).states()
    except Exception:  # an unreadable control ledger is reported by /api/trading/controls
        control_states = {}
    for session in journal.sessions:
        summary = platform.session_summary(session.session_id) or {}
        p = session.provenance
        ccy = session.account_ccy
        forward = "forward" in str(summary.get("source", "")) or session.session_id.startswith(
            "forward-"
        )
        symbols = sorted(
            {s for s in (str(session.instrument or "").split(",")) if s}
            | {t.instrument for t in session.trades}
            | {q.instrument for q in session.positions}
        )
        label = str(summary.get("wiring_version") or session.session_id)
        # The session's own stamp when it wrote one (paper_forward does),
        # else the summary file's mtime as the reader recorded it.
        written = _parse_stamp(summary.get("written_at")) or session.written_at
        age_s = max(0.0, (stamp - written).total_seconds())
        status_obj = lifecycle.get(session.strategy_id)
        lc_state = status_obj.lifecycle.value if status_obj is not None else None
        halts = list(getattr(status_obj, "latched_halts", ()) or ()) if status_obj else []

        # ------------------------------------------------------------ state
        tf_s = _TF_SECONDS.get(str(session.timeframe or "").upper())
        if ks.active:
            state = "halted"
            reason = f"Kill switch ARMED ({ks.mode.value if ks.mode else '?'}): new risk is refused."
        elif halts:
            state = "halted"
            reason = "Latched stopping rule(s): " + ", ".join(h.value for h in halts) + "."
        elif lc_state == "quarantined":
            state = "halted"
            reason = "Strategy is quarantined; it cannot trade until reviewed."
        elif not forward:
            exit_code = summary.get("worker_exit_code")
            if isinstance(exit_code, int) and exit_code != 0:
                state, reason = "failed", f"Replay worker exited with code {exit_code}."
            else:
                state = "finished"
                reason = "A replay: it ran over a recorded window and has ended."
        else:
            market_open = _trades_24_7(symbols) or fx_week_open(stamp)
            limit = (STALE_BARS * tf_s + STALE_GRACE_S) if tf_s else None
            if limit is None:
                state, reason = "stale", "Timeframe unknown, so no cadence to judge the session by."
            elif age_s <= limit:
                state = "running"
                reason = f"Summary written {age_s:.0f}s ago; expected at least every {tf_s}s bar."
            elif not market_open:
                state = "idle"
                reason = "The FX week is closed (Friday 17:00 to Sunday 17:00 New York); no bars close."
            else:
                state = "stale"
                reason = (
                    f"Summary last written {age_s:.0f}s ago, past {limit:.0f}s "
                    f"({STALE_BARS} bars plus {STALE_GRACE_S:.0f}s) while its market is open."
                )

        # ----------------------------------------------------------- health
        warnings = list(session.warnings)
        if state in ("stale", "failed"):
            health, health_reason = "critical", reason
        elif state == "halted":
            health, health_reason = "warn", reason
        elif warnings:
            health, health_reason = "warn", warnings[0]
        elif lc_state == "degraded":
            health, health_reason = "warn", "Strategy is degraded on the lifecycle ladder."
        else:
            health, health_reason = "ok", "No warnings on this session."

        # ------------------------------------------------------------- money
        as_of = session.as_of
        charged = platform.session_charged_costs(session.session_id)
        day = _day(session)
        day_trades = [t for t in session.trades if day is not None and t.exit_time.date() == day]
        realised = figure(
            round(sum(t.net_pnl for t in session.trades), 2),
            p,
            settings=settings,
            unit=ccy,
            as_of=as_of,
            sample_size=len(session.trades),
            affects="net_pnl",
            charged=charged,
        )
        day_pnl = (
            figure(
                round(sum(t.net_pnl for t in day_trades), 2),
                p,
                settings=settings,
                unit=ccy,
                as_of=as_of,
                sample_size=len(day_trades),
                affects="net_pnl",
                charged=charged,
            )
            if day is not None
            else Figure.missing(p, unit=ccy, reason="The session recorded no last bar, so it has no 'today'.")
        )
        if session.equity is not None and session.balance is not None:
            unreal = Figure(
                value=round(session.equity - session.balance, 2),
                provenance=p,
                unit=ccy,
                as_of=as_of,
                estimated=True,
            )
        else:
            unreal = Figure.missing(
                p, unit=ccy, reason="The session did not persist both equity and balance."
            )
        equity = (
            Figure(value=session.equity, provenance=p, unit=ccy, as_of=as_of, estimated=True)
            if session.equity is not None
            else Figure.missing(p, unit=ccy, reason="The session did not persist equity.")
        )
        positions = []
        for q in session.positions:
            positions.append(
                OpenPositionView(
                    position_id=q.position_id,
                    instrument=q.instrument,
                    direction=getattr(q.direction, "value", str(q.direction)),
                    size=Figure(value=q.size, provenance=p, unit=q.size_unit, as_of=as_of),
                    entry_price=Figure(value=q.entry_price, provenance=p, as_of=as_of),
                    mark_price=Figure(value=q.mark_price, provenance=p, as_of=as_of, estimated=True)
                    if q.mark_price is not None
                    else Figure.missing(p, reason="No mark price was persisted. Unknown, not the entry."),
                    unrealised_pnl=Figure(
                        value=q.unrealised_pnl, provenance=p, unit=ccy, as_of=as_of, estimated=True
                    )
                    if q.unrealised_pnl is not None
                    else Figure.missing(
                        p,
                        unit=ccy,
                        reason="Per-position mark-to-market is recorded only when the session "
                        "holds exactly one position.",
                    ),
                    chart=links.chart(q.instrument, timeframe=session.timeframe or None),
                )
            )
        control = control_states.get(session.session_id)
        control_state = None if not forward else (control.state.value if control else "active")
        control_note = (
            f"{control.last.action.value} by {control.last.actor} at "
            f"{control.last.at:%Y-%m-%d %H:%MZ}: {control.last.reason}"
            if forward and control is not None and control.last is not None
            else None
        )
        signal = _last_signal(platform, session.session_id)
        content_hash = getattr(status_obj, "strategy_content_hash", None) if status_obj else None
        primary = symbols[0] if symbols else None
        rows.append(
            FleetRowView(
                session_id=session.session_id,
                label=label,
                strategy_id=session.strategy_id,
                instruments=symbols,
                timeframe=session.timeframe or None,
                asset_class=_asset_class(symbols),
                kind="forward" if forward else "replay",
                provenance=p.value,
                state=state,
                state_reason=reason,
                health=health,
                health_reason=health_reason,
                last_signal=signal,
                last_signal_absent=None
                if signal is not None
                else "No gateway decision was recorded (no telemetry.jsonl, or it is empty).",
                open_positions=Figure(value=float(len(session.positions)), provenance=p, unit="count", as_of=as_of),
                positions=positions,
                unrealised_pnl=unreal,
                day_pnl=day_pnl,
                realised_pnl=realised,
                closed_trades=Figure(
                    value=float(len(session.trades)), provenance=p, unit="count", as_of=as_of
                ),
                equity=equity,
                summary_age=Figure(value=round(age_s, 1), provenance=p, unit="s", as_of=stamp),
                as_of=as_of,
                written_at=written,
                lifecycle=lc_state,
                content_hash=content_hash,
                control_state=control_state,
                control_note=control_note,
                links=LinkSet(
                    strategy=links.strategy(session.strategy_id),
                    chart=links.chart(primary, timeframe=session.timeframe or None) if primary else None,
                    journal=f"{links.JOURNAL}?strategy={session.strategy_id}",
                    lifecycle=links.lifecycle(content_hash) if content_hash else None,
                    session=links.session(session.session_id),
                ),
            )
        )
    order = {"stale": 0, "failed": 1, "halted": 2, "running": 3, "idle": 4, "finished": 5}
    rows.sort(key=lambda r: (order[r.state], r.label))
    return rows


def _note(kind: str, detail: str, as_of: datetime | None) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=as_of or datetime.now(tz=UTC))


@router.get("/fleet", response_model=Page[FleetRowView])
def fleet(platform: PlatformDep, settings: SettingsDep) -> Page[FleetRowView]:
    """Every paper bot, worst state first. The order IS the ranking."""
    journal = platform.journal
    if journal is None:
        return Page(
            items=[],
            total=0,
            source=_note(
                "absent",
                f"No paper journal at {platform.paper_root}: no bot has run, so there is no "
                "fleet. This is not an idle fleet.",
                None,
            ),
        )
    rows = fleet_rows(platform, settings)
    caveats: list[Caveat] = [
        Caveat(
            code="fleet_today_is_last_bar",
            severity="info",
            message="Day P&L is the session's net P&L on the UTC date of its last bar, not "
            "the wall-clock date: a replay that ended in the past reports that day.",
            affects="day_pnl",
            direction="unknown",
        )
    ]
    for name, error in journal.errors:
        caveats.append(
            Caveat(
                code="paper_session_unreadable",
                severity="warning",
                message=f"Session {name} could not be read and is not listed: {error}",
                affects="items",
                direction="optimistic",
            )
        )
    return Page(
        items=rows,
        total=len(rows),
        limit=max(len(rows), 1),
        source=_note(
            "live",
            f"{len(journal.sessions)} paper session(s) under {journal.root}, aged by the API "
            "clock. " + platform.trade_record_detail(),
            journal.as_of,
        ),
        caveats=tuple(caveats),
    )


# -------------------------------------------------------------- attribution


def attribution_view(platform: Any, settings: Any) -> tuple[AttributionView, list[Caveat]]:
    p = settings.provenance_for_execution()
    executed, _ = platform.trades(provenance={p}, limit=100_000)
    positions = [q for q in platform.positions() if q.provenance is p]
    currencies = {getattr(t, "account_ccy", None) for t in executed} | {
        getattr(q, "account_ccy", None) for q in positions
    }
    currencies.discard(None)
    ccy = next(iter(currencies)) if len(currencies) == 1 else None
    caveats: list[Caveat] = []
    account = platform.account()
    as_of = account.as_of if account is not None else None
    day = as_of.date() if as_of is not None else None
    if len(currencies) > 1:
        caveats.append(
            Caveat(
                code="attribution_mixed_currency",
                severity="warning",
                message="Trades are in " + ", ".join(sorted(currencies)) + " and no conversion "
                "is applied, so money totals are not reported.",
                affects="net_pnl",
                direction="unknown",
            )
        )

    def money(value: float | None, n: int, affects: str = "net_pnl") -> Figure:
        if ccy is None:
            return Figure.missing(p, reason="Mixed or unknown account currency: no total.")
        return figure(
            None if value is None else round(value, 2),
            p,
            settings=settings,
            unit=ccy,
            as_of=as_of,
            sample_size=n,
            affects=affects,
        )

    def group(key_of: Any, link_of: Any, chart_of: Any) -> list[AttributionRow]:
        trades: dict[str, list[Any]] = defaultdict(list)
        unreal: dict[str, list[Any]] = defaultdict(list)
        for t in executed:
            trades[key_of(t)].append(t)
        for q in positions:
            unreal[key_of(q)].append(q)
        keys = sorted(set(trades) | set(unreal))
        nets = {k: sum(t.net_pnl for t in trades[k]) for k in keys}
        total_abs = sum(abs(v) for v in nets.values())
        out: list[AttributionRow] = []
        for k in keys:
            ts = trades[k]
            wins = [t for t in ts if t.net_pnl > 0]
            day_ts = [t for t in ts if day is not None and t.exit_time.date() == day]
            unknown_unreal = any(q.unrealised_pnl is None for q in unreal[k])
            out.append(
                AttributionRow(
                    key=k,
                    label=k,
                    trades=Figure(value=float(len(ts)), provenance=p, unit="count", as_of=as_of),
                    wins=Figure(value=float(len(wins)), provenance=p, unit="count", as_of=as_of),
                    win_rate=Figure(
                        value=round(len(wins) / len(ts) * 100.0, 1) if ts else None,
                        provenance=p,
                        unit="pct",
                        as_of=as_of,
                        sample_size=len(ts),
                    ),
                    gross_pnl=money(sum(t.gross_pnl for t in ts), len(ts), "gross_pnl"),
                    costs=money(sum(t.costs for t in ts), len(ts), "costs"),
                    net_pnl=money(nets[k], len(ts)),
                    day_pnl=money(sum(t.net_pnl for t in day_ts), len(day_ts))
                    if day is not None
                    else Figure.missing(p, reason="No account as-of, so no 'today'."),
                    unrealised_pnl=Figure.missing(
                        p,
                        unit=ccy or "",
                        reason="At least one open position has no persisted mark-to-market.",
                    )
                    if unknown_unreal
                    else money(sum(q.unrealised_pnl for q in unreal[k]), len(unreal[k]), "unrealised_pnl"),
                    share_of_abs_pct=Figure(
                        value=round(abs(nets[k]) / total_abs * 100.0, 1) if total_abs else None,
                        provenance=p,
                        unit="pct",
                        as_of=as_of,
                    ),
                    link=link_of(k),
                    chart=chart_of(k),
                )
            )
        out.sort(key=lambda r: (-(abs(r.net_pnl.value) if r.net_pnl.value is not None else -1), r.key))
        return out

    view = AttributionView(
        provenance=p.value,
        currency=ccy,
        by_strategy=group(lambda t: t.strategy_id, links.strategy, lambda _k: None),
        by_instrument=group(
            lambda t: t.instrument, lambda k: f"{links.JOURNAL}?instrument={k}", links.chart
        ),
        total_net_pnl=money(sum(t.net_pnl for t in executed), len(executed)),
    )
    return view, caveats


@router.get("/attribution", response_model=Envelope[AttributionView])
def attribution(platform: PlatformDep, settings: SettingsDep) -> Envelope[AttributionView]:
    """Net P&L of the executing mode, attributed by strategy and by instrument."""
    view, caveats = attribution_view(platform, settings)
    p = settings.provenance_for_execution()
    kind = "seed" if platform.data_source == "seed" else "live"
    return Envelope(
        data=view,
        source=_note(
            kind,
            f"Closed {p.value} trades and open {p.value} positions only; other provenances "
            "are excluded. " + platform.trade_record_detail(),
            platform.journal_as_of(),
        ),
        caveats=tuple(caveats),
    )
