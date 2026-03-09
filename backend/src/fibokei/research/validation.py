"""Validation rerun on shortlisted strategy-instrument-timeframe combinations.

Supports re-testing top-ranked combinations on the same or a different
data provider. Architecture is ready for future alternate-provider
validation (e.g. Dukascopy cross-check).
"""

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.data.providers.registry import load_canonical
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.strategies.registry import strategy_registry


class ValidationStatus(str, Enum):
    """Validation lifecycle status."""

    PENDING = "pending"
    VALIDATED_SAME_SOURCE = "validated_same_source"
    VALIDATED_ALTERNATE = "validated_alternate"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ValidationResult:
    """Result of a validation rerun for a single combination."""

    strategy_id: str
    instrument: str
    timeframe: str
    # Original ranking result
    original_score: float = 0.0
    original_trades: int = 0
    original_net_profit: float = 0.0
    original_sharpe: float = 0.0
    # Validation rerun result
    validation_score: float = 0.0
    validation_trades: int = 0
    validation_net_profit: float = 0.0
    validation_sharpe: float = 0.0
    validation_provider: str | None = None
    validation_metrics: dict = field(default_factory=dict)
    # Divergence
    score_divergence: float = 0.0  # original - validation
    passed: bool = False  # validation score >= 50% of original
    validation_status: str = ValidationStatus.PENDING.value
    status: str = "ok"


@dataclass
class ValidationBatchResult:
    """Result of validating multiple shortlisted combinations."""

    total_validated: int = 0
    total_passed: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    results: list[ValidationResult] = field(default_factory=list)
    pass_rate: float = 0.0


def run_validation_rerun(
    shortlist: list[dict],
    config: BacktestConfig | None = None,
    scoring_config: ScoringConfig | None = None,
    validation_provider: str | None = None,
) -> ValidationBatchResult:
    """Re-run top-ranked combinations as a validation pass.

    Each item in shortlist should have:
        strategy_id, instrument, timeframe,
        original_score, original_trades, original_net_profit, original_sharpe

    If validation_provider is specified, data is loaded from that provider.
    Otherwise uses the default provider search order (same source).
    """
    config = config or BacktestConfig()
    scoring_config = scoring_config or ScoringConfig()

    batch = ValidationBatchResult()

    for item in shortlist:
        sid = item["strategy_id"]
        inst = item["instrument"]
        tf_str = item["timeframe"]

        vr = ValidationResult(
            strategy_id=sid,
            instrument=inst,
            timeframe=tf_str,
            original_score=item.get("original_score", 0.0),
            original_trades=item.get("original_trades", 0),
            original_net_profit=item.get("original_net_profit", 0.0),
            original_sharpe=item.get("original_sharpe", 0.0),
            validation_provider=validation_provider,
        )

        # Load data
        df = load_canonical(inst, tf_str, provider=validation_provider)
        if df is None:
            vr.validation_status = ValidationStatus.SKIPPED.value
            vr.status = "no data available"
            batch.total_skipped += 1
            batch.results.append(vr)
            continue

        df["instrument"] = inst
        df["timeframe"] = tf_str

        try:
            tf_enum = Timeframe(tf_str)
            strategy = strategy_registry.get(sid)
            bt = Backtester(strategy, config)
            bt_result = bt.run(df, inst, tf_enum)
            metrics = compute_metrics(bt_result)
            metrics["equity_curve"] = bt_result.equity_curve
            metrics["initial_capital"] = config.initial_capital
            score = compute_composite_score(metrics, scoring_config)

            vr.validation_score = score
            vr.validation_trades = metrics.get("total_trades", 0)
            vr.validation_net_profit = metrics.get("total_net_profit", 0.0)
            vr.validation_sharpe = metrics.get("sharpe_ratio", 0.0) or 0.0
            vr.validation_metrics = metrics
            vr.score_divergence = round(vr.original_score - score, 4)
            vr.passed = score >= (vr.original_score * 0.5) if vr.original_score > 0 else score > 0

            if validation_provider:
                vr.validation_status = ValidationStatus.VALIDATED_ALTERNATE.value
            else:
                vr.validation_status = ValidationStatus.VALIDATED_SAME_SOURCE.value

            if vr.passed:
                batch.total_passed += 1
            else:
                vr.validation_status = ValidationStatus.FAILED.value
                batch.total_failed += 1

        except Exception as e:
            vr.validation_status = ValidationStatus.FAILED.value
            vr.status = f"error: {e}"
            batch.total_failed += 1

        batch.results.append(vr)

    batch.total_validated = len(batch.results) - batch.total_skipped
    if batch.total_validated > 0:
        batch.pass_rate = round(batch.total_passed / batch.total_validated, 4)

    return batch
