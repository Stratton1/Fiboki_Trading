"""Analytic evaluators for the validation ladder tests.

Deliberately NOT in ``tests/conftest.py``: that file belongs to the data
platform, and these helpers are only needed by the validation and research
tests.

Why synthetic rather than real backtests
----------------------------------------
The ladder's job is to reach the right VERDICT given evidence. To test that, the
evidence has to be known exactly -- "this strategy has a drift of 0.30 per
period and nothing else" -- which no real backtest can give you. Every evaluator
here is an explicit return-generating process, so when a test asserts that the
ladder rejected something, it is asserting against a known truth rather than
against whatever the market did.

Each evaluator is deterministic in ``(params, window)``: the same request always
returns the same numbers, on any machine and in any order, which is what lets the
ladder be tested at all.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason
from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    ParameterGrid,
    WindowEvaluation,
    params_key,
)

DATA_START = pd.Timestamp("2019-01-01", tz="UTC")
DATA_END = pd.Timestamp("2024-01-01", tz="UTC")
DATASET_VERSION = "synthetic_eurusd_h4_v1"


def _seed(*parts: object) -> int:
    blob = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big") % (2**63)


@dataclass(slots=True)
class SyntheticEvaluator:
    """A return-generating process with a controllable parameter surface.

    Returns for one window are ``mu(params, window) + common + idiosyncratic``.
    The COMMON component is shared by every parameterisation of that window,
    which is what makes the trials correlated -- exactly the situation
    ``effective_trials_by_clustering`` exists for, and the one a search over a
    parameter grid always produces.
    """

    mu: Callable[[Mapping[str, Any], DateWindow, np.ndarray], Any]
    """Drift per period. Receives the period midpoints as DAYS SINCE
    :data:`DATA_START`, so a surface can move through time -- which is the whole
    point of the walk-forward trap below."""

    sigma: Callable[[Mapping[str, Any], DateWindow], float] = lambda p, w: 1.0
    periods_per_day: float = 0.45
    common_fraction: float = 0.7
    spread_cost: float = 0.02
    seed: int = 20260919
    calls: list[tuple[str, str]] = field(default_factory=list)

    def periods(self, window: DateWindow) -> int:
        return max(2, int(window.days * self.periods_per_day))

    def __call__(
        self, params: Mapping[str, Any], window: DateWindow
    ) -> WindowEvaluation:
        key = params_key(params)
        self.calls.append((key, window.name))
        n = self.periods(window)

        # One common factor per WINDOW, keyed on its boundaries rather than its
        # name, so two windows covering the same span agree whatever they are
        # called -- otherwise a fold's train and a later full-sample run would
        # disagree about what happened on the same days.
        common = np.random.default_rng(
            _seed(self.seed, "common", window.start.isoformat(), window.end.isoformat())
        ).standard_normal(n)
        idio = np.random.default_rng(
            _seed(self.seed, "idio", key, window.start.isoformat(), window.end.isoformat())
        ).standard_normal(n)

        sd = float(self.sigma(params, window))
        shared = float(np.clip(self.common_fraction, 0.0, 1.0))
        noise = sd * (np.sqrt(shared) * common + np.sqrt(1.0 - shared) * idio)
        step_days = window.days / n
        offsets = (window.start - DATA_START).total_seconds() / 86_400.0
        times = offsets + step_days * (np.arange(n) + 0.5)
        returns = np.asarray(self.mu(params, window, times), dtype=float) + noise

        trades = self._trades(returns, window)
        return WindowEvaluation(
            window=window,
            params=dict(params),
            n_trades=len(trades),
            net_profit=float(returns.sum()),
            returns=returns,
            returns_basis="period",
            trades=tuple(trades),
        )

    def _trades(self, returns: np.ndarray, window: DateWindow) -> list[Trade]:
        """One closed trade per period, so stress and purged CV have real spans."""
        n = returns.size
        step = window.duration / max(n, 1)
        out: list[Trade] = []
        for i, pnl in enumerate(returns):
            entry = window.start + step * i
            out.append(
                Trade(
                    instrument="EURUSD",
                    direction=Direction.LONG,
                    size=1.0,
                    entry_price=1.1000,
                    exit_price=1.1000 + float(pnl) / 100_000.0,
                    entry_time=entry,
                    exit_time=entry + step * 0.9,
                    exit_reason=(
                        ExitReason.TAKE_PROFIT if pnl >= 0 else ExitReason.STOP_LOSS
                    ),
                    gross_pnl=float(pnl) + self.spread_cost,
                    spread_cost=self.spread_cost,
                    commission=0.0,
                    slippage_cost=0.0,
                    financing_cost=0.0,
                    net_pnl=float(pnl),
                    account_ccy="GBP",
                    strategy_id="synthetic",
                    bars_held=1,
                )
            )
        return out


# --------------------------------------------------------------------------
# Parameter surfaces
# --------------------------------------------------------------------------

FAST_VALUES = (5, 10, 15, 20, 25)
SLOW_VALUES = (20, 30, 40, 50, 60)
DEFAULT_PARAMS = {"fast": 15, "slow": 40}
PEAK_PARAMS = {"fast": 15, "slow": 40}


def grid() -> ParameterGrid:
    return ParameterGrid.from_axes({"fast": FAST_VALUES, "slow": SLOW_VALUES})


def candidate(strategy_id: str, content_hash: str) -> Candidate:
    return Candidate(
        strategy_id=strategy_id,
        content_hash=content_hash,
        default_params=dict(DEFAULT_PARAMS),
        grid=grid(),
    )


def plateau_mu(peak: float = 0.30, decay: float = 0.03, floor: float = 0.0):
    """A broad, smooth optimum: the parameter surface a real edge produces.

    Distance is measured in GRID STEPS from the peak, so the surface does not
    depend on the parameters' units.
    """
    fast_i = {v: i for i, v in enumerate(FAST_VALUES)}
    slow_i = {v: i for i, v in enumerate(SLOW_VALUES)}
    peak_fast = fast_i[PEAK_PARAMS["fast"]]
    peak_slow = slow_i[PEAK_PARAMS["slow"]]

    def _mu(params: Mapping[str, Any], _window: DateWindow, _times: np.ndarray) -> float:
        d = abs(fast_i[int(params["fast"])] - peak_fast) + abs(
            slow_i[int(params["slow"])] - peak_slow
        )
        return max(floor, peak - decay * d)

    return _mu


def zero_mu(params: Mapping[str, Any], window: DateWindow, times: np.ndarray) -> float:
    return 0.0


def genuine_edge_evaluator(**kwargs: Any) -> SyntheticEvaluator:
    """A real, modest, parameter-stable edge sitting on a plateau."""
    return SyntheticEvaluator(mu=plateau_mu(), **kwargs)


def pure_noise_evaluator(seed: int = 4242, **kwargs: Any) -> SyntheticEvaluator:
    """No edge anywhere in the declared domain. Everything is sampling noise."""
    return SyntheticEvaluator(mu=zero_mu, seed=seed, **kwargs)


def moving_regime_mu(
    good: float = 2.0,
    staleness_penalty: float = 0.4,
    bad: float = -0.5,
    default_drift: float = 0.35,
    n_eras: int = 6,
    research_days: float = 1460.8,
):
    """A parameter surface whose optimum has ALREADY MOVED by the test window.

    Divide the research period into ``n_eras`` equal eras. Parameterisation
    ``k`` (indexed by its ``slow`` value) earns ``good - staleness_penalty * k``
    in eras ``0..k`` and ``bad`` in every era after that. The DEFAULT
    parameterisation instead earns a small, constant ``default_drift`` for the
    whole period.

    The staleness penalty is what makes the selection DETERMINISTIC rather than
    a coin flip: at fold ``i`` every ``k >= i`` was good for the whole train
    window, and without the penalty they would be separated only by noise. With
    it, ``k = i`` wins by roughly three standard errors -- and ``k = i`` is
    precisely the parameterisation whose regime ends at the train boundary.

    Under an anchored walk-forward whose fold ``i`` trains on eras ``0..i`` and
    tests on era ``i+1``, the in-sample winner is always the cheapest ``k`` that
    was good for the whole train window -- ``k = i`` -- and that parameterisation
    is, by construction, ``bad`` on era ``i+1``. So:

    * a procedure that SELECTS on train and evaluates the selection on test sees
      a loss in every single fold;
    * a procedure that runs the FIXED default parameters on the same test windows
      sees a profit in every single fold.

    That is the difference between walk-forward and what V1 called walk-forward,
    made deterministic rather than left to sampling luck. The strategy is not
    even a fraud: the default really does have a small edge. What is being caught
    is the SELECTION step manufacturing a worse strategy than the one it started
    from, and a procedure that never selects anything cannot catch it.
    """
    era_days = research_days / n_eras
    default_key = params_key(DEFAULT_PARAMS)

    def _mu(params: Mapping[str, Any], _window: DateWindow, times: np.ndarray):
        if params_key(params) == default_key:
            return default_drift
        k = SLOW_VALUES.index(int(params["slow"]))
        era = np.floor(np.asarray(times, dtype=float) / era_days)
        return np.where(era <= k, good - staleness_penalty * k, bad)

    return _mu


def overfit_trap_evaluator(**kwargs: Any) -> SyntheticEvaluator:
    """The case that separates real walk-forward from V1's imitation of it.

    See :func:`moving_regime_mu`. ``common_fraction`` is 0 so that each
    parameterisation stands or falls on its own returns rather than on a shared
    window factor, which would otherwise let a lucky era rescue every column at
    once and hide the effect being tested.
    """
    kwargs.setdefault("common_fraction", 0.0)
    return SyntheticEvaluator(mu=moving_regime_mu(), **kwargs)


# --------------------------------------------------------------------------
# Strategy documents (for the research-memory tests)
# --------------------------------------------------------------------------


def seed_document(name: str = "rsi_band_mean_reversion"):
    """Load one of the repository's seed strategy documents."""
    from pathlib import Path

    from fiboki.strategy.dsl import StrategyDocument

    root = Path(__file__).resolve().parents[1] / "research" / "strategies"
    return StrategyDocument.from_json((root / f"{name}.json").read_text())


