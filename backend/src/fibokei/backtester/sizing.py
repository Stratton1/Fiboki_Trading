"""Position sizing with leverage caps and pip value conversion.

This module enforces economic realism in backtests by:
1. Capping position size to IG-aligned leverage ratios per asset class
2. Converting PnL to account currency for JPY-quoted pairs
3. Providing realistic spread defaults per instrument class

Leverage limits follow FCA retail CFD rules as applied by IG:
  - FX majors: 30:1
  - FX minors/crosses: 20:1
  - Gold/silver: 20:1
  - Indices: 20:1
  - Oil/energy: 10:1
  - Crypto: 2:1

Account currency caveat: the engine treats all PnL as if denominated
in the same currency as the account. For USD-quoted instruments traded
from a GBP account, this is an approximation (no USD→GBP conversion).
This is documented and acceptable for current backtest purposes.
"""


# IG-aligned leverage limits by asset class (FCA retail)
# These match IG demo/live margin requirements.
_IG_LEVERAGE_LIMITS: dict[str, float] = {
    # FX majors: 30:1 (3.33% margin)
    "EURUSD": 30.0, "GBPUSD": 30.0, "USDJPY": 30.0,
    "AUDUSD": 30.0, "USDCAD": 30.0, "USDCHF": 30.0,
    "NZDUSD": 30.0,
    # FX crosses / minors: 20:1 (5% margin)
    "EURJPY": 20.0, "GBPJPY": 20.0, "EURGBP": 20.0,
    "AUDJPY": 20.0, "EURAUD": 20.0,
    "AUDCAD": 20.0, "AUDCHF": 20.0, "AUDNZD": 20.0,
    "CADCHF": 20.0, "CADJPY": 20.0, "CHFJPY": 20.0,
    "EURCAD": 20.0, "EURCHF": 20.0, "EURNZD": 20.0,
    "GBPAUD": 20.0, "GBPCAD": 20.0, "GBPCHF": 20.0,
    "GBPNZD": 20.0, "NZDCAD": 20.0, "NZDCHF": 20.0,
    "NZDJPY": 20.0, "SGDJPY": 20.0,
    # Scandinavian / EM: 20:1
    "USDNOK": 20.0, "USDSEK": 20.0, "EURNOK": 20.0, "EURSEK": 20.0,
    "USDSGD": 20.0, "USDHKD": 20.0, "USDTRY": 20.0, "USDMXN": 20.0,
    "USDZAR": 20.0, "USDPLN": 20.0, "USDCZK": 20.0, "USDHUF": 20.0,
    "ZARJPY": 20.0, "EURTRY": 20.0, "EURPLN": 20.0, "EURCZK": 20.0,
    "EURHUF": 20.0, "EURDKK": 20.0,
    # Gold / Silver: 20:1 (5% margin)
    "XAUUSD": 20.0, "XAGUSD": 20.0,
    # Oil / Energy: 10:1 (10% margin)
    "BCOUSD": 10.0, "WTIUSD": 10.0, "NATGAS": 10.0,
    # Indices: 20:1 (5% margin)
    "US500": 20.0, "US100": 20.0, "UK100": 20.0, "DE40": 20.0,
    "JP225": 20.0, "US30": 20.0, "CAC40": 20.0, "AU200": 20.0,
    "HK50": 20.0, "DXY": 20.0,
    # Crypto: 2:1 (50% margin)
    "BTCUSD": 2.0, "ETHUSD": 2.0, "SOLUSD": 2.0,
    "LTCUSD": 2.0, "XRPUSD": 2.0,
}


_ISO_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF",
    "NOK", "SEK", "SGD", "HKD", "TRY", "MXN", "ZAR", "PLN",
    "CZK", "HUF", "DKK", "XAU", "XAG",
}


def get_ig_leverage(instrument: str) -> float:
    """Get IG-aligned leverage limit for an instrument.

    Falls back to conservative defaults by symbol pattern:
      - 6-letter alphabetic → FX minor (20:1)
      - Otherwise → 10:1

    The config-level max_leverage acts as an additional cap.
    """
    symbol = instrument.upper()
    if symbol in _IG_LEVERAGE_LIMITS:
        return _IG_LEVERAGE_LIMITS[symbol]
    # Heuristic: if both 3-letter halves are known ISO currencies → FX pair
    if len(symbol) == 6 and symbol[:3] in _ISO_CURRENCIES and symbol[3:] in _ISO_CURRENCIES:
        return 20.0  # Assume FX minor
    return 10.0  # Conservative default for unknown instruments


