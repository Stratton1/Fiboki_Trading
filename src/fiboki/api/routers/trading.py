"""TRADING: candidates, positions, portfolio, exposure, risk state, execution.

The central fix here is ``GET /api/trading/trades``. In V1 that page was titled
"Paper / Backtest", contained no paper trades whatsoever, and was re-rendered on
the dashboard under "Recent Execution" beneath a tile called "Fleet PnL (live)".
The operator had no way to know which of the three claims was true.

Here provenance is a COLUMN on every row and a filter on the query, never a tab
and never a page title. A response that mixes backtest and paper rows is a
legitimate, readable response, and the mix is reported in ``provenance_mix``.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AuditDep, PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.logging import current_correlation_id
from fiboki.api.models import Envelope, Page, SourceNote
from fiboki.api.provenance import Caveat, Figure, Series, SeriesPoint, figure, realism_caveats
from fiboki.api.security import Principal, require_admin, require_operator
from fiboki.api.seed import equity_curve, max_drawdown_pct, sharpe
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Provenance
from fiboki.risk.killswitch import RequestKind

router = APIRouter(prefix="/api/trading", tags=["trading"])

AdminPrincipal = Annotated[Principal, Depends(require_admin)]
OperatorPrincipal = Annotated[Principal, Depends(require_operator)]

#: Below this, a ranking figure is indicative only. Project rule, applied here
#: rather than restated in the UI.
MIN_TRADES_FOR_RANKING = 80


def _note(kind: str, detail: str, as_of: datetime | None = None) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=as_of or datetime.now(tz=UTC))


def _record_caveats(platform: Any) -> tuple[Caveat, ...]:
    """What the operator must know about the trade record itself.

    With a journal: where it was read from, its as-of time, that replay
    accounts are summed, and any session that failed to parse. With the seed
    fixture: that nothing on the page was measured.
    """
    if platform.data_source == "seed":
        return (
            Caveat(
                code="seed_fixture",
                severity="warning",
                message=platform.trade_record_detail(),
                affects="all",
                direction="unknown",
            ),
        )
    return tuple(
        Caveat(
            code="paper_journal",
            severity="info" if i == 0 else "warning",
            message=text,
            affects="all",
            direction="unknown",
        )
        for i, text in enumerate(platform.journal_warnings())
    )


def _mode_account(platform: Any, provenance: Provenance) -> Any | None:
    """The journal account, only when every session is in ``provenance``.

    A PAPER journal says nothing about a SHADOW or DEMO account, so in those
    modes the account is unknown rather than borrowed.
    """
    account = platform.account()
    journal = platform.journal
    if account is None or journal is None:
        return None
    if any(s.provenance is not provenance for s in journal.sessions):
        return None
    return account


def _account_ccy(platform: Any) -> str:
    account = platform.account()
    if account is not None and account.account_ccy:
        return str(account.account_ccy)
    return "GBP"


# -------------------------------------------------------------- models


class TradeRowView(BaseModel):
    """One closed trade. ``provenance`` is a field, not a section heading."""

    model_config = ConfigDict(frozen=True)

    trade_id: str
    strategy_id: str
    instrument: str
    direction: str
    #: THE column that V1 did not have.
    provenance: Provenance
    entry_time: datetime
    exit_time: datetime
    exit_reason: str
    size: Figure
    entry_price: Figure
    exit_price: Figure
    gross_pnl: Figure
    costs: Figure
    net_pnl: Figure
    r_multiple: Figure


class PositionRowView(BaseModel):
    model_config = ConfigDict(frozen=True)

    position_id: str
    strategy_id: str
    instrument: str
    direction: str
    provenance: Provenance
    entry_time: datetime
    size: Figure
    entry_price: Figure
    mark_price: Figure
    stop_loss: Figure
    take_profit: Figure
    unrealised_pnl: Figure
    distance_to_stop_pct: Figure


class CandidateView(BaseModel):
    """A strategy proposed for promotion. ONE surface answers 'what next?'.

    V1 had four competing surfaces for this question and six routes to approve,
    each with different copy and different gating.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    name: str
    family: str
    lifecycle: str
    content_hash: str
    #: Every ranking figure carries its own provenance and sample size.
    trades: Figure
    win_rate: Figure
    expectancy_r: Figure
    net_pnl: Figure
    sharpe: Figure
    max_drawdown_pct: Figure
    eligible_for_ranking: bool
    blocking_reasons: list[str]
    next_action: str
    next_action_requires_role: str


class PortfolioView(BaseModel):
    model_config = ConfigDict(frozen=True)

    balance: Figure
    equity: Figure
    realised_pnl: Figure
    unrealised_pnl: Figure
    open_positions: Figure
    max_drawdown_pct: Figure
    provenance_mix: dict[str, int]
    equity_curve: Series


class ExposureRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    exposure_pct: Figure
    limit_pct: Figure
    utilisation_pct: Figure
    breached: bool


class RiskStateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    limits_version: str
    kill_switch_active: bool
    kill_switch_mode: str | None
    new_risk_permitted: bool
    new_risk_reason: str
    closing_permitted: bool
    daily_loss_pct: Figure
    max_daily_loss_pct: Figure
    drawdown_pct: Figure
    max_drawdown_limit_pct: Figure
    margin_utilisation_pct: Figure
    breaches: list[str]


class TelemetryRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    instrument: str
    mode: str
    provenance: Provenance
    requested_price: Figure
    filled_price: Figure
    slippage: Figure
    latency_ms: Figure
    outcome: str


# -------------------------------------------------------------- trades


def _trade_view(
    row: Any, settings: Any, charged: frozenset[str] | None = None
) -> TradeRowView:
    """One trade row. ``charged`` is the cost components the row's paper
    session PROVES it charged (``Platform.session_charged_costs``); it only
    removes a "not modelled" caveat the record contradicts. ``None`` (a seed
    fixture row, or no session) leaves the settings-only caveats."""
    p = row.provenance
    pnl_caveats = realism_caveats(settings, p, affects="net_pnl", charged=charged)
    # Journal rows carry their own account currency, size unit and as-of;
    # fixture rows default to GBP and lots. A USD P&L is never labelled GBP.
    ccy = getattr(row, "account_ccy", "GBP")
    as_of = getattr(row, "as_of", None)
    r_multiple = (
        Figure(value=row.r_multiple, provenance=p, unit="R", as_of=as_of)
        if row.r_multiple is not None
        else Figure.missing(
            p,
            unit="R",
            reason="The persisted trade does not record the risk taken, so an R "
            "multiple cannot be computed. Unknown, not zero.",
        )
    )
    return TradeRowView(
        trade_id=row.trade_id,
        strategy_id=row.strategy_id,
        instrument=row.instrument,
        direction=getattr(row.direction, "value", row.direction),
        provenance=p,
        entry_time=row.entry_time,
        exit_time=row.exit_time,
        exit_reason=getattr(row.exit_reason, "value", row.exit_reason),
        size=Figure(
            value=row.size, provenance=p, unit=getattr(row, "size_unit", "lots"), as_of=as_of
        ),
        entry_price=Figure(value=row.entry_price, provenance=p, as_of=as_of),
        exit_price=Figure(value=row.exit_price, provenance=p, as_of=as_of),
        gross_pnl=Figure(value=row.gross_pnl, provenance=p, unit=ccy, as_of=as_of),
        costs=Figure(value=row.costs, provenance=p, unit=ccy, as_of=as_of),
        net_pnl=Figure(
            value=row.net_pnl, provenance=p, unit=ccy, as_of=as_of, caveats=pnl_caveats
        ),
        r_multiple=r_multiple,
    )


