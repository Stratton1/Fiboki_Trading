"""Live-vs-backtest monitoring for paper bots promoted from candidates.

Compares a paper bot's *live* (forward-monitoring) performance against the
backtest expectation captured at approval time (the 'promoted_to_paper' ledger
event's stats_json). Shared by the /research/paper-monitor endpoint and the
decay/monitor scheduled loop so the verdict is computed in exactly one place.

Review/observe only — this module never trades, never touches a broker, and only
*reads* bot/trade state. The decay loop may pause a PAPER bot (no live impact);
the operator-only kill switch is never touched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from fibokei.db import ledger_repository as ledger
from fibokei.db.repository import get_paper_trades

# Don't judge a bot until it has a minimum live-trade sample.
MIN_LIVE_TRADES = 20
# Decayed if live profit factor falls below this absolute floor ...
PF_FLOOR = 1.0
# ... or below this fraction of the backtested expectation.
PF_RETENTION = 0.5


@dataclass
class BotMonitor:
    bot_id: str
    strategy_id: str | None
    instrument: str | None
    timeframe: str | None
    state: str
    live_trades: int
    live_net_pnl: float
    live_win_rate: float
    live_profit_factor: float | None
    live_max_dd_pct: float
    expected_sharpe: float | None
    expected_profit_factor: float | None
    expected_max_dd: float | None
    verdict: str            # monitoring | healthy | decayed
    reason: str


def _expected_for(session, bot_id: str) -> dict:
    events = ledger.list_lifecycle_events(
        session, bot_id=bot_id, event_type="promoted_to_paper", limit=1)
    if not events:
        return {}
    sj = events[0].stats_json
    if not sj:
        return {}
    data = sj if isinstance(sj, dict) else json.loads(sj)
    return data.get("expected", data)


def _live_stats(pnls: list[float]) -> dict:
    n = len(pnls)
    net = float(sum(pnls))
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else float("inf"))
    win_rate = (len(wins) / n) if n else 0.0
    # Max drawdown of the trade-by-trade equity curve.
    eq, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)
    return {"n": n, "net": net, "win_rate": win_rate, "pf": pf, "max_dd": max_dd}


def compute_bot_monitor(session, bot) -> BotMonitor:
    """Compute the live-vs-expected monitor + drift verdict for one paper bot."""
    expected = _expected_for(session, bot.bot_id)
    trades = get_paper_trades(session, bot_id=bot.bot_id, is_live=True, limit=10000)
    pnls = [float(t.pnl) for t in trades]
    live = _live_stats(pnls)
    exp_pf = expected.get("profit_factor")

    verdict, reason = "monitoring", f"{live['n']}/{MIN_LIVE_TRADES} live trades"
    if live["n"] >= MIN_LIVE_TRADES:
        pf = live["pf"]
        if pf is None or pf < PF_FLOOR:
            verdict = "decayed"
            reason = f"live PF {pf if pf is not None else 0:.2f} < {PF_FLOOR}"
        elif exp_pf and pf < PF_RETENTION * exp_pf:
            verdict = "decayed"
            reason = (f"live PF {pf:.2f} < {PF_RETENTION:.0%} of expected "
                      f"{exp_pf:.2f}")
        else:
            verdict = "healthy"
            reason = f"live PF {pf:.2f} holding vs expected {exp_pf or '—'}"

    return BotMonitor(
        bot_id=bot.bot_id, strategy_id=bot.strategy_id, instrument=bot.instrument,
        timeframe=bot.timeframe, state=bot.state,
        live_trades=live["n"], live_net_pnl=round(live["net"], 2),
        live_win_rate=round(live["win_rate"], 3),
        live_profit_factor=(None if live["pf"] in (None, float("inf"))
                            else round(live["pf"], 3)),
        live_max_dd_pct=round(live["max_dd"], 2),
        expected_sharpe=expected.get("sharpe"),
        expected_profit_factor=exp_pf, expected_max_dd=expected.get("max_dd"),
        verdict=verdict, reason=reason,
    )
