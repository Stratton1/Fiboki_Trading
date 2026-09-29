"""The conviction channel: an agent verdict may only ever make a size SMALLER.

Named tests from research/reports/G_frontend_plans_audit.md §3.7 (T3 row):
``test_conviction_factor_never_exceeds_one`` (property),
``test_conviction_consumed_only_in_step_conviction`` (AST),
``test_stale_conviction_is_neutral``; and from the T1 row
``test_conviction_contract_has_no_price_size_or_free_text``,
``test_policies_default_disabled``, ``test_shadow_writes_only_shadow_provenance``.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fiboki.core.contracts import AccountState, ConvictionReading, Signal
from fiboki.core.enums import Direction, Provenance
from fiboki.core.tier import AgentInfluenceTier, TierReading
from fiboki.marketstate.events import EventVetoPolicy
from fiboki.portfolio.construction import (
    CandidateSignal,
    ConstructionConfig,
    ConvictionPolicy,
    PortfolioConstructor,
    PortfolioSnapshot,
    _conviction_factor,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "fiboki"
NOW = pd.Timestamp("2026-09-29 12:00", tz="UTC")
T = AgentInfluenceTier


def _signal(direction=Direction.LONG, instrument="EURUSD") -> Signal:
    return Signal(
        strategy_id="s1", instrument=instrument, timeframe="H4", direction=direction,
        bar_time=NOW, reference_price=1.1000,
        stop_price=1.0970 if direction is Direction.LONG else 1.1030,
    )


def _reading(stance="short", strength=2, *, as_of=None, hours=4, instrument="EURUSD"):
    start = as_of if as_of is not None else NOW - pd.Timedelta(hours=1)
    return ConvictionReading(
        instrument=instrument, stance=stance, strength=strength, as_of=start,
        valid_until=start + pd.Timedelta(hours=hours), artefact_id="cv_test",
        policy_version="thesis_debate_v1",
    )


def _allocate(reading, *, enabled=False, tier=T.T1_ANNOTATE_SHADOW, direction=Direction.LONG):
    config = ConstructionConfig(conviction=ConvictionPolicy(enabled=enabled))
    candidate = CandidateSignal(signal=_signal(direction), regime="trend", conviction=reading)
    snap = PortfolioSnapshot(
        account=AccountState(balance=100_000.0, equity=100_000.0, peak_equity=100_000.0),
        as_of=NOW, open_risk_by_instrument={},
    )
    return PortfolioConstructor(
        "equal_risk", config, agent_tier=TierReading(tier=tier, source="explicit")
    ).allocate([candidate], snap)


# ------------------------------------------------------------ the contract


def test_conviction_contract_has_no_price_size_or_free_text() -> None:
    fields = {f.name: f.type for f in dataclasses.fields(ConvictionReading)}
    assert set(fields) == {
        "instrument", "stance", "strength", "as_of", "valid_until", "artefact_id",
        "policy_version",
    }
    assert "float" not in " ".join(str(t) for t in fields.values())
    for word in ("price", "stop", "size", "text", "rationale", "reason", "probability"):
        assert not any(word in name for name in fields), word


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stance": "buy"},
        {"strength": 3},
        {"strength": True},
        {"stance": "none", "strength": 1},
        {"stance": "long", "strength": 0},
        {"instrument": "eurusd"},
        {"artefact_id": ""},
        {"policy_version": ""},
    ],
)
def test_the_contract_refuses_malformed_readings(kwargs) -> None:
    base = dict(
        instrument="EURUSD", stance="short", strength=2, as_of=NOW,
        valid_until=NOW + pd.Timedelta(hours=4), artefact_id="cv", policy_version="v",
    )
    base.update(kwargs)
    with pytest.raises(ValueError):
        ConvictionReading(**base)


def test_the_contract_refuses_naive_or_inverted_times() -> None:
    with pytest.raises(ValueError):
        _reading(as_of=pd.Timestamp("2026-09-29 12:00"))
    with pytest.raises(ValueError):
        ConvictionReading(instrument="EURUSD", stance="none", strength=0, as_of=NOW,
                          valid_until=NOW, artefact_id="cv", policy_version="v")


# -------------------------------------------------------------- the policy


def test_policies_default_disabled() -> None:
    assert ConvictionPolicy().enabled is False
    assert ConstructionConfig().conviction.enabled is False
    assert EventVetoPolicy().enabled is False


def test_max_factor_above_one_is_refused_whatever_else_is_set() -> None:
    with pytest.raises(ValueError, match="upsizing"):
        ConvictionPolicy(max_factor=1.01)
    with pytest.raises(ValueError, match="upsizing"):
        ConvictionPolicy(enabled=True, max_factor=1.5)


def test_the_floor_may_never_go_below_one_half() -> None:
    with pytest.raises(ValueError):
        ConvictionPolicy(strong_disagreement_factor=0.49)
    with pytest.raises(ValueError):
        ConvictionPolicy(strong_disagreement_factor=0.9, weak_disagreement_factor=0.8)


@pytest.mark.parametrize(
    ("reading", "direction", "factor", "state"),
    [
        (None, Direction.LONG, 1.0, "missing"),
        (_reading("short", 2), Direction.LONG, 0.75, "disagrees_strength_2"),
        (_reading("short", 1), Direction.LONG, 0.90, "disagrees_strength_1"),
        (_reading("long", 2), Direction.SHORT, 0.75, "disagrees_strength_2"),
        (_reading("long", 2), Direction.LONG, 1.0, "agrees"),
        (_reading("none", 0), Direction.LONG, 1.0, "no_view"),
        (_reading("short", 2, instrument="GBPUSD"), Direction.LONG, 1.0, "instrument_mismatch"),
        (_reading("short", 2, as_of=NOW + pd.Timedelta(minutes=1)), Direction.LONG, 1.0,
         "not_yet_available"),
        (_reading("short", 2, hours=30), Direction.LONG, 1.0, "validity_exceeds_policy"),
    ],
)
def test_the_v1_mapping(reading, direction, factor, state) -> None:
    got, got_state = _conviction_factor(ConvictionPolicy(), reading, _signal(direction), NOW)
    assert got == pytest.approx(factor) and got_state == state


def test_stale_conviction_is_neutral() -> None:
    stale = _reading("short", 2, as_of=NOW - pd.Timedelta(hours=5), hours=4)
    assert _conviction_factor(ConvictionPolicy(), stale, _signal(), NOW) == (1.0, "stale")
    at_expiry = _reading("short", 2, as_of=NOW - pd.Timedelta(hours=4), hours=4)
    assert _conviction_factor(ConvictionPolicy(), at_expiry, _signal(), NOW)[1] == "stale"


_READINGS = st.builds(
    lambda stance, strength, lag, hours, inst: ConvictionReading(
        instrument=inst, stance=stance,
        strength=0 if stance == "none" else strength,
        as_of=NOW - pd.Timedelta(minutes=lag),
        valid_until=NOW - pd.Timedelta(minutes=lag) + pd.Timedelta(hours=hours),
        artefact_id="cv", policy_version="v",
    ),
    st.sampled_from(["long", "short", "none"]), st.sampled_from([1, 2]),
    st.integers(-120, 3000), st.integers(1, 48), st.sampled_from(["EURUSD", "GBPUSD"]),
)
_POLICIES = st.builds(
    lambda strong, gap, cap: ConvictionPolicy(
        strong_disagreement_factor=strong,
        weak_disagreement_factor=min(1.0, strong + gap),
        max_factor=max(min(1.0, strong + gap), cap),
    ),
    st.floats(0.5, 1.0), st.floats(0.0, 0.5), st.floats(0.5, 1.0),
)


@settings(max_examples=400, deadline=None)
@given(st.one_of(st.none(), _READINGS), _POLICIES,
       st.sampled_from([Direction.LONG, Direction.SHORT]))
def test_conviction_factor_never_exceeds_one(reading, policy, direction) -> None:
    factor, state = _conviction_factor(policy, reading, _signal(direction), NOW)
    assert policy.floor <= factor <= 1.0
    if not state.startswith("disagrees"):
        assert factor == min(1.0, policy.max_factor) or factor == 1.0


# ------------------------------------------------------------- the gating


def test_disabled_policy_applies_nothing_but_logs_the_would_be_factor() -> None:
    result = _allocate(_reading("short", 2), enabled=False, tier=T.T3_DAMPEN_SIZE)
    a = result.allocations[0]
    step = next(r for r in a.reasons if r.step == "conviction")
    assert step.factor == 1.0 and "would_be=0.7500" in step.detail and "SHADOW" in step.detail
    assert a.weight == pytest.approx(1.0)
    (row,) = result.shadow
    assert row.would_be_factor == 0.75 and row.applied_factor == 1.0 and not row.effective


def test_enabled_policy_below_t3_still_runs_in_shadow() -> None:
    for tier in (T.T0_OBSERVE, T.T1_ANNOTATE_SHADOW, T.T2_VETO_ENTRIES):
        result = _allocate(_reading("short", 2), enabled=True, tier=tier)
        assert result.allocations[0].weight == pytest.approx(1.0), tier
        assert result.stamp["conviction_gated_by_tier"] is True
        assert result.stamp["conviction_effective"] is False


def test_enabled_policy_at_t3_dampens_and_never_below_the_floor() -> None:
    result = _allocate(_reading("short", 2), enabled=True, tier=T.T3_DAMPEN_SIZE)
    a = result.allocations[0]
    assert a.weight == pytest.approx(0.75)
    assert a.risk_pct == pytest.approx(0.25 * 0.75)
    assert result.stamp["conviction_effective"] is True
    agree = _allocate(_reading("long", 2), enabled=True, tier=T.T4_AUTHOR_CANDIDATES)
    assert agree.allocations[0].weight == pytest.approx(1.0)


def test_shadow_writes_only_shadow_provenance() -> None:
    for enabled, tier in ((False, T.T1_ANNOTATE_SHADOW), (True, T.T3_DAMPEN_SIZE)):
        for row in _allocate(_reading(), enabled=enabled, tier=tier).shadow:
            assert row.provenance is Provenance.SHADOW
            assert row.as_row()["provenance"] == "shadow"


def test_a_missing_reading_is_logged_as_missing_with_factor_one() -> None:
    (row,) = _allocate(None, enabled=True, tier=T.T3_DAMPEN_SIZE).shadow
    assert row.state == "missing" and row.would_be_factor == 1.0 == row.applied_factor


# ------------------------------------------------------ structural (AST)


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    out: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[str]:
    names: list[str] = []
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
    return names


def _all_src() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_conviction_reading_is_constructed_only_in_the_runtime_adapter() -> None:
    sites = []
    for path in _all_src():
        tree = _tree(path)
        parents = _parents(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name == "ConvictionReading":
                    sites.append((path.relative_to(SRC).as_posix(), _enclosing(node, parents)))
    assert sites == [("workers/runtime.py", ["reading", "ConvictionAdapter"])], sites


def test_only_three_modules_mention_the_contract() -> None:
    mentions = set()
    for path in _all_src():
        tree = _tree(path)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and node.id == "ConvictionReading") or (
                isinstance(node, ast.alias) and node.name == "ConvictionReading"
            ):
                mentions.add(path.relative_to(SRC).as_posix())
    assert mentions <= {
        "core/contracts.py", "portfolio/construction.py", "workers/runtime.py"
    }, mentions


def test_conviction_consumed_only_in_step_conviction() -> None:
    """The reading's fields are read only by the policy helper, which only
    ``_step_conviction`` calls, and a candidate's reading is read only there."""
    path = SRC / "portfolio" / "construction.py"
    tree = _tree(path)
    parents = _parents(tree)
    field_reads, helper_calls, candidate_reads = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            where = _enclosing(node, parents)
            # A ShadowFactor reading its OWN fields (``self.artefact_id``) is not
            # a read of the conviction reading.
            is_self = isinstance(node.value, ast.Name) and node.value.id == "self"
            if node.attr in ("stance", "strength", "valid_until", "artefact_id") and not is_self:
                field_reads.append(where[0] if where else "<module>")
            if node.attr == "conviction" and isinstance(node.value, ast.Name) and (
                node.value.id not in ("cfg", "config", "self")
            ):
                candidate_reads.append(where[0] if where else "<module>")
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_conviction_factor":
            helper_calls.append(_enclosing(node, parents)[0])
    assert set(field_reads) <= {"_conviction_factor", "_step_conviction"}, field_reads
    assert helper_calls == ["_step_conviction"], helper_calls
    assert candidate_reads == ["_step_conviction"], candidate_reads


