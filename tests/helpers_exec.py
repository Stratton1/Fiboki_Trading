"""Helpers for the execution-simulator and backtest-engine tests.

Kept OUT of conftest.py deliberately: conftest is shared ground and these
helpers belong to the sim/backtest suites specifically.

Everything here is boring on purpose — constant FX rates, flat cost models and
frames built row by row. A golden test whose fixture is itself clever cannot be
verified with a calculator.
"""
from __future__ import annotations

import pandas as pd

from fiboki.sim.profiles import (
    CommissionModel,
    ExecutionProfile,
    FinancingModel,
    FixedPipSpread,
    MinStopPolicy,
    NoSlippage,
)

__all__ = ["ConstantFx", "flat_profile", "make_frame"]


class ConstantFx:
    """A fixed rate table. Explicit, so every golden number is reproducible.

    ``rates`` maps ``("USD", "GBP") -> 0.80``. Same-currency conversion is 1.0
    and inverse pairs are derived. An unknown pair RAISES, exactly like the
    production sources: a test fixture that silently converted at 1.0 would be
    re-introducing the V1 bug into the safety net meant to catch it.
    """

    def __init__(self, rates: dict[tuple[str, str], float]) -> None:
        self.rates = {(a.upper(), b.upper()): v for (a, b), v in rates.items()}

    def rate(self, from_ccy: str, to_ccy: str, when: pd.Timestamp) -> float:
        f, t = from_ccy.upper(), to_ccy.upper()
        if f == t:
            return 1.0
        if (f, t) in self.rates:
            return self.rates[(f, t)]
        if (t, f) in self.rates:
            return 1.0 / self.rates[(t, f)]
        raise KeyError(f"ConstantFx has no rate for {f}->{t}")


def make_frame(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Build a tz-aware OHLC frame from ``(iso_timestamp, o, h, l, c)`` rows."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0], tz="UTC") for r in rows], name="timestamp")
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
        },
        index=idx,
    )


def flat_profile(
    *,
    spread_pips: float = 0.0,
    commission: CommissionModel | None = None,
    financing: FinancingModel | None = None,
    name: str = "GOLDEN_FLAT",
    **kwargs,
) -> ExecutionProfile:
    """A profile with no randomness at all, for arithmetic that must be exact."""
    return ExecutionProfile(
        name=name,
        spread=FixedPipSpread(spread_pips),
        slippage=NoSlippage(),
        commission=commission or CommissionModel(),
        financing=financing or FinancingModel(),
        min_stop_policy=kwargs.pop("min_stop_policy", MinStopPolicy.ALLOW),
        **kwargs,
    )
