"""Decay / regime-drift monitor for paper bots (review-safe, paper-only).

For every candidate-sourced PAPER bot, compares live forward performance to its
backtest expectation. If a bot has decayed (live profit factor collapsed vs
expectation, past a minimum live-trade sample), it is auto-demoted: the PAPER
bot is paused and a 'demoted_from_paper' event is appended to the ledger with
the reason — that ledger entry is the anti-pattern feedback the discovery loop
can later read.

Hard limits: paper-only. Pausing a paper bot has NO live impact. This never
touches a broker, live mode, or the (operator-only) kill switch.

Usage:
  python scripts/decay_monitor.py --ledger-db app
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from fibokei.db.database import (
    get_engine,
    get_session_factory,
    init_db,
    resolve_app_db_url,
)
from fibokei.db.ledger_repository import create_agent_run, create_lifecycle_event
from fibokei.db.repository import get_paper_bots, update_paper_bot_state
from fibokei.paper.monitoring import compute_bot_monitor


def _resolve(spec: str) -> str:
    if spec == "app":
        return resolve_app_db_url()
    return spec if "://" in spec else f"sqlite:///{spec}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger-db", default="app")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; do not pause/demote")
    args = ap.parse_args()

    engine = get_engine(_resolve(args.ledger_db))
    init_db(engine)
    Session = get_session_factory(engine)
    run_id = f"decay_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"

    demoted, healthy, monitoring = 0, 0, 0
    with Session() as s:
        create_agent_run(s, {"run_id": run_id, "lane": "safety_governor",
                             "actor": "agent", "agent_type": "decay_monitor",
                             "summary": "paper bot live-vs-backtest decay check"})
        bots = [b for b in get_paper_bots(s)
                if b.source_type == "candidate"
                and b.state in ("monitoring", "position_open")]
        print(f"[{run_id}] checking {len(bots)} candidate paper bots", flush=True)
        for b in bots:
            m = compute_bot_monitor(s, b)
            if m.verdict == "decayed":
                demoted += 1
                print(f"  DECAYED {b.bot_id} {b.strategy_id} {b.instrument} "
                      f"{b.timeframe}: {m.reason}", flush=True)
                if not args.dry_run:
                    update_paper_bot_state(s, b.bot_id, "paused")
                    create_lifecycle_event(s, {
                        "event_type": "demoted_from_paper", "actor": "agent",
                        "bot_id": b.bot_id, "strategy_id": b.strategy_id,
                        "instrument": b.instrument, "timeframe": b.timeframe,
                        "approval_status": "n_a",
                        "reason": f"regime drift / decay: {m.reason}",
                        "stats_json": {"live_pf": m.live_profit_factor,
                                       "expected_pf": m.expected_profit_factor,
                                       "live_trades": m.live_trades,
                                       "live_net_pnl": m.live_net_pnl}})
            elif m.verdict == "healthy":
                healthy += 1
            else:
                monitoring += 1

    print(f"\nDone. demoted={demoted} healthy={healthy} "
          f"monitoring(insufficient sample)={monitoring}", flush=True)
    if args.dry_run:
        print("(dry-run — no bots were paused)", flush=True)


if __name__ == "__main__":
    main()