def pip_value_adjustment(instrument: str, price: float) -> float:
    """Return a multiplier to convert raw PnL to account-currency PnL.

    For xxxUSD pairs (EURUSD, GBPUSD, AUDUSD, etc.):
        PnL is already in USD — multiplier = 1.0

    For USDxxx pairs (USDJPY, USDCAD, USDCHF, etc.):
        PnL is in the quote currency — divide by current price.
        e.g. USDJPY PnL in JPY → divide by ~150 to get USD.

    For cross pairs (EURJPY, GBPJPY, etc.):
        PnL is in the quote currency of the cross.
        For xxxJPY crosses, divide by JPY rate (~150).
        For others (EURGBP, AUDNZD etc.), approximate as 1.0.

    For commodities/indices: PnL is in USD — multiplier = 1.0.
    """
    symbol = instrument.upper()

    # JPY-quoted pairs: PnL is in JPY, convert to USD/GBP
    if symbol.endswith("JPY"):
        return 1.0 / price if price > 0 else 1.0

    # CHF-quoted pairs
    if symbol.endswith("CHF") and not symbol.startswith("USD"):
        # Approximate CHF ≈ USD for simplicity
        return 1.0

    return 1.0


def max_position_size(equity: float, entry_price: float, max_leverage: float) -> float:
    """Maximum position size (units) allowed by leverage constraint.

    For a forex pair quoted as BASE/QUOTE:
        notional value ≈ position_size * entry_price  (in quote currency)
        leverage = notional / equity

    So max_position_size = equity * max_leverage / entry_price
    """
    if entry_price <= 0:
        return 0.0
    return equity * max_leverage / entry_price


def calculate_position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    max_leverage: float = 30.0,
    instrument: str = "",
) -> float:
    """Calculate position size with IG-aligned leverage cap.

    Returns the number of units to trade such that a move from
    entry to stop loses risk_pct% of capital, capped by the LOWER of:
      - max_leverage (from BacktestConfig, default 30)
      - IG-specific leverage limit for this instrument
    """
    risk_amount = capital * (risk_pct / 100.0)
    risk_per_unit = abs(entry - stop)
    if risk_per_unit < 1e-10:
        return 0.0

    # Adjust risk_per_unit for JPY pairs (PnL in JPY, not account currency)
    adj = pip_value_adjustment(instrument, entry)
    effective_risk_per_unit = risk_per_unit * adj

    raw_size = risk_amount / effective_risk_per_unit

    # Apply IG-aligned leverage: use the stricter of config and IG limits
    ig_lev = get_ig_leverage(instrument)
    effective_leverage = min(max_leverage, ig_lev)
    max_size = max_position_size(capital, entry, effective_leverage)
    return min(raw_size, max_size)


# Realistic spread defaults in price points (not pips)
DEFAULT_SPREADS: dict[str, float] = {
    # Forex majors: ~1-2 pips
    "EURUSD": 0.00012, "GBPUSD": 0.00014, "USDJPY": 0.012,
    "AUDUSD": 0.00014, "USDCAD": 0.00016, "USDCHF": 0.00016,
    "NZDUSD": 0.00018,
    # Forex crosses: ~2-3 pips
    "EURJPY": 0.018, "GBPJPY": 0.025, "EURGBP": 0.00015,
    "AUDJPY": 0.020, "EURAUD": 0.00020,
    # Gold/Silver
    "XAUUSD": 0.35, "XAGUSD": 0.025,
    # Oil
    "BCOUSD": 0.04, "WTIUSD": 0.04,
    # Indices
    "US500": 0.5, "US100": 1.5, "UK100": 1.0, "DE40": 1.5,
    "JP225": 8.0, "US30": 3.0,
}


def get_default_spread(instrument: str) -> float:
    """Get a realistic default spread for an instrument."""
    symbol = instrument.upper()
    if symbol in DEFAULT_SPREADS:
        return DEFAULT_SPREADS[symbol]
    # Default: 2 pips for unknown forex
    if len(symbol) == 6 and symbol.isalpha():
        if "JPY" in symbol:
            return 0.020
        return 0.00020
    return 0.0
