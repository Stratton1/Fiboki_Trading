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

# FCA/ESMA retail leverage. Source: FCA PS19/18 and ESMA product intervention.
_RETAIL_LEVERAGE = {
    AssetClass.FX_MAJOR: 30.0,
    AssetClass.FX_CROSS: 20.0,
    AssetClass.METAL: 20.0,
    AssetClass.INDEX: 20.0,
    AssetClass.ENERGY: 10.0,
    AssetClass.CRYPTO: 2.0,
    AssetClass.EQUITY: 5.0,
}

_MAJORS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"}


def _fx(symbol: str, spread: float) -> Instrument:
    base, quote = symbol[:3], symbol[3:]
    jpy = quote == "JPY"
    cls = AssetClass.FX_MAJOR if symbol in _MAJORS else AssetClass.FX_CROSS
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
                typical_spread_pips=25.0, retail_leverage=20.0,
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
                    typical_spread_pips=_sp, retail_leverage=20.0,
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
