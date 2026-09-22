"""The three pre-registered stopping rules.

Three properties are being established for each rule: it fires on constructed
data where it should, it stays quiet where it should not, and its parameters
cannot be changed after registration without leaving a record that they were.
"""
from __future__ import annotations

import dataclasses
from datetime import timedelta

import numpy as np
import pytest

from fiboki.lifecycle.stopping_rules import (
    ALL_RULE_KINDS,
    BootstrapDrawdownParameters,
    BootstrapDrawdownRule,
    CusumExcessReturnRule,
    FilePreRegistrationStore,
    HaltNotReleasable,
    HaltRegistry,
    InMemoryHaltJournal,
    InMemoryPreRegistrationStore,
    PsrFloorParameters,
    PsrFloorRule,
    RuleObservation,
    RuleStatus,
    StoppingRuleError,
    StoppingRuleKind,
    _simulate_arl_grid,
    build_rule,
    calibrate_cusum_threshold,
    calibrate_drawdown_limit,
    cusum_parameters_for,
    cusum_path,
)
from tests.lifecycle_fixtures import HASH_A, T0, rule_parameters


def _store_with(kind: StoppingRuleKind, params, *, at=T0):
    store = InMemoryPreRegistrationStore()
    reg = store.register(
        strategy_content_hash=HASH_A,
        kind=kind,
        parameters=params,
        registered_by="joe",
        reason="pre-registered before the first paper trade",
        at=at,
    )
    return store, build_rule(reg)


def _observe(returns, *, at=T0):
    return RuleObservation(
        strategy_content_hash=HASH_A, returns=np.asarray(returns, dtype=float), at=at
    )


def _returns_with_sharpe(target: float, n: int = 300, seed: int = 4) -> np.ndarray:
    """A series whose SAMPLE per-observation Sharpe is exactly ``target``.

    Standardise, then rescale. Doing it exactly rather than in expectation is
    what makes the boundary test a boundary test rather than a coin flip.
    """
    raw = np.random.default_rng(seed).normal(0.0, 1.0, n)
    z = (raw - raw.mean()) / raw.std(ddof=1)
    out = 0.01 * (z + target)
    assert float(out.mean() / out.std(ddof=1)) == pytest.approx(target, abs=1e-9)
    return out


# ==========================================================================
# Pre-registration
# ==========================================================================


def test_parameters_cannot_be_mutated_in_place():
    params = PsrFloorParameters(backtest_sharpe=0.40)
    with pytest.raises(dataclasses.FrozenInstanceError):
        params.floor = 0.10  # type: ignore[misc]


def test_changing_parameters_after_registration_creates_a_new_record():
    """The property the audit asks for, stated mechanically.

    Registering different parameters does not edit the old registration and does
    not refuse: it appends, with ``supersedes`` naming what it replaced. Both are
    visible, in the hash chain, for ever -- so "we loosened the rule after we saw
    the data" is a question anybody can answer from the artefact.
    """
    store, _ = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.40)
    )
    first = store.active(HASH_A, StoppingRuleKind.PSR_FLOOR)
    assert first.supersedes == ""

    second = store.register(
        strategy_content_hash=HASH_A,
        kind=StoppingRuleKind.PSR_FLOOR,
        parameters=PsrFloorParameters(backtest_sharpe=0.40, floor=0.10),
        registered_by="joe",
        reason="the floor felt harsh once the numbers arrived",
        at=T0 + timedelta(days=90),
    )
    history = store.history(HASH_A, StoppingRuleKind.PSR_FLOOR)
    assert len(history) == 2
    assert history[0] == first, "the original registration was not edited"
    assert second.supersedes == first.registration_id
    assert second.parameters_fingerprint != first.parameters_fingerprint
    assert store.active(HASH_A, StoppingRuleKind.PSR_FLOOR) == second
    ok, broken, reason = store.verify()
    assert ok, f"{broken}: {reason}"


def test_an_identical_re_registration_is_a_no_op_not_a_new_row():
    params = PsrFloorParameters(backtest_sharpe=0.40)
    store, _ = _store_with(StoppingRuleKind.PSR_FLOOR, params)
    store.register(
        strategy_content_hash=HASH_A,
        kind=StoppingRuleKind.PSR_FLOOR,
        parameters=PsrFloorParameters(backtest_sharpe=0.40),
        registered_by="joe",
        reason="restating the same thing",
    )
    assert len(store.history(HASH_A, StoppingRuleKind.PSR_FLOOR)) == 1


