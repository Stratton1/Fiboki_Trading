"""OANDA as the source of truth for the instrument registry.

Every assertion here is against the two recorded practice-API responses in
``tests/fixtures/oanda/`` (instruments 2026-09-30, pricing 2026-09-29T23:52Z).
Nothing touches the network.

What this file holds, in order:

1. the parsers (decimal strings parsed once; malformed input refuses);
2. the mapping: complete, bidirectional, collision-free over all 123 names, and
   the ONE mapping every caller uses;
3. asset classes, cross-checked against OANDA's own tags;
4. the leverage cross-check: OANDA's ``1/marginRate`` against the ESMA table,
   with every disagreement listed by name and number (not tolerated silently);
5. the reconciliation of the 41 hand-registered entries, field by field;
6. the 82 OANDA-derived entries equal the derivation, with spread provenance;
7. the committed table regenerates byte-for-byte from the fixtures;
8. every registered instrument is usable by every consumer of its class and
   trading-hours label (no KeyError on BOND, COMMODITY, commodity_cfd, bond_cfd).
"""
from __future__ import annotations

import ast
import dataclasses
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from fiboki.backtest.locks import SESSION_CLOSURES, SessionBarClock
from fiboki.backtest.position import TRIPLE_ROLLOVER_WEEKDAY, financing_nights
from fiboki.broker import oanda as broker_oanda
from fiboki.broker.oanda_instruments import (
    BOND_CFDS,
    CFD_SYMBOLS,
    COMMODITY_CFDS,
    ENERGY_CFDS,
    INDEX_CFDS,
    OandaInstrumentSpec,
    asset_class_for,
    derive_fiboki_symbol,
    instrument_from_oanda,
    parse_instruments_payload,
    parse_pricing_payload,
    snapshot_spread_pips,
    to_fiboki_symbol,
    to_oanda_name,
)
from fiboki.core import instruments as registry
from fiboki.core.enums import AssetClass, Timeframe
from fiboki.data.calendars import calendar_for
from fiboki.data.providers import oanda as provider_oanda
from fiboki.marketstate.calendar import instrument_currencies
from fiboki.marketstate.events import instrument_buckets
from fiboki.sim.profiles import PROFILES

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "oanda"
INSTRUMENTS = FIXTURES / "practice_instruments_2026-09-30.json"
PRICING = FIXTURES / "practice_pricing_2026-09-29T2352Z.json"
SNAPSHOT_LABEL = "snapshot 2026-09-29T23:52Z (late-NY session, wider than London)"


@pytest.fixture(scope="module")
def instruments_payload() -> dict:
    return json.loads(INSTRUMENTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pricing_payload() -> dict:
    return json.loads(PRICING.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def specs(instruments_payload: dict) -> dict[str, OandaInstrumentSpec]:
    return {s.name: s for s in parse_instruments_payload(instruments_payload)}


@pytest.fixture(scope="module")
def snaps(pricing_payload: dict) -> dict:
    return parse_pricing_payload(pricing_payload)


# ============================================================ 1. parsing


def test_the_fixture_has_123_instruments_by_type(specs) -> None:
    assert len(specs) == 123
    counts: dict[str, int] = {}
    for s in specs.values():
        counts[s.type] = counts.get(s.type, 0) + 1
    assert counts == {"CURRENCY": 68, "CFD": 34, "METAL": 21}


def test_decimal_strings_are_parsed_once_into_decimals(specs) -> None:
    eur = specs["EUR_USD"]
    assert isinstance(eur.margin_rate, Decimal)
    assert eur.margin_rate == Decimal("0.03333333333333")
    assert eur.margin_rate_text == "0.03333333333333"
    assert eur.minimum_trade_size == Decimal("1")
    assert eur.pip_location == -4 and eur.pip_size == 0.0001
    assert eur.display_precision == 5 and eur.size_step == 1.0
    assert eur.leverage == Decimal(1) / Decimal("0.03333333333333")
    xau = specs["XAU_USD"]
    assert (xau.pip_size, xau.minimum_trade_size, xau.size_step) == (0.01, Decimal("0.1"), 0.1)
    assert specs["XAU_JPY"].pip_size == 10.0  # pipLocation +1
    assert specs["SUGAR_USD"].pip_size == 0.0001
    assert specs["SPX500_USD"].size_step == 0.01


def test_financing_fields_are_parsed_but_not_turned_into_a_constant(specs) -> None:
    tryjpy = specs["TRY_JPY"]
    assert tryjpy.financing_long_rate == Decimal("0.2191")
    assert tryjpy.financing_short_rate == Decimal("-0.3665")
    assert dict(tryjpy.financing_days)["WEDNESDAY"] == 3
    # The registry keeps its class default, not OANDA's snapshot.
    assert registry.get("TRYJPY").annual_financing_bps == 250.0


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"marginRate": 0.05}, "decimal STRING"),
        ({"marginRate": "abc"}, "not a decimal"),
        ({"marginRate": "0"}, "not in"),
        ({"type": "BOND"}, "unknown OANDA instrument type"),
        ({"pipLocation": "-4"}, "must be an integer"),
        ({"minimumTradeSize": "NaN"}, "not finite"),
    ],
)
def test_malformed_entries_refuse(instruments_payload, patch, match) -> None:
    entry = {**instruments_payload["instruments"][0], **patch}
    with pytest.raises(ValueError, match=match):
        OandaInstrumentSpec.from_payload(entry)


