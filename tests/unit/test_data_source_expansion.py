"""Wider free and licence-clean data surface: GDELT, Finnhub limits and calendar,
the opt-in ForexFactory feed and calendar_diff, positioning snapshots, the FRED pack.

No network. Fixtures: ``news/bis_cbspeeches.rss`` is a trimmed copy of the live
BIS feed fetched 2026-09-29; every ``constructed_*`` fixture is built from the
documented response shape (no key exists here, GDELT returned 429 to this
environment's shared egress address, and the ForexFactory feed is deliberately
not copied because its terms prohibit copying it).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from fiboki.data.news import HeadlineStore, NewsRecorder, NewsSource, parse_feed
from fiboki.data.news.sources import (
    ENV_FINNHUB_API_KEY,
    ENV_GDELT_ENABLED,
    GDELT_QUERIES,
    FeedFormatError,
    FinnhubNewsClient,
    GdeltDocClient,
    env_flag,
    parse_gdelt,
    vendor_clients_from_env,
)
from fiboki.data.positioning import (
    MyfxbookOutlookClient,
    OandaBooksClient,
    PositioningRecorder,
    PositioningSnapshot,
    PositioningStore,
    positioning_clients_from_env,
)
from fiboki.data.positioning.oanda_books import parse_book
from fiboki.data.positioning.store import APPEND_ONLY_MESSAGE
from fiboki.data.providers import MACRO_DATASET_PACKS, AlfredProvider, MacroDatasetStore
from fiboki.data.providers.base import AuthenticationRequired, ProviderError, RateLimited
from fiboki.data.providers.calendar_feed import (
    CalendarFileError,
    calendar_diff,
    dated_event,
    load_events_file,
    write_events_file,
)
from fiboki.data.providers.finnhub import FinnhubCalendarProvider, parse_economic_calendar
from fiboki.data.providers.forexfactory_feed import (
    FF_JSON_URL,
    ForexFactoryFeed,
    SourceNotOptedIn,
    parse_ff_json,
)
from fiboki.data.providers.fred_pack import FRED_CROSS_ASSET_DAILY, fetch_pack
from fiboki.data.providers.ratelimit import DailyRequestBudget, SlidingWindowLimiter
from fiboki.data.store import DataStore
from fiboki.marketstate.calendar import OFFICIAL_EVENTS_FIXTURE, load_events_json
from tests.recorded_http import FIXTURES, RecordedHttpClient

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


# ------------------------------------------------------------ rate limits


def test_sliding_window_limits_sixty_per_minute_and_sleeps_the_remainder():
    clk = FakeClock()
    lim = SlidingWindowLimiter(60, 60.0, clock=clk, sleep=clk.sleep)
    for _ in range(60):
        assert lim.acquire() == 0.0
    with pytest.raises(RateLimited, match="60 call"):
        lim.acquire(block=False)
    clk.t += 10.0
    assert lim.acquire() == pytest.approx(50.0)  # waits until the first call leaves the window
    assert clk.slept == [pytest.approx(50.0)]


def test_daily_budget_resets_on_the_utc_day_and_refuses_without_spending():
    now = {"t": datetime(2026, 9, 29, 23, 0, tzinfo=UTC)}
    b = DailyRequestBudget(3, clock=lambda: now["t"])
    b.take(2)
    with pytest.raises(RateLimited):
        b.take(2)
    assert b.remaining == 1
    now["t"] += timedelta(hours=2)
    assert b.remaining == 3


def test_finnhub_clients_from_env_share_one_limiter_and_it_is_enforced():
    clients, _ = vendor_clients_from_env(object(), env={ENV_FINNHUB_API_KEY: "k"})
    fh = [c for c in clients if isinstance(c, FinnhubNewsClient)]
    assert len(fh) == 2 and fh[0].rate_limiter is fh[1].rate_limiter
    assert fh[0].rate_limiter.max_calls == 60 and fh[0].rate_limiter.window_s == 60.0

    clk = FakeClock()
    lim = SlidingWindowLimiter(1, 60.0, clock=clk, sleep=clk.sleep)
    http = RecordedHttpClient().add_file("https://finnhub.io/api/v1/news",
                                         "news/constructed_finnhub_forex.json")
    c = FinnhubNewsClient("SECRET", http, rate_limiter=lim)
    c.fetch()
    c.fetch()  # second call waits out the window instead of drawing a 429
    assert clk.slept == [pytest.approx(60.0)] and len(http.calls) == 2


# ------------------------------------------------------------------ GDELT


def test_gdelt_parses_dedupes_by_url_and_keeps_seendate_informational():
    text = (FIXTURES / "news" / "constructed_gdelt_central_banks.json").read_text()
    items, rejected = parse_gdelt(text, feed_key="gdelt_central_banks")
    assert rejected == 1  # untitled
    assert [i.title for i in items] == ["Fed holds rates steady, signals patience",
                                        "Lagarde tells lawmakers inflation risks are balanced"]
    assert items[0].vendor_published_at == datetime(2026, 9, 29, 10, 15, tzinfo=UTC)
    assert all(i.source is NewsSource.OTHER for i in items)


def test_gdelt_text_error_is_a_format_error_and_no_match_is_empty():
    with pytest.raises(FeedFormatError, match="non-JSON"):
        parse_gdelt("Your search contained a phrase that is too short.", feed_key="g")
    assert parse_gdelt("{}", feed_key="g") == ([], 0)


def test_gdelt_request_and_observed_at_is_first_poll(tmp_path):
    http = RecordedHttpClient().add_file("https://api.gdeltproject.org/api/v2/doc/doc",
                                         "news/constructed_gdelt_central_banks.json")
    clk = FakeClock()
    shared = SlidingWindowLimiter(1, 5.5, clock=clk, sleep=clk.sleep)
    readers = [GdeltDocClient(q, http, rate_limiter=shared) for q in GDELT_QUERIES[:2]]
    with HeadlineStore(tmp_path / "h.sqlite") as store:
        rec = NewsRecorder(store, readers)
        r1 = rec.poll_once(T0)
        # the same article from two queries is ONE row (same source + URL)
        assert r1.total("new") == 2 and r1.total("duplicate") == 2
        assert clk.slept == [pytest.approx(5.5)]  # one request per 5 s across queries
        call = http.calls[0]["params"]
        assert call["mode"] == "ArtList" and call["format"] == "json"
        assert call["maxrecords"] == 250 and call["sort"] == "DateDesc"
        rec2 = NewsRecorder(store, [GdeltDocClient(GDELT_QUERIES[0], http, rate_limiter=shared)])
        rec2.poll_once(T0 + timedelta(hours=1))
        rows = store.query(T0 + timedelta(hours=2), T0 - timedelta(days=1))
        assert {h.observed_at for h in rows} == {T0}


def test_gdelt_truncation_is_reported_in_the_poll_log(tmp_path):
    body = json.dumps({"articles": [
        {"url": f"https://e.com/{i}", "title": f"t{i}", "seendate": "20260929T100000Z"}
        for i in range(3)]})
    http = RecordedHttpClient().add("https://api.gdeltproject.org/api/v2/doc/doc", body)
    clk = FakeClock()
    reader = GdeltDocClient(GDELT_QUERIES[0], http, maxrecords=3,
                            rate_limiter=SlidingWindowLimiter(1, 5.5, clock=clk, sleep=clk.sleep))
    with HeadlineStore(tmp_path / "h.sqlite") as store:
        r = NewsRecorder(store, [reader]).poll_once(T0)
        assert r.per_feed["gdelt_central_banks"]["truncated"] is True
        assert store.status()["polls"][-1][2]["per_feed"]["gdelt_central_banks"]["truncated"]


def test_gdelt_is_opt_in_with_a_strict_boolean():
    clients, off = vendor_clients_from_env(object(), env={ENV_GDELT_ENABLED: "true"})
    assert sorted(c.key for c in clients) == sorted(f"gdelt_{q.key}" for q in GDELT_QUERIES)
    assert "gdelt" not in off
    assert len({id(c.rate_limiter) for c in clients}) == 1
    with pytest.raises(ValueError, match="not a boolean"):
        env_flag({ENV_GDELT_ENABLED: "ture"}, ENV_GDELT_ENABLED)


def test_bis_feed_parses_as_rss1():
    body = (FIXTURES / "news" / "bis_cbspeeches.rss").read_bytes()
    items, rej = parse_feed(body, source=NewsSource.OTHER, feed_key="bis_cbspeeches")
    assert rej == 0 and len(items) == 2
    assert items[0].url.startswith("https://www.bis.org/speeches/20260928-")
    assert items[0].vendor_published_at == datetime(2026, 9, 28, tzinfo=UTC)


# ------------------------------------------------------- Finnhub calendar


def test_finnhub_calendar_parse_counts_and_scheduled_times_only():
    payload = json.loads((FIXTURES / "calendar" / "constructed_finnhub_economic_calendar.json").read_text())
    events, counts = parse_economic_calendar(payload, retrieved_at="2026-09-29T12:00:00+00:00")
    assert counts == {"received": 6, "kept": 3, "unmapped_country": 1, "bad_impact": 1, "bad_time": 1}
    gdp = next(e for e in events if e["name"] == "GDP Growth Rate QoQ Adv")
    assert gdp["event_time"] == "2026-10-29T12:30:00+00:00" and gdp["currency"] == "USD"
    assert all(e[k] is None for e in events for k in ("actual", "forecast", "previous"))
    assert next(e for e in events if "Unemployment" in e["name"])["currency"] == "EUR"  # DE


def test_finnhub_calendar_header_key_limiter_premium_message_and_file(tmp_path):
    http = RecordedHttpClient().add_file("https://finnhub.io/api/v1/calendar/economic",
                                         "calendar/constructed_finnhub_economic_calendar.json")
    prov = FinnhubCalendarProvider(api_key="FH-SECRET", http_client=http)
    snap = prov.fetch(date(2026, 10, 26), date(2026, 10, 30), now=pd.Timestamp(T0))
    call = http.calls[0]
    assert call["headers"]["X-Finnhub-Token"] == "FH-SECRET"
    assert "FH-SECRET" not in str(call["params"]) and call["params"] == {"from": "2026-10-26",
                                                                         "to": "2026-10-30"}
    path = snap.write(tmp_path)
    assert path.name == "20260929T120000Z.json" and path.parent.name == "finnhub_calendar"
    loaded = load_events_json(path)  # the marketstate loader reads it unchanged
    assert len(loaded) == 3 and {e.source for e in loaded} == {"finnhub_calendar"}
    assert "FH-SECRET" not in path.read_text()

    denied = RecordedHttpClient().add("https://finnhub.io/api/v1/calendar/economic",
                                      '{"error":"You don\'t have access"}', status=403)
    with pytest.raises(AuthenticationRequired, match="Premium Access Required"):
        FinnhubCalendarProvider(api_key="k", http_client=denied).fetch(date(2026, 10, 1),
                                                                       date(2026, 10, 2))
    with pytest.raises(AuthenticationRequired):
        FinnhubCalendarProvider.from_env(http, env={}).fetch(date(2026, 10, 1), date(2026, 10, 2))


# ------------------------------------------------------ ForexFactory feed


def _ff_events():
    text = (FIXTURES / "calendar" / "constructed_ff_calendar_thisweek.json").read_text()
    return parse_ff_json(text, retrieved_at="2026-10-26T12:00:00+00:00")


def test_ff_feed_is_off_by_default_and_refuses_a_bad_flag():
    with pytest.raises(SourceNotOptedIn, match="FIBOKI_FF_CALENDAR_OPT_IN"):
        ForexFactoryFeed.from_env(object(), env={})
    with pytest.raises(SourceNotOptedIn):
        ForexFactoryFeed.from_env(object(), env={"FIBOKI_FF_CALENDAR_OPT_IN": "false"})
    with pytest.raises(ValueError):
        ForexFactoryFeed.from_env(object(), env={"FIBOKI_FF_CALENDAR_OPT_IN": "maybe"})


def test_ff_parse_converts_offsets_skips_holidays_and_widens_midnight_rows():
    events, counts = _ff_events()
    assert counts == {"received": 10, "kept": 7, "skipped_holiday_or_non_economic": 2,
                      "bad_impact": 0, "bad_time": 1, "midnight_unverified": 1}
    fomc = next(e for e in events if e["name"] == "Federal Funds Rate")
    assert fomc["event_time"] == "2026-10-28T18:00:00+00:00" and fomc["source"] == "forexfactory_feed"
    cpi = next(e for e in events if e["name"] == "German Prelim CPI m/m")
    assert cpi["time_known"] is False and cpi["window_end"] == "2026-10-30T04:00:00+00:00"


def test_ff_opted_in_fetch_writes_a_tagged_file_and_rate_limits(tmp_path):
    http = RecordedHttpClient().add_file(FF_JSON_URL, "calendar/constructed_ff_calendar_thisweek.json")
    clk = FakeClock()
    feed = ForexFactoryFeed.from_env(http, env={"FIBOKI_FF_CALENDAR_OPT_IN": "true"}, clock=clk)
    snap = feed.fetch(now=pd.Timestamp("2026-10-26T12:00:00Z"))
    with pytest.raises(RateLimited):
        feed.fetch()
    path = snap.write(tmp_path)
    manifest = json.loads(path.read_text())
    assert manifest["terms_status"] == "opt_in_unclear" and "prohibited" in manifest["notice"]
    assert {e.source for e in load_events_json(path)} == {"forexfactory_feed"}


def test_writer_never_replaces_the_official_calendar_and_is_write_once(tmp_path):
    ev = dated_event(event_time=pd.Timestamp("2026-10-28T18:00Z"), currency="USD", name="x",
                     impact="high", source="s", source_url="u", retrieved_at="r")
    with pytest.raises(CalendarFileError, match="official"):
        write_events_file(tmp_path / "scheduled_events_official.json", [ev], source="s",
                          retrieved_at="r")
    p = write_events_file(tmp_path / "a.json", [ev], source="s", retrieved_at="r")
    with pytest.raises(CalendarFileError, match="write-once"):
        write_events_file(p, [ev], source="s", retrieved_at="r")
    with pytest.raises(CalendarFileError, match="values"):
        write_events_file(tmp_path / "b.json", [{**ev, "actual": 1.0}], source="s", retrieved_at="r")
    assert load_events_file(p).events[0]["name"] == "x"


def test_calendar_diff_against_the_official_calendar_reports_both_sides():
    official = load_events_json(OFFICIAL_EVENTS_FIXTURE)
    feed, _ = _ff_events()
    d = calendar_diff(official, feed)
    matched = {(o.get("name"), f["name"]) for o, f in d.matched}
    assert matched == {("FOMC Rate Decision", "Federal Funds Rate"),
                       ("ECB Rate Decision", "Main Refinancing Rate"),
                       ("Bank of Japan Rate Decision", "BOJ Policy Rate")}  # inside the BoJ window
    # present in the feed, absent from our fixture: the omissions worth checking
    assert sorted(e["name"] for e in d.only_in_feed) == ["Advance GDP q/q", "BOE Gov Bailey Speaks"]
    assert d.only_in_official == ()
    assert d.window[0] == "2026-10-27T14:00:00+00:00"  # the feed's first kept event, not 2024


def test_calendar_diff_reports_an_event_missing_from_the_feed():
    official = load_events_json(OFFICIAL_EVENTS_FIXTURE)
    feed = [e for e in _ff_events()[0] if e["name"] != "Federal Funds Rate"]
    d = calendar_diff(official, feed)
    assert [e["name"] for e in d.only_in_official] == ["FOMC Rate Decision"]
    assert d.to_dict()["only_in_official"][0]["currency"] == "USD"


# ------------------------------------------------------------ positioning


def _oanda_http() -> RecordedHttpClient:
    http = RecordedHttpClient()
    http.add_file("https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/positionBook",
                  "positioning/constructed_oanda_position_book.json")
    http.add_file("https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/orderBook",
                  "positioning/constructed_oanda_order_book.json")
    return http


def test_oanda_book_parse_and_client_are_read_only_and_host_checked():
    payload = json.loads((FIXTURES / "positioning" / "constructed_oanda_position_book.json").read_text())
    snap = parse_book(payload, "position_book")
    assert snap.vendor_time == datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
    assert snap.payload["long_pct_total"] == pytest.approx(0.75)
    assert snap.payload["short_pct_above_price"] == pytest.approx(0.2034)
    c = OandaBooksClient(token="TOK", http_client=_oanda_http(), instruments=("EUR_USD",))
    assert c.url("EUR_USD", "order_book").endswith("/v3/instruments/EUR_USD/orderBook")
    with pytest.raises(ValueError):
        c.url("EUR_USD/../../accounts", "order_book")
    with pytest.raises(ValueError):
        OandaBooksClient(token="t", http_client=None, environment="fxtrade.evil")
    live = OandaBooksClient.from_env(None, env={"FIBOKI_OANDA_BOOKS_TOKEN": "t",
                                                "FIBOKI_OANDA_BOOKS_ENVIRONMENT": "live"})
    assert live.base_url == "https://api-fxtrade.oanda.com"
    with pytest.raises(ProviderError, match="empty book"):
        parse_book({"positionBook": {**payload["positionBook"], "buckets": []}}, "position_book")


def test_oanda_books_record_hourly_dedupe_and_point_in_time(tmp_path):
    http = _oanda_http()
    client = OandaBooksClient(token="TOK", http_client=http, instruments=("EUR_USD",))
    with PositioningStore(tmp_path / "p.sqlite") as store:
        rec = PositioningRecorder(store, [client])
        r1 = rec.poll_once(T0)
        assert r1.per_client["oanda_books_practice"] == {"fetched": 2, "new": 2, "duplicate": 0}
        assert all(c["headers"]["Authorization"] == "Bearer TOK" for c in http.calls)
        assert rec.poll_once(T0 + timedelta(minutes=30)).per_client["oanda_books_practice"] == {
            "skipped": "min interval 3600s"}
        r3 = rec.poll_once(T0 + timedelta(hours=1))  # same book time and content: duplicate
        assert r3.per_client["oanda_books_practice"]["duplicate"] == 2
        # the book says 10:00, we held it at 12:00: before 12:00 it did not exist for us
        assert store.as_of("oanda_books", "EUR_USD", "position_book", T0 - timedelta(minutes=1)) is None
        got = store.as_of("oanda_books", "EUR_USD", "position_book", T0 + timedelta(hours=5))
        assert got is not None and got.observed_at == T0 and got.vendor_time.hour == 10


def test_oanda_failures_are_reported_not_empty(tmp_path):
    http = RecordedHttpClient()
    http.add("https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/positionBook", "{}", 404)
    http.add("https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/orderBook", "{}", 404)
    client = OandaBooksClient(token="TOK", http_client=http, instruments=("EUR_USD",))
    with PositioningStore(tmp_path / "p.sqlite") as store:
        r = PositioningRecorder(store, [client]).poll_once(T0)
        assert "every OANDA book request failed" in r.errors["oanda_books_practice"]
        assert "TOK" not in json.dumps(r.to_dict())
    with pytest.raises(AuthenticationRequired):
        OandaBooksClient(token=None, http_client=http).fetch()


def _myfx_http() -> RecordedHttpClient:
    http = RecordedHttpClient()
    http.add_file("https://www.myfxbook.com/api/login.json", "positioning/constructed_myfxbook_login.json")
    http.add_file("https://www.myfxbook.com/api/get-community-outlook.json",
                  "positioning/constructed_myfxbook_outlook.json")
    return http


def test_myfxbook_session_reuse_budget_and_snapshots(tmp_path):
    http = _myfx_http()
    budget = DailyRequestBudget(100, clock=lambda: T0)
    c = MyfxbookOutlookClient(email="me@example.com", password="PW-SECRET", http_client=http,
                              budget=budget, symbols=("EURUSD", "XAUUSD"))
    snaps = c.fetch()
    assert {s.instrument for s in snaps} == {"EURUSD", "XAUUSD", "_GENERAL"}
    eur = next(s for s in snaps if s.instrument == "EURUSD")
    assert eur.payload["longPercentage"] == 39.0 and eur.vendor_time is None
    c.fetch()  # session reused: one request, not two
    paths = [call["url"].rsplit("/", 1)[-1] for call in http.calls]
    assert paths == ["login.json", "get-community-outlook.json", "get-community-outlook.json"]
    assert budget.remaining == 97
    with PositioningStore(tmp_path / "p.sqlite") as store:
        store.record(snaps, T0)
        store.record(snaps, T0 + timedelta(hours=1))  # no vendor time: each poll is a fact
        hist = store.history("myfxbook_outlook", "EURUSD", "community_outlook",
                             since=T0, as_of=T0 + timedelta(hours=2))
        assert [h.observed_at for h in hist] == [T0, T0 + timedelta(hours=1)]


def test_myfxbook_credentials_scrubbed_and_budget_refuses():
    http = RecordedHttpClient()
    http.fail["www.myfxbook.com/api/login.json"] = RuntimeError(
        "error for https://www.myfxbook.com/api/login.json?email=me@example.com&password=PW-SECRET")
    c = MyfxbookOutlookClient(email="me@example.com", password="PW-SECRET", http_client=http)
    with pytest.raises(ProviderError) as info:
        c.fetch()
    assert "PW-SECRET" not in str(info.value) and "me@example.com" not in str(info.value)
    spent = MyfxbookOutlookClient(email="e", password="p", http_client=_myfx_http(),
                                  budget=DailyRequestBudget(1, clock=lambda: T0))
    with pytest.raises(RateLimited):
        spent.fetch()  # login spends the only request; the outlook call is refused locally


def test_positioning_store_is_append_only(tmp_path):
    path = tmp_path / "p.sqlite"
    with PositioningStore(path) as store:
        store.record([PositioningSnapshot("s", "EURUSD", "k", None, {"a": 1})], T0)
    conn = sqlite3.connect(path)
    for sql in ("UPDATE snapshot SET payload_json = '{}'", "DELETE FROM snapshot",
                "INSERT OR REPLACE INTO snapshot SELECT * FROM snapshot"):
        with pytest.raises(sqlite3.DatabaseError, match=APPEND_ONLY_MESSAGE):
            conn.execute(sql)
    conn.close()


def test_positioning_clients_are_off_without_credentials_and_say_so():
    clients, off = positioning_clients_from_env(object(), env={})
    assert clients == [] and set(off) == {"oanda_books", "myfxbook_outlook"}


# -------------------------------------------------------------- FRED pack


def _alfred_pack_http() -> RecordedHttpClient:
    payloads = json.loads((FIXTURES / "macro" / "constructed_alfred_cross_asset_pack.json").read_text())
    http = RecordedHttpClient()
    for sid in FRED_CROSS_ASSET_DAILY.series_ids:  # served in pack order, one page each
        http.add("https://api.stlouisfed.org/fred/series/observations", json.dumps(payloads[sid]))
    return http


def test_fred_pack_is_registered_vintage_aware_and_storable(tmp_path):
    assert MACRO_DATASET_PACKS["fred_cross_asset_daily"] is FRED_CROSS_ASSET_DAILY
    assert FRED_CROSS_ASSET_DAILY.series_ids == ("DGS2", "DGS10", "DTWEXBGS", "VIXCLS", "DCOILWTICO")
    http = _alfred_pack_http()
    ds = fetch_pack(FRED_CROSS_ASSET_DAILY, AlfredProvider(api_key="FRED-KEY", http_client=http),
                    now=pd.Timestamp(T0))
    assert [c["params"]["series_id"] for c in http.calls] == list(FRED_CROSS_ASSET_DAILY.series_ids)
    assert ds.dataset_key == "fred_cross_asset_daily" and set(ds.frame["availability_basis"]) == {"vintage"}
    assert "FRED-KEY" not in json.dumps(ds.request)
    # DGS10 for 2026-09-21 was 4.10 until the 2026-09-24 vintage revised it to 4.11
    before = ds.as_of(pd.Timestamp("2026-09-23T12:00Z"), series_ids=["DGS10"])
    after = ds.as_of(pd.Timestamp("2026-09-25T12:00Z"), series_ids=["DGS10"])
    v = lambda f: dict(zip(f["period"], f["value"], strict=True))  # noqa: E731
    assert v(before)["2026-09-21"] == 4.10 and v(after)["2026-09-21"] == 4.11
    # nothing is known before the first vintage's day has ended in Chicago
    assert ds.as_of(pd.Timestamp("2026-09-22T04:59Z")).empty
    DataStore.initialise(tmp_path)
    vid, _, created = MacroDatasetStore(tmp_path).write(ds)
    assert created and MacroDatasetStore(tmp_path).write(ds)[2] is False
    assert ds.report["pack"]["series"][3]["terms_status"] == "personal_only"  # VIXCLS


def test_fred_pack_refuses_the_wrong_provider():
    class NotAlfred:
        descriptor = type("D", (), {"name": "ecb_sdmx"})()

    with pytest.raises(ProviderError, match="alfred"):
        fetch_pack(FRED_CROSS_ASSET_DAILY, NotAlfred())
