"""The advisory paper maturity report (ops/maturity.py)."""
from __future__ import annotations

import numpy as np

from fiboki.ops.maturity import ADVISORY_MATURITY_V1, drawdown_band, maturity_report

RNG = np.random.default_rng(7)
BT_R = list(np.where(RNG.random(200) < 0.45, 1.8, -1.0))
BT_TRADES = [{"instrument": "EURUSD", "size": 10_000.0, "spread_cost": 1.0, "commission": 0.0,
              "slippage_cost": 0.2, "financing_cost": 0.0} for _ in range(50)]


def _paper(n: int, *, cost: float = 1.2, pnl: float = 5.0) -> list[dict]:
    return [{"instrument": "EURUSD", "size": 10_000.0, "net_pnl": pnl if i % 3 else -pnl, "costs": cost,
             "exit_time": f"2026-10-{1 + i // 10:02d}T{i % 10:02d}:00:00+00:00"} for i in range(n)]


CLEAN = {"status": "diverged", "status_reason": "2 attributed", "unexplained": 0, "window_end": "2026-12-31T00:00:00+00:00"}
CHAIN = {"present": True, "intact": True, "rows": 120, "reason": "120 row(s) verify from genesis"}


def _report(**kw):
    args = dict(
        paper_trades=_paper(40), initial_balance=100_000.0, risk_fraction=0.005, days_live=35.0,
        backtest_r_multiples=BT_R, backtest_trades=BT_TRADES, backtest_label="backtest bt_1",
        reconciliation=CLEAN, latest_paper_trade_at="2026-10-04T09:00:00+00:00", chain=CHAIN,
    )
    args.update(kw)
    return maturity_report(**args)


def test_every_item_passing_is_ready_to_consider_and_says_it_enables_nothing():
    report = _report()
    assert report.ready and report.verdict == "ready_to_consider"
    assert [i.key for i in report.items] == [
        "min_trades", "min_days", "drawdown_in_band", "costs_as_expected", "reconciliation_clean", "audit_chain_intact",
    ]
    assert all(i.status == "pass" for i in report.items)
    assert "enables nothing" in report.note
    assert report.criteria is ADVISORY_MATURITY_V1


def test_not_evaluated_blocks_and_fail_wins():
    missing = _report(reconciliation=None)
    assert not missing.ready and missing.verdict == "not_evaluated"
    few = _report(paper_trades=_paper(5), reconciliation=None)
    assert few.verdict == "not_ready"
    assert {i.key: i.status for i in few.items}["min_trades"] == "fail"


def test_costs_drawdown_reconciliation_and_chain_fail_for_their_reasons():
    dear = _report(paper_trades=_paper(40, cost=3.0))
    assert {i.key: i.status for i in dear.items}["costs_as_expected"] == "fail"
    losing = [{**t, "net_pnl": -1_500.0} for t in _paper(40)]
    deep = _report(paper_trades=losing)
    assert {i.key: i.status for i in deep.items}["drawdown_in_band"] == "fail"
    bad = _report(reconciliation={**CLEAN, "unexplained": 2})
    assert {i.key: i.status for i in bad.items}["reconciliation_clean"] == "fail"
    stale = _report(reconciliation={**CLEAN, "window_end": "2026-10-01T00:00:00+00:00"})
    assert {i.key: i.status for i in stale.items}["reconciliation_clean"] == "not_evaluated"
    broken = _report(chain={**CHAIN, "intact": False, "reason": "row 3 edited"})
    assert {i.key: i.status for i in broken.items}["audit_chain_intact"] == "fail"
    norisk = _report(risk_fraction=None)
    assert {i.key: i.status for i in norisk.items}["drawdown_in_band"] == "not_evaluated"


def test_the_drawdown_band_is_seeded_deterministic_and_grows_with_the_horizon():
    a = drawdown_band(BT_R, horizon=40, risk_fraction=0.005, n_paths=500)
    b = drawdown_band(BT_R, horizon=40, risk_fraction=0.005, n_paths=500)
    assert (a.p5, a.p50, a.p95) == (b.p5, b.p50, b.p95)
    assert a.p5 <= a.p50 <= a.p95
    longer = drawdown_band(BT_R, horizon=180, risk_fraction=0.005, n_paths=500)
    assert longer.p50 >= a.p50
