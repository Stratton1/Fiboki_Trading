"""Server-computed consequences and per-caveat acknowledgement for promotion.

The promote dialog used to hard-code its consequence list in page code and send
``acknowledge_caveats: true`` on the operator's behalf. These tests pin the
server half of the fix: the consequences and the caveats to acknowledge come
from ``GET .../promote/preflight``; the kill-switch disarm consequences come from
``GET /api/trading/preflight/kill-switch-disarm``; and a promotion that names
the caveats it acknowledged must name all of them, and is audited with them.
"""
from __future__ import annotations

import pytest

from fiboki.api.routers import trading
from tests.api.conftest import csrf_headers

STRATEGY = "ichimoku_kumo_trend"


def _preflight(client, strategy_id: str = STRATEGY):
    return client.get(f"/api/trading/candidates/{strategy_id}/promote/preflight")


def _promote(client, **body):
    payload = {
        "target_lifecycle": "paper",
        "reason": "out-of-sample evidence reviewed",
        "acknowledge_caveats": True,
        **body,
    }
    return client.post(
        f"/api/trading/candidates/{STRATEGY}/promote",
        json=payload,
        headers=csrf_headers(client),
    )


@pytest.fixture
def eligible(monkeypatch):
    """Lower the ranking minimum so the seeded candidate is promotable.

    The seed does not hold 80 out-of-sample trades for any strategy, so without
    this every promotion is refused as ``insufficient_evidence`` before the
    acknowledgement logic is reached.
    """
    monkeypatch.setattr(trading, "MIN_TRADES_FOR_RANKING", 1)


def _last_promote_entry(client):
    entries = client.app.state.audit.entries()
    return next(e for e in reversed(entries) if e.action == "candidate.promote")


# ----------------------------------------------------------- preflight


def test_preflight_returns_consequences_per_target_in_the_arm_shape(admin_client):
    response = _preflight(admin_client)
    assert response.status_code == 200, response.text
    view = response.json()["data"]
    assert set(view["consequences"]) == {"paper", "shadow"}
    for lines in view["consequences"].values():
        assert isinstance(lines, list) and lines
        assert all(isinstance(line, str) and line for line in lines)
    assert view["execution_mode"] == "paper"
    # Demo and live are never offered.
    assert "demo" not in view["consequences"]
    assert "live" not in view["consequences"]


def test_preflight_caveats_are_the_candidates_own_figure_caveats(admin_client):
    view = _preflight(admin_client).json()["data"]
    codes = [c["code"] for c in view["caveats"]]
    assert codes, "an out-of-sample candidate always carries simulation caveats"
    assert len(codes) == len(set(codes)), "caveats are de-duplicated by code"

    row = next(
        r
        for r in admin_client.get("/api/trading/candidates").json()["items"]
        if r["strategy_id"] == STRATEGY
    )
    on_figures = {
        c["code"]
        for key in ("trades", "win_rate", "expectancy_r", "net_pnl", "sharpe", "max_drawdown_pct")
        for c in row[key]["caveats"]
    }
    assert set(codes) == on_figures
    # Every acknowledged code is named in the consequences, verbatim.
    for code in codes:
        assert code in " ".join(view["consequences"]["paper"])


def test_preflight_consequences_do_not_claim_a_run_starts(admin_client):
    # The route only records a decision; the consequence text must say so.
    paper = " ".join(_preflight(admin_client).json()["data"]["consequences"]["paper"])
    assert "does not itself start a run" in paper
    assert "risk budget" not in paper


def test_preflight_for_unknown_strategy_is_404(admin_client):
    response = _preflight(admin_client, "no_such_strategy")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_strategy"


def test_preflight_reports_kill_switch_block(admin_client):
    admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "pause", "reason": "halting while we investigate"},
        headers=csrf_headers(admin_client),
    )
    view = _preflight(admin_client).json()["data"]
    assert view["eligible"] is False
    assert any("kill switch" in r.lower() for r in view["blocking_reasons"])


# ------------------------------------------------------- acknowledgement


def test_partial_acknowledgement_is_refused_and_audited(admin_client, eligible):
    codes = [c["code"] for c in _preflight(admin_client).json()["data"]["caveats"]]
    assert len(codes) >= 2
    response = _promote(admin_client, acknowledged_caveats=codes[:1])
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "caveats_not_acknowledged"
    assert set(body["context"]["missing_caveats"]) == set(codes[1:])

    entry = _last_promote_entry(admin_client)
    assert entry.outcome == "refused"
    assert entry.detail["acknowledged_caveats"] == sorted(codes[:1])


def test_full_acknowledgement_is_allowed_and_audited_with_the_codes(admin_client, eligible):
    codes = [c["code"] for c in _preflight(admin_client).json()["data"]["caveats"]]
    response = _promote(admin_client, acknowledged_caveats=codes)
    assert response.status_code == 200, response.text

    entry = _last_promote_entry(admin_client)
    assert entry.outcome == "allowed"
    assert entry.detail["acknowledged_caveats"] == sorted(codes)
    assert entry.detail["required_caveats"] == codes
    assert entry.detail["target_lifecycle"] == "paper"


def test_boolean_only_clients_are_recorded_as_such(admin_client, eligible):
    response = _promote(admin_client)
    assert response.status_code == 200, response.text
    entry = _last_promote_entry(admin_client)
    assert entry.detail["acknowledged_caveats"] is None


def test_codes_without_the_boolean_are_still_refused(admin_client, eligible):
    codes = [c["code"] for c in _preflight(admin_client).json()["data"]["caveats"]]
    response = _promote(admin_client, acknowledge_caveats=False, acknowledged_caveats=codes)
    assert response.status_code == 409
    assert response.json()["code"] == "caveats_not_acknowledged"


# ------------------------------------------------------------ disarm


def test_disarm_preflight_when_not_armed_says_it_would_be_refused(admin_client):
    view = admin_client.get("/api/trading/preflight/kill-switch-disarm").json()["data"]
    assert view["active"] is False
    assert set(view["consequences"]) == {"disarm"}
    assert "kill_switch_not_active" in " ".join(view["consequences"]["disarm"])


def test_disarm_preflight_names_the_halt_being_lifted(admin_client):
    admin_client.post(
        "/api/system/kill-switch/arm",
        json={"mode": "flatten", "reason": "data feed went stale mid-session"},
        headers=csrf_headers(admin_client),
    )
    view = admin_client.get("/api/trading/preflight/kill-switch-disarm").json()["data"]
    assert view["active"] is True and view["mode"] == "flatten"
    text = " ".join(view["consequences"]["disarm"])
    assert "FLATTEN" in text
    assert "data feed went stale mid-session" in text
    assert "joe" in text
    assert "PAPER mode" in text
    assert "stay closed" in text
