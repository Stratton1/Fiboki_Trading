"""30-instrument launch universe for Fiboki."""

from fibokei.core.models import AssetClass, Instrument

_FXM = AssetClass.FOREX_MAJOR
_FXC = AssetClass.FOREX_CROSS
_MTL = AssetClass.COMMODITY_METAL
_NRG = AssetClass.COMMODITY_ENERGY
_IDX = AssetClass.INDEX
_CRY = AssetClass.CRYPTO

INSTRUMENTS: list[Instrument] = [
    # Forex Major (7)
    Instrument(symbol="EURUSD", name="Euro / US Dollar", asset_class=_FXM),
    Instrument(symbol="GBPUSD", name="British Pound / US Dollar", asset_class=_FXM),
    Instrument(symbol="USDJPY", name="US Dollar / Japanese Yen", asset_class=_FXM),
    Instrument(symbol="AUDUSD", name="Australian Dollar / US Dollar", asset_class=_FXM),
    Instrument(symbol="USDCHF", name="US Dollar / Swiss Franc", asset_class=_FXM),
    Instrument(symbol="USDCAD", name="US Dollar / Canadian Dollar", asset_class=_FXM),
    Instrument(symbol="NZDUSD", name="New Zealand Dollar / US Dollar", asset_class=_FXM),
    # Forex Cross (5)
    Instrument(symbol="EURJPY", name="Euro / Japanese Yen", asset_class=_FXC),
    Instrument(symbol="GBPJPY", name="British Pound / Japanese Yen", asset_class=_FXC),
    Instrument(symbol="EURGBP", name="Euro / British Pound", asset_class=_FXC),
    Instrument(symbol="AUDJPY", name="Australian Dollar / Japanese Yen", asset_class=_FXC),
    Instrument(symbol="EURAUD", name="Euro / Australian Dollar", asset_class=_FXC),
    # Commodities (5)
    Instrument(symbol="XAUUSD", name="Gold / US Dollar", asset_class=_MTL),
    Instrument(symbol="XAGUSD", name="Silver / US Dollar", asset_class=_MTL),
    Instrument(symbol="BCOUSD", name="Brent Crude Oil", asset_class=_NRG),
    Instrument(symbol="WTIUSD", name="WTI Crude Oil", asset_class=_NRG),
    Instrument(symbol="NATGAS", name="Natural Gas", asset_class=_NRG),
    # Indices (8)
    Instrument(symbol="US500", name="S&P 500", asset_class=_IDX),
    Instrument(symbol="US100", name="Nasdaq 100", asset_class=_IDX),
    Instrument(symbol="US30", name="Dow Jones / Wall Street 30", asset_class=_IDX),
    Instrument(symbol="DE40", name="Germany 40 / DAX", asset_class=_IDX),
    Instrument(symbol="UK100", name="FTSE 100", asset_class=_IDX),
    Instrument(symbol="JP225", name="Japan 225 / Nikkei", asset_class=_IDX),
    Instrument(symbol="HK50", name="Hong Kong 50 / Hang Seng", asset_class=_IDX),
    Instrument(symbol="AU200", name="Australia 200 / ASX", asset_class=_IDX),
    # Crypto (5)
    Instrument(symbol="BTCUSD", name="Bitcoin / US Dollar", asset_class=_CRY),
    Instrument(symbol="ETHUSD", name="Ethereum / US Dollar", asset_class=_CRY),
    Instrument(symbol="SOLUSD", name="Solana / US Dollar", asset_class=_CRY),
    Instrument(symbol="LTCUSD", name="Litecoin / US Dollar", asset_class=_CRY),
    Instrument(symbol="XRPUSD", name="Ripple / US Dollar", asset_class=_CRY),
]

_INSTRUMENT_MAP = {inst.symbol: inst for inst in INSTRUMENTS}


def get_instrument(symbol: str) -> Instrument:
    """Get instrument by symbol. Raises KeyError if not found."""
    if symbol not in _INSTRUMENT_MAP:
        raise KeyError(f"Unknown instrument: {symbol}")
    return _INSTRUMENT_MAP[symbol]


def get_instruments_by_class(asset_class: AssetClass) -> list[Instrument]:
    """Get all instruments for an asset class."""
    return [inst for inst in INSTRUMENTS if inst.asset_class == asset_class]
