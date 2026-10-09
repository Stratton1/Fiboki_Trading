"""The paper-forward composition root refuses everything it cannot vouch for.

The end-to-end path is in ``tests/integration/test_paper_forward_e2e.py``. This
file pins the refusals: the wiring is PAPER only, the market-data host is the
OANDA PRACTICE host by parsed hostname, the strategy is the one whose content
hash was reviewed, no FX conversion is guessed, the calendar must cover the
days ahead, the paper venue never sizes, and the CLI keeps refusing to build a
live worker from flags while offering a reviewed paper entrypoint.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from fiboki.broker.oanda import RecordedTransport
from fiboki.cli import EXIT_FAIL, EXIT_MISUSE, EXIT_OK, main
from fiboki.entrypoints.paper_forward import (
    _REPO_STRATEGIES,
    DEFAULT_WIRING,
    WiringError,
    compose,
    load_strategy,
    load_wiring,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
ENV = {"FIBOKI_OANDA_PRACTICE_TOKEN": "t", "FIBOKI_OANDA_PRACTICE_ACCOUNT_ID": "101-004-1-001"}
#: The pre-round-4 names, accepted for one release with a warning.
LEGACY_ENV = {"OANDA_PRACTICE_TOKEN": "t", "OANDA_PRACTICE_ACCOUNT_ID": "101-004-1-001"}


def _committed() -> dict[str, Any]:
    return json.loads(DEFAULT_WIRING.read_text(encoding="utf-8"))


def _write(tmp: Path, body: dict[str, Any], name: str = "w.json") -> Path:
    path = tmp / name
    path.write_text(json.dumps(body))
    return path


# ------------------------------------------------------------- the wiring


def test_the_committed_wiring_loads_and_its_strategy_hash_is_current() -> None:
    wired = load_wiring(DEFAULT_WIRING)
    assert wired.allowed_modes == ("paper",)
    assert wired.market_data_base_url == "https://api-fxpractice.oanda.com"
    assert len(wired.sha256) == 64
    (entry,) = wired.strategies
    # Fails the day somebody edits the seed without re-reviewing the wiring.
    assert load_strategy(entry, _REPO_STRATEGIES).content_hash() == entry.content_hash


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"allowed_modes": ["paper", "demo"]}, "PAPER only"),
        ({"mode": "live"}, "PAPER only"),
        ({"venue": {"kind": "paper_forward",
                    "market_data_base_url": "https://api-fxtrade.oanda.com"}}, "LIVE host"),
        ({"venue": {"kind": "paper_forward",
                    "market_data_base_url": "https://api-fxpractice.oanda.com.evil.io"}},
         "only https://api-fxpractice"),
        ({"venue": {"kind": "paper_forward",
                    "market_data_base_url": "http://api-fxpractice.oanda.com"}},
         "only https://api-fxpractice"),
        ({"venue": {"kind": "oanda", "market_data_base_url": "https://api-fxpractice.oanda.com"}},
         "venue.kind"),
        ({"surprise": 1}, "unknown key"),
        ({"schema": "something/2"}, "schema"),
        ({"ledger_dir": "../../etc"}, "relative path"),
    ],
)
def test_a_wiring_that_asks_for_more_is_refused(tmp_path: Path, patch: dict, match: str) -> None:
    body = {**_committed(), **patch}
    with pytest.raises(WiringError, match=match):
        load_wiring(_write(tmp_path, body))


def test_two_strategies_are_refused_in_v1(tmp_path: Path) -> None:
    body = _committed()
    body["strategies"] = body["strategies"] * 2
    with pytest.raises(WiringError, match="exactly ONE"):
        load_wiring(_write(tmp_path, body))


def test_the_hash_is_of_the_exact_bytes(tmp_path: Path) -> None:
    a = load_wiring(DEFAULT_WIRING)
    b = load_wiring(_write(tmp_path, _committed()))
    assert a.sha256 != b.sha256, "re-serialising the same content is a different file"


def test_a_strategy_edited_after_review_is_refused(tmp_path: Path) -> None:
    body = json.loads((_REPO_STRATEGIES / "donchian_breakout_atr.json").read_text())
    body["parameters"]["channel_period"]["default"] = 25
    (tmp_path / "donchian_breakout_atr.json").write_text(json.dumps(body))
    wired = load_wiring(DEFAULT_WIRING)
    with pytest.raises(WiringError, match="hashes to"):
        load_strategy(wired.strategies[0], tmp_path)


def _settings(tmp: Path) -> Any:
    from fiboki.api.settings import load_settings

    return load_settings({"FIBOKI_STATE_DIR": str(tmp / "var")})


def test_a_quote_currency_other_than_the_account_is_refused(tmp_path: Path) -> None:
    from fiboki.workers.base import WorkerStore

    body = _committed()
    body["instruments"] = ["EURUSD", "USDJPY"]
    with WorkerStore.sqlite_at(tmp_path / "s.db") as store, pytest.raises(
        WiringError, match="converts nothing"
    ):
        compose(_settings(tmp_path), _write(tmp_path, body), store=store, environ=ENV,
                transport=RecordedTransport({}))


def test_a_calendar_that_ends_too_soon_is_refused(tmp_path: Path) -> None:
    from fiboki.workers.base import WorkerStore

    near_the_end = pd.Timestamp("2026-11-25T10:00:00Z")  # official calendar ends 2026-12-04
    with WorkerStore.sqlite_at(tmp_path / "s.db") as store, pytest.raises(
        WiringError, match="cannot vouch"
    ):
        compose(_settings(tmp_path), DEFAULT_WIRING, store=store, environ=ENV,
                transport=RecordedTransport({}), clock=lambda: near_the_end)


def test_compose_builds_a_paper_only_worker(tmp_path: Path) -> None:
    from fiboki.workers.base import WorkerStore
    from fiboki.workers.live_worker import LiveWorker

    with WorkerStore.sqlite_at(tmp_path / "s.db") as store:
        worker = compose(
            _settings(tmp_path), DEFAULT_WIRING, store=store, environ=ENV,
            transport=RecordedTransport({}),
            clock=lambda: pd.Timestamp("2026-10-21T10:00:00Z"),
        )
        assert isinstance(worker, LiveWorker)
        assert worker.lconfig.allowed_modes == ("paper",)
        assert worker.execution.mode.value == "paper"
        worker.setup()  # the worker's own mode gate passes, and only for paper
        runtime = worker.runtime
        assert runtime.gateway.kill_switch.durable
        assert Path(runtime.kill_switch.journal.path) == Path(_settings(tmp_path).killswitch_path)
        assert runtime.builder.spread_source is runtime.spread
        # Round 4: the gateway declares PAPER and carries the explicit, recorded
        # missing-input permission; the allocator sees the gateway's own book,
        # lifecycle view and regime source; open risk is priced by the rule new
        # plans are sized under.
        assert runtime.gateway.mode.value == "paper"
        assert runtime.gateway.paper_allows_missing_inputs is True
        evaluator, builder = runtime.evaluator, runtime.builder
        assert evaluator.snapshot == builder.snapshot
        assert evaluator.strategy == builder.strategy_view
        assert evaluator.regime is builder.regime_source
        assert builder.sizing_policy is evaluator.policy


def test_the_data_stale_threshold_is_settings_health(tmp_path: Path) -> None:
    """One number: LiveWorkerConfig's default IS the shared HealthThresholds
    default, and the composition passes the deployment's Settings.health."""
    from fiboki.api.settings import load_settings
    from fiboki.obs.health import DEFAULT_HEALTH_THRESHOLDS
    from fiboki.workers.base import WorkerStore
    from fiboki.workers.live_worker import LiveWorkerConfig

    assert LiveWorkerConfig().data_stale_after_seconds == (
        DEFAULT_HEALTH_THRESHOLDS.data_stale_after_seconds
    )
    settings = load_settings({"FIBOKI_STATE_DIR": str(tmp_path / "var"),
                              "FIBOKI_DATA_STALE_SECONDS": "1234"})
    with WorkerStore.sqlite_at(tmp_path / "s.db") as store:
        worker = compose(settings, DEFAULT_WIRING, store=store, environ=ENV,
                         transport=RecordedTransport({}),
                         clock=lambda: pd.Timestamp("2026-10-21T10:00:00Z"))
    assert worker.lconfig.data_stale_after_seconds == 1234.0


