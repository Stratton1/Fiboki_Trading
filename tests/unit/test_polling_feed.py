"""``OandaPollingBarFeed``: boundary-aligned, venue-final, never gap-filled.

Deterministic by construction: a fake clock whose sleep advances it, a retry
policy on that clock, and ``RecordedTransport`` behind ``TransportHttpClient``.
Nothing here opens a socket.
"""
from __future__ import annotations

from urllib.parse import parse_qs

import pandas as pd
import pytest

from fiboki.broker.oanda import HttpResponse, RecordedTransport
from fiboki.broker.retry import ReadRetry
from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.data.providers.base import ProviderError
from fiboki.data.providers.oanda import OandaCandlesProvider
from fiboki.obs.alerts import AlertDispatcher, AlertEvent, MemoryChannel
from fiboki.workers.base import reset_log_once
from fiboki.workers.feeds import (
    OandaPollingBarFeed,
    PollingFeedConfig,
    TransportHttpClient,
)
from fiboki.workers.runtime import RiskContextBuilder

CANDLES = "GET /v3/instruments/EUR_USD/candles"
GBP_CANDLES = "GET /v3/instruments/GBP_USD/candles"
H1 = pd.Timedelta(hours=1)
# Wednesday 7 January 2026: FX open all day, winter time.
WED_10 = pd.Timestamp("2026-01-07T10:00:00Z")


class FakeClock:
    def __init__(self, start: pd.Timestamp) -> None:
        self.now = pd.Timestamp(start)
        self.slept: list[float] = []

    def __call__(self) -> pd.Timestamp:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(float(seconds))
        self.now = self.now + pd.Timedelta(seconds=float(seconds))

    def mono(self) -> float:
        return self.now.value / 1e9


def _candle(start: pd.Timestamp, *, complete: bool = True, price: float = 1.1) -> dict:
    p = f"{price:.5f}"
    hi = f"{price + 0.001:.5f}"
    lo = f"{price - 0.001:.5f}"
    return {
        "complete": complete,
        "volume": 10,
        "time": start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        "mid": {"o": p, "h": hi, "l": lo, "c": p},
    }


def _payload(starts, *, forming: pd.Timestamp | None = None, incomplete=()) -> HttpResponse:
    candles = [_candle(s, complete=s not in incomplete) for s in starts]
    if forming is not None:
        candles.append(_candle(forming, complete=False))
    return HttpResponse(200, {"instrument": "EUR_USD", "granularity": "H1", "candles": candles})


def _hours(end: pd.Timestamp, n: int) -> list[pd.Timestamp]:
    """``n`` hourly bar STARTS ending with ``end``."""
    return [end - H1 * (n - 1 - i) for i in range(n)]


def _feed(fixtures, clock: FakeClock, *, instruments=("EURUSD",), dispatcher=None, **cfg):
    reset_log_once()
    transport = RecordedTransport(fixtures)
    provider = OandaCandlesProvider(api_token="t", http_client=TransportHttpClient(transport))
    params = {"instruments": tuple(instruments), "timeframe": Timeframe.H1, "warmup_bars": 50}
    params.update(cfg)
    feed = OandaPollingBarFeed(
        provider,
        PollingFeedConfig(**params),
        clock=clock,
        sleeper=clock.sleep,
        read_retry=ReadRetry(clock=clock.mono, sleeper=clock.sleep, jitter=lambda: 0.0),
        dispatcher=dispatcher,
    )
    return feed, transport


# ------------------------------------------------------------------ timing


def test_it_never_wakes_between_the_boundary_and_the_offset() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=1))
    feed, transport = _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5), forming=WED_10)]}, clock)
    assert feed.wait_until_due() is True
    assert clock.now == WED_10 + pd.Timedelta(seconds=5)
    assert transport.calls == []  # waiting fetches nothing
    feed.poll()
    assert len(transport.calls) == 1


def test_a_direct_poll_inside_the_offset_window_still_waits_for_it() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=2))
    feed, _ = _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5))]}, clock)
    feed.poll()
    assert clock.now == WED_10 + pd.Timedelta(seconds=5)


def test_a_single_wait_is_bounded_so_the_lease_and_heartbeat_survive() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, _ = _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5))]}, clock, max_block_s=30.0)
    feed.poll()
    clock.now = WED_10 + pd.Timedelta(minutes=10)
    assert feed.wait_until_due() is False
    assert clock.now == WED_10 + pd.Timedelta(minutes=10, seconds=30)
    assert feed.next_due() == WED_10 + H1 + pd.Timedelta(seconds=5)


def test_a_stop_request_ends_the_wait() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(minutes=10))
    feed, _ = _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5))]}, clock)
    feed._last_boundary = WED_10
    assert feed.wait_until_due(should_stop=lambda: True) is False


