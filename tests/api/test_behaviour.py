"""Behavioural tests for the failures the V1 audit found."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.core.enums import Provenance
from tests.api.conftest import ORIGIN, csrf_headers

# ------------------------------------------------- provenance as a column


def test_trades_carry_provenance_per_row_not_per_page(admin_client):
    body = admin_client.get("/api/trading/trades?limit=200").json()
    assert body["items"], "no trades returned"
    labels = {row["provenance"] for row in body["items"]}
    # A single mixed response is legitimate; the ROW is what disambiguates it.
    assert len(labels) > 1, "the fixture should exercise the mixed case"
    for row in body["items"]:
        assert row["provenance"] in {p.value for p in Provenance}
        assert row["net_pnl"]["provenance"] == row["provenance"]


def test_trades_view_actually_contains_paper_trades(tmp_path, monkeypatch):
    """V1's 'Paper / Backtest' page contained zero paper trades.

    PAPER rows come only from a real paper journal; the seed fixture is a
    demonstration and is never allowed to carry the label. So this test runs
    the API against the recorded journal fixture rather than the seed.
    """
    from tests.api.conftest import ADMIN_PW, _sha, login

    journal = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"
    monkeypatch.setenv("FIBOKI_OPERATORS", f"joe:admin:{_sha(ADMIN_PW)}")
    settings = load_settings(
        {
            "FIBOKI_STATE_DIR": str(tmp_path / "state"),
            "FIBOKI_ALLOWED_ORIGINS": ORIGIN,
            "FIBOKI_COOKIE_SECURE": "false",
            "FIBOKI_SESSION_SECRET": "test-secret-not-for-production",
            "FIBOKI_PAPER_ROOT": str(journal),
        }
    )
    with TestClient(create_app(settings, configure_logs=False), base_url=ORIGIN) as client:
        assert login(client, "joe", ADMIN_PW).status_code == 200
        body = client.get("/api/trading/trades?provenance=paper&limit=50").json()
        assert body["items"]
        assert {row["provenance"] for row in body["items"]} == {"paper"}
        assert body["source"]["kind"] == "live"


def test_seed_fixture_never_claims_paper_provenance(admin_client):
    """Demonstration rows must not wear the label of executed trades."""
    body = admin_client.get("/api/trading/trades?limit=500").json()
    assert body["source"]["kind"] == "seed"
    assert "paper" not in {row["provenance"] for row in body["items"]}


def test_mixed_provenance_result_is_flagged(admin_client):
    body = admin_client.get("/api/trading/trades?limit=200").json()
    codes = {c["code"] for c in body["caveats"]}
    assert "mixed_provenance_result" in codes


def test_portfolio_balance_counts_only_the_executing_mode(admin_client):
    """V1's 'Fleet PnL (live)' tile was a backtest number."""
    body = admin_client.get("/api/trading/portfolio").json()
    data = body["data"]
    assert data["balance"]["provenance"] == "paper"
    assert data["equity_curve"]["provenance"] == "paper"
    assert "backtest" in data["provenance_mix"]
    assert "excluded from the account figures" in body["source"]["detail"]


def test_candidates_rank_on_out_of_sample_evidence_only(admin_client):
    body = admin_client.get("/api/trading/candidates").json()
    for item in body["items"]:
        for key in ("expectancy_r", "net_pnl", "sharpe", "win_rate"):
            assert item[key]["provenance"] == "out_of_sample", key


# ----------------------------------------------------- computed caveats


