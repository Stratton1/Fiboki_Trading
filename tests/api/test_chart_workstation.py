"""Chart workstation routes (Wave 2B): compare, data window, events, templates, price alerts.

Synthetic and local: EURUSD and GBPUSD H1 datasets written through the real
store, one paper session, and the official calendar shipped in the repo.
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from tests.api.conftest import ORIGIN
from tests.api.test_chart_replay import _write
from tests.api.test_overlays import _write_session
from tests.data_fixtures import make_bars

N = 600


@pytest.fixture
def world(tmp_path, api_env):
    eur = make_bars(periods=N)
    # GBPUSD misses three bars the EURUSD set has: compare must gap, not fill.
    gbp = make_bars(instrument="GBPUSD", periods=N, seed=5)
    gbp = gbp.drop(gbp.index[[50, 51, 52]])
    v_eur = _write(tmp_path / "data", eur)
    v_gbp = _write(tmp_path / "data", gbp)
    _write_session(tmp_path / "paper", eur)
    env = {
        **api_env,
        "FIBOKI_DATA_ROOT": str(tmp_path / "data"),
        "FIBOKI_PAPER_ROOT": str(tmp_path / "paper"),
    }
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        yield {"client": client, "eur": eur, "gbp": gbp, "v_eur": v_eur, "v_gbp": v_gbp}


# ------------------------------------------------------------------ compare


def test_compare_rebases_the_other_close_to_percent_on_the_base_bars(world):
    eur, gbp = world["eur"], world["gbp"]
    body = world["client"].get(
        "/api/markets/compare/EURUSD",
        params={"with": "GBPUSD", "timeframe": "H1", "limit": 100,
                "to": eur.index[99].isoformat()},
    ).json()
    data = body["data"]
    assert data["base"] == "EURUSD" and data["symbol"] == "GBPUSD"
    assert data["unit"] == "percent"
    assert data["base_dataset_version_id"] == world["v_eur"]
    assert data["dataset_version_id"] == world["v_gbp"]
    times = [pd.Timestamp(p["t"]) for p in data["points"]]
    assert times == list(eur.index[:100])
    anchor = float(gbp["close"].iloc[0])
    assert data["anchor_close"]["value"] == anchor
    assert data["points"][0]["v"] == 0.0
    for point in data["points"]:
        stamp = pd.Timestamp(point["t"])
        if stamp in gbp.index:
            expected = (float(gbp.loc[stamp, "close"]) / anchor - 1.0) * 100.0
            assert point["v"] == pytest.approx(expected, abs=1e-12)
        else:
            assert point["v"] is None
    assert data["unmatched"] == 3
    assert [c["code"] for c in body["caveats"]] == ["compare_unaligned_bars"]


def test_compare_is_deterministic_and_validates_its_inputs(world):
    client = world["client"]
    params = {"with": "GBPUSD", "timeframe": "H1", "limit": 50}
    first = client.get("/api/markets/compare/EURUSD", params=params).json()["data"]
    again = client.get("/api/markets/compare/EURUSD", params=params).json()["data"]
    assert first == again
    assert client.get("/api/markets/compare/EURUSD", params={"with": "NOPE"}).status_code == 404
    assert client.get(
        "/api/markets/compare/EURUSD", params={"with": "GBPUSD", "timeframe": "H7"}
    ).status_code == 400


def test_compare_without_market_data_is_unavailable_not_flat(client):
    response = client.get("/api/markets/compare/EURUSD", params={"with": "GBPUSD"})
    assert response.status_code == 503
    assert response.json()["code"] == "market_data_not_mounted"


# -------------------------------------------------------------- data window


def test_data_window_serves_every_indicator_and_overlay_at_one_bar(world):
    from fiboki.indicators import ATR, Ichimoku

    eur = world["eur"]
    # 30 minutes into bar 100 (the paper trade's entry bar): the bar is 100.
    at = eur.index[100] + pd.Timedelta(minutes=30)
    body = world["client"].get(
        "/api/markets/datawindow/EURUSD", params={"t": at.isoformat(), "timeframe": "H1"}
    ).json()
    data = body["data"]
    assert pd.Timestamp(data["t"]) == eur.index[100]
    assert pd.Timestamp(data["t_end"]) == eur.index[100] + pd.Timedelta(hours=1)
    assert data["dataset_version_id"] == world["v_eur"]
    assert data["bar"]["c"] == float(eur["close"].iloc[100])
    values = {v["name"]: v["value"] for v in data["values"]}
    ich = Ichimoku()
    expected = ich.compute(eur).iloc[100]
    assert values[f"{ich.name}_tenkan"] == float(expected[f"{ich.name}_tenkan"])
    # State columns are served too (the chart hides them by default).
    assert f"{ich.name}_price_vs_cloud" in values
    atr = ATR(14)
    assert values[atr.name] == float(atr.compute(eur)[atr.name].iloc[100])
    # The paper trade entered at bar 100's open: its entry fill is on this bar.
    assert [f["role"] for f in data["fills"]] == ["entry"]
    assert all(pd.Timestamp(e["t"]) >= eur.index[100] for e in data["events"])


def test_data_window_needs_a_bar_at_or_before_t(world):
    early = world["eur"].index[0] - pd.Timedelta(days=1)
    response = world["client"].get(
        "/api/markets/datawindow/EURUSD", params={"t": early.isoformat()}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "no_bar_at_time"
