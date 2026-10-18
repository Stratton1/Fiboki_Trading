"""Build official calendar version 1.1.0 from 1.0.0 plus the additions read on 2026-10-18.

Offline and deterministic: every addition is listed below with the publisher
page it was read from. The 1.0.0 file is NEVER edited; this writes
``scheduled_events_official_v1_1_0.json`` and (re)writes the version index
``official_calendar_index.json`` with both files' hashes and the lineage.

Run:  PYTHONPATH=src python scripts/build_official_calendar_v1_1_0.py

Next refresh: copy this script to ``build_official_calendar_vX_Y_Z.py``, change
PARENT/NEW and the ADDITIONS table, re-read every publisher's page (including a
reconciliation of dates already in the file that may have MOVED), and run it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fiboki.marketstate.calendar import (
    FIXTURE_DIR,
    OFFICIAL_CALENDAR_INDEX,
    calendar_events_sha256,
    load_official_calendar,
)

UTC = ZoneInfo("UTC")
READ_ON = "2026-10-18"
PARENT = ("1.0.0", FIXTURE_DIR / "scheduled_events_official.json")
NEW_VERSION = "1.1.0"
NEW_FILE = "scheduled_events_official_v1_1_0.json"

#: (source, local date) -> added. BLS: bls.gov/schedule/2027/home.htm, the monthly
#: calendar pages "last modified October 9, 2026", agreeing with
#: bls.gov/schedule/news_release/{empsit,cpi}.htm (08:30 AM Eastern).
BLS_NFP = ["2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07", "2027-06-04",
           "2027-07-02", "2027-08-06", "2027-09-03", "2027-10-08", "2027-11-05", "2027-12-03"]
BLS_CPI = ["2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10",
           "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10"]
#: ONS release calendar API (api.beta.ons.gov.uk/v1/search/releases, release-type
#: type-upcoming): (UTC release instant, release slug). All finalised (confirmed).
ONS_CPI = [
    ("2027-02-17T07:00", "consumerpriceinflationukjanuary2027"),
    ("2027-03-24T07:00", "consumerpriceinflationukfebruary2027"),
    ("2027-04-21T06:00", "consumerpriceinflationukmarch2027"),
    ("2027-05-19T06:00", "consumerpriceinflationukapril2027"),
    ("2027-06-16T06:00", "consumerpriceinflationukmay2027"),
    ("2027-07-21T06:00", "consumerpriceinflationukjune2027"),
    ("2027-08-18T06:00", "consumerpriceinflationukjuly2027"),
    ("2027-09-15T06:00", "consumerpriceinflationukaugust2027"),
    ("2027-10-20T06:00", "consumerpriceinflationukseptember2027"),
    ("2027-11-17T07:00", "consumerpriceinflationukoctober2027"),
    ("2027-12-15T07:00", "consumerpriceinflationuknovember2027"),
    ("2028-01-19T07:00", "consumerpriceinflationukdecember2027"),
]
ONS_GDP = [
    ("2028-01-14T07:00", "gdpmonthlyestimateuknovember2027"),
    ("2028-02-17T07:00", "gdpmonthlyestimateukdecember2027"),
]
#: federalreserve.gov/monetarypolicy/fomccalendars.htm (updated 2026-10-07): "January 25-26" 2028.
FOMC = ["2028-01-26"]


def _local_to_utc(local_date: str, hhmm: str, tz: str) -> str:
    h, m = map(int, hhmm.split(":"))
    d = datetime.fromisoformat(local_date)
    local = datetime(d.year, d.month, d.day, h, m, tzinfo=ZoneInfo(tz))
    return local.astimezone(UTC).isoformat()


def _template(events: list[dict], source: str) -> dict:
    return [e for e in events if e["source"] == source][-1]


def _clone(tpl: dict, local_date: str, url: str | None = None) -> dict:
    ev = dict(tpl)
    ev["event_time"] = _local_to_utc(local_date, tpl["local_time"], tpl["local_tz"])
    ev["local_date"] = local_date
    ev["retrieved_at"] = READ_ON
    if url:
        ev["source_url"] = url
    return ev


def build() -> None:
    parent_raw = json.loads(PARENT[1].read_text(encoding="utf-8"))
    events = list(parent_raw["events"])
    tpl = {s: _template(events, s) for s in
           ("bls_empsit", "bls_cpi", "ons_cpi", "ons_gdp_monthly", "fed_fomc")}
    added: dict[str, int] = {}

    def add(ev: dict) -> None:
        assert datetime.fromisoformat(ev["local_date"]).weekday() < 5, ev  # no weekend releases
        events.append(ev)
        added[ev["source"]] = added.get(ev["source"], 0) + 1

    for d in BLS_NFP:
        add(_clone(tpl["bls_empsit"], d))
    for d in BLS_CPI:
        add(_clone(tpl["bls_cpi"], d))
    for src, rows in (("ons_cpi", ONS_CPI), ("ons_gdp_monthly", ONS_GDP)):
        for utc, slug in rows:
            t = tpl[src]
            ev = dict(t)
            ev["event_time"] = f"{utc}:00+00:00"
            local = datetime.fromisoformat(ev["event_time"]).astimezone(ZoneInfo(t["local_tz"]))
            assert local.strftime("%H:%M") == t["local_time"], (src, utc)  # 07:00 UK, DST-correct
            ev["local_date"] = local.strftime("%Y-%m-%d")
            ev["source_url"] = f"https://www.ons.gov.uk/releases/{slug}"
            ev["retrieved_at"] = READ_ON
            add(ev)
    for d in FOMC:
        add(_clone(tpl["fed_fomc"], d))

    events.sort(key=lambda e: (e["event_time"], e["name"]))
    keys = [(e["currency"], e["name"], e["event_time"]) for e in events]
    assert len(keys) == len(set(keys)), "duplicate event"

    sources = json.loads(json.dumps(parent_raw["sources_used"]))
    for src, meta in sources.items():
        evs = [e for e in events if e["source"] == src]
        meta["n_events"] = len(evs)
        meta["first_event"] = evs[0]["event_time"]
        meta["last_event"] = evs[-1]["event_time"]
        meta["coverage_end"] = evs[-1]["event_time"]
    sources["bls_empsit"]["exclusions"] = (
        "October 2025 Employment Situation was not published (2025 lapse in appropriations); "
        "September 2025 data released 2025-11-20. 1.1.0: the 2027 schedule is published "
        "(bls.gov/schedule/2027/home.htm, calendars last modified 2026-10-09); the December 2027 "
        "reference month (a January 2028 release) is not yet on any BLS page."
    )
    sources["bls_cpi"]["exclusions"] = (
        "October 2025 CPI was not published (2025 lapse in appropriations); September 2025 CPI "
        "released 2025-10-24. 1.1.0: the 2027 schedule is published; the December 2027 CPI "
        "(January 2028) is not yet on any BLS page."
    )
    sources["bls_empsit"]["urls"] = parent_raw["sources_used"]["bls_empsit"]["urls"] + [
        "https://www.bls.gov/schedule/2027/home.htm"]
    sources["bls_cpi"]["urls"] = parent_raw["sources_used"]["bls_cpi"]["urls"] + [
        "https://www.bls.gov/schedule/2027/home.htm"]
    sources["ons_cpi"]["exclusions"] = (
        "1.0.0 dropped 11 PROVISIONAL dates (2027-02 onwards). On 2026-10-18 every UK CPI release "
        "to January 2028 is finalised (confirmed) in the ONS API, so they are included."
    )
    sources["fed_fomc"]["exclusions"] += (
        " 1.1.0 adds 2028-01-26 (the only 2028 date on the page)."
    )

    ends = {s: m["coverage_end"] for s, m in sources.items()}
    declared_end = min(datetime.fromisoformat(v) for v in ends.values()).isoformat()
    cov = dict(parent_raw["coverage"])
    cov["declared_end"] = declared_end
    cov["family_ends"] = ends
    cov["rule"] = (
        parent_raw["coverage"]["rule"]
        + " family_ends records each source's own last-published event; declared_end is the minimum"
        " of them and the loader refuses a file where it is not."
    )

    new = dict(parent_raw)
    new["calendar_version"] = NEW_VERSION
    new["retrieved_at"] = READ_ON
    new["description"] = parent_raw["description"] + (
        f" Version {NEW_VERSION}: strictly additive on {PARENT[0]} (every parent event is present, "
        "unchanged); no date already in the parent had moved when re-read on " + READ_ON + "."
    )
    new["coverage"] = cov
    new["sources_used"] = sources
    new["sources_skipped"] = {
        "aggregators": parent_raw["sources_skipped"]["aggregators"],
        "bls_2028": "No BLS 2028 schedule published.",
        "ons_labour_market": "No UK labour-market release after 2027-12-14 is in the ONS calendar yet.",
        "boe_2028": "The Bank of England lists no 2028 MPC dates yet.",
        "boj_2028": "The Bank of Japan lists no 2028 meeting dates yet.",
        "other_currencies": "AUD, CAD, CHF, NZD not added: the declared span is one global start "
                            "(2024-01-01), so a family covering only 2026+ would read as 'no events' "
                            "for 2024-2025. Needs per-family start dates first.",
    }
    new["lineage"] = {
        "parent_version": PARENT[0],
        "parent_file": PARENT[1].name,
        "parent_file_sha256": hashlib.sha256(PARENT[1].read_bytes()).hexdigest(),
        "added_events": sum(added.values()),
        "added_by_source": dict(sorted(added.items())),
        "reason": "Coverage was due to end 2026-12-04 (BLS); paper forward needs 14 days ahead.",
    }
    new["events"] = events
    path = FIXTURE_DIR / NEW_FILE
    path.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    parent_cal = load_official_calendar(PARENT[1])
    new_cal = load_official_calendar(path)
    index = {
        "schema": 1,
        "rule": "A calendar version is immutable once listed. A refresh adds a NEW file and a new "
                "entry here with its lineage; it never edits an existing file. load_official_calendar "
                "refuses a listed file whose events no longer hash to events_sha256. Default "
                "(version=None) is the newest entry (paper, API, CLI); research and backtests that "
                "must not drift pin PINNED_RESEARCH_CALENDAR_VERSION.",
        "versions": {
            PARENT[0]: {
                "file": PARENT[1].name, "parent": None,
                "file_sha256": hashlib.sha256(PARENT[1].read_bytes()).hexdigest(),
                "events_sha256": calendar_events_sha256(parent_cal),
                "n_events": len(parent_cal), "retrieved_at": parent_raw["retrieved_at"],
                "declared_end": parent_raw["coverage"]["declared_end"],
            },
            NEW_VERSION: {
                "file": NEW_FILE, "parent": PARENT[0],
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "events_sha256": calendar_events_sha256(new_cal),
                "n_events": len(new_cal), "retrieved_at": READ_ON, "declared_end": declared_end,
            },
        },
    }
    OFFICIAL_CALENDAR_INDEX.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(index["versions"], indent=1))
    print("added", added, "ends", ends)


if __name__ == "__main__":
    build()
