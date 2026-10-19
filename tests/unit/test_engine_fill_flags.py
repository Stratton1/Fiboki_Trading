"""The engine records the fill rule each trade's fills met (robustness item 12).

``Trade.fill_flags`` copies what the fill model already reported
(``ExitFill.gapped`` / ``both_touched`` / ``stale``, ``EntryFill.stale``); it
is read by nothing that decides a fill or a size, and it is not a ledger
column, so no stored ``ledger_sha256`` moves.
"""
from __future__ import annotations

from fiboki.agents.jobs import _trade_payload
from fiboki.backtest.engine import BacktestResult
from tests.helpers_exec import make_frame
from tests.unit.test_engine_exits import _run, _signal
from tests.unit.test_engine_trade_protection import LEDGER_COLUMNS_BEFORE


def test_a_gap_through_the_stop_is_flagged():
    # Long from the 04:00 open (1.1000), stop 1.0900; the 08:00 bar OPENS at
    # 1.0850, through the stop: the fill takes the worse open (gap loss).
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1010, 1.0950, 1.0960),
        ("2024-01-02 08:00", 1.0850, 1.0870, 1.0840, 1.0860),
        ("2024-01-02 12:00", 1.0860, 1.0870, 1.0850, 1.0860),
    ])
    (trade,) = _run(frame, [_signal("2024-01-02 00:00", 1.1000, 1.0900)]).trades
    assert trade.exit_reason.value == "stop_loss"
    assert "gapped" in trade.fill_flags
    assert _trade_payload(trade)["fill_flags"] == list(trade.fill_flags)


def test_a_bar_touching_stop_and_target_is_flagged_both_touched():
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1150, 1.0850, 1.1000),
        ("2024-01-02 08:00", 1.1000, 1.1010, 1.0990, 1.1000),
    ])
    (trade,) = _run(frame, [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100,), (1.0,))]).trades
    assert "both_touched" in trade.fill_flags
    # STOP_FIRST: the unknowable intrabar path is resolved against the trade.
    assert trade.exit_reason.value == "stop_loss"


def test_a_plain_target_fill_has_no_flag_and_the_ledger_is_unchanged():
    frame = make_frame([
        ("2024-01-02 00:00", 1.1000, 1.1010, 1.0990, 1.1000),
        ("2024-01-02 04:00", 1.1000, 1.1150, 1.0990, 1.1100),
        ("2024-01-02 08:00", 1.1100, 1.1110, 1.1090, 1.1100),
    ])
    res = _run(frame, [_signal("2024-01-02 00:00", 1.1000, 1.0900, (1.1100,), (1.0,))])
    (trade,) = res.trades
    assert trade.fill_flags == ()
    assert BacktestResult.LEDGER_COLUMNS == LEDGER_COLUMNS_BEFORE
    assert "fill_flags" not in res.ledger_text()
