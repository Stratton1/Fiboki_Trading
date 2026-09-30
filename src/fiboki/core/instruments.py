"""Instrument contract specifications.

A contract spec is a *market fact*, not a strategy choice. Everything that
converts between price movement and account currency reads from here, so that
no strategy, engine or adapter re-derives it. V1's bug was pip-value logic
scattered across sizing, the paper bot and the IG adapter, each disagreeing.
"""
from __future__ import annotations

from dataclasses import dataclass

from fiboki.core.enums import AssetClass
from fiboki.core.instruments_oanda_table import (
    OANDA_INSTRUMENTS_2026_09_30,
    OANDA_TABLE_FIELDS,
)


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
    "JPY", "MXN", "NOK", "NZD", "PLN", "SEK", "SGD", "THB", "TRY", "USD", "ZAR",
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
    # Annex II(e): "other underlyings" -- a government-bond CFD is neither a
    # currency pair, an index, a commodity nor a crypto-asset, so 20% margin.
    AssetClass.BOND: 5.0,
    # Annex II(c): commodities other than gold.
    AssetClass.COMMODITY: 10.0,
}

#: Retail leverage for a commodity other than gold, and for an index that is not
#: on the ESMA major-index list.
NON_MAJOR_COMMODITY_LEVERAGE = 10.0
NON_MAJOR_INDEX_LEVERAGE = 10.0

#: Registered index symbols that ARE on the ESMA/FCA major-index list.
#: JP225 (OANDA JP225_USD) and JP225Y (JP225Y_JPY) are both the Nikkei 225,
#: quoted in different currencies. US2000 (Russell 2000), NL25 (AEX), CH20
#: (SMI), ES35 (IBEX 35), SG30, CN50, CHINAH and HK50 are NOT on the list.
ESMA_MAJOR_INDICES: frozenset[str] = frozenset(
    {"US500", "US100", "US30", "UK100", "DE40", "FR40", "JP225", "JP225Y", "AU200", "EU50"}
)

#: Annual financing cost per class, in basis points of notional. These are the
#: registry's long-standing per-class assumptions (static financing is a named
#: approximation, AGENTS.md section 2). BOND and COMMODITY had no default before
#: the OANDA registry; they take the nearest existing CFD figure (index 300,
#: energy 350) and are PLACEHOLDERS, not measurements. OANDA's instruments
#: endpoint does carry per-instrument long/short financing rates, but as a
#: daily-moving snapshot; see ``fiboki.broker.oanda_instruments``.
DEFAULT_ANNUAL_FINANCING_BPS: dict[AssetClass, float] = {
    AssetClass.FX_MAJOR: 250.0,
    AssetClass.FX_CROSS: 250.0,
    AssetClass.METAL: 300.0,
    AssetClass.INDEX: 300.0,
    AssetClass.ENERGY: 350.0,
    AssetClass.COMMODITY: 350.0,
    AssetClass.BOND: 300.0,
}

#: ``Instrument.trading_hours`` label per class for OANDA-derived entries. The
#: labels for classes that were already registered are the ones already in use
#: (``index``, ``energy``), so an old and a new index CFD cannot disagree about
#: their session; the two new classes get new labels. Every label here must have
#: an entry in ``fiboki.backtest.locks.SESSION_CLOSURES`` (a test checks).
TRADING_HOURS_BY_CLASS: dict[AssetClass, str] = {
    AssetClass.FX_MAJOR: "fx_24_5",
    AssetClass.FX_CROSS: "fx_24_5",
    AssetClass.METAL: "fx_24_5",
    AssetClass.INDEX: "index",
    AssetClass.ENERGY: "energy",
    AssetClass.COMMODITY: "commodity_cfd",
    AssetClass.BOND: "bond_cfd",
}


def is_esma_major_pair(base: str, quote: str) -> bool:
    """True when BOTH legs are ESMA major currencies (ESMA 2018/796 Annex II)."""
    return base.upper() in ESMA_MAJOR_CURRENCIES and quote.upper() in ESMA_MAJOR_CURRENCIES


