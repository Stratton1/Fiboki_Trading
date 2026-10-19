"""GET /api/markets/replay/{symbol}: bar-by-bar replay with no look-ahead.

The central test perturbs every bar AFTER the cursor (a second dataset whose
future differs) and asserts the replay at that cursor is identical on both:
if any served bar, indicator value, regime, marker or trade field depended on
the future, the two answers would differ. The other tests pin the rule item
by item: every bar served has CLOSED by the cursor, every overlay item is
timed at or before it, and a backtest trade still open at the cursor carries
no exit, no exit reason, no P&L and no final stop.
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.data.integrity import IntegrityConfig, validate
from fiboki.data.schema import DatasetKind, describe_frame
from fiboki.data.store import DataStore
from fiboki.data.versioning import TransformationStep
from fiboki.research.artefacts import BacktestRecord, ResearchStore
from fiboki.research.experiment import ExperimentLedger
from tests.api.conftest import ORIGIN
from tests.api.test_overlays import _write_session
from tests.data_fixtures import make_bars, make_metadata

N = 600
CURSOR_BAR = 300  # the cursor is this bar's OPEN time: bars 0..299 have closed


def _write(root, frame: pd.DataFrame) -> str:
    with DataStore.initialise(root) as store:
        raw = store.write_raw(frame, make_metadata(frame))
        report = validate(frame)
        meta = describe_frame(
            frame, source="test", source_identifier="synthetic", timezone_of_origin="UTC",
            quality=report.quality, kind=DatasetKind.VALIDATED,
        )
        step = TransformationStep(
            operation="validate",
            parameters={"integrity_config": IntegrityConfig().to_dict()},
            code_version="2.0.0",
            inputs=(raw.version_id,),
        )
        stored = store.write_canonical(
            frame, meta, source_version=raw.version, transformation=step, integrity=report
        )
    return stored.version_id


def _ledger(path, frame: pd.DataFrame) -> str:
    def trade(i: int, entry: int, exit_: int) -> dict:
        return {
            "trade_id": f"trd_{i}", "instrument": "EURUSD", "direction": "long", "size": 0.5,
            "entry_price": float(frame.open.iloc[entry]), "exit_price": float(frame.open.iloc[exit_]),
            "entry_time": str(frame.index[entry]), "exit_time": str(frame.index[exit_]),
            "exit_reason": "take_profit", "gross_pnl": 11.0, "spread_cost": 1.0,
            "commission": 0.0, "slippage_cost": 0.0, "financing_cost": 0.0, "net_pnl": 10.0,
            "account_ccy": "GBP", "strategy_id": "donchian_breakout_atr", "bars_held": exit_ - entry,
            "max_adverse_excursion": -2.0, "max_favourable_excursion": 4.0,
            "provenance": "backtest", "fx_rate_used": 0.8,
            "initial_stop": float(frame.open.iloc[entry]) - 0.002,
            "final_stop": float(frame.open.iloc[entry]) + 0.0005,
            "take_profit_targets": [float(frame.open.iloc[entry]) + 0.004],
            "stop_moved_by": "breakeven",
        }

    with ResearchStore(ledger=ExperimentLedger(path)) as store:
        rec = store.add_backtest(
            BacktestRecord(
                strategy_id="donchian_breakout_atr", instruments=("EURUSD",), timeframe="H1",
                dataset_version_ids=("dsv",),
                trades=(trade(0, 250, 280), trade(1, 290, 320), trade(2, 400, 420)),
            )
        )
    return rec.backtest_id


def _world(tmp_path, api_env, frame: pd.DataFrame, name: str):
    root = tmp_path / name
    version = _write(root / "data", frame)
    _write_session(root / "paper", frame)
    run = _ledger(root / "experiments.sqlite", frame)
    env = {
        **api_env,
        "FIBOKI_STATE_DIR": str(root / "state"),
        "FIBOKI_DATA_ROOT": str(root / "data"),
        "FIBOKI_PAPER_ROOT": str(root / "paper"),
        "FIBOKI_EXPERIMENT_DB": str(root / "experiments.sqlite"),
    }
    return create_app(load_settings(env), configure_logs=False), version, run


@pytest.fixture
def worlds(tmp_path, api_env):
    frame = make_bars(periods=N)
    future = frame.copy()
    cut = frame.index[CURSOR_BAR]
    # A different future: every bar at or after the cursor bar scaled by 2%.
    for column in ("open", "high", "low", "close"):
        future.loc[future.index >= cut, column] = future.loc[future.index >= cut, column] * 1.02
    a = _world(tmp_path, api_env, frame, "a")
    b = _world(tmp_path, api_env, future, "b")
    with TestClient(a[0], base_url=ORIGIN) as ca, TestClient(b[0], base_url=ORIGIN) as cb:
        yield {"a": ca, "b": cb, "frame": frame, "run_a": a[2], "run_b": b[2],
               "version_a": a[1], "version_b": b[1]}


def _replay(client, cursor, **params):
    response = client.get(
        "/api/markets/replay/EURUSD",
        params={"cursor": cursor.isoformat(), "timeframe": "H1", **params},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text).tz_convert("UTC")


def _strip(data: dict) -> dict:
    """The replay without identifiers that differ between two stores by construction."""
    out = {k: v for k, v in data.items() if k not in ("dataset_version_id", "run_id")}
    ov = dict(out["overlays"])
    ov.pop("bars_dataset_version_id")
    ov["series"] = [
        {k: v for k, v in s.items() if k not in ("dataset_version_id", "source")}
        for s in ov["series"]
    ]
    ov["regimes"] = [
        {k: v for k, v in r.items() if k not in ("dataset_version_id", "source")}
        for r in ov["regimes"]
    ]
    ov.pop("sections")
    out["overlays"] = ov
    out["trades"] = [{k: v for k, v in t.items()} for t in out["trades"]]
    out.pop("trades_detail")
    # The step-forward cursor is the NEXT bar's close: a time, not a price.
    return out


def test_nothing_after_the_cursor_is_ever_served(worlds):
    frame = worlds["frame"]
    cursor = frame.index[CURSOR_BAR].to_pydatetime()
    data = _replay(worlds["a"], cursor, run=worlds["run_a"], limit=200)
    at = pd.Timestamp(cursor)
    assert _ts(data["cursor"]) == at
    # Bars: every one CLOSED by the cursor (open + 1h <= cursor), the last
    # one is the bar before the cursor bar, and there are `limit` of them.
    assert len(data["bars"]) == 200
    for bar in data["bars"]:
        assert _ts(bar["t"]) + timedelta(hours=1) <= at
    assert _ts(data["bars"][-1]["t"]) == frame.index[CURSOR_BAR - 1]
    assert data["bars_closed"] == CURSOR_BAR
    assert _ts(data["last_close"]) <= at
    assert _ts(data["next_cursor"]) == frame.index[CURSOR_BAR] + timedelta(hours=1)
    ov = data["overlays"]
    for section in ("signals", "fills", "events", "headlines"):
        for item in ov[section]:
            assert _ts(item["t"]) <= at, (section, item["t"])
    for regime in ov["regimes"]:
        assert _ts(regime["to"]) <= at
    for level in ov["levels"]:
        assert _ts(level["from"]) <= at
        assert level["to"] is None or _ts(level["to"]) <= at
    served = {_ts(b["t"]) for b in data["bars"]}
    assert ov["series"], "indicators are served in replay"
    for series in ov["series"]:
        assert not series["display_only"], "the non-causal lagging span is never replayed"
        assert {_ts(p["t"]) for p in series["points"]} <= served
    # Trades: entered by the cursor only; the one open at it has no future.
    trades = {t["trade_id"]: t for t in data["trades"]}
    assert set(trades) == {"trd_0", "trd_1"}
    assert trades["trd_0"]["open_at_cursor"] is False
    assert trades["trd_0"]["net_pnl"]["value"] == 10.0
    open_trade = trades["trd_1"]
    assert open_trade["open_at_cursor"] is True
    for field in ("exit_time", "exit_price", "exit_reason", "net_pnl", "final_stop"):
        assert open_trade[field] is None, field
    # Known at entry, so served: the initial stop and the planned target.
    assert open_trade["stop_loss"]["value"] is not None
    assert open_trade["take_profit_targets"]


def test_a_different_future_changes_nothing_at_the_cursor(worlds):
    """The no-look-ahead proof: same past, different future, identical replay."""
    cursor = worlds["frame"].index[CURSOR_BAR].to_pydatetime()
    a = _replay(worlds["a"], cursor, run=worlds["run_a"], limit=250)
    b = _replay(worlds["b"], cursor, run=worlds["run_b"], limit=250)
    assert a["bars"] == b["bars"]
    assert _strip(a) == _strip(b)
    # Not vacuous: one bar later the two worlds DO differ.
    later = worlds["frame"].index[CURSOR_BAR + 1].to_pydatetime()
    a2 = _replay(worlds["a"], later, limit=250)
    b2 = _replay(worlds["b"], later, limit=250)
    assert a2["bars"][-1] != b2["bars"][-1]


def test_indicators_in_replay_match_the_full_history_on_shared_bars(worlds):
    """Causal indicators: the replay value at a bar equals the full-history one."""
    client = worlds["a"]
    frame = worlds["frame"]
    cursor = frame.index[CURSOR_BAR].to_pydatetime()
    rep = _replay(client, cursor, limit=100)
    full = client.get(
        "/api/markets/overlays/EURUSD", params={"timeframe": "H1", "limit": N}
    ).json()["data"]
    full_series = {s["name"]: {p["t"]: p["v"] for p in s["points"]} for s in full["series"]}
    checked = 0
    for series in rep["series"] if "series" in rep else rep["overlays"]["series"]:
        other = full_series[series["name"]]
        for point in series["points"]:
            assert other[point["t"]] == point["v"], (series["name"], point["t"])
            checked += 1
    assert checked > 100


def test_stepping_walks_one_closed_bar_at_a_time(worlds):
    frame = worlds["frame"]
    first = _replay(worlds["a"], frame.index[CURSOR_BAR].to_pydatetime(), limit=10)
    step = _replay(worlds["a"], pd.Timestamp(first["next_cursor"]).to_pydatetime(), limit=10)
    assert step["bars_closed"] == first["bars_closed"] + 1
    assert step["bars"][-2] == first["bars"][-1]
    back = _replay(worlds["a"], pd.Timestamp(step["prev_cursor"]).to_pydatetime(), limit=10)
    assert back["bars"] == first["bars"]


def test_before_the_first_close_nothing_is_served(worlds):
    frame = worlds["frame"]
    data = _replay(worlds["a"], frame.index[0].to_pydatetime(), limit=10)
    assert data["bars"] == [] and data["bars_closed"] == 0
    assert data["overlays"]["series"] == [] and data["trades"] == []
    assert _ts(data["next_cursor"]) == frame.index[0] + timedelta(hours=1)


def test_replay_needs_a_cursor_and_mounted_data(client):
    assert client.get("/api/markets/replay/EURUSD").status_code == 422
    response = client.get(
        "/api/markets/replay/EURUSD", params={"cursor": "2026-01-05T10:00:00Z"}
    )
    assert response.status_code == 503
    assert response.json()["code"] == "market_data_not_mounted"
    assert client.get(
        "/api/markets/replay/NOPE", params={"cursor": "2026-01-05T10:00:00Z"}
    ).status_code == 404