def test_a_registration_re_made_after_forward_data_is_not_pre_registered():
    store = InMemoryPreRegistrationStore()
    for kind, params in rule_parameters().items():
        store.register(
            strategy_content_hash=HASH_A,
            kind=kind,
            parameters=params,
            registered_by="joe",
            reason="before the first trade",
            at=T0,
        )
    first_trade = T0 + timedelta(days=1)
    assert store.is_pre_registered(HASH_A, first_forward_observation_at=first_trade)

    store.register(
        strategy_content_hash=HASH_A,
        kind=StoppingRuleKind.BOOTSTRAP_DRAWDOWN,
        parameters=BootstrapDrawdownParameters(threshold=0.60, n_boot=500),
        registered_by="joe",
        reason="the limit turned out to be tight",
        at=T0 + timedelta(days=60),
    )
    assert not store.is_pre_registered(
        HASH_A, first_forward_observation_at=first_trade
    ), "the ACTIVE registration postdates the forward data, so it is not pre-registered"


def test_a_partial_registration_is_reported_as_missing():
    store, _ = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.4)
    )
    assert set(store.missing_rules(HASH_A)) == {
        StoppingRuleKind.BOOTSTRAP_DRAWDOWN,
        StoppingRuleKind.CUSUM_EXCESS_RETURN,
    }
    assert not store.is_pre_registered(HASH_A, first_forward_observation_at=T0)


def test_a_registration_must_be_signed_and_reasoned():
    store = InMemoryPreRegistrationStore()
    for by, reason, match in (
        ("", "why", "must name who"),
        ("joe", "", "must say why"),
    ):
        with pytest.raises(StoppingRuleError, match=match):
            store.register(
                strategy_content_hash=HASH_A,
                kind=StoppingRuleKind.PSR_FLOOR,
                parameters=PsrFloorParameters(backtest_sharpe=0.4),
                registered_by=by,
                reason=reason,
            )


def test_a_tampered_registration_breaks_the_chain(tmp_path):
    import json

    path = tmp_path / "registrations.jsonl"
    store = FilePreRegistrationStore(path)
    for kind, params in rule_parameters().items():
        store.register(
            strategy_content_hash=HASH_A,
            kind=kind,
            parameters=params,
            registered_by="joe",
            reason="pre-registration",
            at=T0,
        )
    assert store.verify()[0]

    lines = path.read_text().splitlines()
    row = json.loads(lines[1])
    row["parameters"]["threshold"] = 0.90
    lines[1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    ok, broken, reason = FilePreRegistrationStore(path).verify()
    assert not ok
    assert broken == 1
    assert "record_hash" in reason


def test_a_rule_cannot_be_built_from_the_wrong_registration():
    store, _ = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.4)
    )
    reg = store.active(HASH_A, StoppingRuleKind.PSR_FLOOR)
    with pytest.raises(StoppingRuleError, match="needs a bootstrap_drawdown"):
        BootstrapDrawdownRule(reg)


def test_a_rule_refuses_an_observation_for_a_different_strategy():
    _, rule = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.4)
    )
    with pytest.raises(StoppingRuleError, match="registered for strategy"):
        rule.evaluate(
            RuleObservation(strategy_content_hash="b" * 64, returns=np.zeros(100) + 0.01)
        )


# ==========================================================================
# PSR floor
# ==========================================================================


def test_psr_floor_fires_when_the_live_sharpe_is_exactly_half_the_backtest():
    """The headline case, and the one an operator will argue about.

    Backtest Sharpe 0.40, benchmark 0.20, live Sharpe exactly 0.20. The PSR
    against that benchmark is 0.50 -- the evidence is exactly balanced -- and the
    rule halts, because a strategy delivering half of what it promised has not
    earned the benefit of the doubt.
    """
    _, rule = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.40)
    )
    evaluation = rule.evaluate(_observe(_returns_with_sharpe(0.20)))
    assert evaluation.statistic == pytest.approx(0.50, abs=0.02)
    assert evaluation.fired, evaluation.describe()
    assert evaluation.detail["benchmark_sharpe"] == pytest.approx(0.20)