def test_H4_boundaries_are_epoch_anchored_like_the_research_frames() -> None:
    clock = FakeClock(pd.Timestamp("2026-01-07T13:00:05Z"))
    starts = [pd.Timestamp("2026-01-07T04:00:00Z"), pd.Timestamp("2026-01-07T08:00:00Z")]
    feed, transport = _feed(
        {CANDLES: [_payload(starts)]}, clock, timeframe=Timeframe.H4, offset_s=5.0
    )
    batch = feed.poll()
    assert feed.reports[-1].expected_start == pd.Timestamp("2026-01-07T08:00:00Z")
    assert batch.frames["EURUSD"].timestamp == pd.Timestamp("2026-01-07T08:00:00Z")
    query = parse_qs(transport.calls[0]["query"])
    assert query["dailyAlignment"] == ["0"]
    assert query["alignmentTimezone"] == ["UTC"]


# --------------------------------------------------------------- finality


def test_the_venues_complete_flag_is_authoritative() -> None:
    """09:00 has closed by the clock, but the venue still says complete:false.
    It is NOT treated as closed; it is re-polled until the venue says so."""
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    first = _payload(_hours(WED_10 - H1, 5), incomplete={WED_10 - H1})
    second = _payload(_hours(WED_10 - H1, 5))
    feed, transport = _feed({CANDLES: [first, second]}, clock)
    batch = feed.poll()
    assert len(transport.calls) == 2
    assert feed.reports[-1].repolls == 1
    assert batch.closed_instruments == frozenset({"EURUSD"})
    assert batch.frames["EURUSD"].timestamp == WED_10 - H1


def test_the_forming_candle_is_never_delivered() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, _ = _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5), forming=WED_10)]}, clock)
    batch = feed.poll()
    assert batch.frames["EURUSD"].timestamp == WED_10 - H1
    assert feed.history("EURUSD").index[-1] == WED_10 - H1


def test_a_candle_still_missing_after_the_grace_raises_a_data_quality_event() -> None:
    channel = MemoryChannel()
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    # The venue keeps answering with 08:00 as its newest closed bar.
    feed, transport = _feed(
        {CANDLES: [_payload(_hours(WED_10 - 2 * H1, 5))]},
        clock,
        dispatcher=AlertDispatcher([channel]),
    )
    batch = feed.poll()
    # One fetch plus three re-polls, five seconds apart.
    assert len(transport.calls) == 4
    assert clock.now == WED_10 + pd.Timedelta(seconds=20)
    alert = next(a for a in channel.sent if a.event is AlertEvent.DATA_QUALITY_DEFECT)
    assert alert.context["instrument"] == "EURUSD"
    assert "NOT gap-filled" in alert.message
    assert feed.reports[-1].late == ("EURUSD",)
    # Not evaluated for this boundary... the 08:00 bar is new to the feed, so
    # it is delivered and the GATEWAY decides whether it is still tradeable.
    assert batch.frames["EURUSD"].timestamp == WED_10 - 2 * H1


def test_no_alert_inside_the_grace_window() -> None:
    channel = MemoryChannel()
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, _ = _feed(
        {CANDLES: [_payload(_hours(WED_10 - 2 * H1, 5))]},
        clock,
        dispatcher=AlertDispatcher([channel]),
        repoll_attempts=1,
        late_candle_grace_s=20.0,
    )
    feed.poll()
    assert feed.reports[-1].missing == ("EURUSD",)
    assert AlertEvent.DATA_QUALITY_DEFECT not in channel.events()


def test_it_never_gap_fills() -> None:
    """A bar the venue never sent stays missing: no row, no NaN, no carry."""
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    before = _hours(WED_10 - 2 * H1, 5)  # ... 08:00
    after = [*before, WED_10]  # 10:00 arrives; 09:00 never does
    feed, _ = _feed({CANDLES: [_payload(before), _payload(before), _payload(before),
                               _payload(before), _payload(after)]}, clock)
    feed.poll()
    clock.now = WED_10 + H1 + pd.Timedelta(seconds=5)
    batch = feed.poll()
    held = feed.history("EURUSD")
    assert WED_10 - H1 not in held.index
    assert not held[["open", "high", "low", "close"]].isna().any().any()
    assert batch.frames["EURUSD"].timestamp == WED_10


def test_a_bar_is_delivered_once() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, _ = _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5))]}, clock, repoll_attempts=0)
    assert feed.poll().closed_instruments == frozenset({"EURUSD"})
    clock.now = WED_10 + H1 + pd.Timedelta(seconds=5)
    # The venue has nothing new: nothing is offered for evaluation twice.
    assert feed.poll().closed_instruments == frozenset()


