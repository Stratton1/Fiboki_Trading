"""One entry point: strategy document + bars + broker profile -> ValidationReport.

Everything the ladder needs is assembled here, in one place, so that running a
candidate against the real engine on real data is a single call with no room for
the caller to wire two of the pieces together inconsistently.

What this module refuses to let a caller do
-------------------------------------------
* **Run a strategy on a holdout that was never defined.** The segment is defined
  from the bars' own date range before anything runs, and defining it twice with
  different facts raises inside the registry.
* **Run a template.** The evaluator binds every grid point before compiling, so
  the thing that executes always has a complete, recorded binding.
* **Lose the provenance.** The dataset version id, the engine config
  fingerprint, the broker profile and the gate-set version all reach the report,
  and the same fingerprint is on every individual evaluation.
* **Silently convert currency.** An FX source must be supplied whenever the
  account currency differs from the instrument's quote currency; there is no
  1.0 default.

What it does NOT do
-------------------
It does not decide anything. The verdict is the ladder's and the gates', and a
rejection is returned as a report, not raised: a rejection is a research result
and the record of it is the point.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import FxRateSource, IdentityFxSource
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import (
    EngineEvaluator,
    EvaluationCache,
    EvaluatorConfig,
)
from fiboki.validation.gates import GATE_SET_V2, GateSet
from fiboki.validation.holdout import DEFAULT_HOLDOUT_FRACTION, HoldoutRegistry
from fiboki.validation.ladder import LadderConfig, ValidationLadder
from fiboki.validation.report import ValidationReport

__all__ = ["ValidationRun", "run_validation"]


@dataclass(frozen=True, slots=True)
class ValidationRun:
    """The report, plus the machinery that produced it.

    Returned rather than just the report so a caller can inspect the evaluator's
    cache statistics, count how many engine runs a campaign actually cost, and
    read back the exact grid that was swept.
    """

    report: ValidationReport
    evaluator: EngineEvaluator = field(repr=False)
    registry: HoldoutRegistry = field(repr=False)
    candidate: Any = field(repr=False)

    @property
    def verdict(self) -> Any:
        return self.report.verdict

    def summary(self) -> str:
        return self.report.summary()


def run_validation(
    *,
    document: StrategyDocument,
    bars: pd.DataFrame,
    dataset_version_id: str,
    instrument: str,
    timeframe: Timeframe | str,
    registry: HoldoutRegistry,
    profile_name: str = "IG_REALISTIC",
    account_ccy: str = "USD",
    fx: FxRateSource | None = None,
    fx_label: str = "",
    initial_balance: float = 10_000.0,
    risk_fraction: float = 0.01,
    gate_set: GateSet = GATE_SET_V2,
    ladder_config: LadderConfig | None = None,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    max_grid_points: int = 32,
    max_values_per_axis: int = 3,
    sweep_parameters: Iterable[str] | None = None,
    cache_dir: str | Path | None = None,
    evaluator_overrides: Mapping[str, Any] | None = None,
    experiment_id: str = "",
    actor: str = "",
    notes: str = "",
) -> ValidationRun:
    """Run every rung of the ladder against the real engine and return the report.

    ``registry`` is passed in rather than created here on purpose: the holdout
    ledger is the one piece of state that MUST outlive a single run. A registry
    created inside this function would give every process a fresh, unspent
    holdout, which is exactly the property the registry exists to deny.
    """
    tf = Timeframe(timeframe) if not isinstance(timeframe, Timeframe) else timeframe
    symbol = instrument.upper()
    spec = get_instrument(symbol)

    if fx is None:
        if spec.quote.upper() != account_ccy.upper():
            raise ValueError(
                f"{symbol} is quoted in {spec.quote} but the account currency is "
                f"{account_ccy} and no FX source was supplied. Pass one, or run in "
                f"{spec.quote}. A 1.0 conversion here would mis-state every "
                "monetary figure in the report by the exchange rate -- the exact "
                "V1 defect fiboki.core.money exists to prevent."
            )
        fx = IdentityFxSource()
        fx_label = fx_label or f"identity({spec.quote}=={account_ccy.upper()})"

    config = EvaluatorConfig(
        instrument=symbol,
        timeframe=tf,
        initial_balance=initial_balance,
        account_ccy=account_ccy,
        risk_fraction=risk_fraction,
        profile_name=profile_name,
        **dict(evaluator_overrides or {}),
    )
    evaluator = EngineEvaluator(
        document=document,
        frame=bars,
        dataset_version_id=dataset_version_id,
        config=config,
        fx=fx,
        fx_label=fx_label or type(fx).__name__,
        cache=EvaluationCache(cache_dir),
    )

    # The segment is fixed from the DATA, before a single evaluation runs, and
    # the registry refuses to redefine it later with different facts.
    registry.define(
        dataset_version_id,
        data_start=bars.index[0],
        data_end=bars.index[-1],
        holdout_fraction=holdout_fraction,
        label=f"{symbol} {tf.value}",
    )

    candidate = evaluator.candidate(
        max_points=max_grid_points,
        max_values_per_axis=max_values_per_axis,
        include=sweep_parameters,
    )
    ladder = ValidationLadder(
        config=ladder_config or LadderConfig(min_trades=int(gate_set.by_name("min_trades").threshold)),
        gate_set=gate_set,
    )
    report = ladder.run(
        candidate,
        evaluator,
        registry=registry,
        dataset_version_id=dataset_version_id,
        engine_config=evaluator.engine_fingerprint(),
        broker_profile=config.profile.fingerprint(),
        experiment_id=experiment_id,
        actor=actor,
        notes=notes,
    )
    return ValidationRun(
        report=report, evaluator=evaluator, registry=registry, candidate=candidate
    )
