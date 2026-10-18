"""Overlays read EVERY paper session that trades the symbol, labelled by session.

A replay session names one instrument in ``summary.json``; a forward session
(``paper_forward``: the FX one and the 24/7 crypto one) names several,
comma-separated, and its rows say which instrument each one is. The overlay
route used to match ``summary.instrument == symbol`` and so drew nothing from
a forward session at all. It now matches on the instruments a session names
or has rows for, keeps only the symbol's own rows, and labels every item with
the session it came from. A closed trade whose session recorded its stop and
target gets them as levels that stood from its entry to its exit.
"""
from __future__ import annotations

import csv
import json

from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from tests.api.conftest import ORIGIN

TRADE_FIELDS = [
    "trade_id", "strategy_id", "instrument", "direction", "size", "entry_price",
    "exit_price", "entry_time", "exit_time", "exit_reason", "gross_pnl", "spread_cost",
    "commission", "slippage_cost", "financing_cost", "net_pnl", "account_ccy", "provenance",
    "initial_stop", "final_stop", "take_profit",
]


def _forward_session(root, name, wiring, instruments, trades, positions=()):
    session = root / name
    session.mkdir(parents=True)
    (session / "summary.json").write_text(
        json.dumps(
            {
                "provenance": "paper",
                "account_ccy": "GBP",
                "initial_balance": 10_000.0,
                "balance": 10_000.0 + sum(t["net_pnl"] for t in trades),
                "equity": 10_000.0 + sum(t["net_pnl"] for t in trades),
                "strategy_id": "donchian_breakout_atr__ccore1",
                "instrument": ",".join(instruments),
                "timeframe": "H4",
                "trades": len(trades),
                "dataset_last_bar": "2026-10-12T20:00:00+00:00",
                "wiring_version": wiring,
            }
        )
    )
    with (session / "trades.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_FIELDS)
        writer.writeheader()
        for row in trades:
            writer.writerow(row)
    with (session / "positions.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["position_id", "strategy_id", "instrument", "direction", "size",
                        "entry_price", "mark_price", "entry_time", "stop_loss", "take_profit"],
        )
        writer.writeheader()
        for row in positions:
            writer.writerow(row)


def _trade(trade_id, instrument, entry, exit_, net, *, stop=None, target=None):
    return {
        "trade_id": trade_id,
        "strategy_id": "donchian_breakout_atr__ccore1",
        "instrument": instrument,
        "direction": "long",
        "size": 0.01,
        "entry_price": 50_000.0,
        "exit_price": 50_500.0,
        "entry_time": entry,
        "exit_time": exit_,
        "exit_reason": "take_profit",
        "gross_pnl": net + 6.0,
        "spread_cost": 3.0,
        "commission": 3.0,
        "slippage_cost": 0.0,
        "financing_cost": 0.0,
        "net_pnl": net,
        "account_ccy": "GBP",
        "provenance": "paper",
        "initial_stop": "" if stop is None else stop,
        "final_stop": "" if stop is None else stop,
        "take_profit": "" if target is None else target,
    }


def test_a_crypto_forward_session_is_drawn_per_instrument_and_labelled(api_env, tmp_path):
    paper = tmp_path / "paper"
    _forward_session(
        paper,
        "forward-paper_forward_crypto_v1",
        "paper_forward_crypto_v1",
        ["BTCGBP", "ETHGBP"],
        [
            # A Saturday trade: crypto is 24/7.
            _trade("t_btc", "BTCGBP", "2026-10-10T08:00:00+00:00", "2026-10-10T20:00:00+00:00",
                   41.5, stop=49_200.0, target=51_000.0),
            _trade("t_eth", "ETHGBP", "2026-10-10T12:00:00+00:00", "2026-10-11T00:00:00+00:00",
                   -12.0),
        ],
        positions=[
            {"position_id": "p_eth", "strategy_id": "donchian_breakout_atr__ccore1",
             "instrument": "ETHGBP", "direction": "long", "size": 0.2, "entry_price": 1_900.0,
             "mark_price": 1_910.0, "entry_time": "2026-10-12T00:00:00+00:00",
             "stop_loss": 1_850.0, "take_profit": 2_000.0},
        ],
    )
    _forward_session(
        paper, "forward-paper_forward_v1", "paper_forward_v1", ["EURUSD", "GBPUSD"],
        [_trade("t_fx", "EURUSD", "2026-10-09T08:00:00+00:00", "2026-10-09T12:00:00+00:00", 3.0)],
    )
    env = {**api_env, "FIBOKI_PAPER_ROOT": str(paper)}
    app = create_app(load_settings(env), configure_logs=False)
    window = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-13T00:00:00Z", "timeframe": "H4"}
    with TestClient(app, base_url=ORIGIN) as client:
        btc = client.get("/api/markets/overlays/BTCGBP", params=window).json()["data"]
        eth = client.get("/api/markets/overlays/ETHGBP", params=window).json()["data"]
        fx = client.get("/api/markets/overlays/EURUSD", params=window).json()["data"]

    assert {(f["trade_id"], f["role"]) for f in btc["fills"]} == {
        ("t_btc", "entry"), ("t_btc", "exit")
    }
    assert {f["session_label"] for f in btc["fills"]} == {"paper_forward_crypto_v1"}
    assert {f["session_id"] for f in btc["fills"]} == {"forward-paper_forward_crypto_v1"}
    exit_fill = next(f for f in btc["fills"] if f["role"] == "exit")
    assert exit_fill["net_pnl"]["value"] == 41.5 and exit_fill["net_pnl"]["unit"] == "GBP"
    # The closed trade's recorded stop and target, standing for its life.
    closed = {(lv["role"], lv["price"]["value"], lv["to"]) for lv in btc["levels"]}
    assert closed == {
        ("stop", 49_200.0, "2026-10-10T20:00:00Z"),
        ("target", 51_000.0, "2026-10-10T20:00:00Z"),
    }
    assert "paper_forward_crypto_v1" in btc["sections"]["fills"]["detail"]

    # ETH: its own trade (no recorded stop, so no closed-trade level), and the
    # open position's three levels (to=None), never BTC's.
    assert {f["trade_id"] for f in eth["fills"]} == {"t_eth", "p_eth"}
    assert {(lv["role"], lv["to"]) for lv in eth["levels"]} == {
        ("entry", None), ("stop", None), ("target", None)
    }
    assert {lv["session_label"] for lv in eth["levels"]} == {"paper_forward_crypto_v1"}

    # The FX forward session is drawn on EURUSD only, labelled as itself.
    assert {f["trade_id"] for f in fx["fills"]} == {"t_fx"}
    assert {f["session_label"] for f in fx["fills"]} == {"paper_forward_v1"}
    assert "forward session paper_forward_v1" in fx["fills"][0]["source"]["detail"]
