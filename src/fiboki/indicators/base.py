"""Indicator base layer.

Every indicator in Fiboki V2 is **strictly causal**: the value it publishes on
row ``i`` is a function of rows ``0..i`` only. This is not a style preference —
it is the property that makes backtest, research and paper trading agree. V1
broke it in three separate places (centred swing windows, ``close.shift(-26)``
chikou, and a cloud displaced with the wrong parameter), and every downstream
number inherited the lie.

Two rules are enforced here rather than trusted:

* ``compute`` never mutates the caller's frame. V1's indicators wrote columns
  into the passed DataFrame, so the order in which indicators ran changed what
  later indicators could see.
* ``assert_causal`` mechanically proves the property by corrupting the future
  and demanding bit-identical history. ``tests/unit/test_indicator_causality.py``
  runs it across the whole registry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

import numpy as np
import pandas as pd

OHLC_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")
OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


class CausalityError(RuntimeError):
    """Raised when an indicator's output at bar i depends on a bar after i."""


class VolumeUnavailableError(ValueError):
    """Raised when a volume-dependent indicator is given absent/zero volume.

    V1 computed OBV on FX volume that is identically zero (so one strategy could
    never trade) and let VWAP silently fall back to a rolling mean of typical
    price, which is not VWAP. V2 refuses both: either the caller opts in to an
    explicit ``unavailable`` marker, or the indicator raises.
    """


@runtime_checkable
class IndicatorProtocol(Protocol):
    """Structural contract every indicator satisfies."""

    @property
    def name(self) -> str: ...

    @property
    def warmup_period(self) -> int: ...

    @property
    def required_columns(self) -> tuple[str, ...]: ...

    @property
    def output_columns(self) -> tuple[str, ...]: ...

    def params(self) -> dict[str, Any]: ...

    def compute(self, df: pd.DataFrame) -> pd.DataFrame: ...


class Indicator(ABC):
    """Base class implementing the non-mutating ``compute`` template.

    Subclasses implement :meth:`_compute`, returning a mapping of output column
    name to values. The base class copies the frame, attaches the columns and
    checks that exactly the declared outputs were produced.
    """

    #: True when the indicator reads the ``volume`` column.
    requires_volume: bool = False

    # ------------------------------------------------------------ metadata

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique, parameter-qualified identifier (also the column prefix)."""

    @property
    @abstractmethod
    def warmup_period(self) -> int:
        """Bars that must elapse before any output is trustworthy."""

    @property
    @abstractmethod
    def output_columns(self) -> tuple[str, ...]:
        """Columns this indicator adds. Used to derive a strategy's schema."""

    @property
    def required_columns(self) -> tuple[str, ...]:
        return OHLCV_COLUMNS if self.requires_volume else OHLC_COLUMNS

    @abstractmethod
    def params(self) -> dict[str, Any]:
        """Parameters, for content hashing and sweep bookkeeping."""

    # ------------------------------------------------------------- compute

    @abstractmethod
    def _compute(self, df: pd.DataFrame) -> dict[str, Any]:
        """Return {column: values}. MUST be causal."""

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        produced = self._compute(df)
        declared = set(self.output_columns)
        got = set(produced)
        if got != declared:
            raise ValueError(
                f"{self.name}: declared outputs {sorted(declared)} but produced "
                f"{sorted(got)}. output_columns must match _compute exactly."
            )
        out = df.copy()
        for col in self.output_columns:  # stable, declared order
            values = produced[col]
            out[col] = (
                values.to_numpy() if isinstance(values, pd.Series) else np.asarray(values)
            )
        return out

    def validate_input(self, df: pd.DataFrame) -> None:
        missing = [c for c in self.required_columns if c not in df.columns]
        if missing:
            raise ValueError(f"{self.name} requires missing columns {missing}")
        if len(df) == 0:
            raise ValueError(f"{self.name} requires a non-empty frame")

    # --------------------------------------------------------------- util

    def unavailable_column(self) -> str:
        return f"{self.name}_unavailable"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({self.params()})"


# ------------------------------------------------------------- primitives


def wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing, seeded with the simple mean of the first ``period``.

    Forward recursion only, so causal by construction. Leading non-finite values
    (e.g. the NaN from a ``diff``) are skipped before seeding; the seed lands on
    the last bar of the seeding window, matching the textbook definition.
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    out = np.full(n, np.nan)
    if period < 1:
        raise ValueError("wilder_smooth period must be >= 1")
    start = 0
    while start < n and not np.isfinite(arr[start]):
        start += 1
    if start + period > n:
        return out
    acc = float(np.mean(arr[start : start + period]))
    out[start + period - 1] = acc
    for i in range(start + period, n):
        v = arr[i]
        if not np.isfinite(v):
            v = 0.0
        acc = (acc * (period - 1) + v) / period
        out[i] = acc
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range. Bar 0 falls back to high-low (no prior close exists)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide, mapping a zero denominator to NaN rather than inf."""
    return numerator / denominator.replace(0.0, np.nan)


# -------------------------------------------------------- causality proof


def _corrupt_future(df: pd.DataFrame, k: int, seed: int = 20240719) -> pd.DataFrame:
    """Return a copy of ``df`` whose bars strictly after ``k`` are replaced."""
    rng = np.random.default_rng(seed)
    out = df.copy()
    tail = len(df) - k - 1
    if tail <= 0:
        raise ValueError("cannot corrupt the future of the last bar")
    base = float(df["close"].iloc[k])
    # A violently different future: sign-flipped drift, 40x the level, and
    # volume zeroed, so any leak shows up immediately.
    shocked = base * (2.0 + rng.uniform(0.0, 40.0, size=tail))
    out.iloc[k + 1 :, out.columns.get_loc("open")] = shocked
    out.iloc[k + 1 :, out.columns.get_loc("high")] = shocked * 1.05
    out.iloc[k + 1 :, out.columns.get_loc("low")] = shocked * 0.95
    out.iloc[k + 1 :, out.columns.get_loc("close")] = shocked
    if "volume" in out.columns:
        out.iloc[k + 1 :, out.columns.get_loc("volume")] = rng.uniform(
            0.0, 1e6, size=tail
        )
    return out


def assert_causal(
    indicator: IndicatorProtocol,
    df: pd.DataFrame,
    k: int,
    columns: Iterable[str] | None = None,
) -> None:
    """Prove that ``indicator``'s values at bars <= k ignore bars > k.

    Recomputes on a frame whose future has been replaced with nonsense and
    demands a bit-identical prefix (NaN positions must match too).
    """
    cols = tuple(columns) if columns is not None else tuple(indicator.output_columns)
    clean = indicator.compute(df)
    dirty = indicator.compute(_corrupt_future(df, k))
    for col in cols:
        a = clean[col].to_numpy()[: k + 1]
        b = dirty[col].to_numpy()[: k + 1]
        if a.dtype.kind in "fc" or b.dtype.kind in "fc":
            a_f = a.astype(float)
            b_f = b.astype(float)
            same_nan = np.array_equal(np.isnan(a_f), np.isnan(b_f))
            finite = ~np.isnan(a_f)
            same_val = np.array_equal(a_f[finite], b_f[finite])
            ok = same_nan and same_val
        else:
            ok = np.array_equal(a, b)
        if not ok:
            diff = int(np.argmax(a.astype(float) != b.astype(float)))
            raise CausalityError(
                f"{indicator.name}: column {col!r} changed at or before bar {k} "
                f"(first divergence at bar {diff}) when only bars after {k} were "
                f"altered. The indicator reads the future."
            )
