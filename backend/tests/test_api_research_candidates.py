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


def test_approve_is_gated_403(api_client, auth_headers):
    c = _mk(api_client, auth_headers)
    r = api_client.post(f"{API}/research/candidates/{c['event_id']}/approve",
                        headers=auth_headers)
    assert r.status_code == 403
    assert "not yet enabled" in r.json()["detail"].lower()


def test_candidates_requires_auth(api_client):
    r = api_client.get(f"{API}/research/candidates")
    assert r.status_code in (401, 403)