def test_psr_floor_fires_hard_when_the_live_sharpe_collapses():
    _, rule = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.40)
    )
    evaluation = rule.evaluate(_observe(_returns_with_sharpe(-0.05)))
    assert evaluation.fired
    assert evaluation.statistic < 0.01


def test_psr_floor_stays_quiet_when_the_strategy_delivers():
    _, rule = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.40)
    )
    evaluation = rule.evaluate(_observe(_returns_with_sharpe(0.40)))
    assert evaluation.status is RuleStatus.CLEAR
    assert evaluation.statistic > 0.99


def test_psr_floor_is_not_evaluated_on_too_short_a_record():
    _, rule = _store_with(
        StoppingRuleKind.PSR_FLOOR,
        PsrFloorParameters(backtest_sharpe=0.40, min_observations=100),
    )
    evaluation = rule.evaluate(_observe(_returns_with_sharpe(-0.5, n=40)))
    assert evaluation.status is RuleStatus.NOT_EVALUATED
    assert not evaluation.fired


def test_psr_floor_refuses_a_non_positive_backtest_sharpe():
    with pytest.raises(ValueError, match="positive backtested Sharpe"):
        PsrFloorParameters(backtest_sharpe=0.0)


@pytest.mark.filterwarnings("ignore:Precision loss occurred")
def test_psr_floor_reports_not_evaluated_when_the_sharpe_is_undefined():
    """A near-constant series drives the moment estimators to NaN, and
    ``nan <= floor`` is False. Without a finiteness guard an uncomputable PSR
    would read as a PASSING one."""
    _, rule = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.40)
    )
    evaluation = rule.evaluate(_observe(np.full(100, 0.001)))  # zero dispersion
    assert evaluation.status is RuleStatus.NOT_EVALUATED
    assert "undefined" in evaluation.reason


# ==========================================================================
# Bootstrap drawdown
# ==========================================================================


def test_the_drawdown_limit_is_taken_from_the_validation_distribution():
    """Not a round number: the 95th percentile of the strategy's OWN resampled
    history. It must sit above the median path drawdown and below 1."""
    validation = np.random.default_rng(12).normal(0.004, 0.012, 800)
    cal = calibrate_drawdown_limit(validation, n_boot=600, seed=3)
    assert 0.0 < cal.median_drawdown < cal.threshold < 1.0
    assert cal.quantile == 0.95
    assert "percentile" in cal.describe()
    # Deterministic for a seed: a limit that moves between runs is not a limit.
    again = calibrate_drawdown_limit(validation, n_boot=600, seed=3)
    assert again.threshold == cal.threshold


def test_the_drawdown_calibration_refuses_too_short_a_validation_run():
    with pytest.raises(ValueError, match="at least 30"):
        calibrate_drawdown_limit(np.full(10, 0.001))


def test_bootstrap_drawdown_fires_past_the_registered_threshold():
    _, rule = _store_with(
        StoppingRuleKind.BOOTSTRAP_DRAWDOWN,
        BootstrapDrawdownParameters(threshold=0.20, n_boot=500),
    )
    losing = np.concatenate([np.full(30, 0.004), np.full(40, -0.01)])
    evaluation = rule.evaluate(_observe(losing))
    assert evaluation.fired, evaluation.describe()
    assert evaluation.statistic > 0.20


def test_bootstrap_drawdown_stays_quiet_inside_the_threshold():
    _, rule = _store_with(
        StoppingRuleKind.BOOTSTRAP_DRAWDOWN,
        BootstrapDrawdownParameters(threshold=0.20, n_boot=500),
    )
    calm = np.random.default_rng(6).normal(0.003, 0.006, 200)
    evaluation = rule.evaluate(_observe(calm))
    assert evaluation.status is RuleStatus.CLEAR
    assert evaluation.statistic < 0.20


def test_a_drawdown_threshold_outside_zero_to_one_is_refused():
    with pytest.raises(ValueError, match="must be in \\(0, 1\\)"):
        BootstrapDrawdownParameters(threshold=1.5)


# ==========================================================================
# CUSUM
# ==========================================================================