def test_the_warmup_asks_for_history_then_only_what_is_behind() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, transport = _feed(
        {CANDLES: [_payload(_hours(WED_10 - H1, 5)), _payload(_hours(WED_10, 6))]}, clock
    )
    feed.poll()
    clock.now = WED_10 + H1 + pd.Timedelta(seconds=5)
    feed.poll()
    counts = [parse_qs(c["query"])["count"] for c in transport.calls]
    # Behind by one bar, plus one of overlap, plus the forming candle.
    assert counts == [["50"], ["3"]]


# ---------------------------------------------------------- calendar/ages


def test_a_weekend_is_closed_not_stale_and_is_not_fetched() -> None:
    friday = pd.Timestamp("2026-01-09T21:00:05Z")
    clock = FakeClock(friday)
    fri_bars = _hours(pd.Timestamp("2026-01-09T20:00:00Z"), 5)
    feed, transport = _feed({CANDLES: [_payload(fri_bars)]}, clock)
    feed.poll()
    calls = len(transport.calls)
    clock.now = pd.Timestamp("2026-01-10T12:00:05Z")  # Saturday
    batch = feed.poll()
    assert len(transport.calls) == calls  # nothing can exist, nothing is asked
    assert batch.market_closed == frozenset({"EURUSD"})
    assert batch.closed_instruments == frozenset()
    assert feed.reports[-1].late == ()
    # Age is still REPORTED (the metric wants it), measured from the close.
    assert batch.ages["EURUSD"] == pytest.approx((clock.now - (fri_bars[-1] + H1)).total_seconds())


def test_age_is_measured_from_the_bar_close_not_its_start() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, _ = _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5))]}, clock)
    batch = feed.poll()
    assert batch.ages["EURUSD"] == pytest.approx(5.0)


def test_the_close_time_reaches_the_risk_context_builder() -> None:
    """The feed supplies the INPUT; the gateway's data_freshness owns the check."""
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, _ = _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5))]}, clock)
    builder = RiskContextBuilder(adapter=object(), clock=clock, fx=IdentityFxSource())
    feed.attach(builder)
    feed.poll()
    view = builder.market_view(get_instrument("EURUSD"), clock())
    assert view.last_bar_time == WED_10  # the CLOSE of the 09:00 bar
    assert view.quote_time == WED_10
    assert view.market_open is True
    assert view.mid_price == pytest.approx(1.1)
    saturday = pd.Timestamp("2026-01-10T12:00:00Z")
    assert builder.market_view(get_instrument("EURUSD"), saturday).market_open is False


def test_an_unregistered_instrument_is_refused_at_construction() -> None:
    with pytest.raises(KeyError):
        _feed({CANDLES: [_payload(_hours(WED_10 - H1, 5))]}, FakeClock(WED_10),
              instruments=("NOTREAL",))


# ------------------------------------------------------------- transport


def test_a_transient_503_is_retried_through_the_read_policy() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, transport = _feed(
        {CANDLES: [HttpResponse(503, {}), _payload(_hours(WED_10 - H1, 5))]}, clock
    )
    batch = feed.poll()
    assert batch.closed_instruments == frozenset({"EURUSD"})
    assert len(transport.calls) == 2
    assert len(feed.read_retry.history) == 1


def test_a_4xx_candle_response_is_not_retried() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, transport = _feed({CANDLES: [HttpResponse(400, {"errorMessage": "bad"})]}, clock)
    with pytest.raises(ProviderError, match="every candle fetch failed"):
        feed.poll()
    assert len(transport.calls) == 1


def test_an_outage_raises_and_re_arms_the_same_boundary() -> None:
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    feed, _ = _feed({CANDLES: [HttpResponse(503, {})]}, clock)
    with pytest.raises(ProviderError, match="every candle fetch failed"):
        feed.poll()
    # The next cycle is allowed to try THIS boundary again, not wait an hour.
    assert feed.next_due() == WED_10 + pd.Timedelta(seconds=5)


def test_one_instrument_failing_does_not_blind_the_others() -> None:
    channel = MemoryChannel()
    clock = FakeClock(WED_10 + pd.Timedelta(seconds=5))
    gbp = HttpResponse(
        200,
        {
            "instrument": "GBP_USD",
            "granularity": "H1",
            "candles": [_candle(s, price=1.27) for s in _hours(WED_10 - H1, 5)],
        },
    )
    feed, _ = _feed(
        {CANDLES: [HttpResponse(503, {})], GBP_CANDLES: [gbp]},
        clock,
        instruments=("EURUSD", "GBPUSD"),
        dispatcher=AlertDispatcher([channel]),
    )
    batch = feed.poll()
    assert batch.closed_instruments == frozenset({"GBPUSD"})
    assert "EURUSD" in feed.reports[-1].errors
    alert = next(a for a in channel.sent if a.event is AlertEvent.DATA_QUALITY_DEFECT)
    assert "unfetchable" in alert.message