def test_legacy_credential_names_are_a_warned_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from fiboki.entrypoints.paper_forward import (
        ENV_ACCOUNT,
        ENV_TOKEN,
        _credential_env,
    )

    assert (ENV_TOKEN, ENV_ACCOUNT) == (
        "FIBOKI_OANDA_PRACTICE_TOKEN", "FIBOKI_OANDA_PRACTICE_ACCOUNT_ID"
    )
    from fiboki.api.settings import KNOWN_ENV_NAMES

    assert {ENV_TOKEN, ENV_ACCOUNT} <= KNOWN_ENV_NAMES

    with caplog.at_level("WARNING"):
        resolved = _credential_env({**LEGACY_ENV, "OANDA_PRACTICE_TOKEN": "legacy-secret"})
    assert resolved[ENV_TOKEN] == "legacy-secret"
    assert resolved[ENV_ACCOUNT] == "101-004-1-001"
    text = " ".join(f"{r.getMessage()} {getattr(r, 'deprecated', '')}" for r in caplog.records)
    assert "deprecated credential name" in text and "OANDA_PRACTICE_TOKEN" in text
    assert "legacy-secret" not in text, "a credential value is never logged"

    caplog.clear()
    with caplog.at_level("WARNING"):
        resolved = _credential_env({**ENV, "OANDA_PRACTICE_TOKEN": "old"})
    assert resolved[ENV_TOKEN] == "t", "the FIBOKI_* name wins"
    assert not [r for r in caplog.records if "deprecated credential" in r.getMessage()]

    from fiboki.workers.base import WorkerStore

    with WorkerStore.sqlite_at(tmp_path / "s.db") as store:
        compose(_settings(tmp_path), DEFAULT_WIRING, store=store, environ=LEGACY_ENV,
                transport=RecordedTransport({}),
                clock=lambda: pd.Timestamp("2026-10-21T10:00:00Z"))


