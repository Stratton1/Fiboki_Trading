"""GET /api/markets/overlays/{symbol} and volume on /api/markets/bars.

Everything is synthetic and local: a validated EURUSD H1 dataset built from
``tests.data_fixtures.make_bars``, one paper session written in the format
``scripts/run_paper_session.py`` produces (with telemetry), and a headline
store. The central assertions are parity ones: an indicator series served here
is bit-for-bit what :mod:`fiboki.indicators` computes on the same bars, so the
chart cannot disagree with the backtest.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import UTC, datetime
from itertools import pairwise

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.data.integrity import IntegrityConfig, validate
from fiboki.data.schema import DatasetKind, describe_frame
from fiboki.data.store import DataStore
from fiboki.data.versioning import TransformationStep
from fiboki.indicators import ATR, Ichimoku
from tests.api.conftest import ORIGIN
from tests.data_fixtures import make_bars, make_metadata

N_BARS = 600


def _write_dataset(root) -> tuple[pd.DataFrame, str]:
    frame = make_bars(periods=N_BARS)
    with DataStore.initialise(root) as store:
        raw = store.write_raw(frame, make_metadata(frame))
        report = validate(frame)
        meta = describe_frame(
            frame,
            source="test",
            source_identifier="synthetic",
            timezone_of_origin="UTC",
            quality=report.quality,
            kind=DatasetKind.VALIDATED,
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
    return frame, stored.version_id


def _iso(ts: pd.Timestamp) -> str:
    return ts.isoformat()


def _write_session(root, frame: pd.DataFrame) -> dict[str, pd.Timestamp]:
    t = {k: frame.index[i] for k, i in
         {"signal": 99, "entry": 100, "exit": 120, "blocked": 200, "open": 500}.items()}
    session = root / "donchian_eurusd"
    session.mkdir(parents=True)
    net = 12.5
    (session / "summary.json").write_text(
        json.dumps(
            {
                "provenance": "paper",
                "account_ccy": "USD",
                "instrument": "EURUSD",
                "timeframe": "H1",
                "strategy_id": "donchian_breakout_atr",
                "dataset_version_id": "ds_replayed_test",
                "dataset_last_bar": _iso(frame.index[-1]),
                "initial_balance": 10_000.0,
                "balance": 10_000.0 + net,
                "equity": 10_000.0 + net + 3.0,
                "trades": 1,
                "cost_breakdown": {
                    "spread_cost": 1.0,
                    "commission": 0.0,
                    "slippage_cost": 0.5,
                    "financing_cost": 0.25,
                },
            }
        )
    )
    with (session / "trades.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["trade_id", "instrument", "direction", "size", "entry_price", "exit_price",
             "entry_time", "exit_time", "exit_reason", "gross_pnl", "spread_cost",
             "commission", "slippage_cost", "financing_cost", "net_pnl", "account_ccy",
             "strategy_id", "provenance"]
        )
        writer.writerow(
            ["trd_1", "EURUSD", "long", 1000, 1.1000, 1.1142, _iso(t["entry"]),
             _iso(t["exit"]), "take_profit", 14.25, 1.0, 0.0, 0.5, 0.25, net, "USD",
             "donchian_breakout_atr", "paper"]
        )
    with (session / "positions.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["position_id", "instrument", "direction", "size", "entry_price", "entry_time",
             "stop_loss", "take_profit", "strategy_id"]
        )
        writer.writerow(
            ["pos_1", "EURUSD", "short", 500, 1.1050, _iso(t["open"]), 1.1100, 1.0950,
             "donchian_breakout_atr"]
        )
    telemetry = [
        {
            "signal_id": "sig_ok",
            "strategy_id": "donchian_breakout_atr",
            "instrument": "EURUSD",
            "requested_size": 1000,
            "decision": {"allowed": True, "reasons": []},
            "decided_at": _iso(t["signal"]),
            "extra": {"requested_price": 1.1001, "request_kind": "open"},
        },
        {
            "signal_id": "sig_blocked",
            "strategy_id": "donchian_breakout_atr",
            "instrument": "EURUSD",
            "requested_size": 777,
            "decision": {"allowed": False, "reasons": ["daily_loss"]},
            "decided_at": _iso(t["blocked"]),
            "extra": {"requested_price": 1.1020, "request_kind": "open"},
        },
    ]
    (session / "telemetry.jsonl").write_text("\n".join(json.dumps(r) for r in telemetry))
    return t


def _write_headlines(state_dir, frame: pd.DataFrame):
    from fiboki.data.news import HeadlineStore, NewItem, NewsSource, default_store_path

    path = default_store_path(state_dir)
    with HeadlineStore(path) as store:
        for key, source in (("fed", NewsSource.FED_RSS), ("ecb", NewsSource.ECB_RSS),
                            ("boe", NewsSource.BOE_RSS)):
            store.register_feed(feed_key=key, source=source, url=f"https://{key}.test/rss",
                                fmt="rss", discovered_from="test", retrieved_at="2026-01-01")

        def item(key, source, title):
            return NewItem(source=source, feed_key=key, url=f"https://{key}.test/{title}",
                           title=title, summary=None, source_item_id=None,
                           vendor_published_at=None, raw={})

        store.record([item("fed", NewsSource.FED_RSS, "fed-in-window")],
                     frame.index[150].to_pydatetime())
        store.record([item("ecb", NewsSource.ECB_RSS, "ecb-in-window")],
                     frame.index[151].to_pydatetime())
        store.record([item("boe", NewsSource.BOE_RSS, "boe-wrong-currency")],
                     frame.index[152].to_pydatetime())
        store.record([item("fed", NewsSource.FED_RSS, "fed-before-window")],
                     (frame.index[0] - pd.Timedelta(days=3)).to_pydatetime())
    return path


@pytest.fixture
def overlay_world(tmp_path, api_env):
    frame, version_id = _write_dataset(tmp_path / "data")
    times = _write_session(tmp_path / "paper", frame)
    state_dir = tmp_path / "state"
    news = _write_headlines(state_dir, frame)
    env = {
        **api_env,
        "FIBOKI_STATE_DIR": str(state_dir),
        "FIBOKI_DATA_ROOT": str(tmp_path / "data"),
        "FIBOKI_PAPER_ROOT": str(tmp_path / "paper"),
    }
    app = create_app(load_settings(env), configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        yield {"client": client, "frame": frame, "version": version_id, "times": times,
               "news": news}


def _get(world, **params):
    response = world["client"].get("/api/markets/overlays/EURUSD", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _values(points):
    return [p["v"] for p in points]


def _same(served, expected) -> bool:
    for a, b in zip(served, expected, strict=True):
        if b is None or (isinstance(b, float) and math.isnan(b)):
            if a is not None:
                return False
        elif a != b:
            return False
    return True


# --------------------------------------------------------------- series


def test_indicator_series_are_exactly_what_fiboki_indicators_computes(overlay_world):
    body = _get(overlay_world, timeframe="H1", limit=300)
    data = body["data"]
    frame = overlay_world["frame"]
    window = frame.index[-300:]
    assert data["window_from"].startswith(window[0].isoformat()[:19])
    assert data["bars_dataset_version_id"] == overlay_world["version"]

    series = {s["name"]: s for s in data["series"]}
    ich = Ichimoku()
    expected = ich.compute(frame).loc[window]
    tenkan = series[f"{ich.name}_tenkan"]
    assert tenkan["pane"] == "price"
    assert tenkan["indicator_key"] == "ichimoku"
    assert tenkan["params"] == ich.params()
    assert _same(_values(tenkan["points"]), expected[f"{ich.name}_tenkan"].tolist())
    assert _same(
        _values(series[f"{ich.name}_senkou_b"]["points"]),
        expected[f"{ich.name}_senkou_b"].tolist(),
    )
    assert series[f"{ich.name}_price_vs_cloud"]["pane"] == f"state:{ich.name}"

    atr = ATR(14)
    served_atr = series[atr.name]
    assert served_atr["pane"] == atr.name
    assert _same(_values(served_atr["points"]), atr.compute(frame).loc[window, atr.name].tolist())

    chikou = series[f"{ich.name}_chikou_span_display"]
    assert chikou["display_only"] is True
    # Only the chart-only series may be non-causal; every other one is not.
    assert [s["name"] for s in data["series"] if s["display_only"]] == [chikou["name"]]
    for s in data["series"]:
        assert len(s["points"]) == 300
        assert s["dataset_version_id"] == overlay_world["version"]
        assert s["provenance"] is None
        assert s["source"]["kind"] == "bar_store"
    # Strategy indicators come from the seed documents, via the compiler.
    assert {"rsi_14", "ema_100", "macd_12_26_9_line", "fib_5_ret_0618"} <= set(series)
    assert series["fib_5_dir"]["pane"] == "state:fib_5"


def test_strategy_filter_narrows_to_that_strategy_plus_the_baseline(overlay_world):
    data = _get(overlay_world, strategy_id="rsi_band_mean_reversion", limit=100)["data"]
    ids = {s["indicator_id"] for s in data["series"]}
    assert {"rsi_14", "bb_20_2", "donchian_60", "adx_14", "atr_14", "fib_5"} <= ids
    assert "ema_200" not in ids and "macd_12_26_9" not in ids
    missing = overlay_world["client"].get(
        "/api/markets/overlays/EURUSD", params={"strategy_id": "no_such_strategy"}
    )
    assert missing.status_code == 404


def test_regime_segments_tile_the_window(overlay_world):
    from fiboki.marketstate.regime import RegimeClassifier

    data = _get(overlay_world, limit=300)["data"]
    segments = data["regimes"]
    assert segments, data["sections"]["regimes"]
    assert segments[0]["from"] == data["window_from"]
    assert segments[-1]["to"] == data["window_to"]
    for a, b in pairwise(segments):
        assert a["to"] == b["from"]
        assert a["regime_key"] != b["regime_key"]
    for seg in segments:
        assert seg["label"] in {"trend", "range", "stress", "unknown"}
        assert set(seg["axes"]) == {"direction", "volatility", "persistence", "liquidity", "stress"}
        assert seg["classifier_fingerprint"] == RegimeClassifier().fingerprint
        assert seg["dataset_version_id"] == overlay_world["version"]


# ------------------------------------------------------- journal overlays


def test_fills_levels_and_signals_come_from_the_paper_journal(overlay_world):
    data = _get(overlay_world, limit=N_BARS)["data"]
    t = overlay_world["times"]
    fills = {(f["trade_id"], f["role"]): f for f in data["fills"]}
    entry, exit_ = fills[("trd_1", "entry")], fills[("trd_1", "exit")]
    assert (entry["side"], exit_["side"]) == ("buy", "sell")
    assert entry["t"].startswith(t["entry"].isoformat()[:19])
    assert exit_["t"].startswith(t["exit"].isoformat()[:19])
    assert entry["price"] == {**entry["price"], "value": 1.1, "provenance": "paper"}
    assert exit_["exit_reason"] == "take_profit"
    assert entry["dataset_version_id"] == "ds_replayed_test"
    assert entry["provenance"] == "paper"
    # The session charged slippage and financing: no caveat may deny it.
    codes = {c["code"] for c in exit_["net_pnl"]["caveats"]}
    assert "slippage_not_modelled" not in codes
    assert "financing_not_modelled" not in codes
    assert "paper_fill_assumption" in codes

    short_entry = fills[("pos_1", "entry")]
    assert short_entry["side"] == "sell"
    levels = {lv["role"]: lv for lv in data["levels"]}
    assert set(levels) == {"entry", "stop", "target"}
    assert levels["stop"]["price"]["value"] == 1.11
    assert levels["target"]["price"]["value"] == 1.095
    assert levels["stop"]["from"].startswith(t["open"].isoformat()[:19])
    assert levels["stop"]["to"] is None

    signals = {s["signal_id"]: s for s in data["signals"]}
    assert signals["sig_ok"]["outcome"] == "accepted"
    assert signals["sig_ok"]["side"] == "long", "matched to the fill it produced"
    assert signals["sig_ok"]["requested_price"]["value"] == 1.1001
    assert signals["sig_blocked"]["outcome"] == "blocked"
    assert signals["sig_blocked"]["side"] == "unknown"
    assert signals["sig_blocked"]["reason"] == "daily_loss"
    assert data["sections"]["backtest_fills"]["available"] is False
    assert data["sections"]["stop_moves"]["available"] is False


def test_journal_items_outside_the_window_are_not_drawn(overlay_world):
    frame = overlay_world["frame"]
    data = _get(
        overlay_world,
        **{"from": frame.index[300].isoformat(), "to": frame.index[400].isoformat()},
        limit=N_BARS,
    )["data"]
    assert data["fills"] == []
    assert data["signals"] == []
    assert len(data["series"][0]["points"]) == 101


# ------------------------------------------------------ calendar, headlines


def test_calendar_events_in_range_for_the_instruments_currencies(overlay_world):
    data = _get(overlay_world, limit=N_BARS)["data"]
    names = {(e["name"], e["currency"]) for e in data["events"]}
    assert ("US Non-Farm Payrolls", "USD") in names
    for event in data["events"]:
        assert event["currency"] in {"EUR", "USD"}
        assert event["dataset_version_id"].startswith("sha256:")
        assert data["window_from"] <= event["t"] <= data["window_to"]


def test_headlines_are_read_only_by_observed_at_and_currency(overlay_world):
    import os
    import sqlite3

    news = overlay_world["news"]

    def rows() -> int:
        with sqlite3.connect(f"file:{news}?mode=ro", uri=True) as conn:
            return conn.execute("SELECT COUNT(*) FROM headline").fetchone()[0]

    before = (os.stat(news).st_mtime_ns, os.stat(news).st_size, rows())
    data = _get(overlay_world, limit=N_BARS)["data"]
    # A read-only reader of a WAL database may create the -wal/-shm side files
    # SQLite needs for readers; the database file itself must be untouched.
    assert (os.stat(news).st_mtime_ns, os.stat(news).st_size, rows()) == before
    titles = [h["title"] for h in data["headlines"]]
    assert titles == ["fed-in-window", "ecb-in-window"]
    fed = data["headlines"][0]
    assert fed["t"].startswith(overlay_world["frame"].index[150].isoformat()[:19])
    assert fed["currency"] == "USD"


# ------------------------------------------------------------ absences


def test_without_a_bar_store_the_bar_sections_are_absent_not_empty(client):
    body = client.get(
        "/api/markets/overlays/EURUSD",
        params={"from": "2026-01-05T00:00:00Z", "to": "2026-01-20T00:00:00Z"},
    ).json()
    sections = body["data"]["sections"]
    assert sections["series"]["available"] is False
    assert sections["regimes"]["available"] is False
    # No journal: the seed fixture is never drawn on a chart.
    assert sections["fills"]["available"] is False
    assert body["data"]["fills"] == []
    assert sections["events"]["available"] is True
    assert body["data"]["events"]


def test_bad_requests(client):
    assert client.get("/api/markets/overlays/NOPE").status_code == 404
    assert client.get("/api/markets/overlays/EURUSD?timeframe=H7").status_code == 400
    response = client.get(
        "/api/markets/overlays/EURUSD",
        params={"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
    )
    assert response.status_code == 400


# ----------------------------------------------------------------- bars


def test_bars_carry_volume_when_the_dataset_has_it(overlay_world):
    body = overlay_world["client"].get("/api/markets/bars/EURUSD?timeframe=H1&limit=5").json()
    data = body["data"]
    assert data["volume_kind"] == "volume"
    expected = overlay_world["frame"]["volume"].tail(5).astype(float).tolist()
    assert [b["v"] for b in data["bars"]] == expected


def test_all_zero_volume_is_reported_as_absent():
    from fiboki.api.routers.markets import _volume_column

    frame = pd.DataFrame({"volume": [0.0, 0.0], "tick_volume": [None, None]})
    assert _volume_column(frame) is None
    assert _volume_column(pd.DataFrame({"tick_volume": [3.0, 0.0]})) == "tick_volume"


def test_overlay_times_are_utc(overlay_world):
    data = _get(overlay_world, limit=50)["data"]
    for stamp in (data["window_from"], data["window_to"]):
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        assert parsed.utcoffset() == UTC.utcoffset(None)
