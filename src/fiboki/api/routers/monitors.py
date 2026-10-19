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
from urllib.parse import quote

import pandas as pd
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from fiboki.api.deps import AnyPrincipal, AuditDep, SettingsDep
from fiboki.api.errors import ApiError
from fiboki.api.grid_format import Tone, tones_for
from fiboki.api.models import Envelope, Page, SourceNote
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


# ==================================================================== news

#: The headline recorder polls every 300 s (deploy/launchd/uk.fiboki.news.plist);
#: three missed polls is a recorder that is not recording.
NEWS_STALE_AFTER_S = 900.0
NEWS_HOURS = (1, 4, 12, 24, 72, 168)
_RELATED_UNIVERSE: tuple[str, ...] = tuple(
    dict.fromkeys(BUILTIN_WATCHLISTS["Majors"] + BUILTIN_WATCHLISTS["GBP crosses"] + ("EURJPY", "XAUUSD"))
)


class ChartLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    href: str


class HeadlineView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    source: str
    #: The currency of a central-bank source; ``None`` for a vendor source (not tagged).
    currency: str | None
    title: str
    url: str
    #: The point-in-time key: when OUR recorder first saw it (UTC).
    observed_at: datetime
    #: The vendor's own claim, shown for reference and never used for ordering.
    vendor_published_at: datetime | None
    chart_links: list[ChartLink]


class NewsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    headlines: list[HeadlineView]
    #: Echo of the filters the API applied.
    instrument: str | None
    currencies: list[str]
    sources: list[str]
    hours: int
    #: Every source the store holds (for the filter), whatever the window.
    available_sources: list[str]
    last_observed_at: datetime | None
    last_poll_at: datetime | None
    recorder_state: Literal["live", "stale", "never"]
    recorder_detail: str


