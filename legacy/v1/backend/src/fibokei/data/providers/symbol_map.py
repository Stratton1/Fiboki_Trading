"""Centralised symbol mapping between Fiboki and external data providers.

Every provider has its own naming convention.  This module is the single
source of truth for translating between them.

Usage::

    from fibokei.data.providers.symbol_map import (
        to_provider_symbol,
        to_fiboki_symbol,
        list_mapped_symbols,
    )

    # Fiboki → HistData
    to_provider_symbol("EURUSD", ProviderID.HISTDATA)   # "EURUSD"
    to_provider_symbol("XAUUSD", ProviderID.HISTDATA)   # "XAUUSD"

    # HistData → Fiboki
    to_fiboki_symbol("EURUSD", ProviderID.HISTDATA)     # "EURUSD"
"""

from __future__ import annotations

from fibokei.data.providers.base import ProviderID

# ---------------------------------------------------------------------------
# Mapping tables
#
# Keys are Fiboki canonical symbols.
# Values are the provider-native symbol strings.
#
# If a Fiboki symbol is absent from a provider's map, that instrument is
# not available from that provider.
# ---------------------------------------------------------------------------

_HISTDATA_MAP: dict[str, str] = {
    # Forex majors (7)
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "AUDUSD": "AUDUSD",
    "USDCHF": "USDCHF",
    "USDCAD": "USDCAD",
    "NZDUSD": "NZDUSD",
    # Forex crosses — G10 (22)
    "EURJPY": "EURJPY",
    "GBPJPY": "GBPJPY",
    "EURGBP": "EURGBP",
    "AUDJPY": "AUDJPY",
    "EURAUD": "EURAUD",
    "AUDCAD": "AUDCAD",
    "AUDCHF": "AUDCHF",
    "AUDNZD": "AUDNZD",
    "CADCHF": "CADCHF",
    "CADJPY": "CADJPY",
    "CHFJPY": "CHFJPY",
    "EURCAD": "EURCAD",
    "EURCHF": "EURCHF",
    "EURNZD": "EURNZD",
    "GBPAUD": "GBPAUD",
    "GBPCAD": "GBPCAD",
    "GBPCHF": "GBPCHF",
    "GBPNZD": "GBPNZD",
    "NZDCAD": "NZDCAD",
    "NZDCHF": "NZDCHF",
    "NZDJPY": "NZDJPY",
    "SGDJPY": "SGDJPY",
    # Forex exotic — Scandinavian (4)
    "USDNOK": "USDNOK",
    "USDSEK": "USDSEK",
    "EURNOK": "EURNOK",
    "EURSEK": "EURSEK",
    # Forex exotic — EM (9)
    "USDSGD": "USDSGD",
    "USDHKD": "USDHKD",
    "USDTRY": "USDTRY",
    "USDMXN": "USDMXN",
    "USDZAR": "USDZAR",
    "USDPLN": "USDPLN",
    "USDCZK": "USDCZK",
    "USDHUF": "USDHUF",
    "ZARJPY": "ZARJPY",
    # Forex exotic — EUR EM (5)
    "EURTRY": "EURTRY",
    "EURPLN": "EURPLN",
    "EURCZK": "EURCZK",
    "EURHUF": "EURHUF",
    "EURDKK": "EURDKK",
    # Metals (2)
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
    # Energy (2)
    "BCOUSD": "BCOUSD",
    "WTIUSD": "WTIUSD",
    # Indices (7) — HistData uses different tickers
    "US500": "SPXUSD",     # S&P 500
    "US100": "NSXUSD",     # Nasdaq 100
    "UK100": "UKXGBP",     # FTSE 100
    "DE40": "GRXEUR",      # DAX 40
    "JP225": "JPXJPY",     # Nikkei 225
    "CAC40": "FRXEUR",     # CAC 40
    "AU200": "AUXAUD",     # ASX 200
    # Indices — additional (2)
    "HK50": "HKXHKD",     # Hang Seng 50
    "DXY": "UDXUSD",      # US Dollar Index
}

