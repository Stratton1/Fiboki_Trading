"""OANDA's instrument facts, parsed once, and the rules that turn them into registry entries.

OANDA is the only broker (operator decision, 2026-09-30), so the instrument
registry in :mod:`fiboki.core.instruments` answers to OANDA's own
``GET /v3/accounts/{id}/instruments`` rather than to hand-typed numbers. This
module is PURE: no network, no file I/O. Callers pass an already-decoded JSON
payload; the recorded fixtures live in ``tests/fixtures/oanda/``.

What lives here and what does not
---------------------------------
* :class:`OandaInstrumentSpec` / :func:`parse_instruments_payload`: one entry of
  the instruments endpoint, every decimal string parsed ONCE into a
  :class:`~decimal.Decimal`, explicitly.
* :class:`OandaSpreadSnapshot` / :func:`parse_pricing_payload`: one entry of the
  pricing endpoint, reduced to the spread facts the registry needs.
* :func:`derive_fiboki_symbol`: the naming RULES (FX split, metal split and the
  explicit :data:`CFD_SYMBOLS` table). The runtime mapping,
  :func:`to_fiboki_symbol` / :func:`to_oanda_name`, is the committed, generated
  table in :mod:`fiboki.core.instruments_oanda_table`, looked up through
  :mod:`fiboki.core.instruments`. It lives in ``core`` because ``data`` (rank 10)
  and ``core`` (rank 0) both need it and may not import ``broker`` (rank 80,
  ``tests/unit/test_layering.py``). There is ONE mapping; these rules generate it
  (``scripts/oanda_instrument_table.py``) and a test regenerates and diffs it.
* :func:`asset_class_for` and :func:`instrument_from_oanda`: the derivation of a
  Fiboki :class:`~fiboki.core.instruments.Instrument` from OANDA's facts.

Financing
---------
The instruments endpoint DOES carry ``financing.longRate``/``shortRate`` and
``financingDaysOfWeek``; they are parsed into the spec. They are NOT used for
``Instrument.annual_financing_bps``: that field is one symmetric annual cost,
while OANDA's rates are an asymmetric, daily-moving snapshot (the 2026-09-30
fixture has TRY_JPY at +21.91% long / -36.65% short). A snapshot frozen into a
constant would be believed long after it stopped being true, so the registry
keeps the per-class defaults in
:data:`fiboki.core.instruments.DEFAULT_ANNUAL_FINANCING_BPS` and says so.

Spreads
-------
The pricing snapshot was taken at 23:52 UTC on a Tuesday: late New York, early
Asia, WIDER than the London session the 41 hand-registered typicals describe.
Those 41 keep their hand values. A new instrument gets the snapshot spread
rounded UP to one decimal pip, with its provenance recorded in
:data:`fiboki.core.instruments.SPREAD_PROVENANCE`.

The spread used is TOP OF BOOK (``asks[0] - bids[0]``), not
``closeoutAsk - closeoutBid``. OANDA's closeout prices are the prices used to
close a position out when there is no liquidity and are "never used to open a
new position" (v20 docs); in the fixture they sit at or beyond the deepest
ladder level (EUR_USD: closeout 2.7 pips, top of book 0.8; USD_MXN: 373.5 vs
53.4). Registering them as the typical spread would overstate every new
instrument's cost several-fold AND loosen the gateway's abnormal-spread check
by the same factor, because that check divides the LIVE top-of-book spread
(:mod:`fiboki.broker.oanda_pricing`) by ``typical_spread_pips * pip_size``. Both
numbers are kept on :class:`OandaSpreadSnapshot` so the choice is visible.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any

import pandas as pd

from fiboki.core import instruments as registry
from fiboki.core.enums import AssetClass
from fiboki.core.instruments import (
    DEFAULT_ANNUAL_FINANCING_BPS,
    TRADING_HOURS_BY_CLASS,
    Instrument,
    is_esma_major_pair,
)

__all__ = [
    "BOND_CFDS",
    "CFD_SYMBOLS",
    "COMMODITY_CFDS",
    "ENERGY_CFDS",
    "INDEX_CFDS",
    "OANDA_TYPES",
    "OandaInstrumentSpec",
    "OandaSpreadSnapshot",
    "asset_class_for",
    "base_quote_for",
    "derive_fiboki_symbol",
    "instrument_from_oanda",
    "parse_instruments_payload",
    "parse_pricing_payload",
    "retail_leverage_for",
    "snapshot_spread_pips",
    "to_fiboki_symbol",
    "to_oanda_name",
]

OANDA_TYPES: frozenset[str] = frozenset({"CURRENCY", "CFD", "METAL"})

#: Every OANDA CFD name and its Fiboki symbol, written out. Where the registry
#: already had a symbol for the market (US500, DE40, HK50, WTIUSD ...) that name
#: is kept, so no stored dataset or strategy document changes its key. Note the
#: names that are NOT a rename of the OANDA code: DE30_EUR is the DAX 40
#: (renamed from 30 constituents in 2021), HK33_HKD is the Hang Seng (HK50),
#: ESPIX_EUR is the IBEX 35 (ES35). XCU/XPT/XPD are not in this table: OANDA
#: types them CFD but they are named by the metal rule (``XCU_USD`` -> ``XCUUSD``).
CFD_SYMBOLS: dict[str, str] = {
    "SPX500_USD": "US500",
    "NAS100_USD": "US100",
    "US30_USD": "US30",
    "US2000_USD": "US2000",
    "DE30_EUR": "DE40",
    "UK100_GBP": "UK100",
    "FR40_EUR": "FR40",
    "EU50_EUR": "EU50",
    "JP225_USD": "JP225",
    "JP225Y_JPY": "JP225Y",
    "HK33_HKD": "HK50",
    "AU200_AUD": "AU200",
    "CN50_USD": "CN50",
    "CHINAH_HKD": "CHINAH",
    "SG30_SGD": "SG30",
    "NL25_EUR": "NL25",
    "CH20_CHF": "CH20",
    "ESPIX_EUR": "ES35",
    "BCO_USD": "BCOUSD",
    "WTICO_USD": "WTIUSD",
    "NATGAS_USD": "NATGASUSD",
    "CORN_USD": "CORNUSD",
    "SOYBN_USD": "SOYBNUSD",
    "SUGAR_USD": "SUGARUSD",
    "WHEAT_USD": "WHEATUSD",
    "DE10YB_EUR": "DE10YB",
    "UK10YB_GBP": "UK10YB",
    "USB02Y_USD": "USB02Y",
    "USB05Y_USD": "USB05Y",
    "USB10Y_USD": "USB10Y",
    "USB30Y_USD": "USB30Y",
}

#: CFD metals OANDA types as ``CFD`` and that are named by the metal rule.
_CFD_METALS: frozenset[str] = frozenset({"XCU_USD", "XPT_USD", "XPD_USD"})

#: Equity-index CFDs.
INDEX_CFDS: frozenset[str] = frozenset({
    "SPX500_USD", "NAS100_USD", "US30_USD", "US2000_USD", "DE30_EUR", "UK100_GBP",
    "FR40_EUR", "EU50_EUR", "JP225_USD", "JP225Y_JPY", "HK33_HKD", "AU200_AUD",
    "CN50_USD", "CHINAH_HKD", "SG30_SGD", "NL25_EUR", "CH20_CHF", "ESPIX_EUR",
})
#: Energy CFDs.
ENERGY_CFDS: frozenset[str] = frozenset({"BCO_USD", "WTICO_USD", "NATGAS_USD"})
#: Government-bond CFDs.
BOND_CFDS: frozenset[str] = frozenset({
    "DE10YB_EUR", "UK10YB_GBP", "USB02Y_USD", "USB05Y_USD", "USB10Y_USD", "USB30Y_USD",
})
#: Commodities other than gold that are not energy. XCU/XPT/XPD are here and not
#: under METAL on purpose: the registry's asset class decides the retail leverage
#: class, and ESMA 2018/796 Annex II carves out GOLD ONLY ("commodities other
#: than gold" are 10:1). Copper, platinum and palladium are metals in the
#: everyday sense and commodities-other-than-gold in the one sense that matters
#: here. (Silver stays METAL because OANDA types it METAL and XAGUSD was
#: already registered so; its leverage is set to the commodity cap regardless.)
COMMODITY_CFDS: frozenset[str] = frozenset({
    "CORN_USD", "SOYBN_USD", "SUGAR_USD", "WHEAT_USD", "XCU_USD", "XPT_USD", "XPD_USD",
})

_CCY = re.compile(r"^[A-Z]{3}$")
_METAL_CODES: frozenset[str] = frozenset({"XAU", "XAG", "XCU", "XPT", "XPD"})


# --------------------------------------------------------------------------
# The instruments endpoint
# --------------------------------------------------------------------------


def _dec(raw: Any, field: str, name: str) -> Decimal:
    if not isinstance(raw, str):
        raise ValueError(f"{name}: {field} must be a decimal STRING, got {raw!r}")
    try:
        value = Decimal(raw)
    except ArithmeticError as exc:  # decimal.InvalidOperation
        raise ValueError(f"{name}: {field}={raw!r} is not a decimal") from exc
    if not value.is_finite():
        raise ValueError(f"{name}: {field}={raw!r} is not finite")
    return value


def _int(raw: Any, field: str, name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{name}: {field} must be an integer, got {raw!r}")
    return raw


@dataclass(frozen=True, slots=True)
class OandaInstrumentSpec:
    """One entry of ``GET /v3/accounts/{id}/instruments``, decimals parsed once.

    ``margin_rate_text`` keeps OANDA's own string so a citation can quote it
    verbatim (``"0.03333333333333"``), while ``margin_rate`` is the parsed value.
    """

    name: str
    type: str
    display_name: str
    pip_location: int
    display_precision: int
    trade_units_precision: int
    minimum_trade_size: Decimal
    maximum_order_units: Decimal
    margin_rate: Decimal
    margin_rate_text: str
    guaranteed_stop_loss_order_mode: str
    tags: tuple[tuple[str, str], ...]
    financing_long_rate: Decimal | None
    financing_short_rate: Decimal | None
    #: ``(dayOfWeek, daysCharged)`` exactly as OANDA lists them.
    financing_days: tuple[tuple[str, int], ...]

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> OandaInstrumentSpec:
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"instrument entry has no name: {raw!r}")
        kind = raw.get("type")
        if kind not in OANDA_TYPES:
            raise ValueError(f"{name}: unknown OANDA instrument type {kind!r}")
        financing = raw.get("financing") or {}
        long_rate = financing.get("longRate")
        short_rate = financing.get("shortRate")
        days = tuple(
            (str(d["dayOfWeek"]), _int(d["daysCharged"], "daysCharged", name))
            for d in financing.get("financingDaysOfWeek", [])
        )
        margin_text = raw.get("marginRate")
        margin = _dec(margin_text, "marginRate", name)
        if margin <= 0 or margin > 1:
            raise ValueError(f"{name}: marginRate {margin_text!r} is not in (0, 1]")
        return cls(
            name=name,
            type=str(kind),
            display_name=str(raw.get("displayName", name)),
            pip_location=_int(raw.get("pipLocation"), "pipLocation", name),
            display_precision=_int(raw.get("displayPrecision"), "displayPrecision", name),
            trade_units_precision=_int(
                raw.get("tradeUnitsPrecision"), "tradeUnitsPrecision", name
            ),
            minimum_trade_size=_dec(raw.get("minimumTradeSize"), "minimumTradeSize", name),
            maximum_order_units=_dec(raw.get("maximumOrderUnits"), "maximumOrderUnits", name),
            margin_rate=margin,
            margin_rate_text=str(margin_text),
            guaranteed_stop_loss_order_mode=str(raw.get("guaranteedStopLossOrderMode", "")),
            tags=tuple((str(t["type"]), str(t["name"])) for t in raw.get("tags", [])),
            financing_long_rate=(
                None if long_rate is None else _dec(long_rate, "financing.longRate", name)
            ),
            financing_short_rate=(
                None if short_rate is None else _dec(short_rate, "financing.shortRate", name)
            ),
            financing_days=days,
        )

    # -- derived, each one line and each exact --------------------------------

    @property
    def pip_decimal(self) -> Decimal:
        return Decimal(1).scaleb(self.pip_location)

    @property
    def pip_size(self) -> float:
        """``10 ** pipLocation`` as the float nearest the decimal (0.0001, not 1.0000000000000000e-04 + ulp)."""
        return float(self.pip_decimal)

    @property
    def size_step(self) -> float:
        """``10 ** -tradeUnitsPrecision``: the smallest increment of units OANDA accepts."""
        return float(Decimal(1).scaleb(-self.trade_units_precision))

    @property
    def leverage(self) -> Decimal:
        """``1 / marginRate``: the retail leverage OANDA grants THIS account."""
        return Decimal(1) / self.margin_rate


def parse_instruments_payload(payload: Mapping[str, Any]) -> tuple[OandaInstrumentSpec, ...]:
    """Parse a whole instruments response, sorted by OANDA name. Duplicate names raise."""
    entries = payload.get("instruments")
    if not isinstance(entries, list):
        raise ValueError("instruments payload has no 'instruments' list")
    specs = [OandaInstrumentSpec.from_payload(e) for e in entries]
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"instruments payload lists {dupes} more than once")
    return tuple(sorted(specs, key=lambda s: s.name))


# --------------------------------------------------------------------------
# The pricing endpoint
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OandaSpreadSnapshot:
    """One instrument's spread at ONE instant of ``GET /v3/accounts/{id}/pricing``.

    A snapshot, not a typical: ``response_time`` is when the response was
    produced and ``quote_time`` is the instrument's own last price, which for an
    instrument not ``tradeable`` at the time (a closed cash-index or
    agricultural session) can be hours older.
    """

    name: str
    response_time: pd.Timestamp
    quote_time: pd.Timestamp
    tradeable: bool
    bid: Decimal
    ask: Decimal
    closeout_bid: Decimal
    closeout_ask: Decimal

    @property
    def top_of_book_spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def closeout_spread(self) -> Decimal:
        return self.closeout_ask - self.closeout_bid

    def top_of_book_spread_pips(self, spec: OandaInstrumentSpec) -> Decimal:
        return self.top_of_book_spread / spec.pip_decimal

    def closeout_spread_pips(self, spec: OandaInstrumentSpec) -> Decimal:
        return self.closeout_spread / spec.pip_decimal


def _utc(text: Any, field: str, name: str) -> pd.Timestamp:
    if not isinstance(text, str):
        raise ValueError(f"{name}: {field} must be an RFC3339 string, got {text!r}")
    ts = pd.Timestamp(text)
    if ts.tzinfo is None:
        raise ValueError(f"{name}: {field}={text!r} carries no timezone")
    return ts.tz_convert("UTC")


def parse_pricing_payload(payload: Mapping[str, Any]) -> dict[str, OandaSpreadSnapshot]:
    """Parse a pricing response into ``{oanda_name: snapshot}``.

    Refuses an entry with an empty or crossed book rather than producing a zero
    or negative spread: an absent spread is not a narrow one.
    """
    response_time = _utc(payload.get("time"), "time", "pricing response")
    out: dict[str, OandaSpreadSnapshot] = {}
    for raw in payload.get("prices", []) or []:
        name = str(raw.get("instrument", ""))
        if not name:
            raise ValueError(f"price entry has no instrument: {raw!r}")
        if name in out:
            raise ValueError(f"pricing payload lists {name} more than once")
        bids = raw.get("bids") or []
        asks = raw.get("asks") or []
        if not bids or not asks:
            raise ValueError(f"{name}: empty book side in the pricing snapshot")
        bid = _dec(bids[0].get("price"), "bids[0].price", name)
        ask = _dec(asks[0].get("price"), "asks[0].price", name)
        if ask <= bid:
            raise ValueError(f"{name}: crossed or locked book bid={bid} ask={ask}")
        out[name] = OandaSpreadSnapshot(
            name=name,
            response_time=response_time,
            quote_time=_utc(raw.get("time"), "time", name),
            tradeable=bool(raw.get("tradeable", False)),
            bid=bid,
            ask=ask,
            closeout_bid=_dec(raw.get("closeoutBid"), "closeoutBid", name),
            closeout_ask=_dec(raw.get("closeoutAsk"), "closeoutAsk", name),
        )
    return out


def snapshot_spread_pips(spec: OandaInstrumentSpec, snapshot: OandaSpreadSnapshot) -> float:
    """Top-of-book snapshot spread in OANDA pips, rounded UP to one decimal."""
    if snapshot.name != spec.name:
        raise ValueError(f"snapshot is for {snapshot.name}, spec is {spec.name}")
    pips = snapshot.top_of_book_spread_pips(spec).quantize(Decimal("0.1"), rounding=ROUND_CEILING)
    if pips <= 0:
        raise ValueError(f"{spec.name}: snapshot spread rounds to {pips} pips")
    return float(pips)


def spread_provenance_for(snapshot: OandaSpreadSnapshot) -> str:
    """The provenance string recorded for a snapshot-derived typical spread."""
    stamp = snapshot.response_time.strftime("%Y-%m-%dT%H:%MZ")
    text = f"snapshot {stamp} (late-NY session, wider than London); top of book"
    if not snapshot.tradeable:
        text += (
            "; NOT tradeable at the snapshot, last quote "
            f"{snapshot.quote_time.strftime('%Y-%m-%dT%H:%MZ')}"
        )
    return text


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


def derive_fiboki_symbol(name: str, kind: str) -> str:
    """The naming RULE. Used to generate the committed table, never at runtime.

    * CURRENCY ``AAA_BBB`` -> ``AAABBB`` (both legs three capital letters);
    * METAL ``XAU_USD`` -> ``XAUUSD``, ``XAU_XAG`` -> ``XAUXAG``;
    * CFD: the explicit :data:`CFD_SYMBOLS` table, except ``XCU_USD``,
      ``XPT_USD`` and ``XPD_USD``, which follow the metal rule.

    Anything else raises: a guessed name trades the wrong market.
    """
    if kind == "CFD":
        if name in CFD_SYMBOLS:
            return CFD_SYMBOLS[name]
        if name in _CFD_METALS:
            left, right = name.split("_")
            return left + right
        raise KeyError(f"CFD {name!r} has no entry in CFD_SYMBOLS; add one explicitly")
    parts = name.split("_")
    if len(parts) != 2 or not all(_CCY.match(p) for p in parts):
        raise KeyError(f"{kind} {name!r} is not of the form AAA_BBB")
    left, right = parts
    if kind == "CURRENCY":
        if not (registry.is_iso_currency(left) and registry.is_iso_currency(right)):
            raise KeyError(f"{name!r}: a leg is not a currency the registry knows")
        return left + right
    if kind == "METAL":
        if left not in _METAL_CODES:
            raise KeyError(f"METAL {name!r} does not start with a metal code")
        return left + right
    raise KeyError(f"unknown OANDA type {kind!r} for {name!r}")


def to_fiboki_symbol(oanda_name: str) -> str:
    """``EUR_USD`` -> ``EURUSD``, ``SPX500_USD`` -> ``US500``. Raises for an unknown name."""
    return registry.symbol_for_oanda_name(oanda_name)


def to_oanda_name(symbol: str) -> str:
    """``EURUSD`` -> ``EUR_USD``, ``DE40`` -> ``DE30_EUR``. Raises for an unknown symbol."""
    return registry.oanda_name_for(symbol)


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------


def base_quote_for(spec: OandaInstrumentSpec, symbol: str) -> tuple[str, str]:
    """Base and quote. For a CFD the quote is OANDA's currency suffix and the
    base is the Fiboki symbol with that suffix removed if it ends with it
    (``WTIUSD`` -> ``WTI``, ``US500`` -> ``US500``), which is the convention
    the hand-registered entries already follow."""
    left, _, quote = spec.name.rpartition("_")
    if not left or not _CCY.match(quote):
        raise ValueError(f"{spec.name}: cannot read a quote currency")
    if spec.type in ("CURRENCY", "METAL"):
        return left, quote
    base = symbol[: -len(quote)] if symbol.endswith(quote) and symbol != quote else symbol
    return base, quote


def asset_class_for(spec: OandaInstrumentSpec) -> AssetClass:
    """The registry asset class. Explicit tables, not OANDA's tags (a test cross-checks the tags)."""
    if spec.type == "CURRENCY":
        left, right = spec.name.split("_")
        return AssetClass.FX_MAJOR if is_esma_major_pair(left, right) else AssetClass.FX_CROSS
    if spec.type == "METAL":
        return AssetClass.METAL
    if spec.name in INDEX_CFDS:
        return AssetClass.INDEX
    if spec.name in ENERGY_CFDS:
        return AssetClass.ENERGY
    if spec.name in BOND_CFDS:
        return AssetClass.BOND
    if spec.name in COMMODITY_CFDS:
        return AssetClass.COMMODITY
    raise KeyError(f"CFD {spec.name!r} has no asset class; add it to one table explicitly")