def _ts(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _chart_links(currency: str | None, instrument: str | None) -> list[ChartLink]:
    from fiboki.marketstate.calendar import instrument_currencies

    if instrument:
        return [ChartLink(symbol=instrument, href=f"/markets/{instrument}")]
    if currency is None:
        return []
    out = [s for s in _RELATED_UNIVERSE if currency in instrument_currencies(s)][:4]
    return [ChartLink(symbol=s, href=f"/markets/{s}") for s in out]


@router.get("/news", response_model=Envelope[NewsView])
def news(
    settings: SettingsDep,
    _: AnyPrincipal,
    instrument: str | None = Query(None),
    currency: Annotated[list[str] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
    hours: int = Query(24),
    limit: int = Query(200, ge=1, le=500),
) -> Envelope[NewsView]:
    """Recorded headlines, newest first, filtered by instrument, currency and source.

    Read-only (the store is opened ``mode=ro``). An instrument filter narrows the
    central-bank sources to its currencies; VENDOR headlines are not currency-tagged
    at this layer and pass through an instrument or currency filter, said so in a
    caveat, exactly as the chart's headline overlay treats them.
    """
    from fiboki.data.news.store import CURRENCY_BY_SOURCE, NewsSource, to_utc_text
    from fiboki.marketstate.calendar import instrument_currencies

    if hours not in NEWS_HOURS:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_hours",
                       f"hours is one of {', '.join(map(str, NEWS_HOURS))}.")
    sym = known_symbol(instrument) if instrument else None
    ccys = {c.strip().upper() for c in (currency or []) if c.strip()}
    if sym:
        ccys |= set(instrument_currencies(sym))
    valid_sources = {s.value for s in NewsSource}
    wanted = {s.strip().lower() for s in (source or []) if s.strip()}
    if wanted - valid_sources:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_source",
                       f"Unknown source(s): {', '.join(sorted(wanted - valid_sources))}.")
    now = _now(None)
    since = now - timedelta(hours=hours)
    path = resolve_paths({"FIBOKI_STATE_DIR": str(settings.state_dir)}).news_store
    banks = {s.value: c for s, c in CURRENCY_BY_SOURCE.items()}
    caveats: list[Caveat] = []
    if ccys:
        caveats.append(Caveat(code="vendor_untagged", severity="info",
                              message="Central-bank headlines are narrowed to the chosen currencies; vendor "
                              "headlines are not currency-tagged and pass through.", affects="filter",
                              direction="unknown"))
    empty = NewsView(headlines=[], instrument=sym, currencies=sorted(ccys), sources=sorted(wanted), hours=hours,
                     available_sources=[], last_observed_at=None, last_poll_at=None, recorder_state="never",
                     recorder_detail="No headline store has been recorded on this deployment (fiboki news record).")
    if not path.exists():
        return Envelope(data=empty, source=SourceNote(kind="absent", detail=f"No headline store at {path}.", as_of=now),
                        caveats=tuple(caveats))
    try:
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=2.0) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='headline'").fetchone() is None:
                return Envelope(data=empty, source=SourceNote(kind="absent", detail="The headline store is empty.",
                                                              as_of=now), caveats=tuple(caveats))
            available = [r[0] for r in conn.execute("SELECT DISTINCT source FROM headline ORDER BY source")]
            last_obs = conn.execute("SELECT MAX(observed_at) FROM headline").fetchone()[0]
            try:
                last_poll = conn.execute("SELECT MAX(finished_at) FROM poll_log").fetchone()[0]
            except sqlite3.OperationalError:
                last_poll = None
            rows = conn.execute(
                "SELECT id, source, url, title, vendor_published_at, observed_at FROM headline "
                "WHERE observed_at >= ? AND observed_at <= ? ORDER BY observed_at DESC, id DESC",
                (to_utc_text(since), to_utc_text(now)),
            ).fetchall()
    except sqlite3.Error as exc:
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "news_unavailable",
                       "The headline store could not be read.", context={"error_class": type(exc).__name__}) from exc
    items: list[HeadlineView] = []
    for hid, src, url, title, vendor, observed in rows:
        bank_ccy = banks.get(src)
        if wanted and src not in wanted:
            continue
        if ccys and bank_ccy is not None and bank_ccy not in ccys:
            continue
        seen = _ts(observed)
        if seen is None:
            continue
        items.append(HeadlineView(
            id=int(hid), source=src, currency=bank_ccy, title=title, url=url, observed_at=seen,
            vendor_published_at=_ts(vendor), chart_links=_chart_links(bank_ccy, sym),
        ))
        if len(items) >= limit:
            break
    poll_at = _ts(last_poll)
    if poll_at is None:
        state, detail = "never", "The store has headlines but no poll log: the recorder's cadence is unknown."
    else:
        age = (now - poll_at).total_seconds()
        if age > NEWS_STALE_AFTER_S:
            state, detail = "stale", f"The last poll finished {age:.0f} s ago (stale after {NEWS_STALE_AFTER_S:.0f} s)."
        else:
            state, detail = "live", f"The last poll finished {age:.0f} s ago."
    view = NewsView(headlines=items, instrument=sym, currencies=sorted(ccys), sources=sorted(wanted), hours=hours,
                    available_sources=available, last_observed_at=_ts(last_obs), last_poll_at=poll_at,
                    recorder_state=state, recorder_detail=detail)
    return Envelope(
        data=view,
        source=SourceNote(kind="live", detail=f"Append-only headline store at {path}, read-only; "
                          "ordered by observed_at (our clock), never by the vendor's time.", as_of=now),
        caveats=tuple(caveats),
    )


# ================================================================ calendar

CALENDAR_DAYS = (1, 3, 7, 14, 31)
#: How long after its time an instant release still reads as "in progress".
RELEASE_GRACE = timedelta(minutes=15)

EventStatus = Literal["upcoming", "in_progress", "released"]


