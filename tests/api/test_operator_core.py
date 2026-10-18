"""Operator core (terminal programme Wave 1A): the endpoints behind Command,
Fleet, the limit board, the System board, the alert manager and search.

Every decision these screens show is made by the API and pinned here: limit
bands and breaches, the attention queue's agreement with the bars, bot state,
day and week P&L, worker staleness, search ranking and every deep link.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from fiboki.api import links
from fiboki.api.app import create_app
from fiboki.api.health import build_health
from fiboki.api.limit_board import DISPLAY_BANDS, band_of
from fiboki.api.market_sessions import fx_week_open, market_sessions
from fiboki.api.platform import read_worker_heartbeat
from fiboki.api.routers.fleet import fleet_rows
from fiboki.api.routers.search import tier_of
from fiboki.api.settings import load_settings
from fiboki.workers.base import WORKER_HEARTBEAT, WorkerStore
from tests.api.conftest import ORIGIN

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"


def _client(env: dict[str, str]) -> TestClient:
    return TestClient(create_app(load_settings(env), configure_logs=False), base_url=ORIGIN)


@pytest.fixture
def journal_env(tmp_path, api_env):
    shutil.copytree(FIXTURE, tmp_path / "paper")
    return {**api_env, "FIBOKI_PAPER_ROOT": str(tmp_path / "paper")}


def _forward_session(root: Path, *, name: str, written_at: datetime, instrument: str = "EURUSD",
                     timeframe: str = "H1", telemetry: list[dict] | None = None) -> None:
    d = root / f"forward-{name}"
    d.mkdir(parents=True)
    (d / "summary.json").write_text(
        json.dumps(
            {
                "provenance": "paper",
                "source": "fiboki paper forward",
                "wiring_version": name,
                "strategy_id": "donchian_breakout_atr",
                "instrument": instrument,
                "timeframe": timeframe,
                "account_ccy": "USD",
                "initial_balance": 100000.0,
                "balance": 100000.0,
                "equity": 99990.0,
                "dataset_last_bar": written_at.isoformat(),
                "written_at": written_at.isoformat(),
                "trades": 0,
                "open_positions": 0,
            }
        )
    )
    (d / "trades.csv").write_text("")
    (d / "positions.csv").write_text("")
    if telemetry:
        (d / "telemetry.jsonl").write_text("\n".join(json.dumps(r) for r in telemetry) + "\n")


# ------------------------------------------------------------- limit board


def test_bands_are_the_platform_s_and_inclusive_at_their_thresholds():
    assert (DISPLAY_BANDS.warn_at_pct, DISPLAY_BANDS.critical_at_pct) == (70.0, 90.0)
    assert band_of(69.99, False) == "ok"
    assert band_of(70.0, False) == "warn"
    assert band_of(89.99, False) == "warn"
    assert band_of(90.0, False) == "critical"
    assert band_of(10.0, True) == "breached"
    assert band_of(None, False) == "absent"
    assert band_of(None, True) == "breached"


def test_limit_board_on_the_seed_is_ordered_and_says_it_is_a_fixture(client):
    body = client.get("/api/trading/limits").json()
    board = body["data"]
    assert board["bands"]["warn_at"]["value"] == 70.0
    assert board["bands"]["critical_at"]["value"] == 90.0
    assert "limits_on_fixture" in {c["code"] for c in body["caveats"]}
    rows = board["rows"]
    measured = [r for r in rows if r["band"] != "absent"]
    absent = [r for r in rows if r["band"] == "absent"]
    # Absent rows come last; measured rows most used first (breaches first).
    assert rows == measured + absent
    urg = [(1e9 if r["breached"] else 0) + (r["utilisation_pct"]["value"] or 0) for r in measured]
    assert urg == sorted(urg, reverse=True)
    for row in absent:
        assert row["utilisation_pct"]["value"] is None
        assert row["absent_reason"]
    # Every family lists its rows by key and in board order.
    keys = [r["key"] for r in rows]
    for family in board["families"]:
        assert family["rows"] == [k for k in keys if k in set(family["rows"])]
    assert board["closest_key"] == (measured[0]["key"] if measured else None)


def test_attention_queue_and_limit_bars_agree(journal_env):
    """The queue lists exactly the rows the bars draw NEAR, CRITICAL or BREACHED."""
    with _client(journal_env) as c:
        platform = c.app.state.platform
        # Tighten two caps so the measured book lands in several bands.
        platform.limits = dataclasses.replace(
            platform.limits, max_instrument_exposure_pct=10.0, max_currency_exposure_pct=19.0
        )
        board = c.get("/api/trading/limits").json()["data"]
        items = c.get("/api/command/attention").json()["items"]
    expected = {
        f"limit:{r['key']}": {"breached": "critical", "critical": "error", "warn": "warning"}[r["band"]]
        for r in board["rows"]
        if r["band"] in ("warn", "critical", "breached")
    }
    got = {i["id"]: i["severity"] for i in items if i["category"] in ("limit_breach", "limit_near")}
    assert expected, "the tightened caps must put at least one row in a band"
    assert got == expected
    for item in items:
        if item["category"] == "limit_breach":
            assert item["severity"] == "critical"
    assert items[0]["id"] == "limit:exposure:instrument:XAUUSD"


def test_utilisation_and_headroom_follow_the_breach_rule(journal_env):
    with _client(journal_env) as c:
        board = c.get("/api/trading/limits").json()["data"]
    rows = {r["key"]: r for r in board["rows"]}
    dd = rows["risk:drawdown"]
    if dd["band"] != "absent":
        value, limit = dd["value"]["value"], dd["limit"]["value"]
        assert dd["utilisation_pct"]["value"] == pytest.approx(value / limit * 100, abs=0.01)
        assert dd["headroom"]["value"] == pytest.approx(limit - value, abs=1e-3)
        assert dd["breached"] == (value > limit)
    daily = rows["risk:daily_loss"]
    if daily["band"] != "absent":
        value, limit = daily["value"]["value"], daily["limit"]["value"]
        assert daily["utilisation_pct"]["value"] == pytest.approx(max(0, -value) / limit * 100, abs=0.01)
        assert daily["headroom"]["value"] == pytest.approx(value + limit, abs=1e-3)
    assert rows["risk:weekly_loss"]["band"] == "absent"


# ------------------------------------------------------------- money strip


def test_summary_on_the_seed_reports_absence_not_zero(client):
    body = client.get("/api/command/summary").json()
    data = body["data"]
    assert data["equity"]["value"] is None
    assert data["day_pnl"]["value"] is None
    assert data["bots"]["total"]["value"] is None
    assert data["research_in_flight"]["value"] is None
    assert [s["key"] for s in data["sessions"]] == ["sydney", "tokyo", "london", "new_york"]
    assert all(links.matches_route(v) for v in data["links"].values())


def test_summary_day_and_week_pnl_are_the_server_s_sums(journal_env):
    with _client(journal_env) as c:
        data = c.get("/api/command/summary").json()["data"]
        trades = c.get("/api/trading/trades?provenance=paper&limit=500").json()["items"]
    assert data["pnl_day"] is not None
    day = datetime.fromisoformat(data["pnl_day"]).date()
    week_start = datetime.fromisoformat(data["pnl_week_start"]).date()
    exits = [(datetime.fromisoformat(t["exit_time"].replace("Z", "+00:00")).date(), t["net_pnl"]["value"]) for t in trades]
    assert data["day_pnl"]["value"] == pytest.approx(round(sum(v for d, v in exits if d == day), 2))
    assert data["week_pnl"]["value"] == pytest.approx(
        round(sum(v for d, v in exits if week_start <= d <= day), 2)
    )
    assert data["bots"]["total"]["value"] == 2.0
    assert data["bots"]["finished"]["value"] == 2.0


# ------------------------------------------------------------------- fleet


def test_fleet_lists_every_session_with_state_and_links(journal_env, tmp_path):
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)  # a Wednesday
    root = Path(journal_env["FIBOKI_PAPER_ROOT"])
    _forward_session(
        root,
        name="fx_live",
        written_at=now - timedelta(minutes=10),
        telemetry=[
            {"decided_at": (now - timedelta(hours=3)).isoformat(), "decision": {"allowed": True}, "extra": {"request_kind": "open"}},
            {"decided_at": (now - timedelta(hours=1)).isoformat(), "decision": {"allowed": False, "reasons": ["total_drawdown"]}, "extra": {"request_kind": "open"}},
        ],
    )
    _forward_session(root, name="fx_silent", written_at=now - timedelta(hours=5))
    _forward_session(root, name="crypto", written_at=now - timedelta(hours=1), instrument="BTCGBP", timeframe="H4")
    with _client(journal_env) as c:
        platform = c.app.state.platform
        rows = {r.label: r for r in fleet_rows(platform, c.app.state.settings, now=now)}
        api = c.get("/api/trading/fleet").json()
    assert rows["fx_live"].state == "running"
    assert rows["fx_live"].last_signal is not None
    assert rows["fx_live"].last_signal.outcome == "blocked"
    assert rows["fx_live"].last_signal.reason == "total_drawdown"
    assert rows["fx_silent"].state == "stale"
    assert rows["fx_silent"].health == "critical"
    assert rows["crypto"].state == "running"
    assert rows["crypto"].asset_class == "crypto"
    replays = [r for r in rows.values() if r.kind == "replay"]
    assert len(replays) == 2 and all(r.state == "finished" for r in replays)
    # Worst state first.
    assert next(iter(fleet_rows(platform, c.app.state.settings, now=now))).state == "stale"
    for row in api["items"]:
        for link in row["links"].values():
            assert link is None or links.matches_route(link), link


def test_a_silent_fx_bot_over_the_weekend_is_idle_not_stale(journal_env):
    saturday = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    root = Path(journal_env["FIBOKI_PAPER_ROOT"])
    _forward_session(root, name="fx_weekend", written_at=saturday - timedelta(hours=19))
    with _client(journal_env) as c:
        rows = {r.label: r for r in fleet_rows(c.app.state.platform, c.app.state.settings, now=saturday)}
    assert rows["fx_weekend"].state == "idle"


def test_an_armed_kill_switch_halts_every_bot(journal_env):
    from fiboki.risk.killswitch import KillSwitchMode

    with _client(journal_env) as c:
        platform = c.app.state.platform
        platform.kill_switch.activate(KillSwitchMode.PAUSE, operator="joe", reason="test the fleet halt")
        rows = fleet_rows(platform, c.app.state.settings)
        summary = c.get("/api/command/summary").json()["data"]
    assert rows and all(r.state == "halted" for r in rows)
    assert summary["bots"]["halted"]["value"] == float(len(rows))
    assert summary["kill_switch_active"] is True


def test_fleet_without_a_journal_is_absent_not_idle(client):
    body = client.get("/api/trading/fleet").json()
    assert body["items"] == []
    assert body["source"]["kind"] == "absent"


# ------------------------------------------------------------- attribution


def test_attribution_sums_to_the_paper_total(journal_env):
    with _client(journal_env) as c:
        data = c.get("/api/trading/attribution").json()["data"]
    total = data["total_net_pnl"]["value"]
    assert data["provenance"] == "paper"
    for side in ("by_strategy", "by_instrument"):
        rows = data[side]
        assert rows
        assert sum(r["net_pnl"]["value"] for r in rows) == pytest.approx(total, abs=0.02)
        shares = [r["share_of_abs_pct"]["value"] for r in rows if r["share_of_abs_pct"]["value"] is not None]
        assert sum(shares) == pytest.approx(100.0, abs=0.5)
        for r in rows:
            assert links.matches_route(r["link"])


# ------------------------------------------------------------ system board


def test_board_is_one_clock_and_marks_a_missing_worker_stale(client):
    body = client.get("/api/system/board").json()
    data = body["data"]
    worker = [r for r in data["rows"] if r["group"] == "worker"]
    assert worker and worker[0]["stale"] is True and worker[0]["status"] == "down"
    assert worker[0]["age"]["value"] is None
    assert data["stale"]["value"] >= 1
    assert data["status"] in ("degraded", "down")
    for row in data["rows"]:
        assert links.matches_route(row["deep_link"]), row["deep_link"]


def test_health_carries_the_platform_s_worker_verdict(tmp_path, api_env, monkeypatch):
    now = datetime.now(tz=UTC)
    db = tmp_path / "worker.db"
    with WorkerStore.sqlite_at(db) as store, store.engine.begin() as conn:
        conn.execute(
            insert(WORKER_HEARTBEAT).values(
                worker_id="live@mac:1", kind="live", beat_at=now - timedelta(seconds=30),
                started_at=now - timedelta(hours=1), status="idle",
            )
        )
    settings = load_settings(api_env)
    with _client(api_env) as c:
        platform = c.app.state.platform
        reading = read_worker_heartbeat(db, stale_after_seconds=settings.worker_heartbeat_stale_seconds, now=now)
        monkeypatch.setattr(type(platform), "worker_heartbeat", lambda self: reading)
        report = build_health(platform, settings)
    assert report.worker_state == "ok"
    assert report.worker_stale_after_seconds == settings.worker_heartbeat_stale_seconds
    assert report.worker_stale_at == reading.newest_beat_at + timedelta(
        seconds=settings.worker_heartbeat_stale_seconds
    )


def test_health_without_a_worker_says_absent(client):
    body = client.get("/api/health").json()
    assert body["worker_state"] == "absent"
    assert body["worker_stale_at"] is None


# ----------------------------------------------------------- alert manager


def test_alert_manager_lists_every_rule_with_state_and_history(client, tmp_path, monkeypatch):
    from fiboki.obs.alerts import AlertEvent

    log = tmp_path / "alerts.jsonl"
    at = datetime.now(tz=UTC) - timedelta(minutes=5)
    log.write_text(
        json.dumps({"event": "heartbeat_stale", "severity": "error", "message": "worker late",
                    "at": at.isoformat(), "source": "w1", "dedupe_key": "heartbeat_stale:w1"}) + "\n"
    )
    monkeypatch.setenv("FIBOKI_ALERT_LOG", str(log))
    data = client.get("/api/system/alerts").json()["data"]
    events = {r["event"]: r for r in data["rules"]}
    assert set(events) == {e.value for e in AlertEvent}
    stale = events["heartbeat_stale"]
    assert stale["state"] == "firing"
    assert stale["fired"]["value"] == 1.0
    assert stale["open_incident"] and links.matches_route(stale["open_incident"])
    assert events["worker_down"]["state"] == "never_fired"
    assert data["rules"][0]["event"] == "heartbeat_stale"  # firing first
    assert data["history"][0]["event"] == "heartbeat_stale"
    assert "file" in data["channels"]


# ------------------------------------------------------------------ search


def test_search_ranks_the_exact_instrument_first(client):
    items = client.get("/api/search?q=EURUSD").json()["items"]
    assert items[0]["kind"] == "instrument" and items[0]["id"] == "EURUSD" and items[0]["tier"] == 0
    assert items[0]["href"] == "/markets/EURUSD"
    prefixed = client.get("/api/search?q=eur").json()["items"]
    assert prefixed and all(h["tier"] >= 1 for h in prefixed)
    assert [h["tier"] for h in prefixed] == sorted(h["tier"] for h in prefixed)
    assert client.get("/api/search?q=zzzz_nothing").json()["items"] == []


def test_search_finds_strategies_and_sessions(journal_env):
    with _client(journal_env) as c:
        hits = c.get("/api/search?q=donchian").json()["items"]
    kinds = {h["kind"] for h in hits}
    assert "strategy" in kinds and "session" in kinds
    for hit in hits:
        assert links.matches_route(hit["href"]), hit["href"]


def test_tiers():
    assert tier_of("eurusd", "EURUSD", "EURUSD") == 0
    assert tier_of("eur", "EURUSD", "EURUSD") == 1
    assert tier_of("breakout", "donchian_breakout_atr", "Donchian breakout") == 2
    assert tier_of("usd", "EURUSD", "EURUSD") == 3
    assert tier_of("xyz", "EURUSD", "EURUSD") is None


# --------------------------------------------------------- links and clocks


def test_every_attention_deep_link_is_a_page(journal_env, tmp_path, monkeypatch):
    log = tmp_path / "alerts.jsonl"
    log.write_text(
        json.dumps({"event": "reconciliation_divergence", "severity": "critical", "message": "x",
                    "at": datetime.now(tz=UTC).isoformat(), "source": "live"}) + "\n"
    )
    monkeypatch.setenv("FIBOKI_ALERT_LOG", str(log))
    with _client(journal_env) as c:
        c.app.state.platform.limits = dataclasses.replace(
            c.app.state.platform.limits, max_instrument_exposure_pct=10.0
        )
        items = c.get("/api/command/attention").json()["items"]
    assert items
    for item in items:
        assert links.matches_route(item["deep_link"]), item["deep_link"]


def test_route_matching():
    assert links.matches_route("/trading/risk")
    assert links.matches_route("/markets/EURUSD?tf=H4&run=bt_1")
    assert links.matches_route("/research/runs/abc")
    assert not links.matches_route("/risk")
    assert not links.matches_route("/journal")
    assert not links.matches_route("https://example.com/")


def test_market_sessions_follow_local_time_and_the_fx_week():
    summer_wed = datetime(2026, 7, 15, 7, 30, tzinfo=UTC)  # 08:30 BST
    winter_wed = datetime(2026, 1, 14, 7, 30, tzinfo=UTC)  # 07:30 GMT
    assert {s.key: s.open for s in market_sessions(summer_wed)}["london"] is True
    assert {s.key: s.open for s in market_sessions(winter_wed)}["london"] is False
    saturday = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert not fx_week_open(saturday)
    assert not any(s.open for s in market_sessions(saturday))
    sunday_open = datetime(2026, 10, 18, 21, 30, tzinfo=UTC)  # 17:30 New York (EDT)
    assert fx_week_open(sunday_open)
    for s in market_sessions(saturday):
        assert s.next_change_at > saturday


def test_clock_is_the_api_s_and_lists_the_sessions(client):
    body = client.get("/api/system/clock").json()
    assert [s["key"] for s in body["data"]["sessions"]] == ["sydney", "tokyo", "london", "new_york"]
    stamp = datetime.fromisoformat(body["data"]["server_time"].replace("Z", "+00:00"))
    assert abs((datetime.now(tz=UTC) - stamp).total_seconds()) < 60


def test_the_route_table_names_the_workstation_s_pages():
    """links.ROUTE_PATTERNS is every page on disk (the research entity routes exist since wave/research)."""
    app = Path(__file__).resolve().parents[2] / "apps" / "web" / "app"
    pages = set()
    for page in app.rglob("page.tsx"):
        rel = page.parent.relative_to(app).as_posix()
        pages.add("/" if rel == "." else f"/{rel}")
    assert set(links.ROUTE_PATTERNS) == pages
