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


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


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


def _trade_view(row: Any, settings: Any) -> TradeRowView:
    p = row.provenance
    pnl_caveats = realism_caveats(settings, p, affects="net_pnl")
    return TradeRowView(
        trade_id=row.trade_id,
        strategy_id=row.strategy_id,
        instrument=row.instrument,
        direction=row.direction.value,
        provenance=p,
        entry_time=row.entry_time,
        exit_time=row.exit_time,
        exit_reason=row.exit_reason.value,
        size=Figure(value=row.size, provenance=p, unit="lots"),
        entry_price=Figure(value=row.entry_price, provenance=p),
        exit_price=Figure(value=row.exit_price, provenance=p),
        gross_pnl=Figure(value=row.gross_pnl, provenance=p, unit="GBP"),
        costs=Figure(value=row.costs, provenance=p, unit="GBP"),
        net_pnl=Figure(value=row.net_pnl, provenance=p, unit="GBP", caveats=pnl_caveats),
        r_multiple=Figure(value=row.r_multiple, provenance=p, unit="R"),
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
    return Page(
        items=[_trade_view(r, settings) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
        source=_note(platform.data_source, "Trade record; provenance is per row."),
        caveats=caveats,
    )


@router.get("/positions", response_model=Page[PositionRowView])
def positions(platform: PlatformDep, settings: SettingsDep) -> Page[PositionRowView]:
    rows = platform.positions()
    items = []
    for r in rows:
        p = r.provenance
        distance = (
            abs(r.mark_price - r.stop_loss) / r.mark_price * 100.0 if r.mark_price else None
        )
        items.append(
            PositionRowView(
                position_id=r.position_id,
                strategy_id=r.strategy_id,
                instrument=r.instrument,
                direction=r.direction.value,
                provenance=p,
                entry_time=r.entry_time,
                size=Figure(value=r.size, provenance=p, unit="lots"),
                entry_price=Figure(value=r.entry_price, provenance=p),
                mark_price=Figure(value=r.mark_price, provenance=p, estimated=True),
                stop_loss=Figure(value=r.stop_loss, provenance=p),
                take_profit=Figure(value=r.take_profit, provenance=p),
                unrealised_pnl=figure(
                    r.unrealised_pnl,
                    p,
                    settings=settings,
                    unit="GBP",
                    affects="unrealised_pnl",
                ),
                distance_to_stop_pct=Figure(value=distance, provenance=p, unit="pct"),
            )
        )
    return Page(
        items=items,
        total=len(items),
        source=_note(platform.data_source, "Open positions in the current execution mode."),
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
    realised = round(sum(t.net_pnl for t in executed), 2)
    unrealised = round(sum(p.unrealised_pnl for p in platform.positions()), 2)
    starting = 25_000.0
    curve = equity_curve(executed, starting=starting)
    view = PortfolioView(
        balance=figure(
            starting + realised,
            execution_provenance,
            settings=settings,
            unit="GBP",
            affects="balance",
            sample_size=len(executed),
        ),
        equity=figure(
            starting + realised + unrealised,
            execution_provenance,
            settings=settings,
            unit="GBP",
            affects="equity",
            sample_size=len(executed),
        ),
        realised_pnl=figure(
            realised,
            execution_provenance,
            settings=settings,
            unit="GBP",
            affects="net_pnl",
            sample_size=len(executed),
        ),
        unrealised_pnl=Figure(
            value=unrealised,
            provenance=execution_provenance,
            unit="GBP",
            estimated=True,
        ),
        open_positions=Figure(
            value=float(len(platform.positions())),
            provenance=execution_provenance,
            unit="count",
        ),
        max_drawdown_pct=Figure(
            value=max_drawdown_pct(curve),
            provenance=execution_provenance,
            unit="pct",
            sample_size=len(executed),
        ),
        provenance_mix=dict(Counter(t.provenance.value for t in all_trades)),
        equity_curve=Series(
            name=f"Equity ({execution_provenance.value})",
            provenance=execution_provenance,
            unit="GBP",
            points=tuple(SeriesPoint(t=t, v=v) for t, v in curve),
            caveats=realism_caveats(settings, execution_provenance, affects="equity"),
        ),
    )
    return Envelope(
        data=view,
        source=_note(
            platform.data_source,
            f"Balance and equity count only {execution_provenance.value} trades. "
            f"{len(all_trades) - len(executed)} trade(s) from other provenances are "
            "excluded from the account figures and visible on the trades view.",
        ),
    )


@router.get("/exposure", response_model=Page[ExposureRow])
def exposure(platform: PlatformDep, settings: SettingsDep) -> Page[ExposureRow]:
    limits = platform.limits
    open_positions = platform.positions()
    p = settings.provenance_for_execution()
    equity = 25_000.0

    by_instrument: dict[str, float] = defaultdict(float)
    by_currency: dict[str, float] = defaultdict(float)
    by_strategy: dict[str, float] = defaultdict(float)
    for pos in open_positions:
        notional = pos.size * pos.mark_price * 100_000.0
        by_instrument[pos.instrument] += notional
        by_strategy[pos.strategy_id] += notional
        try:
            inst = instrument_registry.get(pos.instrument)
            by_currency[inst.base] += notional
            by_currency[inst.quote] += notional
        except Exception:
            by_currency["UNKNOWN"] += notional

    rows: list[ExposureRow] = []

    def add(key: str, label: str, notional: float, limit_pct: float) -> None:
        pct = notional / equity * 100.0 if equity else 0.0
        rows.append(
            ExposureRow(
                key=key,
                label=label,
                exposure_pct=Figure(value=round(pct, 2), provenance=p, unit="pct"),
                limit_pct=Figure(value=limit_pct, provenance=p, unit="pct"),
                utilisation_pct=Figure(
                    value=round(pct / limit_pct * 100.0, 1) if limit_pct else None,
                    provenance=p,
                    unit="pct",
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

    return Page(
        items=rows,
        total=len(rows),
        source=_note(
            platform.data_source,
            f"Notional exposure against limit set {limits.version}.",
        ),
    )


@router.get("/risk", response_model=Envelope[RiskStateView])
def risk_state(platform: PlatformDep, settings: SettingsDep) -> Envelope[RiskStateView]:
    limits = platform.limits
    p = settings.provenance_for_execution()
    ks = platform.kill_switch
    permitted, reason = ks.allows(RequestKind.OPEN)
    closing_ok, _ = ks.allows(RequestKind.CLOSE)

    executed, _ = platform.trades(provenance={p}, limit=10_000)
    curve = equity_curve(executed, starting=25_000.0)
    dd = max_drawdown_pct(curve)
    today = datetime.now(tz=UTC).date()
    today_pnl = sum(t.net_pnl for t in executed if t.exit_time.date() == today)
    daily_pct = round(today_pnl / 25_000.0 * 100.0, 3)

    breaches: list[str] = []
    if dd is not None and dd > limits.max_total_drawdown_pct:
        breaches.append(
            f"Drawdown {dd:.1f}% exceeds the {limits.max_total_drawdown_pct:.1f}% limit."
        )
    if daily_pct < -limits.max_daily_loss_pct:
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
        daily_loss_pct=Figure(value=daily_pct, provenance=p, unit="pct"),
        max_daily_loss_pct=Figure(value=limits.max_daily_loss_pct, provenance=p, unit="pct"),
        drawdown_pct=Figure(value=dd, provenance=p, unit="pct", sample_size=len(executed)),
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
    return Envelope(data=view, source=_note("live", f"Limit set {limits.version}."))


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


class PromoteRequest(BaseModel):
    target_lifecycle: str = Field(pattern="^(paper|shadow)$")
    """Neither ``demo`` nor ``live`` is accepted here, by construction."""
    reason: str = Field(min_length=8, max_length=500)
    acknowledge_caveats: bool = False


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

    def deny(code: str, detail: str) -> ApiError:
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
            detail={"code": code},
        )
        return ApiError(status.HTTP_409_CONFLICT, code, detail)

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
    page = candidates(platform, settings)
    match = next((c for c in page.items if c.strategy_id == strategy_id), None)
    if match is None or not match.eligible_for_ranking:
        raise deny(
            "insufficient_evidence",
            f"{strategy_id} does not meet the {MIN_TRADES_FOR_RANKING}-trade "
            "out-of-sample minimum for promotion.",
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
        detail={"target_lifecycle": body.target_lifecycle},
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
