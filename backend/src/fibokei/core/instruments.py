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
    Instrument(symbol="EURUSD", name="Euro / US Dollar", asset_class=_FXM, ig_epic="CS.D.EURUSD.CFD.IP"),
    Instrument(symbol="GBPUSD", name="British Pound / US Dollar", asset_class=_FXM, ig_epic="CS.D.GBPUSD.CFD.IP"),
    Instrument(symbol="USDJPY", name="US Dollar / Japanese Yen", asset_class=_FXM, ig_epic="CS.D.USDJPY.CFD.IP"),
    Instrument(symbol="AUDUSD", name="Australian Dollar / US Dollar", asset_class=_FXM, ig_epic="CS.D.AUDUSD.CFD.IP"),
    Instrument(symbol="USDCHF", name="US Dollar / Swiss Franc", asset_class=_FXM, ig_epic="CS.D.USDCHF.CFD.IP"),
    Instrument(symbol="USDCAD", name="US Dollar / Canadian Dollar", asset_class=_FXM, ig_epic="CS.D.USDCAD.CFD.IP"),
    Instrument(symbol="NZDUSD", name="New Zealand Dollar / US Dollar", asset_class=_FXM, ig_epic="CS.D.NZDUSD.CFD.IP"),
    # ── Forex Cross (5) ──────────────────────────────────────────────────
    Instrument(symbol="EURJPY", name="Euro / Japanese Yen", asset_class=_FXC, ig_epic="CS.D.EURJPY.CFD.IP"),
    Instrument(symbol="GBPJPY", name="British Pound / Japanese Yen", asset_class=_FXC, ig_epic="CS.D.GBPJPY.CFD.IP"),
    Instrument(symbol="EURGBP", name="Euro / British Pound", asset_class=_FXC, ig_epic="CS.D.EURGBP.CFD.IP"),
    Instrument(symbol="AUDJPY", name="Australian Dollar / Japanese Yen", asset_class=_FXC, ig_epic="CS.D.AUDJPY.CFD.IP"),
    Instrument(symbol="EURAUD", name="Euro / Australian Dollar", asset_class=_FXC, ig_epic="CS.D.EURAUD.CFD.IP"),
    # ── Forex G10 Cross (17) ─────────────────────────────────────────────
    Instrument(symbol="AUDCAD", name="Australian Dollar / Canadian Dollar", asset_class=_G10, ig_epic="CS.D.AUDCAD.CFD.IP"),
    Instrument(symbol="AUDCHF", name="Australian Dollar / Swiss Franc", asset_class=_G10, ig_epic="CS.D.AUDCHF.CFD.IP"),
    Instrument(symbol="AUDNZD", name="Australian Dollar / New Zealand Dollar", asset_class=_G10, ig_epic="CS.D.AUDNZD.CFD.IP"),
    Instrument(symbol="CADCHF", name="Canadian Dollar / Swiss Franc", asset_class=_G10, ig_epic="CS.D.CADCHF.CFD.IP"),
    Instrument(symbol="CADJPY", name="Canadian Dollar / Japanese Yen", asset_class=_G10, ig_epic="CS.D.CADJPY.CFD.IP"),
    Instrument(symbol="CHFJPY", name="Swiss Franc / Japanese Yen", asset_class=_G10, ig_epic="CS.D.CHFJPY.CFD.IP"),
    Instrument(symbol="EURCAD", name="Euro / Canadian Dollar", asset_class=_G10, ig_epic="CS.D.EURCAD.CFD.IP"),
    Instrument(symbol="EURCHF", name="Euro / Swiss Franc", asset_class=_G10, ig_epic="CS.D.EURCHF.CFD.IP"),
    Instrument(symbol="EURNZD", name="Euro / New Zealand Dollar", asset_class=_G10, ig_epic="CS.D.EURNZD.CFD.IP"),
    Instrument(symbol="GBPAUD", name="British Pound / Australian Dollar", asset_class=_G10, ig_epic="CS.D.GBPAUD.CFD.IP"),
    Instrument(symbol="GBPCAD", name="British Pound / Canadian Dollar", asset_class=_G10, ig_epic="CS.D.GBPCAD.CFD.IP"),
    Instrument(symbol="GBPCHF", name="British Pound / Swiss Franc", asset_class=_G10, ig_epic="CS.D.GBPCHF.CFD.IP"),
    Instrument(symbol="GBPNZD", name="British Pound / New Zealand Dollar", asset_class=_G10, ig_epic="CS.D.GBPNZD.CFD.IP"),
    Instrument(symbol="NZDCAD", name="New Zealand Dollar / Canadian Dollar", asset_class=_G10, ig_epic="CS.D.NZDCAD.CFD.IP"),
    Instrument(symbol="NZDCHF", name="New Zealand Dollar / Swiss Franc", asset_class=_G10, ig_epic="CS.D.NZDCHF.CFD.IP"),
    Instrument(symbol="NZDJPY", name="New Zealand Dollar / Japanese Yen", asset_class=_G10, ig_epic="CS.D.NZDJPY.CFD.IP"),
    Instrument(symbol="SGDJPY", name="Singapore Dollar / Japanese Yen", asset_class=_G10, ig_epic="CS.D.SGDJPY.CFD.IP"),
    # ── Forex Scandinavian (4) ───────────────────────────────────────────
    Instrument(symbol="USDNOK", name="US Dollar / Norwegian Krone", asset_class=_SCN, ig_epic="CS.D.USDNOK.CFD.IP"),
    Instrument(symbol="USDSEK", name="US Dollar / Swedish Krona", asset_class=_SCN, ig_epic="CS.D.USDSEK.CFD.IP"),
    Instrument(symbol="EURNOK", name="Euro / Norwegian Krone", asset_class=_SCN, ig_epic="CS.D.EURNOK.CFD.IP"),
    Instrument(symbol="EURSEK", name="Euro / Swedish Krona", asset_class=_SCN, ig_epic="CS.D.EURSEK.CFD.IP"),
    # ── Forex EM (14) ────────────────────────────────────────────────────
    Instrument(symbol="USDSGD", name="US Dollar / Singapore Dollar", asset_class=_EM, ig_epic="CS.D.USDSGD.CFD.IP"),
    Instrument(symbol="USDHKD", name="US Dollar / Hong Kong Dollar", asset_class=_EM, ig_epic="CS.D.USDHKD.CFD.IP"),
    Instrument(symbol="USDTRY", name="US Dollar / Turkish Lira", asset_class=_EM, ig_epic="CS.D.USDTRY.CFD.IP"),
    Instrument(symbol="USDMXN", name="US Dollar / Mexican Peso", asset_class=_EM, ig_epic="CS.D.USDMXN.CFD.IP"),
    Instrument(symbol="USDZAR", name="US Dollar / South African Rand", asset_class=_EM, ig_epic="CS.D.USDZAR.CFD.IP"),
    Instrument(symbol="USDPLN", name="US Dollar / Polish Zloty", asset_class=_EM, ig_epic="CS.D.USDPLN.CFD.IP"),
    Instrument(symbol="USDCZK", name="US Dollar / Czech Koruna", asset_class=_EM, ig_epic="CS.D.USDCZK.CFD.IP"),
    Instrument(symbol="USDHUF", name="US Dollar / Hungarian Forint", asset_class=_EM, ig_epic="CS.D.USDHUF.CFD.IP"),
    Instrument(symbol="ZARJPY", name="South African Rand / Japanese Yen", asset_class=_EM, ig_epic="CS.D.ZARJPY.CFD.IP"),
    Instrument(symbol="EURTRY", name="Euro / Turkish Lira", asset_class=_EM, ig_epic="CS.D.EURTRY.CFD.IP"),
    Instrument(symbol="EURPLN", name="Euro / Polish Zloty", asset_class=_EM, ig_epic="CS.D.EURPLN.CFD.IP"),
    Instrument(symbol="EURCZK", name="Euro / Czech Koruna", asset_class=_EM, ig_epic="CS.D.EURCZK.CFD.IP"),
    Instrument(symbol="EURHUF", name="Euro / Hungarian Forint", asset_class=_EM, ig_epic="CS.D.EURHUF.CFD.IP"),
    Instrument(symbol="EURDKK", name="Euro / Danish Krone", asset_class=_EM, ig_epic="CS.D.EURDKK.CFD.IP"),
    # ── Commodity Metal (2) ──────────────────────────────────────────────
    Instrument(symbol="XAUUSD", name="Gold / US Dollar", asset_class=_MTL, ig_epic="CS.D.USCGC.TODAY.IP"),
    Instrument(symbol="XAGUSD", name="Silver / US Dollar", asset_class=_MTL, ig_epic="CS.D.USCSI.TODAY.IP"),
    # ── Commodity Energy (2 HistData + 1 alternate) ──────────────────────
    Instrument(symbol="BCOUSD", name="Brent Crude Oil", asset_class=_NRG, ig_epic="EN.D.LCO.Month2.IP"),
    Instrument(symbol="WTIUSD", name="WTI Crude Oil", asset_class=_NRG, ig_epic="EN.D.CL.Month1.IP"),
    Instrument(symbol="NATGAS", name="Natural Gas", asset_class=_NRG, has_canonical_data=False, ig_epic="EN.D.NG.Month1.IP"),
    # ── Index (9 HistData + 1 alternate) ─────────────────────────────────
    Instrument(symbol="US500", name="S&P 500", asset_class=_IDX, ig_epic="IX.D.SPTRD.IFD.IP"),
    Instrument(symbol="US100", name="Nasdaq 100", asset_class=_IDX, ig_epic="IX.D.NASDAQ.IFD.IP"),
    Instrument(symbol="UK100", name="FTSE 100", asset_class=_IDX, ig_epic="IX.D.FTSE.CFD.IP"),
    Instrument(symbol="DE40", name="Germany 40 / DAX", asset_class=_IDX, ig_epic="IX.D.DAX.IFD.IP"),
    Instrument(symbol="JP225", name="Japan 225 / Nikkei", asset_class=_IDX, ig_epic="IX.D.NIKKEI.IFD.IP"),
    Instrument(symbol="CAC40", name="CAC 40", asset_class=_IDX, ig_epic="IX.D.CAC.IFD.IP"),
    Instrument(symbol="AU200", name="Australia 200 / ASX", asset_class=_IDX, ig_epic="IX.D.ASX.IFD.IP"),
    Instrument(symbol="HK50", name="Hong Kong 50 / Hang Seng", asset_class=_IDX, ig_epic="IX.D.HANGSENG.IFD.IP"),
    Instrument(symbol="DXY", name="US Dollar Index", asset_class=_IDX),
    Instrument(
        symbol="US30", name="Dow Jones / Wall Street 30",
        asset_class=_IDX, has_canonical_data=False, ig_epic="IX.D.DOW.IFD.IP",
    ),
    # ── Crypto (5 — alternate provider only) ─────────────────────────
    Instrument(
        symbol="BTCUSD", name="Bitcoin / US Dollar",
        asset_class=_CRY, has_canonical_data=False, ig_epic="CS.D.BITCOIN.CFD.IP",
    ),
    Instrument(
        symbol="ETHUSD", name="Ethereum / US Dollar",
        asset_class=_CRY, has_canonical_data=False, ig_epic="CS.D.ETHUSD.CFD.IP",
    ),
    Instrument(
        symbol="SOLUSD", name="Solana / US Dollar",
        asset_class=_CRY, has_canonical_data=False,
    ),
    Instrument(
        symbol="LTCUSD", name="Litecoin / US Dollar",
        asset_class=_CRY, has_canonical_data=False, ig_epic="CS.D.LTCUSD.CFD.IP",
    ),
    Instrument(
        symbol="XRPUSD", name="Ripple / US Dollar",
        asset_class=_CRY, has_canonical_data=False,
    ),
]

