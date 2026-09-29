"""``fiboki events shadow-report``: read-only, and absence is an error, not a zero."""
from __future__ import annotations

import json

import pandas as pd

from fiboki.cli import main
from fiboki.marketstate.events import default_annotation_store_path
from tests.event_fixtures import AT, annotation_store, draft


def _trades(path) -> None:
    rows = [
        {"trade_id": "t1", "instrument": "EURUSD", "entry_time": AT.isoformat(),
         "net_pnl": -120.0, "max_adverse_excursion": -200.0},
        {"trade_id": "t2", "instrument": "EURGBP", "entry_time": AT.isoformat(),
         "net_pnl": 50.0, "max_adverse_excursion": -40.0},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_shadow_report_cli_reads_the_store_and_reports_the_split(tmp_path, capsys) -> None:
    state = tmp_path / "state"
    annotation_store(default_annotation_store_path(state), (draft(),)).close()
    trades = tmp_path / "trades.jsonl"
    _trades(trades)
    code = main(["events", "shadow-report", "--trades", str(trades), "--state-dir", str(state),
                 "--no-calendar", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    ev = next(c for c in payload["channels"] if c["name"] == "event_veto")
    assert (ev["blocked"]["n"], ev["kept"]["n"]) == (1, 1)
    assert ev["expectancy_kept_minus_blocked"] == 170.0
    assert payload["sufficient"] is False
    assert payload["policy"]["event_veto_enabled"] is False


def test_shadow_report_cli_fails_without_a_store(tmp_path) -> None:
    trades = tmp_path / "trades.jsonl"
    _trades(trades)
    code = main(["events", "shadow-report", "--trades", str(trades),
                 "--state-dir", str(tmp_path / "empty"), "--no-calendar"])
    assert code == 1
    assert not default_annotation_store_path(tmp_path / "empty").exists()


def test_shadow_report_cli_refuses_a_naive_entry_time(tmp_path) -> None:
    state = tmp_path / "state"
    annotation_store(default_annotation_store_path(state)).close()
    trades = tmp_path / "t.json"
    trades.write_text(json.dumps([{
        "trade_id": "t", "instrument": "EURUSD",
        "entry_time": str(pd.Timestamp("2024-06-03 12:00")), "net_pnl": 1.0,
        "max_adverse_excursion": 0.0,
    }]))
    code = main(["events", "shadow-report", "--trades", str(trades), "--state-dir", str(state),
                 "--no-calendar"])
    assert code == 2
