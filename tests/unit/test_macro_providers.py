"""Point-in-time macro providers: parsing, release stamping, look-ahead, storage.

Fixtures in tests/fixtures/macro are trimmed copies of live responses fetched
on 2026-09-28 (ECB, BoE IADB, ONS, NY Fed, CFTC), except ALFRED, which is
CONSTRUCTED here from the documented FRED response shape because no FRED API
key exists in this environment.

The look-ahead property is the point of the file: for every provider, a query
as of instant D never returns an observation whose ``available_at`` is after D.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from fiboki.data.providers import MACRO_PROVIDERS
from fiboki.data.providers.alfred import (
    ENV_FRED_API_KEY,
    FRED_ATTRIBUTION,
    AlfredProvider,
    vintage_window,
)
from fiboki.data.providers.base import AuthenticationRequired, ProviderError, RateLimited
from fiboki.data.providers.boe_iadb import BoeIadbProvider, parse_iadb_csv
from fiboki.data.providers.cftc_cot import (
    RELEASE_OVERRIDES,
    CftcCotProvider,
    cot_release_time,
    parse_cot_rows,
)
from fiboki.data.providers.ecb_sdmx import EcbSdmxProvider, period_start
from fiboki.data.providers.macro_base import (
    AvailabilityBasis,
    MacroDatasetStore,
    as_of,
    england_bank_holidays_superset,
    http_get_text,
    macro_frame,
    us_bond_market_holidays_superset,
    us_federal_holidays,
)
from fiboki.data.providers.nyfed import NyFedMarketsProvider
from fiboki.data.providers.ons import OnsTimeseriesProvider
from fiboki.data.store import DataStore
from fiboki.data.versioning import VersionConflict
from tests.recorded_http import FIXTURES, RecordedHttpClient

NOW = pd.Timestamp("2026-09-28T22:45:00Z")
UTC = "UTC"


def ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text).tz_convert(UTC)


def assert_no_lookahead(frame: pd.DataFrame) -> int:
    """Probe every availability boundary and assert nothing from the future leaks."""
    stamps = sorted({t for t in frame["available_at"].dropna()})
    stamps += sorted({t for t in frame["superseded_at"].dropna()})
    probes: set[pd.Timestamp] = set()
    for t in stamps:
        probes |= {t - pd.Timedelta(microseconds=1), t, t + pd.Timedelta(microseconds=1)}
    probes |= {t for t in frame["period_start"]}
    checked = 0
    for probe in sorted(probes):
        view = as_of(frame, probe, include_missing=True)
        assert (view["available_at"] <= probe).all(), probe
        assert view["availability_basis"].ne(AvailabilityBasis.UNRESOLVED.value).all()
        live = frame[frame["available_at"].notna() & (frame["available_at"] <= probe)
                     & (frame["superseded_at"].isna() | (frame["superseded_at"] > probe))]
        assert len(view) == live.drop_duplicates(["series_id", "period"]).shape[0]
        checked += 1
    assert checked > 0
    return checked


# ------------------------------------------------------------------ ALFRED


def _alfred_pages() -> list[dict]:
    return [
        {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31", "count": 5, "offset": 0,
         "limit": 100000, "observations": [
             {"realtime_start": "2026-04-30", "realtime_end": "2026-05-27", "date": "2026-01-01",
              "value": "100.0"},
             {"realtime_start": "2026-05-28", "realtime_end": "2026-06-25", "date": "2026-01-01",
              "value": "100.4"},
             {"realtime_start": "2026-06-26", "realtime_end": "9999-12-31", "date": "2026-01-01",
              "value": "100.3"}]},
        {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31", "count": 5, "offset": 3,
         "limit": 100000, "observations": [
             {"realtime_start": "2026-01-29", "realtime_end": "9999-12-31", "date": "2025-10-01",
              "value": "."},
             {"realtime_start": "2026-07-30", "realtime_end": "9999-12-31", "date": "2026-04-01",
              "value": "101.2"}]},
    ]


def _alfred() -> tuple[AlfredProvider, RecordedHttpClient]:
    client = RecordedHttpClient()
    for page in _alfred_pages():
        client.add("https://api.stlouisfed.org/fred/series/observations", json.dumps(page))
    return AlfredProvider(api_key="FRED-SECRET", http_client=client), client


def test_alfred_vintage_windows_tile_without_gap_or_overlap():
    a1, s1 = vintage_window("2026-04-30", "2026-05-27")
    a2, s2 = vintage_window("2026-05-28", "2026-06-25")
    assert a1 == ts("2026-05-01T05:00:00Z")  # 00:00 CDT the day after publication
    assert s1 == a2 == ts("2026-05-29T05:00:00Z")
    a3, s3 = vintage_window("2026-06-26", "9999-12-31")
    assert s3 is None and a3 == ts("2026-06-27T05:00:00Z")
    assert vintage_window("2026-01-05", "2026-01-05")[0] == ts("2026-01-06T06:00:00Z")  # CST


def test_alfred_series_as_of_returns_the_vintage_current_at_the_instant():
    provider, client = _alfred()
    ds = provider.fetch("GDPC1", now=NOW)
    assert len(client.calls) == 2  # paged until FRED's count was reached
    assert client.calls[1]["params"]["offset"] == 3
    q1 = pd.Timestamp("2026-01-01", tz=UTC)
    before = provider.series_as_of("GDPC1", "2026-05-01T04:59:59Z", dataset=ds)
    assert q1 not in before.index  # published on 30 April, stamped from end of that day
    assert provider.series_as_of("GDPC1", "2026-05-01T05:00:00Z", dataset=ds)[q1] == 100.0
    assert provider.series_as_of("GDPC1", "2026-05-29T04:59:59Z", dataset=ds)[q1] == 100.0
    assert provider.series_as_of("GDPC1", "2026-05-29T05:00:00Z", dataset=ds)[q1] == 100.4
    late = provider.series_as_of("GDPC1", "2026-09-01T00:00:00Z", dataset=ds)
    assert late[q1] == 100.3 and late[pd.Timestamp("2026-04-01", tz=UTC)] == 101.2
    assert pd.Timestamp("2025-10-01", tz=UTC) not in late.index  # "." is not a value
    assert late.attrs["attribution"] == FRED_ATTRIBUTION
    assert assert_no_lookahead(ds.frame) > 10


def test_alfred_key_never_enters_lineage_or_errors():
    provider, client = _alfred()
    ds = provider.fetch("GDPC1", now=NOW)
    assert client.calls[0]["params"]["api_key"] == "FRED-SECRET"
    assert "FRED-SECRET" not in json.dumps([s.to_dict() for s in ds.lineage])
    bad = RecordedHttpClient()
    bad.fail["api.stlouisfed.org/fred/series/observations"] = RuntimeError(
        "boom https://api.stlouisfed.org/fred/series/observations?api_key=FRED-SECRET")
    with pytest.raises(ProviderError) as exc:
        AlfredProvider(api_key="FRED-SECRET", http_client=bad).fetch("GDPC1")
    assert "FRED-SECRET" not in str(exc.value)


def test_alfred_without_a_key_refuses_loudly():
    with pytest.raises(AuthenticationRequired, match=ENV_FRED_API_KEY):
        AlfredProvider.from_env(RecordedHttpClient(), env={}).fetch("GDP")


# --------------------------------------------------------------------- COT

#: The 2026 CFTC release schedule (retrieved 2026-09-28): every exception, plus
#: Monday-holiday weeks that stay on Friday.
COT_2026 = {
    "2025-12-29": "2026-01-05", "2026-06-16": "2026-06-22", "2026-06-30": "2026-07-06",
    "2026-11-10": "2026-11-16", "2026-11-24": "2026-11-30", "2026-12-22": "2026-12-28",
    "2026-01-20": "2026-01-23", "2026-02-17": "2026-02-20", "2026-05-26": "2026-05-29",
    "2026-09-08": "2026-09-11", "2026-10-13": "2026-10-16", "2026-09-22": "2026-09-25",
}


@pytest.mark.parametrize("as_of_date,release", sorted(COT_2026.items()))
def test_cot_release_rule_reproduces_the_2026_schedule(as_of_date, release):
    got, basis = cot_release_time(date.fromisoformat(as_of_date))
    assert basis is AvailabilityBasis.RELEASE_RULE
    local = got.tz_convert("America/New_York")
    assert local.date().isoformat() == release and (local.hour, local.minute) == (15, 30)


def test_cot_published_backlog_overrides_and_unresolved_windows():
    got, basis = cot_release_time(date(2025, 10, 7))
    assert basis is AvailabilityBasis.RELEASE_OVERRIDE
    assert got.tz_convert("America/New_York").date() == date(2025, 11, 21)
    assert len(RELEASE_OVERRIDES) == 14
    assert cot_release_time(date(2019, 1, 8)) == (None, AvailabilityBasis.UNRESOLVED)
    rows, report = parse_cot_rows([{"report_date_as_yyyy_mm_dd": "2019-01-08T00:00:00.000",
                                    "cftc_contract_market_code": "099741",
                                    "open_interest_all": "1"}], fields=("open_interest_all",))
    frame = macro_frame(rows)
    assert report["unresolved"] == 1
    assert as_of(frame, "2099-01-01T00:00:00Z").empty  # never served, however late the query


def test_cot_fetch_stamps_friday_and_never_leaks_the_week():
    client = RecordedHttpClient().add_file(
        "https://publicreporting.cftc.gov/resource/gpe5-46if.json", "macro/cftc_tff_099741.json")
    ds = CftcCotProvider(http_client=client).fetch(("099741",), now=NOW)
    params = client.calls[0]["params"]
    assert params["$where"] == "cftc_contract_market_code in('099741')"
    oi = "099741:open_interest_all"
    assert set(ds.frame["series_id"]) >= {oi, "099741:lev_money_positions_short"}
    friday = ts("2026-09-25T19:30:00Z")  # 15:30 EDT
    before = as_of(ds.frame, friday - pd.Timedelta(microseconds=1), series_ids=[oi])
    assert list(before["period"]) == ["2026-09-08", "2026-09-15"]
    after = as_of(ds.frame, friday, series_ids=[oi])
    assert after["period"].iloc[-1] == "2026-09-22" and after["value"].iloc[-1] == 821689.0
    assert_no_lookahead(ds.frame)


def test_cot_refuses_injection_in_contract_codes():
    with pytest.raises(ProviderError):
        CftcCotProvider().request_params(("099741') OR ('1'='1",))


# --------------------------------------------------------------------- ECB


def test_ecb_exr_is_stamped_17_00_frankfurt_and_other_flows_first_seen():
    client = RecordedHttpClient().add_file(
        "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        "macro/ecb_exr_usd_eur.csv")
    ds = EcbSdmxProvider(http_client=client).fetch("EXR", "D.USD.EUR.SP00.A", now=NOW)
    assert client.calls[0]["params"] == {"format": "csvdata"}
    assert set(ds.frame["availability_basis"]) == {"release_rule"}
    row = ds.frame[ds.frame["period"] == "2026-09-25"].iloc[0]
    assert row["available_at"] == ts("2026-09-25T15:00:00Z")  # 17:00 CEST
    assert as_of(ds.frame, "2026-09-25T14:59:59Z")["period"].iloc[-1] == "2026-09-24"
    assert as_of(ds.frame, "2026-09-25T15:00:00Z")["period"].iloc[-1] == "2026-09-25"
    assert_no_lookahead(ds.frame)

    text = (FIXTURES / "macro" / "ecb_exr_usd_eur.csv").read_text().replace(
        "EXR.D.USD.EUR.SP00.A", "ICP.M.U2.N.000000.4.ANR")
    other = RecordedHttpClient().add("https://data-api.ecb.europa.eu/service/data/ICP/x", text)
    ds2 = EcbSdmxProvider(http_client=other).fetch("ICP", "x", now=NOW)
    assert set(ds2.frame["available_at"]) == {NOW}  # FIRST_SEEN: forward-valid only
    assert as_of(ds2.frame, NOW - pd.Timedelta(seconds=1)).empty


def test_ecb_period_labels():
    assert period_start("2026-Q2") == pd.Timestamp("2026-04-01", tz=UTC)
    assert period_start("2026-08") == pd.Timestamp("2026-08-01", tz=UTC)
    assert period_start("2026") == pd.Timestamp("2026-01-01", tz=UTC)
    assert period_start("2026-S2") == pd.Timestamp("2026-07-01", tz=UTC)
    assert period_start("2026-W12") == pd.Timestamp("2026-03-16", tz=UTC)  # ISO week, Monday
    with pytest.raises(ProviderError):
        period_start("2026-M13x")


# ----------------------------------------------------------------- BoE IADB


def test_boe_sonia_is_next_london_business_day_noon_and_blank_is_missing():
    client = RecordedHttpClient().add_file(
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp",
        "macro/boe_iadb_iudbedr_iudsoia.csv")
    ds = BoeIadbProvider(http_client=client).fetch(("IUDBEDR", "IUDSOIA"), start=date(2026, 9, 1),
                                                   now=NOW)
    params = client.calls[0]["params"]
    assert params["SeriesCodes"] == "IUDBEDR,IUDSOIA" and params["Datefrom"] == "01/Sep/2026"
    assert client.calls[0]["headers"]["User-Agent"].startswith("fiboki-data")
    assert ds.report["missing_values"] == 1
    sonia = ds.frame[ds.frame["series_id"] == "IUDSOIA"].set_index("period")
    assert sonia.loc["2026-09-24", "available_at"] == ts("2026-09-25T11:00:00Z")  # 12:00 BST
    assert sonia.loc["2026-09-25", "available_at"] == ts("2026-09-28T11:00:00Z")  # Fri -> Mon
    assert pd.isna(sonia.loc["2026-09-25", "value"])
    view = as_of(ds.frame, "2026-09-25T10:59:59Z", series_ids=["IUDSOIA"])
    assert view["period"].iloc[-1] == "2026-09-23"
    assert as_of(ds.frame, "2026-12-31T00:00:00Z", series_ids=["IUDSOIA"])["period"].iloc[-1] \
        == "2026-09-24"  # the blank day is never served as a value
    rate = ds.frame[ds.frame["series_id"] == "IUDBEDR"].set_index("period")
    assert rate.loc["2026-09-01", "available_at"] == ts("2026-09-01T11:00:00Z")
    assert_no_lookahead(ds.frame)


def test_boe_sonia_over_easter_skips_both_bank_holidays():
    rows, _ = parse_iadb_csv("DATE,IUDSOIA\n02 Apr 2026,3.9\n", fetched_at=NOW)
    assert rows[0]["available_at"] == ts("2026-04-07T11:00:00Z")  # Good Fri + Easter Mon skipped


def test_boe_markup_body_is_an_error_not_an_empty_series():
    client = RecordedHttpClient().add(
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp",
        "<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD></HTML>")
    with pytest.raises(ProviderError, match="markup"):
        BoeIadbProvider(http_client=client).fetch(("IUDSOIA",), start=date(2026, 9, 1))


# ---------------------------------------------------------------------- ONS


def _ons_client(current: dict | None = None) -> RecordedHttpClient:
    cur = current or json.loads((FIXTURES / "macro" / "ons_d7g7_current.json").read_text())
    return (
        RecordedHttpClient()
        .add("https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data",
             json.dumps(cur))
        .add_file("https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/"
                  "previous/v130/data", "macro/ons_d7g7_v130.json")
    )


def test_ons_release_instants_come_from_the_versions_table():
    ds = OnsTimeseriesProvider(http_client=_ons_client()).fetch("D7G7", "MM23", now=NOW)
    months = ds.frame[ds.frame["series_id"] == "D7G7/MM23:months"].set_index("period")
    assert months.loc["2026 AUG", "available_at"] == ts("2026-09-16T06:00:00Z")  # 07:00 BST
    assert months.loc["2026 AUG", "availability_basis"] == "source_timestamp"
    assert months.loc["2026 MAY", "available_at"] == ts("2026-06-17T08:30:00Z")  # 09:30 rule
    assert months.loc["2026 MAY", "availability_basis"] == "release_rule"
    assert ds.report["next_release"] == "21 October 2026"
    view = as_of(ds.frame, "2026-09-16T05:59:59Z", series_ids=["D7G7/MM23:months"])
    assert "2026 AUG" not in set(view["period"])
    assert_no_lookahead(ds.frame)


def test_ons_archived_versions_give_real_vintages():
    cur = json.loads((FIXTURES / "macro" / "ons_d7g7_current.json").read_text())
    for m in cur["months"]:
        if m["date"] == "2026 JUL":  # simulate a revision published with the August release
            m["value"], m["updateDate"] = "3.0", "2026-09-15T23:00:00.000Z"
    provider = OnsTimeseriesProvider(http_client=_ons_client(cur))
    ds = provider.fetch("D7G7", "MM23", include_previous=True, max_previous=1, now=NOW)
    assert ds.report["previous_versions_fetched"] == 1
    sid = ["D7G7/MM23:months"]
    jul = lambda at: as_of(ds.frame, at, series_ids=sid).set_index("period").loc["2026 JUL", "value"]  # noqa: E731
    assert jul("2026-09-01T00:00:00Z") == 2.9
    assert jul("2026-09-16T05:59:59Z") == 2.9
    assert jul("2026-09-16T06:00:00Z") == 3.0
    before = as_of(ds.frame, "2026-05-01T00:00:00Z", series_ids=sid)
    assert "2026 APR" not in set(before["period"])
    assert_no_lookahead(ds.frame)


def test_ons_irregular_archive_stamps_never_make_a_stamp_earlier():
    doc = json.loads((FIXTURES / "macro" / "ons_d7g7_current.json").read_text())
    doc["versions"] = [{"uri": "/x/previous/v53", "updateDate": "2020-04-22T05:07:24.513Z",
                        "label": "v53"}]
    doc["months"] = [{"date": "2020 MAR", "value": "1.5", "label": "2020 MAR",
                      "updateDate": "2020-04-21T23:00:00.000Z"}]
    doc["years"] = []
    from fiboki.data.providers.ons import parse_ons_document

    rows, _ = parse_ons_document(doc, series_id="D7G7/MM23")
    # 05:07Z precedes the 07:00 BST publication; the 09:30 London rule stands.
    assert rows[0]["available_at"] == ts("2020-04-22T08:30:00Z")


def test_ons_unknown_series_needs_an_explicit_path():
    with pytest.raises(ProviderError, match="path="):
        OnsTimeseriesProvider().topic_path("ABCD", "XYZ")
    with pytest.raises(ProviderError):
        OnsTimeseriesProvider().topic_path("ABCD", "XYZ", "https://evil/../x")


# ------------------------------------------------------------------- NY Fed


def test_nyfed_sofr_is_next_business_day_15_00_et():
    client = RecordedHttpClient().add_file(
        "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json",
        "macro/nyfed_sofr.json")
    ds = NyFedMarketsProvider(http_client=client).fetch(
        "sofr", start=date(2026, 9, 21), end=date(2026, 9, 25), now=NOW)
    assert client.calls[0]["params"] == {"startDate": "2026-09-21", "endDate": "2026-09-25"}
    rate = ds.frame[ds.frame["series_id"] == "SOFR:percentRate"].set_index("period")
    assert rate.loc["2026-09-25", "available_at"] == ts("2026-09-28T19:00:00Z")  # Fri -> Mon
    assert rate.loc["2026-09-22", "available_at"] == ts("2026-09-23T19:00:00Z")
    view = as_of(ds.frame, "2026-09-28T18:59:59Z", series_ids=["SOFR:percentRate"])
    assert view["period"].iloc[-1] == "2026-09-24"
    assert_no_lookahead(ds.frame)


def test_nyfed_repo_uses_the_posted_instant():
    client = RecordedHttpClient().add_file(
        "https://markets.newyorkfed.org/api/rp/results/search.json", "macro/nyfed_repo.json")
    ds = NyFedMarketsProvider(http_client=client).fetch_repo(
        start=date(2026, 9, 24), end=date(2026, 9, 25), now=NOW)
    assert set(ds.frame["availability_basis"]) == {"source_timestamp"}
    first = ds.frame.sort_values("available_at").iloc[0]
    assert first["available_at"] == ts("2026-09-24T12:30:36Z")  # "2026-09-24 08:30:36" EDT
    assert_no_lookahead(ds.frame)


def test_us_calendars_include_observed_days_and_good_friday():
    h26 = us_federal_holidays(2026)
    assert date(2026, 7, 3) in h26 and date(2026, 7, 4) not in h26  # Saturday -> Friday
    assert len(h26) == 11
    assert date(2021, 12, 31) in us_federal_holidays(2021)  # New Year 2022 was a Saturday
    assert date(2022, 1, 1) not in us_federal_holidays(2022)
    assert date(2026, 4, 3) in us_bond_market_holidays_superset(2026)  # Good Friday
    assert date(2025, 1, 9) in us_bond_market_holidays_superset(2025)


def test_england_superset_covers_moved_and_substitute_days():
    h = england_bank_holidays_superset(2026)
    assert {date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 4), date(2026, 5, 25),
            date(2026, 8, 31), date(2026, 12, 25), date(2026, 12, 28)} <= h
    assert {date(2022, 6, 2), date(2022, 6, 3), date(2022, 9, 19), date(2022, 5, 30)} <= \
        england_bank_holidays_superset(2022)


# ------------------------------------------------------------ cross-cutting


@pytest.mark.parametrize("name", sorted(MACRO_PROVIDERS))
def test_every_provider_describes_licence_attribution_and_semantics(name):
    provider = MACRO_PROVIDERS[name]()
    d = provider.descriptor
    assert d.licence_url.startswith("https://")
    assert d.attribution and d.point_in_time and d.known_limitations
    text = provider.describe()
    assert d.licence_url in text and d.attribution in text
    assert AvailabilityBasis.FIRST_SEEN not in d.availability_bases or name in {"ecb_sdmx", "boe_iadb"}


def test_fred_attribution_is_the_required_notice_verbatim():
    assert MACRO_PROVIDERS["alfred"]().descriptor.attribution == (
        "This product uses the FRED® API but is not endorsed or certified by the "
        "Federal Reserve Bank of St. Louis.")


@pytest.mark.parametrize("status,exc", [(401, AuthenticationRequired), (403, AuthenticationRequired),
                                        (429, RateLimited), (500, ProviderError)])
def test_http_errors_are_typed(status, exc):
    client = RecordedHttpClient().add("https://x.example/a", "nope", status)
    with pytest.raises(exc):
        http_get_text(client, "https://x.example/a")


def test_macro_store_is_content_addressed_and_write_once(tmp_path):
    root = tmp_path / "root"
    with pytest.raises(Exception, match="not a Fiboki data root"):
        MacroDatasetStore(root)
    DataStore.initialise(root)
    store = MacroDatasetStore(root)
    client = RecordedHttpClient().add_file(
        "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json",
        "macro/nyfed_sofr.json")
    provider = NyFedMarketsProvider(http_client=client)
    a = provider.fetch("sofr", start=date(2026, 9, 21), end=date(2026, 9, 25), now=NOW)
    b = provider.fetch("sofr", start=date(2026, 9, 21), end=date(2026, 9, 25),
                       now=NOW + timedelta(days=3))
    assert a.version_id == b.version_id  # fetch time is not identity for rule-stamped data
    vid, where, created = store.write(a)
    assert created and vid.startswith("ds_")
    assert store.write(b) == (vid, where, False)
    loaded = store.load("nyfed", "sofr", vid)
    pd.testing.assert_frame_equal(loaded, a.frame)
    meta = json.loads((where / "_dataset.json").read_text())
    assert meta["descriptor"]["licence_url"] == "https://www.newyorkfed.org/privacy/termsofuse"
    meta["content_checksum"] = "0" * 64
    (where / "_dataset.json").write_text(json.dumps(meta))
    with pytest.raises(VersionConflict):
        store.write(a)


def test_macro_frame_refuses_naive_and_unstamped_rows():
    base = {"series_id": "s", "period": "p", "period_start": pd.Timestamp("2026-01-01", tz=UTC),
            "value": 1.0, "availability_basis": AvailabilityBasis.RELEASE_RULE}
    with pytest.raises(ProviderError, match="available_at is missing"):
        macro_frame([{**base, "available_at": None}])
    with pytest.raises(ProviderError, match="naive"):
        macro_frame([{**base, "available_at": pd.Timestamp("2026-01-02")}])
    with pytest.raises(ProviderError, match="explicit instant"):
        as_of(macro_frame([{**base, "available_at": NOW}]), None)  # type: ignore[arg-type]
