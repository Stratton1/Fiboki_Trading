"""Generate the research e2e fixtures from the REAL backend.

The Playwright specs for the research analytics screens mock the API, and the
payloads they serve are produced here by the actual code path: the research
worker's job handlers run real engine backtests of registered strategies on
synthetic bars into a file-backed research ledger and a durable job ledger,
then the FastAPI app answers every research analytics route and the
responses are written as JSON beside this file. So a fixture can only contain
what the API can produce.

Run from the repository root:

    PYTHONPATH=src .venv/bin/python apps/web/tests/e2e/research-fixtures/generate.py

Deterministic: synthetic bars are seeded, the Monte Carlo seed is fixed, ids
are rewritten to stable ones (``bt_0001`` ...), and timestamps of generation
(``as_of``) are pinned.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from fiboki.agents.jobs import register_research_handlers  # noqa: E402
from fiboki.agents.orchestrator import JobSpec, JobType, Orchestrator  # noqa: E402
from fiboki.agents.tools import InMemoryBarSource  # noqa: E402
from fiboki.api.app import create_app  # noqa: E402
from fiboki.api.routers import research_analytics  # noqa: E402
from fiboki.api.settings import load_settings  # noqa: E402
from fiboki.core.enums import Timeframe  # noqa: E402
from fiboki.data.schema import PriceBasis, canonical_frame  # noqa: E402
from fiboki.research.artefacts import ResearchStore  # noqa: E402
from fiboki.research.experiment import ExperimentLedger  # noqa: E402
from fiboki.strategy.dsl import StrategyDocument  # noqa: E402
from fiboki.strategy.registry import StrategyRegistry  # noqa: E402
from tests.agents_fixtures import gbp_fx_frames, trending_bars  # noqa: E402

OUT = Path(__file__).resolve().parent
PIN = "2026-10-18T12:00:00Z"
MAIN = "ichimoku_kumo_trend"
SECOND = "rsi_band_mean_reversion"


def _doc(sid: str) -> StrategyDocument:
    return StrategyDocument.from_json((REPO / "research" / "strategies" / f"{sid}.json").read_text())


def _universe_frames(n: int = 520) -> dict[str, pd.DataFrame]:
    """Daily closes with a shared dollar factor, so the matrix has structure."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-06-03", periods=n, freq="1D", tz="UTC", name="timestamp")
    usd = rng.normal(0, 0.004, n)
    risk = rng.normal(0, 0.005, n)
    loadings = {
        "EURUSD": (-1.0, 0.1), "GBPUSD": (-0.9, 0.3), "AUDUSD": (-0.6, 0.9), "NZDUSD": (-0.6, 0.8),
        "USDJPY": (0.8, 0.5), "USDCHF": (0.95, -0.2), "USDCAD": (0.7, -0.5), "XAUUSD": (-0.5, -0.6),
    }
    out = {}
    for sym, (bu, br) in loadings.items():
        r = bu * usd + br * risk + rng.normal(0, 0.003, n)
        close = 1.2 * np.exp(np.cumsum(r))
        open_ = np.r_[close[0], close[:-1]]
        hi = np.maximum(open_, close) * 1.002
        lo = np.minimum(open_, close) * 0.998
        frame = pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close, "volume": 1000}, index=idx)
        out[sym] = canonical_frame(frame, instrument=sym, timeframe=Timeframe.D1, price_basis=PriceBasis.MID)
    return out