@router.get("/trades", response_model=Page[TradeRowView])
def trades(
    platform: PlatformDep,
    settings: SettingsDep,
    provenance: Annotated[list[Provenance] | None, Query()] = None,
    strategy_id: str | None = None,
    instrument: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page[TradeRowView]:
    """Closed trades from every source, each labelled with its own provenance.

    ``provenance`` is a repeatable query parameter, so 'show me only paper' is a
    filter on the data rather than a different page with different copy.
    """
    rows, total = platform.trades(
        provenance=set(provenance) if provenance else None,
        strategy_id=strategy_id,
        instrument=instrument,
        limit=limit,
        offset=offset,
    )
    mix = Counter(r.provenance.value for r in rows)
    caveats: tuple[Caveat, ...] = ()
    if len(mix) > 1:
        caveats = (
            Caveat(
                code="mixed_provenance_result",
                severity="info",
                message=(
                    "This result mixes "
                    + ", ".join(f"{n} {k}" for k, n in sorted(mix.items()))
                    + " trades. Read the provenance column before comparing rows; "
                    "a backtest row and a paper row are not the same evidence."
                ),
                affects="net_pnl",
                direction="unknown",
            ),
        )
    charged_by_session: dict[str, frozenset[str]] = {}

    def _charged(row: Any) -> frozenset[str] | None:
        session_id = getattr(row, "session_id", None)
        if not session_id:
            return None
        if session_id not in charged_by_session:
            charged_by_session[session_id] = platform.session_charged_costs(session_id)
        return charged_by_session[session_id]

    return Page(
        items=[_trade_view(r, settings, _charged(r)) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
        source=_note(
            platform.data_source,
            "Trade record; provenance is per row. " + platform.trade_record_detail(),
            platform.journal_as_of(),
        ),
        caveats=caveats + _record_caveats(platform),
    )


@router.get("/positions", response_model=Page[PositionRowView])
def positions(platform: PlatformDep, settings: SettingsDep) -> Page[PositionRowView]:
    rows = platform.positions()
    items = []
    for r in rows:
        p = r.provenance
        as_of = getattr(r, "as_of", None)
        ccy = getattr(r, "account_ccy", "GBP")
        distance = (
            abs(r.mark_price - r.stop_loss) / r.mark_price * 100.0
            if r.mark_price and r.stop_loss is not None
            else None
        )
        mark = (
            Figure(value=r.mark_price, provenance=p, estimated=True, as_of=as_of)
            if r.mark_price is not None
            else Figure.missing(
                p,
                reason="The session did not persist a mark price for this position. "
                "Unknown, not the entry price.",
            )
        )
        items.append(
            PositionRowView(
                position_id=r.position_id,
                strategy_id=r.strategy_id,
                instrument=r.instrument,
                direction=getattr(r.direction, "value", r.direction),
                provenance=p,
                entry_time=r.entry_time,
                size=Figure(
                    value=r.size, provenance=p, unit=getattr(r, "size_unit", "lots"), as_of=as_of
                ),
                entry_price=Figure(value=r.entry_price, provenance=p, as_of=as_of),
                mark_price=mark,
                stop_loss=Figure(value=r.stop_loss, provenance=p, as_of=as_of),
                take_profit=Figure(value=r.take_profit, provenance=p, as_of=as_of),
                unrealised_pnl=figure(
                    r.unrealised_pnl,
                    p,
                    settings=settings,
                    unit=ccy,
                    as_of=as_of,
                    affects="unrealised_pnl",
                ),
                distance_to_stop_pct=Figure(value=distance, provenance=p, unit="pct"),
            )
        )
    return Page(
        items=items,
        total=len(items),
        source=_note(
            platform.data_source,
            "Open positions. " + platform.trade_record_detail(),
            platform.journal_as_of(),
        ),
        caveats=_record_caveats(platform),
    )


# ----------------------------------------------------------- portfolio


@router.get("/portfolio", response_model=Envelope[PortfolioView])
def portfolio(platform: PlatformDep, settings: SettingsDep) -> Envelope[PortfolioView]:
    all_trades, _ = platform.trades(limit=10_000)
    execution_provenance = settings.provenance_for_execution()
    # The account balance reflects ONLY trades that happened in this mode.
    # Folding backtest P&L into a balance tile is how V1's "Fleet PnL (live)"
    # came to be a backtest number.
    executed = [t for t in all_trades if t.provenance is execution_provenance]
    executed_positions = [
        p for p in platform.positions() if p.provenance is execution_provenance
    ]
    ep = execution_provenance
    account = _mode_account(platform, ep)
    base_detail = (
        f"Balance and equity count only {ep.value} trades. "
        f"{len(all_trades) - len(executed)} trade(s) from other provenances are "
        "excluded from the account figures and visible on the trades view. "
    )

    if account is None or account.account_ccy is None or account.initial_balance is None:
        # No journal (seed fixture), an unreadable one, or one that cannot be
        # summed: the account is UNKNOWN. A 25,000 starting balance with a
        # PAPER chip on it would be a number nothing measured.
        reason = (
            "No paper account is available: "
            + (
                "no paper journal exists, so the rows on the trades view are a "
                "demonstration fixture and there is no balance to report."
                if platform.data_source == "seed"
                else "the journal's sessions could not be read or summed; see the "
                "caveats on this response."
            )
        )
        ccy = _account_ccy(platform)
        view = PortfolioView(
            balance=Figure.missing(ep, unit=ccy, reason=reason),
            equity=Figure.missing(ep, unit=ccy, reason=reason),
            realised_pnl=Figure.missing(ep, unit=ccy, reason=reason),
            unrealised_pnl=Figure.missing(ep, unit=ccy, reason=reason),
            open_positions=Figure.missing(ep, unit="count", reason=reason),
            max_drawdown_pct=Figure.missing(ep, unit="pct", reason=reason),
            provenance_mix=dict(Counter(t.provenance.value for t in all_trades)),
            equity_curve=Series(
                name=f"Equity ({ep.value})",
                provenance=ep,
                unit=ccy,
                points=(),
                caveats=realism_caveats(settings, ep, affects="equity"),
            ),
        )
        return Envelope(
            data=view,
            source=_note(platform.data_source, base_detail + platform.trade_record_detail()),
            caveats=_record_caveats(platform),
        )

    ccy = account.account_ccy
    as_of = account.as_of
    starting = account.initial_balance
    curve = equity_curve(executed, starting=starting)
    view = PortfolioView(
        balance=figure(
            account.balance,
            ep,
            settings=settings,
            unit=ccy,
            as_of=as_of,
            affects="balance",
            sample_size=len(executed),
        ),
        equity=figure(
            account.equity,
            ep,
            settings=settings,
            unit=ccy,
            as_of=as_of,
            affects="equity",
            sample_size=len(executed),
        ),
        realised_pnl=figure(
            round(sum(t.net_pnl for t in executed), 2),
            ep,
            settings=settings,
            unit=ccy,
            as_of=as_of,
            affects="net_pnl",
            sample_size=len(executed),
        ),
        unrealised_pnl=Figure(
            value=account.unrealised_pnl,
            provenance=ep,
            unit=ccy,
            as_of=as_of,
            estimated=True,
        ),
        open_positions=Figure(
            value=float(len(executed_positions)), provenance=ep, unit="count", as_of=as_of
        ),
        max_drawdown_pct=Figure(
            value=max_drawdown_pct(curve),
            provenance=ep,
            unit="pct",
            as_of=as_of,
            sample_size=len(executed),
        ),
        provenance_mix=dict(Counter(t.provenance.value for t in all_trades)),
        equity_curve=Series(
            name=f"Realised equity ({ep.value})",
            provenance=ep,
            unit=ccy,
            points=tuple(SeriesPoint(t=t, v=v) for t, v in curve),
            caveats=realism_caveats(settings, ep, affects="equity"),
        ),
    )
    return Envelope(
        data=view,
        source=_note(
            platform.data_source,
            base_detail
            + f"Account read from {account.sessions} paper session summary(ies). "
            + platform.trade_record_detail(),
            as_of,
        ),
        caveats=_record_caveats(platform),
    )


@router.get("/exposure", response_model=Page[ExposureRow])
def exposure(platform: PlatformDep, settings: SettingsDep) -> Page[ExposureRow]:
    limits = platform.limits
    open_positions = platform.positions()
    # Exposure is labelled with the provenance of the positions it aggregates:
    # PAPER for a paper journal, BACKTEST for the seed fixture. Never PAPER on
    # fixture data.
    labels = {pos.provenance for pos in open_positions}
    p = next(iter(labels)) if len(labels) == 1 else settings.provenance_for_execution()
    account = platform.account()
    seed = platform.data_source == "seed"
    as_of = platform.journal_as_of()
    # The fixture sizes in FX lots against a nominal 25,000 book, both part of
    # the fixture (the envelope says "seed"). A journal sizes in instrument
    # units against the journal's own equity; with no equity there is no
    # percentage, which is unknown rather than zero.
    equity: float | None = 25_000.0 if seed else (account.equity if account else None)
    account_ccy = account.account_ccy if account else None
    at_entry: set[str] = set()
    unconverted: set[str] = set()

    by_instrument: dict[str, float] = defaultdict(float)
    by_currency: dict[str, float] = defaultdict(float)
    by_strategy: dict[str, float] = defaultdict(float)
    for pos in open_positions:
        price = pos.mark_price
        if price is None:
            price = pos.entry_price
            at_entry.add(pos.instrument)
        inst = None
        try:
            inst = instrument_registry.get(pos.instrument)
        except Exception:
            inst = None
        if getattr(pos, "size_unit", "lots") == "lots":
            multiplier = 100_000.0
        else:
            multiplier = float(getattr(inst, "contract_size", 1.0) or 1.0)
        notional = pos.size * price * multiplier
        if not seed and inst is not None and account_ccy and inst.quote != account_ccy:
            unconverted.add(pos.instrument)
        by_instrument[pos.instrument] += notional
        by_strategy[pos.strategy_id] += notional
        if inst is not None:
            by_currency[inst.base] += notional
            by_currency[inst.quote] += notional
        else:
            by_currency["UNKNOWN"] += notional

    rows: list[ExposureRow] = []
    estimated = bool(at_entry or unconverted)

    def add(key: str, label: str, notional: float, limit_pct: float) -> None:
        if not equity:
            rows.append(
                ExposureRow(
                    key=key,
                    label=label,
                    exposure_pct=Figure.missing(
                        p, unit="pct", reason="No account equity is known to divide by."
                    ),
                    limit_pct=Figure(value=limit_pct, provenance=p, unit="pct"),
                    utilisation_pct=Figure.missing(p, unit="pct"),
                    breached=False,
                )
            )
            return
        pct = notional / equity * 100.0
        rows.append(
            ExposureRow(
                key=key,
                label=label,
                exposure_pct=Figure(
                    value=round(pct, 2), provenance=p, unit="pct", as_of=as_of,
                    estimated=estimated,
                ),
                limit_pct=Figure(value=limit_pct, provenance=p, unit="pct"),
                utilisation_pct=Figure(
                    value=round(pct / limit_pct * 100.0, 1) if limit_pct else None,
                    provenance=p,
                    unit="pct",
                    as_of=as_of,
                    estimated=estimated,
                ),
                breached=pct > limit_pct,
            )
        )

    for name, notional in sorted(by_instrument.items()):
        add(f"instrument:{name}", name, notional, limits.max_instrument_exposure_pct)
    for name, notional in sorted(by_strategy.items()):
        add(f"strategy:{name}", name, notional, limits.max_strategy_exposure_pct)
    for name, notional in sorted(by_currency.items()):
        add(f"currency:{name}", name, notional, limits.max_currency_exposure_pct)

    caveats = list(_record_caveats(platform))
    if at_entry:
        caveats.append(
            Caveat(
                code="exposure_at_entry_price",
                severity="warning",
                message="No mark price was persisted for "
                + ", ".join(sorted(at_entry))
                + "; notional is valued at the ENTRY price, so exposure is an "
                "estimate and may be far from the current value.",
                affects="exposure_pct",
                direction="unknown",
            )
        )
    if unconverted:
        caveats.append(
            Caveat(
                code="exposure_not_converted",
                severity="warning",
                message="Notional for "
                + ", ".join(sorted(unconverted))
                + f" is in its quote currency and is not converted to {account_ccy}.",
                affects="exposure_pct",
                direction="unknown",
            )
        )
    return Page(
        items=rows,
        total=len(rows),
        source=_note(
            platform.data_source,
            f"Notional exposure against limit set {limits.version}. "
            + platform.trade_record_detail(),
            as_of,
        ),
        caveats=tuple(caveats),
    )


@router.get("/risk", response_model=Envelope[RiskStateView])
def risk_state(platform: PlatformDep, settings: SettingsDep) -> Envelope[RiskStateView]:
    limits = platform.limits
    p = settings.provenance_for_execution()
    ks = platform.kill_switch
    permitted, reason = ks.allows(RequestKind.OPEN)
    closing_ok, _ = ks.allows(RequestKind.CLOSE)

    executed, _ = platform.trades(provenance={p}, limit=10_000)
    account = _mode_account(platform, p)
    starting = account.initial_balance if account is not None else None
    as_of = account.as_of if account is not None else None
    if starting:
        curve = equity_curve(executed, starting=starting)
        dd = max_drawdown_pct(curve)
        # "Today" for a replayed journal is the last day it replayed, not the
        # wall clock: a 2025 replay has no trades on today's date, and 0.00%
        # would read as a calm day rather than as "not applicable".
        day = (as_of or datetime.now(tz=UTC)).date()
        day_pnl = sum(t.net_pnl for t in executed if t.exit_time.date() == day)
        daily_pct: float | None = round(day_pnl / starting * 100.0, 3)
        daily_fig = Figure(
            value=daily_pct, provenance=p, unit="pct", as_of=as_of,
            sample_size=len(executed),
        )
        dd_fig = Figure(
            value=dd, provenance=p, unit="pct", as_of=as_of, sample_size=len(executed)
        )
    else:
        dd, daily_pct = None, None
        reason = (
            "No paper account exists (the trade record is "
            f"{platform.data_source}), so drawdown and daily loss are unknown, "
            "not zero."
        )
        daily_fig = Figure.missing(p, unit="pct", reason=reason)
        dd_fig = Figure.missing(p, unit="pct", reason=reason)

    breaches: list[str] = []
    if dd is not None and dd > limits.max_total_drawdown_pct:
        breaches.append(
            f"Drawdown {dd:.1f}% exceeds the {limits.max_total_drawdown_pct:.1f}% limit."
        )
    if daily_pct is not None and daily_pct < -limits.max_daily_loss_pct:
        breaches.append(
            f"Daily loss {daily_pct:.2f}% exceeds the "
            f"{limits.max_daily_loss_pct:.1f}% limit."
        )

    view = RiskStateView(
        limits_version=limits.version,
        kill_switch_active=ks.state.active,
        kill_switch_mode=ks.state.mode.value if ks.state.mode else None,
        new_risk_permitted=permitted,
        new_risk_reason=reason,
        closing_permitted=closing_ok,
        daily_loss_pct=daily_fig,
        max_daily_loss_pct=Figure(value=limits.max_daily_loss_pct, provenance=p, unit="pct"),
        drawdown_pct=dd_fig,
        max_drawdown_limit_pct=Figure(
            value=limits.max_total_drawdown_pct, provenance=p, unit="pct"
        ),
        margin_utilisation_pct=Figure.missing(
            p,
            unit="pct",
            reason="Margin utilisation requires a broker account snapshot. No "
            "broker session exists in this mode, so this is unknown — not zero.",
        ),
        breaches=breaches,
    )
    return Envelope(
        data=view,
        source=_note(
            platform.data_source,
            f"Limit set {limits.version}; drawdown and daily loss from the trade "
            "record. " + platform.trade_record_detail(),
            as_of,
        ),
        caveats=_record_caveats(platform),
    )


# ---------------------------------------------------------- candidates


@router.get("/candidates", response_model=Page[CandidateView])
def candidates(platform: PlatformDep, settings: SettingsDep) -> Page[CandidateView]:
    """The ONE surface that answers 'what should I promote?'."""
    items: list[CandidateView] = []
    for doc in platform.strategy_documents():
        strategy_trades, _ = platform.trades(strategy_id=doc.strategy_id, limit=10_000)
        # Ranking uses out-of-sample evidence only. Ranking on in-sample
        # backtest figures is how a curve-fit gets promoted.
        ranking = [
            t
            for t in strategy_trades
            if t.provenance
            in {Provenance.OUT_OF_SAMPLE, Provenance.HOLDOUT, Provenance.WALKFORWARD}
        ]
        p = Provenance.OUT_OF_SAMPLE
        n = len(ranking)
        wins = [t for t in ranking if t.net_pnl > 0]
        net = round(sum(t.net_pnl for t in ranking), 2) if ranking else None
        expectancy = (
            round(sum(t.r_multiple for t in ranking) / n, 3) if n else None
        )
        curve = equity_curve(ranking, starting=25_000.0)
        returns = [t.r_multiple for t in ranking]

        eligible = n >= MIN_TRADES_FOR_RANKING
        blocking: list[str] = []
        if not eligible:
            blocking.append(
                f"{n} out-of-sample trades; primary ranking requires "
                f"{MIN_TRADES_FOR_RANKING}."
            )
        if platform.kill_switch.state.active:
            blocking.append(
                "The kill switch is armed. Promotion is blocked while trading is halted."
            )

        items.append(
            CandidateView(
                strategy_id=doc.strategy_id,
                name=doc.name,
                family=str(getattr(doc.family, "value", doc.family)),
                lifecycle="candidate",
                content_hash=doc.content_hash()[:12]
                if callable(getattr(doc, "content_hash", None))
                else str(getattr(doc, "content_hash", ""))[:12],
                trades=Figure(value=float(n), provenance=p, unit="count"),
                win_rate=Figure(
                    value=round(len(wins) / n * 100.0, 1) if n else None,
                    provenance=p,
                    unit="pct",
                    sample_size=n,
                ),
                expectancy_r=figure(
                    expectancy,
                    p,
                    settings=settings,
                    unit="R",
                    affects="expectancy",
                    sample_size=n,
                ),
                net_pnl=figure(
                    net, p, settings=settings, unit="GBP", affects="net_pnl", sample_size=n
                ),
                sharpe=figure(
                    sharpe(returns),
                    p,
                    settings=settings,
                    unit="ratio",
                    affects="sharpe",
                    sample_size=n,
                ),
                max_drawdown_pct=Figure(
                    value=max_drawdown_pct(curve), provenance=p, unit="pct", sample_size=n
                ),
                eligible_for_ranking=eligible,
                blocking_reasons=blocking,
                next_action="promote_to_paper" if eligible and not blocking else "hold",
                next_action_requires_role="admin",
            )
        )
    items.sort(
        key=lambda c: (c.eligible_for_ranking, c.expectancy_r.value or -99), reverse=True
    )
    return Page(
        items=items,
        total=len(items),
        source=_note(
            platform.data_source,
            "Ranked on out-of-sample, holdout and walk-forward evidence only. "
            "In-sample backtest trades are excluded from every figure here.",
        ),
    )


#: The only lifecycles this route can promote to. ``demo`` and ``live`` are
#: absent by construction, and the preflight enumerates exactly these keys.
PROMOTION_TARGETS: tuple[str, ...] = ("paper", "shadow")


class PromoteRequest(BaseModel):
    target_lifecycle: str = Field(pattern="^(paper|shadow)$")
    """Neither ``demo`` nor ``live`` is accepted here, by construction."""
    reason: str = Field(min_length=8, max_length=500)
    acknowledge_caveats: bool = False
    acknowledged_caveats: list[str] | None = Field(default=None, max_length=64)
    """The caveat codes the operator ticked, one by one, in the promote dialog.

    Optional for older clients. When it is sent it must cover every caveat code
    the preflight returns for this candidate, and it is written to the audit
    trail, so the record says which qualifiers were acknowledged rather than
    only that a box was ticked.
    """


class PromotePreflightView(BaseModel):
    """What promoting this candidate would do, computed server-side.

    ``consequences`` has the same shape as ``KillSwitchView.consequences``: one
    list per choice, keyed by the value the POST accepts. ``caveats`` is the
    de-duplicated set of realism caveats attached to the candidate's ranking
    figures; the dialog renders each as a checkbox the operator must tick.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    execution_mode: str
    eligible: bool
    blocking_reasons: list[str]
    caveats: list[Caveat]
    consequences: dict[str, list[str]]


class KillSwitchDisarmPreflightView(BaseModel):
    """What disarming the kill switch would do, computed server-side."""

    model_config = ConfigDict(frozen=True)

    active: bool
    mode: str | None
    execution_mode: str
    consequences: dict[str, list[str]]


def _candidate_caveats(candidate: CandidateView) -> list[Caveat]:
    """Every distinct caveat on the candidate's ranking figures, first seen wins."""
    seen: dict[str, Caveat] = {}
    for fig in (
        candidate.trades,
        candidate.win_rate,
        candidate.expectancy_r,
        candidate.net_pnl,
        candidate.sharpe,
        candidate.max_drawdown_pct,
    ):
        for caveat in fig.caveats:
            seen.setdefault(caveat.code, caveat)
    return list(seen.values())


def _find_candidate(platform: Any, settings: Any, strategy_id: str) -> CandidateView | None:
    page = candidates(platform, settings)
    return next((c for c in page.items if c.strategy_id == strategy_id), None)


def _promotion_consequences(
    candidate: CandidateView, caveats: list[Caveat], settings: Any
) -> dict[str, list[str]]:
    mode = settings.execution_mode.value
    evidence = candidate.trades.provenance.value
    codes = ", ".join(c.code for c in caveats) or "none"
    common_tail = [
        f"You are acknowledging {len(caveats)} realism caveat(s) on its figures: "
        f"{codes}. They are recorded with your name in the operator audit trail.",
        "This route records the promotion decision. It does not itself start a "
        "run or change the lifecycle store; lifecycle state is served separately "
        "by /api/trading/lifecycle.",
        f"The deployment stays in {mode.upper()} mode. Demo and live are not "
        "promotion targets on this route and cannot be reached from it.",
    ]
    n = candidate.trades.value
    sample = "an unknown number of" if n is None else f"{int(n)}"
    return {
        "paper": [
            f"Records {candidate.strategy_id} as promoted to PAPER on {sample} "
            f"{evidence} trade(s) of ranking evidence.",
            f"Its ranking figures remain {evidence.upper()} evidence; paper "
            "figures would be labelled PAPER and reported separately, never merged.",
            *common_tail,
        ],
        "shadow": [
            f"Records {candidate.strategy_id} as promoted to SHADOW.",
            "Shadow mirrors signals against a venue's pricing with no order "
            "submitted and no position opened.",
            *common_tail,
        ],
    }


@router.get(
    "/candidates/{strategy_id}/promote/preflight",
    response_model=Envelope[PromotePreflightView],
)
def promote_preflight(
    strategy_id: str, platform: PlatformDep, settings: SettingsDep
) -> Envelope[PromotePreflightView]:
    """The caveats to acknowledge and the consequences of each promotion target.

    The promote dialog renders this verbatim. Nothing in it is page copy, so it
    changes when the configuration, the evidence or the kill switch changes.
    """
    if platform.strategy(strategy_id) is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")
    match = _find_candidate(platform, settings, strategy_id)
    if match is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "not_a_candidate",
            f"{strategy_id} is not currently a promotion candidate.",
        )
    caveats = _candidate_caveats(match)
    view = PromotePreflightView(
        strategy_id=strategy_id,
        execution_mode=settings.execution_mode.value,
        eligible=match.eligible_for_ranking and not match.blocking_reasons,
        blocking_reasons=list(match.blocking_reasons),
        caveats=caveats,
        consequences=_promotion_consequences(match, caveats, settings),
    )
    return Envelope(
        data=view,
        source=_note(
            platform.data_source,
            "Computed from the candidate's ranking figures and the configuration "
            "in force at the time of this request.",
        ),
    )


@router.get(
    "/preflight/kill-switch-disarm",
    response_model=Envelope[KillSwitchDisarmPreflightView],
)
def kill_switch_disarm_preflight(
    platform: PlatformDep, settings: SettingsDep
) -> Envelope[KillSwitchDisarmPreflightView]:
    """Consequences of ``POST /api/system/kill-switch/disarm``, for its dialog.

    Same shape as ``KillSwitchView.consequences`` (keyed by the choice, here the
    single ``disarm``). It lives in this router only because the system router
    is owned elsewhere at the time of writing; its natural home is beside
    ``GET /api/system/kill-switch``.
    """
    state = platform.kill_switch.state
    mode = settings.execution_mode.value
    open_positions = len(platform.positions())
    lines: list[str]
    if not state.active:
        lines = [
            "The kill switch is not armed. A disarm request would be refused "
            "(kill_switch_not_active) and nothing would change.",
        ]
    else:
        halt = state.mode.value.upper() if state.mode else "UNKNOWN"
        since = (
            state.since.strftime("%Y-%m-%d %H:%M UTC")
            if state.since is not None
            else "an unrecorded time"
        )
        lines = [
            f"Lifts the {halt} halt armed by {state.operator or 'an unrecorded operator'} "
            f"at {since}: \"{state.reason or 'no reason recorded'}\".",
            "Opening and increasing orders stop being refused by the kill switch "
            f"from the next request. They remain subject to limit set {platform.limits.version}.",
        ]
        if state.requires_flatten:
            lines.append(
                "Positions already closed by FLATTEN stay closed; disarming re-opens "
                "nothing."
            )
        lines.extend(
            [
                f"{open_positions} position(s) are currently open in {mode} mode.",
                f"The deployment stays in {mode.upper()} mode; disarming does not "
                "change the execution mode.",
                "This is recorded against your name, with your reason, in the "
                "operator audit trail and the kill-switch journal.",
            ]
        )
    view = KillSwitchDisarmPreflightView(
        active=state.active,
        mode=state.mode.value if state.mode else None,
        execution_mode=mode,
        consequences={"disarm": lines},
    )
    return Envelope(
        data=view,
        source=_note("live", f"Replayed from {settings.killswitch_path.name}."),
    )


@router.post("/candidates/{strategy_id}/promote", response_model=Envelope[dict])
def promote_candidate(
    strategy_id: str,
    body: PromoteRequest,
    platform: PlatformDep,
    settings: SettingsDep,
    audit: AuditDep,
    principal: AdminPrincipal,
    request: Request,
) -> Envelope[dict]:
    """The ONE promotion route. V1 had six, with different copy and gating."""
    doc = platform.strategy(strategy_id)
    if doc is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "unknown_strategy", "No such strategy.")

    def deny(code: str, detail: str, **extra: Any) -> ApiError:
        audit.record(
            "candidate.promote",
            actor=principal.user_id,
            actor_role=principal.role.value,
            outcome="refused",
            reason=body.reason,
            target=strategy_id,
            execution_mode=settings.execution_mode.value,
            correlation_id=current_correlation_id(),
            source_ip=request.client.host if request.client else "",
            detail={"code": code, **extra},
        )
        return ApiError(status.HTTP_409_CONFLICT, code, detail, context=extra or None)

    if not body.acknowledge_caveats:
        raise deny(
            "caveats_not_acknowledged",
            "Promotion requires acknowledging the realism caveats returned with "
            "the candidate's figures.",
        )
    if platform.kill_switch.state.active:
        raise deny(
            "kill_switch_armed",
            "The kill switch is armed. Nothing is promoted while trading is halted.",
        )
    match = _find_candidate(platform, settings, strategy_id)
    if match is None or not match.eligible_for_ranking:
        raise deny(
            "insufficient_evidence",
            f"{strategy_id} does not meet the {MIN_TRADES_FOR_RANKING}-trade "
            "out-of-sample minimum for promotion.",
        )

    required = [c.code for c in _candidate_caveats(match)]
    acknowledged = (
        sorted(set(body.acknowledged_caveats))
        if body.acknowledged_caveats is not None
        else None
    )
    if acknowledged is not None:
        missing = [code for code in required if code not in acknowledged]
        if missing:
            raise deny(
                "caveats_not_acknowledged",
                "Every realism caveat on the candidate's figures must be "
                "acknowledged individually. Missing: " + ", ".join(missing) + ".",
                missing_caveats=missing,
                acknowledged_caveats=acknowledged,
            )

    audit.record(
        "candidate.promote",
        actor=principal.user_id,
        actor_role=principal.role.value,
        outcome="allowed",
        reason=body.reason,
        target=strategy_id,
        execution_mode=settings.execution_mode.value,
        correlation_id=current_correlation_id(),
        source_ip=request.client.host if request.client else "",
        detail={
            "target_lifecycle": body.target_lifecycle,
            "required_caveats": required,
            # None means the client sent only the boolean (an older client),
            # which is recorded as such rather than as an empty acknowledgement.
            "acknowledged_caveats": acknowledged,
        },
    )
    return Envelope(
        data={
            "strategy_id": strategy_id,
            "target_lifecycle": body.target_lifecycle,
            "recorded": True,
        },
        source=_note("live", "Recorded to the operator audit trail."),
    )