def test_a_duplicate_instrument_refuses(instruments_payload) -> None:
    first = instruments_payload["instruments"][0]
    with pytest.raises(ValueError, match="more than once"):
        parse_instruments_payload({"instruments": [first, first]})


def test_pricing_parses_top_of_book_and_closeout_separately(snaps, specs) -> None:
    assert len(snaps) == 123
    eur = snaps["EUR_USD"]
    assert (eur.bid, eur.ask) == (Decimal("1.13387"), Decimal("1.13395"))
    assert eur.top_of_book_spread_pips(specs["EUR_USD"]) == Decimal("0.8")
    # Closeout sits at the deepest ladder level: 2.7 pips, 3.4x top of book.
    assert eur.closeout_spread_pips(specs["EUR_USD"]) == Decimal("2.7")
    assert eur.tradeable is True
    uk = snaps["UK100_GBP"]
    assert uk.tradeable is False
    assert uk.quote_time == pd.Timestamp("2026-09-29T19:59:05.131306231Z")
    assert uk.response_time == pd.Timestamp("2026-09-29T23:52:22.688236016Z")


def test_snapshot_spread_is_rounded_up_to_one_decimal(snaps, specs) -> None:
    # EUR_USD 0.8 exactly stays 0.8; XAU_USD 0.52 / 0.01 = 52.0; USD_MXN 53.4.
    assert snapshot_spread_pips(specs["EUR_USD"], snaps["EUR_USD"]) == 0.8
    assert snapshot_spread_pips(specs["XAU_USD"], snaps["XAU_USD"]) == 52.0
    assert snapshot_spread_pips(specs["USD_MXN"], snaps["USD_MXN"]) == 53.4
    fake = dataclasses.replace(snaps["EUR_USD"], ask=Decimal("1.133951"))
    assert snapshot_spread_pips(specs["EUR_USD"], fake) == 0.9  # 0.81 -> 0.9


@pytest.mark.parametrize(
    ("bids", "asks", "match"),
    [
        ([], [{"price": "1.1"}], "empty book"),
        ([{"price": "1.2"}], [{"price": "1.1"}], "crossed"),
        ([{"price": "1.1"}], [{"price": "1.1"}], "crossed"),
    ],
)
def test_an_absent_or_crossed_book_refuses(pricing_payload, bids, asks, match) -> None:
    entry = {**pricing_payload["prices"][0], "bids": bids, "asks": asks}
    with pytest.raises(ValueError, match=match):
        parse_pricing_payload({"time": pricing_payload["time"], "prices": [entry]})


def test_no_snapshot_means_no_instrument(specs) -> None:
    with pytest.raises(ValueError, match="refuses to invent a typical spread"):
        instrument_from_oanda(specs["EUR_USD"], spread_snapshot=None)


# ============================================================ 2. mapping


def test_the_mapping_is_complete_and_bidirectional(specs) -> None:
    assert registry.oanda_names() == sorted(specs)
    symbols = [to_fiboki_symbol(n) for n in specs]
    assert len(set(symbols)) == 123, "two OANDA names map to one symbol"
    for name in specs:
        assert to_oanda_name(to_fiboki_symbol(name)) == name
    for symbol in registry.all_symbols():
        assert to_fiboki_symbol(to_oanda_name(symbol)) == symbol
    assert sorted(symbols) == registry.all_symbols()


