"""Instrument contract specifications.

A contract spec is a *market fact*, not a strategy choice. Everything that
converts between price movement and account currency reads from here, so that
no strategy, engine or adapter re-derives it. V1's bug was pip-value logic
scattered across sizing, the paper bot and the IG adapter, each disagreeing.
"""
from __future__ import annotations

from dataclasses import dataclass

from fiboki.core.enums import AssetClass


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    asset_class: AssetClass
    base: str
    quote: str
    pip_size: float
    contract_size: float = 1.0
    min_size: float = 0.01
    size_step: float = 0.01
    price_precision: int = 5
    typical_spread_pips: float = 1.0
    retail_leverage: float = 20.0
    annual_financing_bps: float = 250.0
    trading_hours: str = "fx_24_5"

    @property
    def is_fx(self) -> bool:
        return self.asset_class in (AssetClass.FX_MAJOR, AssetClass.FX_CROSS)

    @property
    def pip_value_quote_ccy(self) -> float:
        """Value of one pip, per unit, in the QUOTE currency."""
        return self.pip_size * self.contract_size


_ISO = {
    "AUD", "CAD", "CHF", "CNH", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "ILS",
    "JPY", "MXN", "NOK", "NZD", "PLN", "SEK", "SGD", "TRY", "USD", "ZAR",
}

# FCA/ESMA retail leverage caps (initial margin 1/cap).
#
# Sources, both retained in UK law after 2020:
#   * ESMA Decision (EU) 2018/796, Article 2 and Annex II (CFD product
#     intervention measures);
#   * FCA Policy Statement PS19/18 (restricting CFDs to retail clients), COBS
#     22.5.
#
# The caps are 30:1 for "major currency pairs", 20:1 for non-major currency
# pairs, gold and major indices, 10:1 for commodities other than gold and for
# non-major equity indices, 5:1 for individual equities, 2:1 for crypto.
#
# A "major currency pair" is DEFINED BY THE CURRENCY SET, not by the colloquial
# list of USD pairs: any pair made of two of USD, EUR, JPY, GBP, CAD and CHF.
# So AUDUSD and NZDUSD are non-major (20:1) and EURGBP, EURJPY, GBPJPY, EURCHF,
# CADJPY, CHFJPY, GBPCAD, GBPCHF, EURCAD and CADCHF are major (30:1). The
# earlier symbol list here had both halves backwards: permissive on AUDUSD and
# NZDUSD, conservative on the ten crosses. ``tests/golden/
# test_golden_retail_leverage.py`` pins every registered instrument.
#
# "Major indices" is a closed list in the same Annex: FTSE 100, CAC 40, DAX 30,
# Dow Jones Industrial Average, S&P 500, NASDAQ Composite, NASDAQ 100, Nikkei
# 225, S&P/ASX 200 and EURO STOXX 50. The Hang Seng is not on it, so HK50 is
# 10:1. Silver is a "commodity other than gold", so XAGUSD is 10:1.
ESMA_MAJOR_CURRENCIES: frozenset[str] = frozenset({"USD", "EUR", "JPY", "GBP", "CAD", "CHF"})

_RETAIL_LEVERAGE = {
    AssetClass.FX_MAJOR: 30.0,
    AssetClass.FX_CROSS: 20.0,
    AssetClass.METAL: 20.0,   # gold only; other metals are set per instrument
    AssetClass.INDEX: 20.0,   # major indices only; others are set per instrument
    AssetClass.ENERGY: 10.0,
    AssetClass.CRYPTO: 2.0,
    AssetClass.EQUITY: 5.0,
}

#: Retail leverage for a commodity other than gold, and for an index that is not
#: on the ESMA major-index list.
NON_MAJOR_COMMODITY_LEVERAGE = 10.0
NON_MAJOR_INDEX_LEVERAGE = 10.0

#: Registered index symbols that ARE on the ESMA/FCA major-index list.
ESMA_MAJOR_INDICES: frozenset[str] = frozenset(
    {"US500", "US100", "US30", "UK100", "DE40", "FR40", "JP225", "AU200", "EU50"}
)


def is_esma_major_pair(base: str, quote: str) -> bool:
    """True when BOTH legs are ESMA major currencies (ESMA 2018/796 Annex II)."""
    return base.upper() in ESMA_MAJOR_CURRENCIES and quote.upper() in ESMA_MAJOR_CURRENCIES


