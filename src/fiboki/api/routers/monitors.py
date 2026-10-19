"""MONITORS: watchlists over recorded quotes, the news monitor, the economic
calendar and Market Pulse.

Every number here is computed in the BACKEND from a recorded store, with its
provenance, and every row says how old its data is:

``GET /api/markets/watchlists`` · ``PUT/DELETE /api/markets/watchlists/{name}``
    The operator's watchlists (server-side, versioned, 409 on a stale write,
    audited), plus four built-in lists for an operator who has saved none.
``GET /api/markets/quotes?symbols=`` · ``GET /api/markets/watchlists/{name}/quotes``
    One row per symbol from the executable-price recorder's log
    (``data/quote_view.py``): bid, ask, mid, spread, change over the window, a
    sparkline of bucket closes, the session state from the instrument's
    session calendar, the data age by the API's clock and the API's verdict on
    it (``live``, ``stale``, ``closed``, ``none``). Conditional-format tones
    are attached by the API (``api/grid_format.py``).

The quotes are OANDA PRACTICE top of book polled every
``FIBOKI_QUOTES_INTERVAL`` seconds: practice prices (``broker_demo``), not
live executable prices, and moves between polls are not seen. Spot crypto is
not recorded by that recorder, so a crypto row says ``none`` rather than
borrowing a price from elsewhere.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AnyPrincipal, AuditDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.grid_format import Tone, tones_for
from fiboki.api.models import Page, SourceNote
from fiboki.api.provenance import Caveat, Figure, Series, SeriesPoint
from fiboki.api.routers.workspace import check_name, workspace_store, write_document
from fiboki.api.security import Principal, Role, require_operator
from fiboki.api.workspace_store import StoredDocument
from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Provenance
from fiboki.core.paths import resolve_paths
from fiboki.data.calendars import calendar_for
from fiboki.data.quote_view import QuoteWindow, read_window
from fiboki.data.recorder import QuoteRecord
from fiboki.marketstate.quote_stats import InstrumentStats, instrument_stats

router = APIRouter(prefix="/api/markets", tags=["monitors"])
OperatorPrincipal = Annotated[Principal, Depends(require_operator)]

#: A quote older than this while the instrument's session is open is STALE.
#: Three recorder polls at the default 30 s cadence: one missed poll is noise,
#: three is a recorder that is not recording (or a feed that is not quoting).
QUOTE_STALE_AFTER_S = 90.0
WINDOWS: dict[str, timedelta] = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "24h": timedelta(hours=24)}
SPARK_BUCKETS = 48
MAX_WATCHLIST = 60
PRACTICE_PROVIDER = "oanda_practice_pricing"

BUILTIN_WATCHLISTS: dict[str, tuple[str, ...]] = {
    "Majors": ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"),
    "GBP crosses": ("EURGBP", "GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD"),
    "Metals and indices": ("XAUUSD", "XAGUSD", "US500", "UK100", "DE40"),
    "Crypto": ("BTCGBP", "ETHGBP"),
}

DataState = Literal["live", "stale", "closed", "none"]


def _note(kind: str, detail: str) -> SourceNote:
    return SourceNote(kind=kind, detail=detail, as_of=datetime.now(tz=UTC))


def quotes_dir(settings: Any):
    return resolve_paths({"FIBOKI_STATE_DIR": str(settings.state_dir)}).quotes_dir


def _now(at: datetime | None) -> datetime:
    return (at or datetime.now(tz=UTC)).astimezone(UTC)


def known_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    try:
        instrument_registry.get(sym)
    except KeyError as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_symbol",
                       f"{sym} is not an instrument the platform lists.") from exc
    return sym


# ================================================================== quotes


class SessionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    open: bool
    calendar: str
    trading_hours: str


class QuoteRowView(BaseModel):
    """One watchlist row: the recorded quote, its age and the API's verdict on it."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    asset_class: str
    provider: str | None
    quote_time: datetime | None
    bid: Figure
    ask: Figure
    mid: Figure
    spread_pips: Figure
    #: Spread now over the instrument's typical (static) spread.
    spread_ratio: Figure
    change_pct: Figure
    range_pct: Figure
    age_s: Figure
    data_state: DataState
    data_state_detail: str
    market_state: str | None
    session: SessionView
    sparkline: Series
    #: Conditional-format tones by column, decided by api/grid_format.py.
    tones: dict[str, Tone]
    chart_href: str