def test_realism_caveats_are_computed_from_configuration(api_env):
    """The prose must change when the assumption changes."""
    strict = dict(api_env)
    strict["FIBOKI_SLIPPAGE_MODEL"] = "measured"
    strict["FIBOKI_FINANCING_MODEL"] = "accrued"
    app = create_app(load_settings(strict), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as c:
        codes = _pnl_caveat_codes(c)
    assert "slippage_not_modelled" not in codes
    assert "financing_not_modelled" not in codes

    app2 = create_app(load_settings(api_env), configure_logs=False)
    with TestClient(app2, base_url=ORIGIN) as c2:
        default_codes = _pnl_caveat_codes(c2)
    assert "slippage_not_modelled" in default_codes
    assert "financing_not_modelled" in default_codes


def _pnl_caveat_codes(client) -> set[str]:
    body = client.get("/api/trading/trades?limit=5").json()
    return {c["code"] for row in body["items"] for c in row["net_pnl"]["caveats"]}


def test_small_sample_figures_are_flagged(admin_client):
    body = admin_client.get("/api/trading/candidates").json()
    for item in body["items"]:
        n = item["trades"]["value"]
        codes = {c["code"] for c in item["expectancy_r"]["caveats"]}
        if n is not None and n < 80:
            assert "sample_below_ranking_minimum" in codes
            assert item["eligible_for_ranking"] is False


# -------------------------------------------------- absence is not zero


def test_unknown_margin_is_null_not_zero(admin_client):
    data = admin_client.get("/api/trading/risk").json()["data"]
    assert data["margin_utilisation_pct"]["value"] is None
    assert data["margin_utilisation_pct"]["caveats"][0]["code"] == "value_unavailable"


def test_absent_bars_return_503_not_an_empty_chart(admin_client):
    response = admin_client.get("/api/markets/bars/EURUSD")
    assert response.status_code == 503
    assert response.json()["code"] == "market_data_not_mounted"


def test_absent_experiment_ledger_is_labelled_absent_not_empty(admin_client):
    body = admin_client.get("/api/research/experiments").json()
    assert body["items"] == []
    assert body["source"]["kind"] == "absent"
    assert "EMPTY SOURCE" in body["source"]["detail"]


def test_queue_depth_is_null_when_unknown(admin_client):
    body = admin_client.get("/api/system/queues").json()
    assert all(row["depth"] is None for row in body["items"])
    assert all(row["available"] is False for row in body["items"])


def test_unvalidated_strategy_is_not_reported_as_passing(admin_client):
    body = admin_client.get("/api/research/validation").json()
    for item in body["items"]:
        if not item["available"]:
            assert item["verdict"] in {"not_validated", "unreadable"}
            assert "this is not a pass" in item["detail"].lower()


# ------------------------------------------------------ execution mode


def test_execution_mode_is_read_from_the_api_not_assumed(admin_client):
    data = admin_client.get("/api/system/execution-mode").json()["data"]
    assert data["mode"] == "paper"
    assert data["severity"] == "info"
    assert data["touches_real_money"] is False
    assert "no venue contacted" in data["headline"].lower()


@pytest.mark.parametrize(
    ("mode", "severity", "real_money"),
    [("demo", "caution", False), ("live", "danger", True), ("backtest", "info", False)],
)
def test_banner_severity_follows_the_mode(api_env, mode, severity, real_money):
    env = dict(api_env)
    env["FIBOKI_EXECUTION_MODE"] = mode
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as c:
        data = c.get("/api/system/execution-mode").json()["data"]
    assert data["mode"] == mode
    assert data["severity"] == severity
    assert data["touches_real_money"] is real_money


def test_no_endpoint_can_enable_live_execution(admin_client):
    response = admin_client.post(
        "/api/system/execution-mode",
        json={"mode": "live"},
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "execution_mode_is_not_an_api_surface"
    controls = body["context"]["controls"]
    assert len(controls) == 5
    assert all(c["satisfied"] is False for c in controls)


def test_refusing_live_is_audited(admin_client):
    admin_client.post(
        "/api/system/execution-mode",
        json={"mode": "live"},
        headers=csrf_headers(admin_client),
    )
    entries = admin_client.get("/api/intelligence/audit").json()["items"]
    assert any(e["action"] == "execution_mode.change_refused" for e in entries)


def test_invalid_execution_mode_refuses_to_start():
    with pytest.raises(RuntimeError, match="not a valid execution mode"):
        load_settings({"FIBOKI_EXECUTION_MODE": "liv"})


# -------------------------------------------------------- kill switch


def test_kill_switch_is_armable_in_paper_mode(admin_client):
    view = admin_client.get("/api/system/kill-switch").json()["data"]
    assert view["armable"] is True
    assert view["active"] is False
    assert set(view["consequences"]) == {"pause", "flatten"}
    assert len(view["consequences"]["pause"]) >= 3
    assert len(view["consequences"]["flatten"]) >= 3


def test_pause_and_flatten_are_distinct_choices(admin_client):
    view = admin_client.get("/api/system/kill-switch").json()["data"]
    pause = " ".join(view["consequences"]["pause"]).lower()
    flatten = " ".join(view["consequences"]["flatten"]).lower()
    assert "left open" in pause
    assert "closed" in flatten
    assert pause != flatten


def test_arming_requires_a_reason(admin_client):
    response = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "short"},
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 422


def test_arming_has_no_default_mode(admin_client):
    response = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"reason": "a mode must be chosen explicitly"},
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 422


