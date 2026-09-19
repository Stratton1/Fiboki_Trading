"""Currency conversion and P&L arithmetic.

V1 computed P&L in the instrument's quote currency and reported it as GBP.
For a GBP account that silently mis-stated every USD-quoted result (including
all of XAUUSD) by the GBPUSD rate, which ranged 1.20-1.43 over the sample —
a 20% error correlated with the very macro regimes the strategies trade.

V2 makes conversion explicit and mandatory: you cannot produce an account-
currency figure without supplying a rate source, and a rate source that has no
observation for the required time raises rather than silently returning 1.0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from fiboki.core.instruments import Instrument


class FxRateSource(Protocol):
    """Supplies QUOTE->ACCOUNT conversion rates at a point in time."""

    def rate(self, from_ccy: str, to_ccy: str, when: pd.Timestamp) -> float: ...


class IdentityFxSource:
    """Explicit 'no conversion' source.

    Legitimate only when the account currency equals the quote currency, or in
    a research context that has deliberately accepted the approximation. It
    refuses silently-wrong conversions: asking it to convert between two
    different currencies raises unless `allow_mismatch` was set deliberately.
    """

    def __init__(self, *, allow_mismatch: bool = False) -> None:
        self.allow_mismatch = allow_mismatch

    def rate(self, from_ccy: str, to_ccy: str, when: pd.Timestamp) -> float:
        if from_ccy.upper() == to_ccy.upper():
            return 1.0
        if self.allow_mismatch:
            return 1.0
        raise ValueError(
            f"IdentityFxSource asked to convert {from_ccy}->{to_ccy}. Supply a real "
            "rate source, or construct IdentityFxSource(allow_mismatch=True) to "
            "record that the approximation was a deliberate, documented choice."
        )


@dataclass
class SeriesFxSource:
    """Conversion from historical FX series. Uses as-of (backward) lookup.

    `series` maps 'GBPUSD' -> a tz-aware Series of rates. Inverse pairs are
    derived automatically. As-of lookup never reads a future observation.
    """

    series: dict[str, pd.Series]
    max_staleness: pd.Timedelta = pd.Timedelta(days=7)

    def rate(self, from_ccy: str, to_ccy: str, when: pd.Timestamp) -> float:
        f, t = from_ccy.upper(), to_ccy.upper()
        if f == t:
            return 1.0
        direct, inverse = f + t, t + f
        if direct in self.series:
            return self._asof(self.series[direct], when, direct)
        if inverse in self.series:
            v = self._asof(self.series[inverse], when, inverse)
            if v == 0.0 or not np.isfinite(v):
                raise ValueError(f"Non-invertible rate {v} for {inverse} at {when}")
            return 1.0 / v
        raise KeyError(
            f"No FX series for {f}->{t}. Load it before running, or the result "
            "would be silently denominated in the wrong currency."
        )

    def _asof(self, s: pd.Series, when: pd.Timestamp, name: str) -> float:
        idx = s.index.searchsorted(when, side="right") - 1
        if idx < 0:
            raise KeyError(f"FX series {name} has no observation at or before {when}")
        ts = s.index[idx]
        if when - ts > self.max_staleness:
            raise KeyError(
                f"FX series {name} is stale at {when}: newest observation {ts} "
                f"is older than {self.max_staleness}"
            )
        return float(s.iloc[idx])


def price_move_to_quote_ccy(
    instrument: Instrument, entry: float, exit_: float, size: float, sign: int
) -> float:
    """P&L in the instrument's QUOTE currency. Pure arithmetic, no conversion."""
    return (exit_ - entry) * sign * size * instrument.contract_size


def to_account_ccy(
    amount_quote: float,
    instrument: Instrument,
    account_ccy: str,
    when: pd.Timestamp,
    fx: FxRateSource,
) -> float:
    """Convert a quote-currency amount into the account currency."""
    if amount_quote == 0.0:
        return 0.0
    return amount_quote * fx.rate(instrument.quote, account_ccy, when)


def pips(instrument: Instrument, price_delta: float) -> float:
    return price_delta / instrument.pip_size


def from_pips(instrument: Instrument, n_pips: float) -> float:
    return n_pips * instrument.pip_size


def round_size(instrument: Instrument, size: float) -> float:
    """Round DOWN to the instrument's size step. Never rounds up into more risk."""
    if size <= 0:
        return 0.0
    steps = int(size / instrument.size_step + 1e-9)
    return round(steps * instrument.size_step, 10)