def test_the_committed_mapping_is_the_rule(specs) -> None:
    for name, spec in specs.items():
        assert derive_fiboki_symbol(name, spec.type) == to_fiboki_symbol(name)


def test_the_rules(specs) -> None:
    assert to_fiboki_symbol("EUR_USD") == "EURUSD"
    assert to_fiboki_symbol("XAU_USD") == "XAUUSD"
    assert to_fiboki_symbol("XAU_XAG") == "XAUXAG"
    assert to_fiboki_symbol("XCU_USD") == "XCUUSD"
    assert to_fiboki_symbol("XPT_USD") == "XPTUSD"
    assert to_fiboki_symbol("XPD_USD") == "XPDUSD"
    cfds = {n for n, s in specs.items() if s.type == "CFD"}
    assert cfds == set(CFD_SYMBOLS) | {"XCU_USD", "XPT_USD", "XPD_USD"}


def test_the_cfd_table_is_the_one_the_operator_specified() -> None:
    assert CFD_SYMBOLS == {
        "SPX500_USD": "US500", "NAS100_USD": "US100", "US30_USD": "US30",
        "US2000_USD": "US2000", "DE30_EUR": "DE40", "UK100_GBP": "UK100",
        "FR40_EUR": "FR40", "EU50_EUR": "EU50", "JP225_USD": "JP225",
        "JP225Y_JPY": "JP225Y", "HK33_HKD": "HK50", "AU200_AUD": "AU200",
        "CN50_USD": "CN50", "CHINAH_HKD": "CHINAH", "SG30_SGD": "SG30",
        "NL25_EUR": "NL25", "CH20_CHF": "CH20", "ESPIX_EUR": "ES35",
        "BCO_USD": "BCOUSD", "WTICO_USD": "WTIUSD", "NATGAS_USD": "NATGASUSD",
        "CORN_USD": "CORNUSD", "SOYBN_USD": "SOYBNUSD", "SUGAR_USD": "SUGARUSD",
        "WHEAT_USD": "WHEATUSD", "DE10YB_EUR": "DE10YB", "UK10YB_GBP": "UK10YB",
        "USB02Y_USD": "USB02Y", "USB05Y_USD": "USB05Y", "USB10Y_USD": "USB10Y",
        "USB30Y_USD": "USB30Y",
    }


def test_unknown_names_raise_on_both_sides() -> None:
    with pytest.raises(KeyError):
        to_oanda_name("NOTREAL")
    with pytest.raises(KeyError):
        to_fiboki_symbol("NOT_REAL")
    with pytest.raises(KeyError):
        derive_fiboki_symbol("FOO_USD", "CFD")
    with pytest.raises(KeyError):
        derive_fiboki_symbol("EUR_XYZ", "CURRENCY")


def test_dxy_is_not_registered_because_oanda_does_not_offer_it(specs) -> None:
    assert not registry.exists("DXY")
    assert not any("DXY" in n or "USDX" in n for n in specs)


def test_every_caller_uses_the_one_mapping(specs) -> None:
    for name in specs:
        symbol = to_fiboki_symbol(name)
        assert broker_oanda.to_oanda_instrument(symbol) == name
        assert broker_oanda.from_oanda_instrument(name) == symbol
        assert provider_oanda.to_oanda_instrument(symbol) == name
        assert provider_oanda.from_oanda_instrument(name) == symbol


def test_no_second_mapping_table_survives() -> None:
    """The adapter used to carry ``_SPECIAL_TO_OANDA``; the provider split strings."""
    for mod in (broker_oanda, provider_oanda):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        names = {
            t.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign | ast.AnnAssign)
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(t, ast.Name)
        }
        assert not {n for n in names if "SPECIAL" in n or "OANDA_NAME" in n}, mod.__name__