def _fx(symbol: str, spread: float) -> Instrument:
    """Register an FX pair. Its class, and so its cap, follow the currency set.

    ``AssetClass.FX_MAJOR`` therefore means the REGULATORY major, not the
    colloquial "USD major": AUDUSD is ``FX_CROSS`` and EURGBP is ``FX_MAJOR``.
    Every consumer of the class outside this module treats the two FX classes
    alike, so only the leverage cap reads the difference.
    """
    base, quote = symbol[:3], symbol[3:]
    jpy = quote == "JPY"
    cls = AssetClass.FX_MAJOR if is_esma_major_pair(base, quote) else AssetClass.FX_CROSS
    return Instrument(
        symbol=symbol, asset_class=cls, base=base, quote=quote,
        pip_size=0.01 if jpy else 0.0001,
        price_precision=3 if jpy else 5,
        typical_spread_pips=spread,
        retail_leverage=_RETAIL_LEVERAGE[cls],
        min_size=1.0, size_step=1.0,
    )


_REGISTRY: dict[str, Instrument] = {}


def _add(i: Instrument) -> Instrument:
    _REGISTRY[i.symbol] = i
    return i


for _s, _sp in [
    ("EURUSD", 0.9), ("GBPUSD", 1.2), ("USDJPY", 0.9), ("USDCHF", 1.3),
    ("USDCAD", 1.4), ("AUDUSD", 1.0), ("NZDUSD", 1.6),
    ("EURGBP", 1.1), ("EURJPY", 1.4), ("GBPJPY", 2.2), ("EURCHF", 1.5),
    ("AUDJPY", 1.6), ("CADJPY", 1.9), ("CHFJPY", 2.2), ("EURAUD", 1.9),
    ("EURCAD", 2.0), ("GBPAUD", 2.6), ("GBPCAD", 2.8), ("GBPCHF", 2.5),
    ("AUDCAD", 1.8), ("AUDCHF", 1.9), ("AUDNZD", 2.2), ("NZDCAD", 2.4),
    ("NZDJPY", 2.0), ("CADCHF", 2.0), ("NZDCHF", 2.5), ("EURNZD", 3.0),
]:
    _add(_fx(_s, _sp))

_add(Instrument("XAUUSD", AssetClass.METAL, "XAU", "USD", 0.01, 1.0,
                min_size=0.01, size_step=0.01, price_precision=2,
                typical_spread_pips=30.0, retail_leverage=20.0,
                annual_financing_bps=300.0))
_add(Instrument("XAGUSD", AssetClass.METAL, "XAG", "USD", 0.001, 1.0,
                min_size=0.05, size_step=0.05, price_precision=3,
                typical_spread_pips=25.0, retail_leverage=NON_MAJOR_COMMODITY_LEVERAGE,
                annual_financing_bps=300.0))
_add(Instrument("WTIUSD", AssetClass.ENERGY, "WTI", "USD", 0.01, 1.0,
                min_size=0.1, size_step=0.1, price_precision=2,
                typical_spread_pips=3.0, retail_leverage=10.0,
                annual_financing_bps=350.0, trading_hours="energy"))
_add(Instrument("BCOUSD", AssetClass.ENERGY, "BCO", "USD", 0.01, 1.0,
                min_size=0.1, size_step=0.1, price_precision=2,
                typical_spread_pips=3.0, retail_leverage=10.0,
                annual_financing_bps=350.0, trading_hours="energy"))

for _s, _q, _sp, _prec in [
    ("US500", "USD", 0.4, 1), ("US100", "USD", 1.0, 1), ("US30", "USD", 1.6, 1),
    ("UK100", "GBP", 1.0, 1), ("DE40", "EUR", 1.2, 1), ("FR40", "EUR", 1.0, 1),
    ("JP225", "JPY", 7.0, 0), ("AU200", "AUD", 1.0, 1), ("HK50", "HKD", 5.0, 1),
    ("EU50", "EUR", 1.5, 1),
]:
    _add(Instrument(_s, AssetClass.INDEX, _s, _q, 1.0, 1.0,
                    min_size=0.1, size_step=0.1, price_precision=_prec,
                    typical_spread_pips=_sp,
                    retail_leverage=(
                        _RETAIL_LEVERAGE[AssetClass.INDEX]
                        if _s in ESMA_MAJOR_INDICES
                        else NON_MAJOR_INDEX_LEVERAGE
                    ),
                    annual_financing_bps=300.0, trading_hours="index"))


def get(symbol: str) -> Instrument:
    key = symbol.upper()
    if key not in _REGISTRY:
        raise KeyError(
            f"Unknown instrument {symbol!r}. Register it in core/instruments.py — "
            "V2 refuses to guess contract specs, because guessing is how V1 "
            "produced wrong position sizes."
        )
    return _REGISTRY[key]


def exists(symbol: str) -> bool:
    return symbol.upper() in _REGISTRY


def all_symbols() -> list[str]:
    return sorted(_REGISTRY)


def by_asset_class(cls: AssetClass) -> list[Instrument]:
    return [i for i in _REGISTRY.values() if i.asset_class is cls]


def is_iso_currency(code: str) -> bool:
    return code.upper() in _ISO