@pytest.mark.golden
def test_cusum_path_is_the_stated_recursion():
    """S_t = max(0, S_{t-1} + (mu - r_t)), mu = 0.01.

    r = [0.02, 0.00, 0.00, 0.03]
      S1 = max(0, 0 + 0.01 - 0.02) = 0
      S2 = max(0, 0 + 0.01 - 0.00) = 0.01
      S3 = max(0, 0.01 + 0.01)     = 0.02
      S4 = max(0, 0.02 + 0.01 - 0.03) = 0.00
    The reflection at zero is the point: the first good trade banks no credit
    against the two bad ones that follow.
    """
    path = cusum_path([0.02, 0.00, 0.00, 0.03], 0.01)
    assert path == pytest.approx([0.0, 0.01, 0.02, 0.0])


def test_cusum_fires_on_a_slow_decay_a_drawdown_limit_would_miss():
    """Returns drift from +0.4% to -0.1% per trade over 400 trades, with noise
    small enough that the equity curve never draws down 20%. The drawdown limit
    is blind to this; the CUSUM is what it is for."""
    params = rule_parameters()[StoppingRuleKind.CUSUM_EXCESS_RETURN]
    _, rule = _store_with(StoppingRuleKind.CUSUM_EXCESS_RETURN, params)
    decaying = np.linspace(0.004, -0.001, 400) + np.random.default_rng(8).normal(
        0, 0.002, 400
    )
    evaluation = rule.evaluate(_observe(decaying))
    assert evaluation.fired, evaluation.describe()
    assert evaluation.detail["first_crossing_index"] > 0

    # ...and the drawdown limit really does miss it, which is the whole argument.
    _, dd = _store_with(
        StoppingRuleKind.BOOTSTRAP_DRAWDOWN,
        BootstrapDrawdownParameters(threshold=0.20, n_boot=200),
    )
    assert not dd.evaluate(_observe(decaying)).fired


def test_cusum_stays_quiet_when_the_strategy_delivers_its_expectation():
    params = rule_parameters()[StoppingRuleKind.CUSUM_EXCESS_RETURN]
    _, rule = _store_with(StoppingRuleKind.CUSUM_EXCESS_RETURN, params)
    on_plan = np.random.default_rng(15).normal(0.004, 0.010, 250)
    assert rule.evaluate(_observe(on_plan)).status is RuleStatus.CLEAR


@pytest.mark.slow
def test_the_cusum_threshold_calibrates_to_roughly_the_target_run_length():
    """Simulate the in-control process at the chosen h and check the ARL.

    The verification simulation uses a different seed from the search, so this
    is not the calibration marking its own homework. The tolerance is wide on
    purpose: the first-passage time of a reflected random walk is heavy-tailed,
    so with 800 paths the standard error of the mean run length is itself on the
    order of 15% of the target.
    """
    target = 504.0
    cal = calibrate_cusum_threshold(sigma=0.01, target_arl_observations=target)
    assert cal.arl_error < 0.25, cal.describe()
    # The analytic reflected-random-walk reference is h = sigma * sqrt(ARL).
    assert cal.h == pytest.approx(cal.analytic_h, rel=0.35)

    times, censored = _simulate_arl_grid(
        np.asarray([cal.h]), 0.01, n_paths=600, max_length=int(20 * target), seed=999
    )
    independent_arl = float(times[0].mean())
    assert independent_arl == pytest.approx(target, rel=0.30), (
        f"independent check: ARL {independent_arl:.0f} against a target of {target:.0f}"
    )
    assert float(censored[0].mean()) < 0.05
    # The median is materially shorter than the mean. An operator told only the
    # ARL will meet the first false halt long before two years.
    assert float(np.median(times[0])) < independent_arl
    assert 0.0 < cal.false_alarm_probability(target / 2.0) < 0.5


@pytest.mark.slow
def test_the_arl_increases_monotonically_with_the_threshold():
    """The property the interpolation relies on. If it failed, the calibrated h
    would be an arbitrary point on a non-monotone curve."""
    grid = np.asarray([0.05, 0.10, 0.15, 0.20, 0.30])
    times, _ = _simulate_arl_grid(grid, 0.01, n_paths=300, max_length=20000, seed=77)
    arl = times.mean(axis=1)
    assert np.all(np.diff(arl) > 0), arl


def test_a_longer_target_run_length_gives_a_higher_threshold():
    short = calibrate_cusum_threshold(
        sigma=0.01, target_arl_observations=252.0, n_paths=200
    )
    long = calibrate_cusum_threshold(
        sigma=0.01, target_arl_observations=1008.0, n_paths=200
    )
    assert long.h > short.h


