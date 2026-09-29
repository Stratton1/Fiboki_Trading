"""The thesis debate end to end, offline, and its one bridge to the portfolio.

deterministic brief (persisted, hashed) -> long/short advocate turns (<= 2
rounds, <= 5 claims, every claim cites resolvable evidence ids and names a
falsifier) -> arbiter reads the brief AND the transcript -> an expiring
conviction row -> ``workers.runtime.conviction_rows_from_sqlite`` +
``ConvictionAdapter`` -> ``_step_conviction`` (down only, shadow unless T3).
EchoProvider plays every model.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from fiboki.agents.audit import NO_MODEL, ActionKind, JsonlAuditLedger
from fiboki.agents.capabilities import Capability, assert_no_execution_capability
from fiboki.agents.manifest import build_run_manifest
from fiboki.agents.providers import EchoProvider, ModelRouter
from fiboki.agents.roles import AgentRole, ContextBudget, all_roles, get_role
from fiboki.agents.session import BudgetExceeded, SessionBudget, open_session
from fiboki.agents.tools import (
    REGISTRY,
    THESIS_DEBATE_POLICY_VERSION,
    ThesisStore,
    ToolRegistry,
    WriteDomain,
    market_brief_hash,
)
from fiboki.agents.workflows import (
    THESIS_ARBITER_STEP,
    THESIS_BRIEF_READER,
    WORKFLOW_END,
    WORKFLOW_START,
    WorkflowDeps,
    last_bar_close,
    offline_thesis_script,
    run_thesis_debate,
    thesis_advocate_step,
    thesis_debate_due,
)
from fiboki.core.contracts import AccountState, Signal
from fiboki.core.enums import Direction, Timeframe
from fiboki.core.tier import AgentInfluenceTier, TierReading
from fiboki.portfolio.construction import (
    CandidateSignal,
    ConstructionConfig,
    ConvictionPolicy,
    PortfolioConstructor,
    PortfolioSnapshot,
)
from fiboki.workers.runtime import ConvictionAdapter, conviction_rows_from_sqlite
from tests.agents_fixtures import Harness

STEPS_2 = [
    "market_brief",
    thesis_advocate_step("long", 1), thesis_advocate_step("short", 1),
    thesis_advocate_step("long", 2), thesis_advocate_step("short", 2),
    THESIS_ARBITER_STEP,
]


def _world(tmp_path, script=None):
    harness = Harness(timeframe=Timeframe.H4)
    as_of = (harness.frame.index[-1] + pd.Timedelta(hours=4)).to_pydatetime()
    written = as_of + timedelta(seconds=30)
    store = ThesisStore(tmp_path / "agents" / "thesis.sqlite")
    ledger = JsonlAuditLedger(tmp_path / "audit.jsonl")
    context = replace(harness.context, as_of=as_of, thesis=store, clock=lambda: written)
    provider = EchoProvider(script if script is not None else offline_thesis_script())
    deps = WorkflowDeps(
        context=context, resolver=harness.resolver, ledger=ledger,
        router=ModelRouter([provider]), orchestrator=harness.orchestrator,
    )
    return deps, store, ledger, provider, as_of, written


@pytest.fixture
def debated(tmp_path):
    deps, store, ledger, provider, as_of, written = _world(tmp_path)
    result = run_thesis_debate(deps, instrument="EURUSD", timeframe="H4", workflow_id="wf_deb")
    return deps, store, ledger, provider, as_of, written, result, tmp_path


# --------------------------------------------------------------- the run


def test_the_debate_runs_end_to_end_and_files_one_expiring_verdict(debated) -> None:
    _d, store, _l, _p, as_of, written, result, _t = debated
    assert result.ok, [(s.step, s.error) for s in result.steps]
    assert [s.step for s in result.steps] == STEPS_2
    assert result.brief_id and result.debate_id == "deb_wf_deb" and result.conviction_id
    (row,) = store.convictions("EURUSD")
    assert row["stance"] == "short" and row["strength"] == 2
    assert row["policy_version"] == THESIS_DEBATE_POLICY_VERSION
    assert pd.Timestamp(row["valid_until"]) == pd.Timestamp(as_of) + pd.Timedelta(hours=4)
    assert pd.Timestamp(row["available_at"]) == pd.Timestamp(written)
    turns = store.turns("deb_wf_deb")
    assert [(t["stance"], t["round"]) for t in turns] == [
        ("long", 1), ("short", 1), ("long", 2), ("short", 2)
    ]
    assert turns[2]["payload"]["claims"][1]["rebuts"] == "short.r1.c1"


def test_the_brief_is_reproducible_from_its_persisted_record(debated) -> None:
    deps, store, _l, _p, _a, _w, result, _t = debated
    brief = store.get_brief(result.brief_id)
    assert market_brief_hash(
        instrument=brief.instrument, timeframe=brief.timeframe, as_of=brief.as_of,
        dataset_version_id=brief.dataset_version_id, evidence=brief.evidence,
    ) == brief.brief_hash
    session = open_session(
        agent_id="rebuild@x", role=AgentRole.THESIS_ARBITER, context=deps.context,
        resolver=deps.resolver, ledger=deps.ledger,
    )
    again = session.call("build_market_brief", {"instrument": "EURUSD", "timeframe": "H4"},
                         reason="rebuild")
    assert again.brief_hash == brief.brief_hash and again.brief_id == brief.brief_id
    ids = brief.evidence_ids()
    assert {"regime.axis.persistence", "bars.realised_vol_100", "data_quality.verdict",
            "calendar.coverage"} <= ids


def test_the_audit_chain_brackets_the_run_and_pins_every_filing(debated) -> None:
    _d, _s, ledger, _p, _a, _w, _r, _t = debated
    assert ledger.verify_chain()
    mine = [r for r in ledger.records() if r.workflow_id == "wf_deb"]
    assert mine[0].tool == WORKFLOW_START and mine[-1].tool == WORKFLOW_END
    brief = next(r for r in mine if r.tool == "build_market_brief")
    assert brief.agent_id == f"{THESIS_BRIEF_READER}@wf_deb" and brief.model_id == NO_MODEL
    models = [r for r in mine if r.kind is ActionKind.MODEL_CALL]
    filings = [r for r in mine if r.tool in ("record_debate_turn", "record_conviction")]
    assert len(models) == len(filings) == 5
    for model, filing in zip(models, filings, strict=True):
        assert filing.parent_action_id == model.action_id
        assert filing.model_digest and filing.model_digest == model.model_digest
    arbiter = [r for r in mine if r.role == AgentRole.THESIS_ARBITER.value
               and r.agent_id.startswith("thesis_arbiter@")]
    assert [r.tool for r in arbiter][:2] == ["get_debate", "model:echo-1"]
    advocates = {r.agent_id for r in mine if r.role == AgentRole.THESIS_ADVOCATE.value}
    assert advocates == {"thesis_advocate[long]@wf_deb", "thesis_advocate[short]@wf_deb"}


def test_the_arbiter_is_shown_the_brief_and_the_transcript(debated) -> None:
    _d, _s, _l, provider, _a, _w, _r, _t = debated
    arbiter_call = next(c for c in provider.calls if c.metadata["step"] == THESIS_ARBITER_STEP)
    assert "<<<BRIEF" in arbiter_call.prompt and "<<<DEBATE" in arbiter_call.prompt
    long_r1 = next(c for c in provider.calls
                   if c.metadata["step"] == thesis_advocate_step("long", 1))
    short_r1 = next(c for c in provider.calls
                    if c.metadata["step"] == thesis_advocate_step("short", 1))
    # Static prefix first: both advocates share everything up to the transcript.
    cut = long_r1.prompt.index("<<<TRANSCRIPT")
    assert long_r1.prompt[:cut] == short_r1.prompt[:cut]
    assert long_r1.system == short_r1.system


# ------------------------------------------------ the bridge to the portfolio


def _candidate(reading, direction=Direction.LONG):
    signal = Signal(
        strategy_id="s1", instrument="EURUSD", timeframe="H4", direction=direction,
        bar_time=pd.Timestamp("2026-01-01", tz="UTC"), reference_price=1.1,
        stop_price=1.097 if direction is Direction.LONG else 1.103,
    )
    return CandidateSignal(signal=signal, regime="trend", conviction=reading)


def test_the_runtime_reads_the_verdict_point_in_time_and_the_policy_bounds_it(debated) -> None:
    _d, _s, _l, _p, as_of, written, _r, tmp_path = debated
    adapter = ConvictionAdapter(conviction_rows_from_sqlite(tmp_path / "agents" / "thesis.sqlite"))
    before = pd.Timestamp(as_of)  # filed 30 s later: not yet available
    assert adapter.reading("EURUSD", before) is None
    at = pd.Timestamp(written) + pd.Timedelta(hours=1)
    reading = adapter.reading("EURUSD", at)
    assert reading is not None and (reading.stance, reading.strength) == ("short", 2)

    def weight(policy, tier, when=at):
        snap = PortfolioSnapshot(
            account=AccountState(balance=1e5, equity=1e5, peak_equity=1e5), as_of=when,
            open_risk_by_instrument={},
        )
        result = PortfolioConstructor(
            config=ConstructionConfig(conviction=policy),
            agent_tier=TierReading(tier=tier, source="explicit"),
        ).allocate([_candidate(reading)], snap)
        return result.allocations[0].weight, result.shadow[0]

    w, shadow = weight(ConvictionPolicy(), AgentInfluenceTier.T1_ANNOTATE_SHADOW)
    assert w == 1.0 and shadow.would_be_factor == 0.75 and shadow.artefact_id == reading.artefact_id
    w, _ = weight(ConvictionPolicy(enabled=True), AgentInfluenceTier.T3_DAMPEN_SIZE)
    assert w == pytest.approx(0.75)
    stale = pd.Timestamp(as_of) + pd.Timedelta(hours=5)
    w, shadow = weight(ConvictionPolicy(enabled=True), AgentInfluenceTier.T3_DAMPEN_SIZE, stale)
    assert w == 1.0 and shadow.state == "stale"


# ----------------------------------------------------------------- refusals


def _script_with(step: str, mutate) -> dict[str, str]:
    script = offline_thesis_script()
    payload = json.loads(script[step])
    mutate(payload)
    script[step] = json.dumps(payload)
    return script


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda p: p["claims"][0].update(evidence_ids=["regime.axis.moon_phase"]), "do not resolve"),
        (lambda p: p["claims"][0].update(falsifier="More research is needed. Be careful."),
         "generic caution"),
        (lambda p: p["claims"][0].update(
            statement="Buy the pair now because momentum is strong and persistent."),
         "order vocabulary"),
        (lambda p: p["claims"].extend([p["claims"][0]] * 5), "claims"),
    ],
)
def test_an_invalid_turn_is_refused_whole_and_the_debate_carries_on(tmp_path, mutate, error):
    step = thesis_advocate_step("long", 1)
    deps, store, *_rest = _world(tmp_path, _script_with(step, mutate))
    result = run_thesis_debate(deps, instrument="EURUSD", workflow_id="wf_bad")
    failed = next(s for s in result.steps if s.step == step)
    assert not failed.ok and error in failed.error
    # Nothing of the refused turn is filed. Short's round-2 rebuttal of
    # "long.r1.c1" is then refused too: that claim was never filed.
    assert [(t["stance"], t["round"]) for t in store.turns("deb_wf_bad")] == [
        ("short", 1), ("long", 2)
    ]
    short_r2 = next(s for s in result.steps if s.step == thesis_advocate_step("short", 2))
    assert "is not a claim the long side has filed" in short_r2.error
    assert result.conviction_id is not None, "the arbiter still rules on what was filed"


def test_a_rebuttal_of_a_claim_that_does_not_exist_is_refused(tmp_path) -> None:
    step = thesis_advocate_step("long", 2)
    deps, store, *_ = _world(
        tmp_path, _script_with(step, lambda p: p["claims"][1].update(rebuts="short.r2.c5"))
    )
    result = run_thesis_debate(deps, instrument="EURUSD", workflow_id="wf_reb")
    assert "is not a claim the short side has filed" in next(
        s for s in result.steps if s.step == step
    ).error


def test_a_failed_debate_produces_no_conviction(tmp_path) -> None:
    script = {k: v for k, v in offline_thesis_script().items() if k == THESIS_ARBITER_STEP}
    deps, store, *_ = _world(tmp_path, script)  # advocates' outputs will not parse
    result = run_thesis_debate(deps, instrument="EURUSD", workflow_id="wf_fail")
    arbiter = next(s for s in result.steps if s.step == THESIS_ARBITER_STEP)
    assert not arbiter.ok and "no debate turn" in arbiter.error
    assert store.convictions() == [] and result.conviction_id is None


def test_a_verdict_beyond_the_ttl_is_refused(tmp_path) -> None:
    far = "2099-01-01T00:00:00+00:00"
    deps, store, *_ = _world(
        tmp_path, _script_with(THESIS_ARBITER_STEP, lambda p: p.update(valid_until=far))
    )
    result = run_thesis_debate(deps, instrument="EURUSD", workflow_id="wf_ttl")
    arbiter = next(s for s in result.steps if s.step == THESIS_ARBITER_STEP)
    assert not arbiter.ok and "TTL" in arbiter.error
    assert store.convictions() == []


def test_an_incoherent_verdict_is_refused(tmp_path) -> None:
    deps, store, *_ = _world(
        tmp_path, _script_with(THESIS_ARBITER_STEP, lambda p: p.update(stance="none", strength=2))
    )
    run_thesis_debate(deps, instrument="EURUSD", workflow_id="wf_inc")
    assert store.convictions() == []


def test_the_store_is_append_only(debated) -> None:
    _d, store, *_ = debated
    import sqlite3

    for sql in ("UPDATE conviction SET strength = 0", "DELETE FROM debate_turn",
                "UPDATE market_brief SET brief_hash = 'x'"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._conn.execute(sql)


def test_rounds_are_capped_at_two(tmp_path) -> None:
    deps, *_ = _world(tmp_path)
    with pytest.raises(ValueError):
        run_thesis_debate(deps, instrument="EURUSD", rounds=3)


def test_an_unpinned_clock_builds_no_brief(tmp_path) -> None:
    deps, store, *_ = _world(tmp_path)
    deps = replace(deps, context=replace(deps.context, as_of=None))
    result = run_thesis_debate(deps, instrument="EURUSD", workflow_id="wf_unpinned")
    assert [s.step for s in result.steps] == ["market_brief"]
    assert "pinned clock" in result.steps[0].error


# ---------------------------------------------------------- the declarations


def test_new_capabilities_and_tools_pass_every_registration_guard() -> None:
    assert_no_execution_capability([Capability.WRITE_DEBATE_TURN, Capability.WRITE_CONVICTION])
    fresh = ToolRegistry()
    for name in ("build_market_brief", "get_debate", "record_debate_turn", "record_conviction"):
        fresh.register(REGISTRY.get(name))
    assert REGISTRY.get("record_debate_turn").write_domain is WriteDomain.RESEARCH_DEBATE
    assert REGISTRY.get("record_conviction").write_domain is WriteDomain.RESEARCH_CONVICTION
    assert not REGISTRY.get("build_market_brief").mutates


def test_the_debate_roles_are_narrow() -> None:
    advocate = get_role(AgentRole.THESIS_ADVOCATE)
    arbiter = get_role(AgentRole.THESIS_ARBITER)
    assert advocate.capabilities == {Capability.READ_REGIME, Capability.WRITE_DEBATE_TURN}
    assert arbiter.capabilities == {
        Capability.READ_REGIME, Capability.READ_RESEARCH_MEMORY, Capability.WRITE_CONVICTION,
    }
    for spec in (advocate, arbiter):
        assert Capability.SUBMIT_JOB not in spec.capabilities
        assert Capability.READ_PORTFOLIO not in spec.capabilities


def test_context_budgets_follow_g_3_4_3_and_are_in_the_manifest() -> None:
    budgets = {s.role.value: (s.context_budget.input_tokens, s.context_budget.output_tokens)
               for s in all_roles()}
    assert budgets["thesis_advocate"] == (8_000, 1_000)
    assert budgets["thesis_arbiter"] == (10_000, 400)
    assert budgets["failure_investigator"] == (8_000, 1_500)
    assert budgets["research_director"] == (12_000, 2_000)
    base = build_run_manifest()
    roles = [
        replace(s, context_budget=ContextBudget(9_000, 1_000))
        if s.role is AgentRole.THESIS_ADVOCATE else s
        for s in all_roles()
    ]
    moved = build_run_manifest(roles=roles)
    assert base.hash != moved.hash
    assert set(base.diff(moved)) == {"role_prompt:thesis_advocate"}


def test_the_debate_is_due_once_per_bar_close() -> None:
    t = datetime(2026, 9, 29, 9, 17, tzinfo=UTC)
    assert last_bar_close("H4", t) == datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
    assert last_bar_close("D1", t) == datetime(2026, 9, 29, 0, 0, tzinfo=UTC)
    assert thesis_debate_due("H4", None, t)
    assert not thesis_debate_due("H4", datetime(2026, 9, 29, 8, 0, tzinfo=UTC), t)
    assert thesis_debate_due("H4", datetime(2026, 9, 29, 4, 0, tzinfo=UTC), t)
    with pytest.raises(ValueError):
        last_bar_close("H1", t)


# ------------------------------------------------------------- the session


def test_a_prompt_over_the_role_budget_is_refused_on_the_record(tmp_path) -> None:
    deps, *_ = _world(tmp_path)
    provider = EchoProvider({"x": "{}"})
    session = open_session(
        agent_id="adv@budget", role=AgentRole.THESIS_ADVOCATE, context=deps.context,
        resolver=deps.resolver, ledger=deps.ledger, router=ModelRouter([provider]),
    )
    with pytest.raises(BudgetExceeded, match="input budget"):
        session.think("x" * 40_000, reason="too long", step="x")
    assert provider.calls == [], "nothing may be sent once the budget refuses"
    refused = deps.ledger.records()[-1]
    assert refused.tool == "model:refused_by_budget" and refused.kind is ActionKind.MODEL_CALL


def test_max_tokens_is_clamped_to_the_output_budget_and_the_model_is_pinned(tmp_path) -> None:
    deps, *_ = _world(tmp_path)
    provider = EchoProvider({"x": "{}"})
    session = open_session(
        agent_id="arb@clamp", role=AgentRole.THESIS_ARBITER, context=deps.context,
        resolver=deps.resolver, ledger=deps.ledger, router=ModelRouter([provider]),
        manifest_hash="mh_test",
    )
    assert session.context.model_id == ""
    session.think("short prompt", reason="clamp", step="x", max_tokens=4_000)
    assert provider.calls[-1].max_tokens == 400
    assert session.context.model_id == "echo-1"
    assert session.context.model_digest.startswith("sha256:")
    assert session.context.manifest_hash == "mh_test"


def test_token_and_seconds_budgets_bound_a_zero_cost_session() -> None:
    budget = SessionBudget(max_prompt_tokens=100, max_completion_tokens=50, max_model_seconds=1.0)
    budget.check_model_headroom(60)
    budget.charge_model(0.0, prompt_tokens=60, completion_tokens=10, seconds=0.1)
    with pytest.raises(BudgetExceeded, match="prompt-token"):
        budget.check_model_headroom(60)
    with pytest.raises(BudgetExceeded, match="completion tokens"):
        SessionBudget(max_completion_tokens=5).charge_model(0.0, completion_tokens=6)
    with pytest.raises(BudgetExceeded, match="model seconds"):
        SessionBudget(max_model_seconds=1.0).charge_model(0.0, seconds=2.0)
    declared = SessionBudget().declaration()
    assert {"max_prompt_tokens", "max_completion_tokens", "max_model_seconds",
            "max_cost_usd"} <= set(declared)
