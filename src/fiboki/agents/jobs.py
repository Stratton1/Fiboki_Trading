"""The deterministic worker: what actually runs when an agent queues a job.

Nothing in this module consults a language model.  A handler receives a
:class:`~fiboki.agents.orchestrator.JobContext` -- its recorded payload plus the
services injected at registration -- and returns a plain mapping.  Given the
same payload and the same data, it returns the same answer, which is what makes
a queued research result something you can audit six months later.

The division of labour the cardinal rule requires:

    agent   ->  "run a backtest of strategy X on EURUSD H1 from A to B"
    worker  ->  compiles the document, loads the versioned bars, runs the
                engine, computes the metrics, records the result

The agent's influence ends at the payload.  It cannot alter the engine, the
cost model, the metric definitions, or the verdict.

Known approximations, stated rather than hidden
-----------------------------------------------
* FX conversion is identity unless the account currency equals the instrument's
  quote currency.  A mismatch must be acknowledged explicitly in the payload,
  and the acknowledgement is recorded as a caveat on the result.
* ``run_sensitivity`` perturbs EXECUTION assumptions (spread, slippage, delay,
  missed fills, sample window), not strategy parameters.  DSL parameters are
  declarative and are not substituted into rules, so a parameter sweep would be
  measuring nothing; saying so is better than shipping a sweep that does not
  sweep.
* The validation gates use trade-level returns.  Trade returns are not
  calendar-spaced, so the Sharpe they produce is per-trade, not annualised, and
  the report says so.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from fiboki.agents.orchestrator import JobContext, JobType, Orchestrator
from fiboki.agents.tools import BarSource, ToolExecutionError
from fiboki.backtest.engine import (
    BacktestConfig,
    BarContext,
    FixedFractionalSizer,
    run_backtest,
)
from fiboki.backtest.exits import exit_policy_from_document
from fiboki.backtest.metrics import compute_metrics
from fiboki.core.contracts import Signal, Trade
from fiboki.core.enums import Provenance, Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.research.artefacts import (
    BacktestRecord,
    ResearchStore,
    ValidationReportRecord,
)
from fiboki.research.experiment import ExperimentLedger
from fiboki.research.structure import structure_hash
from fiboki.sim.profiles import get_profile
from fiboki.stats.bootstrap import bootstrap_confidence_interval
from fiboki.stats.sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    sharpe_moments,
)
from fiboki.stats.stress import net_profit, run_stress_suite, spread_multiplier_stress
from fiboki.strategy.compiler import CompiledStrategy, compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from fiboki.strategy.registry import StrategyRegistry
from fiboki.validation.gates import GATE_SET_V2

#: The promotion floor, taken from the platform's canonical gate set rather
#: than restated here. Restating it is how two numbers drift apart.
MIN_TRADES_FOR_PROMOTION = int(GATE_SET_V2.by_name("min_trades").threshold)


# ---------------------------------------------------------------------------
# The engine adapter
# ---------------------------------------------------------------------------


class CompiledStrategyRunner:
    """Adapts a compiled DSL document to the engine's ``Strategy`` protocol.

    Indicators are computed ONCE over each full frame at construction.  That is
    safe only because every indicator in the library is proved causal by
    ``tests/unit/test_indicator_causality.py``: the value on row i is identical
    whether it was computed over the whole frame or over ``frame[:i+1]``.
    Without that proof this would be look-ahead, so the shortcut is taken
    knowingly and cited, not assumed.
    """

    def __init__(
        self,
        compiled: CompiledStrategy,
        frames: Mapping[str, pd.DataFrame],
        timeframe: Timeframe,
    ) -> None:
        self.compiled = compiled
        self.timeframe = timeframe
        self._prepared: dict[str, pd.DataFrame] = {}
        self._index: dict[str, dict[pd.Timestamp, int]] = {}
        for symbol, frame in frames.items():
            prepared = compiled.prepare(frame)
            self._prepared[symbol] = prepared
            self._index[symbol] = {ts: i for i, ts in enumerate(prepared.index)}
        self.signals_emitted = 0

    def prepared(self, symbol: str) -> pd.DataFrame:
        """The indicator-bearing frame for one symbol.

        Exposed so a caller can hand the engine the SAME series the strategy
        reads -- a trailing stop must trail on the ATR of the bar it is
        trailing, not on one recomputed by a second code path that could drift.
        """
        return self._prepared[symbol]

    def on_bar(self, ctx: BarContext) -> Sequence[Signal]:
        out: list[Signal] = []
        for symbol in ctx.instruments:
            if symbol not in self._prepared:
                continue
            idx = self._index[symbol].get(ctx.timestamp)
            if idx is None:
                continue  # no bar for this instrument at exactly this timestamp
            signal = self.compiled.generate_signal(
                self._prepared[symbol], idx, instrument=symbol, timeframe=self.timeframe
            )
            if signal is not None:
                out.append(signal)
        self.signals_emitted += len(out)
        return tuple(out)


#: The account currency an agent job runs in when its payload names none: the
#: operator's account currency, the one research (``validation.run``) and paper
#: use, so an agent figure is comparable with both.
DEFAULT_ACCOUNT_CCY = "GBP"


def _fx_source(
    account_ccy: str, quote_ccy: str, acknowledged: bool, bars: Any = None
) -> tuple[Any, tuple[str, ...]]:
    if account_ccy.upper() == quote_ccy.upper():
        return IdentityFxSource(), ()
    # THE research conversion when the bar source can build it (daily crosses
    # from the same store the bars came from); never a guessed rate.
    resolver = getattr(bars, "fx_source", None)
    unavailable = ""
    if resolver is not None:
        try:
            fx, label = resolver(account_ccy, quote_ccy)
        except Exception as exc:  # FxSourceUnavailable names the pairs to ingest
            unavailable = f" ({type(exc).__name__}: {exc})"
        else:
            return fx, (
                f"FX: {quote_ccy.upper()}->{account_ccy.upper()} converted with {label}; "
                "rates are daily closes known at bar close (usually BID, about half a "
                "spread off mid).",
            )
    if not acknowledged:
        raise ValueError(
            f"account currency {account_ccy} differs from the quote currency "
            f"{quote_ccy} and no FX series is wired in{unavailable}. Either run in "
            f"{quote_ccy}, or set fx_approximation_acknowledged=true to record, in the "
            "result, that a 1.0 conversion was used deliberately."
        )
    return IdentityFxSource(allow_mismatch=True), (
        f"FX APPROXIMATION: {quote_ccy}->{account_ccy} was converted at 1.0. Every "
        "monetary figure in this result is denominated in {quote} and merely LABELLED "
        f"{account_ccy}.".replace("{quote}", quote_ccy),
    )


def _trade_payload(trade: Trade) -> dict[str, Any]:
    return {
        "trade_id": trade.trade_id,
        "instrument": trade.instrument,
        "direction": trade.direction.value,
        "size": trade.size,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "entry_time": str(trade.entry_time),
        "exit_time": str(trade.exit_time),
        "exit_reason": trade.exit_reason.value,
        "gross_pnl": trade.gross_pnl,
        "spread_cost": trade.spread_cost,
        "commission": trade.commission,
        "slippage_cost": trade.slippage_cost,
        "financing_cost": trade.financing_cost,
        "net_pnl": trade.net_pnl,
        "account_ccy": trade.account_ccy,
        "strategy_id": trade.strategy_id,
        "bars_held": trade.bars_held,
        "max_adverse_excursion": trade.max_adverse_excursion,
        "max_favourable_excursion": trade.max_favourable_excursion,
        "provenance": trade.provenance.value,
        "fx_rate_used": trade.fx_rate_used,
    }


def _equity_payload(curve: pd.DataFrame, max_points: int = 5000) -> tuple[dict[str, Any], ...]:
    """Store the equity curve, thinned deterministically if it is very long."""
    if len(curve) > max_points:
        step = len(curve) // max_points + 1
        keep = list(range(0, len(curve), step))
        if keep[-1] != len(curve) - 1:
            keep.append(len(curve) - 1)
        curve = curve.iloc[keep]
    return tuple(
        {
            "timestamp": str(ts),
            "equity": float(row["equity"]),
            "balance": float(row["balance"]),
            "open_positions": int(row["open_positions"]),
        }
        for ts, row in curve.iterrows()
    )


def _clean_metrics(metrics: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in metrics.as_dict().items():
        if isinstance(value, tuple | list):
            out[key] = list(value)
        elif isinstance(value, float) and not np.isfinite(value):
            out[key] = None
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Shared execution core (used by backtest, walkforward and ablation)
# ---------------------------------------------------------------------------


def _materialise(
    document: StrategyDocument, parameters: Mapping[str, Any] | None = None
) -> tuple[StrategyDocument, tuple[str, ...]]:
    """Bind a registered TEMPLATE so it can be compiled, and say what was used.

    A registered document declares parameters and references them; the compiler
    refuses one outright, because a strategy whose reported parameters and
    executed parameters can differ is unusable as evidence. Jobs therefore state
    the binding here, once, and the binding is returned as a caveat so it reaches
    the recorded result rather than living only in this function.
    """
    if not document.unbound_parameters():
        return document, ()
    overrides = dict(parameters or {})
    binding = {**document.default_values(), **overrides}
    bound = document.bind(binding)
    note = (
        "PARAMETER BINDING: "
        + ", ".join(f"{k}={binding[k]!r}" for k in sorted(binding))
        + (
            " (declared defaults)"
            if not overrides
            else f" (overridden: {sorted(overrides)})"
        )
    )
    return bound, (note,)


def _run_one(
    *,
    document: StrategyDocument,
    bars: BarSource,
    instrument: str,
    timeframe: Timeframe,
    start: str | None,
    end: str | None,
    initial_balance: float,
    risk_fraction: float,
    account_ccy: str,
    profile_name: str,
    fx_acknowledged: bool,
    strategy_id_override: str | None = None,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile, load, run, measure.  The one place the engine is invoked."""
    symbol = instrument.upper()
    if symbol not in document.universe:
        raise ValueError(
            f"{document.strategy_id}: {symbol} is not in the document's universe "
            f"{list(document.universe)}; running it anyway would test a strategy "
            "nobody wrote"
        )
    if timeframe not in document.timeframes:
        raise ValueError(
            f"{document.strategy_id}: {timeframe.value} is not a permitted timeframe "
            f"{[t.value for t in document.timeframes]}"
        )
    document, binding_caveats = _materialise(document, parameters)
    compiled = compile_strategy(document)
    frame, version_id = bars.load(symbol, timeframe, start=start, end=end)
    if len(frame) <= compiled.warmup_period:
        raise ValueError(
            f"{document.strategy_id} needs {compiled.warmup_period} warmup bars but the "
            f"window holds {len(frame)}; the result would be entirely warmup"
        )
    instrument_spec = get_instrument(symbol)
    fx, caveats = _fx_source(account_ccy, instrument_spec.quote, fx_acknowledged, bars)
    frames = {symbol: frame}
    runner = CompiledStrategyRunner(compiled, frames, timeframe)
    config = BacktestConfig(
        initial_balance=float(initial_balance),
        account_ccy=account_ccy.upper(),
        profile=get_profile(profile_name),
        max_concurrent=document.position_management.max_concurrent_positions,
        max_per_instrument=document.position_management.max_concurrent_positions,
        strategy_id=strategy_id_override or document.strategy_id,
        provenance=Provenance.BACKTEST,
    )
    # The document's FULL exit vocabulary -- scale-out legs, trailing stops,
    # breakeven, time stop, cooldown, reversal, event restrictions. Without this
    # the agent-facing backtest would honour only the stop and the first target
    # while `validation/engine_evaluator.py` honoured the whole document, and
    # the two would report different numbers for the same strategy.
    exit_policy = exit_policy_from_document(document)
    exit_series = {
        symbol: runner.prepared(symbol)[list(exit_policy.needs_series)]
        for symbol in frames
    } if exit_policy.needs_series else None
    result = run_backtest(
        data=frames,
        config=config,
        strategy=runner,
        sizer=FixedFractionalSizer(risk_fraction=float(risk_fraction)),
        fx=fx,
        exit_policy=exit_policy,
        exit_series=exit_series,
    )
    metrics = compute_metrics(
        trades=result.trades,
        equity_curve=result.equity_curve,
        initial_equity=config.initial_balance,
        strict=False,
    )
    return {
        "result": result,
        "metrics": _clean_metrics(metrics),
        "dataset_version_id": version_id,
        "caveats": (*binding_caveats, *caveats),
        "content_hash": document.content_hash(),
        "binding": dict(document.binding or {}),
        "signals_emitted": runner.signals_emitted,
        "n_bars": len(frame),
        "first_timestamp": str(frame.index[0]),
        "last_timestamp": str(frame.index[-1]),
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def backtest_handler(ctx: JobContext) -> Mapping[str, Any]:
    store: ResearchStore = ctx.service("research")
    strategies: StrategyRegistry = ctx.service("strategies")
    bars: BarSource = ctx.service("bars")
    payload = ctx.payload
    document = strategies.get(str(payload["strategy_id"]))
    run = _run_one(
        document=document,
        bars=bars,
        instrument=str(payload["instrument"]),
        timeframe=Timeframe(str(payload["timeframe"])),
        start=payload.get("start"),
        end=payload.get("end"),
        initial_balance=float(payload.get("initial_balance", 10_000.0)),
        risk_fraction=float(payload.get("risk_fraction", 0.01)),
        account_ccy=str(payload.get("account_ccy", DEFAULT_ACCOUNT_CCY)),
        profile_name=str(payload.get("profile", "ig_realistic")),
        fx_acknowledged=bool(payload.get("fx_approximation_acknowledged", False)),
        parameters=payload.get("parameters"),
    )
    result = run["result"]
    record = store.add_backtest(
        BacktestRecord(
            job_id=ctx.job_id,
            strategy_id=document.strategy_id,
            content_hash=run["content_hash"],
            experiment_id=payload.get("experiment_id"),
            instruments=(str(payload["instrument"]).upper(),),
            timeframe=str(payload["timeframe"]),
            dataset_version_ids=(run["dataset_version_id"],),
            config_fingerprint=_jsonable(result.config_fingerprint),
            data_fingerprint=_jsonable(result.data_fingerprint),
            # Which exit policy produced these numbers. The field existed and
            # nothing filled it, so every stored result claimed the default
            # policy by omission. It matters because the exit VOCABULARY has
            # since changed (``ExitReason.BREAKEVEN``): a reader comparing two
            # results needs to see that they were executed under different exit
            # rules, and an empty fingerprint is how a pre-stamp record is told
            # apart from one that genuinely ran with no trail and no breakeven.
            exit_policy_fingerprint=_jsonable(result.exit_policy_fingerprint),
            ledger_sha256=result.ledger_sha256(),
            metrics=run["metrics"],
            trades=tuple(_trade_payload(t) for t in result.trades),
            equity_curve=_equity_payload(result.equity_curve),
            rejections=dict(result.rejections),
            label=str(payload.get("label", "")),
            created_by="worker",
            role="deterministic_worker",
        )
    )
    return {
        "backtest_id": record.backtest_id,
        "strategy_id": document.strategy_id,
        "n_trades": len(result.trades),
        "signals_seen": result.signals_seen,
        "orders_submitted": result.orders_submitted,
        "bankrupt": result.bankrupt,
        "ledger_sha256": record.ledger_sha256,
        "metrics": run["metrics"],
        "caveats": list(run["caveats"]),
        "dataset_version_id": run["dataset_version_id"],
    }


def _jsonable(payload: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, Mapping):
            out[str(key)] = _jsonable(value)
        elif isinstance(value, tuple | list):
            out[str(key)] = [str(v) if not isinstance(v, int | float | str | bool) else v for v in value]
        elif isinstance(value, int | float | str | bool) or value is None:
            out[str(key)] = value
        else:
            out[str(key)] = str(value)
    return out


def _trade_returns(record: BacktestRecord, initial_equity: float) -> np.ndarray:
    """Per-trade returns on the running equity, in order.

    Using net P&L over the equity at the time of the trade -- rather than over
    a constant base -- is the honest version: a fixed-fraction sizer compounds,
    and a Sharpe computed on absolute P&L of a compounding account overstates
    the early sample.
    """
    equity = initial_equity
    out: list[float] = []
    for trade in record.trades:
        pnl = float(trade.get("net_pnl", 0.0))
        if equity <= 0:
            break
        out.append(pnl / equity)
        equity += pnl
    return np.array(out, dtype=float)


def _lo_null_sharpe_variance(moments: Any) -> float | None:
    """Null dispersion of a per-observation Sharpe on ``T`` observations (Lo 2002).

    ``var(SR_hat) = (1 - g3*SR + (g4 - 1)/4 * SR^2) / (T - 1)`` with non-excess
    kurtosis ``g4``. ``None`` when the bracket is not positive (the asymptotic
    expansion does not hold) or ``T < 2``.
    """
    t = int(moments.n_obs)
    if t < 2:
        return None
    sr, g3, g4 = float(moments.sr_hat), float(moments.skew), float(moments.kurtosis)
    bracket = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr
    if not np.isfinite(bracket) or bracket <= 0.0:
        return None
    return bracket / (t - 1)


def _ledger_trial_count(
    ctx: JobContext, record: BacktestRecord, *, declared: int
) -> tuple[dict[str, Any], list[str]]:
    """``N`` for deflation, from the experiment ledger. Never from the payload.

    The honest search size is the larger of the strategy FAMILY's trials (every
    reparameterisation of this rule structure, across campaigns) and the
    CAMPAIGN's trials (everything searched alongside it), when the backtest names
    an experiment that belongs to one. A payload ``n_trials_in_search`` larger
    than that is honoured -- it can only make deflation harder -- and recorded;
    a smaller one is ignored. With no ledger injected, or nothing in it for
    this scope, the count is unknown and the gate is NOT_EVALUATED.
    """
    notes: list[str] = []
    ledger = ctx.services.get("experiments")
    out: dict[str, Any] = {
        "known": False,
        "n_trials": 0,
        "source": "experiment_ledger",
        "declared_in_payload": int(declared),
    }
    if ledger is None:
        notes.append(
            "no experiment ledger was injected into the validation handler, so the "
            "trial count is unknown"
        )
        return out, notes
    counts: list[dict[str, Any]] = []
    try:
        strategies: StrategyRegistry = ctx.service("strategies")
        family = structure_hash(strategies.get(record.strategy_id))
    except Exception as exc:  # unknown strategy, or no registry
        family = ""
        notes.append(f"strategy family could not be resolved ({type(exc).__name__})")
    try:
        if family:
            counts.append(ledger.count_trials(structure_hash=family).to_dict())
        experiment_id = record.experiment_id or ctx.payload.get("experiment_id")
        if experiment_id:
            experiment = ledger.get(str(experiment_id))
            campaign = str((experiment.outputs or {}).get("campaign_id", "") or "")
            if campaign:
                counts.append(ledger.count_trials(campaign_id=campaign).to_dict())
    except Exception as exc:  # key-version mismatch, missing experiment
        notes.append(
            f"the experiment ledger could not answer the trial count "
            f"({type(exc).__name__}: {exc}); it is treated as unknown"
        )
        return out, notes
    known = [c for c in counts if c["n_experiments"] > 0]
    out["scopes"] = counts
    if not known:
        return out, notes
    n = max(int(c["n_trials"]) for c in known)
    if declared > n:
        notes.append(
            f"the payload declared a search of {declared} trials, more than the "
            f"{n} the ledger records; the larger figure is used, and the ledger "
            "is missing trials"
        )
        n = declared
    out.update({"known": True, "n_trials": int(n)})
    return out, notes


def validation_handler(ctx: JobContext) -> Mapping[str, Any]:
    """Evaluate the PLATFORM's promotion gates. The verdict is not an opinion.

    The thresholds are not defined here. They come from
    :data:`fiboki.validation.gates.GATE_SET_V2`, which is the single place the
    project states what a candidate must clear. This job computes the metrics
    it can honestly compute from one recorded backtest and hands them to that
    gate set.

    Everything it cannot compute from a single backtest -- walk-forward
    efficiency, PBO, SPA, StepM membership, parameter plateau -- is reported as
    NOT_EVALUATED, which BLOCKS promotion. That is deliberate: a gate nobody
    ran is not a gate that passed, and the full ladder in
    :mod:`fiboki.validation.ladder` is what produces those numbers.
    """
    store: ResearchStore = ctx.service("research")
    payload = ctx.payload
    record = store.get_backtest(str(payload["backtest_id"]))
    #: Recorded, and allowed only to RAISE the ledger's count (a declared search
    #: larger than the ledger knows about is information); never used alone.
    declared_trials = int(payload.get("n_trials_in_search", 0) or 0)
    seed = int(payload.get("seed", 0))
    n_boot = int(payload.get("bootstrap_samples", 500))

    initial_equity = float(record.metrics.get("initial_equity") or 0.0)
    if initial_equity <= 0 and record.equity_curve:
        initial_equity = float(record.equity_curve[0]["equity"])
    returns = _trade_returns(record, initial_equity or 1.0)

    #: Metrics fed to the gate set, keyed by the gate's metric name.
    gate_values: dict[str, float | None] = {"n_trades": float(record.n_trades)}
    #: Diagnostics that inform a reader but gate nothing.
    metrics: dict[str, Any] = {"n_trades": record.n_trades}
    caveats: list[str] = [
        "Sharpe here is PER TRADE, not annualised: trade returns are not "
        "calendar-spaced and annualising them would invent a frequency.",
        "One backtest cannot produce the walk-forward, PBO, SPA, StepM or "
        "parameter-plateau numbers. Those gates are NOT_EVALUATED, which blocks "
        "promotion; run the full validation ladder to fill them.",
    ]

    if returns.size >= 2 and float(np.std(returns, ddof=1)) > 0.0:
        moments = sharpe_moments(returns)
        metrics["sharpe_per_trade"] = round(moments.sr_hat, 8)
        metrics["n_observations"] = moments.n_obs
        metrics["skew"] = round(moments.skew, 8)
        metrics["kurtosis"] = round(moments.kurtosis, 8)
        psr = probabilistic_sharpe_ratio(
            moments.sr_hat, moments.n_obs, moments.skew, moments.kurtosis, 0.0
        )
        metrics["psr"] = round(psr, 8)

        trials, trial_notes = _ledger_trial_count(ctx, record, declared=declared_trials)
        caveats.extend(trial_notes)
        metrics["trial_count"] = trials
        n_trials = int(trials["n_trials"]) if trials.get("known") else 0
        null_variance = _lo_null_sharpe_variance(moments)
        metrics["null_sharpe_variance"] = (
            round(null_variance, 12) if null_variance is not None else None
        )
        metrics["null_sharpe_variance_source"] = "lo_2002_asymptotic"
        dsr: float | None
        if n_trials <= 0:
            dsr = None
            caveats.append(
                "the experiment ledger holds no trials for this strategy's family or "
                "campaign, so the size of the search is UNKNOWN and the deflated "
                "Sharpe is NOT_EVALUATED (it blocks). A payload trial count is never "
                "used: it is written by whoever wants the number."
            )
        elif null_variance is None:
            dsr = None
            caveats.append(
                "Lo's (2002) variance term is non-positive for these moments, so "
                "the null dispersion of the trial Sharpes cannot be estimated and "
                "the deflated Sharpe is NOT_EVALUATED"
            )
        elif n_trials == 1:
            dsr = psr
            caveats.append(
                "the ledger records exactly one trial for this family, so the "
                "deflated Sharpe equals the PSR"
            )
        else:
            # No cross-section of trial Sharpes is recorded, so the NULL
            # dispersion of a per-trade Sharpe estimated on T trades stands in:
            # var(SR_hat) = (1 - g3*SR + (g4-1)/4*SR^2) / (T-1) (Lo 2002). The
            # variance of per-TRADE RETURNS used before is ~0.01^2 at 1% risk,
            # which put the expected maximum of 1,000 trials at ~0.033 instead
            # of ~0.165 and reported a DSR of ~0.91 for a strategy whose true
            # figure was ~0.05 (audit P1-2).
            dsr = deflated_sharpe_ratio(
                moments.sr_hat, moments.n_obs, n_trials, null_variance,
                moments.skew, moments.kurtosis,
            )
            metrics["expected_max_sharpe_of_search"] = round(
                expected_max_sharpe(n_trials, null_variance), 8
            )
        metrics["deflated_sharpe_ratio"] = round(dsr, 8) if dsr is not None else None
        if dsr is not None:
            gate_values["deflated_sharpe_ratio"] = dsr

        ci = bootstrap_confidence_interval(
            returns, lambda a: float(np.mean(a)), n_boot=n_boot, alpha=0.05, rng=seed
        )
        metrics["mean_trade_return"] = round(float(np.mean(returns)), 10)
        metrics["mean_return_ci_lower"] = round(float(ci.lower), 10)
        metrics["mean_return_ci_upper"] = round(float(ci.upper), 10)
        metrics["mean_return_ci_excludes_zero"] = bool(ci.excludes_zero)
    else:
        caveats.append(
            "fewer than two trades with non-zero dispersion: the Sharpe-based "
            "metrics could not be computed and are left NOT_EVALUATED, which blocks"
        )

    # Cost robustness is the one robustness-rung number a single recorded
    # backtest CAN answer, because it reprices the trades that actually happened.
    if record.trades:
        trades = tuple(_rehydrate_trade(t) for t in record.trades)
        curve = spread_multiplier_stress(trades, (1.0, 2.0), net_profit, rng=seed)
        at_2x = float(curve.median[-1])
        metrics["net_profit_at_2x_spread"] = round(at_2x, 6)
        metrics["net_profit_baseline"] = round(float(curve.baseline), 6)
        gate_values["net_profit_at_2x_spread"] = at_2x
        if curve.notes:
            caveats.append(curve.notes)

    metrics["max_drawdown_pct_abs"] = round(
        abs(float(record.metrics.get("max_drawdown_pct") or 0.0)), 6
    )
    metrics["total_return_pct"] = record.metrics.get("total_return_pct")
    metrics["gross_pnl"] = record.metrics.get("gross_pnl")
    metrics["total_costs"] = record.metrics.get("total_costs")

    results = GATE_SET_V2.evaluate(gate_values)
    binding = GATE_SET_V2.binding_constraint(results)
    checks = tuple(
        {
            "name": r.gate.name,
            "metric": r.gate.metric,
            "passed": r.passed,
            "status": r.status.value,
            "value": r.value,
            "threshold": r.gate.threshold,
            "comparison": r.gate.comparison.symbol,
            "rung": r.gate.rung,
            "shortfall": r.shortfall,
            "detail": r.describe(),
        }
        for r in results
    )
    n_failed = sum(1 for r in results if not r.passed)
    if record.n_trades == 0:
        verdict = "inconclusive"
        caveats.append("the backtest produced no trades at all; there is nothing to validate")
    else:
        verdict = "pass" if n_failed == 0 else "fail"

    report = store.add_validation_report(
        ValidationReportRecord(
            job_id=ctx.job_id,
            strategy_id=record.strategy_id,
            backtest_id=record.backtest_id,
            experiment_id=payload.get("experiment_id"),
            kind="promotion_gates",
            checks=checks,
            metrics={
                **metrics,
                "gate_set_version": GATE_SET_V2.version,
                "gate_set_fingerprint": GATE_SET_V2.fingerprint(),
            },
            verdict=verdict,  # type: ignore[arg-type]
            caveats=tuple(caveats),
            created_by="worker",
            role="deterministic_worker",
        )
    )
    return {
        "report_id": report.report_id,
        "strategy_id": report.strategy_id,
        "backtest_id": report.backtest_id,
        "verdict": verdict,
        "gate_set_version": GATE_SET_V2.version,
        "n_checks": len(checks),
        "n_failed": n_failed,
        "failed_checks": list(report.failed_checks),
        "binding_constraint": binding.gate.name if binding else None,
        "binding_detail": binding.describe() if binding else "",
        "metrics": metrics,
    }


def walkforward_handler(ctx: JobContext) -> Mapping[str, Any]:
    """Sequential folds with an embargo, each run through the same engine."""
    store: ResearchStore = ctx.service("research")
    strategies: StrategyRegistry = ctx.service("strategies")
    bars: BarSource = ctx.service("bars")
    payload = ctx.payload
    document = strategies.get(str(payload["strategy_id"]))
    timeframe = Timeframe(str(payload["timeframe"]))
    symbol = str(payload["instrument"]).upper()
    n_folds = int(payload.get("n_folds", 4))
    embargo = int(payload.get("embargo_bars", 0))

    frame, _version = bars.load(
        symbol, timeframe, start=payload.get("start"), end=payload.get("end")
    )
    fold_parameters = payload.get("parameters")
    compiled = compile_strategy(_materialise(document, fold_parameters)[0])
    usable = len(frame) - compiled.warmup_period
    if usable <= n_folds * (embargo + 2):
        raise ValueError(
            f"{len(frame)} bars with a {compiled.warmup_period}-bar warmup cannot be "
            f"split into {n_folds} folds with a {embargo}-bar embargo"
        )
    edges = np.linspace(compiled.warmup_period, len(frame), n_folds + 1).astype(int)

    folds: list[dict[str, Any]] = []
    backtest_ids: list[str] = []
    for i in range(n_folds):
        lo = int(edges[i]) + (embargo if i > 0 else 0)
        hi = int(edges[i + 1]) - 1
        # Each fold is run over bars 0..hi so the indicators are warm, but the
        # fold's RESULT window starts at lo.  Restricting the engine's data
        # instead would leave the first fold's indicators unwarmed and make the
        # folds incomparable.
        fold_start = str(frame.index[max(0, lo - compiled.warmup_period)])
        fold_end = str(frame.index[hi])
        run = _run_one(
            document=document,
            bars=bars,
            instrument=symbol,
            timeframe=timeframe,
            start=fold_start,
            end=fold_end,
            initial_balance=float(payload.get("initial_balance", 10_000.0)),
            risk_fraction=float(payload.get("risk_fraction", 0.01)),
            account_ccy=str(payload.get("account_ccy", DEFAULT_ACCOUNT_CCY)),
            profile_name=str(payload.get("profile", "ig_realistic")),
            fx_acknowledged=bool(payload.get("fx_approximation_acknowledged", False)),
            parameters=payload.get("parameters"),
        )
        result = run["result"]
        record = store.add_backtest(
            BacktestRecord(
                job_id=ctx.job_id,
                strategy_id=document.strategy_id,
                content_hash=run["content_hash"],
                experiment_id=payload.get("experiment_id"),
                instruments=(symbol,),
                timeframe=timeframe.value,
                dataset_version_ids=(run["dataset_version_id"],),
                config_fingerprint=_jsonable(result.config_fingerprint),
                data_fingerprint=_jsonable(result.data_fingerprint),
                exit_policy_fingerprint=_jsonable(result.exit_policy_fingerprint),
                ledger_sha256=result.ledger_sha256(),
                metrics=run["metrics"],
                trades=tuple(_trade_payload(t) for t in result.trades),
                equity_curve=_equity_payload(result.equity_curve),
                rejections=dict(result.rejections),
                label=f"walkforward_fold_{i}",
                created_by="worker",
                role="deterministic_worker",
            )
        )
        backtest_ids.append(record.backtest_id)
        folds.append(
            {
                "fold": i,
                "backtest_id": record.backtest_id,
                "window": f"{fold_start}..{fold_end}",
                "n_trades": len(result.trades),
                "total_return_pct": run["metrics"].get("total_return_pct"),
                "sharpe": run["metrics"].get("sharpe"),
                "max_drawdown_pct": run["metrics"].get("max_drawdown_pct"),
            }
        )

    returns = [f["total_return_pct"] for f in folds if f["total_return_pct"] is not None]
    positive = sum(1 for r in returns if r > 0)
    return {
        "strategy_id": document.strategy_id,
        "n_folds": n_folds,
        "embargo_bars": embargo,
        "folds": folds,
        "backtest_ids": backtest_ids,
        "positive_folds": positive,
        "consistency": round(positive / len(returns), 6) if returns else None,
        "mean_fold_return_pct": round(float(np.mean(returns)), 6) if returns else None,
        "stdev_fold_return_pct": (
            round(float(np.std(returns, ddof=1)), 6) if len(returns) > 1 else None
        ),
        "caveats": [
            "folds share the warmup bars that precede them, so they are sequential "
            "out-of-sample windows, not independent samples",
            "each fold restarts from the initial balance, so fold returns are "
            "comparable to each other but do not compound into the headline figure",
        ],
    }


#: Which document component each ablation removes, and how.
_ABLATIONS: dict[str, str] = {
    "regime": "remove every regime gate",
    "filters": "remove every filter rule",
    "confirmation": "remove the confirmation rule set",
    "trailing": "remove the trailing model",
    "take_profits": "remove every take-profit leg",
    "sessions": "remove the session restriction",
}


def _ablate(document: StrategyDocument, component: str) -> StrategyDocument | None:
    data = document.model_dump(mode="json")
    data.pop("complexity_score", None)
    if component == "regime":
        if not data.get("regime"):
            return None
        data["regime"] = []
    elif component == "filters":
        if not data.get("filters"):
            return None
        data["filters"] = []
    elif component == "confirmation":
        confirmation = data.get("confirmation") or {}
        if not (confirmation.get("long") or confirmation.get("short")):
            return None
        data["confirmation"] = {"long": [], "short": []}
    elif component == "trailing":
        if data.get("trailing") is None:
            return None
        data["trailing"] = None
    elif component == "take_profits":
        if not data.get("take_profits"):
            return None
        data["take_profits"] = []
    elif component == "sessions":
        if data.get("sessions") is None:
            return None
        data["sessions"] = None
    else:  # pragma: no cover - the caller validates
        raise ValueError(f"unknown ablation component {component!r}")
    data["strategy_id"] = f"{document.strategy_id}_ablate_{component}"[:64]
    data["name"] = f"{document.name} (-{component})"[:120]
    data["parent_strategy_ids"] = [document.strategy_id]
    return StrategyDocument.model_validate(data)


def ablation_handler(ctx: JobContext) -> Mapping[str, Any]:
    """Re-run with each component removed.  Which ones actually carry the result?"""
    strategies: StrategyRegistry = ctx.service("strategies")
    bars: BarSource = ctx.service("bars")
    payload = ctx.payload
    document = strategies.get(str(payload["strategy_id"]))
    timeframe = Timeframe(str(payload["timeframe"]))
    components = tuple(payload.get("components") or tuple(_ABLATIONS))

    def _run_variant(
        doc: StrategyDocument, *, strategy_id_override: str | None = None
    ) -> dict[str, Any]:
        """Every arm of the ablation runs under IDENTICAL settings but the doc."""
        return _run_one(
            document=doc,
            bars=bars,
            instrument=str(payload["instrument"]),
            timeframe=timeframe,
            start=payload.get("start"),
            end=payload.get("end"),
            initial_balance=float(payload.get("initial_balance", 10_000.0)),
            risk_fraction=float(payload.get("risk_fraction", 0.01)),
            account_ccy=str(payload.get("account_ccy", DEFAULT_ACCOUNT_CCY)),
            profile_name=str(payload.get("profile", "ig_realistic")),
            fx_acknowledged=bool(payload.get("fx_approximation_acknowledged", False)),
            parameters=payload.get("parameters"),
            strategy_id_override=strategy_id_override,
        )

    baseline = _run_variant(document)
    base_metrics = baseline["metrics"]
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for component in components:
        if component not in _ABLATIONS:
            raise ValueError(f"unknown ablation component {component!r}")
        variant = _ablate(document, component)
        if variant is None:
            skipped.append(component)
            continue
        run = _run_variant(variant, strategy_id_override=variant.strategy_id)
        rows.append(
            {
                "component": component,
                "description": _ABLATIONS[component],
                "n_trades": len(run["result"].trades),
                "total_return_pct": run["metrics"].get("total_return_pct"),
                "delta_return_pct": _delta(
                    run["metrics"].get("total_return_pct"),
                    base_metrics.get("total_return_pct"),
                ),
                "sharpe": run["metrics"].get("sharpe"),
                "delta_sharpe": _delta(
                    run["metrics"].get("sharpe"), base_metrics.get("sharpe")
                ),
                "max_drawdown_pct": run["metrics"].get("max_drawdown_pct"),
            }
        )
    return {
        "strategy_id": document.strategy_id,
        "baseline": {
            "n_trades": len(baseline["result"].trades),
            "total_return_pct": base_metrics.get("total_return_pct"),
            "sharpe": base_metrics.get("sharpe"),
            "max_drawdown_pct": base_metrics.get("max_drawdown_pct"),
        },
        "ablations": rows,
        "skipped": skipped,
        "caveats": [
            "removing a component changes the trade count, so a delta in a "
            "trade-count-sensitive metric is partly a sample-size effect",
            "an ablation that IMPROVES the result is evidence the component was "
            "fitted, not that it should simply be deleted",
            *list(baseline["caveats"]),
        ],
    }


def _delta(value: Any, base: Any) -> float | None:
    if value is None or base is None:
        return None
    return round(float(value) - float(base), 8)


def sensitivity_handler(ctx: JobContext) -> Mapping[str, Any]:
    """Execution-assumption sensitivity: how hard do you have to push to break it?"""
    store: ResearchStore = ctx.service("research")
    payload = ctx.payload
    record = store.get_backtest(str(payload["backtest_id"]))
    if not record.trades:
        raise ValueError(
            f"{record.backtest_id} has no trades; there is nothing to stress"
        )
    trades = tuple(_rehydrate_trade(t) for t in record.trades)
    curves = run_stress_suite(
        trades,
        net_profit,
        n_samples=int(payload.get("n_samples", 200)),
        rng=int(payload.get("seed", 0)),
    )
    rows: list[dict[str, Any]] = []
    for name in sorted(curves):
        curve = curves[name]
        rows.append(
            {
                "stress": name,
                "level_name": curve.level_name,
                "levels": [float(v) for v in curve.levels],
                "median": [float(v) for v in curve.median],
                "retention": [
                    None if not np.isfinite(v) else round(float(v), 6)
                    for v in curve.retention
                ],
                "baseline": float(curve.baseline),
                "breaking_level": curve.breaking_level,
                "notes": curve.notes,
            }
        )
    broken = [r["stress"] for r in rows if r["breaking_level"] is not None]
    return {
        "backtest_id": record.backtest_id,
        "strategy_id": record.strategy_id,
        "metric": "net_profit",
        "curves": rows,
        "stresses_that_break_it": broken,
        "caveats": [
            "this sweeps EXECUTION assumptions (spread, slippage, delay, missed "
            "fills, sample window), not strategy parameters: DSL parameters are "
            "declarative and are not substituted into rules, so a parameter sweep "
            "here would measure nothing",
            "stresses are applied to the recorded trades; they cannot capture a "
            "strategy that would have taken DIFFERENT trades under the new "
            "assumptions",
        ],
    }


def _rehydrate_trade(payload: Mapping[str, Any]) -> Trade:
    from fiboki.core.enums import Direction, ExitReason

    return Trade(
        instrument=str(payload["instrument"]),
        direction=Direction(str(payload["direction"])),
        size=float(payload["size"]),
        entry_price=float(payload["entry_price"]),
        exit_price=float(payload["exit_price"]),
        entry_time=pd.Timestamp(str(payload["entry_time"])),
        exit_time=pd.Timestamp(str(payload["exit_time"])),
        exit_reason=ExitReason(str(payload["exit_reason"])),
        gross_pnl=float(payload["gross_pnl"]),
        spread_cost=float(payload["spread_cost"]),
        commission=float(payload["commission"]),
        slippage_cost=float(payload["slippage_cost"]),
        financing_cost=float(payload["financing_cost"]),
        net_pnl=float(payload["net_pnl"]),
        account_ccy=str(payload["account_ccy"]),
        strategy_id=str(payload.get("strategy_id", "")),
        bars_held=int(payload.get("bars_held", 0)),
        max_adverse_excursion=float(payload.get("max_adverse_excursion", 0.0)),
        max_favourable_excursion=float(payload.get("max_favourable_excursion", 0.0)),
        provenance=Provenance(str(payload.get("provenance", "backtest"))),
        fx_rate_used=float(payload.get("fx_rate_used", 1.0)),
    )


def data_quality_scan_handler(ctx: JobContext) -> Mapping[str, Any]:
    """Scan a dataset for defects.  Detects; never repairs."""
    from fiboki.data.integrity import IntegrityConfig
    from fiboki.data.integrity import validate as validate_integrity

    bars: BarSource = ctx.service("bars")
    payload = ctx.payload
    frame, version_id = bars.load(
        str(payload["instrument"]),
        Timeframe(str(payload["timeframe"])),
        start=payload.get("start"),
        end=payload.get("end"),
    )
    report = validate_integrity(frame, config=IntegrityConfig())
    return {
        "instrument": str(payload["instrument"]).upper(),
        "timeframe": str(payload["timeframe"]),
        "dataset_version_id": version_id,
        "quality": report.quality.value,
        "is_clean": report.is_clean,
        "worst_severity": report.worst_severity.value,
        "row_count": report.row_count,
        "defects": [d.to_dict() for d in report.defects],
        "n_gaps": len(report.gaps),
    }


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

HANDLERS: dict[JobType, Any] = {
    JobType.BACKTEST: backtest_handler,
    JobType.VALIDATION: validation_handler,
    JobType.WALKFORWARD: walkforward_handler,
    JobType.ABLATION: ablation_handler,
    JobType.SENSITIVITY: sensitivity_handler,
    JobType.DATA_QUALITY_SCAN: data_quality_scan_handler,
}


def register_research_handlers(
    orchestrator: Orchestrator,
    *,
    research: ResearchStore,
    strategies: StrategyRegistry,
    bars: BarSource | None = None,
    job_types: Sequence[JobType] | None = None,
    experiments: ExperimentLedger | None = None,
) -> None:
    """Wire the deterministic handlers into an orchestrator.

    Called by the platform at start-up, never by an agent.  An agent can submit
    a job only for a type that has been registered here, so the set of things
    the research fleet can cause to happen is fixed at wiring time.
    """
    services = {
        "research": research,
        "strategies": strategies,
        "bars": bars,
        # The trial count for deflation. Without it the validation handler's
        # DSR is NOT_EVALUATED, which blocks: it will not deflate by a guess.
        "experiments": experiments,
    }
    for job_type in job_types or tuple(HANDLERS):
        handler = HANDLERS.get(job_type)
        if handler is None:
            raise ToolExecutionError(f"no deterministic handler for {job_type.value}")
        orchestrator.register_handler(job_type, handler, services=services)


__all__ = [
    "HANDLERS",
    "MIN_TRADES_FOR_PROMOTION",
    "CompiledStrategyRunner",
    "ablation_handler",
    "backtest_handler",
    "data_quality_scan_handler",
    "register_research_handlers",
    "sensitivity_handler",
    "validation_handler",
    "walkforward_handler",
]