def reparameterise(document, **parameter_defaults: Any):
    """Same rules, same indicators, different NUMBERS.

    The returned document has a different ``content_hash`` and an identical
    ``structure_hash`` -- the rediscovery a content hash cannot see.
    """
    data = document.model_dump(mode="json")
    data.pop("complexity_score", None)
    for name, value in parameter_defaults.items():
        if name not in data["parameters"]:
            raise KeyError(f"{name} is not a declared parameter of this document")
        data["parameters"][name] = {**data["parameters"][name], "default": value}
    return type(document).model_validate(data)


def trades_from_returns(
    returns: Sequence[float], start: pd.Timestamp | None = None
) -> list[Trade]:
    """Closed trades whose net P&L is exactly ``returns``."""
    origin = start or DATA_START
    out: list[Trade] = []
    for i, pnl in enumerate(returns):
        entry = origin + pd.Timedelta(hours=4 * i)
        out.append(
            Trade(
                instrument="EURUSD",
                direction=Direction.LONG,
                size=1.0,
                entry_price=1.1,
                exit_price=1.1 + float(pnl) / 100_000.0,
                entry_time=entry,
                exit_time=entry + pd.Timedelta(hours=3),
                exit_reason=ExitReason.TAKE_PROFIT if pnl >= 0 else ExitReason.STOP_LOSS,
                gross_pnl=float(pnl) + 0.02,
                spread_cost=0.02,
                commission=0.0,
                slippage_cost=0.0,
                financing_cost=0.0,
                net_pnl=float(pnl),
                account_ccy="GBP",
                strategy_id="synthetic",
                bars_held=1,
            )
        )
    return out
