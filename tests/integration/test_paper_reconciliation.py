"""Backtest-versus-paper reconciliation (ops/reconcile.py) on the whole paper composition.

The restore suite's harness runs the real paper runtime forward over a
synthetic EURUSD H1 series; the reconciliation re-runs the same bound document
through the backtest engine over the same bars and compares trade for trade.

* Uninterrupted: every paper trade matches an engine trade on instrument,
  direction, entry and exit time, entry and exit price and size. The only
  differences are the seeded slippage draws, which are keyed on a bar index
  the two paths count from different origins; they are reported and
  attributed (``slippage_draw``), and nothing is unexplained.
* Deterministic: the same inputs give the same report digest.
* A pause: the trades the engine takes while the bot is paused are
  ``backtest_only`` with reason ``operator_control``; nothing is unexplained.
* The job: requested through the API, run by ``run_jobs`` (a separate process
  in production), read back through the API.
* Refusals: no bars covering the session, a changed wiring file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.core.durable import read_payloads
from fiboki.entrypoints.ops_jobs import reconcile_session, run_jobs
from fiboki.entrypoints.paper_forward import load_wiring
from fiboki.ops.controls import BotControlLedger
from fiboki.ops.reconcile import ReconcileInputs, reconcile
from tests.api.conftest import OPERATOR_PW, ORIGIN, csrf_headers, login
from tests.integration import test_paper_forward_restore as harness

SID = "forward-restore_test"


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return harness._frame()


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory, frame: pd.DataFrame) -> harness.Account:
    return harness.run_uninterrupted(tmp_path_factory.mktemp("recon_baseline"), frame)


def _inputs(acct: harness.Account, frame: pd.DataFrame) -> ReconcileInputs:
    wired = load_wiring(acct.wiring)
    return ReconcileInputs(
        session_id=SID,
        document=acct.document.bind_defaults(),
        timeframe=wired.timeframe,
        instruments=wired.instruments,
        bars={"EURUSD": frame},
        dataset_ids={"EURUSD": "synthetic_eurusd_h1"},
        initial_balance=wired.initial_balance,
        account_ccy=wired.account_ccy,
        profile_name=wired.broker_profile,
        risk_fraction=wired.risk_fraction,
        sizing_policy_id=wired.sizing_policy_id,
        max_concurrent=wired.max_concurrent,
        paper_trades=acct.trades(),
        journal_events=[json.loads(p) for p in read_payloads(acct.ledger / "journal.jsonl")],
        wiring_sha256=wired.sha256,
        wiring_version=wired.version,
        paper_history_bars=int(wired.feed["history_bars"]),
    )


def test_the_uninterrupted_paper_record_matches_the_engine_trade_for_trade(
    baseline: harness.Account, frame: pd.DataFrame
) -> None:
    report = reconcile(_inputs(baseline, frame))
    assert report.paper_trades == report.backtest_trades == report.matched == len(baseline.trades()) >= 2
    assert report.paper_only == report.backtest_only == 0
    assert report.unexplained == 0, [d for d in report.divergences if not d.explained]
    differing = {d.field for d in report.divergences}
    assert differing <= {"slippage_cost", "net_pnl"}, differing
    assert {d.reason for d in report.divergences} <= {"slippage_draw"}
    assert report.provenance["dataset_ids"] == {"EURUSD": "synthetic_eurusd_h1"}
    assert report.provenance["strategy_content_hash"] == baseline.document.bind_defaults().content_hash()


def test_the_report_is_deterministic(baseline: harness.Account, frame: pd.DataFrame) -> None:
    assert reconcile(_inputs(baseline, frame)).digest() == reconcile(_inputs(baseline, frame)).digest()


def test_trades_suppressed_by_a_pause_are_attributed_to_the_operator(
    tmp_path: Path, frame: pd.DataFrame, baseline: harness.Account
) -> None:
    acct = harness.Account(tmp_path, frame)
    acct.start()
    for _ in range(30):
        acct.next_bar()
    BotControlLedger(acct.settings.paths.bot_controls).request(
        SID, "pause", actor="joe", actor_role="admin", reason="test: pause over a signal"
    )
    for _ in range(25):
        acct.next_bar()
    BotControlLedger(acct.settings.paths.bot_controls).request(
        SID, "resume", actor="joe", actor_role="admin", reason="test: resume after window"
    )
    for _ in range(harness.N_POLLS - 57):
        acct.next_bar()
    acct.close()
    report = reconcile(_inputs(acct, frame))
    suppressed = [d for d in report.divergences if d.kind == "backtest_only" and d.reason == "operator_control"]
    assert suppressed, report.divergences
    assert report.unexplained == 0, [d for d in report.divergences if not d.explained]
    assert report.status == "diverged"


def test_the_job_is_queued_by_the_api_run_by_the_runner_and_read_back(
    tmp_path: Path, frame: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    acct = harness.run_uninterrupted(tmp_path, frame, polls=40)
    from tests.api.conftest import _sha

    monkeypatch.setenv("FIBOKI_OPERATORS", f"tom:operator:{_sha(OPERATOR_PW)}")
    env = {
        "FIBOKI_STATE_DIR": str(tmp_path / "var"),
        "FIBOKI_ALLOWED_ORIGINS": ORIGIN,
        "FIBOKI_COOKIE_SECURE": "false",
        "FIBOKI_SESSION_SECRET": "test-secret-not-for-production",
    }
    from fiboki.api.settings import load_settings

    settings = load_settings(env)
    with TestClient(create_app(settings, configure_logs=False), base_url=ORIGIN) as c:
        empty = c.get(f"/api/trading/sessions/{SID}/reconciliation").json()
        assert empty["data"]["report"] is None and empty["source"]["kind"] == "absent"
        assert login(c, "tom", OPERATOR_PW).status_code == 200
        r = c.post(f"/api/trading/sessions/{SID}/reconciliation",
                   json={"reason": "weekly reconciliation"}, headers=csrf_headers(c))
        assert r.status_code == 202, r.text
        assert r.json()["data"]["job"]["status"] == "queued"
        ran = run_jobs(
            settings, once=True, bars_loader=lambda s, tf, a, b: (frame, "synthetic_eurusd_h1"),
            wiring_dir=acct.tmp, strategies_dir=acct.strategies, calendar=None,
        )
        assert ran == 1
        body = c.get(f"/api/trading/sessions/{SID}/reconciliation").json()
        data = body["data"]
        assert data["job"]["status"] == "succeeded"
        report = data["report"]
        assert report["status"] in ("match", "diverged") and report["unexplained"]["value"] == 0
        assert report["matched"]["value"] == len(acct.trades())
        assert body["source"]["kind"] == "live"
        assert any(e.action == "ops.reconcile.request" for e in c.app.state.audit.entries())


def test_no_bars_or_a_changed_wiring_is_not_evaluated(tmp_path: Path, frame: pd.DataFrame) -> None:
    acct = harness.run_uninterrupted(tmp_path, frame, polls=20)
    late = frame.loc[frame.index > harness.FIRST_POLL + pd.Timedelta(hours=5)]
    report = reconcile_session(
        acct.settings, SID, bars_loader=lambda s, tf, a, b: (late, "late"),
        wiring_dir=acct.tmp, strategies_dir=acct.strategies, calendar=None,
    )
    assert report.status == "not_evaluated" and "overlap" in report.status_reason
    body = json.loads(acct.wiring.read_text())
    body["description"] = "edited after the session ran"
    acct.wiring.write_text(json.dumps(body, indent=2))
    changed = reconcile_session(
        acct.settings, SID, bars_loader=lambda s, tf, a, b: (frame, "x"),
        wiring_dir=acct.tmp, strategies_dir=acct.strategies, calendar=None,
    )
    assert changed.status == "not_evaluated" and "cannot be reconstructed" in changed.status_reason
