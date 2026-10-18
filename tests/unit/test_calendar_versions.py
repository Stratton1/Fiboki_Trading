"""Official calendar versions: immutability, lineage, pinning and the 1.1.0 extension.

1.1.0 (read from the publishers on 2026-10-18) extends 1.0.0 so paper forward,
which refuses to start with under 14 days of coverage ahead, keeps running past
2026-12-04. These tests do not fetch anything. They pin what the committed files
say, and the rules that stop a refresh from changing a stored study's inputs.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fiboki.marketstate import calendar as cal
from fiboki.marketstate.calendar import (
    FIXTURE_DIR,
    OFFICIAL_CALENDAR_INDEX,
    OFFICIAL_EVENTS_FIXTURE,
    PINNED_RESEARCH_CALENDAR_VERSION,
    CalendarError,
    calendar_events_sha256,
    latest_official_calendar_version,
    load_official_calendar,
    load_research_calendar,
    official_calendar_index,
    official_calendar_path,
)
from fiboki.validation.engine_evaluator import blackout_fingerprint

REPO = Path(__file__).resolve().parents[2]
INDEX = official_calendar_index()
V100 = INDEX["versions"]["1.0.0"]
V110 = INDEX["versions"]["1.1.0"]
RAW110 = json.loads((FIXTURE_DIR / V110["file"]).read_text(encoding="utf-8"))
RAW100 = json.loads(OFFICIAL_EVENTS_FIXTURE.read_text(encoding="utf-8"))
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
OFFICIAL_HOSTS = {"www.federalreserve.gov", "www.ecb.europa.eu", "www.bankofengland.co.uk",
                  "www.boj.or.jp", "www.bls.gov", "www.ons.gov.uk"}


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


# ---- immutability and the index --------------------------------------------------------------


def test_the_1_0_0_file_is_byte_identical_to_what_was_filed() -> None:
    """Pinned values: E-4 and K5/K6 recorded this calendar's events hash. Editing the 1.0.0
    file in place would change their fingerprints; add a new version instead."""
    assert hashlib.sha256(OFFICIAL_EVENTS_FIXTURE.read_bytes()).hexdigest() == (
        "1063b1c171ddc16316cc6b981a0ae64ef417b38f0abcf2841f8ec33c75b84fa4")
    assert V100["events_sha256"] == "d7a0cec51f3c824b737edb1ddf39d2dbc077555ff59a6e45742c09598a2189ea"
    assert V100["n_events"] == 339


def test_every_listed_file_matches_its_recorded_hashes() -> None:
    for v, entry in INDEX["versions"].items():
        path = FIXTURE_DIR / entry["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["file_sha256"], v
        c = load_official_calendar(version=v)
        assert calendar_events_sha256(c) == entry["events_sha256"], v
        assert len(c) == entry["n_events"], v
        assert str(c.coverage().declared_end) == str(ts(entry["declared_end"])), v


def test_events_hash_is_the_fingerprint_studies_record() -> None:
    c = load_official_calendar(version="1.0.0")
    assert blackout_fingerprint(c)["events_sha256"] == calendar_events_sha256(c) == V100["events_sha256"]


def test_a_tampered_listed_version_is_refused(tmp_path, monkeypatch) -> None:
    doctored = json.loads((FIXTURE_DIR / V110["file"]).read_text(encoding="utf-8"))
    doctored["events"][0]["event_time"] = "2024-01-05T13:31:00+00:00"
    (tmp_path / V110["file"]).write_text(json.dumps(doctored), encoding="utf-8")
    (tmp_path / OFFICIAL_CALENDAR_INDEX.name).write_text(OFFICIAL_CALENDAR_INDEX.read_text())
    monkeypatch.setattr(cal, "FIXTURE_DIR", tmp_path)
    monkeypatch.setattr(cal, "OFFICIAL_CALENDAR_INDEX", tmp_path / OFFICIAL_CALENDAR_INDEX.name)
    with pytest.raises(CalendarError, match="immutable"):
        load_official_calendar(version="1.1.0")


def test_an_unknown_version_is_refused() -> None:
    with pytest.raises(CalendarError, match="unknown official calendar version"):
        official_calendar_path("9.9.9")


# ---- default, newest and pinned --------------------------------------------------------------


def test_default_is_the_newest_and_research_is_pinned() -> None:
    assert latest_official_calendar_version() == "1.1.0"
    assert PINNED_RESEARCH_CALENDAR_VERSION == "1.0.0"
    newest = load_official_calendar()
    assert calendar_events_sha256(newest) == V110["events_sha256"]
    pinned = load_research_calendar()
    assert calendar_events_sha256(pinned) == V100["events_sha256"]
    assert blackout_fingerprint(pinned) != blackout_fingerprint(newest)


@pytest.mark.parametrize("relpath", [
    "src/fiboki/discovery/campaign.py",
    "scripts/gate_power_study.py",
    "scripts/campaign_k6.py",
    "scripts/pooled_universe_study.py",
    "research/run_discovery_campaign.py",
])
def test_research_entry_points_never_resolve_newest(relpath: str) -> None:
    """E-4 (pooled_universe_study, gate_power_study), K5/K6 and the discovery campaign
    must not pick up a refreshed calendar: their checkpoints record the events hash and a
    shard merge refuses a different one."""
    text = (REPO / relpath).read_text(encoding="utf-8")
    assert "load_official_calendar" not in re.sub(r"#.*|\"\"\".*?\"\"\"", "", text, flags=re.S) or \
        "version=" in text
    assert "load_research_calendar" in text


def test_paper_surfaces_use_the_newest_calendar() -> None:
    for rel in ("src/fiboki/entrypoints/paper_forward.py", "src/fiboki/workers/runtime.py",
                "scripts/run_paper_session.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "load_official_calendar()" in text, rel
        assert "load_research_calendar" not in text, rel


# ---- lineage ---------------------------------------------------------------------------------


def test_1_1_0_is_strictly_additive_on_1_0_0() -> None:
    old = {(e["currency"], e["name"], e["event_time"]): e for e in RAW100["events"]}
    new = {(e["currency"], e["name"], e["event_time"]): e for e in RAW110["events"]}
    assert set(old) <= set(new)
    for k, e in old.items():
        assert new[k] == e, k  # unchanged, byte for byte
    added = [new[k] for k in new if k not in old]
    assert len(added) == len(new) - len(old) == RAW110["lineage"]["added_events"] == 39
    assert all(e["retrieved_at"] == "2026-10-18" for e in added)
    assert all(e["event_time"] > "2026-12-04" or e["source"].startswith("ons") for e in added)


def test_lineage_names_the_parent_and_its_hash() -> None:
    lin = RAW110["lineage"]
    assert RAW110["calendar_version"] == "1.1.0" and V110["parent"] == "1.0.0"
    assert lin["parent_version"] == "1.0.0"
    assert lin["parent_file_sha256"] == V100["file_sha256"]
    assert lin["added_by_source"] == {"bls_cpi": 12, "bls_empsit": 12, "fed_fomc": 1,
                                      "ons_cpi": 12, "ons_gdp_monthly": 2}


# ---- every new event: source URL, UTC, local clock ---------------------------------------------


def _added() -> list[dict]:
    old = {(e["currency"], e["name"], e["event_time"]) for e in RAW100["events"]}
    return [e for e in RAW110["events"] if (e["currency"], e["name"], e["event_time"]) not in old]


def test_every_new_event_has_an_official_source_url_and_a_utc_timestamp() -> None:
    new = _added()
    assert new
    for e in new:
        url = urlparse(e["source_url"])
        assert url.scheme == "https" and url.netloc in OFFICIAL_HOSTS, e
        assert e["event_time"].endswith("+00:00")
        assert pd.Timestamp(e["event_time"]).utcoffset() == pd.Timedelta(0)
        assert e["impact"] == "high" and e["source"] in EXPECTED_CLOCK
        assert e["currency"] == EXPECTED_CLOCK[e["source"]][0]
        for leak in ("actual", "forecast", "previous"):
            assert e.get(leak) is None


def test_every_new_utc_time_re_derives_from_the_publisher_clock() -> None:
    for e in _added():
        _, clock, zone = EXPECTED_CLOCK[e["source"]]
        assert (e["local_time"], e["local_tz"]) == (clock, zone)
        h, m = map(int, clock.split(":"))
        d = datetime.fromisoformat(e["local_date"])
        local = datetime(d.year, d.month, d.day, h, m, tzinfo=ZoneInfo(zone))
        assert pd.Timestamp(local.astimezone(ZoneInfo("UTC"))) == pd.Timestamp(e["event_time"]), e
        assert d.weekday() < 5, e


@pytest.mark.parametrize(("source", "local_date", "utc"), [
    ("bls_empsit", "2027-03-05", "2027-03-05T13:30:00+00:00"),   # EST (US DST starts 14 March)
    ("bls_empsit", "2027-04-02", "2027-04-02T12:30:00+00:00"),   # EDT
    ("bls_cpi", "2027-03-10", "2027-03-10T13:30:00+00:00"),      # transatlantic gap week: EST
    ("bls_cpi", "2027-11-10", "2027-11-10T13:30:00+00:00"),      # EST again (ended 7 Nov)
    ("ons_cpi", "2027-03-24", "2027-03-24T07:00:00+00:00"),      # GMT (UK DST starts 28 March)
    ("ons_cpi", "2027-04-21", "2027-04-21T06:00:00+00:00"),      # BST
    ("ons_cpi", "2028-01-19", "2028-01-19T07:00:00+00:00"),      # GMT
    ("fed_fomc", "2028-01-26", "2028-01-26T19:00:00+00:00"),
])
def test_dst_spot_checks_on_new_events(source: str, local_date: str, utc: str) -> None:
    [hit] = [e for e in RAW110["events"] if e["source"] == source and e["local_date"] == local_date]
    assert hit["event_time"] == utc


def test_bls_2027_matches_the_published_schedule() -> None:
    nfp = [e["local_date"] for e in RAW110["events"] if e["source"] == "bls_empsit" and e["local_date"] >= "2027"]
    cpi = [e["local_date"] for e in RAW110["events"] if e["source"] == "bls_cpi" and e["local_date"] >= "2027"]
    assert nfp == ["2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07", "2027-06-04",
                   "2027-07-02", "2027-08-06", "2027-09-03", "2027-10-08", "2027-11-05", "2027-12-03"]
    assert cpi == ["2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10",
                   "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10"]


# ---- coverage --------------------------------------------------------------------------------


def _family_ends(raw: dict) -> dict[str, pd.Timestamp]:
    ends: dict[str, pd.Timestamp] = {}
    for e in raw["events"]:
        t = pd.Timestamp(e["event_time"])
        ends[e["source"]] = max(ends.get(e["source"], t), t)
    return ends


def test_coverage_end_is_the_earliest_family_end() -> None:
    ends = _family_ends(RAW110)
    assert {k: str(v) for k, v in ends.items()} == {
        "bls_cpi": "2027-12-10 13:30:00+00:00",
        "bls_empsit": "2027-12-03 13:30:00+00:00",
        "boe_mpc": "2027-12-16 12:00:00+00:00",
        "boj_mpm": "2027-12-17 00:00:00+00:00",
        "ecb_governing_council": "2028-12-07 13:15:00+00:00",
        "fed_fomc": "2028-01-26 19:00:00+00:00",
        "ons_cpi": "2028-01-19 07:00:00+00:00",
        "ons_gdp_monthly": "2028-02-17 07:00:00+00:00",
        "ons_labour_market": "2027-12-14 07:00:00+00:00",
    }
    assert RAW110["coverage"]["family_ends"] == {k: v.isoformat() for k, v in ends.items()}
    c = load_official_calendar().coverage()
    assert c.declared_end == min(ends.values()) == ts("2027-12-03T13:30:00Z")
    assert all(v >= ts("2027-06-30T00:00:00Z") for v in ends.values())  # every family reaches mid-2027
    assert c.declared_start == ts("2024-01-01T00:00:00Z")
    assert c.covers(ts("2026-10-18T00:00:00Z"), ts("2027-06-30T00:00:00Z"))
    assert not c.covers(ts("2026-10-18T00:00:00Z"), ts("2027-12-04T00:00:00Z"))


def test_the_loader_refuses_a_declared_end_that_is_not_the_earliest_family_end(tmp_path, monkeypatch) -> None:
    doctored = json.loads((FIXTURE_DIR / V110["file"]).read_text(encoding="utf-8"))
    doctored["coverage"]["declared_end"] = "2028-02-17T07:00:00+00:00"  # an optimistic claim
    (tmp_path / V110["file"]).write_text(json.dumps(doctored), encoding="utf-8")
    (tmp_path / OFFICIAL_CALENDAR_INDEX.name).write_text(OFFICIAL_CALENDAR_INDEX.read_text())
    monkeypatch.setattr(cal, "FIXTURE_DIR", tmp_path)
    monkeypatch.setattr(cal, "OFFICIAL_CALENDAR_INDEX", tmp_path / OFFICIAL_CALENDAR_INDEX.name)
    with pytest.raises(CalendarError, match="earliest family end"):
        load_official_calendar(version="1.1.0")


def test_paper_forward_coverage_check_passes_until_mid_november_2027() -> None:
    c = load_official_calendar()
    now = ts("2026-10-18T12:00:00Z")
    c.assert_populated(start=now, end=now + pd.Timedelta(days=14), currencies=["USD", "EUR", "GBP", "JPY"])
    late = ts("2027-11-19T12:00:00Z")  # 14 days ahead is 2027-12-03T12:00, inside the declared end
    c.assert_populated(start=late, end=late + pd.Timedelta(days=14))
    with pytest.raises(CalendarError):
        later = ts("2027-11-25T12:00:00Z")
        c.assert_populated(start=later, end=later + pd.Timedelta(days=14))


def test_a_new_event_blacks_out_a_2027_instrument() -> None:
    c = load_official_calendar()
    nfp = ts("2027-02-05T13:30:00Z")
    assert c.in_blackout("EURUSD", nfp)
    assert not load_research_calendar().in_blackout("EURUSD", nfp)  # 1.0.0 knows nothing of it
