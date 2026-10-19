"""Re-running a stored run's binding through the real engine, with one thing changed.

Every re-run diagnostic (warm-up, execution stress, parameter perturbation,
price noise) asks the same question: *what does THIS binding do over THIS
research window when one input moves?* :class:`BindingExecutor` answers it with
:class:`~fiboki.validation.engine_evaluator.EngineEvaluator`, the ladder's own
adapter onto :func:`fiboki.backtest.engine.run_backtest`, so a diagnostic and
the ladder cannot disagree about how a binding is executed. It adds nothing to
the engine; it chooses the bars, the profile and the parameters it is handed.

The binding
-----------
:class:`RunBinding` is everything needed to re-run a stored run: the strategy,
instrument, timeframe, the research window (its own first bar to the holdout
boundary or its own last bar, whichever is earlier), account, sizing, cost
profile, construction, and the FULL parameter binding read back from the job
ledger. The executor re-binds the registered template with those parameters and
REFUSES when the bound content hash differs from the one stored on the run:
a diagnostic of a different strategy is not a diagnostic of this run.

The baseline
------------
Every diagnostic reports against a BASELINE re-run under the same executor,
never against the stored headline: the window can be shorter than the stored
run's (the holdout is excluded) and a stored run may predate the current engine.
When the window covers the whole stored run, the baseline's ledger hash is
compared with the stored one and the result is reported (``reproduces_stored``).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd

from fiboki.backtest.metrics import compute_metrics
from fiboki.core.contracts import Trade
from fiboki.core.enums import Timeframe
from fiboki.core.instruments import get as get_instrument
from fiboki.core.money import IdentityFxSource
from fiboki.research.robustness.boundary import HoldoutBoundary, assert_clear, load_end
from fiboki.sim.profiles import ExecutionProfile, get_profile
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.engine_evaluator import (
    EngineEvaluator,
    EvaluatorConfig,
    research_construction_policy,
)
from fiboki.validation.evaluation import DateWindow, WindowEvaluation

__all__ = [
    "BarLoader",
    "BindingExecutor",
    "BindingMismatch",
    "RunBinding",
    "RunOutcome",
    "summarise",
]


class BarLoader(Protocol):
    """The read-only bar source the research worker injects (``agents.tools.BarSource``)."""

    def load(self, instrument: str, timeframe: Timeframe, *, start: str | None = None,
             end: str | None = None) -> tuple[pd.DataFrame, str]: ...


class BindingMismatch(ValueError):
    """The re-bound document is not the strategy the stored run executed."""


@dataclass(frozen=True, slots=True)
class RunBinding:
    """A stored run, as the inputs a re-run needs. JSON-able (it is a job payload)."""

    run_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    window_start: str
    window_end: str
    initial_balance: float
    risk_fraction: float
    account_ccy: str
    profile_name: str
    parameters: dict[str, Any]
    #: ``none`` (flat risk fraction, the backtest job's path) or ``research``
    #: (the paper runtime's construction, the ladder's path).
    construction: str
    sizing_policy: str
    content_hash: str
    ledger_sha256: str
    dataset_version_id: str
    #: The stored run's own last bar: the window covers the whole run only when
    #: ``window_end`` is after it, which is when the stored ledger is comparable.
    run_last: str
    boundary: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "strategy_id": self.strategy_id,
            "instrument": self.instrument, "timeframe": self.timeframe,
            "window_start": self.window_start, "window_end": self.window_end,
            "initial_balance": self.initial_balance, "risk_fraction": self.risk_fraction,
            "account_ccy": self.account_ccy, "profile_name": self.profile_name,
            "parameters": dict(sorted(self.parameters.items())),
            "construction": self.construction, "sizing_policy": self.sizing_policy,
            "content_hash": self.content_hash, "ledger_sha256": self.ledger_sha256,
            "dataset_version_id": self.dataset_version_id, "run_last": self.run_last,
            "boundary": dict(self.boundary),
        }

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> RunBinding:
        return cls(
            run_id=str(raw["run_id"]), strategy_id=str(raw["strategy_id"]),
            instrument=str(raw["instrument"]).upper(), timeframe=str(raw["timeframe"]),
            window_start=str(raw["window_start"]), window_end=str(raw["window_end"]),
            initial_balance=float(raw["initial_balance"]), risk_fraction=float(raw["risk_fraction"]),
            account_ccy=str(raw["account_ccy"]).upper(), profile_name=str(raw["profile_name"]),
            parameters=dict(raw.get("parameters") or {}), construction=str(raw["construction"]),
            sizing_policy=str(raw["sizing_policy"]), content_hash=str(raw.get("content_hash") or ""),
            ledger_sha256=str(raw.get("ledger_sha256") or ""),
            dataset_version_id=str(raw.get("dataset_version_id") or ""),
            run_last=str(raw.get("run_last") or ""), boundary=dict(raw.get("boundary") or {}),
        )

    @property
    def window(self) -> DateWindow:
        return DateWindow(f"robustness::{self.run_id}", self.window_start, self.window_end)

    @property
    def covers_stored_run(self) -> bool:
        return bool(self.run_last) and pd.Timestamp(self.window_end) > pd.Timestamp(self.run_last)

    def holdout(self) -> HoldoutBoundary:
        b = self.boundary
        return HoldoutBoundary(
            start=_utc(b["start"]), basis=str(b.get("basis") or ""),
            dataset_version_ids=tuple(b.get("dataset_version_ids") or ()),
            detail=str(b.get("detail") or ""),
        )


def _utc(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(str(value))
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


@dataclass(frozen=True, slots=True)
class _ProfiledConfig(EvaluatorConfig):
    """``EvaluatorConfig`` with an explicit profile VALUE instead of a name lookup.

    The override's full fingerprint reaches the engine fingerprint through
    ``BacktestConfig.profile``, so a stressed evaluation is stamped as one.
    """

    override: ExecutionProfile | None = None

    @property
    def profile(self) -> ExecutionProfile:  # type: ignore[override]
        return self.override if self.override is not None else get_profile(self.profile_name)

    def to_dict(self) -> dict[str, Any]:
        out = EvaluatorConfig.to_dict(self)
        if self.override is not None:
            out["profile_override"] = self.override.name
        return out


def summarise(trades: tuple[Trade, ...], equity: pd.Series, initial: float) -> dict[str, float | None]:
    """The engine's own metric definitions (``backtest/metrics.py``) over one re-run."""
    if equity.empty:
        return {"n_trades": float(len(trades)), "net_profit": float(sum(t.net_pnl for t in trades))}
    m = compute_metrics(trades=list(trades), equity_curve=equity.to_frame("equity"),
                        initial_equity=float(initial), strict=False)

    def f(v: Any) -> float | None:
        if v is None:
            return None
        x = float(v)
        return x if math.isfinite(x) else None

    return {
        "n_trades": float(m.n_trades),
        "net_profit": f(m.final_equity - m.initial_equity),
        "total_return_pct": f(m.total_return_pct),
        "cagr_pct": f(m.cagr_pct),
        "sharpe": f(m.sharpe),
        "sortino": f(m.sortino),
        "calmar": f(m.calmar),
        "max_drawdown_pct": f(m.max_drawdown_pct),
        "profit_factor": f(m.profit_factor),
        "win_rate_pct": f(m.win_rate_pct),
        "expectancy": f(m.expectancy),
        "total_costs": f(m.total_costs),
    }


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """One re-run: its evaluation, its equity curve and its summary metrics."""

    evaluation: WindowEvaluation
    equity: pd.Series
    metrics: dict[str, float | None]
    profile_name: str

    @property
    def trades(self) -> tuple[Trade, ...]:
        return self.evaluation.trades

    @property
    def ledger_sha256(self) -> str:
        return str(self.evaluation.meta.get("ledger_sha256") or "")

    def brief(self) -> dict[str, Any]:
        return {"profile": self.profile_name, "metrics": dict(self.metrics),
                "ledger_sha256": self.ledger_sha256,
                "rejections": dict(self.evaluation.meta.get("rejections") or {}),
                "signals_emitted": int(self.evaluation.meta.get("signals_emitted") or 0)}