def test_arm_pause_then_escalate_then_disarm(admin_client):
    headers = csrf_headers(admin_client)
    armed = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "spread blowout on the open"},
        headers=headers,
    ).json()["data"]
    assert armed["active"] is True and armed["mode"] == "pause"
    assert armed["blocks_new_risk"] is True

    escalated = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "flatten", "reason": "escalating, get the book flat"},
        headers=headers,
    ).json()["data"]
    assert escalated["mode"] == "flatten" and escalated["requires_flatten"] is True

    # Downgrading without an explicit disarm is refused by the domain object.
    downgrade = admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "trying to soften the decision"},
        headers=headers,
    )
    assert downgrade.status_code == 409

    disarmed = admin_client.post(
        "/api/system/kill-switch/disarm",
        json={"reason": "spreads normalised, re-arming trading"},
        headers=headers,
    ).json()["data"]
    assert disarmed["active"] is False


def test_kill_switch_blocks_promotion(admin_client):
    headers = csrf_headers(admin_client)
    admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "halting while we investigate"},
        headers=headers,
    )
    body = admin_client.get("/api/trading/candidates").json()
    assert all(
        any("kill switch" in r.lower() for r in item["blocking_reasons"])
        for item in body["items"]
    )


def test_kill_switch_actions_are_audited_with_the_reason(admin_client):
    headers = csrf_headers(admin_client)
    admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "flatten", "reason": "data feed went stale mid-session"},
        headers=headers,
    )
    entries = admin_client.get("/api/intelligence/audit").json()["items"]
    match = next(e for e in entries if e["action"] == "killswitch.arm")
    assert match["reason"] == "data feed went stale mid-session"
    assert match["actor"] == "joe"
    assert match["execution_mode"] == "paper"
    integrity = admin_client.get("/api/intelligence/audit/integrity").json()["data"]
    assert integrity["intact"] is True


# ----------------------------------------------------------- promotion


def test_promotion_requires_acknowledging_caveats(admin_client):
    response = admin_client.post(
        "/api/trading/candidates/ichimoku_kumo_trend/promote",
        json={
            "target_lifecycle": "paper",
            "reason": "looks good on the out-of-sample",
            "acknowledge_caveats": False,
        },
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "caveats_not_acknowledged"


def test_promotion_cannot_target_live_or_demo(admin_client):
    for target in ("live", "demo"):
        response = admin_client.post(
            "/api/trading/candidates/ichimoku_kumo_trend/promote",
            json={
                "target_lifecycle": target,
                "reason": "attempting to skip the ladder",
                "acknowledge_caveats": True,
            },
            headers=csrf_headers(admin_client),
        )
        assert response.status_code == 422, target


def test_promotion_is_refused_below_the_trade_minimum(admin_client):
    response = admin_client.post(
        "/api/trading/candidates/ichimoku_kumo_trend/promote",
        json={
            "target_lifecycle": "paper",
            "reason": "pushing it through anyway",
            "acknowledge_caveats": True,
        },
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_evidence"


# ------------------------------------------------------------- agents


def test_no_agent_role_holds_an_execution_capability(admin_client):
    body = admin_client.get("/api/intelligence/agents").json()
    assert body["items"]
    for role in body["items"]:
        assert role["can_execute"] is False
        for capability in role["capabilities"]:
            assert "order" not in capability.lower()
            assert "execute" not in capability.lower()
