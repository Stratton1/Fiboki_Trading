"""Economic calendar: blackout logic, loaders, and the honest empty default.

The single most dangerous thing about an event calendar is that an *empty* one
answers "no, you are not in a blackout" for every bar in history, and a backtest
then trades straight through every NFP with no warning. These tests pin both the
blackout maths and the loudness of that default.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from fiboki.marketstate.calendar import (
    USER_ACTION_NOTE,
    CalendarError,
    EconomicEvent,
    ImpactLevel,
    InMemoryEconomicCalendar,
    instrument_currencies,
    load_events_csv,
    load_events_json,
    load_recurring_event_types,
    recurring_types_for,
    synthesise_events,
)

FOMC = pd.Timestamp("2019-07-31 18:00", tz="UTC")
NFP = pd.Timestamp("2019-08-02 12:30", tz="UTC")
ECB = pd.Timestamp("2019-09-12 11:45", tz="UTC")


def _events() -> list[EconomicEvent]:
    return [
        EconomicEvent(FOMC, "USD", "FOMC Rate Decision", ImpactLevel.HIGH,
                      actual=2.25, forecast=2.25, previous=2.50,
                      recurring_key="fomc_rate_decision", source="test"),
        EconomicEvent(NFP, "USD", "US Non-Farm Payrolls", ImpactLevel.HIGH,
                      actual=164.0, forecast=165.0, previous=193.0,
                      recurring_key="us_nfp", source="test"),
        EconomicEvent(ECB, "EUR", "ECB Rate Decision", ImpactLevel.HIGH,
                      recurring_key="ecb_rate_decision", source="test"),
        EconomicEvent(
            pd.Timestamp("2019-08-15 12:30", tz="UTC"), "USD", "US Retail Sales",
            ImpactLevel.MEDIUM, source="test",
        ),
        EconomicEvent(
            pd.Timestamp("2019-08-20 08:30", tz="UTC"), "GBP", "UK PPI",
            ImpactLevel.LOW, source="test",
        ),
    ]


@pytest.fixture
def calendar() -> InMemoryEconomicCalendar:
    return InMemoryEconomicCalendar(_events())


# =====================================================================
# The event model
# =====================================================================


def test_naive_event_time_is_refused() -> None:
    with pytest.raises(CalendarError, match="tz-aware UTC"):
        EconomicEvent(pd.Timestamp("2019-07-31 18:00"), "USD", "FOMC", ImpactLevel.HIGH)


def test_event_time_is_normalised_to_utc() -> None:
    ny = EconomicEvent(
        pd.Timestamp("2019-07-31 14:00", tz="America/New_York"),
        "USD", "FOMC", ImpactLevel.HIGH,
    )
    assert ny.event_time == FOMC
    assert str(ny.event_time.tz) == "UTC"


def test_actual_is_not_visible_before_the_release(calendar) -> None:
    """Reading ``.actual`` in a backtest before the print is look-ahead."""
    fomc = calendar.all_events()[0]
    assert fomc.actual == 2.25  # the raw field is there for reporting
    assert fomc.actual_as_of(FOMC - pd.Timedelta(minutes=1)) is None
    assert fomc.actual_as_of(FOMC) == 2.25
    assert fomc.actual_as_of(FOMC + pd.Timedelta(hours=1)) == 2.25


def test_surprise_needs_both_a_release_and_a_forecast(calendar) -> None:
    nfp = next(e for e in calendar.all_events() if e.recurring_key == "us_nfp")
    assert nfp.surprise(NFP - pd.Timedelta(minutes=1)) is None
    assert nfp.surprise(NFP) == pytest.approx(-1.0)
    ecb = next(
        e for e in calendar.all_events() if e.recurring_key == "ecb_rate_decision"
    )
    assert ecb.surprise(ECB + pd.Timedelta(days=1)) is None  # no forecast recorded


def test_actual_as_of_requires_a_tz(calendar) -> None:
    with pytest.raises(CalendarError):
        calendar.all_events()[0].actual_as_of(pd.Timestamp("2019-07-31 18:00"))


def test_impact_levels_are_ordered() -> None:
    assert ImpactLevel.HIGH > ImpactLevel.MEDIUM > ImpactLevel.LOW
    assert ImpactLevel.HIGH >= ImpactLevel.HIGH
    assert ImpactLevel.LOW < ImpactLevel.HIGH


# =====================================================================
# Instrument exposure
# =====================================================================


def test_instrument_currencies() -> None:
    assert instrument_currencies("EURUSD") == ("EUR", "USD")
    assert instrument_currencies("GBPJPY") == ("GBP", "JPY")
    # Gold is quoted in dollars and moves on US macro; there is no "XAU" economy.
    assert instrument_currencies("XAUUSD") == ("USD",)
    assert instrument_currencies("XAGUSD") == ("USD",)
    assert instrument_currencies("US500") == ("USD",)
    assert instrument_currencies("UK100") == ("GBP",)


def test_unregistered_instrument_is_refused() -> None:
    with pytest.raises(KeyError):
        instrument_currencies("NOTAPAIR")


# =====================================================================
# Querying
# =====================================================================


def test_events_between_filters_by_window_currency_and_impact(calendar) -> None:
    all_aug = calendar.events_between(
        pd.Timestamp("2019-08-01", tz="UTC"), pd.Timestamp("2019-08-31", tz="UTC")
    )
    assert {e.name for e in all_aug} == {
        "US Non-Farm Payrolls", "US Retail Sales", "UK PPI"
    }
    usd_high = calendar.events_between(
        pd.Timestamp("2019-08-01", tz="UTC"),
        pd.Timestamp("2019-08-31", tz="UTC"),
        currencies=["USD"],
        min_impact=ImpactLevel.HIGH,
    )
    assert [e.name for e in usd_high] == ["US Non-Farm Payrolls"]


def test_events_between_rejects_a_reversed_window(calendar) -> None:
    with pytest.raises(CalendarError, match="start is after end"):
        calendar.events_between(
            pd.Timestamp("2019-09-01", tz="UTC"), pd.Timestamp("2019-08-01", tz="UTC")
        )


def test_next_event(calendar) -> None:
    nxt = calendar.next_event(
        pd.Timestamp("2019-08-01", tz="UTC"), currencies=["USD"],
        min_impact=ImpactLevel.HIGH,
    )
    assert nxt is not None and nxt.event_time == NFP
    assert calendar.next_event(pd.Timestamp("2030-01-01", tz="UTC")) is None


def test_events_are_sorted_and_deduplicated() -> None:
    dup = _events() + _events()
    cal = InMemoryEconomicCalendar(dup)
    assert len(cal) == 5
    times = [e.event_time for e in cal.all_events()]
    assert times == sorted(times)


# =====================================================================
# Blackout logic — the point of the module
# =====================================================================


def test_blackout_window_is_symmetric_and_closed(calendar) -> None:
    before = FOMC - pd.Timedelta(minutes=30)
    after = FOMC + pd.Timedelta(minutes=30)
    assert calendar.in_blackout("EURUSD", FOMC)
    assert calendar.in_blackout("EURUSD", before)
    assert calendar.in_blackout("EURUSD", after)
    assert not calendar.in_blackout("EURUSD", before - pd.Timedelta(minutes=1))
    assert not calendar.in_blackout("EURUSD", after + pd.Timedelta(minutes=1))


def test_asymmetric_margins(calendar) -> None:
    assert calendar.in_blackout(
        "EURUSD", FOMC + pd.Timedelta(minutes=90),
        minutes_before=5, minutes_after=120,
    )
    assert not calendar.in_blackout(
        "EURUSD", FOMC - pd.Timedelta(minutes=90),
        minutes_before=5, minutes_after=120,
    )


def test_blackout_respects_instrument_currency_exposure(calendar) -> None:
    """A dollar event blacks out dollar pairs; a euro event does not."""
    assert calendar.in_blackout("EURUSD", FOMC)
    assert calendar.in_blackout("XAUUSD", FOMC)
    assert not calendar.in_blackout("GBPJPY", FOMC)
    assert calendar.in_blackout("EURUSD", ECB)
    assert not calendar.in_blackout("GBPJPY", ECB)
    assert not calendar.in_blackout("XAUUSD", ECB)


def test_blackout_respects_the_impact_floor(calendar) -> None:
    retail = pd.Timestamp("2019-08-15 12:30", tz="UTC")
    assert not calendar.in_blackout("EURUSD", retail, min_impact=ImpactLevel.HIGH)
    assert calendar.in_blackout("EURUSD", retail, min_impact=ImpactLevel.MEDIUM)
    assert calendar.in_blackout("EURUSD", retail, min_impact=ImpactLevel.LOW)


def test_events_near_names_the_reason(calendar) -> None:
    near = calendar.events_near("EURUSD", FOMC + pd.Timedelta(minutes=10))
    assert [e.name for e in near] == ["FOMC Rate Decision"]


def test_negative_margins_are_refused(calendar) -> None:
    with pytest.raises(CalendarError, match="non-negative"):
        calendar.in_blackout("EURUSD", FOMC, minutes_before=-1)


def test_blackout_mask_over_a_bar_index(calendar) -> None:
    idx = pd.date_range("2019-07-31 12:00", periods=12, freq="1h", tz="UTC")
    mask = calendar.blackout_mask("EURUSD", idx, minutes_before=60, minutes_after=60)
    assert mask.sum() == 3  # 17:00, 18:00 and 19:00 UTC
    assert bool(mask.loc[FOMC])
    assert bool(mask.loc[FOMC - pd.Timedelta(hours=1)])
    assert not bool(mask.loc[FOMC - pd.Timedelta(hours=2)])


def test_blackout_mask_is_empty_for_an_unexposed_instrument(calendar) -> None:
    idx = pd.date_range("2019-07-31 12:00", periods=12, freq="1h", tz="UTC")
    assert not calendar.blackout_mask("GBPJPY", idx).any()


def test_blackout_mask_needs_a_tz(calendar) -> None:
    idx = pd.date_range("2019-07-31 12:00", periods=4, freq="1h")
    with pytest.raises(CalendarError, match="tz-aware"):
        calendar.blackout_mask("EURUSD", idx)


def test_overlapping_windows_are_merged() -> None:
    a = pd.Timestamp("2020-01-02 12:30", tz="UTC")
    cal = InMemoryEconomicCalendar(
        [
            EconomicEvent(a, "USD", "CPI", ImpactLevel.HIGH),
            EconomicEvent(a + pd.Timedelta(minutes=20), "USD", "Claims", ImpactLevel.HIGH),
            EconomicEvent(a + pd.Timedelta(hours=6), "USD", "FOMC", ImpactLevel.HIGH),
        ]
    )
    windows = cal.blackout_windows(
        "EURUSD",
        pd.Timestamp("2020-01-01", tz="UTC"),
        pd.Timestamp("2020-01-03", tz="UTC"),
        minutes_before=30,
        minutes_after=30,
    )
    assert len(windows) == 2
    assert len(windows[0].events) == 2
    assert windows[0].start == a - pd.Timedelta(minutes=30)
    assert windows[0].end == a + pd.Timedelta(minutes=50)
    assert windows[0].contains(a + pd.Timedelta(minutes=25))
    assert "CPI" in windows[0].reason and "Claims" in windows[0].reason
    unmerged = cal.blackout_windows(
        "EURUSD",
        pd.Timestamp("2020-01-01", tz="UTC"),
        pd.Timestamp("2020-01-03", tz="UTC"),
        merge=False,
    )
    assert len(unmerged) == 3


# =====================================================================
# The empty calendar is loud
# =====================================================================


def test_empty_calendar_reports_zero_coverage() -> None:
    cal = InMemoryEconomicCalendar.empty()
    cov = cal.coverage()
    assert not cov.is_populated
    assert cov.n_events == 0
    assert cov.first_event is None


def test_empty_calendar_silently_permits_everything() -> None:
    """Documenting the dangerous default, so nobody discovers it in production."""
    cal = InMemoryEconomicCalendar.empty()
    assert not cal.in_blackout("EURUSD", FOMC)
    assert cal.events_between(
        pd.Timestamp("1990-01-01", tz="UTC"), pd.Timestamp("2030-01-01", tz="UTC")
    ) == ()


def test_assert_populated_is_the_guard(calendar) -> None:
    with pytest.raises(CalendarError, match="empty"):
        InMemoryEconomicCalendar.empty().assert_populated()
    calendar.assert_populated()  # a populated one passes
    with pytest.raises(CalendarError, match="does not span"):
        calendar.assert_populated(
            start=pd.Timestamp("2010-01-01", tz="UTC"),
            end=pd.Timestamp("2030-01-01", tz="UTC"),
        )


def test_coverage_reports_what_it_has(calendar) -> None:
    cov = calendar.coverage()
    assert cov.n_events == 5
    assert set(cov.currencies) == {"USD", "EUR", "GBP"}
    assert cov.by_impact == {"high": 3, "medium": 1, "low": 1}
    assert cov.covers(FOMC, ECB)
    assert not cov.covers(pd.Timestamp("2000-01-01", tz="UTC"), ECB)


def test_user_action_note_states_the_gap_plainly() -> None:
    assert "DATED EVENTS ARE NOT SUPPLIED" in USER_ACTION_NOTE
    assert "USER ACTION REQUIRED" in USER_ACTION_NOTE
    assert "external feed" in USER_ACTION_NOTE
    assert "revis" in USER_ACTION_NOTE  # the first-print caveat
    assert "NO network access" in USER_ACTION_NOTE


# =====================================================================
# Loaders and the shipped fixture
# =====================================================================


def test_recurring_event_fixture_loads_and_is_sane() -> None:
    types = load_recurring_event_types()
    keys = {t.key for t in types}
    for expected in (
        "fomc_rate_decision", "us_nfp", "us_cpi",
        "ecb_rate_decision", "boe_rate_decision",
    ):
        assert expected in keys
    assert len(keys) == len(types)  # unique
    for t in types:
        assert len(t.currency) == 3
        assert t.typical_schedule
        assert ":" in t.typical_release_utc


def test_recurring_types_for_instrument() -> None:
    eurusd = {t.key for t in recurring_types_for("EURUSD")}
    assert "fomc_rate_decision" in eurusd
    assert "ecb_rate_decision" in eurusd
    assert "boj_rate_decision" not in eurusd
    gold = {t.currency for t in recurring_types_for("XAUUSD")}
    assert gold == {"USD"}


def test_fixture_contains_no_dated_events() -> None:
    """The taxonomy must not be mistakable for a calendar."""
    raw = json.loads(
        (
            __import__("fiboki.marketstate.calendar", fromlist=["x"])
            .RECURRING_EVENTS_FIXTURE
        ).read_text(encoding="utf-8")
    )
    for item in raw["event_types"]:
        assert "event_time" not in item
        assert "actual" not in item
    assert "NO dated instances" in raw["description"]


def test_json_round_trip(tmp_path, calendar) -> None:
    path = tmp_path / "events.json"
    path.write_text(calendar.to_json(), encoding="utf-8")
    loaded = InMemoryEconomicCalendar.from_json(path)
    assert len(loaded) == len(calendar)
    assert loaded.all_events()[0].event_time == calendar.all_events()[0].event_time
    assert loaded.all_events()[0].actual == 2.25


def test_json_loader_accepts_a_wrapped_list(tmp_path) -> None:
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "event_time": "2019-07-31T18:00:00+00:00",
                        "currency": "usd",
                        "name": "FOMC",
                        "impact": "HIGH",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    events = load_events_json(path)
    assert len(events) == 1
    assert events[0].currency == "USD"
    assert events[0].impact is ImpactLevel.HIGH


def test_json_loader_refuses_a_naive_event_time(tmp_path) -> None:
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            [{"event_time": "2019-07-31 18:00:00", "currency": "USD",
              "name": "FOMC", "impact": "high"}]
        ),
        encoding="utf-8",
    )
    with pytest.raises(CalendarError, match="timezone-naive"):
        load_events_json(path)


def test_csv_loader(tmp_path) -> None:
    path = tmp_path / "events.csv"
    path.write_text(
        "event_time,currency,name,impact,actual,forecast,previous\n"
        "2019-07-31T18:00:00+00:00,USD,FOMC Rate Decision,high,2.25,2.25,2.50\n"
        "2019-08-02T12:30:00+00:00,USD,US Non-Farm Payrolls,high,164,165,\n",
        encoding="utf-8",
    )
    events = load_events_csv(path)
    assert len(events) == 2
    assert events[0].forecast == 2.25
    assert events[1].previous is None


def test_csv_loader_reports_missing_columns(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("event_time,currency\n2019-07-31T18:00:00+00:00,USD\n", encoding="utf-8")
    with pytest.raises(CalendarError, match="missing required columns"):
        load_events_csv(path)


def test_missing_file_is_reported(tmp_path) -> None:
    with pytest.raises(CalendarError, match="not found"):
        load_events_json(tmp_path / "nope.json")
    with pytest.raises(CalendarError, match="not found"):
        load_events_csv(tmp_path / "nope.csv")


def test_synthesised_events_are_marked_as_synthetic() -> None:
    types = [t for t in load_recurring_event_types() if t.key == "us_nfp"]
    events = synthesise_events(types, [FOMC])
    assert events[0].source == "synthetic"
    assert events[0].recurring_key == "us_nfp"
    assert events[0].currency == "USD"
    with pytest.raises(CalendarError, match="one timestamp per type"):
        synthesise_events(types, [FOMC, NFP])


def test_with_events_does_not_mutate(calendar) -> None:
    extra = EconomicEvent(
        pd.Timestamp("2021-01-01 00:00", tz="UTC"), "JPY", "BoJ", ImpactLevel.HIGH
    )
    bigger = calendar.with_events([extra])
    assert len(calendar) == 5
    assert len(bigger) == 6