def esma_retail_leverage(symbol: str, asset_class: AssetClass, base: str, quote: str) -> float:
    """The ESMA/FCA retail cap the regulation alone implies. The CROSS-CHECK.

    OANDA's ``1 / marginRate`` is the binding figure for this account and is
    what the registry stores; this function is what the regulation says, so a
    test can list every instrument where the two differ instead of assuming
    they agree.

    * FX: by the currency set (:func:`is_esma_major_pair`).
    * METAL: gold against a currency is gold (20:1); anything with silver in it
      (XAG against anything, and the XAU/XAG cross) is a commodity other than
      gold (10:1). The gold carve-out is for gold, and a gold/silver cross is
      exposed to silver.
    * INDEX: major (20:1) only if on :data:`ESMA_MAJOR_INDICES`, else 10:1.
    * ENERGY, COMMODITY: 10:1. BOND: 5:1.
    """
    if asset_class in (AssetClass.FX_MAJOR, AssetClass.FX_CROSS):
        cls = AssetClass.FX_MAJOR if is_esma_major_pair(base, quote) else AssetClass.FX_CROSS
        return _RETAIL_LEVERAGE[cls]
    if asset_class is AssetClass.METAL:
        gold = base.upper() == "XAU" and quote.upper() in _ISO
        return _RETAIL_LEVERAGE[AssetClass.METAL] if gold else NON_MAJOR_COMMODITY_LEVERAGE
    if asset_class is AssetClass.INDEX:
        return (
            _RETAIL_LEVERAGE[AssetClass.INDEX]
            if symbol.upper() in ESMA_MAJOR_INDICES
            else NON_MAJOR_INDEX_LEVERAGE
        )
    if asset_class is AssetClass.ENERGY:
        return NON_MAJOR_COMMODITY_LEVERAGE
    return _RETAIL_LEVERAGE[asset_class]


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

# --- Reconciled against OANDA (tests/fixtures/oanda/practice_instruments_2026-09-30.json).
# Where OANDA disagreed with the hand-typed value, OANDA's value is used and the
# old one is recorded beside it; ``tests/unit/test_oanda_instruments.py``
# asserts that every field of these 41 except ``typical_spread_pips`` now equals
# the OANDA derivation. A size the venue cannot represent is a size the adapter
# must refuse (``OandaAdapter._units_for``), and a pip size the venue disagrees
# with makes ``OandaAdapter.market_spec`` raise, so neither can be "close enough".
_add(Instrument("XAUUSD", AssetClass.METAL, "XAU", "USD", 0.01, 1.0,
                # OANDA XAU_USD minimumTradeSize "0.1", tradeUnitsPrecision 1
                # (was 0.01 / 0.01); displayPrecision 3 (was 2).
                min_size=0.1, size_step=0.1, price_precision=3,
                typical_spread_pips=30.0, retail_leverage=20.0,
                annual_financing_bps=300.0))
_add(Instrument("XAGUSD", AssetClass.METAL, "XAG", "USD",
                # OANDA XAG_USD pipLocation -4 (was 0.001). The typical spread is
                # the SAME price, 0.025, restated in the new pip: 25 x 0.001 =
                # 250 x 0.0001. minimumTradeSize "1", tradeUnitsPrecision 0 (was
                # 0.05 / 0.05); displayPrecision 5 (was 3).
                0.0001, 1.0,
                min_size=1.0, size_step=1.0, price_precision=5,
                typical_spread_pips=250.0, retail_leverage=NON_MAJOR_COMMODITY_LEVERAGE,
                annual_financing_bps=300.0))
