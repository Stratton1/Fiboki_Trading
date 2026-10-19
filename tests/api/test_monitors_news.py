"""The news monitor (GET /api/markets/news) over the append-only headline store."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fiboki.data.news.store import HeadlineStore, NewItem, NewsSource, default_store_path
from tests.api.conftest import OPERATOR_PW, login


def seed_news(state_dir: Path, now: datetime) -> None:
    with HeadlineStore(default_store_path(state_dir)) as store:
        def item(src: NewsSource, title: str, url: str) -> NewItem:
            return NewItem(source=src, feed_key=f"{src.value}:main", url=url, title=title, summary=None,
                           source_item_id=None, vendor_published_at=now - timedelta(hours=9), raw={})
        store.record([item(NewsSource.ECB_RSS, "ECB holds rates", "https://ecb.example/1")], now - timedelta(hours=2))
        store.record([item(NewsSource.FED_RSS, "FOMC statement", "https://fed.example/1")], now - timedelta(hours=1))
        store.record([item(NewsSource.BOJ_RSS, "BoJ minutes", "https://boj.example/1")], now - timedelta(minutes=30))
        store.record([item(NewsSource.FINNHUB, "Markets wrap", "https://vendor.example/1")], now - timedelta(minutes=10))
        store.record([item(NewsSource.ECB_RSS, "Old ECB speech", "https://ecb.example/0")], now - timedelta(days=3))
        store.log_poll(now - timedelta(minutes=2), now - timedelta(minutes=1), {"ok": True})


@pytest.fixture
def newsy(client, api_env):
    seed_news(Path(api_env["FIBOKI_STATE_DIR"]), datetime.now(tz=UTC))
    login(client, "tom", OPERATOR_PW)
    return client


def test_newest_first_by_our_clock_with_chart_links(newsy):
    body = newsy.get("/api/markets/news?hours=24").json()
    view = body["data"]
    titles = [h["title"] for h in view["headlines"]]
    assert titles == ["Markets wrap", "BoJ minutes", "FOMC statement", "ECB holds rates"]
    fomc = next(h for h in view["headlines"] if h["source"] == "fed_rss")
    assert fomc["currency"] == "USD" and fomc["chart_links"] and all(
        "USD" in link["symbol"] for link in fomc["chart_links"]
    )
    vendor = view["headlines"][0]
    assert vendor["currency"] is None and vendor["chart_links"] == []
    assert view["recorder_state"] == "live" and view["available_sources"] == ["boj_rss", "ecb_rss", "fed_rss", "finnhub"]


def test_instrument_filter_narrows_banks_and_says_vendors_pass(newsy):
    body = newsy.get("/api/markets/news?instrument=EURUSD").json()
    sources = [h["source"] for h in body["data"]["headlines"]]
    assert sources == ["finnhub", "fed_rss", "ecb_rss"]
    assert body["data"]["currencies"] == ["EUR", "USD"]
    assert all(link["symbol"] == "EURUSD" for h in body["data"]["headlines"] for link in h["chart_links"])
    assert any(c["code"] == "vendor_untagged" for c in body["caveats"])


def test_currency_and_source_filters(newsy):
    jpy = newsy.get("/api/markets/news?currency=JPY").json()["data"]["headlines"]
    assert [h["source"] for h in jpy] == ["finnhub", "boj_rss"]
    only = newsy.get("/api/markets/news?source=ecb_rss&hours=168").json()["data"]["headlines"]
    assert [h["title"] for h in only] == ["ECB holds rates", "Old ECB speech"]
    assert newsy.get("/api/markets/news?source=reuters").status_code == 422
    assert newsy.get("/api/markets/news?hours=5").status_code == 422
    assert newsy.get("/api/markets/news?instrument=NOPE").status_code == 422


def test_an_absent_store_is_absent_not_quiet(client):
    login(client, "tom", OPERATOR_PW)
    body = client.get("/api/markets/news").json()
    assert body["source"]["kind"] == "absent" and body["data"]["recorder_state"] == "never"
    assert body["data"]["headlines"] == []
