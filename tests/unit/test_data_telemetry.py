"""Execution telemetry: slippage, latency, partial fills, divergence."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fiboki.core.enums import Direction, ExecutionMode, OrderType
from fiboki.data.telemetry import (
    ExecutionOutcome,
    ExecutionTelemetryRecord,
    MarketRegime,
    TelemetryReader,
    TelemetryStore,
    divergence_report,
    slippage_summary,
)

T0 = datetime(2026, 1, 6, 12, 0, tzinfo=UTC)


def an_event(
    i: int = 0,
    *,
    direction: Direction = Direction.LONG,
    instrument: str = "EURUSD",
    requested: float = 1.10000,
    filled: float | None = 1.10003,
    requested_size: float = 10_000.0,
    filled_size: float | None = None,
    outcome: ExecutionOutcome = ExecutionOutcome.FILLED,
) -> ExecutionTelemetryRecord:
    return ExecutionTelemetryRecord(
        event_id=f"ev-{i}",
        strategy_id="ichimoku_v1",
        instrument=instrument,
        timeframe="H1",
        direction=direction,
        execution_mode=ExecutionMode.PAPER,
        order_type=OrderType.MARKET,
        outcome=outcome,
        signal_ts=T0 + timedelta(hours=i),
        decision_ts=T0 + timedelta(hours=i, milliseconds=40),
        submit_ts=T0 + timedelta(hours=i, milliseconds=55),
        broker_ack_ts=T0 + timedelta(hours=i, milliseconds=110),
        fill_ts=T0 + timedelta(hours=i, milliseconds=140),
        requested_price=requested,
        filled_price=filled,
        requested_size=requested_size,
        filled_size=requested_size if filled_size is None else filled_size,
        spread_at_decision=0.00009,
        market_regime=MarketRegime.TRENDING,
        dataset_version_id="ds_abc123",
    )


# ------------------------------------------------------------ slippage


def test_long_slippage_is_positive_when_filled_worse():
    e = an_event(requested=1.10000, filled=1.10003)
    assert e.slippage_price == pytest.approx(0.00003)
    assert e.slippage_pips == pytest.approx(0.3)


def test_short_slippage_is_positive_when_filled_lower():
    """A short filled *below* the request is worse for the short."""
    e = an_event(direction=Direction.SHORT, requested=1.10000, filled=1.09997)
    assert e.slippage_price == pytest.approx(0.00003)
    assert e.slippage_pips == pytest.approx(0.3)


def test_favourable_slippage_is_negative():
    e = an_event(requested=1.10000, filled=1.09998)
    assert e.slippage_price < 0


def test_slippage_is_none_without_a_fill():
    e = an_event(filled=None, outcome=ExecutionOutcome.REJECTED)
    assert e.slippage_price is None
    assert e.slippage_pips is None


def test_slippage_uses_the_instruments_pip_size():
    e = an_event(instrument="XAUUSD", requested=2000.00, filled=2000.05)
    assert e.slippage_pips == pytest.approx(5.0)  # XAU pip = 0.01


# ------------------------------------------------------------- latency


def test_latency_stages_are_measured_separately():
    e = an_event()
    assert e.decision_latency_ms == pytest.approx(40.0)
    assert e.submit_latency_ms == pytest.approx(15.0)
    assert e.ack_latency_ms == pytest.approx(55.0)
    assert e.fill_latency_ms == pytest.approx(85.0)
    assert e.total_latency_ms == pytest.approx(140.0)


def test_missing_stage_gives_none_not_zero():
    e = ExecutionTelemetryRecord(
        event_id="x",
        strategy_id="s",
        instrument="EURUSD",
        timeframe="H1",
        direction=Direction.LONG,
        execution_mode=ExecutionMode.BACKTEST,
        order_type=OrderType.MARKET,
        outcome=ExecutionOutcome.REJECTED,
        signal_ts=T0,
        decision_ts=T0 + timedelta(milliseconds=10),
    )
    assert e.submit_latency_ms is None
    assert e.total_latency_ms is None


def test_partial_fill_ratio():
    e = an_event(requested_size=10_000.0, filled_size=6_000.0,
                 outcome=ExecutionOutcome.PARTIAL)
    assert e.fill_ratio == pytest.approx(0.6)


# ------------------------------------------------------------- storage


def test_payload_roundtrip(tmp_path):
    e = an_event(3)
    back = ExecutionTelemetryRecord.from_payload(e.to_payload())
    assert back.event_id == e.event_id
    assert back.direction is e.direction
    assert back.market_regime is e.market_regime
    assert back.dataset_version_id == e.dataset_version_id
    assert back.slippage_pips == pytest.approx(e.slippage_pips)


def test_store_is_append_only_and_replayable(tmp_path):
    directory = tmp_path / "telemetry"
    with TelemetryStore(directory, fsync_every=1) as store:
        for i in range(25):
            store.record(an_event(i))
    records, report = TelemetryReader(directory).records()
    assert len(records) == 25
    assert report.is_clean
    assert [r.event_id for r in records] == [f"ev-{i}" for i in range(25)]


def test_store_survives_a_torn_tail(tmp_path):
    directory = tmp_path / "telemetry"
    store = TelemetryStore(directory, fsync_every=1)
    for i in range(10):
        store.record(an_event(i))
    store.log.flush()
    segment = store.log.current_path
    raw = segment.read_bytes()
    segment.write_bytes(raw[: raw.rindex(b"\n", 0, len(raw) - 1) + 1 + 30])
    records, report = TelemetryReader(directory).records()
    assert len(records) == 9
    assert report.truncated_tail


def test_frame_has_the_columns_an_operator_needs(tmp_path):
    directory = tmp_path / "telemetry"
    with TelemetryStore(directory, fsync_every=0) as store:
        for i in range(12):
            store.record(an_event(i))
    frame, _ = TelemetryReader(directory).frame()
    for col in (
        "slippage_pips", "fill_ratio", "spread_at_decision", "market_regime",
        "total_latency_ms", "dataset_version_id",
    ):
        assert col in frame.columns
    assert frame.index.name == "signal_ts"


# ------------------------------------------------------------ analysis


def _frame(tmp_path, events):
    directory = tmp_path / "telemetry"
    with TelemetryStore(directory, fsync_every=0) as store:
        for e in events:
            store.record(e)
    frame, _ = TelemetryReader(directory).frame()
    return frame


def test_slippage_summary_counts_fills_and_rejects(tmp_path):
    events = [an_event(i) for i in range(8)]
    events += [
        an_event(100 + i, filled=None, outcome=ExecutionOutcome.REJECTED)
        for i in range(2)
    ]
    summary = slippage_summary(_frame(tmp_path, events))
    row = summary.loc["EURUSD"]
    assert row["attempts"] == 10
    assert row["fills"] == 8
    assert row["reject_rate"] == pytest.approx(0.2)
    assert row["median_slippage_pips"] == pytest.approx(0.3)


def test_slippage_summary_on_empty_frame_returns_empty_not_zeros():
    import pandas as pd

    out = slippage_summary(pd.DataFrame())
    assert out.empty
    assert "median_slippage_pips" in out.columns


def test_divergence_report_shows_the_excess_cost(tmp_path):
    """Live slippage of 0.9 pips against an assumed 0.9-pip spread is not free."""
    events = [an_event(i, requested=1.10000, filled=1.10009) for i in range(10)]
    frame = _frame(tmp_path, events)
    report = divergence_report(frame, assumed_spread_pips={"EURUSD": 0.9})
    row = report.loc["EURUSD"]
    assert row["realised_slippage_pips"] == pytest.approx(0.9)
    assert row["assumed_spread_pips"] == pytest.approx(0.9)
    # Backtest charged half the spread (0.45); reality cost 0.9.
    assert row["excess_cost_pips"] == pytest.approx(0.45)
    assert row["n"] == 10


def test_divergence_report_can_show_the_backtest_being_conservative(tmp_path):
    events = [an_event(i, requested=1.10000, filled=1.10001) for i in range(5)]
    frame = _frame(tmp_path, events)
    report = divergence_report(frame, assumed_spread_pips={"EURUSD": 0.9})
    assert report.loc["EURUSD", "excess_cost_pips"] < 0
