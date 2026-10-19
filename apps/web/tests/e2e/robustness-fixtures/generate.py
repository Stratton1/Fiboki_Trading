"""Generate the robustness e2e fixtures from the REAL backend (wave 3R).

Engine backtests of ``ichimoku_kumo_trend`` on seeded synthetic H4 bars are run
through the research worker's handlers into a file-backed ledger; every
re-run diagnostic is then queued through the API's admin POST and executed by
a worker-side orchestrator with the real ``robustness`` handler; finally the
API answers every robustness route and the responses are written beside this
file. A fixture can only contain what the API can produce.

    PYTHONPATH=src .venv/bin/python apps/web/tests/e2e/robustness-fixtures/generate.py

Deterministic up to ids and generation times, which are rewritten to stable
values (``bt_0001``, ``2026-10-19T00:00:00Z``).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from fiboki.agents.jobs import register_research_handlers  # noqa: E402
from fiboki.agents.orchestrator import JobSpec, JobType, Orchestrator  # noqa: E402
from fiboki.agents.tools import InMemoryBarSource  # noqa: E402
from fiboki.api.app import create_app  # noqa: E402
from fiboki.api.settings import load_settings  # noqa: E402
from fiboki.core.enums import Timeframe  # noqa: E402
from fiboki.research.artefacts import ResearchStore  # noqa: E402
from fiboki.research.experiment import ExperimentLedger  # noqa: E402
from fiboki.research.robustness.store import RobustnessStore, store_path  # noqa: E402
from fiboki.strategy.dsl import StrategyDocument  # noqa: E402
from fiboki.strategy.registry import StrategyRegistry  # noqa: E402
from tests.agents_fixtures import gbp_fx_frames, trending_bars  # noqa: E402

OUT = Path(__file__).resolve().parent
STRATEGY = "ichimoku_kumo_trend"
ADMIN_PW = "fixture-admin"
ORIGIN = "https://workstation.fiboki.test"
#: Default options: the workstation queues and reads every diagnostic with them.
OPTIONS: dict[str, dict] = {k: {} for k in ("warmup", "lookahead", "execution", "perturbation", "random", "noise")}


def _bars() -> InMemoryBarSource:
    frame = trending_bars(instrument="EURUSD", timeframe=Timeframe.H4, periods=2400, seed=7, drift=0.0002)
    return InMemoryBarSource({("EURUSD", "H4"): frame, **gbp_fx_frames()})


def _registry() -> StrategyRegistry:
    reg = StrategyRegistry()
    reg.register(StrategyDocument.from_json((REPO / "research" / "strategies" / f"{STRATEGY}.json").read_text()))
    return reg


def main() -> None:
    import hashlib

    root = Path(tempfile.mkdtemp())
    state, db = root / "state", root / "experiments.sqlite"
    ids: list[str] = []
    with ResearchStore(ledger=ExperimentLedger(db)) as store:
        orch = Orchestrator(path=state / "jobs.sqlite")
        register_research_handlers(orch, research=store, strategies=_registry(), bars=_bars())
        for tenkan in (7, 9, 11):
            orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research", idempotency_key=f"bt{tenkan}",
                                payload={"strategy_id": STRATEGY, "instrument": "EURUSD", "timeframe": "H4",
                                         "account_ccy": "USD",
                                         "parameters": {"tenkan_period": tenkan, "kijun_period": 26}}))
        ids = [str(r.result["backtest_id"]) for r in orch.drain("research", max_jobs=10)]
        orch.close()
    os.environ["FIBOKI_OPERATORS"] = f"joe:admin:{hashlib.sha256(ADMIN_PW.encode()).hexdigest()}"
    env = {"FIBOKI_STATE_DIR": str(state), "FIBOKI_EXPERIMENT_DB": str(db), "FIBOKI_ALLOWED_ORIGINS": ORIGIN,
           "FIBOKI_COOKIE_SECURE": "false", "FIBOKI_SESSION_SECRET": "fixtures"}
    run = ids[1]
    out: dict[str, object] = {}
    with TestClient(create_app(load_settings(env), configure_logs=False), base_url=ORIGIN) as c:
        c.post("/api/auth/login", json={"username": "joe", "password": ADMIN_PW}, headers={"Origin": ORIGIN})
        csrf = {"Origin": ORIGIN, "X-Fiboki-CSRF": c.cookies.get("fiboki_csrf", "")}
        out["index_not_run"] = c.get(f"/api/research/runs/{run}/robustness").json()
        out["rerun_not_run"] = c.get(f"/api/research/runs/{run}/robustness/warmup").json()
        for kind, opts in OPTIONS.items():
            r = c.post(f"/api/research/runs/{run}/robustness/{kind}", json={"reason": "fixtures", "options": opts},
                       headers=csrf)
            assert r.status_code == 202, r.text
        out["rerun_pending"] = c.get(f"/api/research/runs/{run}/robustness/warmup",
                                     params={"options": json.dumps(OPTIONS["warmup"])}).json()
        with ResearchStore(ledger=ExperimentLedger(db)) as store:
            orch = Orchestrator(path=state / "jobs.sqlite")
            register_research_handlers(orch, research=store, strategies=_registry(), bars=_bars(),
                                       robustness=RobustnessStore(store_path(state)))
            done = orch.drain("research", max_jobs=20)
            assert all(j.status.value == "succeeded" for j in done), [j.error for j in done]
            orch.close()
        out["index"] = c.get(f"/api/research/runs/{run}/robustness").json()
        for kind, opts in OPTIONS.items():
            out[f"rerun_{kind}"] = c.get(f"/api/research/runs/{run}/robustness/{kind}",
                                         params={"options": json.dumps(opts)}).json()
        for what in ("bands", "breakdowns", "consistency", "fills"):
            out[what] = c.get(f"/api/research/runs/{run}/robustness/{what}").json()
        out["profile"] = c.get(f"/api/research/strategies/{STRATEGY}/optimisation-profile",
                               params={"objective": "sharpe"}).json()
        out["rankings"] = c.get("/api/research/rankings", params={"objective": "sharpe",
                                                                  "strategy_id": STRATEGY}).json()
    text = json.dumps(out, sort_keys=True)
    for i, rid in enumerate(ids, start=1):
        text = text.replace(rid, f"bt_{i:04d}")
    text = re.sub(r"job_[0-9a-f]{16}", "job_0000000000000001", text)
    text = re.sub(r'"(as_of|computed_at|updated_at|created_at)": "[^"]+"', r'"\1": "2026-10-19T00:00:00Z"', text)
    for name, payload in json.loads(text).items():
        (OUT / f"{name}.json").write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
