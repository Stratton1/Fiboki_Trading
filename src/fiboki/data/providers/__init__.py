"""Market data providers.

A provider's contract is to declare what its data actually is — which side of
the book, in which timezone convention, complete or still forming — and to
convert to canonical UTC bars itself. A provider that does not know its own
convention must say so rather than let the platform assume one.
"""
from __future__ import annotations

from fiboki.data.providers.alfred import AlfredProvider
from fiboki.data.providers.base import (
    AuthenticationRequired,
    BarBatch,
    BarProvider,
    ProviderCapabilities,
    ProviderError,
    RateLimited,
)
from fiboki.data.providers.boe_iadb import BoeIadbProvider
from fiboki.data.providers.cftc_cot import CftcCotProvider
from fiboki.data.providers.dukascopy import DukascopyProvider
from fiboki.data.providers.ecb_sdmx import EcbSdmxProvider
from fiboki.data.providers.histdata import HistDataParquetProvider
from fiboki.data.providers.macro_base import (
    AvailabilityBasis,
    MacroDataset,
    MacroDatasetStore,
    MacroProviderDescriptor,
)
from fiboki.data.providers.nyfed import NyFedMarketsProvider
from fiboki.data.providers.oanda import OandaCandlesProvider
from fiboki.data.providers.ons import OnsTimeseriesProvider

# Point-in-time macro providers (DATA_ARCHITECTURE.md §14) are not bar
# providers: each declares when a value became knowable (``available_at``)
# instead of a price basis. See ``macro_base``.
#: name -> class, for the ``fiboki macro`` CLI and for describe-all listings.
MACRO_PROVIDERS: dict[str, type] = {
    "alfred": AlfredProvider,
    "cftc_cot": CftcCotProvider,
    "ecb_sdmx": EcbSdmxProvider,
    "boe_iadb": BoeIadbProvider,
    "ons": OnsTimeseriesProvider,
    "nyfed": NyFedMarketsProvider,
}

__all__ = [
    "MACRO_PROVIDERS",
    "AlfredProvider",
    "AuthenticationRequired",
    "AvailabilityBasis",
    "BarBatch",
    "BarProvider",
    "BoeIadbProvider",
    "CftcCotProvider",
    "DukascopyProvider",
    "EcbSdmxProvider",
    "HistDataParquetProvider",
    "MacroDataset",
    "MacroDatasetStore",
    "MacroProviderDescriptor",
    "NyFedMarketsProvider",
    "OandaCandlesProvider",
    "OnsTimeseriesProvider",
    "ProviderCapabilities",
    "ProviderError",
    "RateLimited",
]
