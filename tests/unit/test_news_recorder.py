"""Headline recorder: parsing, append-only storage, dedupe and point-in-time reads.

Every HTTP response here is a recorded fixture (tests/fixtures/news): eight are
trimmed copies of the live central-bank feeds fetched on 2026-09-28; the Atom,
Finnhub and Marketaux ones are CONSTRUCTED from the documented shapes because
no bank serves Atom and no vendor key exists in this environment.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from fiboki.cli import main as cli_main
from fiboki.data.news import (
    OFFICIAL_FEEDS,
    HeadlineStore,
    NewItem,
    NewsRecorder,
    NewsSource,
    NewsStoreError,
    gaps_in_polls,
    parse_feed,
    run_loop,
)
from fiboki.data.news.sources import (
    ENV_FINNHUB_API_KEY,
    ENV_MARKETAUX_API_KEY,
    FeedFormatError,
    FinnhubNewsClient,
    MarketauxNewsClient,
    parse_finnhub,
    parse_marketaux,
    vendor_clients_from_env,
)
from fiboki.data.news.store import APPEND_ONLY_MESSAGE
from tests.recorded_http import FIXTURES, RecordedHttpClient

T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

#: fixture file -> (source, feed key, items, first title, first vendor instant)
FEED_CASES = {
    "fed_press_monetary.xml": (NewsSource.FED_RSS, 3, "Federal Reserve issues FOMC statement",
                               datetime(2026, 9, 16, 18, 0, tzinfo=UTC)),
    "fed_speeches.xml": (NewsSource.FED_RSS, 3, "Cook, An Update on AI and the Economy",
                         datetime(2026, 9, 28, 17, 25, tzinfo=UTC)),
    "ecb_press.xml": (NewsSource.ECB_RSS, 3,
                      "Christine Lagarde: Hearing of the Committee on Economic and Monetary "
                      "Affairs of the European Parliament",
                      datetime(2026, 9, 28, 13, 30, tzinfo=UTC)),
    "boe_news.xml": (NewsSource.BOE_RSS, 3,
                     "Minutes of the Market Participants Group meeting \u2013 24 September 2026",
                     datetime(2026, 9, 25, 6, 30, tzinfo=UTC)),
    "boj_whatsnew.xml": (NewsSource.BOJ_RSS, 3,
                         "Minutes of the Monetary Policy Meeting on July 30 and 31, 2026",
                         datetime(2026, 9, 27, 23, 50, tzinfo=UTC)),
    "snb_pressrel.xml": (NewsSource.SNB_RSS, 3,
                         "2026-09-24 - Monetary policy assessment of 24 September 2026",
                         datetime(2026, 9, 24, 7, 30, tzinfo=UTC)),
    "rba_media.xml": (NewsSource.RBA_RSS, 1,
                      "Assessment of ASX Clearing and Settlement Facilities - September 2026",
                      datetime(2026, 9, 23, 1, 30, tzinfo=UTC)),
    "rba_speeches.xml": (NewsSource.RBA_RSS, 1,
                         "Speech: A Wage-price Spiral: What are the Chances?",
                         datetime(2026, 9, 22, 7, 30, tzinfo=UTC)),
}


def _item(url: str, title: str = "t", *, source: NewsSource = NewsSource.FED_RSS,
          vendor: datetime | None = None) -> NewItem:
    return NewItem(source=source, feed_key="k", url=url, title=title, summary=None,
                   source_item_id=None, vendor_published_at=vendor, raw={"u": url})


@pytest.fixture
def store(tmp_path):
    s = HeadlineStore(tmp_path / "news" / "headlines.sqlite")
    yield s
    s.close()


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize("name", sorted(FEED_CASES))
def test_each_recorded_central_bank_feed_parses(name):
    source, n, title, vendor = FEED_CASES[name]
    items, rejected = parse_feed((FIXTURES / "news" / name).read_bytes(), source=source,
                                 feed_key=name)
    assert rejected == 0
    assert len(items) == n
    first = items[0]
    assert first.title == title
    assert first.vendor_published_at == vendor
    assert first.url.startswith("http")
    assert first.source is source
    assert first.raw["item_xml"].startswith("<")


def test_rss1_rdf_items_carry_rdf_about_as_item_id():
    items, _ = parse_feed((FIXTURES / "news" / "rba_media.xml").read_bytes(),
                          source=NewsSource.RBA_RSS, feed_key="rba_media")
    assert items[0].source_item_id == "https://www.rba.gov.au/media-releases/2026/mr-26-26.html"
    assert items[0].raw["format"] == "rss1.0"


def test_atom_parses_and_rejects_untitled_entries_visibly():
    items, rejected = parse_feed((FIXTURES / "news" / "constructed_atom.xml").read_bytes(),
                                 source=NewsSource.OTHER, feed_key="atom")
    assert rejected == 1  # the entry with no title is counted, not silently dropped
    assert [i.title for i in items] == ["Labour market overview: September 2026",
                                        "Entry with no link but an id"]
    assert items[0].url == "https://example.org/releases/lmo-2026-09"
    assert items[1].url == "" and items[1].source_item_id == "urn:example:no-link"
    assert items[1].vendor_published_at == datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


def test_feed_declaring_entities_is_refused():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]>'
            b'<rss><channel><item><title>&b;</title><link>http://x</link></item></channel></rss>')
    with pytest.raises(FeedFormatError, match="entities"):
        parse_feed(bomb, source=NewsSource.OTHER, feed_key="bomb")


@pytest.mark.parametrize("body", [b"<html><body>Access Denied</body></html>", b"not xml", b"{}"])
def test_non_feed_bodies_raise(body):
    with pytest.raises(FeedFormatError):
        parse_feed(body, source=NewsSource.OTHER, feed_key="x")


def test_zone_less_vendor_date_is_null_not_guessed():
    body = (b"<rss><channel><item><title>x</title><link>http://a/1</link>"
            b"<pubDate>2026-09-28T10:00:00</pubDate></item></channel></rss>")
    items, _ = parse_feed(body, source=NewsSource.OTHER, feed_key="x")
    assert items[0].vendor_published_at is None


def test_finnhub_and_marketaux_parse_documented_shapes():
    fh, rej = parse_finnhub((FIXTURES / "news" / "constructed_finnhub_forex.json").read_text(),
                            feed_key="finnhub_forex")
    assert rej == 1 and len(fh) == 2
    assert fh[0].source_item_id == "7301001"
    assert fh[0].vendor_published_at == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    mx, rej = parse_marketaux((FIXTURES / "news" / "constructed_marketaux.json").read_text(),
                              feed_key="marketaux_all")
    assert rej == 0 and [i.title for i in mx] == ["Euro area inflation data due", "Dollar index flat"]
    assert mx[1].summary == "Dollar flat."


def test_finnhub_key_travels_in_a_header_never_the_url():
    client = RecordedHttpClient().add_file("https://finnhub.io/api/v1/news",
                                           "news/constructed_finnhub_forex.json")
    items, _ = FinnhubNewsClient("SECRET-FH", client, category="forex").fetch()
    call = client.calls[0]
    assert call["headers"]["X-Finnhub-Token"] == "SECRET-FH"
    assert "SECRET-FH" not in call["url"] and "SECRET-FH" not in str(call["params"])
    assert all("SECRET-FH" not in str(i.raw) for i in items)


def test_marketaux_token_is_scrubbed_from_failure_messages(store):
    client = RecordedHttpClient()
    client.fail["api.marketaux.com/v1/news/all"] = RuntimeError(
        "Client error for url https://api.marketaux.com/v1/news/all?api_token=SECRET-MX")
    recorder = NewsRecorder(store, [MarketauxNewsClient("SECRET-MX", client)])
    result = recorder.poll_once(T0)
    err = result.errors["marketaux_all"]
    assert "SECRET-MX" not in err and "***" in err
    polls = store.status()["polls"]
    assert "SECRET-MX" not in str(polls)


def test_vendors_are_off_without_keys_and_say_so():
    clients, off = vendor_clients_from_env(object(), env={})
    assert clients == []
    assert off == {"finnhub": f"{ENV_FINNHUB_API_KEY} not set",
                   "marketaux": f"{ENV_MARKETAUX_API_KEY} not set"}
    clients, off = vendor_clients_from_env(
        object(), env={ENV_FINNHUB_API_KEY: "k1", ENV_MARKETAUX_API_KEY: "k2"})
    assert sorted(c.key for c in clients) == ["finnhub_forex", "finnhub_general", "marketaux_all"]
    assert off == {}


def test_official_feed_registry_is_complete_and_https():
    sources = {f.source for f in OFFICIAL_FEEDS}
    assert sources == {NewsSource.FED_RSS, NewsSource.ECB_RSS, NewsSource.BOE_RSS,
                       NewsSource.BOJ_RSS, NewsSource.SNB_RSS, NewsSource.RBA_RSS}
    assert len({f.key for f in OFFICIAL_FEEDS}) == len(OFFICIAL_FEEDS)
    for f in OFFICIAL_FEEDS:
        assert f.url.startswith("https://") and f.discovered_from.startswith("https://")
        assert f.retrieved_at.endswith("Z")


# -------------------------------------------------------------- append-only


def test_append_only_triggers_refuse_update_delete_and_replace(store):
    store.record([_item("https://a/1")], T0)
    store.register_feed(feed_key="f", source=NewsSource.FED_RSS, url="https://a", fmt="rss2.0",
                        discovered_from="https://a/index", retrieved_at="2026-09-28T00:00:00Z")
    store.log_poll(T0, T0, {"x": 1})
    conn = sqlite3.connect(store.path)
    statements = [
        "UPDATE headline SET title = 'changed'",
        "UPDATE headline SET observed_at = '2000-01-01T00:00:00.000000Z'",
        "DELETE FROM headline",
        "INSERT OR REPLACE INTO headline (id, source, url, url_hash, title, observed_at, raw_json, "
        "content_hash, feed_key) SELECT id, source, url, url_hash, 'x', observed_at, raw_json, "
        "content_hash, feed_key FROM headline",
        "UPDATE feed_source SET url = 'https://evil'",
        "DELETE FROM feed_source",
        "UPDATE poll_log SET outcomes_json = '{}'",
        "DELETE FROM poll_log",
        "DELETE FROM store_meta",
    ]
    for sql in statements:
        with pytest.raises(sqlite3.DatabaseError, match=APPEND_ONLY_MESSAGE):
            conn.execute(sql)
    conn.close()
    assert [h.title for h in store.query(T0, T0 - timedelta(days=1))] == ["t"]


def test_schema_refuses_unknown_source_and_missing_observed_at(store):
    conn = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO headline (source, url, url_hash, title, observed_at, raw_json, "
                     "content_hash, feed_key) VALUES ('bloomberg', 'u', 'h', 't', "
                     "'2026-09-28T12:00:00.000000Z', '{}', 'c', 'k')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO headline (source, url, url_hash, title, raw_json, content_hash, "
                     "feed_key) VALUES ('fed_rss', 'u', 'h', 't', '{}', 'c', 'k')")
    conn.close()


def test_register_feed_is_idempotent_and_refuses_redefinition(store):
    kw = dict(feed_key="f", source=NewsSource.ECB_RSS, url="https://e/rss", fmt="rss2.0",
              discovered_from="https://e", retrieved_at="2026-09-28T00:00:00Z")
    assert store.register_feed(**kw) is True
    assert store.register_feed(**kw) is False
    with pytest.raises(NewsStoreError, match="new feed key"):
        store.register_feed(**{**kw, "url": "https://e/other"})


# --------------------------------------------------------- dedupe + stamping


def test_observed_at_is_our_clock_not_the_vendor_timestamp(store):
    vendor = datetime(2020, 1, 1, tzinfo=UTC)  # a feed back-dating an item by years
    store.record([_item("https://a/1", vendor=vendor)], T0)
    (h,) = store.query(T0, T0 - timedelta(days=1))
    assert h.observed_at == T0 and h.available_at == T0
    assert h.vendor_published_at == vendor
    # Asked as of a moment after the vendor stamp but before we saw it: absent.
    assert store.query(T0 - timedelta(seconds=1), vendor) == ()


def test_naive_observed_at_is_refused(store):
    with pytest.raises(NewsStoreError, match="naive"):
        store.record([_item("https://a/1")], datetime(2026, 9, 28, 12))


def test_dedupe_across_polls_keeps_first_seen_and_logs_revisions(store):
    c1 = store.record([_item("https://a/1", "first"), _item("https://a/2")], T0)
    assert c1 == {"new": 2, "duplicate": 0, "revised": 0, "revision_duplicate": 0}
    later = T0 + timedelta(minutes=5)
    c2 = store.record([_item("HTTPS://A/1#frag", "first"), _item("https://a/1", "retitled"),
                       _item("https://a/3")], later)
    assert c2 == {"new": 1, "duplicate": 1, "revised": 1, "revision_duplicate": 0}
    c3 = store.record([_item("https://a/1", "retitled")], later + timedelta(minutes=5))
    assert c3["revision_duplicate"] == 1
    rows = store.query(later, T0)
    assert [(h.url, h.title, h.observed_at) for h in rows] == [
        ("https://a/1", "first", T0), ("https://a/2", "t", T0), ("https://a/3", "t", later)]
    assert store.status()["revisions"] == 1


def test_same_url_from_two_sources_is_two_headlines(store):
    store.record([_item("https://x/1", source=NewsSource.FED_RSS),
                  _item("https://x/1", source=NewsSource.FINNHUB)], T0)
    assert len(store.query(T0, T0)) == 2


# ---------------------------------------------------------- point in time


def test_query_never_returns_a_row_observed_after_as_of(store):
    instants = [T0 + timedelta(minutes=5 * i) for i in range(40)]
    for i, t in enumerate(instants):
        # vendor stamps deliberately scrambled, including ones in the future
        vendor = T0 + timedelta(days=(i % 3) - 1)
        store.record([_item(f"https://a/{i}", f"h{i}", vendor=vendor)], t)
    since = T0 - timedelta(days=30)
    for k, as_of in enumerate(instants):
        for probe in (as_of - timedelta(microseconds=1), as_of, as_of + timedelta(seconds=1)):
            rows = store.query(probe, since)
            assert all(h.observed_at <= probe for h in rows)
            expected = sum(1 for t in instants if t <= probe)
            assert len(rows) == expected, (k, probe)
    assert store.query(instants[10], instants[10]) == store.query(instants[10], instants[10])
    assert len(store.query(instants[10], instants[10])) == 1


def test_query_requires_as_of_and_ordered_window(store):
    with pytest.raises(NewsStoreError):
        store.query(None, T0)  # type: ignore[arg-type]
    with pytest.raises(NewsStoreError):
        store.query(T0, T0 + timedelta(seconds=1))


def test_query_filters_by_source_hint_and_limit(store):
    store.record([_item("https://f/1", source=NewsSource.FED_RSS),
                  _item("https://e/1", source=NewsSource.ECB_RSS),
                  _item("https://v/1", source=NewsSource.FINNHUB)], T0)
    store.record([_item("https://f/2", source=NewsSource.FED_RSS)], T0 + timedelta(minutes=1))
    end, since = T0 + timedelta(hours=1), T0 - timedelta(hours=1)
    assert {h.source for h in store.query(end, since, sources=["ecb_rss"])} == {NewsSource.ECB_RSS}
    hinted = store.query(end, since, currencies_hint=["USD"])
    assert {h.source for h in hinted} == {NewsSource.FED_RSS, NewsSource.FINNHUB}
    assert store.query(end, since, sources=[NewsSource.ECB_RSS], currencies_hint=["USD"]) == ()
    newest = store.query(end, since, limit=2)
    assert [h.url for h in newest] == ["https://v/1", "https://f/2"]


# ---------------------------------------------------------------- recorder


def _official_client() -> RecordedHttpClient:
    client = RecordedHttpClient()
    by_key = {
        "fed_press_monetary": "fed_press_monetary.xml", "fed_press_all": "fed_press_monetary.xml",
        "fed_speeches": "fed_speeches.xml", "ecb_press": "ecb_press.xml",
        "ecb_blog": "ecb_press.xml", "boe_news": "boe_news.xml", "boe_speeches": "boe_news.xml",
        "boj_whatsnew": "boj_whatsnew.xml", "snb_pressrel": "snb_pressrel.xml",
        "snb_mopo": "snb_pressrel.xml", "rba_media": "rba_media.xml",
        "rba_speeches": "rba_speeches.xml",
    }
    for spec in OFFICIAL_FEEDS:
        client.add_file(spec.url, f"news/{by_key[spec.key]}")
    return client


def test_poll_once_fetches_every_feed_dedupes_and_logs(store):
    recorder = NewsRecorder.official(store, _official_client(), disabled={"finnhub": "no key"})
    r1 = recorder.poll_once(T0)
    assert r1.errors == {}
    assert r1.total("fetched") == 32
    # fed_press_all/ecb_blog/boe_speeches/snb_mopo replay a sibling's items: same source+URL
    assert r1.total("new") == 3 + 3 + 3 + 3 + 3 + 1 + 1 + 3 == 20
    assert r1.total("duplicate") == 12
    assert r1.per_feed["finnhub"] == {"disabled": "no key"}
    r2 = recorder.poll_once(T0 + timedelta(minutes=5))
    assert r2.total("new") == 0 and r2.total("duplicate") == 32
    st = store.status()
    assert st["rows"] == 20 and len(st["polls"]) == 2 and len(st["feeds"]) == len(OFFICIAL_FEEDS)
    assert {h.observed_at for h in store.query(T0 + timedelta(hours=1), T0)} == {T0}


def test_recorder_survives_restart_without_duplicates(tmp_path):
    path = tmp_path / "h.sqlite"
    with HeadlineStore(path) as s1:
        NewsRecorder.official(s1, _official_client()).poll_once(T0)
    with HeadlineStore(path) as s2:  # a new process on the same file
        r = NewsRecorder.official(s2, _official_client()).poll_once(T0 + timedelta(minutes=5))
        assert r.total("new") == 0
        assert s2.status()["rows"] == 20
        assert len(s2.status()["polls"]) == 2


def test_one_failing_feed_is_recorded_and_the_rest_carry_on(store):
    client = _official_client()
    client.fail["www.boj.or.jp/en/rss/whatsnew.xml"] = TimeoutError("read timed out")
    client.responses["www.snb.ch/public/rss/en/pressrel"] = []
    client.add("https://www.snb.ch/public/rss/en/pressrel", "<html>Maintenance</html>", 503)
    r = NewsRecorder.official(store, client).poll_once(T0)
    assert set(r.errors) == {"boj_whatsnew", "snb_pressrel"}
    assert "HTTP 503" in r.errors["snb_pressrel"]
    assert r.total("new") == 20 - 3  # BoJ's three missing; SNB's still arrive via snb_mopo
    last = store.status()["polls"][-1][2]
    assert "boj_whatsnew" in last["errors"]


def test_min_interval_skips_a_rate_limited_source(store):
    client = RecordedHttpClient().add_file("https://api.marketaux.com/v1/news/all",
                                           "news/constructed_marketaux.json")
    mx = MarketauxNewsClient("tok", client, min_interval_s=900)
    recorder = NewsRecorder(store, [mx])
    assert recorder.poll_once(T0).total("new") == 2
    skipped = recorder.poll_once(T0 + timedelta(seconds=300))
    assert "skipped" in skipped.per_feed["marketaux_all"]
    assert len(client.calls) == 1
    recorder.poll_once(T0 + timedelta(seconds=900))
    assert len(client.calls) == 2


def test_run_loop_keeps_cadence_without_catch_up_bursts(store):
    now = {"t": T0}
    polled: list[datetime] = []

    class Reader:
        key, source, min_interval_s = "r", NewsSource.OTHER, 0.0

        def fetch(self):
            polled.append(now["t"])
            if len(polled) == 2:
                now["t"] += timedelta(seconds=700)  # a slow poll overruns two slots
            return [], 0

    def sleep(s: float) -> None:
        now["t"] += timedelta(seconds=s)

    n = run_loop(NewsRecorder(store, [Reader()]), interval_s=300, clock=lambda: now["t"],
                 sleep=sleep, max_polls=4)
    assert n == 4
    offsets = [(p - T0).total_seconds() for p in polled]
    assert offsets == [0, 300, 1000, 1200]  # one immediate catch-up poll, then back on the grid


def test_gaps_in_polls_reports_holes_and_a_stopped_recorder():
    starts = [T0, T0 + timedelta(minutes=5), T0 + timedelta(minutes=40)]
    gaps = gaps_in_polls(starts, threshold=timedelta(minutes=15))
    assert [g["seconds"] for g in gaps] == [2100.0]
    stopped = gaps_in_polls(starts, threshold=timedelta(minutes=15), until=T0 + timedelta(hours=2))
    assert len(stopped) == 2


# --------------------------------------------------------------------- CLI


def test_cli_record_once_then_status(tmp_path, monkeypatch, capsys):
    import fiboki.cli as cli

    monkeypatch.setattr(cli, "_http_client", _ClosableClient)
    monkeypatch.delenv(ENV_FINNHUB_API_KEY, raising=False)
    monkeypatch.delenv(ENV_MARKETAUX_API_KEY, raising=False)
    state = tmp_path / "state"
    assert cli_main(["news", "status", "--state-dir", str(state)]) == 1  # no store yet
    assert cli_main(["news", "record", "--once", "--state-dir", str(state), "--json"]) == 0
    assert cli_main(["news", "record", "--once", "--state-dir", str(state)]) == 0
    capsys.readouterr()
    assert cli_main(["news", "status", "--state-dir", str(state), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"rows": 20' in out and '"polls": 2' in out
    assert cli_main(["news", "record", "--state-dir", str(state)]) == 2  # neither --once nor --loop
    assert cli_main(["news", "record", "--loop", "--interval", "5", "--state-dir", str(state)]) == 2


class _ClosableClient(RecordedHttpClient):
    def __init__(self) -> None:
        super().__init__()
        self.responses = _official_client().responses

    def close(self) -> None:
        pass
