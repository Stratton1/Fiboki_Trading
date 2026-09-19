"""Recorder crash-safety and replay.

The recorder runs unattended for months and will be killed mid-write. These
tests simulate that by truncating a segment at a byte boundary inside the last
record, then assert everything written before the kill is still readable and the
torn tail is reported rather than swallowed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from fiboki.data.recorder import (
    AppendOnlyLog,
    LogCorruption,
    LogReader,
    QuoteRecord,
    QuoteRecorder,
    QuoteReplayReader,
    SimulatedQuoteFeed,
    decode_record,
    encode_record,
    quotes_to_bars,
    spread_profile,
)
from fiboki.data.schema import MarketState

START = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def a_quote(i: int = 0, instrument: str = "EURUSD") -> QuoteRecord:
    return QuoteRecord(
        timestamp=START + timedelta(milliseconds=250 * i),
        instrument=instrument,
        bid=1.1000 + i * 1e-5,
        ask=1.1001 + i * 1e-5,
        provider="test",
        market_state=MarketState.OPEN,
        latency_ms=13.5,
        tick_volume=1,
        quote_id=f"q-{i}",
        sequence=i,
    )


# ------------------------------------------------------- framing


def test_record_framing_roundtrip():
    payload = {"a": 1, "b": "two", "c": [1, 2, 3]}
    assert decode_record(encode_record(payload)) == payload


def test_crc_mismatch_is_detected():
    blob = bytearray(encode_record({"a": 1}))
    blob[-3] = ord("9") if blob[-3] != ord("9") else ord("8")
    with pytest.raises(ValueError, match="crc32|length"):
        decode_record(bytes(blob))


def test_missing_newline_is_a_torn_write():
    blob = encode_record({"a": 1})[:-1]
    with pytest.raises(ValueError, match="torn write"):
        decode_record(blob)


# ------------------------------------------------- crash safety


def test_kill_mid_write_leaves_all_prior_records_readable(tmp_path):
    """Truncate the segment inside the final record — the crash case."""
    directory = tmp_path / "quotes"
    recorder = QuoteRecorder(directory, fsync_every=1)
    for i in range(50):
        recorder.record(a_quote(i))
    recorder.log.flush()
    segment = recorder.log.current_path
    # Simulate SIGKILL: the last record is only partly on disk.
    raw = segment.read_bytes()
    last_newline = raw.rindex(b"\n", 0, len(raw) - 1)
    torn_at = last_newline + 1 + 20
    segment.write_bytes(raw[:torn_at])

    quotes, report = QuoteReplayReader(directory).records()
    assert len(quotes) == 49, "records written before the crash must survive"
    assert report.truncated_tail is True
    assert not report.is_clean
    assert not report.corrupt_interior_lines
    assert [q.sequence for q in quotes] == list(range(49))


def test_kill_between_records_loses_nothing(tmp_path):
    directory = tmp_path / "quotes"
    recorder = QuoteRecorder(directory, fsync_every=1)
    for i in range(30):
        recorder.record(a_quote(i))
    recorder.log.flush()
    # No truncation at all: a clean kill on a record boundary.
    quotes, report = QuoteReplayReader(directory).records()
    assert len(quotes) == 30
    assert report.is_clean


def test_corruption_in_the_middle_is_not_treated_as_a_crash(tmp_path):
    directory = tmp_path / "quotes"
    recorder = QuoteRecorder(directory, fsync_every=1)
    for i in range(20):
        recorder.record(a_quote(i))
    recorder.close()

    segment = sorted(directory.glob("seg-*.ndjson"))[0]
    lines = segment.read_bytes().splitlines(keepends=True)
    lines[5] = b"this is not a record\n"
    segment.write_bytes(b"".join(lines))

    with pytest.raises(LogCorruption, match="not a crash"):
        QuoteReplayReader(directory).records()

    quotes, report = QuoteReplayReader(directory).records(
        tolerate_interior_corruption=True
    )
    assert len(quotes) == 19
    assert report.corrupt_interior_lines


def test_segment_rotation_is_atomic_and_manifested(tmp_path):
    directory = tmp_path / "quotes"
    recorder = QuoteRecorder(directory, max_records_per_segment=10, fsync_every=1)
    for i in range(35):
        recorder.record(a_quote(i))
    recorder.close()

    segments = sorted(directory.glob("seg-*.ndjson"))
    manifests = sorted(directory.glob("seg-*.manifest.json"))
    assert len(segments) == 4
    assert len(manifests) == 4
    assert not list(directory.glob("*.tmp")), "a tmp manifest was left behind"

    total = 0
    for m in manifests:
        data = json.loads(m.read_text())
        assert data["stream"] == "quotes"
        total += data["records"]
    assert total == 35

    quotes, report = QuoteReplayReader(directory).records()
    assert len(quotes) == 35
    assert report.segments_read == 4
    assert [q.sequence for q in quotes] == list(range(35))


def test_reader_reads_segments_in_order(tmp_path):
    directory = tmp_path / "quotes"
    recorder = QuoteRecorder(directory, max_records_per_segment=3, fsync_every=0)
    for i in range(25):
        recorder.record(a_quote(i))
    recorder.close()
    quotes, _ = QuoteReplayReader(directory).records()
    assert [q.sequence for q in quotes] == list(range(25))


def test_append_only_log_never_rewrites_a_closed_segment(tmp_path):
    log = AppendOnlyLog(tmp_path / "log", max_records_per_segment=5, fsync_every=1)
    for i in range(12):
        log.append({"i": i})
    first = sorted((tmp_path / "log").glob("seg-*.ndjson"))[0]
    before = first.read_bytes()
    for i in range(12, 20):
        log.append({"i": i})
    log.close()
    assert first.read_bytes() == before


def test_reopening_a_log_directory_appends_a_new_segment(tmp_path):
    directory = tmp_path / "log"
    first = AppendOnlyLog(directory, fsync_every=1)
    first.append({"i": 0})
    first.close()
    second = AppendOnlyLog(directory, fsync_every=1)
    second.append({"i": 1})
    second.close()
    records, report = LogReader(directory).read_all()
    assert [r["i"] for r in records] == [0, 1]
    assert report.segments_read == 2


# ------------------------------------------------------ quote content


def test_quote_derives_mid_and_spread():
    q = a_quote(0)
    assert q.mid == pytest.approx((q.bid + q.ask) / 2)
    assert q.spread == pytest.approx(0.0001)
    assert q.spread_pips() == pytest.approx(1.0)


def test_quote_payload_roundtrip_preserves_everything():
    q = QuoteRecord(
        timestamp=START,
        instrument="XAUUSD",
        bid=2000.10,
        ask=2000.40,
        provider="ig",
        market_state=MarketState.HALTED,
        session_open=False,
        latency_ms=88.0,
        bid_size=3.0,
        ask_size=4.0,
        tick_volume=7,
        trade_price=2000.25,
        trade_size=0.5,
        depth=((2000.05, 10.0, 2000.45, 12.0),),
        quote_id="abc",
        broker_status="degraded",
        sequence=99,
    )
    back = QuoteRecord.from_payload(q.to_payload())
    assert back == q


def test_replay_filters_by_instrument_and_time(tmp_path):
    directory = tmp_path / "quotes"
    recorder = QuoteRecorder(directory, fsync_every=0)
    for i in range(20):
        recorder.record(a_quote(i, "EURUSD"))
        recorder.record(a_quote(i, "XAUUSD"))
    recorder.close()

    reader = QuoteReplayReader(directory)
    eur, _ = reader.records(instrument="EURUSD")
    assert len(eur) == 20
    windowed, _ = reader.records(
        instrument="EURUSD", start=START + timedelta(seconds=1)
    )
    assert len(windowed) == 16


def test_replay_frame_has_derived_columns(tmp_path):
    directory = tmp_path / "quotes"
    recorder = QuoteRecorder(directory, fsync_every=0)
    recorder.record_many(a_quote(i) for i in range(40))
    recorder.close()
    frame, report = QuoteReplayReader(directory).frame()
    assert report.is_clean
    assert list(frame.index.names) == ["timestamp"]
    assert {"bid", "ask", "mid", "spread", "spread_pips", "latency_ms"} <= set(frame.columns)
    assert (frame["ask"] > frame["bid"]).all()


# --------------------------------------------------- simulated feed


def test_simulated_feed_is_deterministic():
    feed = SimulatedQuoteFeed(seed=42)
    a = list(feed.stream(["EURUSD"], limit=100))
    b = list(SimulatedQuoteFeed(seed=42).stream(["EURUSD"], limit=100))
    assert [q.bid for q in a] == [q.bid for q in b]
    assert [q.ask for q in a] == [q.ask for q in b]


def test_simulated_feed_changes_with_the_seed():
    a = list(SimulatedQuoteFeed(seed=1).stream(["EURUSD"], limit=50))
    b = list(SimulatedQuoteFeed(seed=2).stream(["EURUSD"], limit=50))
    assert [q.bid for q in a] != [q.bid for q in b]


def test_simulated_feed_widens_the_spread_out_of_session():
    open_feed = SimulatedQuoteFeed(
        seed=5, start=datetime(2026, 1, 6, 12, 0, tzinfo=timezone.utc)  # Tuesday
    )
    closed_feed = SimulatedQuoteFeed(
        seed=5, start=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)  # Saturday
    )
    open_spread = pd.Series([q.spread for q in open_feed.stream(["EURUSD"], limit=200)])
    closed_spread = pd.Series(
        [q.spread for q in closed_feed.stream(["EURUSD"], limit=200)]
    )
    assert closed_spread.median() > open_spread.median() * 3


def test_recorder_runs_a_feed_end_to_end(tmp_path):
    directory = tmp_path / "quotes"
    with QuoteRecorder(directory, max_records_per_segment=200, fsync_every=0) as rec:
        written = rec.run(SimulatedQuoteFeed(seed=3), ["EURUSD", "XAUUSD"], limit=500)
    assert written == 500
    quotes, report = QuoteReplayReader(directory).records()
    assert len(quotes) == 500
    assert report.is_clean
    assert {q.instrument for q in quotes} == {"EURUSD", "XAUUSD"}


# ------------------------------------------------------- derivations


def test_spread_profile_by_hour(tmp_path):
    directory = tmp_path / "quotes"
    with QuoteRecorder(directory, fsync_every=0) as rec:
        rec.run(
            SimulatedQuoteFeed(
                seed=9, start=datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc),
                interval_ms=60_000,
            ),
            ["EURUSD"],
            limit=1440,
        )
    frame, _ = QuoteReplayReader(directory).frame()
    profile = spread_profile(frame)
    assert len(profile) == 24
    assert (profile["count"] > 0).all()
    assert (profile["p90_pips"] >= profile["median_pips"]).all()


def test_quotes_to_bars_respects_the_chosen_side(tmp_path):
    directory = tmp_path / "quotes"
    with QuoteRecorder(directory, fsync_every=0) as rec:
        rec.record_many(a_quote(i) for i in range(1200))  # 250ms apart -> 5 minutes
    frame, _ = QuoteReplayReader(directory).frame()
    bid_bars = quotes_to_bars(frame, timeframe_minutes=1, side="bid")
    ask_bars = quotes_to_bars(frame, timeframe_minutes=1, side="ask")
    assert len(bid_bars) == 5
    assert (ask_bars["close"] > bid_bars["close"]).all()
    assert (bid_bars["high"] >= bid_bars["low"]).all()


def test_quotes_to_bars_rejects_an_unknown_side(tmp_path):
    directory = tmp_path / "quotes"
    with QuoteRecorder(directory, fsync_every=0) as rec:
        rec.record_many(a_quote(i) for i in range(10))
    frame, _ = QuoteReplayReader(directory).frame()
    with pytest.raises(ValueError, match="bid/ask/mid"):
        quotes_to_bars(frame, side="last")