_add(Instrument("WTIUSD", AssetClass.ENERGY, "WTI", "USD", 0.01, 1.0,
                # OANDA WTICO_USD minimumTradeSize "1", tradeUnitsPrecision 0 (was
                # 0.1 / 0.1); displayPrecision 3 (was 2).
                min_size=1.0, size_step=1.0, price_precision=3,
                typical_spread_pips=3.0, retail_leverage=10.0,
                annual_financing_bps=350.0, trading_hours="energy"))
_add(Instrument("BCOUSD", AssetClass.ENERGY, "BCO", "USD", 0.01, 1.0,
                # OANDA BCO_USD minimumTradeSize "1", tradeUnitsPrecision 0 (was
                # 0.1 / 0.1); displayPrecision 3 (was 2).
                min_size=1.0, size_step=1.0, price_precision=3,
                typical_spread_pips=3.0, retail_leverage=10.0,
                annual_financing_bps=350.0, trading_hours="energy"))

# (symbol, quote, typical spread, displayPrecision, minimumTradeSize = size step)
# OANDA disagreed on: US500/US100/US30/DE40/JP225 minimumTradeSize "0.01" and
# tradeUnitsPrecision 2 (were 0.1 / 0.1); JP225 displayPrecision 1 (was 0) and,
# above all, JP225's QUOTE currency: OANDA's JP225_USD is the Nikkei 225 priced
# and settled in USD (the JPY-settled contract is JP225Y_JPY, registered
# separately as JP225Y). The registry said JPY, which would have converted every
# JP225 point to account currency at the JPY rate, ~157x too small.
for _s, _q, _sp, _prec, _step in [
    ("US500", "USD", 0.4, 1, 0.01), ("US100", "USD", 1.0, 1, 0.01),
    ("US30", "USD", 1.6, 1, 0.01), ("UK100", "GBP", 1.0, 1, 0.1),
    ("DE40", "EUR", 1.2, 1, 0.01), ("FR40", "EUR", 1.0, 1, 0.1),
    ("JP225", "USD", 7.0, 1, 0.01), ("AU200", "AUD", 1.0, 1, 0.1),
    ("HK50", "HKD", 5.0, 1, 0.1), ("EU50", "EUR", 1.5, 1, 0.1),
]:
    _add(Instrument(_s, AssetClass.INDEX, _s, _q, 1.0, 1.0,
                    min_size=_step, size_step=_step, price_precision=_prec,
                    typical_spread_pips=_sp,
                    retail_leverage=(
                        _RETAIL_LEVERAGE[AssetClass.INDEX]
                        if _s in ESMA_MAJOR_INDICES
                        else NON_MAJOR_INDEX_LEVERAGE
                    ),
                    annual_financing_bps=300.0, trading_hours="index"))

#: Index CFDs whose QUOTE (settlement) currency is not the currency of the
#: market the index tracks. OANDA's JP225_USD is the Nikkei 225 settled in USD:
#: its P&L converts from USD, but Japanese releases are what move it. Consumers
#: that ask "which economy moves this?" (event buckets, the economic calendar)
#: read this; consumers that convert money read ``quote``. CN50 (FTSE China A50,
#: USD-settled) is NOT listed: the event and calendar layers have no CNY/CNH
#: bucket to point it at, and a wrong home is worse than an admitted gap.
INDEX_HOME_CURRENCY: dict[str, str] = {"JP225": "JPY"}


def home_currency(instrument: Instrument) -> str:
    """The currency of the economy an instrument's price is driven by, for an index."""
    return INDEX_HOME_CURRENCY.get(instrument.symbol, instrument.quote)


#: The 41 instruments registered by hand before OANDA became the authority.
HAND_REGISTERED: frozenset[str] = frozenset(_REGISTRY)


