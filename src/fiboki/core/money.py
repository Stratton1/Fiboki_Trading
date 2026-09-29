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

from dataclasses import dataclass, field
from typing import Any, Protocol

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


#: How old the newest daily observation may be before a lookup refuses. Four
#: days covers a weekend plus a one-day holiday (Friday close to Tuesday), and no
#: more. It was seven, which let a rate from the previous Wednesday price a
#: Tuesday trade; for daily FX that is a stale rate, not a holiday.
DEFAULT_MAX_STALENESS = pd.Timedelta(days=4)


#: How :meth:`SeriesFxSource.rate_with_route` answered a conversion.
ROUTE_IDENTITY = "identity"
ROUTE_DIRECT = "direct"
ROUTE_INVERSE = "inverse"


def route_via(pivot: str) -> str:
    """The route name for a triangulation through ``pivot``: ``via_usd``."""
    return f"via_{pivot.lower()}"


@dataclass
class SeriesFxSource:
    """Conversion from historical FX series. Uses as-of (backward) lookup.

    ``series`` maps 'GBPUSD' -> a tz-aware Series of rates, indexed by the time
    each rate became KNOWN (for a daily bar, its close, not its open). Inverse
    pairs are derived automatically. As-of lookup never reads a future
    observation.

    ``pivot`` allows ONE triangulation leg through a pivot currency when neither
    the direct nor the inverse pair is loaded: NZD->GBP is NZD->USD times
    USD->GBP. Each leg is looked up and staleness-checked on its own, so a
    triangulated rate is never fresher than its stalest leg. ``pivot=None``
    forbids triangulation.

    ``fallback_via_pivot`` (off by default) extends that to a LOADED direct
    pair that cannot answer at ``when``: no observation yet (the cross starts
    later than the instrument) or none within ``max_staleness``. The direct
    pair is always tried first and wins whenever it has a fresh rate; only its
    failure reaches the pivot, whose two legs are each staleness-checked. When
    the pivot cannot answer either, the DIRECT pair's error is raised with the
    pivot's appended, so a refusal names the series that should have answered.

    Every answer is tallied in ``route_counts`` as ``(from, to, route) ->
    count`` with ``route`` one of ``identity``, ``direct``, ``inverse`` or
    ``via_<pivot>`` (:meth:`rate_with_route` returns it per conversion). The
    tally counts lookups made through THIS object; a cached evaluation makes
    none, so it is a diagnostic, not a lineage record. ``lineage`` is free-form
    provenance per series (dataset version, how a daily rate was derived) set
    by whoever built the source; neither field takes part in equality.
    """

    series: dict[str, pd.Series]
    max_staleness: pd.Timedelta = field(default_factory=lambda: DEFAULT_MAX_STALENESS)
    pivot: str | None = "USD"
    fallback_via_pivot: bool = False
    lineage: dict[str, dict[str, Any]] = field(default_factory=dict, compare=False, repr=False)
    route_counts: dict[tuple[str, str, str], int] = field(
        default_factory=dict, compare=False, repr=False
    )

    def rate(self, from_ccy: str, to_ccy: str, when: pd.Timestamp) -> float:
        return self.rate_with_route(from_ccy, to_ccy, when)[0]

    def rate_with_route(
        self, from_ccy: str, to_ccy: str, when: pd.Timestamp
    ) -> tuple[float, str]:
        """The rate and the route that produced it (see the class docstring)."""
        f, t = from_ccy.upper(), to_ccy.upper()
        if f == t:
            return self._tally(f, t, 1.0, ROUTE_IDENTITY)
        direct_error: KeyError | None = None
        try:
            direct = self._direct(f, t, when)
        except KeyError as exc:
            if not self.fallback_via_pivot:
                raise
            direct, direct_error = None, exc
        if direct is not None:
            return self._tally(f, t, direct[0], direct[1])
        pivot = (self.pivot or "").upper()
        if pivot and pivot not in (f, t):
            try:
                first = self._direct(f, pivot, when)
                second = self._direct(pivot, t, when)
            except KeyError as exc:
                if direct_error is not None:
                    raise KeyError(
                        f"{direct_error.args[0]}; the {pivot} fallback could not answer "
                        f"either: {exc.args[0]}"
                    ) from exc
                raise
            if first is not None and second is not None:
                return self._tally(f, t, first[0] * second[0], route_via(pivot))
        if direct_error is not None:
            raise direct_error
        raise KeyError(
            f"No FX series for {f}->{t}"
            + (f" (directly, inversely or via {pivot})" if pivot else "")
            + ". Load it before running, or the result would be silently "
            "denominated in the wrong currency."
        )

    def has_route(self, from_ccy: str, to_ccy: str) -> bool:
        """Could :meth:`rate` answer this pair at all, ignoring staleness?"""
        f, t = from_ccy.upper(), to_ccy.upper()
        if f == t or f + t in self.series or t + f in self.series:
            return True
        pivot = (self.pivot or "").upper()
        if not pivot or pivot in (f, t):
            return False

        def _one(a: str, b: str) -> bool:
            return a + b in self.series or b + a in self.series

        return _one(f, pivot) and _one(pivot, t)

    def _tally(self, f: str, t: str, value: float, route: str) -> tuple[float, str]:
        key = (f, t, route)
        self.route_counts[key] = self.route_counts.get(key, 0) + 1
        return value, route

    def _direct(self, f: str, t: str, when: pd.Timestamp) -> tuple[float, str] | None:
        direct, inverse = f + t, t + f
        if direct in self.series:
            return self._asof(self.series[direct], when, direct), ROUTE_DIRECT
        if inverse in self.series:
            v = self._asof(self.series[inverse], when, inverse)
            if v == 0.0 or not np.isfinite(v):
                raise ValueError(f"Non-invertible rate {v} for {inverse} at {when}")
            return 1.0 / v, ROUTE_INVERSE
        return None

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


def daily_rates_from_intraday_closes(
    frame: pd.DataFrame, bar_minutes: int, *, name: str = ""
) -> pd.Series:
    """One rate per UTC day from intraday bars: the last bar to CLOSE that day.

    ``frame`` is indexed by bar OPEN time (tz-aware UTC) with a ``close``
    column; each bar closes at ``open + bar_minutes``. Bars are grouped by the
    UTC day in which they close (a bar closing exactly at midnight belongs to
    the day that midnight ends), the last one in each day is kept, and its
    close is stamped at its CLOSE time, never its open: the rate is not known
    before the bar that produced it has closed. The nominal close is used even
    when trading stopped earlier inside the bar, which can only make the rate
    later, never earlier.
    """
    if frame is None or len(frame) == 0:
        return pd.Series(dtype=float, name=name)
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        raise ValueError("intraday FX bars must carry a tz-aware UTC index")
    closes_at = index.tz_convert("UTC") + pd.Timedelta(minutes=int(bar_minutes))
    day = (closes_at - pd.Timedelta(microseconds=1)).floor("D")
    bars = pd.DataFrame(
        {"close": frame["close"].to_numpy(dtype=float), "day": day, "closes_at": closes_at}
    ).sort_values("closes_at", kind="stable")
    last = bars.groupby("day", sort=True).tail(1)
    return pd.Series(
        last["close"].to_numpy(), index=pd.DatetimeIndex(last["closes_at"]), name=name or None
    )


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