def _provenance(rec: QuoteRecord | None) -> tuple[Provenance, bool, tuple[Caveat, ...]]:
    if rec is None or rec.provider == PRACTICE_PROVIDER:
        return Provenance.BROKER_DEMO, False, (
            Caveat(code="practice_prices", severity="info",
                   message="OANDA practice top of book, polled; not a live executable price.",
                   affects="price", direction="unknown"),
        )
    return Provenance.BROKER_DEMO, True, (
        Caveat(code="not_a_venue_quote", severity="warning",
               message=f"Recorded by provider {rec.provider!r}, not the OANDA practice feed.",
               affects="price", direction="unknown"),
    )


def _session(symbol: str, now: datetime) -> SessionView:
    inst = instrument_registry.get(symbol)
    try:
        cal = calendar_for(symbol)
        is_open = bool(cal.is_open(pd.Timestamp(now)))
        name = cal.name
    except KeyError:
        is_open, name = True, "unknown"
    return SessionView(open=is_open, calendar=name, trading_hours=inst.trading_hours)


def data_state(last: QuoteRecord | None, session_open: bool, now: datetime) -> tuple[DataState, str, float | None]:
    """The API's verdict on a quote's age. The workstation renders it; it never decides it."""
    if last is None:
        return "none", "No quote for this instrument was recorded in the window.", None
    age = max(0.0, (now - last.timestamp).total_seconds())
    if not session_open:
        return "closed", "The instrument's session is closed; the last quote is from before it closed.", age
    if age > QUOTE_STALE_AFTER_S:
        return "stale", (f"Session open but the newest recorded quote is {age:.0f} s old "
                         f"(stale after {QUOTE_STALE_AFTER_S:.0f} s)."), age
    return "live", f"Recorded {age:.0f} s ago.", age


def quote_row(symbol: str, window: QuoteWindow, start: datetime, now: datetime) -> QuoteRowView:
    inst = instrument_registry.get(symbol)
    quotes = window.by_instrument.get(symbol, [])
    last = quotes[-1] if quotes else None
    stats: InstrumentStats = instrument_stats(quotes, start, now, buckets=SPARK_BUCKETS)
    prov, estimated, caveats = _provenance(last)
    session = _session(symbol, now)
    state, detail, age = data_state(last, session.open, now)

    def fig(value: float | None, unit: str = "") -> Figure:
        if value is None:
            return Figure.missing(prov, unit=unit, reason="No recorded quote in the window.")
        return Figure(value=value, provenance=prov, unit=unit, as_of=last.timestamp if last else None,
                      sample_size=stats.n_quotes, estimated=estimated, caveats=caveats)

    spread_pips = last.spread_pips() if last is not None else None
    if spread_pips is not None and spread_pips != spread_pips:  # NaN
        spread_pips = None
    typical = inst.typical_spread_pips
    ratio = None if spread_pips is None or not typical else spread_pips / typical
    change = stats.change_pct
    row_values = {"change_pct": change, "data_state": state, "spread_ratio": ratio}
    points = tuple(SeriesPoint(t=t, v=v) for t, v in zip(stats.bucket_starts, stats.spark, strict=True))
    return QuoteRowView(
        symbol=symbol,
        asset_class=inst.asset_class.value,
        provider=last.provider if last else None,
        quote_time=last.timestamp if last else None,
        bid=fig(last.bid if last else None),
        ask=fig(last.ask if last else None),
        mid=fig(last.mid if last else None),
        spread_pips=fig(spread_pips, "pips"),
        spread_ratio=Figure(
            value=ratio, provenance=prov, unit="x", estimated=True,
            caveats=(Caveat(code="static_typical_spread", severity="info",
                            message="Divided by the registry's single typical spread, itself an assumption.",
                            affects="spread", direction="unknown"),),
        ),
        change_pct=fig(change, "pct"),
        range_pct=fig(stats.range_pct, "pct"),
        age_s=Figure(value=age, provenance=prov, unit="s") if age is not None
        else Figure.missing(prov, unit="s", reason="Never recorded in the window."),
        data_state=state,
        data_state_detail=detail,
        market_state=last.market_state.value if last else None,
        session=session,
        sparkline=Series(name=f"{symbol} mid, last per bucket", provenance=prov, points=points),
        tones=tones_for("watchlist", row_values),
        chart_href=f"/markets/{symbol}",
    )


