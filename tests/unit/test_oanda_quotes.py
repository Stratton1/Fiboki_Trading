"""The executable-price recorder's OANDA practice feed (USER_ACTIONS P2).

Pins: a changed quote is recorded once with the venue's time, an unchanged one
is not recorded again, a non-tradeable quote is still recorded (as CLOSED), a
failed poll records nothing and is reported, the loop stops when told, the
records round-trip through the crash-safe log, and the practice-only host rule
is inherited from the pricing client.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.broker.oanda import OANDA_LIVE_HOST, HttpResponse, OandaHostError, RecordedTransport
from fiboki.broker.oanda_pricing import OandaPricingSpreadSource
from fiboki.broker.oanda_quotes import DEFAULT_RECORDED_INSTRUMENTS, OandaPricingQuoteFeed
from fiboki.broker.retry import ReadRetry
from fiboki.data.recorder import MarketState, QuoteRecorder, QuoteReplayReader

ACCOUNT = "101-004-1-001"
KEY = f"GET /v3/accounts/{ACCOUNT}/pricing"
T0 = pd.Timestamp("2026-10-21T10:00:00Z")


def _price(inst: str, bid: str, ask: str, at: pd.Timestamp, tradeable: bool = True) -> dict:
    return {
        "instrument": inst,
        "time": at.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        "tradeable": tradeable,
        "bids": [{"price": bid, "liquidity": 1000000}],
        "asks": [{"price": ask, "liquidity": 1000000}],
    }


def _feed(responses, *, received: pd.Timestamp | None = None, polls: int = 99):
    received = T0 + pd.Timedelta(seconds=1) if received is None else received
    transport = RecordedTransport({KEY: responses})
    clock = {"now": received}
    source = OandaPricingSpreadSource(
        transport=transport, account_id=ACCOUNT, api_token="t",
        read_retry=ReadRetry(sleeper=lambda _s: None, jitter=lambda: 0.0, max_attempts=1),
        clock=lambda: clock["now"],
    )
    sleeps: list[float] = []
    reports: list = []
    feed = OandaPricingQuoteFeed(
        source=source, interval_s=30.0, sleeper=sleeps.append,
        should_stop=lambda: len(reports) >= polls, on_poll=reports.append,
    )
    return feed, transport, sleeps, reports


def test_changed_quotes_are_recorded_once_with_the_venue_time() -> None:
    t1 = T0 + pd.Timedelta(seconds=30)
    first = HttpResponse(200, {"prices": [_price("EUR_USD", "1.10000", "1.10012", T0),
                                          _price("XAU_USD", "2400.10", "2400.45", T0)]})
    # Second poll: EURUSD unchanged, XAUUSD moved.
    second = HttpResponse(200, {"prices": [_price("EUR_USD", "1.10000", "1.10012", T0),
                                           _price("XAU_USD", "2400.20", "2400.60", t1)]})
    feed, transport, sleeps, reports = _feed([first, second], polls=2)
    records = list(feed.stream(["EURUSD", "XAUUSD"]))
    assert [(r.instrument, r.timestamp) for r in records] == [
        ("EURUSD", T0.to_pydatetime()), ("XAUUSD", T0.to_pydatetime()), ("XAUUSD", t1.to_pydatetime()),
    ]
    assert records[0].bid == 1.1 and records[0].ask == 1.10012
    assert records[0].provider == "oanda_practice_pricing"
    assert records[0].latency_ms == pytest.approx(1000.0)
    assert [r.sequence for r in records] == [0, 1, 2]
    assert [rep.recorded for rep in reports] == [2, 1]
    assert sleeps == [30.0]  # one sleep between two polls, none after the last
    assert len(transport.calls) == 2


def test_a_non_tradeable_quote_is_recorded_as_closed() -> None:
    body = HttpResponse(200, {"prices": [_price("EUR_USD", "1.0990", "1.1010", T0, tradeable=False)]})
    feed, _t, _s, _r = _feed([body], polls=1)
    (record,) = list(feed.stream(["EURUSD"]))
    assert record.market_state is MarketState.CLOSED and record.session_open is False
    assert record.broker_status == "not_tradeable"


def test_a_failed_poll_records_nothing_and_says_why() -> None:
    feed, _t, _s, reports = _feed([HttpResponse(401, {"errorMessage": "Insufficient authorization"})], polls=1)
    assert list(feed.stream(["EURUSD"])) == []
    (report,) = reports
    assert report.received == 0 and report.recorded == 0
    assert "401" in report.error
    assert "Bearer" not in report.as_row()["error"]  # the auth header never reaches a report


def test_the_limit_stops_the_stream() -> None:
    body = HttpResponse(200, {"prices": [_price("EUR_USD", "1.1", "1.2", T0), _price("GBP_USD", "1.3", "1.4", T0)]})
    feed, _t, _s, _r = _feed([body])
    assert len(list(feed.stream(["EURUSD", "GBPUSD"], limit=1))) == 1


def test_records_round_trip_through_the_crash_safe_log(tmp_path) -> None:
    body = HttpResponse(200, {"prices": [_price("EUR_USD", "1.10000", "1.10012", T0)]})
    feed, _t, _s, _r = _feed([body], polls=1)
    with QuoteRecorder(tmp_path) as recorder:
        assert recorder.run(feed, ["EURUSD"]) == 1
    frame, report = QuoteReplayReader(tmp_path).frame()
    assert report.is_clean and report.records_read == 1
    assert list(frame["instrument"]) == ["EURUSD"]
    assert frame["spread"].iloc[0] == pytest.approx(0.00012)
    assert str(frame.index[0]) == str(T0)


def test_polling_faster_than_five_seconds_is_refused() -> None:
    feed, *_ = _feed([])
    with pytest.raises(ValueError):
        OandaPricingQuoteFeed(source=feed.source, interval_s=1.0)


def test_the_live_host_is_refused_by_the_client_it_wraps() -> None:
    with pytest.raises(OandaHostError):
        OandaPricingSpreadSource(
            transport=RecordedTransport({}), account_id=ACCOUNT, api_token="t",
            base_url=f"https://{OANDA_LIVE_HOST}",
        )


def test_the_default_universe_is_the_research_series() -> None:
    assert len(DEFAULT_RECORDED_INSTRUMENTS) == len(set(DEFAULT_RECORDED_INSTRUMENTS)) == 20
    assert {"EURUSD", "GBPUSD", "XAUUSD", "US500"} <= set(DEFAULT_RECORDED_INSTRUMENTS)


# ------------------------------------------------------------------ the CLI


def test_quotes_status_fails_when_nothing_was_ever_written(tmp_path) -> None:
    from fiboki.cli import main

    assert main(["quotes", "status", "--state-dir", str(tmp_path)]) == 1


def test_quotes_status_passes_on_a_fresh_segment_and_fails_on_a_stale_one(tmp_path) -> None:
    import os
    import time

    from fiboki.cli import main

    body = HttpResponse(200, {"prices": [_price("EUR_USD", "1.1", "1.2", T0)]})
    feed, *_ = _feed([body], polls=1)
    with QuoteRecorder(tmp_path / "quotes") as recorder:
        recorder.run(feed, ["EURUSD"])
    assert main(["quotes", "status", "--state-dir", str(tmp_path)]) == 0
    old = time.time() - 3600
    for seg in (tmp_path / "quotes").glob("seg-*.ndjson"):
        os.utime(seg, (old, old))
    assert main(["quotes", "status", "--state-dir", str(tmp_path), "--max-age", "600"]) == 1


def test_quotes_record_refuses_without_a_token(tmp_path, monkeypatch) -> None:
    from fiboki.broker.http_transport import MissingCredential
    from fiboki.cli import main

    monkeypatch.delenv("FIBOKI_OANDA_PRACTICE_TOKEN", raising=False)
    with pytest.raises(MissingCredential):
        main(["quotes", "record", "--once", "--state-dir", str(tmp_path)])
    assert not (tmp_path / "quotes").exists()
