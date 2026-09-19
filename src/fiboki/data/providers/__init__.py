"""Market data providers.

A provider's contract is to declare what its data actually is — which side of
the book, in which timezone convention, complete or still forming — and to
convert to canonical UTC bars itself. A provider that does not know its own
convention must say so rather than let the platform assume one.
"""
from __future__ import annotations

from fiboki.data.providers.base import (
    AuthenticationRequired,
    BarBatch,
    BarProvider,
    ProviderCapabilities,
    ProviderError,
    RateLimited,
)
from fiboki.data.providers.dukascopy import DukascopyProvider
from fiboki.data.providers.histdata import HistDataParquetProvider
from fiboki.data.providers.oanda import OandaCandlesProvider

__all__ = [
    "AuthenticationRequired",
    "BarBatch",
    "BarProvider",
    "DukascopyProvider",
    "HistDataParquetProvider",
    "OandaCandlesProvider",
    "ProviderCapabilities",
    "ProviderError",
    "RateLimited",
]