def recorder_caveats(window: QuoteWindow, now: datetime) -> tuple[Caveat, ...]:
    out: list[Caveat] = []
    if window.absent:
        out.append(Caveat(code="recorder_never_ran", severity="warning",
                          message="No quote log exists: the recorder (fiboki quotes record) has never written "
                          "on this deployment. Every row is 'none'.", affects="price", direction="unknown"))
    elif window.newest_write is not None:
        age = (now - window.newest_write).total_seconds()
        if age > QUOTE_STALE_AFTER_S:
            out.append(Caveat(code="recorder_not_writing", severity="warning",
                              message=f"The recorder last wrote {age:.0f} s ago.", affects="price",
                              direction="unknown"))
    if window.torn_tail:
        out.append(Caveat(code="torn_tail", severity="info", message="A torn final record was skipped.",
                          affects="price", direction="unknown"))
    if window.corrupt_lines:
        out.append(Caveat(code="corrupt_records", severity="warning",
                          message=f"{window.corrupt_lines} corrupt record(s) in the read segments were skipped.",
                          affects="price", direction="unknown"))
    return tuple(out)


def _window(name: str) -> timedelta:
    if name not in WINDOWS:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_window",
                       f"window is one of {', '.join(WINDOWS)}.")
    return WINDOWS[name]


def quotes_page(settings: Any, symbols: list[str], window_name: str, now: datetime) -> Page[QuoteRowView]:
    span = _window(window_name)
    start = now - span
    window = read_window(quotes_dir(settings), start, now, instruments=symbols)
    items = [quote_row(s, window, start, now) for s in symbols]
    detail = (f"Executable-price recorder log ({window.segments_read} segment(s), {window.records_read} "
              f"quote(s) in the last {window_name}); computed by the API at {now.isoformat()}.")
    return Page(
        items=items, total=len(items), limit=max(len(items), 1),
        source=SourceNote(kind="absent" if window.absent else "live", detail=detail, as_of=now),
        caveats=recorder_caveats(window, now),
    )


@router.get("/quotes", response_model=Page[QuoteRowView])
def quotes(
    settings: SettingsDep,
    _: AnyPrincipal,
    symbols: str = Query(..., min_length=2, max_length=1200, description="Comma-separated symbols."),
    window: str = Query("24h"),
) -> Page[QuoteRowView]:
    """Recorded quotes, sparkline, session and data age for each symbol."""
    wanted = [known_symbol(s) for s in symbols.split(",") if s.strip()]
    if len(wanted) > MAX_WATCHLIST:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "too_many_symbols",
                       f"At most {MAX_WATCHLIST} symbols per request.")
    return quotes_page(settings, list(dict.fromkeys(wanted)), window, _now(None))


# ============================================================== watchlists


