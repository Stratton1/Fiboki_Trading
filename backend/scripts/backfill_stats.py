"""One-off: backfill missing descriptive stats on imported 'validated' events.

The early full_pipeline run (pre full-stats persistence) saved only a few stat
fields. These survivors already PASSED the ladder; this just re-runs one
backtest per combo to fill profit_factor / max_dd / trades / net_profit so
demo-readiness can be determined. Read-only research data; updates only the
app ledger stats_json.
"""

from __future__ import annotations

import json
import sqlite3

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.db.database import resolve_app_db_url
from fibokei.strategies.registry import strategy_registry


def main() -> None:
    url = resolve_app_db_url()
    path = url.replace("sqlite:///", "") if url.startswith("sqlite") else "fibokei.db"
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "select id,strategy_id,instrument,timeframe,stats_json from "
        "bot_lifecycle_events where event_type='validated'"))
    cfg = BacktestConfig(initial_capital=10000.0)
    patched = 0
    for r in rows:
        s = json.loads(r["stats_json"]) if r["stats_json"] else {}
        if s.get("profit_factor") is not None and s.get("trades") is not None:
            continue
        df = load_canonical(r["instrument"], r["timeframe"])
        if df is None:
            continue
        df = df.copy()
        df["instrument"], df["timeframe"] = r["instrument"], r["timeframe"]
        strat = strategy_registry.get(r["strategy_id"])
        res = Backtester(strat, cfg).run(df, r["instrument"], Timeframe(r["timeframe"]))
        m = compute_metrics(res)
        s["profit_factor"] = round(m.get("profit_factor", 0.0) or 0.0, 3)
        s["max_dd"] = round(m.get("max_drawdown_pct", 0.0), 2)
        s["trades"] = int(m.get("total_trades", 0))
        s["net_profit"] = round(m.get("total_net_profit", 0.0), 2)
        con.execute("update bot_lifecycle_events set stats_json=? where id=?",
                    (json.dumps(s), r["id"]))
        con.commit()
        patched += 1
        print(f"patched {r['strategy_id']} {r['instrument']} {r['timeframe']}: "
              f"PF={s['profit_factor']} DD={s['max_dd']} tr={s['trades']}", flush=True)
    print(f"DONE patched={patched}", flush=True)


if __name__ == "__main__":
    main()
