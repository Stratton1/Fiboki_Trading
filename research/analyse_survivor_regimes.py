"""Per-regime breakdown of a campaign survivor. Scepticism, not celebration.

Usage::

    python research/analyse_survivor_regimes.py \\
        --ledger    <the campaign's experiments.sqlite> \\
        --experiment-id <the survivor cell's experiment id> \\
        --data-root <the fiboki data root> \\
        --out       research/reports/campaign_k2_multi_instrument

The campaign writes no per-cell report files: every ``ValidationReport`` and the
document that produced it live in the campaign's experiment ledger, which is
what makes the record append-only. This script reads from there.

Why this exists
---------------
V1's headline result was a USDJPY system whose entire edge, on inspection, sat
in one directional regime over one stretch of history. A strategy that only
works when the market trends one way has not found an edge; it has found the
sample. ``regime_segmented_performance`` is the direct test, and this script
runs it against the survivor's ACTUAL trades from the ACTUAL selected binding,
over the research window only -- never the holdout.

What it deliberately does not report
------------------------------------
A per-regime Sharpe. Sharpe needs an equity curve and a time base; computing one
from a bag of trade P&Ls is exactly the inflation V1 shipped.
``pnl_tstat`` answers the question people use Sharpe for here and degrades
honestly on small samples, and ``sufficient`` says outright when a bucket has
too few trades to mean anything.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource, SeriesFxSource
from fiboki.data.schema import DatasetKind
from fiboki.data.store import DataStore
from fiboki.marketstate.features import FeatureEngine
from fiboki.marketstate.regime import (
    RegimeAxis,
    RegimeClassifier,
    regime_segmented_performance,
)
from fiboki.research.experiment import ExperimentLedger
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import EngineEvaluator, EvaluatorConfig
from fiboki.validation.evaluation import DateWindow

FX_SERIES_FOR = {
    "JPY": "USDJPY",
    "CHF": "USDCHF",
    "CAD": "USDCAD",
    "GBP": "GBPUSD",
    "EUR": "EURUSD",
}

AXES = (
    RegimeAxis.DIRECTION,
    RegimeAxis.VOLATILITY,
    RegimeAxis.PERSISTENCE,
    RegimeAxis.LIQUIDITY,
    RegimeAxis.STRESS,
)


def _fx_for(store: DataStore, instrument: str, timeframe: Timeframe, account_ccy: str):
    quote = get_instrument(instrument.upper()).quote.upper()
    if quote == account_ccy.upper():
        return IdentityFxSource(), f"identity({quote}=={account_ccy.upper()})"
    pair = FX_SERIES_FOR[quote]
    frame, _ = store.read_latest(pair, timeframe, kind=DatasetKind.VALIDATED)
    return (
        SeriesFxSource(series={pair: frame["close"].astype(float)}),
        f"SeriesFxSource({pair}->{quote}; bid closes, HistData {timeframe.value})",
    )


def analyse(ledger_path: Path, experiment_id: str, data_root: Path) -> dict[str, Any]:
    with ExperimentLedger(ledger_path) as ledger:
        experiment = ledger.get(experiment_id)
        report = experiment.validation_report
        if report is None:
            raise SystemExit(
                f"experiment {experiment_id} holds no validation report; it is not "
                "a candidate that was taken through the ladder"
            )
        if experiment.strategy_document is None:
            raise SystemExit(
                f"experiment {experiment_id} holds no strategy document, so the "
                "binding that produced these trades cannot be rebuilt. Refusing to "
                "guess it."
            )
        document = StrategyDocument.model_validate(experiment.strategy_document)
        outputs = dict(experiment.outputs or {})

    engine_config = dict(report.engine_config or {})
    evaluator_cfg = dict(engine_config.get("evaluator") or {})
    instrument = str(evaluator_cfg.get("instrument") or "").upper()
    timeframe = Timeframe(str(evaluator_cfg.get("timeframe")))
    account_ccy = str(evaluator_cfg.get("account_ccy") or "USD").upper()
    if not instrument:
        raise SystemExit(
            "the report does not name the instrument it ran on; refusing to assume one"
        )

    store = DataStore(data_root)
    bars, version = store.read_latest(instrument, timeframe, kind=DatasetKind.VALIDATED)
    fx, fx_label = _fx_for(store, instrument, timeframe, account_ccy)

    windows = dict(report.windows or {})
    research = dict(windows.get("research") or {})
    if not research:
        raise SystemExit("the report records no research window; refusing to invent one")
    research_start = pd.Timestamp(research["start"])
    research_end = pd.Timestamp(research["end"])
    window = DateWindow(
        name=str(research.get("name") or f"research::{version.version_id}"),
        start=research_start,
        end=research_end,
    )
    selected, selection_source = _selected_parameters(report, document)

    evaluator = EngineEvaluator(
        document=document,
        frame=bars,
        dataset_version_id=str(version.version_id),
        config=EvaluatorConfig(
            instrument=instrument,
            timeframe=timeframe,
            account_ccy=account_ccy,
            initial_balance=float(evaluator_cfg.get("initial_balance", 10_000.0)),
            risk_fraction=float(evaluator_cfg.get("risk_fraction", 0.01)),
            profile_name=str(evaluator_cfg.get("profile_name", "IG_REALISTIC")),
        ),
        fx=fx,
        fx_label=fx_label,
    )
    evaluation = evaluator(selected, window)
    trades = list(evaluation.trades)

    research_bars = bars.loc[(bars.index >= research_start) & (bars.index <= research_end)]
    # KNOWN INTEROP GAP, worked around here rather than silently: the data store
    # returns a datetime64[us, UTC] index (pyarrow's parquet precision) while
    # Trade timestamps are datetime64[ns, UTC], and RegimeSeries.segment_trades
    # joins the two with pd.merge_asof, which refuses mismatched resolutions.
    # Normalising to ns changes no value -- H4 bar starts carry no sub-second
    # component -- but it is a conversion, so it is named.
    research_bars = research_bars.copy()
    research_bars.index = pd.DatetimeIndex(research_bars.index).as_unit("ns")
    features = FeatureEngine(
        timeframe=timeframe, instrument=instrument, on_invalid_bars="drop"
    ).compute(research_bars)
    regimes = RegimeClassifier().classify(features)

    per_axis: dict[str, Any] = {}
    for axis in AXES:
        table = regime_segmented_performance(trades, regimes, axis=axis, min_trades=30)
        per_axis[axis.value] = (
            json.loads(table.reset_index().to_json(orient="records"))
            if len(table)
            else []
        )
    full_key = regime_segmented_performance(trades, regimes, axis=None, min_trades=30)

    store.close()
    return {
        "experiment_id": experiment_id,
        "strategy_id": report.strategy_id,
        "strategy_content_hash": report.strategy_content_hash,
        "instrument": instrument,
        "timeframe": timeframe.value,
        "dataset_version_id": str(version.version_id),
        "account_ccy": account_ccy,
        "fx_label": fx_label,
        "campaign_outputs": outputs,
        "selected_parameters": selected,
        "selected_parameters_source": selection_source,
        "research_window": {"start": str(research_start), "end": str(research_end)},
        "holdout_touched": False,
        "n_trades_reproduced": len(trades),
        "net_profit_reproduced": float(evaluation.net_profit),
        "per_bar_sharpe_reproduced": float(evaluation.sharpe),
        "regime_fingerprint": regimes.fingerprint,
        "per_axis": per_axis,
        "full_regime_key": (
            json.loads(full_key.reset_index().to_json(orient="records"))
            if len(full_key)
            else []
        ),
    }


def _selected_parameters(report: Any, document: StrategyDocument) -> tuple[dict[str, Any], str]:
    """The binding the ladder actually carried forward, and where it came from.

    Preference order, latest decision first: the walk-forward rung's
    ``selected_params`` (the binding a survivor is promoted WITH), then rung 1's
    ``best_params`` (the in-sample winner), then the document's declared
    defaults. The source is returned alongside the binding because a regime
    breakdown of the wrong parameterisation is worse than none: it would look
    like evidence about a strategy nobody selected.
    """
    for rung in report.rungs:
        metrics = dict(getattr(rung, "metrics", {}) or {})
        value = metrics.get("selected_params")
        if isinstance(value, dict) and value:
            return dict(value), f"rung {rung.name}: selected_params"
    for rung in report.rungs:
        metrics = dict(getattr(rung, "metrics", {}) or {})
        value = metrics.get("best_params")
        if isinstance(value, dict) and value:
            return dict(value), f"rung {rung.name}: best_params"
    return dict(document.default_values()), "document declared defaults (no rung recorded a selection)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    result = analyse(args.ledger, args.experiment_id, args.data_root)
    args.out.mkdir(parents=True, exist_ok=True)
    name = f"regime_breakdown__{result['strategy_id']}__{result['instrument']}.json"
    path = args.out / name
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print(
        f"{result['strategy_id']} on {result['instrument']} {result['timeframe']}: "
        f"{result['n_trades_reproduced']} trades reproduced over the research window, "
        f"net {result['net_profit_reproduced']:.2f} {result['account_ccy']}"
    )
    for axis, rows in result["per_axis"].items():
        print(f"\n-- {axis} --")
        for row in rows:
            label = row.get(f"regime_{axis}") or row.get("regime_key") or row.get("index")
            print(
                f"   {label!s:16s} n={row['n_trades']:>4} "
                f"net={row['net_pnl']:>12.2f} mean={row['mean_pnl']:>9.2f} "
                f"win={row['win_rate']:.2f} t={row['pnl_tstat'] if row['pnl_tstat'] is None else round(row['pnl_tstat'],2)} "
                f"pnl_share={row['pnl_share']:.2f} sufficient={row['sufficient']}"
            )
    print(f"\n  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
