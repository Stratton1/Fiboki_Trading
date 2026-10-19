"""Recorded-quote views (data/quote_view.py) and monitor statistics (marketstate/quote_stats.py)."""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from fiboki.data.quote_view import QuoteTail
from fiboki.data.recorder import MarketState, QuoteRecord, QuoteRecorder
from fiboki.marketstate.quote_stats import bucket_closes, currency_strength, instrument_stats

T0 = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


def q(minutes: float, symbol: str, mid: float, spread: float = 0.0002, provider: str = "oanda_practice_pricing") -> QuoteRecord:
    return QuoteRecord(
        timestamp=T0 + timedelta(minutes=minutes), instrument=symbol, bid=mid - spread / 2,
        ask=mid + spread / 2, provider=provider, market_state=MarketState.OPEN,
    )


def test_window_reads_newest_segments_and_filters(tmp_path):
    with QuoteRecorder(tmp_path, max_records_per_segment=3) as rec:
        for i in range(10):
            rec.record(q(i * 10, "EURUSD" if i % 2 == 0 else "GBPUSD", 1.1 + i * 0.001))
    tail = QuoteTail(tmp_path)
    win = tail.window(T0 + timedelta(minutes=45), T0 + timedelta(hours=2))
    assert win.instruments == ["EURUSD", "GBPUSD"]
    assert [r.mid for r in win.by_instrument["EURUSD"]] == sorted(r.mid for r in win.by_instrument["EURUSD"])
    times = sorted(r.timestamp for rows in win.by_instrument.values() for r in rows)
    assert times[0] == T0 + timedelta(minutes=50) and times[-1] == T0 + timedelta(minutes=90)
    # Stopped early: the oldest segment (minutes 0-20) was not needed.
    assert win.segments_read < len(list(tmp_path.glob("seg-*.ndjson")))
    only = tail.window(T0, T0 + timedelta(hours=2), instruments=["gbpusd"])
    assert only.instruments == ["GBPUSD"] and len(only.by_instrument["GBPUSD"]) == 5


def test_window_is_incremental_and_reports_damage(tmp_path):
    rec = QuoteRecorder(tmp_path)
    rec.record(q(0, "EURUSD", 1.1))
    tail = QuoteTail(tmp_path)
    assert tail.window(T0 - timedelta(hours=1), T0 + timedelta(hours=1)).records_read == 1
    rec.record(q(1, "EURUSD", 1.2))
    assert tail.window(T0 - timedelta(hours=1), T0 + timedelta(hours=1)).records_read == 2
    rec.close()
    seg = sorted(tmp_path.glob("seg-*.ndjson"))[-1]
    with seg.open("ab") as fh:
        fh.write(b"deadbeef 5 {bad}\n")  # a corrupt but terminated line
    win = QuoteTail(tmp_path).window(T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    assert win.records_read == 2 and win.corrupt_lines == 1


def test_an_absent_log_says_so(tmp_path):
    win = QuoteTail(tmp_path / "nope").window(T0, T0 + timedelta(hours=1))
    assert win.absent and win.records_read == 0 and win.newest_write is None


def test_naive_times_are_refused(tmp_path):
    with pytest.raises(ValueError):
        QuoteTail(tmp_path).window(datetime(2026, 1, 1), T0)


def test_bucket_closes_leave_gaps_as_none():
    starts, closes = bucket_closes([q(1, "X", 1.0), q(2, "X", 2.0), q(31, "X", 3.0)], T0, T0 + timedelta(hours=1), 4)
    assert closes == (2.0, None, 3.0, None)
    assert starts[1] == T0 + timedelta(minutes=15)


def test_instrument_stats_by_hand():
    quotes = [q(0, "X", 100.0), q(20, "X", 102.0), q(40, "X", 99.0), q(59, "X", 101.0)]
    s = instrument_stats(quotes, T0, T0 + timedelta(hours=1), buckets=3)
    assert s.n_quotes == 4 and s.first_mid == 100.0 and s.last_mid == 101.0
    assert s.change_pct == pytest.approx(1.0)
    assert s.range_pct == pytest.approx((102.0 - 99.0) / 101.0 * 100)
    # closes per 20-minute bucket: 100, 102, 101 -> log returns, sample sd, * sqrt(2)
    r = [math.log(102 / 100), math.log(101 / 102)]
    mean = sum(r) / 2
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 1)
    assert s.realised_vol_pct == pytest.approx(sd * math.sqrt(2) * 100)


def test_instrument_stats_is_deterministic_and_honest_about_absence():
    quotes = [q(i, "X", 1 + i / 1000) for i in range(30)]
    a = instrument_stats(quotes, T0, T0 + timedelta(hours=1))
    b = instrument_stats(list(quotes), T0, T0 + timedelta(hours=1))
    assert a == b
    empty = instrument_stats([], T0, T0 + timedelta(hours=1))
    assert empty.change_pct is None and empty.last_mid is None and set(empty.spark) == {None}


def test_currency_strength_by_hand():
    pairs = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "EURGBP": ("EUR", "GBP"), "XAUUSD": ("XAU", "USD")}
    scores = currency_strength({"EURUSD": 1.0, "GBPUSD": -0.5, "EURGBP": None, "XAUUSD": 3.0}, {k: v for k, v in pairs.items() if k != "XAUUSD"})
    by = {s.currency: (round(s.score_pct, 6), s.pairs) for s in scores}
    assert by == {"EUR": (1.0, 1), "USD": (round((-1.0 + 0.5) / 2, 6), 2), "GBP": (-0.5, 1)}
    assert [s.currency for s in scores] == ["EUR", "USD", "GBP"]
