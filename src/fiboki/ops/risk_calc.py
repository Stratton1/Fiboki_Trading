"""Risk of ruin and position size, from the platform's own rules (Wave 3O). Advisory.

Two questions an operator asks before changing a risk fraction or adding an
instrument, answered with the code that actually decides:

* **Position size per instrument**: :func:`fiboki.portfolio.sizing.size_trade`,
  the ONE sizing call the paper runtime makes (``fixed_fractional_v2`` prices
  the spread and expected slippage of both legs into the risk per unit; the
  instrument's registered retail leverage cap binds). Nothing here re-derives a
  size: :func:`position_sizes` builds the signal and the account and calls it.
* **Risk of ruin**: :func:`fiboki.stats.bootstrap.resample_with_compounding`
  over a trade-return distribution in R multiples (a strategy's stored
  backtest, or the operator's own list), re-sized off RUNNING equity at the
  chosen risk fraction, stationary-bootstrap resampled, seeded. Ruin is a
  drawdown from the running peak deeper than the chosen level; ruined paths
  stop trading. The horizon is the length of the distribution supplied.

Approximations, stated: an R multiple is the trade's net P&L over its planned
risk, so resampling it at a different risk fraction assumes the stop-out costs
scale with size (they do, under v2, to first order); gaps beyond the stop are
in the distribution only as far as the backtest saw them.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.contracts import AccountState, Signal
from fiboki.core.enums import Direction
from fiboki.core.instruments import get as get_instrument
from fiboki.portfolio.sizing import SizingPolicy, size_trade
from fiboki.sim.profiles import get_profile
from fiboki.stats.bootstrap import optimal_block_length, resample_with_compounding

__all__ = ["RUIN_SEED", "RuinResult", "SizeRequest", "SizeRow", "position_sizes", "risk_of_ruin"]

RUIN_SEED = 20261019
MAX_PATHS = 20_000


@dataclass(frozen=True, slots=True)
class SizeRequest:
    instrument: str
    entry: float
    stop_pips: float
    #: Quote currency -> account currency. Required when they differ.
    fx_quote_to_account: float | None = None


@dataclass(frozen=True, slots=True)
class SizeRow:
    instrument: str
    entry: float
    stop: float | None
    stop_pips: float
    size: float | None
    risk_amount: float | None
    notional: float | None
    leverage_cap: float | None
    leverage_binding: bool
    size_from_risk: float | None
    rejection: str | None
    detail: dict[str, Any] = field(default_factory=dict)


def position_sizes(
    requests: Sequence[SizeRequest],
    *,
    account_size: float,
    account_ccy: str,
    risk_fraction: float,
    policy_id: str,
    profile_name: str,
) -> list[SizeRow]:
    """``size_trade`` per instrument, for a long entry at ``entry`` with a stop ``stop_pips`` below."""
    policy = SizingPolicy(risk_fraction=risk_fraction, policy_id=policy_id, cost_profile=get_profile(profile_name))
    account = AccountState(balance=account_size, equity=account_size, currency=account_ccy, peak_equity=account_size)
    out: list[SizeRow] = []
    for req in requests:
        try:
            inst = get_instrument(req.instrument)
        except Exception:
            out.append(SizeRow(req.instrument, req.entry, None, req.stop_pips, None, None, None, None, False, None,
                               "unknown_instrument"))
            continue
        fx = req.fx_quote_to_account
        if inst.quote == account_ccy.upper():
            fx = 1.0
        if fx is None:
            out.append(SizeRow(inst.symbol, req.entry, None, req.stop_pips, None, None, None, None, False, None,
                               f"fx_rate_required:{inst.quote}->{account_ccy.upper()}"))
            continue
        stop = req.entry - req.stop_pips * inst.pip_size
        if not (req.entry > 0 and stop > 0 and req.stop_pips > 0):
            out.append(SizeRow(inst.symbol, req.entry, stop, req.stop_pips, None, None, None, None, False, None,
                               "invalid_entry_or_stop"))
            continue
        signal = Signal(
            strategy_id="risk_calculator", instrument=inst.symbol, timeframe="H4", direction=Direction.LONG,
            bar_time=pd.Timestamp("2026-01-01T00:00:00Z"), reference_price=float(req.entry), stop_price=float(stop),
        )
        outcome = size_trade(signal=signal, instrument=inst, account=account, fx_quote_to_account=float(fx),
                             policy=policy, account_ccy=account_ccy.upper())
        d = dict(outcome.detail)
        plan = outcome.plan
        out.append(SizeRow(
            instrument=inst.symbol, entry=float(req.entry), stop=float(stop), stop_pips=float(req.stop_pips),
            size=None if plan is None else float(plan.size),
            risk_amount=None if plan is None else float(plan.risk_amount),
            notional=None if plan is None else float(plan.size * req.entry * inst.contract_size * float(fx)),
            leverage_cap=d.get("leverage_cap"), leverage_binding=bool(d.get("leverage_binding", False)),
            size_from_risk=d.get("size_from_risk"),
            rejection=None if outcome.reason is None else str(outcome.reason),
            detail={k: v for k, v in d.items() if isinstance(v, int | float | str | bool)},
        ))
    return out


@dataclass(frozen=True, slots=True)
class RuinResult:
    probability: float
    n_paths: int
    seed: int
    horizon_trades: int
    block_length: float
    risk_fraction: float
    ruin_drawdown_pct: float
    final_multiple_p5: float
    final_multiple_p50: float
    final_multiple_p95: float
    max_dd_p50: float
    max_dd_p95: float
    max_dd_p99: float
    mean_r: float
    win_rate: float


def risk_of_ruin(
    r_multiples: Sequence[float],
    *,
    risk_fraction: float,
    ruin_drawdown_pct: float,
    n_paths: int = 5_000,
    seed: int = RUIN_SEED,
) -> RuinResult:
    series = np.asarray([float(r) for r in r_multiples], dtype=float)
    if series.size < 2 or not np.all(np.isfinite(series)):
        raise ValueError("risk of ruin needs at least two finite R multiples")
    if not 0.0 < risk_fraction < 1.0:
        raise ValueError("risk_fraction must be in (0, 1)")
    if not 0.0 < ruin_drawdown_pct < 100.0:
        raise ValueError("the ruin drawdown must be between 0 and 100 percent")
    paths = int(min(max(int(n_paths), 100), MAX_PATHS))
    block = optimal_block_length(series).stationary if series.size >= 8 else 1.0
    sim = resample_with_compounding(
        series, n_paths=paths, risk_fraction=float(risk_fraction), initial_equity=1.0,
        ruin_fraction=1.0 - ruin_drawdown_pct / 100.0, ruin_basis="peak", block_length=block, rng=int(seed),
    )
    return RuinResult(
        probability=sim.ruin_probability, n_paths=paths, seed=int(seed), horizon_trades=int(series.size),
        block_length=float(block), risk_fraction=float(risk_fraction), ruin_drawdown_pct=float(ruin_drawdown_pct),
        final_multiple_p5=sim.quantile(0.05), final_multiple_p50=sim.quantile(0.5), final_multiple_p95=sim.quantile(0.95),
        max_dd_p50=sim.drawdown_quantile(0.5) * 100.0, max_dd_p95=sim.drawdown_quantile(0.95) * 100.0,
        max_dd_p99=sim.drawdown_quantile(0.99) * 100.0,
        mean_r=float(series.mean()), win_rate=float((series > 0).mean()),
    )