# =====================================================================
# OANDA-derived entries (every instrument OANDA offers that is not above)
# =====================================================================
#
# OANDA is the only broker, so every instrument its practice account offers is
# registered. The 41 entries above were hand-registered first and are the
# cross-checked ones: ``tests/unit/test_oanda_instruments.py`` reconciles each of
# them field by field against OANDA's instruments endpoint. Everything below is
# built from ``OANDA_INSTRUMENTS_2026_09_30``, a literal GENERATED by
# ``scripts/oanda_instrument_table.py`` from the two recorded fixtures
# (``OANDA_TABLE_SOURCES``) by the rules in ``fiboki.broker.oanda_instruments``.
# A test regenerates it and diffs, so it cannot drift from the fixtures. Nothing
# here touches the network, at import time or ever.
#
# Not registered: DXY. OANDA does not offer the US dollar index, so there is no
# venue fact to derive it from and no market to trade it on.

_ROWS: tuple[dict[str, object], ...] = tuple(
    dict(zip(OANDA_TABLE_FIELDS, row, strict=True)) for row in OANDA_INSTRUMENTS_2026_09_30
)

#: Where each registered instrument's ``typical_spread_pips`` came from.
SPREAD_PROVENANCE: dict[str, str] = {
    sym: "hand-registered London-session typical (pre-OANDA registry; not re-measured)"
    for sym in HAND_REGISTERED
}

for _row in _ROWS:
    _sym = str(_row["symbol"])
    if _sym in _REGISTRY:
        continue
    _add(Instrument(
        symbol=_sym,
        asset_class=AssetClass(str(_row["asset_class"])),
        base=str(_row["base"]),
        quote=str(_row["quote"]),
        pip_size=float(_row["pip_size"]),  # type: ignore[arg-type]
        contract_size=1.0,
        min_size=float(_row["min_size"]),  # type: ignore[arg-type]
        size_step=float(_row["size_step"]),  # type: ignore[arg-type]
        price_precision=int(_row["price_precision"]),  # type: ignore[call-overload]
        typical_spread_pips=float(_row["snapshot_spread_pips"]),  # type: ignore[arg-type]
        retail_leverage=float(_row["retail_leverage"]),  # type: ignore[arg-type]
        annual_financing_bps=float(_row["annual_financing_bps"]),  # type: ignore[arg-type]
        trading_hours=str(_row["trading_hours"]),
    ))
    SPREAD_PROVENANCE[_sym] = str(_row["spread_provenance"])

_OANDA_NAME_BY_SYMBOL: dict[str, str] = {str(r["symbol"]): str(r["oanda_name"]) for r in _ROWS}
_SYMBOL_BY_OANDA_NAME: dict[str, str] = {v: k for k, v in _OANDA_NAME_BY_SYMBOL.items()}
if len(_SYMBOL_BY_OANDA_NAME) != len(_OANDA_NAME_BY_SYMBOL) or len(_ROWS) != len(
    _OANDA_NAME_BY_SYMBOL
):
    raise ImportError("the OANDA instrument table maps two names to one symbol")


def oanda_name_for(symbol: str) -> str:
    """The OANDA v20 instrument name for a registered symbol (``DE40`` -> ``DE30_EUR``).

    The one runtime mapping; ``fiboki.broker.oanda_instruments.to_oanda_name``,
    the broker adapter and the candles provider all delegate here.
    """
    key = symbol.upper()
    if key not in _OANDA_NAME_BY_SYMBOL:
        raise KeyError(
            f"No OANDA instrument for {symbol!r}. The mapping is the committed table "
            "in core/instruments_oanda_table.py; a guessed name trades the wrong market."
        )
    return _OANDA_NAME_BY_SYMBOL[key]


def symbol_for_oanda_name(name: str) -> str:
    """The registered symbol for an OANDA v20 instrument name (``SPX500_USD`` -> ``US500``)."""
    if name not in _SYMBOL_BY_OANDA_NAME:
        raise KeyError(
            f"Unknown OANDA instrument {name!r}. It is not in the 2026-09-30 table; "
            "regenerate the table from a fresh instruments fixture rather than guess."
        )
    return _SYMBOL_BY_OANDA_NAME[name]


def oanda_names() -> list[str]:
    return sorted(_SYMBOL_BY_OANDA_NAME)


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
