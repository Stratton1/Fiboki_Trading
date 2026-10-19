"""Wave 3O advisory reports over a real research ledger and a forward paper session:
self-correlation of candidates, the maturity band, risk of ruin, portfolio weights."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from tests.api.conftest import ORIGIN
from tests.api.ops_fixtures import add_backtest, synthetic_trades, write_forward_session

PNL = np.random.default_rng(11).normal(5.0, 40.0, 120)


@pytest.fixture
def reports(tmp_path, api_env):
    db = tmp_path / "experiments.sqlite"
    add_backtest(db, "donchian_breakout_atr", synthetic_trades(seed=1, n=120, strategy_id="donchian_breakout_atr", pnl=PNL))
    add_backtest(db, "ichimoku_kumo_trend", synthetic_trades(seed=2, n=120, strategy_id="ichimoku_kumo_trend", pnl=PNL * 1.3))
    root = tmp_path / "paper"
    paper = synthetic_trades(seed=3, n=40, strategy_id="donchian_breakout_atr", pnl=PNL[:40] * 10, size=100_000.0,
                             cost=12.5, provenance="paper")
    write_forward_session(root, "paper_forward_v1", "donchian_breakout_atr", paper,
                          extra={"risk_fraction": 0.005, "ledger_dir": "paper_forward/paper_forward_v1"})
    env = {**api_env, "FIBOKI_EXPERIMENT_DB": str(db), "FIBOKI_PAPER_ROOT": str(root)}
    with TestClient(create_app(load_settings(env), configure_logs=False), base_url=ORIGIN) as c:
        yield c


def test_a_candidate_correlated_with_the_fleet_is_flagged(reports):
    body = reports.get("/api/trading/candidates/correlation").json()
    view = body["data"]
    assert view["threshold"] == 0.7 and view["bots"] == ["forward-paper_forward_v1"]
    rows = {r["strategy_id"]: r for r in view["rows"]}
    ichi = rows["ichimoku_kumo_trend"]
    bt = next(c for c in ichi["cells"] if c["basis"] == "backtest")
    assert bt["rho"]["value"] > 0.95 and bt["flagged"] and bt["rho"]["provenance"] == "backtest"
    assert ichi["flagged"] and "above 0.7" in ichi["flag_reason"]
    # The bot's own strategy is not flagged against itself.
    don = rows["donchian_breakout_atr"]
    assert all(c["same_strategy"] and not c["flagged"] for c in don["cells"])
    assert not don["flagged"]
    # A candidate with no stored backtest is not measurable, never 0.
    missing = [r for r in view["rows"] if r["strategy_id"] not in ("ichimoku_kumo_trend", "donchian_breakout_atr")]
    assert missing and all(r["max_abs_rho"]["value"] is None and not r["flagged"] for r in missing)


def test_the_maturity_band_reads_the_stored_backtest(reports):
    data = reports.get("/api/trading/sessions/forward-paper_forward_v1/maturity").json()["data"]
    items = {i["key"]: i for i in data["items"]}
    assert items["min_trades"]["status"] == "pass"
    assert items["drawdown_in_band"]["status"] in ("pass", "fail")
    assert data["band"]["source_trades"] == 120 and data["band"]["horizon_trades"] == 40
    assert data["band"]["p5"]["value"] <= data["band"]["p95"]["value"]
    assert items["costs_as_expected"]["status"] == "pass"
    assert items["costs_as_expected"]["value"]["value"] == pytest.approx(12.5 / 100_000.0 / (1.2 / 10_000.0), rel=1e-3)
    assert data["verdict"] == "not_evaluated"  # no reconciliation, no chain yet


def test_the_risk_calculator_sizes_by_the_platform_and_runs_a_seeded_ruin(reports):
    q = ("/api/trading/risk/calculator?account_size=100000&risk_pct=1&ruin_pct=30"
         "&strategy_id=donchian_breakout_atr&entries=EURUSD:1.1:20,USDJPY:150:30&paths=1000")
    a = reports.get(q).json()
    b = reports.get(q).json()
    assert a["data"]["ruin"] == b["data"]["ruin"]
    ruin = a["data"]["ruin"]
    assert ruin["horizon_trades"] == 120 and 0 <= ruin["probability_pct"]["value"] <= 100
    assert ruin["probability_pct"]["provenance"] == "backtest"
    sizes = {s["instrument"]: s for s in a["data"]["sizes"]}
    assert sizes["EURUSD"]["size"]["value"] > 0 and sizes["EURUSD"]["rejection"] is None
    assert sizes["USDJPY"]["size"]["value"] is None and "fx_rate_required" in sizes["USDJPY"]["rejection"]
    typed = reports.get("/api/trading/risk/calculator?account_size=1000&risk_pct=2&r_multiples=1,-1,2,-1,-1").json()
    assert typed["data"]["ruin"]["horizon_trades"] == 5
    assert "user_supplied_distribution" in {c["code"] for c in typed["caveats"]}
    none = reports.get("/api/trading/risk/calculator?account_size=1000&risk_pct=1").json()
    assert none["data"]["ruin"] is None and none["data"]["ruin_absent"]
    assert reports.get("/api/trading/risk/calculator?account_size=1000&risk_pct=9").status_code == 422


def test_allocation_weights_are_display_only_and_sum_to_one(reports):
    body = reports.get("/api/trading/allocation?basis=backtest").json()
    view = body["data"]
    keys = {m["key"] for m in view["members"]}
    assert {"donchian_breakout_atr", "ichimoku_kumo_trend"} <= keys
    assert next(m for m in view["members"] if m["key"] == "donchian_breakout_atr")["in_fleet"]
    assert view["common_days"] >= 20 and {s["scheme"] for s in view["schemes"]} == {
        "equal_weight", "equal_risk", "inverse_vol", "min_correlation"}
    for s in view["schemes"]:
        assert sum(f["value"] for f in s["weights"].values()) == pytest.approx(100.0, abs=1e-2)
        assert s["sharpe"]["provenance"] == "backtest"
    assert "DISPLAY ONLY" in view["method"]
    again = reports.get("/api/trading/allocation?basis=backtest").json()["data"]
    assert again["schemes"] == view["schemes"]
    paper = reports.get("/api/trading/allocation?basis=paper").json()["data"]
    assert paper["schemes"] == [] and paper["absent_reason"]  # one bot: nothing to weight
