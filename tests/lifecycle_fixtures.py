"""Builders shared by the lifecycle tests.

Plain functions rather than fixtures so they can be called several times inside
one test with different arguments, which most of these tests need.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from fiboki.core.enums import StrategyLifecycle
from fiboki.lifecycle.monitor import Expectation, Observation
from fiboki.lifecycle.promotion import PromotionEvidence
from fiboki.lifecycle.state import (
    Actor,
    Evidence,
    HumanAuthorisation,
    LifecycleStateMachine,
)
from fiboki.lifecycle.stopping_rules import (
    BootstrapDrawdownParameters,
    CusumParameters,
    PsrFloorParameters,
    StoppingRuleKind,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
T0 = datetime(2026, 1, 1, tzinfo=UTC)

HUMAN = Actor.human("joe")
RULE = Actor.rule("stopping_rule:psr_floor")
SYSTEM = Actor.system("research-loop")

#: Every actor that is NOT a human. Parametrised over by the LIVE tests.
AUTOMATED_ACTORS = (
    Actor.rule("stopping_rule:psr_floor"),
    Actor.rule("stopping_rule:bootstrap_drawdown"),
    Actor.rule("stopping_rule:cusum_excess_return"),
    Actor.rule("lifecycle:degradation"),
    Actor.system("research-loop"),
    Actor.system("migration"),
)


def note(text: str = "because the test says so") -> tuple[Evidence, ...]:
    return (Evidence.note("note:test", text),)


def authorisation(
    content_hash: str = HASH_A,
    to_state: StrategyLifecycle = StrategyLifecycle.LIVE,
    *,
    by: str = "joe",
) -> HumanAuthorisation:
    return HumanAuthorisation(
        authorised_by=by,
        strategy_content_hash=content_hash,
        to_state=to_state,
        statement=(
            "Gate C evidence reviewed in full; approximations accepted in writing; "
            "kill-switch drill timed at 41 seconds."
        ),
        authorised_at=T0,
    )


def machine_at(
    state: StrategyLifecycle,
    *,
    content_hash: str = HASH_A,
    strategy_id: str = "ichimoku_baseline",
    at: datetime | None = None,
) -> LifecycleStateMachine:
    """A machine with one strategy walked up the ladder to ``state``.

    Walks the real edges rather than setting the state directly, so every test
    that starts from a state starts from one the machine agrees is reachable.
    """
    from fiboki.lifecycle.state import LADDER

    when = at or T0
    m = LifecycleStateMachine()
    m.register(
        strategy_id=strategy_id,
        strategy_content_hash=content_hash,
        actor=HUMAN,
        reason="seed document",
        at=when,
    )
    if state is StrategyLifecycle.DISCOVERY:
        return m
    # Off-ladder states are reached by walking to PAPER and then escalating:
    # the lowest running state that can legally leave the ladder.
    target_index = (
        LADDER.index(state) if state in LADDER else LADDER.index(StrategyLifecycle.PAPER)
    )
    for i in range(1, target_index + 1):
        step = LADDER[i]
        evidence: tuple[Evidence, ...] = note(f"promoted to {step.value}")
        if step is StrategyLifecycle.LIVE:
            evidence = (*evidence, authorisation(content_hash).as_evidence())
        m.transition(
            content_hash,
            step,
            actor=HUMAN,
            reason=f"promotion to {step.value}",
            evidence=evidence,
            at=when + timedelta(days=i),
        )
    if state not in LADDER:
        actor = HUMAN if state is StrategyLifecycle.RETIRED else RULE
        m.transition(
            content_hash,
            state,
            actor=actor,
            reason=f"moved to {state.value}",
            evidence=note("monitor said so"),
            at=when + timedelta(days=target_index + 1),
        )
    return m


# ---------------------------------------------------------------- monitors


def backtest_returns(
    n: int = 600, mu: float = 0.004, sigma: float = 0.010, seed: int = 11
) -> np.ndarray:
    return np.random.default_rng(seed).normal(mu, sigma, n)


def expectation(
    content_hash: str = HASH_A,
    *,
    returns: np.ndarray | None = None,
    elapsed_days: float = 300.0,
    **kwargs: Any,
) -> Expectation:
    base: dict[str, Any] = {
        "spread_pips": 1.0,
        "slippage_pips": 0.20,
        "latency_ms": 90.0,
        "reject_rate": 0.01,
    }
    base.update(kwargs)
    return Expectation.from_backtest(
        content_hash,
        backtest_returns() if returns is None else returns,
        elapsed_days=elapsed_days,
        source="report:test",
        **base,
    )


def observation(
    content_hash: str = HASH_A,
    *,
    returns: np.ndarray | None = None,
    elapsed_days: float = 100.0,
    spread: float = 1.0,
    slippage: float = 0.20,
    latency: float = 90.0,
    n_cost: int = 80,
    orders_submitted: int = 210,
    orders_rejected: int = 2,
    regime_returns: Mapping[str, Any] | None = None,
    seed: int = 23,
) -> Observation:
    rng = np.random.default_rng(seed)
    live = rng.normal(0.004, 0.010, 200) if returns is None else np.asarray(returns)
    return Observation(
        strategy_content_hash=content_hash,
        returns=live,
        observed_at=T0 + timedelta(days=elapsed_days),
        elapsed_days=elapsed_days,
        spread_pips=rng.normal(spread, spread * 0.1, n_cost),
        slippage_pips=rng.normal(slippage, abs(slippage) * 0.2 + 1e-6, n_cost),
        latency_ms=rng.normal(latency, latency * 0.1, n_cost),
        orders_submitted=orders_submitted,
        orders_rejected=orders_rejected,
        regime_returns=dict(regime_returns or {}),
    )


# ----------------------------------------------------------- stopping rules


def rule_parameters(
    *,
    backtest_sharpe: float = 0.40,
    drawdown_threshold: float = 0.25,
    mu_expected: float = 0.004,
    h: float = 0.302,
) -> dict[StoppingRuleKind, Any]:
    """A cheap, fixed parameter set. Calibration is exercised separately."""
    return {
        StoppingRuleKind.PSR_FLOOR: PsrFloorParameters(backtest_sharpe=backtest_sharpe),
        StoppingRuleKind.BOOTSTRAP_DRAWDOWN: BootstrapDrawdownParameters(
            threshold=drawdown_threshold,
            n_boot=500,
            block_length=4.0,
            calibration_seed=1,
            calibration_n_observations=600,
        ),
        # Calibrated for per-TRADE observations at roughly 500 trades a year,
        # so a two-year in-control ARL is 1000 observations and h ~= 0.302.
        StoppingRuleKind.CUSUM_EXCESS_RETURN: CusumParameters(
            mu_expected=mu_expected,
            h=h,
            sigma=0.01,
            target_arl_observations=1000.0,
            simulated_arl_observations=961.0,
            observations_per_year=500.0,
            calibration_seed=20240919,
            calibration_n_paths=800,
            censoring_multiple=20.0,
            censored_fraction=0.0,
            median_run_length=731.0,
            false_alarm_probability_first_year=0.311,
        ),
    }


# ---------------------------------------------------------------- promotion


def promotion_metrics(**overrides: float) -> dict[str, float]:
    """Every metric any promotion rule asks for, all passing."""
    metrics: dict[str, float] = {
        "hypothesis_states_evidence_against": 1.0,
        "structurally_novel": 1.0,
        "in_sample_trades": 250.0,
        "in_sample_sharpe": 0.45,
        "parameter_count": 5.0,
        "external_trial_count": 23040.0,
        "live_execution_disabled": 1.0,
        "risk_limits_reviewed": 1.0,
        "stopping_rules_pre_registered": 3.0,
        "stopping_rules_registered_before_forward_data": 1.0,
        "days_in_state": 120.0,
        "forward_trades": 150.0,
        "max_unexplained_heartbeat_gap_hours": 0.0,
        "forward_to_backtest_sharpe_ratio": 0.90,
        "divergence_dimensions_flagged": 0.0,
        "worker_kill_alert_proven": 1.0,
        "reconciliation_clean_days": 14.0,
        "sharpe_inside_backtest_bootstrap_ci": 1.0,
        "realised_cost_to_modelled_ratio": 1.1,
        "unreconciled_fills": 0.0,
        "kill_switch_drill_completed": 1.0,
        "approximations_accepted_in_writing": 1.0,
    }
    metrics.update(overrides)
    return metrics


def promotion_evidence(
    *, content_hash: str = HASH_A, with_auth: bool = True, **overrides: float
) -> PromotionEvidence:
    return PromotionEvidence(
        metrics=promotion_metrics(**overrides),
        holdout_consumption_count=1,
        human_authorisation=(
            authorisation(content_hash) if with_auth else None
        ),
    )
