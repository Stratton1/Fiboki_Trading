"""The engine records each trade's stop and targets at the ledger boundary.

``core.contracts.Trade`` carries ``initial_stop``, ``final_stop``,
``take_profit_targets`` and ``stop_moved_by`` (2026-10-18) so the chart
workstation can draw a backtest trade's protection as the ENGINE held it, not
as a browser guessed it. Two properties matter:

* the values are the engine's own (the signal's stop, the planned legs, the
  ratcheted trail), asserted here on the hand-calculated cases of
  ``test_engine_exits.py``;
* recording them changes no result: the canonical ledger text, and therefore
  every stored ``ledger_sha256``, has exactly the columns it had before.
"""
from __future__ import annotations

import pandas as pd
import pytest

from fiboki.agents.jobs import _trade_payload
from fiboki.backtest.engine import BacktestResult
from fiboki.backtest.exits import ExitPolicy, TrailKind, TrailSpec
from tests.helpers_exec import make_frame
from tests.unit.test_engine_exits import TRAIL_FRAME_PREFIX, _run, _series, _signal

#: The canonical ledger columns as they stood before the protection fields
#: existed. If this changes, every stored ledger hash moves: that is a
#: deliberate, documented engine change, never a side effect of charting.
LEDGER_COLUMNS_BEFORE = (
    "instrument", "direction", "size", "entry_price", "exit_price", "entry_time",
    "exit_time", "exit_reason", "gross_pnl", "spread_cost", "commission",
    "slippage_cost", "financing_cost", "net_pnl", "account_ccy", "bars_held",
    "max_adverse_excursion", "max_favourable_excursion", "fx_rate_used",
)


def test_the_ledger_columns_and_text_are_unchanged():
    assert BacktestResult.LEDGER_COLUMNS == LEDGER_COLUMNS_BEFORE
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0990, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1150, 1.1030, 1.1100),
        ("2024-01-02 12:00", 1.1100, 1.1250, 1.1090, 1.1200),
    ])
    res = _run(frame, [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100, 1.1200), (0.4, 0.6))])
    header = res.ledger_text().splitlines()[0].split("\t")
    assert tuple(header) == LEDGER_COLUMNS_BEFORE
    for name in ("initial_stop", "final_stop", "take_profit_targets", "stop_moved_by"):
        assert name not in res.ledger_text()


def test_a_two_leg_trade_records_its_stop_and_both_targets():
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0990, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1150, 1.1030, 1.1100),
        ("2024-01-02 12:00", 1.1100, 1.1250, 1.1090, 1.1200),
    ])
    res = _run(frame, [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100, 1.1200), (0.4, 0.6))])
    (trade,) = res.trades
    assert trade.initial_stop == pytest.approx(1.0900, abs=1e-12)
    # No breakeven and no trail declared: the stop never moved.
    assert trade.final_stop == pytest.approx(1.0900, abs=1e-12)
    assert trade.stop_moved_by == ""
    assert trade.take_profit_targets == pytest.approx((1.1100, 1.1200), abs=1e-12)


def test_a_trailed_trade_records_the_ratcheted_stop_and_the_rule():
    # test_engine_exits.test_chandelier_trail_ratchets_and_never_loosens: the
    # stop ratchets 1.0900 -> 1.0940 -> 1.1020 and the 16:00 bar takes it.
    frame = make_frame([*TRAIL_FRAME_PREFIX, ("2024-01-02 16:00", 1.1060, 1.1065, 1.1000, 1.1010)])
    res = _run(
        frame,
        [_signal("2024-01-02 00:00", 1.1000, 1.0900)],
        policy=ExitPolicy(
            trailing=TrailSpec(
                kind=TrailKind.ATR_CHANDELIER, value=2.0, atr_column="atr", activate_after_r=0.0
            )
        ),
        exit_series=_series(frame, atr=0.0040),
    )
    (trade,) = res.trades
    assert trade.initial_stop == pytest.approx(1.0900, abs=1e-12)
    assert trade.final_stop == pytest.approx(1.1020, abs=1e-12)
    assert trade.stop_moved_by == "trailing"
    assert trade.take_profit_targets == ()


def test_the_research_payload_carries_the_protection():
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1050, 1.0990, 1.1040),
        ("2024-01-02 08:00", 1.1040, 1.1150, 1.1030, 1.1100),
    ])
    res = _run(frame, [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100,), (1.0,))])
    payload = _trade_payload(res.trades[0])
    assert payload["initial_stop"] == pytest.approx(1.09)
    assert payload["final_stop"] == pytest.approx(1.09)
    assert payload["take_profit_targets"] == [pytest.approx(1.11)]
    assert payload["stop_moved_by"] == ""
    assert pd.Timestamp(payload["entry_time"]).tzinfo is not None
