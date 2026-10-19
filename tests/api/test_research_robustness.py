"""The robustness routes (api/routers/research_robustness.py), end to end.

Stored runs are produced the honest way (the research worker's backtest handler
through a durable job ledger); re-run diagnostics are queued through the API's
admin POST, executed by a worker-side orchestrator over the SAME job ledger
with the real handler, and read back from the cache the API reads read-only.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fiboki.agents.jobs import register_research_handlers
from fiboki.agents.orchestrator import JobSpec, JobStatus, JobType, Orchestrator
from fiboki.agents.tools import InMemoryBarSource
from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.core.enums import Timeframe
from fiboki.research.artefacts import ResearchStore
from fiboki.research.experiment import ExperimentLedger
from fiboki.research.robustness.store import RobustnessStore, store_path
from fiboki.strategy.dsl import StrategyDocument
from fiboki.strategy.registry import StrategyRegistry
from fiboki.validation.holdout import HoldoutRegistry
from tests.agents_fixtures import gbp_fx_frames, trending_bars
from tests.api.conftest import ADMIN_PW, ORIGIN, VIEWER_PW, csrf_headers, login

STRATEGY = "ichimoku_kumo_trend"
REPO = Path(__file__).resolve().parents[2]
GRID = [(7, 26), (9, 26), (11, 26)]
SMALL = {
    "warmup": {"extra_bars": [60]},
    "lookahead": {"samples": 12},
    "execution": {"skip_levels": [0.1], "skip_seeds": 2, "delays": [1], "multipliers": [1.0, 2.0]},
    "perturbation": {"n": 4},
    "random": {"n_runs": 30},
    "noise": {"levels": [0.1], "n": 2},
}


def _bars() -> InMemoryBarSource:
    frame = trending_bars(instrument="EURUSD", timeframe=Timeframe.H4, periods=2400, seed=7, drift=0.0002)
    return InMemoryBarSource({("EURUSD", "H4"): frame, **gbp_fx_frames()})


def _registry() -> StrategyRegistry:
    reg = StrategyRegistry()
    reg.register(StrategyDocument.from_json((REPO / "research" / "strategies" / f"{STRATEGY}.json").read_text()))
    return reg


@pytest.fixture(scope="module")
def ledger(tmp_path_factory):
    root = tmp_path_factory.mktemp("robustness")
    state = root / "state"
    db = root / "experiments.sqlite"
    ids: dict[str, str] = {}
    with ResearchStore(ledger=ExperimentLedger(db)) as store:
        orch = Orchestrator(path=state / "jobs.sqlite")
        register_research_handlers(orch, research=store, strategies=_registry(), bars=_bars())
        for tenkan, kijun in GRID:
            orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research", idempotency_key=f"bt_{tenkan}",
                                payload={"strategy_id": STRATEGY, "instrument": "EURUSD", "timeframe": "H4",
                                         "account_ccy": "USD",
                                         "parameters": {"tenkan_period": tenkan, "kijun_period": kijun}}))
        done = orch.drain("research", max_jobs=10)
        assert all(r.status is JobStatus.SUCCEEDED for r in done), [r.error for r in done]
        for r in done:
            ids[str(r.spec.payload["parameters"]["tenkan_period"])] = str(r.result["backtest_id"])
        orch.close()
    return {"db": db, "state": state, "ids": ids}


def _app(ledger, api_env):
    env = {**api_env, "FIBOKI_EXPERIMENT_DB": str(ledger["db"]), "FIBOKI_STATE_DIR": str(ledger["state"])}
    return create_app(load_settings(env), configure_logs=False)


@pytest.fixture
def client(ledger, api_env):
    with TestClient(_app(ledger, api_env), base_url=ORIGIN) as c:
        assert login(c, "guest", VIEWER_PW).status_code == 200
        yield c


@pytest.fixture
def admin(ledger, api_env):
    with TestClient(_app(ledger, api_env), base_url=ORIGIN) as c:
        assert login(c, "joe", ADMIN_PW).status_code == 200
        yield c


def _record(ledger, run_id):
    with ResearchStore(ledger=ExperimentLedger(ledger["db"])) as store:
        return store.get_backtest(run_id)


def _work(ledger) -> list:
    """The research worker: drain the shared job ledger with the real handlers."""
    with ResearchStore(ledger=ExperimentLedger(ledger["db"])) as store:
        orch = Orchestrator(path=ledger["state"] / "jobs.sqlite")
        register_research_handlers(orch, research=store, strategies=_registry(), bars=_bars(),
                                   robustness=RobustnessStore(store_path(ledger["state"])))
        try:
            return orch.drain("research", max_jobs=20)
        finally:
            orch.close()


# ------------------------------------------------------------------ ledger diagnostics


def test_bands_are_deterministic_ordered_and_exclude_the_holdout(client, ledger):
    run_id = ledger["ids"]["9"]
    a = client.get(f"/api/research/runs/{run_id}/robustness/bands", params={"seed": 5, "n_boot": 300}).json()
    b = client.get(f"/api/research/runs/{run_id}/robustness/bands", params={"seed": 5, "n_boot": 300}).json()
    assert a["data"] == b["data"]
    view = a["data"]
    assert {x["key"] for x in view["bands"]} == {"cagr_pct", "max_drawdown_pct", "sharpe", "profit_factor",
                                                  "win_rate_pct"}
    for band in view["bands"]:
        if band["p5"]["value"] is not None:
            assert band["p5"]["value"] <= band["p50"]["value"] <= band["p95"]["value"]
            assert band["p50"]["estimated"] is True
    # No registry segment: the provisional boundary (last 20% of the run) is excluded.
    assert view["boundary"]["basis"] == "provisional"
    assert view["boundary"]["trades_excluded"]["value"] >= 0
    assert view["boundary"]["points_excluded"]["value"] > 0
    assert any(c["code"] == "advisory_diagnostic" for c in a["caveats"])
    other = client.get(f"/api/research/runs/{run_id}/robustness/bands", params={"seed": 6, "n_boot": 300}).json()
    assert other["data"]["bands"] != view["bands"]


def test_breakdowns_partition_the_research_slice(client, ledger):
    run_id = ledger["ids"]["9"]
    view = client.get(f"/api/research/runs/{run_id}/robustness/breakdowns").json()["data"]
    assert set(view["groups"]) == {"exit_reason", "direction", "year", "month", "weekday", "hour", "session"}
    rec = _record(ledger, run_id)
    boundary = pd.Timestamp(view["boundary"]["start"])
    kept = [t for t in rec.trades if pd.Timestamp(t["exit_time"]) < boundary]
    for name, buckets in view["groups"].items():
        assert sum(b["trades"]["value"] for b in buckets) == len(kept), name
        assert sum(b["net_pnl"]["value"] for b in buckets) == pytest.approx(sum(t["net_pnl"] for t in kept))
    assert "DIRECTION" in view["entry_reason_note"]


def test_consistency_splits_and_shares(client, ledger):
    view = client.get(f"/api/research/runs/{ledger['ids']['9']}/robustness/consistency",
                      params={"k": 3}).json()["data"]
    assert len(view["periods"]) == 3 and view["k"] == 3
    assert [i["key"] for i in view["instruments"]] == ["EURUSD"]
    signed = [p["positive"] for p in view["periods"] if p["positive"] is not None]
    if signed:
        assert view["period_positive_share"]["value"] == pytest.approx(sum(signed) / len(signed))
    assert client.get(f"/api/research/runs/{ledger['ids']['9']}/robustness/consistency",
                      params={"k": 1}).status_code == 422


def test_fill_detail_reads_the_engine_flags_and_costs(client, ledger):
    run_id = ledger["ids"]["9"]
    view = client.get(f"/api/research/runs/{run_id}/robustness/fills").json()["data"]
    table = view["table"]
    n = len(table["trade_id"])
    assert view["trades_total"]["value"] == n
    assert view["trades_recorded"]["value"] == n  # filed by the current engine: every row has flags
    for i in range(n):
        costs = sum(table[k][i] for k in ("spread_cost", "slippage_cost", "commission", "financing_cost"))
        assert table["gross_pnl"][i] - costs == pytest.approx(table["net_pnl"][i], abs=1e-6)
        assert table["recorded"][i] and table["rules"][i]
    assert view["intrabar_policy"] == "stop_first"
    assert set(view["rule_counts"]) <= set(view["rule_definitions"])


def test_rankings_penalise_below_eighty_trades_and_never_flatter(client, ledger):
    body = client.get("/api/research/rankings", params={"objective": "sharpe", "strategy_id": STRATEGY}).json()
    view = body["data"]
    assert view["min_trades"] == 80
    scores = [r["score"]["value"] for r in view["rows"]]
    assert scores == sorted(scores, reverse=True)
    for r in view["rows"]:
        n = r["trades"]["value"]
        raw, score = r["raw"]["value"], r["score"]["value"]
        if n < 80 and raw > 0:
            assert score == pytest.approx(raw * n / 80)
        else:
            assert score == pytest.approx(raw)
    assert client.get("/api/research/rankings", params={"objective": "headline"}).status_code == 400
    pf = client.get("/api/research/rankings", params={"objective": "profit_factor"}).json()["data"]
    for r in pf["rows"]:
        if r["raw"]["value"] > 1 and r["trades"]["value"] < 80:
            assert 1 <= r["score"]["value"] < r["raw"]["value"]


def test_optimisation_profile_reports_median_not_peak(client, ledger):
    view = client.get(f"/api/research/strategies/{STRATEGY}/optimisation-profile",
                      params={"objective": "net_profit"}).json()["data"]
    values = sorted(p["objective"]["value"] for p in view["points"] if p["objective"]["value"] is not None)
    assert view["n_points"]["value"] == len(GRID)
    assert view["peak"]["value"] == pytest.approx(values[-1])
    assert view["median"]["value"] == pytest.approx(float(pd.Series(values).median()))
    assert view["profitable_share"]["value"] == pytest.approx(sum(v > 0 for v in values) / len(values))
    assert client.get("/api/research/strategies/nope/optimisation-profile").status_code == 404


# ------------------------------------------------------------------ re-run jobs


def test_index_lists_every_kind_not_run(client, ledger):
    view = client.get(f"/api/research/runs/{ledger['ids']['7']}/robustness").json()["data"]
    assert view["rerunnable"] is True, view["reason"]
    assert {k["kind"] for k in view["kinds"]} == set(SMALL)
    assert view["binding"]["parameters"]["tenkan_period"] == 7
    assert pd.Timestamp(view["binding"]["window_end"]) <= pd.Timestamp(view["binding"]["boundary"]["start"])


def test_queue_is_admin_only(client, ledger):
    r = client.post(f"/api/research/runs/{ledger['ids']['7']}/robustness/warmup",
                    json={"reason": "check warm-up"}, headers=csrf_headers(client))
    assert r.status_code == 403


@pytest.mark.parametrize("kind", list(SMALL))
def test_queue_run_and_read_every_rerun_kind(admin, ledger, kind):
    run_id = ledger["ids"]["9"]
    opts = SMALL[kind]
    r = admin.post(f"/api/research/runs/{run_id}/robustness/{kind}",
                   json={"reason": f"robustness {kind}", "options": opts}, headers=csrf_headers(admin))
    assert r.status_code == 202, r.text
    assert r.json()["data"]["status"] in ("pending", "done")
    # Queueing twice is one job (idempotent on the cache key).
    again = admin.post(f"/api/research/runs/{run_id}/robustness/{kind}",
                       json={"reason": "again", "options": opts}, headers=csrf_headers(admin)).json()["data"]
    assert again["cache_key"] == r.json()["data"]["cache_key"]
    q = {"options": json.dumps(opts)}
    before = admin.get(f"/api/research/runs/{run_id}/robustness/{kind}", params=q).json()
    if before["data"]["status"]["status"] != "done":
        assert before["data"][kind] is None and before["source"]["kind"] == "absent"
        done = _work(ledger)
        assert all(j.status is JobStatus.SUCCEEDED for j in done), [j.error for j in done]
    after = admin.get(f"/api/research/runs/{run_id}/robustness/{kind}", params=q).json()
    data = after["data"]
    assert data["status"]["status"] == "done"
    assert data["status"]["progress"]["value"] == 1.0
    assert data[kind] is not None
    prov = data["provenance"]
    # The look-ahead detector re-prepares truncated frames; it needs no engine run.
    assert prov["engine"]["engine_version"] and prov["engine_runs"]["value"] >= (kind != "lookahead")
    assert pd.Timestamp(prov["window_end"]) <= pd.Timestamp(prov["boundary"]["start"])
    # The window stops at the boundary, so the stored run is not covered and
    # no reproduction is claimed either way.
    assert prov["covers_stored_run"] is False and prov["reproduces_stored"] is None
    jobs = admin.get("/api/research/jobs").json()["data"]["jobs"]
    mine = [j for j in jobs if j["job_type"] == "robustness"]
    assert mine and all(j["status"] == "succeeded" for j in mine)


def test_unknown_kind_and_bad_options(admin, ledger):
    run_id = ledger["ids"]["9"]
    assert admin.get(f"/api/research/runs/{run_id}/robustness/astrology").status_code == 404
    r = admin.post(f"/api/research/runs/{run_id}/robustness/noise", json={"reason": "bad options", "options": {"x": 1}},
                   headers=csrf_headers(admin))
    assert r.status_code == 400


def test_a_registry_segment_moves_the_boundary_and_is_never_crossed(ledger, api_env):
    """With the ladder's segment defined, every diagnostic stops at ITS start."""
    state = ledger["state"]
    rec = _record(ledger, ledger["ids"]["11"])
    fp = next(iter(rec.data_fingerprint.values()))
    first, last = pd.Timestamp(fp["first"]), pd.Timestamp(fp["last"])
    with HoldoutRegistry(state / "holdout.sqlite") as reg:
        seg = reg.define(rec.dataset_version_ids[0], data_start=first, data_end=last, holdout_fraction=0.5)
    try:
        with TestClient(_app(ledger, api_env), base_url=ORIGIN) as c:
            login(c, "guest", VIEWER_PW)
            for path in ("bands", "breakdowns", "consistency", "fills"):
                view = c.get(f"/api/research/runs/{rec.backtest_id}/robustness/{path}").json()["data"]
                assert view["boundary"]["basis"] == "registry", path
                assert pd.Timestamp(view["boundary"]["start"]) == seg.holdout_start
            fills = c.get(f"/api/research/runs/{rec.backtest_id}/robustness/fills").json()["data"]["table"]
            assert all(pd.Timestamp(t) < seg.holdout_start for t in fills["exit_time"])
            idx = c.get(f"/api/research/runs/{rec.backtest_id}/robustness").json()["data"]
            assert pd.Timestamp(idx["binding"]["window_end"]) == seg.holdout_start
    finally:
        # The registry is append-only by trigger; the module fixture's file is
        # replaced so later tests see no segment.
        conn = sqlite3.connect(state / "holdout.sqlite")
        conn.close()
        (state / "holdout.sqlite").unlink()