# ---------------------------------------------------------- telemetry


@router.get("/execution-telemetry", response_model=Page[TelemetryRow])
def execution_telemetry(
    platform: PlatformDep,
    settings: SettingsDep,
    limit: int = Query(50, ge=1, le=200),
) -> Page[TelemetryRow]:
    p = settings.provenance_for_execution()
    rows, _ = platform.trades(provenance={p}, limit=limit)
    items = [
        TelemetryRow(
            order_id=r.trade_id.replace("trd", "ord"),
            instrument=r.instrument,
            mode=settings.execution_mode.value,
            provenance=p,
            requested_price=Figure(value=r.entry_price, provenance=p),
            filled_price=Figure(value=r.entry_price, provenance=p),
            slippage=Figure(
                value=0.0,
                provenance=p,
                unit="pips",
                caveats=(
                    Caveat(
                        code="slippage_not_modelled",
                        severity="warning",
                        message=(
                            f"Slippage model is {settings.slippage_model!r}, so this "
                            "is an assumption, not a measurement."
                        ),
                        affects="slippage",
                        direction="optimistic",
                    ),
                ),
                estimated=True,
            ),
            latency_ms=Figure.missing(
                p,
                unit="ms",
                reason="No venue round trip occurs in this mode, so there is no "
                "latency to report.",
            ),
            outcome="filled",
        )
        for r in rows
    ]
    return Page(
        items=items,
        total=len(items),
        limit=limit,
        source=_note(platform.data_source, "Execution telemetry for the current mode."),
    )
