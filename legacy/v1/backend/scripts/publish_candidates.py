"""Publish survivor candidates from the local research DB to a destination DB.

Option B of the research/operational split: heavy research stays in the local
SQLite ledger; only the *survivors* (validated candidates the review surface
needs) are published to the production DB. This keeps the firehose of
rejections / ranking-only rows out of the operational database while making
candidates visible on the deployed site.

Publishes ONLY 'validated' events (the laddered survivors), deduped by
(strategy, instrument, timeframe, content of stats), carrying full stats +
recommended state. Never publishes rejected/backtested churn. Append-only,
zero execution — it only copies review records.

Usage:
  python scripts/publish_candidates.py --src app --dest "<postgres-url>"
  python scripts/publish_candidates.py --src app --dest sqlite:///prod_test.db --dry-run
"""

from __future__ import annotations

import argparse

from fibokei.db.database import (
    get_engine,
    get_session_factory,
    init_db,
    resolve_app_db_url,
)
from fibokei.db.ledger_repository import create_lifecycle_event, list_lifecycle_events


def _url(spec: str) -> str:
    if spec == "app":
        return resolve_app_db_url()
    return spec if "://" in spec else f"sqlite:///{spec}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="app", help="source research DB ('app'|path|url)")
    ap.add_argument("--dest", required=True, help="destination DB url (prod)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src_engine = get_engine(_url(args.src))
    SrcSession = get_session_factory(src_engine)
    with SrcSession() as s:
        survivors = list_lifecycle_events(s, event_type="validated", limit=100000)
        # Detach the data we need before the session closes.
        rows = [{
            "strategy_id": e.strategy_id, "instrument": e.instrument,
            "timeframe": e.timeframe, "risk_decision": e.risk_decision,
            "research_run_id": e.research_run_id, "approval_status": e.approval_status,
            "reason": e.reason, "stats_json": e.stats_json,
        } for e in survivors]

    print(f"Source survivors (validated): {len(rows)}", flush=True)
    if args.dry_run:
        for r in rows[:10]:
            print(f"  {r['strategy_id']} {r['instrument']} {r['timeframe']} "
                  f"{r['risk_decision']}")
        print("(dry-run — nothing written)", flush=True)
        return

    dest_engine = get_engine(_url(args.dest))
    init_db(dest_engine)
    DestSession = get_session_factory(dest_engine)
    with DestSession() as d:
        existing = {(e.strategy_id, e.instrument, e.timeframe)
                    for e in list_lifecycle_events(d, event_type="validated",
                                                   limit=100000)}
        published = 0
        for r in rows:
            key = (r["strategy_id"], r["instrument"], r["timeframe"])
            if key in existing:
                continue
            create_lifecycle_event(d, {
                "event_type": "validated", "actor": "agent",
                "strategy_id": r["strategy_id"], "instrument": r["instrument"],
                "timeframe": r["timeframe"], "research_run_id": r["research_run_id"],
                "approval_status": r["approval_status"] or "pending",
                "risk_decision": r["risk_decision"], "reason": r["reason"],
                "stats_json": r["stats_json"]})
            existing.add(key)
            published += 1
    print(f"Published {published} new survivors to dest "
          f"({len(rows) - published} already present).", flush=True)


if __name__ == "__main__":
    main()
