"""Smoke tests for ``research/run_discovery_campaign.py`` under engine_v3_realism.

The script is the K3 entry point, so what it records about its own
configuration is part of the result: a campaign report whose run.log does not
say which engine, which account currency, which FX conversion and how much
calendar coverage produced it cannot be told apart from a stale one.

The data root is synthetic (``tests.data_fixtures.make_bars``, validated
through the real integrity check and written through the real ``DataStore``).
EURUSD H4 runs from 2023-07-03 into March 2024, so it straddles the official
calendar's declared start of 2024-01-01; GBPUSD D1 supplies USD->GBP.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from fiboki.backtest.version import ENGINE_VERSION
from fiboki.core.enums import Timeframe
from fiboki.data.integrity import IntegrityConfig, validate
from fiboki.data.schema import DatasetKind, describe_frame
from fiboki.data.store import DataStore
from fiboki.data.versioning import TransformationStep
from fiboki.marketstate.calendar import load_official_calendar
from tests.data_fixtures import make_bars, make_metadata

SCRIPT = Path(__file__).resolve().parents[2] / "research" / "run_discovery_campaign.py"
CALENDAR_START = pd.Timestamp("2024-01-01", tz="UTC")


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_run_discovery_campaign_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(store: DataStore, instrument: str, timeframe: Timeframe, periods: int, start: str) -> str:
    frame = make_bars(instrument=instrument, timeframe=timeframe, periods=periods, start=start)
    frame = frame[frame.index.dayofweek < 5]  # no weekend bars: keeps the integrity verdict VALIDATED
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
    return stored.version_id


def _data_root(tmp_path: Path, *, with_gbp_cross: bool = True, h4_start: str = "2023-07-03 00:00") -> Path:
    root = tmp_path / "datastore"
    with DataStore.initialise(root) as store:
        _write(store, "EURUSD", Timeframe.H4, 1500, h4_start)
        if with_gbp_cross:
            _write(store, "GBPUSD", Timeframe.D1, 1400, "2023-05-01 00:00")
    return root


def _argv(root: Path, out: Path, tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--data-root", str(root),
        "--out", str(out),
        "--cache", str(tmp_path / "cache"),
        "--campaign-id", "k3_smoke",
        "--instruments", "EURUSD",
        "--timeframes", "H4",
        "--max-evaluations", "2",
        *extra,
    ]


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script()


def test_main_writes_the_effective_configuration_the_gbp_fx_label_and_a_report(
    script: ModuleType, tmp_path: Path
) -> None:
    root = _data_root(tmp_path)
    out = tmp_path / "out"

    assert script.main(_argv(root, out, tmp_path, "--engine-version-check")) == 0

    lines = (out / "run.log").read_text(encoding="utf-8").splitlines()
    header = lines[: lines.index("====")]
    assert header[0].startswith("==== run_discovery_campaign ")
    fields = dict(line.split(": ", 1) for line in header[1:])
    assert fields["campaign_id"] == "k3_smoke"
    assert fields["engine_version"] == f"{ENGINE_VERSION} (required engine_v3_realism: ok)"
    assert fields["account_ccy"] == "GBP"
    assert fields["fx_label"].startswith("SeriesFxSource(GBP; daily closes as-of bar close;")
    assert "GBPUSD@ds_" in fields["fx_label"]
    assert "policy enforce-where-covered; allow_empty_calendar=True" in fields["calendar"]
    assert fields["calendar_coverage"].startswith("1 series, share of bars inside the declared span")
    assert fields["construction_policy"].startswith("construction_v2 (run_validation default 'research_default'")
    assert fields["gate_set"] == "v2.0.0-audit (min_trades=400)"

    report_path = out / "campaign_k3_smoke.json"
    assert report_path.exists()
    assert (out / "campaign_k3_smoke.md").exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    spec = report["spec"]
    assert spec["account_ccy"] == "GBP"
    assert spec["fx_label"] == fields["fx_label"]
    assert spec["allow_empty_calendar"] is True
    assert f"engine {ENGINE_VERSION}" in report["notes"]
    assert "enforce-where-covered" in report["notes"]
    assert "blackouts were enforced on bars inside the declared span" in report["notes"]

    # The covered fraction is the share of bars at or after the declared start,
    # recomputed here from the stored bars rather than trusted.
    with DataStore(root) as store:
        frame, _ = store.read_latest("EURUSD", Timeframe.H4, kind=DatasetKind.VALIDATED)
    declared_start = load_official_calendar().coverage().declared_start
    assert declared_start == CALENDAR_START
    expected = float((frame.index >= declared_start).mean())
    assert 0.0 < expected < 1.0
    coverage = json.loads((out / "calendar_coverage.json").read_text(encoding="utf-8"))
    row = coverage["series"]["EURUSD H4"]
    assert row["time_covered_fraction"] == pytest.approx(expected, abs=1e-6)
    assert row["currencies_not_carried"] == []
    assert f"EURUSD H4 {100 * expected:.1f}% of {len(frame):,} bars" in report["notes"]

    fx_coverage = json.loads((out / "fx_coverage.json").read_text(encoding="utf-8"))
    assert fx_coverage["account_ccy"] == "GBP"
    assert list(fx_coverage["pairs_loaded"]) == ["GBPUSD"]
    assert fx_coverage["instruments_without_usable_coverage"] == []


def test_a_stale_engine_is_refused_before_anything_is_written(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _data_root(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(script, "ENGINE_VERSION", "engine_v2_exit_vocabulary")

    assert script.main(_argv(root, out, tmp_path, "--engine-version-check")) == script.EXIT_ENGINE_VERSION
    assert not out.exists()


def test_a_missing_gbp_cross_refuses_and_names_the_pair(script: ModuleType, tmp_path: Path) -> None:
    root = _data_root(tmp_path, with_gbp_cross=False)
    out = tmp_path / "out"

    assert script.main(_argv(root, out, tmp_path)) == script.EXIT_FX_REFUSED
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "fx_label: REFUSED: research FX conversion cannot be built: ingest GBPUSD" in log
    assert "REFUSED (fx)" in log
    assert not (out / "campaign_k3_smoke.json").exists()


def test_calendar_none_says_blackouts_were_not_enforced(script: ModuleType, tmp_path: Path) -> None:
    root = _data_root(tmp_path)
    out = tmp_path / "out"

    assert script.main(_argv(root, out, tmp_path, "--calendar", "none", "--generations", "0")) == 0
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "calendar: none (--calendar none): event blackouts NOT enforced" in log
    report = json.loads((out / "campaign_k3_smoke.json").read_text(encoding="utf-8"))
    assert report["spec"]["allow_empty_calendar"] is True
    assert "Event blackouts were NOT enforced in this run" in report["notes"]


def test_bars_after_the_calendars_declared_end_are_refused(script: ModuleType, tmp_path: Path) -> None:
    """The lift covers bars BEFORE the declared start only, not an uncovered tail."""
    root = _data_root(tmp_path, h4_start="2026-11-02 00:00")
    out = tmp_path / "out"

    assert script.main(_argv(root, out, tmp_path)) == script.EXIT_CALENDAR_REFUSED
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "REFUSED (calendar): EURUSD H4:" in log
    assert "after the calendar's declared end" in log


def test_the_legacy_usd_account_is_still_available(script: ModuleType, tmp_path: Path) -> None:
    root = _data_root(tmp_path, with_gbp_cross=False)
    out = tmp_path / "out"

    assert script.main(_argv(root, out, tmp_path, "--account-ccy", "USD", "--plan-only", "--generations", "0")) == 0
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "account_ccy: USD" in log
    assert "fx_label: none needed: every instrument is quoted in USD" in log
    assert "engine_version: " + ENGINE_VERSION + " (not checked" in log


def test_a_cell_straddling_the_declared_start_runs_through_the_real_engine(
    script: ModuleType, tmp_path: Path
) -> None:
    """Enforce where covered: the cell is validated, not refused by the coverage check.

    With the strict policy (``allow_empty_calendar=False``) ``run_validation``
    refuses these bars because they start before 2024-01-01; under the K3
    policy the official calendar is still the blackout source and the cell
    reaches the ladder. One seed cell, eight trials, no mutations.
    """
    root = _data_root(tmp_path)
    out = tmp_path / "out"

    argv = _argv(root, out, tmp_path, "--generations", "0")
    argv[argv.index("--max-evaluations") + 1] = "8"
    assert script.main(argv) == 0

    report = json.loads((out / "campaign_k3_smoke.json").read_text(encoding="utf-8"))
    assert len(report["attempted"]) == 1
    cell = report["attempted"][0]
    assert cell["strategy_id"] == "donchian_breakout_atr"
    assert cell["engine_runs"] >= 1
    assert cell["died_at_rung"] == "RUNG 0 SANITY"
    assert "CalendarError" not in json.dumps(cell)


def test_plan_only_succeeds_on_h4_only_gbp_crosses_that_start_late(
    script: ModuleType, tmp_path: Path
) -> None:
    """The K3 data shape: no D1 anywhere, and the GBP cross starts after the instrument.

    EURJPY H4 from 2023-07-03; GBPJPY H4 only from 2023-09-04; USDJPY and
    GBPUSD H4 from 2023-06-05. JPY->GBP comes from GBPJPY's daily closes where
    they exist and via USD before that, so every EURJPY bar has a rate.
    """
    root = tmp_path / "datastore"
    with DataStore.initialise(root) as store:
        _write(store, "EURJPY", Timeframe.H4, 1500, "2023-07-03 00:00")
        _write(store, "GBPJPY", Timeframe.H4, 1200, "2023-09-04 00:00")
        _write(store, "USDJPY", Timeframe.H4, 1700, "2023-06-05 00:00")
        _write(store, "GBPUSD", Timeframe.H4, 1700, "2023-06-05 00:00")
    out = tmp_path / "out"
    argv = [
        "--data-root", str(root),
        "--out", str(out),
        "--campaign-id", "k3_smoke_h4_fx",
        "--instruments", "EURJPY",
        "--timeframes", "H4",
        "--max-evaluations", "2",
        "--generations", "0",
        "--engine-version-check",
        "--plan-only",
    ]

    assert script.main(argv) == 0

    log = (out / "run.log").read_text(encoding="utf-8")
    label = next(line for line in log.splitlines() if line.startswith("fx_label: "))
    assert "GBPJPY@ds_" in label and "[derived:H4 last close per UTC day]" in label
    assert "fallback via_usd where the direct cross has no fresh rate for JPY" in label
    assert "REFUSED" not in log

    coverage = json.loads((out / "fx_coverage.json").read_text(encoding="utf-8"))
    assert coverage["pairs_loaded"]["GBPJPY"]["source_timeframe"] == "H4"
    assert coverage["pairs_loaded"]["GBPJPY"]["derivation"].startswith("derived from H4 closes")
    assert coverage["fallback"]["JPY"]["available"] is True
    route = coverage["routes_at_bar_open"]["EURJPY H4"]
    assert route["bars_via_usd"] > 0 and route["bars_direct"] > 0
    assert route["bars_without_rate"] == 0
    assert route["via_usd_first"].startswith("2023-07-03")
    assert route["via_usd_last"] < "2023-09-06"
    assert coverage["instruments_without_usable_coverage"] == []
    assert "fx route EURJPY H4:" in log
