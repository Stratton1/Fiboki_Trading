"""The official scheduled-events fixture, its loader, and the blackout it enables.

``scheduled_events_official.json`` was built on 2026-09-28 from the publishers'
own pages (Federal Reserve, ECB, Bank of England, Bank of Japan, US BLS, UK
ONS). These tests do not re-fetch anything; they check that the committed file
is internally consistent, that every UTC time re-derives from the publisher's
local clock, that the counts are plausible for the schedule each publisher
runs, and that a blackout query now actually fires around a known release.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fiboki.marketstate.calendar import (
    OFFICIAL_EVENTS_FIXTURE,
    USER_ACTION_NOTE,
    CalendarError,
    EconomicEvent,
    ImpactLevel,
    InMemoryEconomicCalendar,
    load_events_csv,
    load_events_json,
    load_official_calendar,
    load_recurring_event_types,
    official_calendar_manifest,
)

RAW = json.loads(OFFICIAL_EVENTS_FIXTURE.read_text(encoding="utf-8"))
EVENTS = RAW["events"]
UTC = ZoneInfo("UTC")

OFFICIAL_HOSTS = {
    "www.federalreserve.gov",
    "www.ecb.europa.eu",
    "www.bankofengland.co.uk",
    "www.boj.or.jp",
    "www.bls.gov",
    "www.ons.gov.uk",
}
FORBIDDEN_FRAGMENTS = ("forexfactory", "investing.com", "tradingeconomics", "fxstreet", "dailyfx")

#: source -> (currency, local clock time, local zone)
EXPECTED_CLOCK = {
    "fed_fomc": ("USD", "14:00", "America/New_York"),
    "ecb_governing_council": ("EUR", "14:15", "Europe/Berlin"),
    "boe_mpc": ("GBP", "12:00", "Europe/London"),
    "boj_mpm": ("JPY", "09:00", "Asia/Tokyo"),
    "bls_empsit": ("USD", "08:30", "America/New_York"),
    "bls_cpi": ("USD", "08:30", "America/New_York"),
    "ons_cpi": ("GBP", "07:00", "Europe/London"),
    "ons_gdp_monthly": ("GBP", "07:00", "Europe/London"),
    "ons_labour_market": ("GBP", "07:00", "Europe/London"),
}


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


@pytest.fixture(scope="module")
def official() -> InMemoryEconomicCalendar:
    return load_official_calendar()


# =====================================================================
# Schema
# =====================================================================


def test_every_event_is_utc_aware_and_parses() -> None:
    assert len(EVENTS) > 300
    for raw in EVENTS:
        parsed = pd.Timestamp(raw["event_time"])
        assert parsed.tzinfo is not None, raw
        assert parsed.utcoffset() == pd.Timedelta(0), raw
        assert raw["event_time"].endswith("+00:00"), raw
        if "window_end" in raw:
            end = pd.Timestamp(raw["window_end"])
            assert end.utcoffset() == pd.Timedelta(0)
            assert end > parsed


def test_events_are_sorted_and_unique() -> None:
    keys = [(pd.Timestamp(e["event_time"]), e["name"]) for e in EVENTS]
    assert keys == sorted(keys)
    ids = [(e["currency"], e["name"], e["event_time"]) for e in EVENTS]
    assert len(ids) == len(set(ids))


def test_loader_keeps_every_event(official) -> None:
    """The loader deduplicates on event_id; nothing may collapse."""
    assert len(official) == len(EVENTS) == len(load_events_json(OFFICIAL_EVENTS_FIXTURE))


def test_every_event_carries_provenance() -> None:
    for raw in EVENTS:
        host = urlparse(raw["source_url"]).netloc
        assert raw["source_url"].startswith("https://"), raw
        assert host in OFFICIAL_HOSTS, raw["source_url"]
        assert not any(bad in raw["source_url"].lower() for bad in FORBIDDEN_FRAGMENTS)
        assert raw["retrieved_at"] == "2026-09-28"
        assert raw["impact"] == "high"
        assert raw["source"] in EXPECTED_CLOCK
        assert raw["currency"] == EXPECTED_CLOCK[raw["source"]][0]
        # Scheduled times only: no realised figure may ride along.
        for leak in ("actual", "forecast", "previous"):
            assert raw.get(leak) is None


def test_event_types_are_in_the_taxonomy_or_declared_extensions() -> None:
    known = {t.key for t in load_recurring_event_types()} | set(RAW["taxonomy_extensions"])
    for raw in EVENTS:
        assert raw["event_type"] in known, raw["event_type"]


def test_every_utc_time_re_derives_from_the_publisher_clock() -> None:
    """The DST class of bug, checked on all events rather than two."""
    for raw in EVENTS:
        _, clock, zone = EXPECTED_CLOCK[raw["source"]]
        assert raw["local_time"] == clock, raw
        assert raw["local_tz"] == zone, raw
        h, m = map(int, clock.split(":"))
        d = datetime.fromisoformat(raw["local_date"])
        local = datetime(d.year, d.month, d.day, h, m, tzinfo=ZoneInfo(zone))
        assert pd.Timestamp(local.astimezone(UTC)) == pd.Timestamp(raw["event_time"]), raw


def test_boj_events_are_windows_not_instants() -> None:
    boj = [e for e in EVENTS if e["source"] == "boj_mpm"]
    assert boj
    for raw in boj:
        assert raw["time_known"] is False
        start, end = pd.Timestamp(raw["event_time"]), pd.Timestamp(raw["window_end"])
        assert (start.hour, start.minute) == (0, 0)  # 09:00 JST
        assert (end.hour, end.minute) == (5, 0)  # 14:00 JST
    others = [e for e in EVENTS if e["source"] != "boj_mpm"]
    assert all(e["time_known"] is True and "window_end" not in e for e in others)


# =====================================================================
# Plausible counts per publisher and year
# =====================================================================


def _per_year(source: str) -> Counter:
    return Counter(e["event_time"][:4] for e in EVENTS if e["source"] == source)


def test_central_banks_meet_eight_times_a_year() -> None:
    assert _per_year("fed_fomc") == {"2024": 8, "2025": 8, "2026": 8, "2027": 8}
    assert _per_year("boe_mpc") == {"2024": 8, "2025": 8, "2026": 8, "2027": 8}
    assert _per_year("boj_mpm") == {"2024": 8, "2025": 8, "2026": 8, "2027": 8}
    ecb = _per_year("ecb_governing_council")
    assert ecb == {"2024": 8, "2025": 8, "2026": 8, "2027": 8, "2028": 8}


def test_the_2025_notation_vote_is_not_a_rate_decision() -> None:
    fomc_days = {e["local_date"] for e in EVENTS if e["source"] == "fed_fomc"}
    assert "2025-08-22" not in fomc_days


def test_bls_counts_include_the_2025_shutdown_gap() -> None:
    """October 2025 NFP and CPI were never published; nothing may be invented for them."""
    assert _per_year("bls_empsit") == {"2024": 12, "2025": 11, "2026": 12}
    assert _per_year("bls_cpi") == {"2024": 12, "2025": 11, "2026": 12}
    nfp = {e["local_date"] for e in EVENTS if e["source"] == "bls_empsit"}
    assert "2025-11-20" in nfp  # September 2025 report, delayed
    assert not any(d.startswith("2025-10") for d in nfp)


def test_ons_monthly_series_are_monthly() -> None:
    for src in ("ons_cpi", "ons_gdp_monthly", "ons_labour_market"):
        years = _per_year(src)
        for y in ("2024", "2025", "2026"):
            assert years[y] == 12, (src, y, years)


def test_manifest_counts_match_the_events() -> None:
    manifest = official_calendar_manifest()
    by_source = Counter(e["source"] for e in EVENTS)
    assert set(manifest["sources_used"]) == set(by_source)
    for key, meta in manifest["sources_used"].items():
        assert meta["n_events"] == by_source[key], key
        assert meta["urls"], key
    assert manifest["sources_skipped"]
    assert "events" not in manifest


# =====================================================================
# DST spot checks
# =====================================================================


@pytest.mark.parametrize(
    ("source", "local_date", "expected_utc"),
    [
        # US on EDT in March 2024 (from 10 March), EST again in November.
        ("fed_fomc", "2024-03-20", "2024-03-20T18:00:00+00:00"),
        ("fed_fomc", "2024-11-07", "2024-11-07T19:00:00+00:00"),
        # Euro area still on CET on 7 March 2024, CEST by June.
        ("ecb_governing_council", "2024-03-07", "2024-03-07T13:15:00+00:00"),
        ("ecb_governing_council", "2024-06-06", "2024-06-06T12:15:00+00:00"),
        # NFP on 8 March 2024 is still EST: the transatlantic gap week.
        ("bls_empsit", "2024-03-08", "2024-03-08T13:30:00+00:00"),
        ("bls_empsit", "2024-06-07", "2024-06-07T12:30:00+00:00"),
        ("boe_mpc", "2024-06-20", "2024-06-20T11:00:00+00:00"),
        ("boe_mpc", "2024-11-07", "2024-11-07T12:00:00+00:00"),
    ],
)
def test_dst_conversion(source: str, local_date: str, expected_utc: str) -> None:
    [hit] = [e for e in EVENTS if e["source"] == source and e["local_date"] == local_date]
    assert hit["event_time"] == expected_utc


# =====================================================================
# Blackout behaviour on the real calendar
# =====================================================================

NFP_2025_03 = ts("2025-03-07T13:30:00Z")


def test_blackout_fires_around_a_known_2025_nfp(official) -> None:
    for sym in ("EURUSD", "XAUUSD", "USDJPY"):
        assert official.in_blackout(sym, NFP_2025_03)
        assert official.in_blackout(sym, NFP_2025_03 + pd.Timedelta(minutes=29))
        assert official.in_blackout(sym, NFP_2025_03 - pd.Timedelta(minutes=30))
    [event] = official.events_near("EURUSD", NFP_2025_03)
    assert event.recurring_key == "us_nfp"
    assert event.source_url.startswith("https://www.bls.gov/")


def test_blackout_is_clear_a_day_later(official) -> None:
    assert not official.in_blackout("EURUSD", NFP_2025_03 + pd.Timedelta(days=1))
    assert not official.in_blackout("XAUUSD", NFP_2025_03 + pd.Timedelta(days=1))


def test_blackout_respects_currency_exposure(official) -> None:
    ecb = ts("2025-03-06T13:15:00Z")
    assert official.in_blackout("EURUSD", ecb)
    assert not official.in_blackout("USDJPY", ecb)


def test_boj_window_blacks_out_the_whole_morning(official) -> None:
    decision_day = ts("2025-01-24T00:00:00Z")
    assert official.in_blackout("USDJPY", decision_day + pd.Timedelta(hours=3, minutes=10))
    assert official.in_blackout("USDJPY", decision_day + pd.Timedelta(hours=5, minutes=20))
    assert official.in_blackout("USDJPY", decision_day - pd.Timedelta(minutes=30))
    assert not official.in_blackout("USDJPY", decision_day + pd.Timedelta(hours=5, minutes=31))
    assert not official.in_blackout("EURUSD", decision_day + pd.Timedelta(hours=3))
    index = pd.date_range("2025-01-23 20:00", "2025-01-24 08:00", freq="1h", tz="UTC")
    mask = official.blackout_mask("USDJPY", index)
    assert list(index[mask.to_numpy()]) == list(
        pd.date_range("2025-01-24 00:00", "2025-01-24 05:00", freq="1h", tz="UTC")
    )
    windows = official.blackout_windows(
        "USDJPY", ts("2025-01-24T00:00:00Z"), ts("2025-01-24T06:00:00Z")
    )
    assert len(windows) == 1
    assert windows[0].end == ts("2025-01-24T05:30:00Z")
    assert "time not fixed" in windows[0].reason


# =====================================================================
# Coverage and the guard
# =====================================================================


def test_declared_span_governs_coverage(official) -> None:
    cov = official.coverage()
    assert cov.declared_start == ts("2024-01-01T00:00:00Z")
    assert cov.declared_end == ts(RAW["coverage"]["declared_end"])
    # The first event is 5 January, but the calendar was enumerated from 1 January.
    assert cov.first_event > cov.declared_start
    assert cov.covers(ts("2024-01-01T00:00:00Z"), ts("2026-09-01T00:00:00Z"))
    assert not cov.covers(ts("2023-12-31T00:00:00Z"), ts("2024-06-01T00:00:00Z"))
    assert not cov.covers(ts("2024-01-01T00:00:00Z"), ts("2027-06-01T00:00:00Z"))
    assert set(cov.currencies) == {"USD", "EUR", "GBP", "JPY"}
    assert sum(cov.by_source.values()) == cov.n_events


def test_assert_populated_raises_on_an_empty_calendar() -> None:
    with pytest.raises(CalendarError, match="empty") as info:
        InMemoryEconomicCalendar.empty().assert_populated(
            start=ts("2024-01-01T00:00:00Z"), end=ts("2024-02-01T00:00:00Z")
        )
    assert USER_ACTION_NOTE in str(info.value)


def test_assert_populated_on_the_official_calendar(official) -> None:
    official.assert_populated(
        start=ts("2024-01-01T00:00:00Z"), end=ts("2026-09-01T00:00:00Z"),
        currencies=["USD", "EUR", "GBP", "JPY"],
    )
    with pytest.raises(CalendarError, match="does not span") as info:
        official.assert_populated(start=ts("2015-01-01T00:00:00Z"), end=ts("2025-01-01T00:00:00Z"))
    assert USER_ACTION_NOTE in str(info.value)
    with pytest.raises(CalendarError, match="no events for \\['AUD'\\]"):
        official.assert_populated(currencies=["AUD", "USD"])


def test_with_events_keeps_the_declared_span(official) -> None:
    extra = EconomicEvent(ts("2025-05-05T01:00:00Z"), "AUD", "RBA", ImpactLevel.HIGH)
    bigger = official.with_events([extra])
    assert bigger.declared_span() == official.declared_span()
    assert len(bigger) == len(official) + 1


# =====================================================================
# The schema extension stays backwards compatible
# =====================================================================


def test_new_fields_round_trip_through_json(tmp_path) -> None:
    ev = EconomicEvent(
        ts("2025-01-24T00:00:00Z"), "JPY", "BoJ", ImpactLevel.HIGH,
        recurring_key="boj_rate_decision", source="boj_mpm",
        source_url="https://www.boj.or.jp/en/mopo/mpmsche_minu/past.htm",
        retrieved_at="2026-09-28", time_known=False,
        window_end=ts("2025-01-24T05:00:00Z"), tags=("central_bank_rate",),
    )
    path = tmp_path / "e.json"
    path.write_text(InMemoryEconomicCalendar([ev]).to_json(), encoding="utf-8")
    [back] = load_events_json(path)
    assert back == ev
    assert back.span_end == ts("2025-01-24T05:00:00Z")


def test_old_shape_still_loads_with_defaults(tmp_path) -> None:
    path = tmp_path / "old.csv"
    path.write_text(
        "event_time,currency,name,impact\n2019-07-31T18:00:00+00:00,USD,FOMC,high\n",
        encoding="utf-8",
    )
    [ev] = load_events_csv(path)
    assert ev.window_end is None and ev.time_known is True
    assert ev.source_url == "" and ev.tags == ()
    assert ev.span_end == ev.event_time


def test_event_type_is_an_alias_for_recurring_key() -> None:
    ev = EconomicEvent.from_dict(
        {"event_time": "2025-03-07T13:30:00+00:00", "currency": "USD",
         "name": "NFP", "impact": "high", "event_type": "us_nfp"}
    )
    assert ev.recurring_key == "us_nfp"


def test_a_bad_window_is_refused() -> None:
    with pytest.raises(CalendarError, match="before event_time"):
        EconomicEvent(ts("2025-01-24T05:00:00Z"), "JPY", "BoJ", ImpactLevel.HIGH,
                      window_end=ts("2025-01-24T00:00:00Z"))
    with pytest.raises(CalendarError, match="naive"):
        EconomicEvent.from_dict(
            {"event_time": "2025-01-24T00:00:00+00:00", "currency": "JPY", "name": "BoJ",
             "impact": "high", "window_end": "2025-01-24 05:00:00"}
        )


def test_a_windowed_actual_is_hidden_until_the_window_closes() -> None:
    ev = EconomicEvent(ts("2025-01-24T00:00:00Z"), "JPY", "BoJ", ImpactLevel.HIGH,
                       actual=0.5, window_end=ts("2025-01-24T05:00:00Z"))
    assert ev.actual_as_of(ts("2025-01-24T03:00:00Z")) is None
    assert ev.actual_as_of(ts("2025-01-24T05:00:00Z")) == 0.5