class WatchlistView(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: int
    updated_at: datetime | None
    symbols: list[str]
    #: The grid column set this list opens with (``/api/workspace/column-sets/watchlist``).
    column_set: str | None
    #: A built-in list (code), shown until the operator saves one of that name.
    builtin: bool
    can_edit: bool


class WatchlistWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_version: int = Field(ge=0)
    symbols: list[str] = Field(max_length=MAX_WATCHLIST)
    column_set: str | None = Field(default=None, max_length=48)


def _list_view(doc: StoredDocument, principal: Principal) -> WatchlistView:
    payload = doc.payload or {}
    return WatchlistView(
        name=doc.name, version=doc.version, updated_at=doc.updated_at,
        symbols=list(payload.get("symbols") or []), column_set=payload.get("column_set"),
        builtin=False, can_edit=principal.role.satisfies(Role.OPERATOR),
    )


def _builtin(name: str, principal: Principal) -> WatchlistView:
    return WatchlistView(name=name, version=0, updated_at=None, symbols=list(BUILTIN_WATCHLISTS[name]),
                         column_set=None, builtin=True, can_edit=principal.role.satisfies(Role.OPERATOR))


def _store_source(path: Any) -> SourceNote:
    return _note("live", f"Operator watchlists at {path} (display state, never a trading universe); "
                 "built-in lists are code.")


def resolve_watchlist(settings: Any, principal: Principal, name: str) -> WatchlistView:
    store = workspace_store(settings)
    try:
        doc = store.get(principal.user_id, "watchlist", name)
    except (sqlite3.Error, ValueError) as exc:
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "workspace_unavailable",
                       "The watchlist store could not be read.") from exc
    if doc.exists:
        return _list_view(doc, principal)
    if name in BUILTIN_WATCHLISTS and doc.version == 0:
        return _builtin(name, principal)
    raise ApiError(status.HTTP_404_NOT_FOUND, "watchlist_not_found", f"No watchlist is saved as {name!r}.",
                   context={"version": doc.version})


@router.get("/watchlists", response_model=Page[WatchlistView])
def list_watchlists(settings: SettingsDep, principal: AnyPrincipal) -> Page[WatchlistView]:
    """The operator's saved watchlists, then any built-in list they have not saved over."""
    store = workspace_store(settings)
    try:
        docs = store.list(principal.user_id, "watchlist")
        names = {d.name for d in docs}
        touched = {n for n in BUILTIN_WATCHLISTS if store.get(principal.user_id, "watchlist", n).version > 0}
    except (sqlite3.Error, ValueError) as exc:
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "workspace_unavailable",
                       "The watchlist store could not be read.") from exc
    items = [_list_view(d, principal) for d in docs]
    items += [_builtin(n, principal) for n in BUILTIN_WATCHLISTS if n not in names and n not in touched]
    return Page(items=items, total=len(items), limit=max(len(items), 1), source=_store_source(store.path))


@router.put("/watchlists/{name}", response_model=Page[WatchlistView])
def put_watchlist(
    name: str,
    body: WatchlistWrite,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
) -> Page[WatchlistView]:
    """Save a watchlist version (``base_version`` 0 creates or saves over a built-in). 409 on stale."""
    check_name(name, "watchlist")
    symbols = [known_symbol(s) for s in body.symbols]
    if len(set(symbols)) != len(symbols):
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "duplicate_symbols",
                       "A watchlist lists each symbol once.")
    store = workspace_store(settings)
    stored = write_document(
        store=store, kind="watchlist", name=name, base_version=body.base_version,
        payload={"symbols": symbols, "column_set": body.column_set}, action="save", audit=audit,
        principal=principal, request=request, settings=settings, label="Watchlist",
    )
    view = _list_view(stored, principal)
    return Page(items=[view], total=1, limit=1, source=_store_source(store.path))


@router.delete("/watchlists/{name}", response_model=Page[WatchlistView])
def delete_watchlist(
    name: str,
    request: Request,
    settings: SettingsDep,
    audit: AuditDep,
    principal: OperatorPrincipal,
    base_version: int = Query(..., ge=1),
) -> Page[WatchlistView]:
    """Delete a saved watchlist (a tombstone version; a deleted built-in stays hidden)."""
    check_name(name, "watchlist")
    store = workspace_store(settings)
    write_document(
        store=store, kind="watchlist", name=name, base_version=base_version, payload=None,
        action="delete", audit=audit, principal=principal, request=request, settings=settings,
        label="Watchlist",
    )
    return list_watchlists(settings, principal)


@router.get("/watchlists/{name}/quotes", response_model=Page[QuoteRowView])
def watchlist_quotes(
    name: str,
    settings: SettingsDep,
    principal: AnyPrincipal,
    window: str = Query("24h"),
) -> Page[QuoteRowView]:
    """The list's rows: recorded quote, sparkline, session and data age per symbol."""
    check_name(name, "watchlist")
    wl = resolve_watchlist(settings, principal, name)
    return quotes_page(settings, wl.symbols, window, _now(None))