_EXECUTION_SIDE = ("risk", "broker", "strategy", "backtest", "sim", "indicators")


def test_conviction_never_reaches_sizing_the_gateway_or_execution() -> None:
    offenders = []
    targets = [p for pkg in _EXECUTION_SIDE for p in (SRC / pkg).rglob("*.py")]
    targets.append(SRC / "portfolio" / "sizing.py")
    for path in targets:
        tree = _tree(path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.alias):
                names.append(node.name)
            elif isinstance(node, ast.arg | ast.keyword) and node.arg:
                names.append(node.arg)
            if any("conviction" in n.lower() for n in names):
                offenders.append(path.relative_to(SRC).as_posix())
    assert offenders == [], sorted(set(offenders))


def test_nothing_on_the_sizing_path_rewrites_signal_confidence() -> None:
    for rel in ("portfolio/construction.py", "workers/runtime.py"):
        tree = _tree(SRC / rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword):
                assert node.arg != "confidence", rel
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                assert node.attr != "confidence", rel


def test_the_agent_package_never_sees_the_contract_or_the_policy() -> None:
    """Agents write rows; they never hold the contract, the policy or portfolio."""
    for path in (SRC / "agents").rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Name | ast.alias):
                name = node.id if isinstance(node, ast.Name) else node.name
                assert name not in ("ConvictionReading", "ConvictionPolicy"), path
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("fiboki.portfolio"), path
