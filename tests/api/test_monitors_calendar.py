"""The economic calendar panel (GET /api/markets/calendar): newest official version, filters, statuses."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

import fiboki.api.routers.monitors as monitors
from fiboki.marketstate.calendar import (
    PINNED_RESEARCH_CALENDAR_VERSION,
    latest_official_calendar_version,
)
from tests.api.conftest import OPERATOR_PW, login

#: Inside the 1.1.0 coverage, the Monday before the 2026-11-06 US payrolls.
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def cal(client, monkeypatch):
    monkeypatch.setattr(monitors, "_now", lambda at=None: NOW)
    login(client, "tom", OPERATOR_PW)
    return client


def test_reads_the_newest_version_not_the_research_pin(cal):
    body = cal.get("/api/markets/calendar?days=7").json()
    view = body["data"]
    assert view["calendar_version"] == latest_official_calendar_version()
    assert view["research_pinned_version"] == PINNED_RESEARCH_CALENDAR_VERSION
    assert view["calendar_version"] != PINNED_RESEARCH_CALENDAR_VERSION  # 1.1.0 is listed
    assert view["events"], "a week inside the declared span has scheduled releases"
    times = [e["event_time"] for e in view["events"]]
    assert times == sorted(times)
    assert all(e["status"] == "upcoming" for e in view["events"])
    assert view["next_event_id"] == view["events"][0]["event_id"]


def test_impact_currency_and_instrument_filters(cal):
    high = cal.get("/api/markets/calendar?days=31&impact=high").json()["data"]["events"]
    assert high and {e["impact"] for e in high} == {"high"}
    usd = cal.get("/api/markets/calendar?days=31&currency=USD").json()["data"]["events"]
    assert usd and {e["currency"] for e in usd} == {"USD"}
    eu = cal.get("/api/markets/calendar?days=31&instrument=EURUSD").json()["data"]
    assert {e["currency"] for e in eu["events"]} <= {"EUR", "USD"} and eu["currencies"] == ["EUR", "USD"]
    link = eu["events"][0]["chart_links"][0]
    from urllib.parse import parse_qs, urlsplit

    assert link["symbol"] == "EURUSD" and link["href"].startswith("/markets/EURUSD?event=")
    assert parse_qs(urlsplit(link["href"]).query)["event"] == [eu["events"][0]["event_id"]]


def test_uncovered_currency_and_bad_params_are_said(cal):
    body = cal.get("/api/markets/calendar?currency=AUD").json()
    assert body["data"]["events"] == []
    assert any(c["code"] == "currency_not_covered" for c in body["caveats"])
    assert cal.get("/api/markets/calendar?impact=extreme").status_code == 422
    assert cal.get("/api/markets/calendar?days=2").status_code == 422


def test_statuses_by_the_api_clock(cal, monkeypatch):
    first = cal.get("/api/markets/calendar?days=7&impact=high").json()["data"]["events"][0]
    at = datetime.fromisoformat(first["event_time"].replace("Z", "+00:00"))
    monkeypatch.setattr(monitors, "_now", lambda a=None: at)
    again = cal.get("/api/markets/calendar?days=7&impact=high&past_hours=1").json()["data"]["events"]
    assert next(e for e in again if e["event_id"] == first["event_id"])["status"] == "in_progress"


def test_beyond_the_declared_end_is_unknown(client, monkeypatch):
    monkeypatch.setattr(monitors, "_now", lambda at=None: datetime(2027, 12, 1, tzinfo=UTC))
    login(client, "tom", OPERATOR_PW)
    body = client.get("/api/markets/calendar?days=31").json()
    assert any(c["code"] == "beyond_declared_end" for c in body["caveats"])


def test_one_event_by_id(cal):
    first = cal.get("/api/markets/calendar?days=7").json()["data"]["events"][0]
    one = cal.get(f"/api/markets/calendar/events/{first['event_id']}?instrument=EURUSD").json()["data"]
    assert one["name"] == first["name"] and one["status"] == "upcoming"
    assert cal.get("/api/markets/calendar/events/nope").status_code == 404
