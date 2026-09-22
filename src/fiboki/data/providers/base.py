"""The provider interface.

Every provider must state four things before it is allowed to hand over a
single bar:

* ``native_price_basis`` — bid, ask, mid or last. Not optional, not inferred.
* ``native_timezone`` — the convention the *source* uses, verbatim, including
  the awkward ones like "EST without DST". The provider converts to true UTC and
  records the conversion as a declared adjustment.
* whether bid and ask are separately available.
* whether an incomplete (still-forming) bar can appear in its output, and how it
  is identified — because Fiboki evaluates signals on closed candles only, and a
  provider that can leak a forming candle must filter it at the source.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from fiboki.core.enums import Timeframe
from fiboki.data.schema import Adjustment, BarDatasetMetadata, PriceBasis


class ProviderError(RuntimeError):
    pass


class AuthenticationRequired(ProviderError):
    """Credentials are needed and none are configured. Never a silent skip."""


class RateLimited(ProviderError):
    pass


class UnsupportedRequest(ProviderError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What a provider can and cannot do. Read this before trusting its output."""

    name: str
    native_price_basis: PriceBasis
    native_timezone: str
    supports_bid_ask: bool
    supports_tick_volume: bool
    supports_real_volume: bool
    timeframes: tuple[Timeframe, ...]
    requires_credentials: bool
    can_emit_incomplete_bars: bool
    max_bars_per_request: int | None = None
    notes: str = ""

    def assert_timeframe(self, timeframe: Timeframe) -> None:
        if timeframe not in self.timeframes:
            raise UnsupportedRequest(
                f"{self.name} does not provide {timeframe.value}; available: "
                + ", ".join(t.value for t in self.timeframes)
            )


@dataclass(frozen=True, slots=True)
class BarBatch:
    """Canonical bars plus the metadata that says what they are."""

    frame: pd.DataFrame
    metadata: BarDatasetMetadata
    adjustments: tuple[Adjustment, ...] = ()
    warnings: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.frame)


class BarProvider(ABC):
    """Fetch historical bars and declare honestly what they are."""

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    def available(self) -> list[tuple[str, Timeframe]]:
        """(instrument, timeframe) pairs this provider can currently serve."""

    @abstractmethod
    def fetch_bars(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> BarBatch:
        """Return canonical UTC bars for the range, or raise. Never empty-on-miss."""

    def describe(self) -> str:
        c = self.capabilities
        return (
            f"{c.name}: basis={c.native_price_basis.value} tz={c.native_timezone} "
            f"bid_ask={c.supports_bid_ask} volume={c.supports_real_volume}"
        )
