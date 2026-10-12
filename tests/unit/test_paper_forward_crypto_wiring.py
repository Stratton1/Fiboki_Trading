"""The 24/7 crypto paper wiring, its refusals, and its desktop service.

The end-to-end path is ``tests/integration/test_paper_forward_crypto_e2e.py``.
Here: the committed wiring loads and names the reviewed document; a wiring that
crosses venues, hosts or cost models is refused; the service case and the
launchd template run it in paper only, beside the FX paper service and apart
from it.
"""
from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from fiboki.entrypoints.paper_forward import (
    _REPO_STRATEGIES,
    CRYPTO_WIRING,
    DEFAULT_WIRING,
    WiringError,
    compose,
    load_strategy,
    load_wiring,
)

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
LAUNCHD = REPO / "deploy" / "launchd"


class _NoNetwork:
    def request(self, *a: Any, **k: Any) -> Any:  # pragma: no cover - must not be called
        raise AssertionError("composition must not touch the network")


def _committed() -> dict[str, Any]:
    return json.loads(CRYPTO_WIRING.read_text(encoding="utf-8"))


def _write(tmp: Path, body: dict[str, Any]) -> Path:
    path = tmp / "w.json"
    path.write_text(json.dumps(body))
    return path


def _settings(tmp: Path) -> Any:
    from fiboki.api.settings import load_settings

    return load_settings({"FIBOKI_STATE_DIR": str(tmp / "var")})


def test_the_committed_crypto_wiring_loads_and_names_the_reviewed_document() -> None:
    wired = load_wiring(CRYPTO_WIRING)
    assert wired.allowed_modes == ("paper",)
    assert wired.market_data == "coinbase_public"
    assert wired.market_data_base_url == "https://api.exchange.coinbase.com"
    assert wired.instruments == ("BTCGBP", "ETHGBP")
    assert wired.timeframe.value == "H4"
    assert wired.broker_profile == "COINBASE_UK_SPOT" and wired.account_ccy == "GBP"
    (entry,) = wired.strategies
    assert load_strategy(entry, _REPO_STRATEGIES).content_hash() == entry.content_hash
    fx = load_wiring(DEFAULT_WIRING)
    assert fx.market_data == "oanda_practice", "the FX wiring is unchanged by default"
    assert wired.ledger_dir != fx.ledger_dir


@pytest.mark.parametrize(
    ("venue", "match"),
    [
        ({"market_data": "coinbase_public",
          "market_data_base_url": "https://api-fxpractice.oanda.com"}, "only https://api.exchange"),
        ({"market_data": "coinbase_public",
          "market_data_base_url": "https://api.exchange.coinbase.com.evil.io"},
         "only https://api.exchange"),
        ({"market_data": "binance", "market_data_base_url": "https://api.binance.com"},
         "not one of"),
        ({"market_data": "oanda_practice",
          "market_data_base_url": "https://api.exchange.coinbase.com"}, "only https://api-fxpractice"),
    ],
)
def test_a_wiring_that_crosses_hosts_is_refused(tmp_path: Path, venue: dict, match: str) -> None:
    body = {**_committed(), "venue": {"kind": "paper_forward", **venue}}
    with pytest.raises(WiringError, match=match):
        load_wiring(_write(tmp_path, body))


def test_a_cfd_profile_on_crypto_is_refused_at_composition(tmp_path: Path) -> None:
    from fiboki.workers.base import WorkerStore

    body = {**_committed(), "broker_profile": "OANDA_REALISTIC"}
    with WorkerStore.sqlite_at(tmp_path / "s.db") as store, pytest.raises(
        WiringError, match="spot crypto"
    ):
        compose(_settings(tmp_path), _write(tmp_path, body), store=store, environ={},
                transport=_NoNetwork(), clock=lambda: pd.Timestamp("2026-10-21T10:00:00Z"))


def test_a_crypto_instrument_on_oanda_market_data_is_refused(tmp_path: Path) -> None:
    from fiboki.workers.base import WorkerStore

    body = {**_committed(), "venue": {
        "kind": "paper_forward", "market_data": "oanda_practice",
        "market_data_base_url": "https://api-fxpractice.oanda.com"}}
    env = {"FIBOKI_OANDA_PRACTICE_TOKEN": "t", "FIBOKI_OANDA_PRACTICE_ACCOUNT_ID": "1"}
    with WorkerStore.sqlite_at(tmp_path / "s.db") as store, pytest.raises(
        WiringError, match="venue.market_data is oanda_practice"
    ):
        compose(_settings(tmp_path), _write(tmp_path, body), store=store, environ=env,
                transport=_NoNetwork(), clock=lambda: pd.Timestamp("2026-10-21T10:00:00Z"))


