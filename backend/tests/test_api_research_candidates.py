"""Tests for the research candidate review surface (read-only over the ledger)."""

API = "/api/v1"


def _mk(api_client, auth_headers, **kw):
    body = {"event_type": "validated", "actor": "agent",
            "strategy_id": "factory_trad_macd_cross_v1",
            "instrument": "EURUSD", "timeframe": "H4",
            "approval_status": "pending", "risk_decision": "paper_candidate",
            "reason": "passed all robustness rungs",
            "stats_json": {"sharpe": 1.4, "composite": 0.55, "oos_score": 0.4,
                           "mc_profit_prob": 0.82}}
    body.update(kw)
    r = api_client.post(f"{API}/bot-lifecycle", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_candidates_listed_and_ranked(api_client, auth_headers):
    _mk(api_client, auth_headers, strategy_id="factory_hyb_macd_ema_trend_v1",
        instrument="USDJPY", stats_json={"sharpe": 3.0, "composite": 0.66,
                                         "oos_score": 0.5, "mc_profit_prob": 0.9})
    _mk(api_client, auth_headers, risk_decision="research_watchlist",
        instrument="GBPUSD", stats_json={"sharpe": 0.9, "composite": 0.32,
                                         "oos_score": 0.2, "mc_profit_prob": 0.71})

    r = api_client.get(f"{API}/research/candidates", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 2
    # Ranked by Sharpe desc.
    sharpes = [c["sharpe"] for c in data if c["sharpe"] is not None]
    assert sharpes == sorted(sharpes, reverse=True)
    top = data[0]
    assert top["tier"] in ("hybrid_gen1", "traditional_gen1")
    assert "recommended_state" in top and "oos_score" in top


def test_candidates_filter_by_state(api_client, auth_headers):
    r = api_client.get(f"{API}/research/candidates?state=paper_candidate",
                       headers=auth_headers)
    assert r.status_code == 200
    assert all(c["recommended_state"] == "paper_candidate" for c in r.json())


def test_funnel_counts(api_client, auth_headers):
    _mk(api_client, auth_headers)  # one validated paper_candidate
    _mk(api_client, auth_headers, event_type="rejected", risk_decision=None,
        reason="oos", stats_json={"composite": 0.41})
    _mk(api_client, auth_headers, event_type="rejected", risk_decision=None,
        reason="monte_carlo", stats_json={"composite": 0.5})
    r = api_client.get(f"{API}/research/funnel", headers=auth_headers)
    assert r.status_code == 200
    f = r.json()
    assert f["validated"] >= 1
    assert f["rejections"].get("oos", 0) >= 1
    assert f["rejections"].get("monte_carlo", 0) >= 1
    assert f["total_ledgered"] >= f["validated"]


def test_demo_ready_flag(api_client, auth_headers):
    # A strong paper_candidate that clears the demo bar.
    _mk(api_client, auth_headers, instrument="USDCHF",
        stats_json={"sharpe": 1.6, "composite": 0.6, "oos_score": 0.45,
                    "oos_robust": True, "profit_factor": 1.4, "max_dd": 8.0,
                    "mc_profit_prob": 0.85, "mc_ruin_prob": 0.0, "trades": 120})
    # A weak one (low PF, high DD) — passed ladder but not demo-ready.
    _mk(api_client, auth_headers, instrument="GBPUSD",
        stats_json={"sharpe": 1.1, "composite": 0.4, "oos_score": 0.3,
                    "oos_robust": True, "profit_factor": 1.05, "max_dd": 28.0,
                    "mc_profit_prob": 0.72, "mc_ruin_prob": 0.0, "trades": 90})
    data = api_client.get(f"{API}/research/candidates", headers=auth_headers).json()
    by_inst = {c["instrument"]: c for c in data}
    assert by_inst["USDCHF"]["demo_ready"] is True
    assert by_inst["GBPUSD"]["demo_ready"] is False
    assert by_inst["USDCHF"]["profit_factor"] == 1.4


def test_by_strategy_rollup(api_client, auth_headers):
    _mk(api_client, auth_headers, strategy_id="factory_trad_macd_cross_v1",
        instrument="EURUSD", stats_json={"sharpe": 1.2, "composite": 0.5})
    _mk(api_client, auth_headers, strategy_id="factory_trad_macd_cross_v1",
        instrument="USDJPY", stats_json={"sharpe": 2.1, "composite": 0.6})
    r = api_client.get(f"{API}/research/candidates/by-strategy", headers=auth_headers)
    assert r.status_code == 200
    roll = {s["strategy_id"]: s for s in r.json()}
    macd = roll["factory_trad_macd_cross_v1"]
    assert macd["combos"] >= 2
    assert macd["best_combo"]["instrument"] == "USDJPY"  # higher Sharpe wins
    assert macd["best_sharpe"] == 2.1


def test_approve_creates_paper_bot(api_client, auth_headers):
    c = _mk(api_client, auth_headers, instrument="EURUSD",
            stats_json={"sharpe": 1.5, "profit_factor": 1.4, "max_dd": 8.0,
                        "oos_robust": True, "trades": 120})
    r = api_client.post(f"{API}/research/candidates/{c['event_id']}/approve",
                        headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "monitoring"
    assert body["strategy_id"] == "factory_trad_macd_cross_v1"
    assert "no live" in body["message"].lower()
    # It shows up as a candidate-sourced paper bot.
    bots = api_client.get(f"{API}/paper/bots", headers=auth_headers).json()
    assert any(b["bot_id"] == body["bot_id"] for b in bots)
    # A promoted_to_paper event was ledgered.
    ev = api_client.get(f"{API}/bot-lifecycle?event_type=promoted_to_paper",
                        headers=auth_headers).json()
    assert any(e["bot_id"] == body["bot_id"] for e in ev)


def test_approve_rejects_watchlist(api_client, auth_headers):
    c = _mk(api_client, auth_headers, risk_decision="research_watchlist")
    r = api_client.post(f"{API}/research/candidates/{c['event_id']}/approve",
                        headers=auth_headers)
    assert r.status_code == 409


def test_approve_unknown_404(api_client, auth_headers):
    r = api_client.post(f"{API}/research/candidates/nope-xyz/approve",
                        headers=auth_headers)
    assert r.status_code == 404


def test_paper_monitor_lists_promoted_bot(api_client, auth_headers):
    c = _mk(api_client, auth_headers, instrument="USDCHF",
            stats_json={"sharpe": 1.6, "profit_factor": 1.4, "max_dd": 8.0,
                        "oos_robust": True, "trades": 120})
    api_client.post(f"{API}/research/candidates/{c['event_id']}/approve",
                    headers=auth_headers)
    r = api_client.get(f"{API}/research/paper-monitor", headers=auth_headers)
    assert r.status_code == 200
    mon = r.json()
    assert len(mon) >= 1
    m = mon[0]
    # No live trades yet → still in monitoring verdict, expected metrics carried.
    assert m["verdict"] == "monitoring"
    assert m["expected_profit_factor"] == 1.4


def test_candidates_requires_auth(api_client):
    r = api_client.get(f"{API}/research/candidates")
    assert r.status_code in (401, 403)