def test_the_candle_fixtures_use_mapped_names() -> None:
    for path in FIXTURES.glob("candles_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert to_fiboki_symbol(payload["instrument"]) == "EURUSD"


# ============================================================ 3. asset classes


def test_asset_class_counts(specs) -> None:
    counts: dict[AssetClass, int] = {}
    for s in specs.values():
        cls = asset_class_for(s)
        counts[cls] = counts.get(cls, 0) + 1
    assert counts == {
        AssetClass.FX_MAJOR: 15,
        AssetClass.FX_CROSS: 53,
        AssetClass.METAL: 21,
        AssetClass.INDEX: 18,
        AssetClass.ENERGY: 3,
        AssetClass.BOND: 6,
        AssetClass.COMMODITY: 7,
    }


def test_the_cfd_class_tables_partition_the_cfds(specs) -> None:
    tables = [INDEX_CFDS, ENERGY_CFDS, BOND_CFDS, COMMODITY_CFDS]
    union: set[str] = set().union(*tables)
    assert sum(len(t) for t in tables) == len(union) == 34
    assert union == {n for n, s in specs.items() if s.type == "CFD"}


def test_asset_classes_agree_with_oandas_own_tags(specs) -> None:
    """OANDA's ``ASSET_CLASS`` tag is a coarser classification; it must not contradict ours."""
    allowed = {
        AssetClass.FX_MAJOR: {"CURRENCY"},
        AssetClass.FX_CROSS: {"CURRENCY"},
        AssetClass.METAL: {"COMMODITY"},
        AssetClass.INDEX: {"INDEX"},
        AssetClass.ENERGY: {"COMMODITY"},
        AssetClass.BOND: {"BOND"},
        AssetClass.COMMODITY: {"COMMODITY"},
    }
    brain = {
        AssetClass.INDEX: {"INDEX"},
        AssetClass.ENERGY: {"ENERGY"},
        AssetClass.BOND: {"BOND"},
        AssetClass.COMMODITY: {"COMMODITY", "METAL"},
    }
    for name, spec in specs.items():
        cls = asset_class_for(spec)
        tags = dict(spec.tags)
        assert tags["ASSET_CLASS"] in allowed[cls], name
        if cls in brain:
            assert tags["BRAIN_ASSET_CLASS"] in brain[cls], name


def test_platinum_palladium_copper_are_commodities_not_metals(specs) -> None:
    for name in ("XPT_USD", "XPD_USD", "XCU_USD"):
        assert asset_class_for(specs[name]) is AssetClass.COMMODITY
        assert registry.get(to_fiboki_symbol(name)).retail_leverage == 10.0


# ============================================================ 4. leverage cross-check

#: Every instrument where OANDA's 1/marginRate differs from the ESMA class cap,
#: as ``(OANDA, ESMA)``. Found by this test, not assumed. OANDA is STRICTER in
#: every case; there is no instrument where it is looser.
LEVERAGE_EXCEPTIONS: dict[str, tuple[float, float]] = {
    "AUD_HKD": (10.0, 20.0),
    "CAD_HKD": (10.0, 20.0),
    "CHF_HKD": (10.0, 20.0),
    "EUR_HKD": (10.0, 20.0),
    "GBP_HKD": (10.0, 20.0),
    "HKD_JPY": (10.0, 20.0),
    "NZD_HKD": (10.0, 20.0),
    "USD_HKD": (10.0, 20.0),
    "EUR_DKK": (10.0, 20.0),
    "EUR_TRY": (4.0, 20.0),
    "TRY_JPY": (4.0, 20.0),
    "USD_TRY": (4.0, 20.0),
}


def _esma(spec: OandaInstrumentSpec) -> float:
    inst = registry.get(to_fiboki_symbol(spec.name))
    return registry.esma_retail_leverage(inst.symbol, inst.asset_class, inst.base, inst.quote)


def test_oanda_margin_against_the_esma_table_lists_every_exception(specs) -> None:
    found = {}
    for name, spec in specs.items():
        oanda = float(round(spec.leverage, 6))
        esma = _esma(spec)
        if oanda != esma:
            found[name] = (oanda, esma)
    assert found == LEVERAGE_EXCEPTIONS


@pytest.mark.parametrize("oanda_type", ["CURRENCY", "METAL"])
def test_currency_and_metal_agree_with_esma_except_the_listed(specs, oanda_type) -> None:
    for name, spec in specs.items():
        if spec.type != oanda_type or name in LEVERAGE_EXCEPTIONS:
            continue
        assert float(round(spec.leverage, 6)) == _esma(spec), name


def test_index_leverage_agrees_with_esma(specs) -> None:
    for name in INDEX_CFDS:
        assert float(round(specs[name].leverage, 6)) == _esma(specs[name]), name


def test_the_registry_stores_oandas_figure_not_the_esma_one(specs) -> None:
    for name, spec in specs.items():
        assert registry.get(to_fiboki_symbol(name)).retail_leverage == float(
            round(spec.leverage, 6)
        ), name


# ============================================================ 5. the 41


#: Fields where the hand entry DISAGREED with OANDA and now carries OANDA's value
#: (old -> new). Recorded so the change is visible in one place.
RECONCILED_41: dict[str, dict[str, tuple[object, object]]] = {
    "XAUUSD": {"min_size": (0.01, 0.1), "size_step": (0.01, 0.1), "price_precision": (2, 3)},
    "XAGUSD": {
        "pip_size": (0.001, 0.0001), "typical_spread_pips": (25.0, 250.0),
        "min_size": (0.05, 1.0), "size_step": (0.05, 1.0), "price_precision": (3, 5),
    },
    "WTIUSD": {"min_size": (0.1, 1.0), "size_step": (0.1, 1.0), "price_precision": (2, 3)},
    "BCOUSD": {"min_size": (0.1, 1.0), "size_step": (0.1, 1.0), "price_precision": (2, 3)},
    "US500": {"min_size": (0.1, 0.01), "size_step": (0.1, 0.01)},
    "US100": {"min_size": (0.1, 0.01), "size_step": (0.1, 0.01)},
    "US30": {"min_size": (0.1, 0.01), "size_step": (0.1, 0.01)},
    "DE40": {"min_size": (0.1, 0.01), "size_step": (0.1, 0.01)},
    "JP225": {
        "quote": ("JPY", "USD"), "min_size": (0.1, 0.01), "size_step": (0.1, 0.01),
        "price_precision": (0, 1),
    },
}


def test_41_hand_entries(specs) -> None:
    assert len(registry.HAND_REGISTERED) == 41


@pytest.mark.parametrize("symbol", sorted(registry.HAND_REGISTERED))
def test_hand_entry_equals_oanda_except_its_spread(symbol, specs, snaps) -> None:
    name = to_oanda_name(symbol)
    derived = instrument_from_oanda(specs[name], spread_snapshot=snaps[name])
    ours = registry.get(symbol)
    diff = {
        f.name: (getattr(ours, f.name), getattr(derived, f.name))
        for f in dataclasses.fields(ours)
        if getattr(ours, f.name) != getattr(derived, f.name)
    }
    diff.pop("typical_spread_pips", None)
    assert diff == {}
    for field_name, (_old, new) in RECONCILED_41.get(symbol, {}).items():
        assert getattr(ours, field_name) == new


def test_hand_spreads_keep_their_values_and_say_so() -> None:
    for symbol in registry.HAND_REGISTERED:
        assert registry.SPREAD_PROVENANCE[symbol].startswith("hand-registered London-session")
    assert registry.get("EURUSD").typical_spread_pips == 0.9


def test_xagusd_spread_is_the_same_price_in_the_new_pip() -> None:
    x = registry.get("XAGUSD")
    assert x.typical_spread_pips * x.pip_size == pytest.approx(25.0 * 0.001, abs=1e-15)


# ============================================================ 6. the 82


def test_82_instruments_are_oanda_derived() -> None:
    derived = set(registry.all_symbols()) - registry.HAND_REGISTERED
    assert len(derived) == 82
    assert len(registry.all_symbols()) == 123


@pytest.mark.parametrize(
    "symbol", sorted(set(registry.all_symbols()) - registry.HAND_REGISTERED)
)
def test_new_instrument_is_exactly_the_derivation(symbol, specs, snaps) -> None:
    name = to_oanda_name(symbol)
    assert registry.get(symbol) == instrument_from_oanda(specs[name], spread_snapshot=snaps[name])
    provenance = registry.SPREAD_PROVENANCE[symbol]
    assert provenance.startswith(SNAPSHOT_LABEL)
    assert ("NOT tradeable" in provenance) is (not snaps[name].tradeable)


def test_some_new_fields_by_hand() -> None:
    usb = registry.get("USB10Y")
    assert (usb.asset_class, usb.base, usb.quote) == (AssetClass.BOND, "USB10Y", "USD")
    assert (usb.pip_size, usb.price_precision, usb.retail_leverage) == (0.01, 3, 5.0)
    assert (usb.min_size, usb.size_step, usb.trading_hours) == (1.0, 1.0, "bond_cfd")
    corn = registry.get("CORNUSD")
    assert (corn.asset_class, corn.base, corn.trading_hours) == (
        AssetClass.COMMODITY, "CORN", "commodity_cfd",
    )
    jpy = registry.get("JP225Y")
    assert (jpy.quote, jpy.retail_leverage, jpy.min_size) == ("JPY", 20.0, 1.0)
    xauxag = registry.get("XAUXAG")
    assert (xauxag.asset_class, xauxag.base, xauxag.quote, xauxag.retail_leverage) == (
        AssetClass.METAL, "XAU", "XAG", 10.0,
    )


# ============================================================ 7. the table


def test_the_committed_table_regenerates_byte_for_byte(instruments_payload, pricing_payload):
    spec = importlib.util.spec_from_file_location(
        "oanda_instrument_table", ROOT / "scripts" / "oanda_instrument_table.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = (ROOT / "src" / "fiboki" / "core" / "instruments_oanda_table.py").read_text(
        encoding="utf-8"
    )
    assert module.render_table(instruments_payload, pricing_payload) == committed


def test_the_table_cites_its_sources() -> None:
    from fiboki.core.instruments_oanda_table import OANDA_TABLE_SOURCES

    assert OANDA_TABLE_SOURCES == (
        "tests/fixtures/oanda/practice_instruments_2026-09-30.json",
        "tests/fixtures/oanda/practice_pricing_2026-09-29T2352Z.json",
    )


_NETWORK_MODULES = {"urllib", "http", "socket", "requests", "httpx", "aiohttp", "ssl"}


@pytest.mark.parametrize(
    "path",
    [
        "src/fiboki/core/instruments.py",
        "src/fiboki/core/instruments_oanda_table.py",
        "src/fiboki/broker/oanda_instruments.py",
    ],
)
def test_no_network_and_no_io_at_import(path: str) -> None:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    imported: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.add(node.func.id)
    assert not imported & _NETWORK_MODULES
    assert "open" not in calls


# ============================================================ 8. every consumer


def test_every_registered_instrument_has_a_sane_pip_and_precision() -> None:
    for symbol in registry.all_symbols():
        inst = registry.get(symbol)
        assert inst.pip_size > 0, symbol
        assert inst.price_precision >= 0, symbol
        # the pip is a power of ten ...
        exponent = Decimal(repr(inst.pip_size)).normalize().as_tuple()
        assert exponent.digits == (1,), symbol
        # ... and representable at the displayed precision
        assert inst.pip_size >= 10.0 ** -inst.price_precision - 1e-15, symbol
        assert inst.min_size > 0 and inst.size_step > 0, symbol
        assert inst.typical_spread_pips > 0 and inst.retail_leverage > 0, symbol


def test_every_class_and_label_has_its_rules() -> None:
    classes = {registry.get(s).asset_class for s in registry.all_symbols()}
    labels = {registry.get(s).trading_hours for s in registry.all_symbols()}
    assert {AssetClass.BOND, AssetClass.COMMODITY} <= classes
    assert labels == {"fx_24_5", "index", "energy", "commodity_cfd", "bond_cfd"}
    assert labels <= set(SESSION_CLOSURES)
    assert {c.value for c in classes} <= set(TRIPLE_ROLLOVER_WEEKDAY)
    assert set(registry.TRADING_HOURS_BY_CLASS) == set(registry.DEFAULT_ANNUAL_FINANCING_BPS)
    assert classes <= set(registry.TRADING_HOURS_BY_CLASS)


@pytest.mark.parametrize("symbol", registry.all_symbols())
def test_every_consumer_accepts_every_instrument(symbol: str) -> None:
    inst = registry.get(symbol)
    calendar_for(symbol)
    SessionBarClock.for_instrument(symbol, Timeframe.H4)
    t0, t1 = pd.Timestamp("2024-01-01 12:00", tz="UTC"), pd.Timestamp("2024-01-08 12:00", tz="UTC")
    assert financing_nights(t0, t1, 21, inst.asset_class) == 7
    for profile in PROFILES.values():
        assert profile.min_stop_distance_price(inst) >= 0
    instrument_buckets(symbol)
    instrument_currencies(symbol)


def test_jp225_is_usd_settled_but_still_moves_on_japan() -> None:
    assert registry.get("JP225").quote == "USD"
    assert instrument_buckets("JP225") == frozenset({"INDEX_JP", "JPY"})
    assert instrument_currencies("JP225") == ("JPY",)
    assert instrument_buckets("JP225Y") == frozenset({"INDEX_JP", "JPY"})
