"""Human-review surface for the research pipeline (read-only).

Reads the append-only lifecycle ledger and reports the candidates awaiting human
review — grouped by recommended state, ranked by Sharpe — plus the validation
funnel (how many combos were rejected at each robustness rung). Pure read-only:
opens the ledger, prints/exports, changes nothing.

Usage:
  python scripts/review_candidates.py --ledger-db results/pipeline/ledger.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger-db", default="results/pipeline/ledger.db")
    ap.add_argument("--out", default="results/pipeline/candidates_review.json")
    args = ap.parse_args()

    db = Path(args.ledger_db)
    if not db.exists():
        raise SystemExit(f"No ledger at {db} — run the pipeline first.")

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    # Validation funnel: reject reasons across the ledger.
    funnel: dict[str, int] = {}
    for row in con.execute(
        "select reason, count(*) n from bot_lifecycle_events "
        "where event_type='rejected' group by reason"):
        funnel[row["reason"] or "unknown"] = row["n"]

    validated = con.execute(
        "select count(*) n from bot_lifecycle_events where event_type='validated'"
    ).fetchone()["n"]

    # Candidates: validated events carry recommended state in risk_decision.
    rows = con.execute(
        "select strategy_id, instrument, timeframe, risk_decision, stats_json, "
        "created_at from bot_lifecycle_events where event_type='validated'"
    ).fetchall()

    by_state: dict[str, list[dict]] = {}
    for r in rows:
        stats = json.loads(r["stats_json"]) if r["stats_json"] else {}
        rec = {
            "strategy_id": r["strategy_id"], "instrument": r["instrument"],
            "timeframe": r["timeframe"], "state": r["risk_decision"],
            "sharpe": stats.get("sharpe"), "composite": stats.get("composite"),
            "oos_score": stats.get("oos_score"),
            "mc_profit_prob": stats.get("mc_profit_prob"),
            "created_at": r["created_at"],
        }
        by_state.setdefault(r["risk_decision"] or "unknown", []).append(rec)

    for state in by_state:
        by_state[state].sort(key=lambda x: (x["sharpe"] or 0), reverse=True)

    paper = by_state.get("paper_candidate", [])
    watch = by_state.get("research_watchlist", [])

    print("=== Fiboki research — candidates awaiting human review ===")
    print(f"ledger: {db}")
    print(f"validated (passed all rungs): {validated}")
    print(f"paper_candidate: {len(paper)} | research_watchlist: {len(watch)}")
    print("\nrejection funnel (where combos failed):")
    for reason, n in sorted(funnel.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5}  {reason}")

    if paper:
        print("\nTOP paper_candidates by Sharpe:")
        for c in paper[:20]:
            print(f"  {c['strategy_id']:<34} {c['instrument']:<7} {c['timeframe']:<3} "
                  f"sharpe={c['sharpe']} oos={c['oos_score']} "
                  f"mc_pp={c['mc_profit_prob']}")
    else:
        print("\nNo paper_candidates yet (none passed every rung + gate).")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({
        "validated": validated, "rejection_funnel": funnel,
        "paper_candidate_count": len(paper),
        "research_watchlist_count": len(watch),
        "paper_candidates": paper, "research_watchlist": watch[:50],
    }, indent=2))
    print(f"\nWritten: {args.out}  (REVIEW-ONLY — no action taken)")


if __name__ == "__main__":
    main()