class CalendarEventView(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    name: str
    currency: str
    impact: Literal["low", "medium", "high"]
    event_time: datetime
    #: For a release with no fixed time (the Bank of Japan): the window's end.
    window_end: datetime | None
    time_known: bool
    #: The API's judgement at ``as_of``: upcoming, in progress (a window, or the
    #: first 15 minutes after an instant release) or released.
    status: EventStatus
    source: str
    source_url: str
    tags: list[str]
    #: Charts of the instruments this currency moves, each opened at the event.
    chart_links: list[ChartLink]


class CalendarView(BaseModel):
    model_config = ConfigDict(frozen=True)

    events: list[CalendarEventView]
    #: The official calendar version read: the NEWEST listed (the paper path),
    #: never the research-pinned one.
    calendar_version: str
    research_pinned_version: str
    events_sha256: str
    declared_start: datetime | None
    declared_end: datetime | None
    covered_currencies: list[str]
    window_from: datetime
    window_to: datetime
    impacts: list[str]
    currencies: list[str]
    instrument: str | None
    #: The next upcoming event in the filtered set, if any (for a countdown).
    next_event_id: str | None


def _calendar_index_entry(version: str) -> dict[str, Any]:
    from fiboki.marketstate.calendar import official_calendar_index

    return official_calendar_index()["versions"][version]


_CALENDARS: dict[str, Any] = {}


def newest_calendar() -> tuple[str, Any]:
    """The newest listed official calendar (paper path), loaded once per version."""
    from fiboki.marketstate.calendar import latest_official_calendar_version, load_official_calendar

    version = latest_official_calendar_version()
    if version not in _CALENDARS:
        _CALENDARS[version] = load_official_calendar(version=version)
    return version, _CALENDARS[version]


def event_status(event: Any, now: datetime) -> EventStatus:
    start = event.event_time.to_pydatetime()
    end = event.window_end.to_pydatetime() if event.window_end is not None else start + RELEASE_GRACE
    if now < start:
        return "upcoming"
    if now <= end:
        return "in_progress"
    return "released"


@router.get("/calendar", response_model=Envelope[CalendarView])
def calendar(
    _: AnyPrincipal,
    impact: Annotated[list[str] | None, Query()] = None,
    currency: Annotated[list[str] | None, Query()] = None,
    instrument: str | None = Query(None),
    days: int = Query(7),
    past_hours: int = Query(6, ge=0, le=72),
    limit: int = Query(200, ge=1, le=1000),
) -> Envelope[CalendarView]:
    """Scheduled official releases from the NEWEST official calendar version.

    The paper path and every operator view read the newest version; research and
    backtests pin ``PINNED_RESEARCH_CALENDAR_VERSION`` (``marketstate/calendar.py``)
    and are unaffected by what this screen shows. Events after the calendar's
    declared end are unknown, not absent, and the response says so.
    """
    from fiboki.marketstate.calendar import (
        PINNED_RESEARCH_CALENDAR_VERSION,
        ImpactLevel,
        instrument_currencies,
    )

    if days not in CALENDAR_DAYS:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_days",
                       f"days is one of {', '.join(map(str, CALENDAR_DAYS))}.")
    impacts = sorted({i.strip().lower() for i in (impact or []) if i.strip()})
    valid = {i.value for i in ImpactLevel}
    if set(impacts) - valid:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_impact",
                       f"impact is one or more of {', '.join(sorted(valid))}.")
    sym = known_symbol(instrument) if instrument else None
    ccys = {c.strip().upper() for c in (currency or []) if c.strip()}
    if sym:
        ccys |= set(instrument_currencies(sym))
    now = _now(None)
    lo, hi = now - timedelta(hours=past_hours), now + timedelta(days=days)
    try:
        version, cal = newest_calendar()
        entry = _calendar_index_entry(version)
    except Exception as exc:  # the loader refuses a tampered version: say so
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "calendar_unavailable",
                       f"The official calendar could not be loaded: {type(exc).__name__}: {exc}") from exc
    found = cal.events_between(pd.Timestamp(lo), pd.Timestamp(hi), currencies=sorted(ccys) or None)
    events: list[CalendarEventView] = []
    for e in sorted(found, key=lambda x: (x.event_time, x.currency, x.event_id)):
        if impacts and e.impact.value not in impacts:
            continue
        links = _chart_links(e.currency, sym)
        events.append(CalendarEventView(
            event_id=e.event_id, name=e.name, currency=e.currency, impact=e.impact.value,
            event_time=e.event_time.to_pydatetime(),
            window_end=e.window_end.to_pydatetime() if e.window_end is not None else None,
            time_known=e.time_known, status=event_status(e, now), source=e.source, source_url=e.source_url,
            tags=list(e.tags),
            chart_links=[ChartLink(symbol=link.symbol, href=f"{link.href}?event={quote(e.event_id, safe='')}") for link in links],
        ))
        if len(events) >= limit:
            break
    coverage = cal.coverage()
    caveats: list[Caveat] = []
    declared_end = coverage.declared_end.to_pydatetime() if coverage.declared_end is not None else None
    if declared_end is not None and hi > declared_end:
        caveats.append(Caveat(code="beyond_declared_end", severity="warning",
                              message=f"The calendar is complete only to {declared_end.isoformat()}; later "
                              "releases are unknown, not absent.", affects="events", direction="unknown"))
    uncovered = sorted(ccys - set(coverage.currencies))
    if uncovered:
        caveats.append(Caveat(code="currency_not_covered", severity="warning",
                              message=f"No official events are recorded for {', '.join(uncovered)}: an empty "
                              "list for them is a gap in the calendar, not a quiet week.", affects="events",
                              direction="unknown"))
    upcoming = next((ev for ev in events if ev.status == "upcoming"), None)
    view = CalendarView(
        events=events, calendar_version=version, research_pinned_version=PINNED_RESEARCH_CALENDAR_VERSION,
        events_sha256=str(entry.get("events_sha256", "")),
        declared_start=coverage.declared_start.to_pydatetime() if coverage.declared_start is not None else None,
        declared_end=declared_end, covered_currencies=list(coverage.currencies), window_from=lo, window_to=hi,
        impacts=impacts, currencies=sorted(ccys), instrument=sym,
        next_event_id=upcoming.event_id if upcoming else None,
    )
    return Envelope(
        data=view,
        source=SourceNote(kind="live", detail=f"Official scheduled-events calendar {version} (newest listed; "
                          f"the paper path). Research pins {PINNED_RESEARCH_CALENDAR_VERSION}.", as_of=now),
        caveats=tuple(caveats),
    )


