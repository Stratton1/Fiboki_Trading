"""Fiboki V2 statistical validation library.

This package decides whether a research result can be believed.  V1 searched
23,040 strategy-instrument-timeframe combinations with NO multiple-testing
correction of any kind, so its leaderboard was a sampling distribution of the
maximum wearing the costume of a discovery.  Nothing promoted out of V2 research
should reach paper trading without passing through here.

Layering:

    sharpe            PSR / deflated Sharpe / MinTRL - is this Sharpe real, given
                      non-normality and given how many trials produced it?
    pbo               CSCV - does selecting the in-sample best carry ANY
                      out-of-sample information, or is it a coin flip?
    spa               Hansen SPA, White Reality Check, Romano-Wolf StepM - does
                      the best of the family beat the benchmark, and which ones?
    bootstrap         stationary / moving-block resampling, automatic block
                      length, and ruin simulation that re-sizes off running equity
    cv                purged and embargoed (combinatorial) cross-validation
    multiple_testing  Bonferroni / Holm / BH, and the EFFECTIVE trial count
    stress            cost, delay, deletion and window perturbations as curves
    stability         parameter plateaus versus fitted spikes

The recommended promotion sequence for a candidate, in order of how cheaply each
one kills a bad idea:

    1. effective_trials_by_clustering  - how many independent bets was the search?
    2. deflated_sharpe_ratio           - beat the expected max of that search?
    3. combinatorially_symmetric_cv    - does selection generalise at all?
    4. superior_predictive_ability     - beat the benchmark across the family?
    5. CombinatorialPurgedCV           - consistent across backtest PATHS?
    6. run_stress_suite                - survive the assumptions moving?
    7. analyse_parameter_stability     - a plateau, not a spike?
    8. resample_with_compounding       - ruin probability with REAL position sizing
"""
from __future__ import annotations

from fiboki.stats.bootstrap import (
    BlockLengthEstimate,
    BootstrapCI,
    RuinSimulation,
    bootstrap_confidence_interval,
    compare_sizing_modes,
    moving_block_bootstrap,
    optimal_block_length,
    resample_fixed_size,
    resample_with_compounding,
    stationary_bootstrap,
)
from fiboki.stats.cv import (
    CombinatorialPurgedCV,
    CVSplit,
    LabelSpans,
    PurgedKFold,
    n_backtest_paths,
)
from fiboki.stats.multiple_testing import (
    EffectiveTrials,
    MultipleTestResult,
    benjamini_hochberg,
    bonferroni,
    effective_trials_by_clustering,
    holm,
)
from fiboki.stats.pbo import (
    PBOResult,
    combinatorially_symmetric_cv,
    pbo_null_expectation,
    probability_of_backtest_overfitting,
)
from fiboki.stats.sharpe import (
    SharpeMoments,
    deflated_sharpe_from_trials,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_moments,
)
from fiboki.stats.spa import (
    RealityCheckResult,
    SPAResult,
    StepMResult,
    differentials_from_returns,
    reality_check,
    step_m,
    superior_predictive_ability,
)
from fiboki.stats.stability import (
    ParameterPoint,
    StabilityReport,
    analyse_parameter_stability,
    marginal_sensitivity,
    sensitivity_surface,
)
from fiboki.stats.stress import (
    StressCurve,
    end_date_stress,
    execution_delay_stress,
    missing_fill_stress,
    net_profit,
    random_deletion_stress,
    run_stress_suite,
    slippage_stress,
    spread_multiplier_stress,
    start_date_stress,
)

__all__ = [
    "BlockLengthEstimate",
    "BootstrapCI",
    "CVSplit",
    "CombinatorialPurgedCV",
    "EffectiveTrials",
    "LabelSpans",
    "MultipleTestResult",
    "PBOResult",
    "ParameterPoint",
    "PurgedKFold",
    "RealityCheckResult",
    "RuinSimulation",
    "SPAResult",
    "SharpeMoments",
    "StabilityReport",
    "StepMResult",
    "StressCurve",
    "analyse_parameter_stability",
    "benjamini_hochberg",
    "bonferroni",
    "bootstrap_confidence_interval",
    "combinatorially_symmetric_cv",
    "compare_sizing_modes",
    "deflated_sharpe_from_trials",
    "deflated_sharpe_ratio",
    "differentials_from_returns",
    "effective_trials_by_clustering",
    "end_date_stress",
    "execution_delay_stress",
    "expected_max_sharpe",
    "holm",
    "marginal_sensitivity",
    "minimum_track_record_length",
    "missing_fill_stress",
    "moving_block_bootstrap",
    "n_backtest_paths",
    "net_profit",
    "optimal_block_length",
    "pbo_null_expectation",
    "probabilistic_sharpe_ratio",
    "probability_of_backtest_overfitting",
    "random_deletion_stress",
    "reality_check",
    "resample_fixed_size",
    "resample_with_compounding",
    "run_stress_suite",
    "sensitivity_surface",
    "sharpe_moments",
    "slippage_stress",
    "spread_multiplier_stress",
    "start_date_stress",
    "stationary_bootstrap",
    "step_m",
    "superior_predictive_ability",
]
