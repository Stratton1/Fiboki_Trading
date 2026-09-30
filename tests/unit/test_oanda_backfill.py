"""``fiboki data oanda-backfill``: paging, resume, refusal, idempotence, lineage.

The fetch is a fake built from the recorded practice-API fixtures plus
synthetic continuations that behave like v20 ``from`` + ``count``: every candle
at or after ``from``, oldest first, at most ``count`` of them, the newest one
still forming. Nothing here reaches a network.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd
import pytest

from fiboki.broker.oanda import HttpResponse
from fiboki.broker.retry import ReadRetry
from fiboki.cli import EXIT_FAIL, EXIT_MISUSE, EXIT_OK, main
from fiboki.core.enums import Timeframe
from fiboki.data import backfill as bf
from fiboki.data.calendars import FX_CALENDAR
from fiboki.data.integrity import validate
from fiboki.data.providers.base import AuthenticationRequired, ProviderError
from fiboki.data.providers.oanda import GRANULARITY, OandaCandlesProvider
from fiboki.data.schema import DatasetKind, PriceBasis, canonical_frame, describe_frame
from fiboki.data.store import DataStore
from fiboki.data.versioning import TransformationStep
from fiboki.workers.feeds import CandleHttpError, TransportHttpClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oanda"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _stamp(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def synthetic_candles(
    start: str, end: str, tf: Timeframe, *, seed: int = 7, first_open: float = 1.1
) -> list[dict]:
    """Complete mid candles at every FX-session bar open in ``[start, end)``.

    D1 bars are Monday-Friday 00:00Z. Prices are a seeded random walk with
    consistent OHLC, so the integrity checks have nothing to object to.
    """
    grid = pd.date_range(start, end, freq=f"{tf.minutes}min", tz="UTC", inclusive="left")
    if tf is Timeframe.D1:
        opens = [t for t in grid if t.weekday() < 5]
    else:
        opens = [t for t in grid if FX_CALENDAR.is_open(t)]
    rng = np.random.default_rng(seed)
    price = first_open
    out = []
    for t in opens:
        o = price
        c = o + float(rng.normal(0, 0.0006))
        h = max(o, c) + 0.0003
        lo = min(o, c) - 0.0003
        out.append(
            {
                "complete": True,
                "volume": int(rng.integers(100, 5000)),
                "time": _stamp(t),
                "mid": {k: f"{v:.5f}" for k, v in zip("ohlc", (o, h, lo, c), strict=True)},
            }
        )
        price = c
    return out


class FakeV20:
    """v20 ``from`` + ``count`` over an in-memory candle list, per (symbol, timeframe)."""

    def __init__(self) -> None:
        self.series: dict[tuple[str, Timeframe], tuple[str, str, list[dict]]] = {}
        self.calls: list[tuple[str, Timeframe, pd.Timestamp, int]] = []
        self.failures: list[BaseException] = []

    def add(self, symbol: str, tf: Timeframe, candles: list[dict], *, forming: bool = True):
        name = {"EURUSD": "EUR_USD", "GBPNZD": "GBP_NZD", "GBPUSD": "GBP_USD"}[symbol]
        candles = [dict(c) for c in candles]
        if forming:
            last = pd.Timestamp(candles[-1]["time"]) + pd.Timedelta(minutes=tf.minutes)
            candles.append({**candles[-1], "time": _stamp(last), "complete": False})
        self.series[(symbol, tf)] = (name, GRANULARITY[tf], candles)

    def __call__(self, symbol: str, tf: Timeframe, start: pd.Timestamp, count: int) -> dict:
        self.calls.append((symbol, tf, start, count))
        if self.failures:
            raise self.failures.pop(0)
        name, gran, candles = self.series[(symbol, tf)]
        selected = [c for c in candles if pd.Timestamp(c["time"]) >= start][:count]
        return {"instrument": name, "granularity": gran, "candles": selected}


class CountingLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self) -> float:
        self.acquired += 1
        return 0.0


@pytest.fixture
def store(tmp_path: Path) -> DataStore:
    s = DataStore.initialise(tmp_path / "data")
    yield s
    s.close()


def _instant_retry(**kw) -> ReadRetry:
    ticks = iter(range(10_000))
    return ReadRetry(
        clock=lambda: float(next(ticks)) * 0.001, sleeper=lambda _s: None,
        jitter=lambda: 0.0, **kw,
    )


NOW = pd.Timestamp("2024-02-23T12:30:00Z")  # a Friday


def _h1_fake() -> FakeV20:
    fake = FakeV20()
    fake.add("EURUSD", Timeframe.H1, synthetic_candles("2024-02-05", "2024-02-23T12:00", Timeframe.H1))
    return fake


# ------------------------------------------------------------ granularity map


def test_granularity_names_map_to_timeframes_and_agree_with_the_provider() -> None:
    assert bf.timeframe_for_granularity("H1") is Timeframe.H1
    assert bf.timeframe_for_granularity("h4") is Timeframe.H4
    assert bf.timeframe_for_granularity("D") is Timeframe.D1
    for gran, tf in bf.BACKFILL_GRANULARITIES.items():
        assert GRANULARITY[tf] == gran
    with pytest.raises(bf.BackfillRefused, match="M5"):
        bf.timeframe_for_granularity("M5")
    with pytest.raises(bf.BackfillRefused, match="D1"):
        bf.timeframe_for_granularity("D1")  # v20 calls it D


# ------------------------------------------------------------------- planning


def test_an_unknown_symbol_refuses_the_whole_plan_naming_it(store: DataStore) -> None:
    with pytest.raises(bf.BackfillRefused, match="NOTREAL") as info:
        bf.plan_backfill(store, ["EURUSD", "NOTREAL", "XXXYYY"], ["H1"], now=NOW)
    assert "XXXYYY" in str(info.value)


def test_the_default_window_is_2005_to_the_last_closed_bar(store: DataStore) -> None:
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1", "H4", "D"], now=NOW)
    assert [(j.granularity, j.timeframe) for j in jobs] == [
        ("H1", Timeframe.H1), ("H4", Timeframe.H4), ("D", Timeframe.D1),
    ]
    assert all(j.start == pd.Timestamp("2005-01-01T00:00Z") and j.action == "fetch" for j in jobs)
    # end is EXCLUSIVE and is the current bar's open: every bar before it has closed.
    assert [j.end for j in jobs] == [
        pd.Timestamp("2024-02-23T12:00Z"),
        pd.Timestamp("2024-02-23T12:00Z"),
        pd.Timestamp("2024-02-23T00:00Z"),
    ]


def test_the_request_estimate_is_the_documented_upper_bound() -> None:
    now = pd.Timestamp("2026-09-30T12:00Z")
    job = bf.BackfillJob(
        instrument="EURUSD", oanda_name="EUR_USD", granularity="H1", timeframe=Timeframe.H1,
        start=bf.DEFAULT_START, end=now, action="fetch", requested_from=bf.DEFAULT_START,
    )
    slots = int((now - bf.DEFAULT_START) / pd.Timedelta(hours=1))
    assert job.slots == slots
    assert job.estimated_requests() == (slots - 2) // 4999 + 1 == 39


def test_a_budget_defers_whole_jobs_never_half_of_one(store: DataStore) -> None:
    jobs = bf.plan_backfill(store, ["EURUSD", "GBPUSD"], ["H1", "D"], now=NOW)
    costs = [j.estimated_requests() for j in jobs]
    run, deferred = bf.select_within_budget(jobs, costs[0] + costs[1])
    assert run == jobs[:2] and deferred == jobs[2:]
    with pytest.raises(bf.BackfillRefused):
        bf.select_within_budget(jobs, 0)


# ---------------------------------------------------------------------- paging


def _paging_fake(n_complete: int) -> FakeV20:
    fake = FakeV20()
    fake.add("EURUSD", Timeframe.H1,
             synthetic_candles("2024-02-05", "2024-02-20", Timeframe.H1)[:n_complete])
    return fake


def test_a_full_page_continues_and_a_short_page_stops(store: DataStore) -> None:
    fake = _paging_fake(19)  # pages of 10: [0..9] full, [9..18] full, [18] short
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    report = bf.run_backfill(jobs, fake, store, page=10)
    (outcome,) = report.outcomes
    assert outcome.status == "written", outcome.reason
    assert len(fake.calls) == 3 == outcome.pages
    candles = fake.series[("EURUSD", Timeframe.H1)][2]
    # Each page starts FROM the previous page's last candle.
    assert [c[2] for c in fake.calls] == [
        pd.Timestamp("2024-02-05T00:00Z"),
        pd.Timestamp(candles[9]["time"]),
        pd.Timestamp(candles[18]["time"]),
    ]
    assert outcome.new_bars == outcome.total_rows == 19
    assert outcome.counters["duplicates_dropped"] == 2  # one overlap per page boundary
    assert outcome.counters["incomplete_dropped"] == 1  # the forming candle
    frame = store.read(outcome.raw_version_id)
    assert frame.index.is_monotonic_increasing and not frame.index.has_duplicates


def test_paging_stops_once_the_window_end_is_reached(store: DataStore) -> None:
    fake = _paging_fake(200)
    end = pd.Timestamp(fake.series[("EURUSD", Timeframe.H1)][2][25]["time"])
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", end=end, now=NOW)
    report = bf.run_backfill(jobs, fake, store, page=10)
    (outcome,) = report.outcomes
    assert outcome.total_rows == 25  # candles [0, 25): end is exclusive
    assert len(fake.calls) == 3
    assert store.read(outcome.raw_version_id).index.max() < end


def test_overlapping_pages_are_deduplicated_but_a_changed_candle_is_refused(
    store: DataStore,
) -> None:
    fake = _paging_fake(30)
    candles = fake.series[("EURUSD", Timeframe.H1)][2]
    real_call = FakeV20.__call__

    def tampered(self, symbol, tf, start, count):  # the overlap candle changes on page 2
        payload = real_call(self, symbol, tf, start, count)
        if len(self.calls) == 2:
            first = dict(payload["candles"][0])
            first["mid"] = {**first["mid"], "c": "9.99999"}
            payload["candles"] = [first, *payload["candles"][1:]]
        return payload

    fake.__class__ = type("Tampered", (FakeV20,), {"__call__": tampered})
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    report = bf.run_backfill(jobs, fake, store, page=10)
    (outcome,) = report.outcomes
    assert outcome.status == "refused"
    assert "EURUSD H1" in outcome.reason and candles[9]["time"][:13] in outcome.reason
    assert store.catalogue.list_versions() == [], "a refused job writes nothing"


def test_non_monotonic_candles_are_refused_not_sorted(store: DataStore) -> None:
    fake = _paging_fake(8)
    name, gran, candles = fake.series[("EURUSD", Timeframe.H1)]
    candles[2], candles[3] = candles[3], candles[2]
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    (outcome,) = bf.run_backfill(jobs, fake, store, page=10).outcomes
    assert outcome.status == "refused" and "strictly increasing" in outcome.reason


# ------------------------------------------------- recorded practice fixtures


def test_the_mba_fixture_drops_the_forming_candle_and_stores_mid_only(store: DataStore) -> None:
    payload = _fixture("candles_EUR_USD_H4_MBA_count12.json")
    fake = FakeV20()
    fake.add("EURUSD", Timeframe.H4, payload["candles"], forming=False)  # last is complete:false
    now = pd.Timestamp("2026-09-30T00:30Z")
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H4"], start="2026-09-28", now=now)
    (outcome,) = bf.run_backfill(jobs, fake, store).outcomes
    assert outcome.status == "written", outcome.reason
    assert outcome.counters["incomplete_dropped"] == 1 and outcome.total_rows == 11
    frame = store.read(outcome.raw_version_id)
    assert not any(c.startswith(("bid_", "ask_")) for c in frame.columns)
    assert "volume" not in frame.columns and "tick_volume" in frame.columns
    assert frame["tick_volume"].iloc[0] == 14838
    assert frame["close"].iloc[0] == pytest.approx(1.13864)  # the MID close, not bid 1.13856
    assert set(frame["price_basis"]) == {"mid"}


def test_the_2005_fixture_continues_into_a_synthetic_second_page(store: DataStore) -> None:
    first = _fixture("candles_EUR_USD_H4_M_from2005.json")["candles"]
    rest = synthetic_candles("2005-01-03T20:00", "2005-01-15", Timeframe.H4, first_open=1.3479)
    fake = FakeV20()
    fake.add("EURUSD", Timeframe.H4, first + rest)
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H4"], now=pd.Timestamp("2005-01-20T00:00Z"))
    assert jobs[0].start == pd.Timestamp("2005-01-01T00:00Z")
    (outcome,) = bf.run_backfill(jobs, fake, store, page=5).outcomes
    assert outcome.status == "written", outcome.reason
    frame = store.read(outcome.raw_version_id)
    assert frame.index[0] == pd.Timestamp("2005-01-03T00:00Z")
    assert frame["close"].iloc[0] == pytest.approx(1.34115)
    assert outcome.total_rows == len(first) + len(rest)
    assert outcome.pages >= 2 and fake.calls[1][2] == pd.Timestamp("2005-01-03T16:00Z")


# ------------------------------------------------------------ what is written


def test_written_versions_carry_source_basis_and_lineage(store: DataStore) -> None:
    fake = _h1_fake()
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    (outcome,) = bf.run_backfill(jobs, fake, store, page=100).outcomes
    raw = store.catalogue.resolve(outcome.raw_version_id)
    assert raw.kind is DatasetKind.RAW and raw.source == "oanda_practice"
    assert raw.price_basis is PriceBasis.MID
    (step,) = raw.lineage
    assert step.operation == "oanda_v20_candles_backfill"
    p = step.parameters
    assert p["endpoint"] == "GET /v3/instruments/EUR_USD/candles"
    assert p["host"] == "api-fxpractice.oanda.com"
    assert p["params"] == {
        "granularity": "H1", "price": "M", "dailyAlignment": 0,
        "alignmentTimezone": "UTC", "count": 100,
    }
    assert p["page_count"] == outcome.pages == len(fake.calls)
    assert p["window"] == {"from": "2024-02-05T00:00:00+00:00",
                           "to_exclusive": "2024-02-23T12:00:00+00:00"}
    assert raw.metadata["source_identifier"].startswith(
        "https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/candles?granularity=H1&price=M"
    )

    validated = store.catalogue.resolve(outcome.validated_version_id)
    assert validated.kind is DatasetKind.VALIDATED
    assert validated.parent_ids == (raw.version_id,)
    assert validated.lineage[-1].operation == "validate"
    assert validated.lineage[-1].parameters["repairs_applied"] == []
    assert validated.source == "oanda_practice" and validated.price_basis is PriceBasis.MID


def test_the_validated_version_passes_integrity_and_is_what_read_latest_returns(
    store: DataStore,
) -> None:
    fake = _h1_fake()
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    (outcome,) = bf.run_backfill(jobs, fake, store, page=100).outcomes
    assert outcome.quality == "validated", outcome.integrity_summary
    frame, version = store.read_latest("EURUSD", Timeframe.H1)  # raises if dirty
    assert version.version_id == outcome.validated_version_id
    assert version.integrity_report["is_clean"] is True
    assert validate(frame, calendar=FX_CALENDAR).is_clean


def test_d_is_stored_as_d1(store: DataStore) -> None:
    fake = FakeV20()
    fake.add("EURUSD", Timeframe.D1, synthetic_candles("2024-01-08", "2024-02-23", Timeframe.D1))
    jobs = bf.plan_backfill(store, ["EURUSD"], ["D"], start="2024-01-08", now=NOW)
    assert jobs[0].timeframe is Timeframe.D1 and jobs[0].end == pd.Timestamp("2024-02-23T00:00Z")
    (outcome,) = bf.run_backfill(jobs, fake, store).outcomes
    assert outcome.status == "written", outcome.reason
    assert fake.calls[0][1] is Timeframe.D1
    frame, version = store.read_latest("EURUSD", "D1")
    assert version.timeframe is Timeframe.D1 and set(frame["timeframe"]) == {"D1"}
    assert (frame.index.hour == 0).all()
    assert version.lineage[0].parameters["params"]["granularity"] == "D"


# ------------------------------------------------------ resume and idempotence


def test_resume_fetches_only_after_the_last_stored_bar_and_stores_the_whole_series(
    store: DataStore,
) -> None:
    full = synthetic_candles("2024-02-05", "2024-02-23T12:00", Timeframe.H1)
    cut = pd.Timestamp("2024-02-14T00:00Z")
    fake = FakeV20()
    fake.add("EURUSD", Timeframe.H1, [c for c in full if pd.Timestamp(c["time"]) < cut])
    first_jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=cut)
    (first,) = bf.run_backfill(first_jobs, fake, store).outcomes

    fake2 = FakeV20()
    fake2.add("EURUSD", Timeframe.H1, full)
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    (job,) = jobs
    assert job.action == "resume" and job.prior_raw_id == first.raw_version_id
    assert job.start == pd.Timestamp("2024-02-13T23:00Z") + pd.Timedelta(hours=1)
    (second,) = bf.run_backfill(jobs, fake2, store).outcomes
    assert fake2.calls[0][2] == job.start
    assert second.status == "written"
    assert second.new_bars == len(full) - first.total_rows
    assert second.total_rows == len(full)
    raw = store.catalogue.resolve(second.raw_version_id)
    assert raw.lineage[0].parameters["extends_version"] == first.raw_version_id
    assert raw.lineage[0].parameters["requested_from"] == "2024-02-05T00:00:00+00:00"
    frame, latest = store.read_latest("EURUSD", Timeframe.H1)
    assert latest.version_id == second.validated_version_id
    assert len(frame) == len(full), "the newest version is the whole series, not a tail"


def test_an_earlier_start_than_the_stored_one_refetches_in_full(store: DataStore) -> None:
    fake = _h1_fake()
    bf.run_backfill(
        bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-12", now=NOW), fake, store
    )
    (job,) = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    assert job.action == "refetch" and job.start == pd.Timestamp("2024-02-05T00:00Z")


def test_a_rerun_after_completion_writes_nothing_and_says_why(store: DataStore) -> None:
    fake = _h1_fake()
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    bf.run_backfill(jobs, fake, store)
    before = len(store.catalogue.list_versions())
    calls = len(fake.calls)

    again = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    assert [j.action for j in again] == ["current"]
    assert again[0].estimated_requests() == 0
    report = bf.run_backfill(again, fake, store)
    (outcome,) = report.outcomes
    assert outcome.status == "skipped" and report.requests == 0
    assert "last stored bar 2024-02-23T11:00:00+00:00" in outcome.reason
    assert len(fake.calls) == calls and len(store.catalogue.list_versions()) == before


def test_a_weekend_resume_with_nothing_new_writes_nothing(store: DataStore) -> None:
    fake = _h1_fake()  # history ends Friday 2024-02-23 11:00
    bf.run_backfill(
        bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW), fake, store
    )
    before = len(store.catalogue.list_versions())
    sunday = pd.Timestamp("2024-02-25T12:00Z")
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=sunday)
    assert jobs[0].action == "resume"
    (outcome,) = bf.run_backfill(jobs, fake, store).outcomes
    assert outcome.status == "skipped" and "nothing written" in outcome.reason
    assert len(store.catalogue.list_versions()) == before


def test_a_missing_validated_version_is_rebuilt_from_the_stored_raw(store: DataStore) -> None:
    fake = _h1_fake()
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    (first,) = bf.run_backfill(jobs, fake, store).outcomes
    with store.catalogue._session_factory() as session:  # simulate a crash after write_raw
        from fiboki.data.versioning import DatasetVersionRow, LineageEdgeRow

        session.query(LineageEdgeRow).delete()
        session.query(DatasetVersionRow).filter_by(kind="validated").delete()
        session.commit()
    (job,) = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    assert job.action == "validate_only"
    (outcome,) = bf.run_backfill([job], fake, store).outcomes
    assert outcome.status == "written"
    assert outcome.validated_version_id == first.validated_version_id
    assert store.read_latest("EURUSD", Timeframe.H1)[1].version_id == first.validated_version_id


# ------------------------------------------------------- retry, limiter, refusal


def test_a_429_is_retried_then_succeeds_through_the_limiter(store: DataStore) -> None:
    fake = _h1_fake()
    fake.failures = [CandleHttpError(429, "u", retry_after="0")]
    limiter = CountingLimiter()
    retry = _instant_retry()
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    report = bf.run_backfill(jobs, fake, store, limiter=limiter, retry=retry)
    (outcome,) = report.outcomes
    assert outcome.status == "written", outcome.reason
    assert len(retry.history) == 1 and "429" in retry.history[0].error
    assert len(fake.calls) == outcome.pages + 1
    assert limiter.acquired == len(fake.calls) == report.requests, "every attempt is paced"


def test_a_401_refuses_the_job_naming_it_without_retrying(store: DataStore) -> None:
    fake = FakeV20()
    fake.add("EURUSD", Timeframe.H1, synthetic_candles("2024-02-05", "2024-02-10", Timeframe.H1))
    fake.add("GBPUSD", Timeframe.H1, synthetic_candles("2024-02-05", "2024-02-10", Timeframe.H1))
    fake.failures = [CandleHttpError(401, "u", body={"errorMessage": "Insufficient auth"})]
    retry = _instant_retry()
    jobs = bf.plan_backfill(store, ["EURUSD", "GBPUSD"], ["H1"], start="2024-02-05", now=NOW)
    report = bf.run_backfill(jobs, fake, store, retry=retry)
    eur, gbp = report.outcomes
    assert eur.status == "refused" and "EURUSD H1" in eur.reason and "HTTP 401" in eur.reason
    assert retry.history == [], "a 4xx other than 429 is never retried"
    assert [c[0] for c in fake.calls].count("EURUSD") == 1
    assert gbp.status == "written", "one refused job does not stop the batch"
    assert report.refused == [eur]
    assert not store.catalogue.list_versions(instrument="EURUSD")


def test_retries_are_bounded(store: DataStore) -> None:
    fake = _h1_fake()
    fake.failures = [CandleHttpError(503, "u") for _ in range(10)]
    retry = _instant_retry(max_attempts=3)
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    (outcome,) = bf.run_backfill(jobs, fake, store, retry=retry).outcomes
    assert outcome.status == "refused" and "HTTP 503" in outcome.reason
    assert len(fake.calls) == 3


def test_a_response_for_the_wrong_instrument_is_refused(store: DataStore) -> None:
    fake = _h1_fake()
    name, gran, candles = fake.series[("EURUSD", Timeframe.H1)]
    fake.series[("EURUSD", Timeframe.H1)] = ("GBP_USD", gran, candles)
    jobs = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    (outcome,) = bf.run_backfill(jobs, fake, store).outcomes
    assert outcome.status == "refused" and "GBP_USD" in outcome.reason


# ------------------------------------------------ read_latest: what research sees


def _write_histdata_bid(store: DataStore) -> str:
    candles = synthetic_candles("2024-02-05", "2024-02-10", Timeframe.H1, seed=3)
    frame = pd.DataFrame(
        {k: [float(c["mid"][k[0]]) for c in candles] for k in ("open", "high", "low", "close")},
        index=pd.DatetimeIndex([pd.Timestamp(c["time"]) for c in candles]),
    )
    frame = canonical_frame(frame, instrument="EURUSD", timeframe="H1", price_basis="bid")
    meta = describe_frame(frame, source="histdata", source_identifier="t", timezone_of_origin="EST")
    raw = store.write_raw(frame, meta)
    val = store.write_canonical(
        frame, meta, source_version=raw.version,
        transformation=TransformationStep(operation="validate"),
    )
    return val.version_id


def test_the_plan_warns_when_research_will_switch_source_and_read_latest_does(
    store: DataStore,
) -> None:
    hist = _write_histdata_bid(store)
    assert store.read_latest("EURUSD", Timeframe.H1)[1].version_id == hist
    (job,) = bf.plan_backfill(store, ["EURUSD"], ["H1"], start="2024-02-05", now=NOW)
    (warning,) = job.warnings
    assert hist in warning and "histdata" in warning and "research switches" in warning
    (outcome,) = bf.run_backfill([job], _h1_fake(), store).outcomes
    _, latest = store.read_latest("EURUSD", Timeframe.H1)
    # Newest by created_at, regardless of source: the OANDA MID version now shadows HistData,
    # which is still stored and resolvable by id.
    assert latest.version_id == outcome.validated_version_id
    assert latest.source == "oanda_practice"
    assert store.catalogue.resolve(hist).source == "histdata"


# ------------------------------------------------------------ the fetch glue


class _FakeTransport:
    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status, self.body = status, body or {"instrument": "EUR_USD",
                                                  "granularity": "D", "candles": []}
        self.seen: list[tuple[str, str, dict]] = []

    def request(self, method, url, *, headers, body=None, timeout=10.0):
        self.seen.append((method, url, dict(headers)))
        return HttpResponse(status=self.status, body=self.body, headers={"Retry-After": "1"})


def test_make_v20_fetch_requests_mid_utc_aligned_from_plus_count() -> None:
    transport = _FakeTransport()
    provider = OandaCandlesProvider(api_token="tok", http_client=TransportHttpClient(transport))
    fetch = bf.make_v20_fetch(provider)
    fetch("EURUSD", Timeframe.D1, pd.Timestamp("2005-01-01T00:00Z"), 5000)
    ((method, url, headers),) = transport.seen
    parts = urlsplit(url)
    assert method == "GET" and parts.hostname == "api-fxpractice.oanda.com"
    assert parts.path == "/v3/instruments/EUR_USD/candles"
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q == {
        "granularity": "D", "price": "M", "dailyAlignment": "0",
        "alignmentTimezone": "UTC", "count": "5000", "from": "2005-01-01T00:00:00+00:00",
    }
    assert headers["Authorization"] == "Bearer tok"


def test_make_v20_fetch_raises_a_classifiable_status_error() -> None:
    from fiboki.broker.retry import is_retryable

    for status, retryable in ((429, True), (503, True), (401, False), (400, False)):
        provider = OandaCandlesProvider(
            api_token="tok", http_client=TransportHttpClient(_FakeTransport(status, {}))
        )
        with pytest.raises(CandleHttpError) as info:
            bf.make_v20_fetch(provider)("EURUSD", Timeframe.H1, pd.Timestamp("2024-01-01T00:00Z"), 10)
        assert info.value.status == status and is_retryable(info.value) is retryable


def test_make_v20_fetch_refuses_live_host_missing_token_and_non_mid() -> None:
    client = TransportHttpClient(_FakeTransport())
    with pytest.raises(ProviderError, match="practice host only"):
        bf.make_v20_fetch(OandaCandlesProvider(
            api_token="t", host="https://api-fxtrade.oanda.com", http_client=client))
    with pytest.raises(ProviderError, match="practice host only"):
        bf.make_v20_fetch(OandaCandlesProvider(
            api_token="t", host="https://api-fxpractice.oanda.com.evil.example",
            http_client=client))
    with pytest.raises(AuthenticationRequired):
        bf.make_v20_fetch(OandaCandlesProvider(api_token=None, http_client=client))
    with pytest.raises(ProviderError, match="MID"):
        bf.make_v20_fetch(OandaCandlesProvider(
            api_token="t", price_basis=PriceBasis.BID, http_client=client))


def test_assert_backfill_params_refuses_new_york_alignment_and_other_bases() -> None:
    provider = OandaCandlesProvider(api_token="t")
    _, params = provider.request_params(
        "EURUSD", Timeframe.H4, start=pd.Timestamp("2005-01-01T00:00Z"), count=5000)
    bf.assert_backfill_params(params)
    _, ny = provider.request_params(
        "EURUSD", Timeframe.H4, start=pd.Timestamp("2005-01-01T00:00Z"), count=5000, align_utc=False)
    with pytest.raises(ProviderError, match="dailyAlignment"):
        bf.assert_backfill_params(ny)
    with pytest.raises(ProviderError, match="price"):
        bf.assert_backfill_params({**params, "price": "MBA"})


# ----------------------------------------------------------------------- CLI


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FIBOKI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FIBOKI_OANDA_PRACTICE_TOKEN", raising=False)
    monkeypatch.delenv("FIBOKI_DATA_ROOT", raising=False)
    root = tmp_path / "data"
    DataStore.initialise(root).close()
    return root


def test_cli_dry_run_prints_the_plan_without_network_or_token(cli_env, capsys) -> None:
    code = main([
        "data", "oanda-backfill", "--instruments", "GBPNZD,EURUSD", "-g", "H1", "-g", "D",
        "--data-root", str(cli_env), "--dry-run", "--json",
    ])
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    payload = json.loads(out[out.index("{"):])
    assert payload["dry_run"] is True
    assert [(r["instrument"], r["granularity"]) for r in payload["plan"]] == [
        ("GBPNZD", "H1"), ("GBPNZD", "D"), ("EURUSD", "H1"), ("EURUSD", "D"),
    ]
    assert all(r["from"].startswith("2005-01-01") for r in payload["plan"])
    assert payload["estimated_requests"] > 0


def test_cli_refuses_an_unknown_symbol_up_front(cli_env, capsys) -> None:
    code = main(["data", "oanda-backfill", "-i", "EURUSD", "-i", "NOPE",
                 "--data-root", str(cli_env), "--dry-run"])
    assert code == EXIT_MISUSE
    assert "NOPE" in capsys.readouterr().err


def test_cli_refuses_to_run_without_the_practice_token(cli_env, capsys) -> None:
    code = main(["data", "oanda-backfill", "-i", "EURUSD", "-g", "D",
                 "--data-root", str(cli_env)])
    assert code == EXIT_MISUSE
    assert "FIBOKI_OANDA_PRACTICE_TOKEN is not set" in capsys.readouterr().err


def test_cli_needs_a_marked_data_root(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FIBOKI_DATA_ROOT", raising=False)
    assert main(["data", "oanda-backfill", "-i", "EURUSD", "--dry-run"]) == EXIT_MISUSE
    (tmp_path / "unmarked").mkdir()
    assert main(["data", "oanda-backfill", "-i", "EURUSD", "--dry-run",
                 "--data-root", str(tmp_path / "unmarked")]) == EXIT_MISUSE


def test_cli_exit_code_is_nonzero_when_a_job_is_refused(cli_env, monkeypatch) -> None:
    """The real command, with the transport's client swapped for a 401 at the socket."""
    import httpx

    from fiboki.broker import http_transport

    monkeypatch.setenv("FIBOKI_OANDA_PRACTICE_TOKEN", "practice-token")
    real_init = http_transport.HttpxTransport.__init__
    hosts: list[set[str]] = []

    def init(self, *, allowed_hosts, client=None, user_agent="x"):
        hosts.append(set(allowed_hosts))
        mock = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"errorMessage": "no"})))
        real_init(self, allowed_hosts=allowed_hosts, client=mock, user_agent=user_agent)

    monkeypatch.setattr(http_transport.HttpxTransport, "__init__", init)
    code = main(["data", "oanda-backfill", "-i", "EURUSD", "-g", "D",
                 "--data-root", str(cli_env), "--requests-per-second", "10", "--json"])
    assert code == EXIT_FAIL
    assert hosts == [{"api-fxpractice.oanda.com"}]
