"""The research analytics routes (api/routers/research_analytics.py).

The ledger here is produced the honest way: the research worker's own job
handlers (``agents/jobs.py``) run real backtests of ``donchian_breakout_atr``
through the engine on synthetic H4 bars, through a durable job ledger at
``<state>/jobs.sqlite``, into a file-backed experiment ledger. The routes then
read those bytes read-only, and every figure is checked against the stored
record or against an independent recomputation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fiboki.agents.jobs import register_research_handlers
from fiboki.agents.orchestrator import JobSpec, JobStatus, JobType, Orchestrator
from fiboki.agents.tools import InMemoryBarSource
from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.core.enums import Timeframe
from fiboki.research import tearsheet as ts
from fiboki.research.artefacts import BacktestRecord, ResearchStore
from fiboki.research.experiment import ExperimentLedger
from fiboki.strategy.dsl import StrategyDocument
from fiboki.strategy.registry import StrategyRegistry
from tests.agents_fixtures import gbp_fx_frames, trending_bars
from tests.api.conftest import ORIGIN, VIEWER_PW, login

STRATEGY = "ichimoku_kumo_trend"
WF_STRATEGY = "donchian_breakout_atr"
REPO = Path(__file__).resolve().parents[2]
GRID = [(7, 22), (7, 26), (9, 22), (9, 26), (11, 22), (11, 26)]


def _document() -> StrategyDocument:
    return StrategyDocument.from_json((REPO / "research" / "strategies" / f"{STRATEGY}.json").read_text())


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    root = tmp_path_factory.mktemp("research_analytics")
    state = root / "state"
    db = root / "experiments.sqlite"
    frame = trending_bars(instrument="EURUSD", timeframe=Timeframe.H4, periods=3000, seed=7,
                          drift=0.0002)
    bars = InMemoryBarSource({("EURUSD", "H4"): frame, **gbp_fx_frames()})
    registry = StrategyRegistry()
    registry.register(_document())
    ids: dict[str, str] = {}
    with ResearchStore(ledger=ExperimentLedger(db)) as store:
        orch = Orchestrator(path=state / "jobs.sqlite")
        register_research_handlers(orch, research=store, strategies=registry, bars=bars)
        for tenkan, kijun in GRID:
            orch.submit(JobSpec(
                job_type=JobType.BACKTEST, queue="research", idempotency_key=f"bt_{tenkan}_{kijun}",
                payload={"strategy_id": STRATEGY, "instrument": "EURUSD", "timeframe": "H4",
                         "account_ccy": "USD",
                         "parameters": {"tenkan_period": tenkan, "kijun_period": kijun}},
            ))
        done = orch.drain("research", max_jobs=20)
        assert all(r.status is JobStatus.SUCCEEDED for r in done), [r.error for r in done]
        for r in done:
            p = r.spec.payload["parameters"]
            ids[f"{p['tenkan_period']}_{p['kijun_period']}"] = str(r.result["backtest_id"])
        orch.submit(JobSpec(job_type=JobType.VALIDATION, queue="research",
                            payload={"backtest_id": ids["9_26"], "seed": 1, "bootstrap_samples": 50},
                            idempotency_key="val_9_26"))
        val = orch.drain("research")[0]
        assert val.status is JobStatus.SUCCEEDED, val.error
        # A queued job and a failed one, for the queue view.
        orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research", idempotency_key="queued",
                            payload={"strategy_id": STRATEGY, "instrument": "EURUSD", "timeframe": "H4"}))
        # A run filed before stops were stamped and without excursion keys.
        legacy = store.add_backtest(BacktestRecord(
            strategy_id=STRATEGY, instruments=("EURUSD",), timeframe="H4", engine_version="engine_v1",
            trades=tuple(
                {k: v for k, v in t.items()
                 if k not in ("initial_stop", "final_stop", "take_profit_targets",
                              "stop_moved_by", "max_adverse_excursion", "max_favourable_excursion")}
                for t in store.get_backtest(ids["7_22"]).trades
            ),
            equity_curve=store.get_backtest(ids["7_22"]).equity_curve,
            metrics=store.get_backtest(ids["7_22"]).metrics,
        ))
        ids["legacy"] = legacy.backtest_id
        orch.close()
    return {"db": db, "state": state, "ids": ids, "report_id": val.result["report_id"]}


@pytest.fixture
def client(ledger, api_env, monkeypatch):
    env = {**api_env, "FIBOKI_EXPERIMENT_DB": str(ledger["db"]), "FIBOKI_STATE_DIR": str(ledger["state"])}
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as c:
        assert login(c, "guest", VIEWER_PW).status_code == 200
        yield c


def _record(ledger, run_id: str) -> BacktestRecord:
    with ResearchStore(ledger=ExperimentLedger(ledger["db"])) as store:
        return store.get_backtest(run_id)


def _stat(view: dict, key: str) -> dict:
    return next(s for s in view["stats"] if s["key"] == key)


# ------------------------------------------------------------------ runs


def test_runs_lists_every_stored_run_with_headline_metrics(client, ledger):
    body = client.get("/api/research/runs", params={"strategy_id": STRATEGY}).json()
    assert body["source"]["kind"] == "live"
    assert body["total"] == len(GRID) + 1
    by_id = {r["run"]["run_id"]: r for r in body["items"]}
    rec = _record(ledger, ledger["ids"]["9_26"])
    row = by_id[rec.backtest_id]
    assert row["sharpe"]["value"] == pytest.approx(rec.metrics["sharpe"])
    assert row["tearsheet"] == f"/research/runs/{rec.backtest_id}"
    assert row["run"]["gate_verdict"] in ("pass", "fail", "inconclusive")


def test_runs_without_a_ledger_say_the_source_is_absent(api_env):
    app = create_app(load_settings(api_env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as c:
        login(c, "guest", VIEWER_PW)
        body = c.get("/api/research/runs").json()
    assert body["items"] == [] and body["source"]["kind"] == "absent"


# ------------------------------------------------------------------ tearsheet


def test_tearsheet_stats_are_the_stored_ones_with_definitions(client, ledger):
    run_id = ledger["ids"]["9_26"]
    rec = _record(ledger, run_id)
    body = client.get(f"/api/research/runs/{run_id}/tearsheet").json()
    view = body["data"]
    assert _stat(view, "sharpe")["figure"]["value"] == pytest.approx(rec.metrics["sharpe"])
    assert _stat(view, "max_drawdown_pct")["figure"]["value"] == pytest.approx(rec.metrics["max_drawdown_pct"])
    assert _stat(view, "n_trades")["figure"]["value"] == len(rec.trades)
    assert _stat(view, "net_profit")["figure"]["value"] == pytest.approx(
        rec.metrics["final_equity"] - rec.metrics["initial_equity"])
    costs = sum(t["spread_cost"] + t["commission"] + t["slippage_cost"] + t["financing_cost"]
                for t in rec.trades)
    assert _stat(view, "costs_paid")["figure"]["value"] == pytest.approx(costs)
    for s in view["stats"]:
        assert s["definition"] and s["formula"] and s["source"], s["key"]
    assert set(view["definitions"]) >= {"sharpe", "deflated_sharpe", "mae", "underwater"}


def test_tearsheet_psr_and_dsr_come_from_the_filed_gate_report(client, ledger):
    view = client.get(f"/api/research/runs/{ledger['ids']['9_26']}/tearsheet").json()["data"]
    report = view["gate_report"]
    assert report["report_id"] == ledger["report_id"]
    assert report["checks"] and {c["status"] for c in report["checks"]} <= {
        "pass", "fail", "not_evaluated", "not_applicable"}
    psr = _stat(view, "psr")
    assert psr["figure"]["value"] == pytest.approx(report["psr"]["value"])
    assert ledger["report_id"] in psr["source"]
    # No experiment-ledger trials were recorded: the DSR is NOT_EVALUATED, with the reason.
    dsr = _stat(view, "deflated_sharpe")
    assert dsr["figure"]["value"] is None
    assert dsr["figure"]["caveats"][0]["message"]
    # The trade-count stat carries the gate on the same metric.
    assert _stat(view, "n_trades")["check"]["metric"] in ("n_trades", "n_trades_over_min_trl")


def test_tearsheet_without_a_gate_report_computes_psr_and_leaves_dsr_missing(client, ledger):
    view = client.get(f"/api/research/runs/{ledger['ids']['7_22']}/tearsheet").json()["data"]
    assert view["gate_report"] is None
    rec = _record(ledger, ledger["ids"]["7_22"])
    expected = ts.per_trade_psr(rec.trades, rec.metrics["initial_equity"])
    assert _stat(view, "psr")["figure"]["value"] == pytest.approx(expected.psr)
    dsr = _stat(view, "deflated_sharpe")["figure"]
    assert dsr["value"] is None and "No gate report" in dsr["caveats"][0]["message"]


def test_tearsheet_curves_drawdowns_and_months(client, ledger):
    run_id = ledger["ids"]["9_26"]
    rec = _record(ledger, run_id)
    view = client.get(f"/api/research/runs/{run_id}/tearsheet").json()["data"]
    eq = ts.equity_series(rec.equity_curve)
    assert view["equity"]["points"][-1]["v"] == pytest.approx(float(eq.iloc[-1]))
    assert len(view["equity"]["points"]) <= ts.MAX_SERIES_POINTS + 2
    # The deepest drawdown period equals the engine's max drawdown.
    assert view["drawdowns"][0]["depth_pct"]["value"] == pytest.approx(rec.metrics["max_drawdown_pct"], abs=1e-6)
    assert min(p["v"] for p in view["underwater"]["points"] if p["v"] is not None) == pytest.approx(
        rec.metrics["max_drawdown_pct"], abs=1e-6)
    assert len(view["drawdowns"]) <= 5
    # Monthly returns compound to the total return.
    total = 1.0
    for cell in view["monthly"]:
        total *= 1 + cell["return_pct"]["value"] / 100
    assert (total - 1) * 100 == pytest.approx(rec.metrics["total_return_pct"], rel=1e-9)
    assert view["benchmark_kind"] == "flat" and view["benchmark"]["points"]
    assert view["chart_links"] == {"EURUSD": f"/markets/EURUSD?run={run_id}"}
    assert view["strategy_link"] == f"/research/strategies/{STRATEGY}"


def test_tearsheet_excursions_and_histogram_in_r(client, ledger):
    run_id = ledger["ids"]["9_26"]
    rec = _record(ledger, run_id)
    view = client.get(f"/api/research/runs/{run_id}/tearsheet").json()["data"]
    assert view["trade_histogram"]["basis"] == "r_multiple"
    assert sum(view["trade_histogram"]["counts"]) == len(rec.trades)
    first = rec.trades[0]
    table = view["excursions"]
    assert table["provenance"] == "backtest" and table["r_unit"] == "R"
    assert len(table["trade_id"]) == len(rec.trades)
    i = table["trade_id"].index(first["trade_id"])
    risk = abs(first["entry_price"] - first["initial_stop"]) * first["size"] * first["fx_rate_used"]
    assert table["r_multiple"][i] == pytest.approx(first["net_pnl"] / risk)
    assert table["mae"][i] == pytest.approx(first["max_adverse_excursion"])
    assert table["mfe_r"][i] == pytest.approx(first["max_favourable_excursion"] / risk)


def test_legacy_run_without_stops_or_excursions_says_so(client, ledger):
    body = client.get(f"/api/research/runs/{ledger['ids']['legacy']}/tearsheet").json()
    view = body["data"]
    assert view["trade_histogram"]["basis"] == "net_pnl"
    table = view["excursions"]
    assert all(v is None for v in table["mae"]) and all(v is None for v in table["r_multiple"])
    assert "no excursion keys" in view["excursions_note"]
    assert _stat(view, "expectancy_r")["figure"]["value"] is None
    assert any(c["code"] == "backtest_engine_stale" for c in body["caveats"])


def test_tearsheet_provenance_block_is_complete(client, ledger):
    run_id = ledger["ids"]["11_22"]
    rec = _record(ledger, run_id)
    prov = client.get(f"/api/research/runs/{run_id}/tearsheet").json()["data"]["provenance"]
    assert prov["strategy_hash"] == rec.content_hash
    assert prov["parameters"]["tenkan_period"] == 11 and prov["parameters"]["kijun_period"] == 22
    assert prov["parameters"]["senkou_b_period"] == 52  # a declared default
    assert "job payload" in prov["parameters_source"]
    assert prov["cost_profile"] == "IG_REALISTIC"
    assert "Spread" in prov["spread_model"] and "Slippage" in prov["slippage_model"]
    assert prov["sizing"]["risk_fraction"] == 0.01
    assert prov["engine_version"] == rec.engine_version and prov["engine_current"] is True
    assert prov["calendar_version"] and prov["ledger_sha256"] == rec.ledger_sha256
    assert prov["dataset_version_ids"] == list(rec.dataset_version_ids)
    assert prov["gate_set_version"].startswith("not evaluated")


def test_unknown_run_is_404(client):
    r = client.get("/api/research/runs/bt_nope/tearsheet")
    assert r.status_code == 404 and r.json()["code"] == "unknown_backtest"


# ------------------------------------------------------------------ Monte Carlo


def test_monte_carlo_is_deterministic_and_shows_its_seed(client, ledger):
    run_id = ledger["ids"]["9_26"]
    a = client.get(f"/api/research/runs/{run_id}/monte-carlo", params={"paths": 300, "seed": 5}).json()["data"]
    b = client.get(f"/api/research/runs/{run_id}/monte-carlo", params={"paths": 300, "seed": 5}).json()["data"]
    c = client.get(f"/api/research/runs/{run_id}/monte-carlo", params={"paths": 300, "seed": 6}).json()["data"]
    assert a == b and a["fan"] != c["fan"]
    assert a["seed"] == 5 and a["basis"] == "r_multiple" and a["risk_fraction"] == 0.01
    assert "seed 5" in a["method"]
    assert set(a["drawdown_percentiles"]) == {"p50", "p75", "p90", "p95", "p99"}
    assert a["fan"]["p5"][0] == a["initial_equity"]["value"]
    assert a["drawdown_percentiles"]["p95"]["estimated"] is True
    assert a["ruin_probability"]["estimated"] is True


def test_monte_carlo_of_a_legacy_run_resamples_returns(client, ledger):
    data = client.get(f"/api/research/runs/{ledger['ids']['legacy']}/monte-carlo",
                      params={"paths": 200}).json()["data"]
    assert data["basis"] == "trade_return" and data["notes"]


# ------------------------------------------------------------------ comparison


def test_compare_rebases_and_correlates(client, ledger):
    a, b = ledger["ids"]["7_22"], ledger["ids"]["11_26"]
    body = client.get("/api/research/compare", params={"runs": f"{a},{b}"}).json()
    view = body["data"]
    assert [r["run"]["run_id"] for r in view["runs"]] == [a, b]
    for r in view["runs"]:
        assert r["rebased"]["points"][0]["v"] == pytest.approx(100.0, abs=5.0)
        assert _stat(r, "sharpe")["figure"]["value"] is not None
    cells = {(c["a"], c["b"]): c for c in view["correlations"]}
    assert cells[(a, a)]["rho"]["value"] == 1.0
    off = cells[(a, b)]
    assert off["n_common"] > 20 and -1.0 <= off["rho"]["value"] <= 1.0
    assert off["rho"]["sample_size"] == off["n_common"]


def test_compare_refuses_one_or_seven_runs(client, ledger):
    assert client.get("/api/research/compare", params={"runs": ledger["ids"]["7_22"]}).status_code == 400
    many = ",".join(f"r{i}" for i in range(7))
    assert client.get("/api/research/compare", params={"runs": many}).status_code == 400


# ------------------------------------------------------------------ strategy


def test_strategy_overview(client, ledger):
    view = client.get(f"/api/research/strategies/{STRATEGY}/overview").json()["data"]
    assert view["runs_total"] == len(GRID) + 1
    assert view["gate_reports"][0]["report_id"] == ledger["report_id"]
    assert view["links"]["walk_forward"] == f"/research/walk-forward?strategy={STRATEGY}"
    assert view["parameters"]["tenkan_period"]["min"] == 5.0
    # The repository's report file is part of the ladder history.
    assert any(e["source"] == "report_file" for e in view["ladder_history"])
    assert view["lifecycle"] == [] and view["lifecycle_note"]


def test_strategy_overview_unknown_is_404(client):
    assert client.get("/api/research/strategies/nope/overview").status_code == 404


def test_walk_forward_reads_the_ladder_rung(client, ledger, monkeypatch):
    monkeypatch.chdir(REPO)
    view = client.get(f"/api/research/strategies/{WF_STRATEGY}/walk-forward").json()["data"]
    raw = json.loads((REPO / "research" / "reports" / f"{WF_STRATEGY}.json").read_text())
    rung = next(r for r in raw["rungs"] if r["name"] == "WALK_FORWARD")
    assert view["n_folds"] == len(rung["metrics"]["folds"])
    assert view["walk_forward_efficiency"]["value"] == pytest.approx(rung["metrics"]["walk_forward_efficiency"])
    f0 = rung["metrics"]["folds"][0]
    assert view["folds"][0]["efficiency"]["value"] == pytest.approx(f0["oos_rate"] / f0["is_rate"])
    assert view["folds"][0]["oos_rate"]["provenance"] == "walkforward"
    assert view["gates"] and all(g["rung"] == 2 for g in view["gates"])
    assert view["outcome"] == rung["outcome"]


def test_walk_forward_not_run_is_an_explicit_404(client):
    r = client.get("/api/research/strategies/tsmom_dual_horizon/walk-forward")
    assert r.status_code == 404 and r.json()["code"] == "walk_forward_not_run"


# ------------------------------------------------------------------ parameter surface


def test_parameter_surface_places_every_stored_run(client, ledger):
    body = client.get(f"/api/research/parameter-lab/{STRATEGY}/surface",
                      params={"x": "tenkan_period", "y": "kijun_period", "metric": "sharpe"}).json()
    view = body["data"]
    assert view["runs_used"] == len(GRID)
    # The legacy run has no job in the job ledger: unknown parameters, said so.
    assert view["runs_unknown_params"] == 1 and any("job ledger" in n for n in view["notes"])
    assert view["x_values"][0] == 5.0 and view["x_values"][-1] == 15.0  # declared domain
    cells = {(c["x"], c["y"]): c for c in view["cells"]}
    rec = _record(ledger, ledger["ids"]["9_26"])
    assert cells[(9.0, 26.0)]["score"]["value"] == pytest.approx(rec.metrics["sharpe"])
    assert cells[(10.0, 26.0)]["n_runs"] == 0 and cells[(10.0, 26.0)]["score"]["value"] is None
    assert {g["metric"] for g in view["plateau_gates"]} >= {"plateau_neighbourhood_median_ratio"}
    assert view["radius"] == 1 and "plateau" in view["plateau_definition"]


def test_parameter_surface_off_slice_runs_are_counted(client):
    view = client.get(f"/api/research/parameter-lab/{STRATEGY}/surface",
                      params={"x": "tenkan_period", "y": "adx_floor",
                              "fixed": json.dumps({"kijun_period": 22})}).json()["data"]
    assert view["runs_used"] == 3 and view["runs_other_params"] == 3


def test_parameter_surface_validates_its_inputs(client):
    base = f"/api/research/parameter-lab/{STRATEGY}/surface"
    assert client.get(base, params={"x": "tenkan_period", "y": "tenkan_period"}).status_code == 400
    assert client.get(base, params={"metric": "made_up"}).status_code == 400
    assert client.get(base, params={"fixed": "[1]"}).status_code == 400


# ------------------------------------------------------------------ jobs


def test_job_queue_reads_the_job_ledger(client, ledger):
    view = client.get("/api/research/jobs").json()["data"]
    assert view["counts"]["succeeded"] == len(GRID) + 1
    assert view["counts"]["pending"] == 1
    queued = next(j for j in view["jobs"] if j["status"] == "pending")
    assert view["jobs"][0]["status"] == "pending"  # queued before finished
    assert queued["eta_seconds"]["value"] is not None and queued["eta_seconds"]["estimated"]
    done = next(j for j in view["jobs"] if j["status"] == "succeeded" and j["job_type"] == "backtest")
    assert done["outputs"]["tearsheet"].startswith("/research/runs/bt_")


def test_job_queue_reads_shard_status_files(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shards = tmp_path / "research" / "reports" / "e4_pooled" / "shards"
    shards.mkdir(parents=True)
    (shards / "main.0-of-4.json").write_text(json.dumps({
        "stage": "main", "shard": {"k": 0, "n": 4, "of": 20}, "partial": True,
        "runs": [{"index": 0}, {"index": 4}], "wall_seconds": 100.0,
    }))
    k6 = tmp_path / "research" / "reports" / "campaign_k6" / "shards" / "fx"
    k6.mkdir(parents=True)
    lines = [json.dumps({"kind": "header", "stage": "screen", "indices": [0, 2, 4, 6]})]
    lines += [json.dumps({"kind": "row", "index": i, "seconds": 30.0}) for i in (0, 2)]
    (k6 / "screen_H4.0-of-2.jsonl").write_text("\n".join(lines) + "\n")
    view = client.get("/api/research/jobs").json()["data"]
    by_study = {s["study"]: s for s in view["shards"]}
    e4 = by_study["e4_pooled"]
    assert (e4["done"], e4["total"], e4["unit"]) == (2, 5, "candidates")
    assert e4["eta_seconds"]["value"] == pytest.approx(150.0)
    assert e4["progress"]["value"] == pytest.approx(0.4)
    k = by_study["campaign_k6"]
    assert (k["done"], k["total"]) == (2, 4) and k["eta_seconds"]["value"] == pytest.approx(60.0)


def test_job_queue_without_a_ledger_is_absent(api_env):
    app = create_app(load_settings(api_env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as c:
        login(c, "guest", VIEWER_PW)
        body = c.get("/api/research/jobs").json()
    assert body["data"]["jobs"] == [] and "No job ledger" in body["data"]["job_ledger"]


# ------------------------------------------------------------------ correlations


def _datastore(root: Path) -> None:
    from fiboki.data.integrity import IntegrityConfig, validate
    from fiboki.data.schema import DatasetKind, describe_frame
    from fiboki.data.store import DataStore
    from fiboki.data.versioning import TransformationStep
    from tests.data_fixtures import make_bars, make_metadata

    with DataStore.initialise(root) as store:
        for sym, seed in (("EURUSD", 1), ("GBPUSD", 2), ("USDJPY", 3)):
            frame = make_bars(instrument=sym, timeframe=Timeframe.D1, periods=300, seed=seed,
                              start="2025-01-06")
            raw = store.write_raw(frame, make_metadata(frame))
            report = validate(frame)
            meta = describe_frame(frame, source="test", source_identifier="synthetic",
                                  timezone_of_origin="UTC", quality=report.quality,
                                  kind=DatasetKind.VALIDATED)
            step = TransformationStep(operation="validate",
                                      parameters={"integrity_config": IntegrityConfig().to_dict()},
                                      code_version="2.0.0", inputs=(raw.version_id,))
            store.write_canonical(frame, meta, source_version=raw.version, transformation=step,
                                  integrity=report)


def test_correlations_known_answer(api_env, tmp_path):
    import numpy as np

    from fiboki.data.store import DataStore

    root = tmp_path / "data"
    _datastore(root)
    env = {**api_env, "FIBOKI_DATA_ROOT": str(root)}
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as c:
        login(c, "guest", VIEWER_PW)
        r = c.get("/api/research/correlations", params={"timeframe": "D1", "window": 50})
        bad = c.get("/api/research/correlations", params={"window": 7})
    assert r.status_code == 200, r.text
    view = r.json()["data"]
    assert view["instruments"] == ["EURUSD", "GBPUSD", "USDJPY"]
    assert view["bars_used"] == 50
    with DataStore(root) as store:
        a, _ = store.read_latest("EURUSD", "D1")
        b, _ = store.read_latest("GBPUSD", "D1")
    ra = np.log(a["close"]).diff().dropna().tail(50)
    rb = np.log(b["close"]).diff().dropna().tail(50)
    assert view["matrix"][0][1] == pytest.approx(float(np.corrcoef(ra, rb)[0, 1]))
    assert view["matrix"][0][0] == pytest.approx(1.0)
    assert bad.status_code == 400


def test_correlations_without_data_are_503(client):
    r = client.get("/api/research/correlations")
    assert r.status_code == 503 and r.json()["code"] == "market_data_not_mounted"
