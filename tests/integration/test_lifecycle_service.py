"""The lifecycle service end to end: monitor, rules, demotion, alerts, state.

These are the tests that check the wiring rather than the parts -- that a fired
rule actually reaches a transition and an alert, that a demotion is recorded
under a named automated actor, and that the state the API would serve agrees with
the log.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from fiboki.core.enums import StrategyLifecycle
from fiboki.lifecycle.degradation import DegradationBand, DegradationConfig
from fiboki.lifecycle.monitor import ForwardMonitor, MonitorConfig
from fiboki.lifecycle.service import LifecycleService, PromotionRefused
from fiboki.lifecycle.state import (
    ActorKind,
    FileTransitionLog,
    HumanAuthorisationRequired,
    LifecycleError,
    LifecycleStateMachine,
    UnknownStrategy,
)
from fiboki.lifecycle.stopping_rules import (
    ALL_RULE_KINDS,
    FileHaltJournal,
    FilePreRegistrationStore,
    HaltNotReleasable,
    HaltRegistry,
    PsrFloorParameters,
    StoppingRuleKind,
)
from fiboki.obs.alerts import AlertDispatcher, AlertEvent, MemoryChannel, Severity
from tests.lifecycle_fixtures import (
    HASH_A,
    HUMAN,
    T0,
    expectation,
    machine_at,
    observation,
    promotion_evidence,
    rule_parameters,
)


def _service(
    state: StrategyLifecycle = StrategyLifecycle.PAPER,
    *,
    register_rules: bool = True,
    backtest_sharpe: float = 0.40,
    drawdown_threshold: float = 0.25,
    config: DegradationConfig | None = None,
) -> tuple[LifecycleService, MemoryChannel]:
    channel = MemoryChannel()
    service = LifecycleService(
        machine=machine_at(state),
        dispatcher=AlertDispatcher([channel], min_severity=Severity.INFO),
        monitor=ForwardMonitor(MonitorConfig()),
        degradation_config=config or DegradationConfig(),
        clock=lambda: T0,
    )
    if register_rules:
        service.pre_register_rules(
            HASH_A,
            parameters=rule_parameters(
                backtest_sharpe=backtest_sharpe, drawdown_threshold=drawdown_threshold
            ),
            registered_by="joe",
            reason="pre-registered before the first paper trade",
            at=T0,
        )
    return service, channel


# ==========================================================================
# Registration and pre-registration
# ==========================================================================


def test_the_service_refuses_a_partial_stopping_rule_set():
    service, _ = _service(register_rules=False)
    with pytest.raises(LifecycleError, match="all three stopping rules"):
        service.pre_register_rules(
            HASH_A,
            parameters={StoppingRuleKind.PSR_FLOOR: PsrFloorParameters(backtest_sharpe=0.4)},
            registered_by="joe",
            reason="partial",
        )


def test_a_running_strategy_with_no_registrations_is_reported_as_unprotected():
    service, _ = _service(register_rules=False)
    evaluation = service.evaluate(
        HASH_A, expectation=expectation(), observation=observation()
    )
    assert any("nothing pre-registered can halt on" in n for n in evaluation.notes)
    assert set(service.status(HASH_A).missing_rule_registrations) == set(ALL_RULE_KINDS)


def test_an_unregistered_strategy_cannot_be_evaluated():
    service, _ = _service()
    with pytest.raises(UnknownStrategy):
        service.evaluate("f" * 64)


# ==========================================================================
# The quiet case
# ==========================================================================


def test_a_healthy_strategy_is_left_alone():
    service, channel = _service()
    rng = np.random.default_rng(101)
    evaluation = service.evaluate(
        HASH_A,
        expectation=expectation(),
        observation=observation(returns=rng.normal(0.004, 0.010, 220)),
    )
    assert not evaluation.demoted
    assert evaluation.fired_rules == ()
    assert evaluation.divergence.n_flagged == 0
    assert channel.sent == []
    assert service.lifecycle_of(HASH_A) is StrategyLifecycle.PAPER

    status = service.status(HASH_A)
    assert not status.degraded
    assert status.health > 0.9
    assert status.ever_evaluated


# ==========================================================================
# The headline demotion
# ==========================================================================


def test_a_strategy_whose_live_sharpe_is_half_its_backtest_is_demoted():
    """The requirement, end to end.

    Backtest Sharpe 0.40; the forward record delivers 0.20 -- exactly half. The
    PSR floor fires, the halt latches, the strategy is quarantined automatically
    under a named rule actor, and a CRITICAL alert is raised.
    """
    service, channel = _service(backtest_sharpe=0.40)
    raw = np.random.default_rng(4).normal(0.0, 1.0, 300)
    z = (raw - raw.mean()) / raw.std(ddof=1)
    half = 0.01 * (z + 0.20)
    assert float(half.mean() / half.std(ddof=1)) == pytest.approx(0.20, abs=1e-9)

    evaluation = service.evaluate(
        HASH_A, expectation=expectation(), observation=observation(returns=half)
    )

    assert evaluation.demoted
    assert evaluation.state_after is StrategyLifecycle.QUARANTINED
    assert StoppingRuleKind.PSR_FLOOR in {e.kind for e in evaluation.fired_rules}
    assert StoppingRuleKind.PSR_FLOOR in evaluation.latched_halts

    transition = evaluation.transitions[-1]
    assert transition.automatic is True
    assert transition.actor.kind is ActorKind.AUTOMATED_RULE
    assert transition.actor.name == "stopping_rule:psr_floor"
    assert any(e.kind.value == "stopping_rule" for e in transition.evidence)

    severities = {a.severity for a in channel.sent}
    assert Severity.CRITICAL in severities
    # The taxonomy gap this package used to record is closed: a fired stopping
    # rule and a demotion into QUARANTINED are separate events, so a channel can
    # filter on "this has stopped" without reading the severity.
    events = {a.event for a in channel.sent}
    assert AlertEvent.STRATEGY_HALTED in events, "the fired rule did not alert as a halt"
    assert AlertEvent.STRATEGY_QUARANTINED in events, (
        "the demotion into QUARANTINED alerted as something else"
    )
    assert events <= {
        AlertEvent.STRATEGY_HALTED,
        AlertEvent.STRATEGY_QUARANTINED,
        AlertEvent.STRATEGY_DEGRADED,
    }
    halted = next(a for a in channel.sent if a.event is AlertEvent.STRATEGY_HALTED)
    assert halted.severity is Severity.CRITICAL
    assert halted.context["requires_operator_release"] is True
    assert service.machine.verify().ok


def test_a_quarantined_strategy_stays_quarantined_when_the_numbers_recover():
    """The latch, through the service. A strategy in decline has good weeks."""
    service, _ = _service(backtest_sharpe=0.40)
    collapsed = np.random.default_rng(9).normal(-0.004, 0.010, 250)
    service.evaluate(
        HASH_A, expectation=expectation(), observation=observation(returns=collapsed)
    )
    assert service.lifecycle_of(HASH_A) is StrategyLifecycle.QUARANTINED

    healthy = np.random.default_rng(101).normal(0.004, 0.010, 220)
    later = service.evaluate(
        HASH_A,
        expectation=expectation(),
        observation=observation(returns=healthy),
        at=T0 + timedelta(days=30),
    )
    assert service.lifecycle_of(HASH_A) is StrategyLifecycle.QUARANTINED
    assert later.latched_halts
    assert later.degradation.forced_band is DegradationBand.QUARANTINED


def test_releasing_a_halt_does_not_by_itself_restore_the_strategy():
    """Two decisions, two records. Clearing the halt and putting risk back on are
    not the same act."""
    service, _ = _service(backtest_sharpe=0.40)
    collapsed = np.random.default_rng(9).normal(-0.004, 0.010, 250)
    service.evaluate(
        HASH_A, expectation=expectation(), observation=observation(returns=collapsed)
    )
    for kind in service.halts.latched(HASH_A):
        service.release_halt(
            HASH_A, kind, operator="joe", reason="cause traced to a feed outage"
        )
    assert not service.halts.is_halted(HASH_A)
    assert service.lifecycle_of(HASH_A) is StrategyLifecycle.QUARANTINED

    record = service.machine.transition(
        HASH_A,
        StrategyLifecycle.PAPER,
        actor=HUMAN,
        reason="feed fixed, re-running from paper",
        evidence=(service.last_evaluation(HASH_A).transitions[-1].evidence[0],),
        at=T0 + timedelta(days=1),
    )
    assert record.state is StrategyLifecycle.PAPER
    assert record.history[-1].actor.kind is ActorKind.HUMAN


def test_a_halt_cannot_be_released_without_a_named_operator():
    service, _ = _service(backtest_sharpe=0.40)
    collapsed = np.random.default_rng(9).normal(-0.004, 0.010, 250)
    service.evaluate(
        HASH_A, expectation=expectation(), observation=observation(returns=collapsed)
    )
    with pytest.raises(HaltNotReleasable):
        service.release_halt(
            HASH_A, StoppingRuleKind.PSR_FLOOR, operator="", reason="tidy up"
        )


# ==========================================================================
# Degradation without a fired rule
# ==========================================================================


def test_a_cost_blowout_alone_walks_the_bands_rather_than_jumping_to_quarantine():
    """No pre-registered rule has fired; the costs have merely gone wrong. The
    strategy must move DOWN, and it must take more than one reading to do it."""
    service, channel = _service(backtest_sharpe=0.40, drawdown_threshold=0.90)
    obs = lambda t: observation(  # noqa: E731
        returns=np.random.default_rng(101).normal(0.004, 0.010, 220),
        spread=3.0,
        slippage=1.0,
        latency=500.0,
    )
    first = service.evaluate(
        HASH_A, expectation=expectation(), observation=obs(0), at=T0
    )
    assert first.fired_rules == ()
    assert not first.demoted, "one reading is noise"
    assert first.verdict.pending_band is not None
    assert any(a.severity is Severity.WARNING for a in channel.sent)

    second = service.evaluate(
        HASH_A, expectation=expectation(), observation=obs(1), at=T0 + timedelta(days=1)
    )
    assert second.demoted
    assert second.state_after in (StrategyLifecycle.WATCH, StrategyLifecycle.DEGRADED)
    assert second.transitions[-1].actor.name == "lifecycle:degradation"
    assert second.divergence.cost_divergence


def test_a_demotion_cites_the_divergence_report_as_evidence():
    service, _ = _service(drawdown_threshold=0.90)
    for i in range(2):
        evaluation = service.evaluate(
            HASH_A,
            expectation=expectation(),
            observation=observation(
                returns=np.random.default_rng(101).normal(0.004, 0.010, 220),
                spread=3.0,
                slippage=1.0,
                latency=500.0,
            ),
            at=T0 + timedelta(days=i),
        )
    kinds = {e.kind.value for e in evaluation.transitions[-1].evidence}
    assert "monitor_result" in kinds


# ==========================================================================
# Promotion through the service
# ==========================================================================


def test_promotion_requires_the_criteria_and_a_human():
    service, _ = _service(StrategyLifecycle.PAPER)
    with pytest.raises(PromotionRefused):
        service.promote(
            HASH_A,
            StrategyLifecycle.SHADOW,
            actor=HUMAN,
            reason="early",
            evidence=promotion_evidence(days_in_state=2.0),
            at=T0,
        )
    record = service.promote(
        HASH_A,
        StrategyLifecycle.SHADOW,
        actor=HUMAN,
        reason="Gate B first half satisfied",
        evidence=promotion_evidence(),
        at=T0,
    )
    assert record.state is StrategyLifecycle.SHADOW


def test_the_service_cannot_promote_a_quarantined_strategy_back_up():
    service, _ = _service(backtest_sharpe=0.40)
    collapsed = np.random.default_rng(9).normal(-0.004, 0.010, 250)
    service.evaluate(
        HASH_A, expectation=expectation(), observation=observation(returns=collapsed)
    )
    from fiboki.lifecycle.promotion import UnknownPromotion

    with pytest.raises(UnknownPromotion):
        service.promote(
            HASH_A,
            StrategyLifecycle.SHADOW,
            actor=HUMAN,
            reason="it looks fine now",
            evidence=promotion_evidence(),
            at=T0,
        )


def test_reaching_live_still_requires_a_human_even_through_a_configured_service():
    from tests.lifecycle_fixtures import AUTOMATED_ACTORS

    for actor in AUTOMATED_ACTORS:
        service, _ = _service(StrategyLifecycle.APPROVED)
        with pytest.raises(HumanAuthorisationRequired):
            service.promote(
                HASH_A,
                StrategyLifecycle.LIVE,
                actor=actor,
                reason="criteria met",
                evidence=promotion_evidence(),
                at=T0,
            )


# ==========================================================================
# Queryable state, and durability
# ==========================================================================


def test_status_supplies_exactly_what_the_risk_gateway_needs():
    """``lifecycle``, ``health`` and ``degraded`` are the three fields
    ``risk.gateway.StrategyView`` carries. This package must not import it."""
    from fiboki.risk.gateway import StrategyView

    service, _ = _service(backtest_sharpe=0.40)
    collapsed = np.random.default_rng(9).normal(-0.004, 0.010, 250)
    service.evaluate(
        HASH_A, expectation=expectation(), observation=observation(returns=collapsed)
    )
    status = service.status(HASH_A)
    view = StrategyView(
        lifecycle=status.lifecycle, health=status.health, degraded=status.degraded
    )
    assert view.lifecycle is StrategyLifecycle.QUARANTINED
    assert view.degraded is True
    assert 0.0 <= view.health <= 1.0


def test_no_lifecycle_module_imports_the_execution_layer():
    """An AST-free structural check: the dependency direction, asserted.

    ``risk`` and ``portfolio`` consume ``StrategyLifecycle``; if this package
    imported them the cycle would make the gateway's lifecycle check testable
    only through this service.
    """
    import pathlib

    root = pathlib.Path("src/fiboki/lifecycle")
    offenders = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for banned in ("fiboki.risk", "fiboki.portfolio", "fiboki.broker", "fiboki.api"):
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) and banned in stripped:
                    offenders.append(f"{path.name}: {stripped}")
    assert offenders == [], offenders


def test_status_is_not_claimed_for_an_unevaluated_strategy():
    service, _ = _service()
    status = service.status(HASH_A)
    assert not status.ever_evaluated
    assert status.last_score is None


def test_evaluate_all_covers_running_strategies_with_no_observations():
    """A strategy that stopped producing observations is exactly the case a
    monitor keyed only on supplied inputs would silently skip."""
    service, _ = _service()
    evaluations = service.evaluate_all({}, at=T0)
    assert len(evaluations) == 1
    assert any("not the same as agreeing" in n for n in evaluations[0].notes)
    assert not evaluations[0].demoted


def test_the_whole_apparatus_survives_a_restart(tmp_path):
    log = FileTransitionLog(tmp_path / "transitions.jsonl")
    registrations = FilePreRegistrationStore(tmp_path / "registrations.jsonl")
    halts = HaltRegistry(FileHaltJournal(tmp_path / "halts.jsonl"))
    machine = LifecycleStateMachine(log)
    machine.register(
        strategy_id="ichimoku_baseline",
        strategy_content_hash=HASH_A,
        actor=HUMAN,
        reason="seed",
        at=T0,
    )
    for i, step in enumerate(
        (
            StrategyLifecycle.RESEARCH,
            StrategyLifecycle.VALIDATING,
            StrategyLifecycle.CANDIDATE,
            StrategyLifecycle.PAPER,
        ),
        start=1,
    ):
        machine.transition(
            HASH_A,
            step,
            actor=HUMAN,
            reason="up",
            evidence=(
                __import__("fiboki.lifecycle.state", fromlist=["Evidence"]).Evidence.note(
                    "n", "promotion"
                ),
            ),
            at=T0 + timedelta(days=i),
        )
    service = LifecycleService(
        machine=machine, registrations=registrations, halts=halts, clock=lambda: T0
    )
    service.pre_register_rules(
        HASH_A,
        parameters=rule_parameters(backtest_sharpe=0.40),
        registered_by="joe",
        reason="pre-registration",
        at=T0,
    )
    collapsed = np.random.default_rng(9).normal(-0.004, 0.010, 250)
    service.evaluate(
        HASH_A, expectation=expectation(), observation=observation(returns=collapsed)
    )
    assert service.lifecycle_of(HASH_A) is StrategyLifecycle.QUARANTINED

    restarted = LifecycleService(
        machine=LifecycleStateMachine(FileTransitionLog(tmp_path / "transitions.jsonl")),
        registrations=FilePreRegistrationStore(tmp_path / "registrations.jsonl"),
        halts=HaltRegistry(FileHaltJournal(tmp_path / "halts.jsonl")),
        clock=lambda: T0,
    )
    assert restarted.lifecycle_of(HASH_A) is StrategyLifecycle.QUARANTINED
    assert restarted.halts.is_halted(HASH_A)
    assert restarted.registrations.missing_rules(HASH_A) == ()
    assert restarted.status(HASH_A).degraded
    assert restarted.machine.verify().ok


def test_an_evaluation_serialises_whole():
    import json

    service, _ = _service(backtest_sharpe=0.40)
    evaluation = service.evaluate(
        HASH_A,
        expectation=expectation(),
        observation=observation(
            returns=np.random.default_rng(9).normal(-0.004, 0.010, 250)
        ),
    )
    payload = json.dumps(evaluation.to_dict(), default=str)
    assert "quarantined" in payload
    assert "psr_floor" in payload
