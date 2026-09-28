"""The agent's clock is pinned by ToolContext.as_of.

A model-supplied date later than the pin is clamped (not refused) and the
clamp is visible in the tool output; an absent date defaults to the pin; only
candles CLOSED by the pin are visible; with no pin nothing changes.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from fiboki.agents.tools import REGISTRY, InMemoryBarSource, ToolContext, ToolExecutionError
from fiboki.core.enums import Direction, ExecutionMode, OrderType, Timeframe
from fiboki.data.telemetry import ExecutionOutcome, ExecutionTelemetryRecord, TelemetryStore
from fiboki.research.artefacts import ResearchStore
from fiboki.strategy.registry import StrategyRegistry
from tests.agents_fixtures import trending_bars

PIN = datetime(2024, 2, 1, 0, 30, tzinfo=UTC)
#: H1 bars are left-labelled: the 00:00 bar closes at 01:00, after the pin, so
#: the last visible bar is the one stamped 23:00 on the previous day.
LAST_CLOSED = pd.Timestamp("2024-01-31 23:00", tz="UTC")
LAST_IN_SOURCE = pd.Timestamp("2024-03-03 11:00", tz="UTC")


@pytest.fixture(scope="module")
def bars() -> InMemoryBarSource:
    frame = trending_bars(instrument="EURUSD", timeframe=Timeframe.H1, periods=1500)
    assert frame.index[-1] == LAST_IN_SOURCE
    return InMemoryBarSource({("EURUSD", Timeframe.H1.value): frame})


def _ctx(bars: InMemoryBarSource, as_of: datetime | None = None, **kw: Any) -> ToolContext:
    return ToolContext(
        research=ResearchStore(), strategies=StrategyRegistry(), bars=bars, as_of=as_of, **kw
    )


def _call(name: str, ctx: ToolContext, **inputs: Any) -> Any:
    spec = REGISTRY.get(name)
    out = spec.handler(ctx, spec.input_model(**inputs))
    return spec.output_model.model_validate(out.model_dump())


# ------------------------------------------------------------ ToolContext


def test_tz_naive_as_of_is_rejected(bars: InMemoryBarSource) -> None:
    with pytest.raises(ValueError, match="timezone-naive"):
        _ctx(bars, datetime(2024, 2, 1, 0, 30))


def test_non_datetime_as_of_is_rejected(bars: InMemoryBarSource) -> None:
    with pytest.raises(TypeError, match="must be a datetime"):
        _ctx(bars, "2024-02-01T00:30:00Z")  # type: ignore[arg-type]


def test_aware_as_of_is_normalised_to_utc(bars: InMemoryBarSource) -> None:
    plus_two = timezone(timedelta(hours=2))
    ctx = _ctx(bars, datetime(2024, 2, 1, 2, 30, tzinfo=plus_two))
    assert ctx.as_of == PIN
    assert ctx.as_of.utcoffset() == timedelta(0)


def test_the_pin_survives_the_session_rebinding(bars: InMemoryBarSource) -> None:
    # AgentSession rebinds agent_id and role with dataclasses.replace.
    rebound = replace(_ctx(bars, PIN), agent_id="a@wf", role="quant_researcher")
    assert rebound.as_of == PIN


# ------------------------------------------------------------ market data


def test_later_end_is_clamped_and_the_clamp_is_recorded(bars: InMemoryBarSource) -> None:
    out = _call(
        "query_market_data", _ctx(bars, PIN),
        instrument="EURUSD", timeframe="H1", end="2024-03-01 00:00",
    )
    assert out.as_of_clamped is True
    assert out.effective_as_of == PIN.isoformat()
    assert pd.Timestamp(out.last_timestamp) == LAST_CLOSED


def test_absent_end_defaults_to_the_pin(bars: InMemoryBarSource) -> None:
    out = _call("query_market_data", _ctx(bars, PIN), instrument="EURUSD", timeframe="H1")
    assert out.as_of_clamped is False
    assert out.effective_as_of == PIN.isoformat()
    assert pd.Timestamp(out.last_timestamp) == LAST_CLOSED


def test_earlier_end_is_respected_and_not_flagged(bars: InMemoryBarSource) -> None:
    out = _call(
        "query_market_data", _ctx(bars, PIN),
        instrument="EURUSD", timeframe="H1", end="2024-01-20 12:00",
    )
    assert out.as_of_clamped is False
    assert pd.Timestamp(out.last_timestamp) == pd.Timestamp("2024-01-20 12:00", tz="UTC")


def test_tail_rows_never_include_a_bar_after_the_pin(bars: InMemoryBarSource) -> None:
    out = _call(
        "query_market_data", _ctx(bars, PIN),
        instrument="EURUSD", timeframe="H1", end="2030-01-01", tail_bars=5,
    )
    assert all(
        pd.Timestamp(r.timestamp) + pd.Timedelta(hours=1) <= pd.Timestamp(PIN) for r in out.tail
    )


def test_start_after_the_pin_is_clamped_and_the_error_says_so(bars: InMemoryBarSource) -> None:
    with pytest.raises(ToolExecutionError, match="clamped"):
        _call(
            "query_market_data", _ctx(bars, PIN),
            instrument="EURUSD", timeframe="H1", start="2024-02-15",
        )


def test_unpinned_market_data_is_unchanged(bars: InMemoryBarSource) -> None:
    out = _call(
        "query_market_data", _ctx(bars, None),
        instrument="EURUSD", timeframe="H1", end="2030-01-01",
    )
    assert out.as_of_clamped is False
    assert out.effective_as_of is None
    assert pd.Timestamp(out.last_timestamp) == LAST_IN_SOURCE
    assert out.n_bars == 1500


# ---------------------------------------------------------------- regime


def test_regime_as_of_later_than_the_pin_is_clamped(bars: InMemoryBarSource) -> None:
    pinned = _call(
        "query_regime", _ctx(bars, PIN),
        instrument="EURUSD", timeframe="H1", as_of="2024-03-01 00:00",
        include_distribution=False,
    )
    assert pinned.as_of_clamped is True
    assert pd.Timestamp(pinned.as_of) == pd.Timestamp(PIN)
    assert pinned.effective_as_of == pinned.as_of
    # The pinned label is exactly what an unpinned read of the closed history
    # produces: nothing after the pin reached it.
    reference = _call(
        "query_regime", _ctx(bars, None),
        instrument="EURUSD", timeframe="H1", end=str(LAST_CLOSED), as_of=str(LAST_CLOSED),
        include_distribution=False,
    )
    assert pinned.regime_key == reference.regime_key
    assert pinned.n_bars_used == reference.n_bars_used


def test_regime_absent_as_of_defaults_to_the_pin(bars: InMemoryBarSource) -> None:
    out = _call(
        "query_regime", _ctx(bars, PIN),
        instrument="EURUSD", timeframe="H1", include_distribution=False,
    )
    assert out.as_of_clamped is False
    assert pd.Timestamp(out.as_of) == pd.Timestamp(PIN)


def test_unpinned_regime_is_unchanged(bars: InMemoryBarSource) -> None:
    out = _call(
        "query_regime", _ctx(bars, None),
        instrument="EURUSD", timeframe="H1", include_distribution=False,
    )
    assert pd.Timestamp(out.as_of) == LAST_IN_SOURCE
    assert out.as_of_clamped is False
    assert out.effective_as_of is None


# ---------------------------------------------------------- data quality


def test_data_quality_window_is_clamped(bars: InMemoryBarSource) -> None:
    out = _call(
        "query_data_quality", _ctx(bars, PIN),
        instrument="EURUSD", timeframe="H1", end="2024-03-01",
    )
    assert out.as_of_clamped is True
    assert out.effective_as_of == PIN.isoformat()
    assert pd.Timestamp(out.last_timestamp) == LAST_CLOSED


def test_unpinned_data_quality_is_unchanged(bars: InMemoryBarSource) -> None:
    out = _call("query_data_quality", _ctx(bars, None), instrument="EURUSD", timeframe="H1")
    assert out.effective_as_of is None
    assert pd.Timestamp(out.last_timestamp) == LAST_IN_SOURCE


# -------------------------------------------------------------- telemetry


def _telemetry(tmp_path: Path) -> Path:
    directory = tmp_path / "telemetry"
    signals = [
        ("before", PIN - timedelta(hours=5), PIN - timedelta(hours=4)),
        ("filled_after", PIN - timedelta(minutes=10), PIN + timedelta(minutes=5)),
        ("after", PIN + timedelta(hours=1), PIN + timedelta(hours=1, minutes=1)),
    ]
    with TelemetryStore(directory) as store:
        for event_id, signal, fill in signals:
            store.record(
                ExecutionTelemetryRecord(
                    event_id=event_id,
                    strategy_id="s1",
                    instrument="EURUSD",
                    timeframe="H1",
                    direction=Direction.LONG,
                    execution_mode=ExecutionMode.PAPER,
                    order_type=OrderType.MARKET,
                    outcome=ExecutionOutcome.FILLED,
                    signal_ts=signal,
                    decision_ts=signal,
                    fill_ts=fill,
                    requested_price=1.1,
                    filled_price=1.1001,
                    requested_size=1000.0,
                    filled_size=1000.0,
                )
            )
    return directory


def test_telemetry_after_the_pin_is_not_visible(bars: InMemoryBarSource, tmp_path: Path) -> None:
    directory = _telemetry(tmp_path)
    pinned = _call("query_execution_telemetry", _ctx(bars, PIN, telemetry_dir=directory))
    assert pinned.n_records == 1
    assert pinned.effective_as_of == PIN.isoformat()
    assert any("pinned to as_of" in c for c in pinned.caveats)

    unpinned = _call("query_execution_telemetry", _ctx(bars, None, telemetry_dir=directory))
    assert unpinned.n_records == 3
    assert unpinned.effective_as_of is None
    assert not any("pinned" in c for c in unpinned.caveats)