class BindingExecutor:
    """Loads a binding's bars once (strictly before the holdout) and re-runs it."""

    def __init__(self, binding: RunBinding, template: StrategyDocument, bars: BarLoader,
                 *, fx_resolver: Callable[[str, str], tuple[Any, str]] | None = None) -> None:
        self.binding = binding
        self.template = template
        self.boundary = binding.holdout()
        window = binding.window
        assert_clear(self.boundary, start=window.start, end=window.end)
        tf = Timeframe(binding.timeframe)
        self.timeframe = tf
        frame, version = bars.load(binding.instrument, tf, start=None, end=load_end(self.boundary))
        frame = frame.loc[frame.index < self.boundary.start]
        frame = frame.loc[frame.index < window.end]
        if frame.empty or frame.index[-1] < window.start:
            raise ValueError(f"no bars of {binding.instrument} {tf.value} inside the research window {window}")
        self.history = frame
        self.version_id = version
        self.window = window
        self.window_index = int(frame.index.searchsorted(window.start, side="left"))
        bound = template.bind({**template.default_values(), **binding.parameters}) \
            if template.unbound_parameters() else template
        if binding.content_hash and bound.content_hash() != binding.content_hash:
            raise BindingMismatch(
                f"re-binding {template.strategy_id} with {binding.parameters} gives content hash "
                f"{bound.content_hash()[:12]}, but the stored run executed "
                f"{binding.content_hash[:12]}: the run's parameters cannot be reproduced, so no "
                "diagnostic of it is possible."
            )
        self.bound = bound
        spec = get_instrument(binding.instrument)
        if spec.quote.upper() == binding.account_ccy.upper():
            self.fx: Any = IdentityFxSource()
            self.fx_label = f"identity({spec.quote}=={binding.account_ccy})"
        else:
            resolver = fx_resolver or getattr(bars, "fx_source", None)
            if resolver is None:
                raise ValueError(
                    f"{binding.instrument} is quoted in {spec.quote}, the run's account is "
                    f"{binding.account_ccy}, and the bar source offers no FX conversion: refusing "
                    "to re-run at a 1.0 rate."
                )
            self.fx, self.fx_label = resolver(binding.account_ccy, spec.quote)
        self.base_profile = get_profile(binding.profile_name)
        self.n_runs = 0

    # ------------------------------------------------------------ frames

    def window_frame(self, extra_bars: int = 0) -> pd.DataFrame:
        """The window's bars plus ``extra_bars`` of earlier history (never later)."""
        start = max(0, self.window_index - int(extra_bars))
        return self.history.iloc[start:]

    @property
    def prologue_available(self) -> int:
        return self.window_index

    # ------------------------------------------------------------ runs

    def run(self, *, params: Mapping[str, Any] | None = None, profile: ExecutionProfile | None = None,
            frame: pd.DataFrame | None = None, extra_bars: int = 0) -> RunOutcome:
        bars = frame if frame is not None else self.window_frame(extra_bars)
        if bars.index[-1] >= self.boundary.start:  # pragma: no cover - guarded at load
            raise AssertionError("a reserved bar reached a diagnostic run")
        config = _ProfiledConfig(
            instrument=self.binding.instrument, timeframe=self.timeframe,
            initial_balance=self.binding.initial_balance, account_ccy=self.binding.account_ccy,
            risk_fraction=self.binding.risk_fraction, sizing_policy=self.binding.sizing_policy,
            profile_name=self.binding.profile_name,
            # The prologue is exactly the history handed over: zero for the
            # baseline (the stored run started cold at its first bar), N for
            # the warm-up diagnostic.
            warmup_margin_bars=max(int(extra_bars), 0),
            override=profile,
        )
        evaluator = EngineEvaluator(
            document=self.template, frame=bars, dataset_version_id=self.version_id or "unversioned",
            config=config, fx=self.fx, fx_label=self.fx_label,
            price_basis_lineage=dict(bars.attrs.get("price_lineage") or {}),
            construction=research_construction_policy() if self.binding.construction == "research" else None,
        )
        merged = {**self.template.default_values(), **self.binding.parameters, **dict(params or {})}
        ev = evaluator(merged, self.window)
        self.n_runs += 1
        idx = bars.index[(bars.index >= self.window.start) & (bars.index < self.window.end)]
        opening = float(ev.meta.get("opening_equity") or self.binding.initial_balance)
        values = opening + np.cumsum(np.asarray(ev.returns, dtype=float))
        equity = pd.Series(values[: len(idx)], index=idx[: len(values)], dtype=float)
        return RunOutcome(ev, equity, summarise(ev.trades, equity, opening),
                          (profile or self.base_profile).name)