_INSTRUMENT_MAP = {inst.symbol: inst for inst in INSTRUMENTS}
_EPIC_TO_SYMBOL = {inst.ig_epic: inst.symbol for inst in INSTRUMENTS if inst.ig_epic}


def get_instrument(symbol: str) -> Instrument:
    """Get instrument by symbol. Raises KeyError if not found."""
    if symbol not in _INSTRUMENT_MAP:
        raise KeyError(f"Unknown instrument: {symbol}")
    return _INSTRUMENT_MAP[symbol]


def get_instruments_by_class(asset_class: AssetClass) -> list[Instrument]:
    """Get all instruments for an asset class."""
    return [inst for inst in INSTRUMENTS if inst.asset_class == asset_class]


def get_ig_epic(symbol: str) -> str:
    """Get IG epic code for a Fiboki symbol. Raises KeyError if no mapping."""
    inst = get_instrument(symbol)
    if inst.ig_epic is None:
        raise KeyError(f"No IG epic mapping for {symbol}")
    return inst.ig_epic


def get_symbol_by_epic(epic: str) -> str:
    """Get Fiboki symbol from an IG epic code. Raises KeyError if unknown."""
    if epic not in _EPIC_TO_SYMBOL:
        raise KeyError(f"Unknown IG epic: {epic}")
    return _EPIC_TO_SYMBOL[epic]


def get_ig_supported_instruments() -> list[Instrument]:
    """Return all instruments with IG epic mappings."""
    return [inst for inst in INSTRUMENTS if inst.ig_epic is not None]
