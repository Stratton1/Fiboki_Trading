"""The chart workstation's backtest-trade overlay and operator drawings.

``GET /api/markets/backtests/{symbol}`` and
``GET /api/markets/backtests/{symbol}/{run_id}/trades`` read the research
ledger read-only; every figure they serve is a stored one. The drawing routes
(``GET/PUT/DELETE /api/markets/drawings/{symbol}``) persist operator
annotations per operator and symbol, with optimistic versioning, audited
writes and the operator role required to write.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.backtest.version import ENGINE_VERSION
from fiboki.research.artefacts import (
    BacktestRecord,
    ResearchNote,
    ResearchStore,
    ValidationReportRecord,
)
from fiboki.research.experiment import ExperimentLedger
from tests.api.conftest import (
    ADMIN_PW,
    OPERATOR_PW,
    ORIGIN,
    VIEWER_PW,
    csrf_headers,
    login,
)

T0 = datetime(2026, 9, 21, tzinfo=UTC)


def _t(hours: int) -> str:
    return str(T0 + timedelta(hours=hours)).replace(" ", "T")


def _trade(i: int, entry_h: int, exit_h: int, *, stamped: bool, instrument="EURUSD", pnl=10.0):
    row = {
        "trade_id": f"trd_{i}",
        "instrument": instrument,
        "direction": "long" if i % 2 == 0 else "short",
        "size": 0.5,
        "entry_price": 1.1000 + i * 0.001,
        "exit_price": 1.1010 + i * 0.001,
        "entry_time": str(T0 + timedelta(hours=entry_h)),
        "exit_time": str(T0 + timedelta(hours=exit_h)),
        "exit_reason": "take_profit" if pnl > 0 else "stop_loss",
        "gross_pnl": pnl + 1.5,
        "spread_cost": 1.0,
        "commission": 0.0,
        "slippage_cost": 0.5,
        "financing_cost": 0.0,
        "net_pnl": pnl,
        "account_ccy": "GBP",
        "strategy_id": "donchian_breakout_atr",
        "bars_held": exit_h - entry_h,
        "max_adverse_excursion": -2.0,
        "max_favourable_excursion": 4.0,
        "provenance": "backtest",
        "fx_rate_used": 0.8,
    }
    if stamped:
        row.update(
            initial_stop=1.0950 + i * 0.001,
            final_stop=1.0990 + i * 0.001,
            take_profit_targets=[1.1100 + i * 0.001, 1.1150 + i * 0.001],
            stop_moved_by="breakeven" if i == 0 else "",
        )
    return row


@pytest.fixture
def ledger_env(api_env, tmp_path):
    db = tmp_path / "experiments.sqlite"
    with ResearchStore(ledger=ExperimentLedger(db)) as store:
        new = store.add_backtest(
            BacktestRecord(
                strategy_id="donchian_breakout_atr",
                instruments=("EURUSD",),
                timeframe="H1",
                dataset_version_ids=("dsv_eurusd_h1_0007abcdef",),
                trades=(
                    _trade(0, 2, 10, stamped=True),
                    _trade(1, 20, 30, stamped=True, pnl=-7.25),
                    _trade(2, 200, 210, stamped=True),
                ),
                label="",
                created_by="worker",
            )
        )
        old = store.add_backtest(
            BacktestRecord(
                strategy_id="ichimoku_kumo_trend",
                instruments=("EURUSD",),
                timeframe="H1",
                dataset_version_ids=("dsv_eurusd_h1_0006",),
                engine_version="engine_v1",
                trades=(_trade(5, 4, 8, stamped=False),),
                label="walkforward_fold_0",
            )
        )
        other = store.add_backtest(
            BacktestRecord(
                strategy_id="donchian_breakout_atr",
                instruments=("GBPUSD",),
                timeframe="H1",
                trades=(_trade(7, 1, 2, stamped=True, instrument="GBPUSD"),),
            )
        )
        h4 = store.add_backtest(
            BacktestRecord(
                strategy_id="donchian_breakout_atr", instruments=("EURUSD",), timeframe="H4"
            )
        )
        store.add_validation_report(
            ValidationReportRecord(
                strategy_id="donchian_breakout_atr", backtest_id=new.backtest_id, verdict="fail"
            )
        )
        store.add_note(
            ResearchNote(
                title=f"Superseded backtest {old.backtest_id}",
                body="engine_v1 predates the exit vocabulary change.",
                tags=("superseded_by_engine_version",),
                supersedes=old.backtest_id,
            )
        )
    env = {**api_env, "FIBOKI_EXPERIMENT_DB": str(db)}
    return env, {"new": new, "old": old, "other": other, "h4": h4}


@pytest.fixture
def ledger_client(ledger_env):
    env, runs = ledger_env
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, runs


# ------------------------------------------------------------- backtest runs


def test_runs_list_the_symbols_backtests_newest_first(ledger_client):
    client, runs = ledger_client
    body = client.get("/api/markets/backtests/EURUSD", params={"timeframe": "H1"}).json()
    ids = [r["run_id"] for r in body["items"]]
    assert ids == [runs["old"].backtest_id, runs["new"].backtest_id]
    assert body["total"] == 2
    new = next(r for r in body["items"] if r["run_id"] == runs["new"].backtest_id)
    assert new["strategy_id"] == "donchian_breakout_atr"
    assert new["gate_verdict"] == "fail"
    assert new["kind"] == "backtest"
    assert new["engine_current"] is True and new["engine_version"] == ENGINE_VERSION
    assert new["superseded"] is False
    assert new["trade_count"]["value"] == 3
    assert new["trades_on_symbol"]["value"] == 3
    assert new["trades_with_stops"]["value"] == 3
    assert new["provenance"] == "backtest"
    assert new["dataset_version_ids"] == ["dsv_eurusd_h1_0007abcdef"]
    old = next(r for r in body["items"] if r["run_id"] == runs["old"].backtest_id)
    assert old["kind"] == "walkforward_fold"
    assert old["gate_verdict"] == "not_evaluated"
    assert old["engine_current"] is False and old["superseded"] is True
    assert old["trades_with_stops"]["value"] == 0
    codes = {c["code"] for c in body["caveats"]}
    assert {"backtest_engine_stale", "backtest_stops_not_recorded"} <= codes
    assert body["source"]["kind"] == "live"


def test_runs_filter_by_timeframe_and_strategy(ledger_client):
    client, runs = ledger_client
    every = client.get("/api/markets/backtests/EURUSD").json()
    assert {r["run_id"] for r in every["items"]} == {
        runs["new"].backtest_id, runs["old"].backtest_id, runs["h4"].backtest_id
    }
    only = client.get(
        "/api/markets/backtests/EURUSD", params={"strategy_id": "ichimoku_kumo_trend"}
    ).json()
    assert [r["run_id"] for r in only["items"]] == [runs["old"].backtest_id]
    assert client.get("/api/markets/backtests/GBPUSD").json()["total"] == 1


def test_runs_refuse_unknown_symbols_and_timeframes(ledger_client):
    client, _ = ledger_client
    assert client.get("/api/markets/backtests/NOPE").status_code == 404
    bad = client.get("/api/markets/backtests/EURUSD", params={"timeframe": "H2"})
    assert bad.status_code == 400 and bad.json()["code"] == "unknown_timeframe"


def test_no_ledger_is_an_absent_source_not_an_empty_one(client):
    body = client.get("/api/markets/backtests/EURUSD").json()
    assert body["items"] == [] and body["source"]["kind"] == "absent"
    assert body["caveats"][0]["code"] == "source_not_configured"
    trades = client.get("/api/markets/backtests/EURUSD/bt_x/trades")
    assert trades.status_code == 503
    assert trades.json()["code"] == "experiment_ledger_not_configured"


# ----------------------------------------------------------- backtest trades


def test_trades_in_the_window_carry_the_engines_stops_and_targets(ledger_client):
    client, runs = ledger_client
    run_id = runs["new"].backtest_id
    body = client.get(
        f"/api/markets/backtests/EURUSD/{run_id}/trades",
        params={"from": _t(0), "to": _t(100), "timeframe": "H1"},
    ).json()
    data = body["data"]
    assert [t["trade_id"] for t in data["trades"]] == ["trd_0", "trd_1"]
    assert data["total_on_symbol"]["value"] == 3
    assert data["outside_window"]["value"] == 1
    first = data["trades"][0]
    assert first["stop_loss"]["value"] == pytest.approx(1.0950)
    assert first["final_stop"]["value"] == pytest.approx(1.0990)
    assert first["take_profit"]["value"] == pytest.approx(1.1100)
    assert [f["value"] for f in first["take_profit_targets"]] == [
        pytest.approx(1.11), pytest.approx(1.115)
    ]
    assert first["stop_moved_by"] == "breakeven"
    assert first["direction"] == "long"
    assert first["size"] == {**first["size"], "value": 0.5, "unit": "lots"}
    assert first["net_pnl"]["value"] == 10.0 and first["net_pnl"]["unit"] == "GBP"
    assert first["net_pnl"]["provenance"] == "backtest"
    assert first["costs"]["value"] == pytest.approx(1.5)
    assert first["bars_held"] == {**first["bars_held"], "value": 8, "unit": "bars"}
    assert first["entry_time"].startswith("2026-09-21T02:00:00")
    second = data["trades"][1]
    assert second["direction"] == "short" and second["net_pnl"]["value"] == -7.25
    assert second["stop_moved_by"] is None
    assert data["run"]["run_id"] == run_id and data["run"]["gate_verdict"] == "fail"
    assert data["account_ccy"] == "GBP"
    assert run_id in body["source"]["detail"]
    assert "dsv_eurusd_h1_0007abcdef" in body["source"]["detail"]


def test_a_trade_overlapping_the_window_edge_is_served(ledger_client):
    client, runs = ledger_client
    body = client.get(
        f"/api/markets/backtests/EURUSD/{runs['new'].backtest_id}/trades",
        params={"from": _t(25), "to": _t(205)},
    ).json()
    assert [t["trade_id"] for t in body["data"]["trades"]] == ["trd_1", "trd_2"]


def test_a_run_filed_before_stops_were_recorded_says_so_per_trade(ledger_client):
    client, runs = ledger_client
    body = client.get(f"/api/markets/backtests/EURUSD/{runs['old'].backtest_id}/trades").json()
    (trade,) = body["data"]["trades"]
    for key in ("stop_loss", "final_stop", "take_profit"):
        assert trade[key]["value"] is None
        assert "before the engine recorded" in trade[key]["caveats"][0]["message"]
    assert trade["take_profit_targets"] == []
    assert "re-run" in body["data"]["stops_detail"]
    codes = {c["code"] for c in body["caveats"]}
    assert "backtest_engine_stale" in codes


def test_trade_route_refusals(ledger_client):
    client, runs = ledger_client
    missing = client.get("/api/markets/backtests/EURUSD/bt_nope/trades")
    assert missing.status_code == 404 and missing.json()["code"] == "unknown_backtest"
    elsewhere = client.get(f"/api/markets/backtests/EURUSD/{runs['other'].backtest_id}/trades")
    assert elsewhere.status_code == 404 and elsewhere.json()["code"] == "backtest_not_on_symbol"
    inverted = client.get(
        f"/api/markets/backtests/EURUSD/{runs['new'].backtest_id}/trades",
        params={"from": _t(10), "to": _t(0)},
    )
    assert inverted.status_code == 400
    mismatch = client.get(
        f"/api/markets/backtests/EURUSD/{runs['new'].backtest_id}/trades",
        params={"timeframe": "D1"},
    ).json()
    assert "backtest_timeframe_mismatch" in {c["code"] for c in mismatch["caveats"]}


def test_the_ledger_is_opened_read_only(ledger_env, tmp_path):
    env, runs = ledger_env
    db = tmp_path / "experiments.sqlite"
    before = db.stat().st_mtime_ns
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        client.get("/api/markets/backtests/EURUSD").raise_for_status()
        client.get(f"/api/markets/backtests/EURUSD/{runs['new'].backtest_id}/trades")
    assert db.stat().st_mtime_ns == before


# ------------------------------------------------------------------ drawings


def _drawing(i: int, kind="trend", **extra):
    points = {
        "trend": 2, "ray": 2, "rect": 2, "fib": 2, "hline": 1, "vline": 1, "text": 1
    }[kind]
    body = {
        "id": f"d{i}",
        "kind": kind,
        "points": [
            {"t": _t(i + k * 5), "price": 1.1 + 0.001 * (i + k)} for k in range(points)
        ],
    }
    if kind == "text":
        body["text"] = "Watch the 1.1050 shelf"
    body.update(extra)
    return body


def test_a_viewer_reads_an_empty_set_and_cannot_write(client):
    assert login(client, "guest", VIEWER_PW).status_code == 200
    got = client.get("/api/markets/drawings/EURUSD").json()["data"]
    assert got["version"] == 0 and got["drawings"] == [] and got["can_edit"] is False
    refused = client.put(
        "/api/markets/drawings/EURUSD",
        json={"base_version": 0, "drawings": [_drawing(1)]},
        headers=csrf_headers(client),
    )
    assert refused.status_code == 403 and refused.json()["code"] == "insufficient_role"


def test_drawings_need_a_session(client):
    assert client.get("/api/markets/drawings/EURUSD").status_code == 401


def test_an_operator_writes_reads_and_conflicts(client):
    assert login(client, "tom", OPERATOR_PW).status_code == 200
    every = [
        _drawing(1, "trend"), _drawing(2, "ray"), _drawing(3, "hline"), _drawing(4, "vline"),
        _drawing(5, "rect"), _drawing(6, "fib", fib_extensions=True), _drawing(7, "text"),
        _drawing(8, "trend", timeframe="H4", locked=True, hidden=True),
    ]
    put = client.put(
        "/api/markets/drawings/EURUSD",
        json={"base_version": 0, "drawings": every},
        headers=csrf_headers(client),
    )
    assert put.status_code == 200, put.text
    data = put.json()["data"]
    assert data["version"] == 1 and data["owner"] == "tom" and data["can_edit"] is True
    assert [d["kind"] for d in data["drawings"]] == [d["kind"] for d in every]
    got = client.get("/api/markets/drawings/EURUSD").json()["data"]
    assert got["version"] == 1 and len(got["drawings"]) == 8
    assert got["drawings"][7]["timeframe"] == "H4" and got["drawings"][7]["locked"] is True
    assert got["drawings"][0]["points"][0]["t"].startswith("2026-09-21T01:00:00")

    stale = client.put(
        "/api/markets/drawings/EURUSD",
        json={"base_version": 0, "drawings": []},
        headers=csrf_headers(client),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "drawings_conflict"
    assert stale.json()["context"]["current_version"] == 1
    # Nothing was overwritten.
    assert len(client.get("/api/markets/drawings/EURUSD").json()["data"]["drawings"]) == 8
    # Another symbol is its own set.
    assert client.get("/api/markets/drawings/GBPUSD").json()["data"]["version"] == 0

    cleared = client.delete(
        "/api/markets/drawings/EURUSD", params={"base_version": 1}, headers=csrf_headers(client)
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["version"] == 2 and cleared.json()["data"]["drawings"] == []


def test_other_operators_sets_are_shown_read_only(client):
    assert login(client, "tom", OPERATOR_PW).status_code == 200
    client.put(
        "/api/markets/drawings/EURUSD",
        json={"base_version": 0, "drawings": [_drawing(1)]},
        headers=csrf_headers(client),
    ).raise_for_status()
    client.post("/api/auth/logout", headers=csrf_headers(client))
    client.cookies.clear()
    assert login(client, "joe", ADMIN_PW).status_code == 200
    data = client.get("/api/markets/drawings/EURUSD").json()["data"]
    assert data["owner"] == "joe" and data["drawings"] == []
    assert [o["owner"] for o in data["others"]] == ["tom"]
    assert data["others"][0]["drawings"][0]["id"] == "d1"


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "x", "kind": "circle", "points": [{"t": "2026-09-21T00:00:00Z", "price": 1}]},
        {"id": "x", "kind": "trend", "points": [{"t": "2026-09-21T00:00:00Z", "price": 1}]},
        {"id": "x", "kind": "hline", "points": [{"t": "2026-09-21T00:00:00", "price": 1}]},
        {"id": "x", "kind": "text", "points": [{"t": "2026-09-21T00:00:00Z", "price": 1}]},
        {"id": "bad id!", "kind": "hline", "points": [{"t": "2026-09-21T00:00:00Z", "price": 1}]},
        {"id": "x", "kind": "hline", "points": [{"t": "2026-09-21T00:00:00Z", "price": 1}],
         "timeframe": "H2"},
        {"id": "x", "kind": "hline", "points": [{"t": "2026-09-21T00:00:00Z", "price": 1}],
         "fib_extensions": True},
        {"id": "x", "kind": "hline", "points": [{"t": "2026-09-21T00:00:00Z", "price": 1}],
         "order": "buy"},
    ],
)
def test_invalid_drawings_are_refused(operator_client, bad):
    response = operator_client.put(
        "/api/markets/drawings/EURUSD",
        json={"base_version": 0, "drawings": [bad]},
        headers=csrf_headers(operator_client),
    )
    assert response.status_code == 422, response.text


def test_non_finite_prices_and_duplicate_ids_are_refused(operator_client):
    response = operator_client.put(
        "/api/markets/drawings/EURUSD",
        content='{"base_version":0,"drawings":[{"id":"x","kind":"hline","points":'
        '[{"t":"2026-09-21T00:00:00Z","price":Infinity}]}]}',
        headers={**csrf_headers(operator_client), "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    dup = operator_client.put(
        "/api/markets/drawings/EURUSD",
        json={"base_version": 0, "drawings": [_drawing(1, "hline"), _drawing(1, "hline")]},
        headers=csrf_headers(operator_client),
    )
    assert dup.status_code == 422 and dup.json()["code"] == "invalid_drawings"


def test_writes_need_csrf_and_an_unknown_symbol_is_refused(operator_client):
    no_csrf = operator_client.put(
        "/api/markets/drawings/EURUSD",
        json={"base_version": 0, "drawings": []},
        headers={"Origin": ORIGIN},
    )
    assert no_csrf.status_code == 403 and no_csrf.json()["code"] == "csrf_failed"
    unknown = operator_client.put(
        "/api/markets/drawings/NOPE",
        json={"base_version": 0, "drawings": []},
        headers=csrf_headers(operator_client),
    )
    assert unknown.status_code == 404


def test_every_write_is_audited_and_kept_in_the_revision_history(operator_client, api_env):
    from pathlib import Path

    from fiboki.api.drawings import DrawingStore

    h = csrf_headers(operator_client)
    operator_client.put(
        "/api/markets/drawings/EURUSD", json={"base_version": 0, "drawings": [_drawing(1)]},
        headers=h,
    ).raise_for_status()
    operator_client.put(
        "/api/markets/drawings/EURUSD", json={"base_version": 0, "drawings": []}, headers=h
    )
    app = operator_client.app
    actions = [(e.action, e.outcome, e.actor) for e in app.state.audit.entries()]
    assert ("chart.drawings.put", "allowed", "tom") in actions
    assert ("chart.drawings.put", "refused", "tom") in actions
    store = DrawingStore(Path(api_env["FIBOKI_STATE_DIR"]) / "chart" / "drawings.sqlite")
    assert store.revisions("tom", "EURUSD") == [(1, "put", "tom")]


def test_the_principal_says_who_may_draw(client):
    assert login(client, "guest", VIEWER_PW).json()["can_draw"] is False
    client.cookies.clear()
    assert login(client, "tom", OPERATOR_PW).json()["can_draw"] is True


def test_crypto_symbols_have_drawings_and_runs(operator_client):
    assert operator_client.get("/api/markets/drawings/BTCGBP").status_code == 200
    assert operator_client.get("/api/markets/backtests/BTCGBP").status_code == 200
