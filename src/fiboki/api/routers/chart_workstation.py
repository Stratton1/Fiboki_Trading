"""CHART WORKSTATION (Wave 2B): replay, compare, data window, events, templates, price alerts.

Everything the chart workstation draws beyond the candles and the overlays
(``routers/markets.py``) and the backtest trades and drawings
(``routers/chart.py``). Nothing here computes a signal, a stop, a size or a
P&L: those are the stored ones, or the indicator package's, served as they are.

``GET /api/markets/replay/{symbol}``
    Bar-by-bar replay. Given a ``cursor`` (a UTC instant) it serves only the
    candles CLOSED by then, the indicators and regimes computed over those
    candles alone, and the paper and backtest trades and strategy markers as
    they stood at the cursor: a trade still open at the cursor has no exit, no
    P&L and no final stop. Nothing timed after the cursor is ever served
    (``tests/api/test_chart_replay.py`` proves it by perturbing the future).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import PlatformDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.models import Envelope, SourceNote
from fiboki.api.provenance import Caveat, Figure
from fiboki.api.routers import chart as chart_router
from fiboki.api.routers import markets as markets_router
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Provenance, Timeframe

router = APIRouter(tags=["chart-workstation"])

#: Closed bars served per replay step.
REPLAY_DEFAULT_BARS = 300
REPLAY_MAX_BARS = 2_000


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _symbol(symbol: str) -> str:
    if not instrument_registry.exists(symbol):
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_instrument",
            f"{symbol!r} is not in the instrument registry.",
        )
    return instrument_registry.get(symbol).symbol


def _tf(value: str) -> Timeframe:
    try:
        return Timeframe(value)
    except ValueError as exc:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "unknown_timeframe",
            f"{value!r} is not a recognised timeframe.",
        ) from exc


def _mounted(settings: Any, symbol: str, timeframe: str) -> None:
    if settings.data_root is None or not settings.data_root.exists():
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "market_data_not_mounted",
            "No market-data root is mounted on this deployment, so no bars can "
            "be served. This is a missing data source, not a quiet market.",
            context={"symbol": symbol, "timeframe": timeframe},
        )


def _read_frame(settings: Any, symbol: str, tf: Timeframe) -> tuple[Any, str]:
    from fiboki.data.store import DataStore

    try:
        with DataStore(settings.data_root) as store:
            frame, version = store.read_latest(instrument=symbol, timeframe=tf)
    except Exception as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "bars_unavailable",
            "The bar dataset could not be read.",
            context={"error_class": type(exc).__name__},
        ) from exc
    return frame, str(getattr(version, "version_id", "") or "")


def _bar_dicts(frame: Any) -> tuple[list[dict[str, Any]], str | None]:
    volume_kind = markets_router._volume_column(frame)
    out: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        bar: dict[str, Any] = {
            "t": index.isoformat(),
            "o": float(row.open),
            "h": float(row.high),
            "l": float(row.low),
            "c": float(row.close),
        }
        if volume_kind is not None:
            raw = row[volume_kind]
            bar["v"] = None if raw is None or raw != raw else float(raw)
        out.append(bar)
    return out, volume_kind


# ================================================================== replay


class ReplayBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    t: datetime
    o: float
    h: float
    l: float  # noqa: E741 - the bar contract's own name
    c: float
    v: float | None = None


class ReplayTrade(BaseModel):
    """A backtest trade AS IT STOOD at the cursor.

    ``open_at_cursor`` trades have entered but not exited by the cursor: their
    exit, exit reason, P&L, costs and final stop are not known yet and are
    null, never the stored future values.
    """

    model_config = ConfigDict(frozen=True)

    trade_id: str
    strategy_id: str
    direction: str = Field(pattern="^(long|short)$")
    size: Figure
    entry_time: datetime
    entry_price: Figure
    stop_loss: Figure
    take_profit_targets: list[Figure]
    open_at_cursor: bool
    exit_time: datetime | None
    exit_price: Figure | None
    exit_reason: str | None
    net_pnl: Figure | None
    final_stop: Figure | None
    provenance: Provenance


class ReplayView(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    #: The replay instant: every item served is timed at or before it.
    cursor: datetime
    dataset_version_id: str
    volume_kind: str | None
    bars: list[ReplayBar]
    #: The close time of the last served bar (``<= cursor``), or null when no
    #: bar has closed by the cursor.
    last_close: datetime | None
    #: The cursor at which one more bar has closed (step forward), or null at
    #: the end of the dataset.
    next_cursor: datetime | None
    #: The cursor one bar earlier (step back), or null at the start.
    prev_cursor: datetime | None
    first_bar: datetime | None
    last_bar: datetime | None
    #: Bars closed by the cursor in the whole dataset (the served ones are the
    #: last ``limit`` of these).
    bars_closed: int
    bars_total: int
    #: The overlay payload restricted to the cursor (see the module docstring).
    overlays: markets_router.OverlayView
    run_id: str | None
    trades: list[ReplayTrade]
    trades_detail: str


def _close_of(open_time: Any, tf: Timeframe) -> Any:
    return open_time + timedelta(minutes=tf.minutes)


def _restrict_overlays(
    view: markets_router.OverlayView, cursor: datetime
) -> tuple[markets_router.OverlayView, list[str]]:
    """Every overlay item timed after ``cursor`` removed; the rest as served."""
    notes: list[str] = []
    levels = []
    dropped_open = 0
    for level in view.levels:
        if level.from_ > cursor:
            continue
        if level.to is None:
            # A position open at the end of the session: its persisted stop
            # and target are TODAY's, not the cursor's. Only its entry stands.
            if level.role != "entry":
                dropped_open += 1
                continue
            levels.append(level)
        elif level.to > cursor:
            # A closed trade's recorded stop or target, set at entry: it stood
            # at the cursor, and when it stopped standing is not yet known.
            levels.append(level.model_copy(update={"to": None}))
        else:
            levels.append(level)
    if dropped_open:
        notes.append(
            f"{dropped_open} stop/target level(s) of positions open now are not drawn: "
            "the journal holds their current values, not the values at the cursor."
        )
    series = [s for s in view.series if not s.display_only]
    if len(series) != len(view.series):
        notes.append("The chart-only lagging span is not served in replay (it reads later closes).")
    restricted = view.model_copy(
        update={
            "signals": [s for s in view.signals if s.t <= cursor],
            "fills": [f for f in view.fills if f.t <= cursor],
            "levels": levels,
            "regimes": [r for r in view.regimes if r.to <= cursor],
            "series": series,
            "events": [e for e in view.events if e.t <= cursor],
            "headlines": [h for h in view.headlines if h.t <= cursor],
            "window_to": min(view.window_to, cursor) if view.window_to else view.window_to,
        }
    )
    return restricted, notes


def _replay_trades(
    symbol: str, run_id: str, settings: Any, tf: str, lo: datetime, cursor: datetime
) -> tuple[list[ReplayTrade], str]:
    env = chart_router.backtest_trades(
        symbol, run_id, settings, from_=lo, to=cursor, timeframe=tf
    )
    out: list[ReplayTrade] = []
    for t in env.data.trades:
        if t.entry_time > cursor:
            continue
        closed = t.exit_time <= cursor
        out.append(
            ReplayTrade(
                trade_id=t.trade_id,
                strategy_id=t.strategy_id,
                direction=t.direction,
                size=t.size,
                entry_time=t.entry_time,
                entry_price=t.entry_price,
                stop_loss=t.stop_loss,
                take_profit_targets=t.take_profit_targets,
                open_at_cursor=not closed,
                exit_time=t.exit_time if closed else None,
                exit_price=t.exit_price if closed else None,
                exit_reason=t.exit_reason if closed else None,
                net_pnl=t.net_pnl if closed else None,
                final_stop=t.final_stop if closed else None,
                provenance=t.provenance,
            )
        )
    detail = (
        f"Backtest {run_id}: {len(out)} trade(s) entered by the cursor, "
        f"{sum(1 for t in out if t.open_at_cursor)} still open at it (exit, P&L and final "
        "stop withheld until their exit time)."
    )
    return out, detail


@router.get("/api/markets/replay/{symbol}", response_model=Envelope[ReplayView])
def replay(
    symbol: str,
    platform: PlatformDep,
    settings: SettingsDep,
    cursor: Annotated[datetime, Query(description="UTC instant; nothing after it is served.")],
    timeframe: str = Query("H1"),
    limit: int = Query(REPLAY_DEFAULT_BARS, ge=1, le=REPLAY_MAX_BARS),
    run: str | None = Query(None, description="A backtest run whose trades to replay."),
    include_indicators: bool = Query(True),
) -> Envelope[ReplayView]:
    """Closed candles, overlays and trades as of ``cursor``; never anything later."""
    import pandas as pd

    sym = _symbol(symbol)
    tf = _tf(timeframe)
    _mounted(settings, sym, tf.value)
    at = _utc(cursor)
    frame, version = _read_frame(settings, sym, tf)
    opens = frame.index
    closes = opens + pd.Timedelta(minutes=tf.minutes)
    closed_mask = closes <= pd.Timestamp(at)
    closed = frame[closed_mask]
    n_closed = int(closed_mask.sum())
    window = closed.tail(limit)
    bars, volume_kind = _bar_dicts(window)

    next_cursor = (
        closes[n_closed].to_pydatetime() if n_closed < len(frame) else None
    )
    prev_cursor = closes[n_closed - 2].to_pydatetime() if n_closed >= 2 else None
    caveats: list[Caveat] = [
        Caveat(
            code="replay_bar_close_rule",
            severity="info",
            message=f"A bar counts as closed at its open plus {tf.minutes} minutes. A bar "
            "whose venue session ended early (a Friday close) is shown one bar late, "
            "never early.",
            affects="bars",
            direction="pessimistic",
        )
    ]

    if window.empty:
        overlay_view = markets_router.OverlayView(
            symbol=sym, timeframe=tf.value, window_from=None, window_to=None,
            bars_dataset_version_id=version or None, signals=[], fills=[], levels=[],
            regimes=[], series=[], events=[], headlines=[],
            sections={"bars": markets_router.SectionStatus(
                available=False, detail="No bar of this dataset had closed by the cursor.")},
        )
        notes: list[str] = []
    else:
        lo = window.index[0].to_pydatetime()
        hi = window.index[-1].to_pydatetime()
        env = markets_router.overlays(
            sym, platform, settings, from_=lo, to=hi, strategy_id=None,
            timeframe=tf.value, limit=limit, include_indicators=include_indicators,
        )
        overlay_view, notes = _restrict_overlays(env.data, at)
        caveats.extend(env.caveats)
        # The overlay route reads its own copy of the latest dataset; if a new
        # version landed between the two reads, say so rather than mix them.
        if overlay_view.bars_dataset_version_id not in (None, version):
            caveats.append(
                Caveat(
                    code="replay_dataset_changed",
                    severity="warning",
                    message="A new dataset version landed during this step; overlays "
                    f"are from {overlay_view.bars_dataset_version_id}, bars from {version}.",
                    affects="series",
                    direction="unknown",
                )
            )

    trades: list[ReplayTrade] = []
    trades_detail = "No backtest run selected."
    if run:
        lo_t = window.index[0].to_pydatetime() if not window.empty else at
        trades, trades_detail = _replay_trades(sym, run, settings, tf.value, lo_t, at)
    for note in notes:
        caveats.append(
            Caveat(code="replay_withheld", severity="info", message=note,
                   affects="overlays", direction="unknown")
        )

    view = ReplayView(
        symbol=sym,
        timeframe=tf.value,
        cursor=at,
        dataset_version_id=version,
        volume_kind=volume_kind,
        bars=[ReplayBar(**b) for b in bars],
        last_close=closes[n_closed - 1].to_pydatetime() if n_closed else None,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        first_bar=opens[0].to_pydatetime() if len(opens) else None,
        last_bar=opens[-1].to_pydatetime() if len(opens) else None,
        bars_closed=n_closed,
        bars_total=len(frame),
        overlays=overlay_view,
        run_id=run,
        trades=trades,
        trades_detail=trades_detail,
    )
    return Envelope(
        data=view,
        source=_note(
            "live",
            f"Replay of dataset {version} as of {at.isoformat()}: closed bars only; "
            "indicators and regimes computed over those bars alone.",
        ),
        caveats=tuple(caveats),
    )