def retail_leverage_for(spec: OandaInstrumentSpec) -> float:
    """``round(1 / marginRate, 6)``. OANDA's figure is the binding constraint for this account."""
    return float(round(spec.leverage, 6))


def instrument_from_oanda(
    spec: OandaInstrumentSpec, *, spread_snapshot: OandaSpreadSnapshot | None
) -> Instrument:
    """A registry :class:`Instrument` built from OANDA's facts alone.

    * ``pip_size = 10 ** pipLocation``; ``price_precision = displayPrecision``;
    * ``retail_leverage = round(1 / marginRate, 6)``;
    * ``min_size = minimumTradeSize``; ``size_step = 10 ** -tradeUnitsPrecision``;
      ``contract_size = 1.0`` (OANDA units are units of the underlying);
    * ``trading_hours`` by class (:data:`~fiboki.core.instruments.TRADING_HOURS_BY_CLASS`);
    * ``annual_financing_bps``: the per-class default, NOT OANDA's financing
      snapshot (module docstring);
    * ``typical_spread_pips``: the top-of-book snapshot spread rounded up to one
      decimal pip. ``spread_snapshot=None`` raises: a registry entry without a
      spread would be priced as free.
    """
    if spread_snapshot is None:
        raise ValueError(
            f"{spec.name}: no spread snapshot. The registry refuses to invent a "
            "typical spread; pass the pricing snapshot for this instrument."
        )
    # The RULE, not the committed table: this function is what generates the
    # table (scripts/oanda_instrument_table.py), so it cannot read it.
    symbol = derive_fiboki_symbol(spec.name, spec.type)
    cls = asset_class_for(spec)
    base, quote = base_quote_for(spec, symbol)
    return Instrument(
        symbol=symbol,
        asset_class=cls,
        base=base,
        quote=quote,
        pip_size=spec.pip_size,
        contract_size=1.0,
        min_size=float(spec.minimum_trade_size),
        size_step=spec.size_step,
        price_precision=spec.display_precision,
        typical_spread_pips=snapshot_spread_pips(spec, spread_snapshot),
        retail_leverage=retail_leverage_for(spec),
        annual_financing_bps=DEFAULT_ANNUAL_FINANCING_BPS[cls],
        trading_hours=TRADING_HOURS_BY_CLASS[cls],
    )