def test_the_crypto_composition_needs_no_credential_and_reads_one_public_host(
    tmp_path: Path,
) -> None:
    from fiboki.workers.base import WorkerStore
    from fiboki.workers.live_worker import LiveWorker

    with WorkerStore.sqlite_at(tmp_path / "s.db") as store:
        worker = compose(_settings(tmp_path), CRYPTO_WIRING, store=store, environ={},
                         transport=_NoNetwork(),
                         clock=lambda: pd.Timestamp("2026-10-24T10:00:00Z"))  # a Saturday
        assert isinstance(worker, LiveWorker)
        assert worker.lconfig.allowed_modes == ("paper",)
        assert worker.lconfig.lease_name == "paper-forward-crypto"
        runtime = worker.runtime
        assert runtime.builder.spread_source is runtime.spread
        assert type(runtime.spread).__name__ == "CoinbaseTickerSpreadSource"
        assert type(runtime.feed.provider).__name__ == "CoinbaseCandlesProvider"
        # 24/7: the feed's market-open source says open on a Saturday.
        assert runtime.feed.is_market_open("BTCGBP", pd.Timestamp("2026-10-24T03:00:00Z"))
        assert runtime.evaluator.policy.resolved_cost_profile.name == "COINBASE_UK_SPOT"
        assert "paper_forward_crypto_v1" in str(runtime.journal.ledger_dir)


def test_the_default_transport_allows_only_the_public_exchange_host() -> None:
    text = (REPO / "src" / "fiboki" / "entrypoints" / "paper_forward.py").read_text()
    assert "COINBASE_EXCHANGE_HOST if crypto else OANDA_PRACTICE_HOST" in text


# ------------------------------------------------------------------- deploy

pytestmark_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_the_crypto_plist_is_a_standard_paper_process() -> None:
    plist = plistlib.loads((LAUNCHD / "uk.fiboki.paper-crypto.plist").read_bytes())
    assert plist["Label"] == "uk.fiboki.paper-crypto"
    assert plist["ProgramArguments"] == [
        "/bin/bash", "__FIBOKI_ROOT__/scripts/fiboki-service.sh", "paper-crypto"]
    assert plist["ProcessType"] == "Standard"
    assert plist["EnvironmentVariables"] == {"FIBOKI_EXECUTION_MODE": "paper"}
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ExitTimeOut"] > 30 and plist["ThrottleInterval"] >= 30
    assert plist["StandardOutPath"] == "__FIBOKI_ROOT__/var/logs/paper-crypto.log"
    body = (LAUNCHD / "uk.fiboki.paper-crypto.plist").read_text().split("-->", 1)[1]
    assert "TOKEN" not in body and "LIVE" not in body


def test_the_service_case_runs_the_crypto_wiring_with_a_sleep_assertion() -> None:
    text = (SCRIPTS / "fiboki-service.sh").read_text()
    case = text[text.index("  paper-crypto)"):text.index("  *)")]
    assert '--wiring "$ROOT/src/fiboki/entrypoints/wiring/paper_forward_crypto_v1.json"' in case
    assert "caffeinate -is -w $$" in case
    assert case.index("caffeinate -is") < case.index('exec "$ROOT/.venv/bin/fiboki"')
    assert text.index('done < "$ENV_FILE"') < text.index("export FIBOKI_EXECUTION_MODE=paper")
    assert text.index("export FIBOKI_EXECUTION_MODE=paper") < text.index("  paper-crypto)")


@pytestmark_bash
def test_the_installer_writes_the_crypto_service_when_asked(tmp_path: Path) -> None:
    import os

    target = tmp_path / "LaunchAgents"
    env = {k: v for k, v in os.environ.items() if not k.startswith("FIBOKI_")}
    proc = subprocess.run(
        ["bash", str(SCRIPTS / "launchd-install.sh"), "--services", "paper-crypto",
         "--target", str(target)], cwd=REPO, env=env, capture_output=True, text=True,
        timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    plist = plistlib.loads((target / "uk.fiboki.paper-crypto.plist").read_bytes())
    assert plist["ProgramArguments"] == [
        "/bin/bash", f"{REPO}/scripts/fiboki-service.sh", "paper-crypto"]
