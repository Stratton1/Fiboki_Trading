"""Missing loss/exposure inputs block; they no longer read as a benign zero (audit F P1-4).

Before: ``RiskContext.daily_pnl`` and friends defaulted to ``0.0`` and
``fx_quote_to_account`` to ``1.0``, so a composition that forgot the P&L ledger
passed ``daily_loss`` on every order. That is absence dressed as a value.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fiboki.core.enums import ExecutionMode, StrategyLifecycle
from fiboki.risk.gateway import InMemoryAttemptRecorder, RiskContext, RiskGateway, StrategyView
from fiboki.risk.limits import DEFAULT_LIMITS
from tests.exec_fixtures import NOW, healthy_market, healthy_venue, make_plan, make_snapshot

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"

REQUIRED = {
    "open_risk_amount",
    "correlated_exposure",
    "daily_pnl",
    "weekly_pnl",
    "fx_quote_to_account",
}


def _bare(mode: ExecutionMode, **inputs) -> RiskContext:
    lifecycle = {
        ExecutionMode.LIVE: StrategyLifecycle.LIVE,
        ExecutionMode.DEMO: StrategyLifecycle.DEMO,
    }.get(mode, StrategyLifecycle.PAPER)
    return RiskContext(
        plan=make_plan(),
        snapshot=make_snapshot(as_of=NOW),
        now=NOW,
        mode=mode,
        limits=DEFAULT_LIMITS,
        market=healthy_market(now=NOW),
        venue=healthy_venue(),
        strategy=StrategyView(lifecycle=lifecycle),
        **inputs,
    )


def test_the_optional_inputs_default_to_none_not_zero() -> None:
    ctx = _bare(ExecutionMode.PAPER)
    for name in REQUIRED:
        assert getattr(ctx, name) is None, name
    assert {name for name, _ in RiskGateway.INPUT_FIELDS} == REQUIRED


@pytest.mark.parametrize("mode", [ExecutionMode.DEMO, ExecutionMode.LIVE])
def test_missing_inputs_block_in_broker_modes_even_when_paper_is_excused(mode, tmp_path) -> None:
    from fiboki.risk.killswitch import KillSwitch

    gw = RiskGateway(
        mode=mode,
        kill_switch=KillSwitch.at_path(tmp_path / "k.jsonl"),
        paper_allows_missing_inputs=True,
    )
    decision = gw.evaluate(_bare(mode))
    assert not decision.allowed
    for reason in (
        "daily_loss_input_missing:daily_pnl",
        "weekly_loss_input_missing:weekly_pnl",
        "max_account_risk_input_missing:open_risk_amount",
        "max_correlated_exposure_input_missing:correlated_exposure,fx_quote_to_account",
        "max_instrument_exposure_input_missing:fx_quote_to_account",
    ):
        assert reason in decision.reasons, (reason, decision.reasons)


def test_paper_blocks_on_a_missing_input_by_default() -> None:
    decision = RiskGateway().evaluate(_bare(ExecutionMode.PAPER))
    assert not decision.allowed
    assert "daily_loss_input_missing:daily_pnl" in decision.reasons


def test_paper_passes_only_with_the_explicit_permission_and_records_it() -> None:
    recorder = InMemoryAttemptRecorder()
    gw = RiskGateway(recorder=recorder, paper_allows_missing_inputs=True)
    decision = gw.evaluate(_bare(ExecutionMode.PAPER))
    assert decision.allowed, decision.reasons
    row = recorder.attempts[-1].extra
    assert row["paper_allows_missing_inputs"] is True
    assert set(row["missing_inputs"]) == REQUIRED


def test_one_missing_input_blocks_only_its_own_checks() -> None:
    full = {name: 0.0 for name in REQUIRED} | {"fx_quote_to_account": 1.0}
    full.pop("weekly_pnl")
    decision = RiskGateway().evaluate(_bare(ExecutionMode.PAPER, **full))
    assert decision.blocking_reasons == ("weekly_loss_input_missing:weekly_pnl",)


def test_src_constructs_RiskContext_only_with_every_input_explicit() -> None:
    """AST: a RiskContext(...) in src/ that omits one of these is a composition
    silently relying on the default, which now blocks at runtime; fail at CI."""
    offenders: list[str] = []
    found = 0
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "RiskContext":
                continue
            found += 1
            if any(kw.arg is None for kw in node.keywords):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno} uses **kwargs")
                continue
            passed = {kw.arg for kw in node.keywords}
            missing = REQUIRED - passed
            if missing:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno} omits {sorted(missing)}")
    assert found, "no RiskContext construction found in src/; the walk has rotted"
    assert not offenders, "\n".join(offenders)
