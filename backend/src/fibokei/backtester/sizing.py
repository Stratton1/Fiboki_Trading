"""Position sizing with leverage caps and pip value conversion.

This module enforces economic realism in backtests by:
1. Capping position size to a maximum leverage ratio
2. Converting PnL to account currency for JPY-quoted pairs
3. Providing realistic spread defaults per instrument class
"""


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
    """Calculate position size with leverage cap.

    Returns the number of units to trade such that a move from
    entry to stop loses risk_pct% of capital, capped by max_leverage.
    """
    risk_amount = capital * (risk_pct / 100.0)
    risk_per_unit = abs(entry - stop)
    if risk_per_unit < 1e-10:
        return 0.0

    # Adjust risk_per_unit for JPY pairs (PnL in JPY, not account currency)
    adj = pip_value_adjustment(instrument, entry)
    effective_risk_per_unit = risk_per_unit * adj

    raw_size = risk_amount / effective_risk_per_unit

    # Clamp to leverage limit
    max_size = max_position_size(capital, entry, max_leverage)
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
