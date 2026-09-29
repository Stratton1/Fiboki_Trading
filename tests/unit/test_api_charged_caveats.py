"""Realism caveats must not contradict the record they are attached to.

A persisted paper session whose ``summary.json`` shows slippage and financing
were charged must not carry "slippage not modelled" / "financing not charged"
caveats just because the API process runs with the default settings.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fiboki.api.platform import Platform
from fiboki.api.provenance import charged_cost_components, figure, realism_caveats
from fiboki.api.settings import load_settings
from fiboki.core.enums import Provenance

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "paper_journal"
DENIALS = {"slippage_not_modelled", "financing_not_modelled"}


@pytest.fixture
def defaults(tmp_path):
    return load_settings({"FIBOKI_STATE_DIR": str(tmp_path / "state")})


def _codes(caveats) -> set[str]:
    return {c.code for c in caveats}


def test_charged_components_come_from_non_zero_totals():
    assert charged_cost_components(
        {"spread_cost": 77.95, "commission": 0.0, "slippage_cost": 1.06, "financing_cost": 128.81}
    ) == {"spread", "slippage", "financing"}
    assert charged_cost_components({}) == frozenset()
    assert charged_cost_components({"slippage_cost": "nan", "financing_cost": None}) == frozenset()
    assert charged_cost_components({"slippage_cost": "bad"}) == frozenset()


def test_both_charged_removes_both_denials(defaults):
    codes = _codes(
        realism_caveats(defaults, Provenance.PAPER, charged=frozenset({"slippage", "financing"}))
    )
    assert not codes & DENIALS
    # What the record does NOT contradict stays: spreads are still static values.
    assert {"static_spread", "paper_fill_assumption"} <= codes


def test_only_what_was_charged_is_removed(defaults):
    codes = _codes(realism_caveats(defaults, Provenance.PAPER, charged=frozenset({"slippage"})))
    assert "slippage_not_modelled" not in codes
    assert "financing_not_modelled" in codes


def test_without_evidence_the_configuration_decides(defaults):
    assert _codes(realism_caveats(defaults, Provenance.PAPER)) >= DENIALS
    assert _codes(realism_caveats(defaults, Provenance.BACKTEST, charged=frozenset())) >= DENIALS


def test_figure_threads_the_evidence(defaults):
    fig = figure(
        -2676.71, Provenance.PAPER, settings=defaults, unit="USD", affects="net_pnl",
        charged=frozenset({"slippage", "financing"}),
    )
    assert not _codes(fig.caveats) & DENIALS


def test_platform_reads_the_session_summary(tmp_path, monkeypatch):
    monkeypatch.delenv("FIBOKI_PAPER_ROOT", raising=False)
    root = tmp_path / "paper"
    shutil.copytree(FIXTURE, root)
    platform = Platform(load_settings({"FIBOKI_STATE_DIR": str(tmp_path / "s")}), paper_root=root)
    charged = platform.session_charged_costs("donchian_breakout_atr")
    assert {"slippage", "financing"} <= charged
    assert platform.session_charged_costs("no_such_session") == frozenset()
    assert platform.session_summary("donchian_breakout_atr")["dataset_version_id"]
    assert platform.session_telemetry("donchian_breakout_atr") == []  # fixture has none