@router.get("/calendar/events/{event_id}", response_model=Envelope[CalendarEventView])
def calendar_event(event_id: str, _: AnyPrincipal, instrument: str | None = Query(None)) -> Envelope[CalendarEventView]:
    """One event of the newest official calendar, by id (what a chart opened with ``?event=`` shows)."""
    sym = known_symbol(instrument) if instrument else None
    version, cal = newest_calendar()
    now = _now(None)
    for e in cal.all_events():
        if e.event_id == event_id:
            links = _chart_links(e.currency, sym)
            view = CalendarEventView(
                event_id=e.event_id, name=e.name, currency=e.currency, impact=e.impact.value,
                event_time=e.event_time.to_pydatetime(),
                window_end=e.window_end.to_pydatetime() if e.window_end is not None else None,
                time_known=e.time_known, status=event_status(e, now), source=e.source, source_url=e.source_url,
                tags=list(e.tags),
                chart_links=[ChartLink(symbol=link.symbol, href=f"{link.href}?event={quote(e.event_id, safe='')}") for link in links],
            )
            return Envelope(data=view, source=SourceNote(kind="live", detail=f"Official calendar {version}.", as_of=now))
    raise ApiError(status.HTTP_404_NOT_FOUND, "event_not_found",
                   f"No event {event_id!r} in the official calendar {version}.")