_DUKASCOPY_MAP: dict[str, str] = {
    # Forex majors
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "AUDUSD": "AUDUSD",
    "USDCHF": "USDCHF",
    "USDCAD": "USDCAD",
    "NZDUSD": "NZDUSD",
    # Forex crosses
    "EURJPY": "EURJPY",
    "GBPJPY": "GBPJPY",
    "EURGBP": "EURGBP",
    "AUDJPY": "AUDJPY",
    "EURAUD": "EURAUD",
    # Metals
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
    # Energy
    "BCOUSD": "BCOUSD",
    "WTIUSD": "WTIUSD",
    # Indices
    "US500": "USA500IDXUSD",
    "US100": "USATECHIDXUSD",
    "US30": "USA30IDXUSD",
    "DE40": "DEUIDXEUR",
    "UK100": "GBRIDXGBP",
    "JP225": "JPNIDXJPY",
    # Crypto
    "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD",
}

_YAHOO_MAP: dict[str, str] = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCHF": "CHF=X",
    "USDCAD": "CAD=X",
    "NZDUSD": "NZDUSD=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "AUDJPY": "AUDJPY=X",
    "EURAUD": "EURAUD=X",
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "BCOUSD": "BZ=F",
    "WTIUSD": "CL=F",
    "NATGAS": "NG=F",
    "US500": "^GSPC",
    "US100": "^IXIC",
    "US30": "^DJI",
    "DE40": "^GDAXI",
    "UK100": "^FTSE",
    "JP225": "^N225",
    "HK50": "^HSI",
    "AU200": "^AXJO",
    "CAC40": "^FCHI",
    "DXY": "DX-Y.NYB",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "LTCUSD": "LTC-USD",
    "XRPUSD": "XRP-USD",
}

# Master registry: provider ID → mapping dict
_PROVIDER_MAPS: dict[ProviderID, dict[str, str]] = {
    ProviderID.HISTDATA: _HISTDATA_MAP,
    ProviderID.DUKASCOPY: _DUKASCOPY_MAP,
    ProviderID.YAHOO: _YAHOO_MAP,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_provider_symbol(fiboki_symbol: str, provider: ProviderID) -> str:
    """Convert a Fiboki canonical symbol to the provider's native symbol.

    Raises ``KeyError`` if the symbol is not mapped for that provider.
    """
    mapping = _PROVIDER_MAPS.get(provider, {})
    if fiboki_symbol not in mapping:
        raise KeyError(
            f"Symbol {fiboki_symbol!r} is not mapped for provider {provider.value!r}"
        )
    return mapping[fiboki_symbol]


def to_fiboki_symbol(provider_symbol: str, provider: ProviderID) -> str:
    """Convert a provider-native symbol back to the Fiboki canonical symbol.

    Raises ``KeyError`` if no reverse mapping exists.
    """
    mapping = _PROVIDER_MAPS.get(provider, {})
    reverse = {v: k for k, v in mapping.items()}
    if provider_symbol not in reverse:
        # Try identity mapping — many provider symbols match Fiboki symbols
        if provider_symbol in mapping:
            return provider_symbol
        raise KeyError(
            f"Provider symbol {provider_symbol!r} has no Fiboki mapping "
            f"for provider {provider.value!r}"
        )
    return reverse[provider_symbol]


def list_mapped_symbols(provider: ProviderID) -> list[str]:
    """Return all Fiboki symbols available from the given provider."""
    return sorted(_PROVIDER_MAPS.get(provider, {}).keys())


def provider_has_symbol(fiboki_symbol: str, provider: ProviderID) -> bool:
    """Check whether a provider can supply data for a Fiboki symbol."""
    return fiboki_symbol in _PROVIDER_MAPS.get(provider, {})