def _datastore(root: Path) -> None:
    from fiboki.data.integrity import IntegrityConfig, validate
    from fiboki.data.schema import DatasetKind, describe_frame
    from fiboki.data.store import DataStore
    from fiboki.data.versioning import TransformationStep
    from tests.data_fixtures import make_metadata

    with DataStore.initialise(root) as store:
        for frame in _universe_frames().values():
            raw = store.write_raw(frame, make_metadata(frame))
            report = validate(frame)
            meta = describe_frame(frame, source="fixture", source_identifier="synthetic",
                                  timezone_of_origin="UTC", quality=report.quality, kind=DatasetKind.VALIDATED)
            step = TransformationStep(operation="validate",
                                      parameters={"integrity_config": IntegrityConfig().to_dict()},
                                      code_version="2.0.0", inputs=(raw.version_id,))
            store.write_canonical(frame, meta, source_version=raw.version, transformation=step, integrity=report)


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="fiboki-research-fixtures-"))
    state, db, data = work / "state", work / "experiments.sqlite", work / "data"
    reports = work / "reports"
    reports.mkdir()
    for sid in ("donchian_breakout_atr", MAIN):
        shutil.copy(REPO / "research" / "reports" / f"{sid}.json", reports / f"{sid}.json")
    _shards(reports)
    _datastore(data)

    h4 = trending_bars(instrument="EURUSD", timeframe=Timeframe.H4, periods=6000, seed=7, drift=0.0002)
    h1 = trending_bars(instrument="EURUSD", timeframe=Timeframe.H1, periods=9000, seed=11)
    bars = InMemoryBarSource({("EURUSD", "H4"): h4, ("EURUSD", "H1"): h1, **gbp_fx_frames()})
    registry = StrategyRegistry()
    for sid in (MAIN, SECOND):
        registry.register(_doc(sid))
    ids: list[str] = []
    with ResearchStore(ledger=ExperimentLedger(db)) as store:
        orch = Orchestrator(path=state / "jobs.sqlite")
        register_research_handlers(orch, research=store, strategies=registry, bars=bars)

        def bt(sid: str, tf: str, params: dict | None, key: str) -> str:
            payload = {"strategy_id": sid, "instrument": "EURUSD", "timeframe": tf, "account_ccy": "USD"}
            if params:
                payload["parameters"] = params
            orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research", payload=payload, idempotency_key=key))
            rec = orch.drain("research")[0]
            assert rec.result, rec.error
            return str(rec.result["backtest_id"])

        main_id = bt(MAIN, "H4", None, "main")
        ids.append(main_id)
        orch.submit(JobSpec(job_type=JobType.VALIDATION, queue="research",
                            payload={"backtest_id": main_id, "seed": 1, "bootstrap_samples": 100},
                            idempotency_key="val_main"))
        orch.drain("research")
        ids.append(bt(SECOND, "H1", None, "second"))
        for tenkan in (7, 8, 9, 10, 11):
            for kijun in (22, 24, 26, 28, 30):
                if (tenkan, kijun) == (9, 26):
                    continue
                ids.append(bt(MAIN, "H4", {"tenkan_period": tenkan, "kijun_period": kijun}, f"s_{tenkan}_{kijun}"))
        # A queued job and one that failed for good, for the queue view.
        orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="research", idempotency_key="queued",
                            payload={"strategy_id": MAIN, "instrument": "EURUSD", "timeframe": "D1"}))
        orch.submit(JobSpec(job_type=JobType.BACKTEST, queue="slow", max_attempts=1, idempotency_key="broken",
                            payload={"strategy_id": MAIN, "instrument": "EURUSD", "timeframe": "M5"}))
        orch.drain("slow")
        orch.close()

    research_analytics.REPORTS_ROOT = reports
    os.environ["FIBOKI_OPERATORS"] = "fixture:viewer:" + hashlib.sha256(b"fixture-password").hexdigest()
    env = {"FIBOKI_STATE_DIR": str(state), "FIBOKI_EXPERIMENT_DB": str(db), "FIBOKI_DATA_ROOT": str(data),
           "FIBOKI_ALLOWED_ORIGINS": "https://fixtures.invalid", "FIBOKI_COOKIE_SECURE": "false",
           "FIBOKI_SESSION_SECRET": "fixture-secret"}
    app = create_app(load_settings(env), configure_logs=False)
    client = TestClient(app, base_url="https://fixtures.invalid")
    # Reads need a principal; mint one through the app's own dev directory.
    _login(client)

    stable = {rid: f"bt_{i + 1:04d}" for i, rid in enumerate(ids)}
    second_id = ids[1]
    other = ids[2]
    calls = {
        "runs": "/api/research/runs?limit=300",
        "tearsheet": f"/api/research/runs/{ids[0]}/tearsheet",
        "tearsheet_second": f"/api/research/runs/{second_id}/tearsheet",
        "monte_carlo": f"/api/research/runs/{ids[0]}/monte-carlo?paths=1000&seed=20261018",
        "compare": f"/api/research/compare?runs={ids[0]},{second_id},{other}",
        "overview": f"/api/research/strategies/{MAIN}/overview",
        "overview_donchian": "/api/research/strategies/donchian_breakout_atr/overview",
        "walk_forward": "/api/research/strategies/donchian_breakout_atr/walk-forward",
        "surface": f"/api/research/parameter-lab/{MAIN}/surface?metric=sharpe&x=tenkan_period&y=kijun_period",
        "parameter_lab": f"/api/research/parameter-lab/{MAIN}",
        "strategies": "/api/research/strategies",
        "validation": "/api/research/validation",
        "jobs": "/api/research/jobs",
        "correlations": "/api/research/correlations?timeframe=D1&window=100",
    }
    for name, path in calls.items():
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:400])
        text = _stabilise(r.text, stable, work)
        (OUT / f"{name}.json").write_text(json.dumps(json.loads(text), indent=1, sort_keys=True) + "\n")
        print(f"{name}: {len(text):,} bytes")
    shutil.rmtree(work, ignore_errors=True)


def _login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": "fixture", "password": "fixture-password"},
                    headers={"Origin": "https://fixtures.invalid"})
    assert r.status_code == 200, r.text


def _stabilise(text: str, stable: dict[str, str], work: Path) -> str:
    for real, fixed in stable.items():
        text = text.replace(real, fixed)
    text = text.replace(str(work), "/var/fiboki")
    text = re.sub(r'"as_of": ?"[^"]+"', f'"as_of":"{PIN}"', text)
    text = re.sub(r'"(job_[0-9a-f]{16})"', lambda m: '"job_' + hashlib.sha1(m.group(1).encode()).hexdigest()[:16] + '"', text)
    text = re.sub(r'"(val_[0-9a-f]{16})"', '"val_0001"', text)
    return text


def _shards(reports: Path) -> None:
    e4 = reports / "e4_pooled" / "shards"
    e4.mkdir(parents=True)
    for k in range(4):
        done = [k + 4 * i for i in range(min(43, 18 + 8 * k))]
        (e4 / f"main.{k}-of-4.json").write_text(json.dumps({
            "study": "e4_pooled", "stage": "main", "shard": {"k": k, "n": 4, "of": 172},
            "partial": True, "runs": [{"index": i} for i in done], "wall_seconds": 61.0 * len(done)}))
    k6 = reports / "campaign_k6" / "shards" / "fx16"
    k6.mkdir(parents=True)
    lines = [json.dumps({"kind": "header", "stage": "screen", "indices": list(range(0, 400, 2))})]
    lines += [json.dumps({"kind": "row", "index": i, "seconds": 41.5}) for i in range(0, 400, 2)]
    (k6 / "screen_H4.0-of-2.jsonl").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