# ------------------------------------------------------------- structure


SIZERS = {"size_trade", "size_for", "PortfolioSizer", "FixedFractionalSizer"}


def test_the_paper_venue_never_sizes() -> None:
    tree = ast.parse((SRC / "entrypoints" / "paper_venue.py").read_text(encoding="utf-8"))
    names = {
        (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert not names & SIZERS


def test_nothing_but_the_cli_imports_the_entrypoints() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("entrypoints/") or rel == "cli.py" or "__pycache__" in rel:
            continue
        if "fiboki.entrypoints" in path.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert not offenders


# ------------------------------------------------------------------ CLI


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FIBOKI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FIBOKI_STATE_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("FIBOKI_EXECUTION_MODE", "paper")
    monkeypatch.delenv("FIBOKI_STATE_DB", raising=False)
    for name in ("OANDA_PRACTICE_TOKEN", "OANDA_PRACTICE_ACCOUNT_ID",
                 "FIBOKI_OANDA_PRACTICE_TOKEN", "FIBOKI_OANDA_PRACTICE_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_cli_paper_forward_check_validates_and_starts_nothing(cli_env: Path) -> None:
    assert main(["paper", "forward", "--wiring", str(DEFAULT_WIRING), "--check"]) == EXIT_OK


def test_cli_paper_forward_refuses_a_live_host_wiring(cli_env: Path) -> None:
    body = {**_committed(), "venue": {"kind": "paper_forward",
                                      "market_data_base_url": "https://api-fxtrade.oanda.com"}}
    path = _write(cli_env, body)
    assert main(["paper", "forward", "--wiring", str(path), "--check"]) == EXIT_MISUSE


def test_cli_paper_forward_without_a_token_fails_before_touching_the_network(cli_env: Path) -> None:
    assert main(["paper", "forward", "--wiring", str(DEFAULT_WIRING), "--once"]) == EXIT_FAIL


def test_cli_paper_forward_needs_a_wiring_file(cli_env: Path) -> None:
    assert main(["paper", "forward", "--wiring", str(cli_env / "nope.json")]) == EXIT_MISUSE


def test_cli_worker_run_live_still_refuses(cli_env: Path) -> None:
    assert main(["worker", "run", "live"]) == EXIT_MISUSE


def test_paper_forward_rides_out_outages_and_names_each_session_in_its_refs(tmp_path: Path) -> None:
    """Two defects from the first fortnight on the practice account: an offline
    laptop drove a restart loop that abandoned an open trade, and two trades in
    one journal shared the reference PFWD-00000001."""
    from fiboki.workers.base import WorkerStore

    with WorkerStore.sqlite_at(tmp_path / "s.db") as store:
        worker = compose(
            _settings(tmp_path), DEFAULT_WIRING, store=store, environ=ENV,
            transport=RecordedTransport({}),
            clock=lambda: pd.Timestamp("2026-10-21T10:00:00Z"),
        )
        assert worker.lconfig.exit_on_outage is False
        venue = worker.runtime.venue
        assert venue.ref_prefix == f"PFWD-S{worker.runtime.restarts:04d}"
        assert venue._next_ref() == f"{venue.ref_prefix}-00000001"
