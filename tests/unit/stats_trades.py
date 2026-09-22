"""Trade builders for the statistical-validation tests.

Kept out of ``tests/conftest.py`` deliberately: that file belongs to the data
platform tests, and these helpers are only needed by ``tests/unit/test_stress.py``
and ``tests/unit/test_cv.py``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fiboki.core.contracts import Trade
from fiboki.core.enums import Direction, ExitReason

START = pd.Timestamp("2022-01-03 08:00", tz="UTC")


def make_trade(
    net_pnl: float,
    *,
    index: int = 0,
    spread_cost: float = 2.0,
    commission: float = 0.0,
    slippage_cost: float = 0.0,
    financing_cost: float = 0.0,
    bars_held: int = 4,
    instrument: str = "EURUSD",
) -> Trade:
    """A closed trade whose net P&L is exactly ``net_pnl``.

    ``gross_pnl`` is back-solved from the costs so ``net == gross - total_costs``
    holds exactly, which is the invariant the stress repricing relies on.
    """
    costs = spread_cost + commission + slippage_cost + financing_cost
    entry = START + pd.Timedelta(hours=index)
    return Trade(
        instrument=instrument,
        direction=Direction.LONG,
        size=1.0,
        entry_price=1.1000,
        exit_price=1.1000 + net_pnl / 100_000.0,
        entry_time=entry,
        exit_time=entry + pd.Timedelta(hours=bars_held),
        exit_reason=ExitReason.TAKE_PROFIT if net_pnl >= 0 else ExitReason.STOP_LOSS,
        gross_pnl=net_pnl + costs,
        spread_cost=spread_cost,
        commission=commission,
        slippage_cost=slippage_cost,
        financing_cost=financing_cost,
        net_pnl=net_pnl,
        account_ccy="GBP",
        strategy_id="test_strategy",
        bars_held=bars_held,
    )


def edge_trades(
    n: int = 150, win_rate: float = 0.45, win: float = 200.0, loss: float = -120.0, seed: int = 7
) -> list[Trade]:
    """A trade list with a modest, genuine positive expectancy."""
    rng = np.random.default_rng(seed)
    pnls = np.where(rng.random(n) < win_rate, win, loss)
    return [make_trade(float(p), index=i) for i, p in enumerate(pnls)]