def test_cusum_parameters_carry_their_calibration_and_its_caveats():
    params = cusum_parameters_for(
        mu_expected=0.004, sigma=0.01, observations_per_year=252.0, n_paths=200
    )
    assert params.target_arl_years == pytest.approx(2.0)
    assert 1.0 < params.simulated_arl_years < 3.0
    payload = params.to_dict()
    assert payload["censoring_multiple"] == 20.0
    assert payload["calibration_seed"]
    # The honest pair: the ARL is a mean, and the median is shorter.
    assert 0.0 < params.median_run_length < params.simulated_arl_observations
    assert 0.0 < params.false_alarm_probability_first_year < 1.0


def test_the_calibration_note_is_carried_onto_every_evaluation():
    """"This calibration is a design choice" must reach the operator, not stop at
    the docstring."""
    _, rule = _store_with(
        StoppingRuleKind.CUSUM_EXCESS_RETURN,
        rule_parameters()[StoppingRuleKind.CUSUM_EXCESS_RETURN],
    )
    evaluation = rule.evaluate(_observe(np.full(100, 0.004)))
    assert "design choices" in evaluation.detail["calibration_note"]


# ==========================================================================
# Halts latch and are released only by a human
# ==========================================================================


def _fired_evaluation():
    _, rule = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.40)
    )
    evaluation = rule.evaluate(_observe(_returns_with_sharpe(-0.2)))
    assert evaluation.fired
    return evaluation


def test_a_halt_latches_and_does_not_clear_when_the_condition_clears():
    """The condition clearing for a week is exactly what a decaying strategy does.
    A halt that clears itself is not a halt."""
    halts = HaltRegistry(InMemoryHaltJournal())
    halts.record_firing(_fired_evaluation())
    assert halts.is_halted(HASH_A, StoppingRuleKind.PSR_FLOOR)

    _, rule = _store_with(
        StoppingRuleKind.PSR_FLOOR, PsrFloorParameters(backtest_sharpe=0.40)
    )
    recovered = rule.evaluate(_observe(_returns_with_sharpe(0.45)))
    assert not recovered.fired
    halts.record_firing(recovered)
    assert halts.is_halted(HASH_A, StoppingRuleKind.PSR_FLOOR)


def test_a_repeat_firing_does_not_write_a_second_halt():
    halts = HaltRegistry(InMemoryHaltJournal())
    evaluation = _fired_evaluation()
    assert halts.record_firing(evaluation) is not None
    assert halts.record_firing(evaluation) is None
    assert len([e for e in halts.events() if e.action == "halt"]) == 1


def test_releasing_a_halt_requires_a_named_operator_and_a_reason():
    halts = HaltRegistry(InMemoryHaltJournal())
    halts.record_firing(_fired_evaluation())
    for operator, reason in (("", "cause understood"), ("joe", "")):
        with pytest.raises(HaltNotReleasable, match="named operator"):
            halts.release(
                HASH_A, StoppingRuleKind.PSR_FLOOR, operator=operator, reason=reason
            )
    assert halts.is_halted(HASH_A)

    halts.release(
        HASH_A,
        StoppingRuleKind.PSR_FLOOR,
        operator="joe",
        reason="cause traced to a data gap, re-run clean",
    )
    assert not halts.is_halted(HASH_A)


def test_releasing_a_halt_that_is_not_latched_is_refused():
    halts = HaltRegistry(InMemoryHaltJournal())
    with pytest.raises(HaltNotReleasable, match="nothing to release"):
        halts.release(HASH_A, StoppingRuleKind.PSR_FLOOR, operator="joe", reason="tidy")


def test_the_halt_journal_records_both_directions():
    halts = HaltRegistry(InMemoryHaltJournal())
    halts.record_firing(_fired_evaluation())
    halts.release(HASH_A, StoppingRuleKind.PSR_FLOOR, operator="joe", reason="fixed")
    actions = [e.action for e in halts.events()]
    assert actions == ["halt", "release"]
    assert halts.events()[1].operator == "joe"


def test_all_three_rule_kinds_are_covered():
    assert len(ALL_RULE_KINDS) == 3
    for kind, params in rule_parameters().items():
        _, rule = _store_with(kind, params)
        assert rule.kind is kind
    assert {PsrFloorRule.kind, BootstrapDrawdownRule.kind, CusumExcessReturnRule.kind} == set(
        ALL_RULE_KINDS
    )
