"""Import a standalone pipeline ledger into the app DB (one-off reconcile).

The early manual full_pipeline run wrote lifecycle events to a standalone sqlite
ledger using a two-event scheme (a 'validated' event without a decision, plus a
separate 'shortlisted' event carrying the paper_candidate decision). The app /
candidates API expects a single 'validated' event whose risk_decision is the
recommended state. This script reconciles the former into the latter and writes
it into the app DB so those survivors become visible — without recomputing
anything. Read-only against research data; append-only into the app ledger.

Usage:
  python scripts/import_ledger.py --src results/pipeline/ledger.db --dest app
"""

from __future__ import annotations

import argparse
import json
import sqlite3

from fibokei.db.database import (
    get_engine,
    get_session_factory,
    init_db,
    resolve_app_db_url,
)
from fibokei.db.ledger_repository import create_lifecycle_event, list_lifecycle_events


def _dest_url(spec: str) -> str:
    if spec == "app":
        return resolve_app_db_url()
    return spec if "://" in spec else f"sqlite:///{spec}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="results/pipeline/ledger.db")
    ap.add_argument("--dest", default="app")
    args = ap.parse_args()

    src = sqlite3.connect(args.src)
    src.row_factory = sqlite3.Row
    rows = list(src.execute(
        "select event_type, strategy_id, instrument, timeframe, risk_decision, "
        "reason, stats_json, research_run_id from bot_lifecycle_events"))

    # Map combo -> paper_candidate/research_watchlist from 'shortlisted' events.
    decision: dict[tuple, str] = {}
    for r in rows:
        if r["event_type"] == "shortlisted" and r["risk_decision"]:
            decision[(r["strategy_id"], r["instrument"], r["timeframe"])] = r["risk_decision"]

    engine = get_engine(_dest_url(args.dest))
    init_db(engine)
    Session = get_session_factory(engine)

    imported = {"validated": 0, "rejected": 0, "skipped": 0}
    with Session() as s:
        # Avoid duplicate imports: existing validated combos in the app DB.
        existing = {(e.strategy_id, e.instrument, e.timeframe)
                    for e in list_lifecycle_events(s, event_type="validated", limit=100000)}
        for r in rows:
            if r["event_type"] not in ("validated", "rejected"):
                continue
            key = (r["strategy_id"], r["instrument"], r["timeframe"])
            if r["event_type"] == "validated" and key in existing:
                imported["skipped"] += 1
                continue
            stats = json.loads(r["stats_json"]) if r["stats_json"] else {}
            rd = (decision.get(key, "research_watchlist")
                  if r["event_type"] == "validated" else None)
            create_lifecycle_event(s, {
                "event_type": r["event_type"], "actor": "agent",
                "strategy_id": r["strategy_id"], "instrument": r["instrument"],
                "timeframe": r["timeframe"], "research_run_id": r["research_run_id"],
                "approval_status": "pending", "risk_decision": rd,
                "reason": r["reason"], "stats_json": stats})
            imported[r["event_type"]] += 1

    print(f"Imported into {args.dest}: {imported}", flush=True)


if __name__ == "__main__":
    main()
