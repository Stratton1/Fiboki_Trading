"""Supported instrument universe for Fiboki (67 instruments).

60 instruments with HistData canonical data (60 x 6 timeframes = 360 datasets)
plus 7 alternate-provider instruments (NATGAS, US30, BTCUSD, ETHUSD, SOLUSD,
LTCUSD, XRPUSD).
"""

from fibokei.core.models import AssetClass, Instrument

_FXM = AssetClass.FOREX_MAJOR
_FXC = AssetClass.FOREX_CROSS
_G10 = AssetClass.FOREX_G10_CROSS
_SCN = AssetClass.FOREX_SCANDINAVIAN
_EM = AssetClass.FOREX_EM
_MTL = AssetClass.COMMODITY_METAL
_NRG = AssetClass.COMMODITY_ENERGY
_IDX = AssetClass.INDEX
_CRY = AssetClass.CRYPTO

INSTRUMENTS: list[Instrument] = [
    # ── Forex Major (7) ──────────────────────────────────────────────────
    Instrument(symbol="EURUSD", name="Euro / US Dollar", asset_class=_FXM),
    Instrument(symbol="GBPUSD", name="British Pound / US Dollar", asset_class=_FXM),
    Instrument(symbol="USDJPY", name="US Dollar / Japanese Yen", asset_class=_FXM),
    Instrument(symbol="AUDUSD", name="Australian Dollar / US Dollar", asset_class=_FXM),
    Instrument(symbol="USDCHF", name="US Dollar / Swiss Franc", asset_class=_FXM),
    Instrument(symbol="USDCAD", name="US Dollar / Canadian Dollar", asset_class=_FXM),
    Instrument(symbol="NZDUSD", name="New Zealand Dollar / US Dollar", asset_class=_FXM),
    # ── Forex Cross (5) ──────────────────────────────────────────────────
    Instrument(symbol="EURJPY", name="Euro / Japanese Yen", asset_class=_FXC),
    Instrument(symbol="GBPJPY", name="British Pound / Japanese Yen", asset_class=_FXC),
    Instrument(symbol="EURGBP", name="Euro / British Pound", asset_class=_FXC),
    Instrument(symbol="AUDJPY", name="Australian Dollar / Japanese Yen", asset_class=_FXC),
    Instrument(symbol="EURAUD", name="Euro / Australian Dollar", asset_class=_FXC),
    # ── Forex G10 Cross (17) ─────────────────────────────────────────────
    Instrument(symbol="AUDCAD", name="Australian Dollar / Canadian Dollar", asset_class=_G10),
    Instrument(symbol="AUDCHF", name="Australian Dollar / Swiss Franc", asset_class=_G10),
    Instrument(symbol="AUDNZD", name="Australian Dollar / New Zealand Dollar", asset_class=_G10),
    Instrument(symbol="CADCHF", name="Canadian Dollar / Swiss Franc", asset_class=_G10),
    Instrument(symbol="CADJPY", name="Canadian Dollar / Japanese Yen", asset_class=_G10),
    Instrument(symbol="CHFJPY", name="Swiss Franc / Japanese Yen", asset_class=_G10),
    Instrument(symbol="EURCAD", name="Euro / Canadian Dollar", asset_class=_G10),
    Instrument(symbol="EURCHF", name="Euro / Swiss Franc", asset_class=_G10),
    Instrument(symbol="EURNZD", name="Euro / New Zealand Dollar", asset_class=_G10),
    Instrument(symbol="GBPAUD", name="British Pound / Australian Dollar", asset_class=_G10),
    Instrument(symbol="GBPCAD", name="British Pound / Canadian Dollar", asset_class=_G10),
    Instrument(symbol="GBPCHF", name="British Pound / Swiss Franc", asset_class=_G10),
    Instrument(symbol="GBPNZD", name="British Pound / New Zealand Dollar", asset_class=_G10),
    Instrument(symbol="NZDCAD", name="New Zealand Dollar / Canadian Dollar", asset_class=_G10),
    Instrument(symbol="NZDCHF", name="New Zealand Dollar / Swiss Franc", asset_class=_G10),
    Instrument(symbol="NZDJPY", name="New Zealand Dollar / Japanese Yen", asset_class=_G10),
    Instrument(symbol="SGDJPY", name="Singapore Dollar / Japanese Yen", asset_class=_G10),
    # ── Forex Scandinavian (4) ───────────────────────────────────────────
    Instrument(symbol="USDNOK", name="US Dollar / Norwegian Krone", asset_class=_SCN),
    Instrument(symbol="USDSEK", name="US Dollar / Swedish Krona", asset_class=_SCN),
    Instrument(symbol="EURNOK", name="Euro / Norwegian Krone", asset_class=_SCN),
    Instrument(symbol="EURSEK", name="Euro / Swedish Krona", asset_class=_SCN),
    # ── Forex EM (14) ────────────────────────────────────────────────────
    Instrument(symbol="USDSGD", name="US Dollar / Singapore Dollar", asset_class=_EM),
    Instrument(symbol="USDHKD", name="US Dollar / Hong Kong Dollar", asset_class=_EM),
    Instrument(symbol="USDTRY", name="US Dollar / Turkish Lira", asset_class=_EM),
    Instrument(symbol="USDMXN", name="US Dollar / Mexican Peso", asset_class=_EM),
    Instrument(symbol="USDZAR", name="US Dollar / South African Rand", asset_class=_EM),
    Instrument(symbol="USDPLN", name="US Dollar / Polish Zloty", asset_class=_EM),
    Instrument(symbol="USDCZK", name="US Dollar / Czech Koruna", asset_class=_EM),
    Instrument(symbol="USDHUF", name="US Dollar / Hungarian Forint", asset_class=_EM),
    Instrument(symbol="ZARJPY", name="South African Rand / Japanese Yen", asset_class=_EM),
    Instrument(symbol="EURTRY", name="Euro / Turkish Lira", asset_class=_EM),
    Instrument(symbol="EURPLN", name="Euro / Polish Zloty", asset_class=_EM),
    Instrument(symbol="EURCZK", name="Euro / Czech Koruna", asset_class=_EM),
    Instrument(symbol="EURHUF", name="Euro / Hungarian Forint", asset_class=_EM),
    Instrument(symbol="EURDKK", name="Euro / Danish Krone", asset_class=_EM),
    # ── Commodity Metal (2) ──────────────────────────────────────────────
    Instrument(symbol="XAUUSD", name="Gold / US Dollar", asset_class=_MTL),
    Instrument(symbol="XAGUSD", name="Silver / US Dollar", asset_class=_MTL),
    # ── Commodity Energy (2 HistData + 1 alternate) ──────────────────────
    Instrument(symbol="BCOUSD", name="Brent Crude Oil", asset_class=_NRG),
    Instrument(symbol="WTIUSD", name="WTI Crude Oil", asset_class=_NRG),
    Instrument(symbol="NATGAS", name="Natural Gas", asset_class=_NRG, has_canonical_data=False),
    # ── Index (9 HistData + 1 alternate) ─────────────────────────────────
    Instrument(symbol="US500", name="S&P 500", asset_class=_IDX),
    Instrument(symbol="US100", name="Nasdaq 100", asset_class=_IDX),
    Instrument(symbol="UK100", name="FTSE 100", asset_class=_IDX),
    Instrument(symbol="DE40", name="Germany 40 / DAX", asset_class=_IDX),
    Instrument(symbol="JP225", name="Japan 225 / Nikkei", asset_class=_IDX),
    Instrument(symbol="CAC40", name="CAC 40", asset_class=_IDX),
    Instrument(symbol="AU200", name="Australia 200 / ASX", asset_class=_IDX),
    Instrument(symbol="HK50", name="Hong Kong 50 / Hang Seng", asset_class=_IDX),
    Instrument(symbol="DXY", name="US Dollar Index", asset_class=_IDX),
    Instrument(
        symbol="US30", name="Dow Jones / Wall Street 30",
        asset_class=_IDX, has_canonical_data=False,
    ),
    # ── Crypto (5 — alternate provider only) ─────────────────────────
    Instrument(
        symbol="BTCUSD", name="Bitcoin / US Dollar",
        asset_class=_CRY, has_canonical_data=False,
    ),
    Instrument(
        symbol="ETHUSD", name="Ethereum / US Dollar",
        asset_class=_CRY, has_canonical_data=False,
    ),
    Instrument(
        symbol="SOLUSD", name="Solana / US Dollar",
        asset_class=_CRY, has_canonical_data=False,
    ),
    Instrument(
        symbol="LTCUSD", name="Litecoin / US Dollar",
        asset_class=_CRY, has_canonical_data=False,
    ),
    Instrument(
        symbol="XRPUSD", name="Ripple / US Dollar",
        asset_class=_CRY, has_canonical_data=False,
    ),
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
