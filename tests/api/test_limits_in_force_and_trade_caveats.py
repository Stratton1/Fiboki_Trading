"""The API shows the limit set the gateway enforces, and trade rows carry the
realism caveats their own paper session supports.

* ``/api/system/settings`` and ``/api/trading/risk`` used a hard-coded
  ``limits_v1_paper`` while every composition root enforces ``limits_v2_paper``
  in paper mode. Both now read :func:`fiboki.risk.limits.default_limit_set`.
* ``/api/trading/trades`` rows got settings-only caveats ("slippage not
  modelled") even when the row's session summary proves slippage was charged.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from fiboki.api.app import create_app
from fiboki.api.settings import load_settings
from fiboki.core.enums import ExecutionMode
from fiboki.risk.limits import (
    CONSERVATIVE_LIMITS_V2,
    DEFAULT_LIMITS_V2,
    PAPER_LIMITS_V2,
    default_limit_set,
)
from tests.api.conftest import ORIGIN

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"
DENIALS = {"slippage_not_modelled", "financing_not_modelled"}


def test_default_limit_set_per_mode() -> None:
    assert default_limit_set(ExecutionMode.PAPER) is PAPER_LIMITS_V2
    assert default_limit_set("paper").version == "limits_v2_paper"
    assert default_limit_set(ExecutionMode.BACKTEST) is PAPER_LIMITS_V2
    assert default_limit_set(ExecutionMode.SHADOW) is DEFAULT_LIMITS_V2
    assert default_limit_set(ExecutionMode.DEMO) is DEFAULT_LIMITS_V2
    assert default_limit_set(ExecutionMode.LIVE) is CONSERVATIVE_LIMITS_V2


def test_settings_and_risk_show_the_set_in_force(admin_client) -> None:
    body = admin_client.get("/api/system/settings").json()
    data = body.get("data", body)
    assert data["limits_version"] == "limits_v2_paper"
    assert data["limits"]["version"] == "limits_v2_paper"
    risk = admin_client.get("/api/trading/risk").json()
    assert risk.get("data", risk)["limits_version"] == "limits_v2_paper"


def test_trade_rows_drop_caveats_their_session_contradicts(api_env, tmp_path) -> None:
    root = tmp_path / "paper"
    shutil.copytree(FIXTURE, root)
    settings = load_settings({**api_env, "FIBOKI_PAPER_ROOT": str(root)})
    app = create_app(settings, configure_logs=False)
    with TestClient(app, base_url=ORIGIN) as client:
        platform = app.state.platform
        rows = client.get("/api/trading/trades", params={"limit": 500}).json()["items"]
        assert rows, "the fixture journal has trades"
        checked = 0
        for row in rows:
            charged = platform.session_charged_costs(
                next(t.session_id for t in platform.trades(limit=10_000)[0]
                     if t.trade_id == row["trade_id"])
            )
            codes = {c["code"] for c in row["net_pnl"]["caveats"]}
            if "slippage" in charged:
                assert "slippage_not_modelled" not in codes
                checked += 1
            if "financing" in charged:
                assert "financing_not_modelled" not in codes
        assert checked, "at least one fixture session proves slippage was charged"
