"""FCA/ESMA retail leverage caps for every registered instrument.

Regulatory sources, both still in force for a UK retail client:

* ESMA Decision (EU) 2018/796 of 22 May 2018, Article 2(1)(b) and Annex II:
  initial margin 3.33% (30:1) for "major currency pairs", 5% (20:1) for
  non-major currency pairs, gold and major equity indices, 10% (10:1) for
  commodities other than gold and non-major equity indices, 20% (5:1) for
  individual equities, 50% (2:1) for crypto-assets.
* FCA Policy Statement PS19/18 (July 2019), which made the same margin rules
  permanent for UK retail CFD and spread-bet clients (COBS 22.5.14).

"Major currency pair" is defined by the currency set in Annex II: any pair made
of two of USD, EUR, JPY, GBP, CAD and CHF. So AUDUSD and NZDUSD are NOT major
(20:1) and EURGBP, EURJPY, GBPJPY, EURCHF, CADJPY, CHFJPY, GBPCAD, GBPCHF,
EURCAD and CADCHF ARE (30:1). "Major indices" are the closed list FTSE 100,
CAC 40, DAX, Dow Jones Industrial Average, S&P 500, NASDAQ Composite, NASDAQ
100, Nikkei 225, S&P/ASX 200 and EURO STOXX 50; the Hang Seng is not on it.

Every row of ``EXPECTED`` is written out by hand from those rules, not read
from the code. If this test fails, the code is wrong or this table is wrong, in
that order (AGENTS.md section 1).

The OANDA registry (2026-09-30)
-------------------------------
OANDA is the only broker, so every instrument its practice account offers is
registered: 123, of which the 41 above were hand-registered first. For those 41
OANDA's own margin agrees with the ESMA row in every case
(:func:`test_oanda_agrees_with_every_hand_row`), so none of their values moved.

For the other 82 the binding figure is OANDA's: the retail leverage OANDA
grants this account is ``1 / marginRate``, and it is what the registry stores.
Each row of ``OANDA_EXPECTED`` cites its source as "OANDA practice instruments
endpoint, marginRate, fixture 2026-09-30" -- ``GET /v3/accounts/{id}/instruments``
recorded in ``tests/fixtures/oanda/practice_instruments_2026-09-30.json`` -- with
the OANDA instrument name and the marginRate string transcribed VERBATIM, and
the leverage worked out by hand as its reciprocal (0.05 -> 20, 0.1 -> 10,
0.20 -> 5, 0.25 -> 4). The transcription was done by script from the fixture,
so it is checked against the fixture rather than trusted:
:func:`test_every_oanda_row_cites_the_fixture_verbatim` re-reads the fixture and
fails on any name or marginRate that differs, and
:func:`test_every_oanda_row_is_the_reciprocal_of_its_margin_rate` redoes the
division in :class:`~decimal.Decimal`.

Where OANDA is STRICTER than the ESMA class cap the row says so with both
numbers (twelve rows: eight HKD pairs and EURDKK at 10:1, three TRY pairs at
4:1, against ESMA's 20:1 for a non-major pair). OANDA is never more permissive
than ESMA for any of the 123; :func:`test_oanda_is_never_more_permissive_than_esma`
holds that.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from fiboki.core.enums import AssetClass
from fiboki.core.instruments import all_symbols, esma_retail_leverage, get

pytestmark = pytest.mark.golden

MAJOR_FX = 30.0      # ESMA 2018/796 Annex II 1(a); PS19/18
NON_MAJOR_FX = 20.0  # Annex II 1(b)
GOLD = 20.0          # Annex II 1(b)
MAJOR_INDEX = 20.0   # Annex II 1(b)
COMMODITY = 10.0     # Annex II 1(c): commodity other than gold
NON_MAJOR_INDEX = 10.0  # Annex II 1(c)

EXPECTED: dict[str, tuple[float, str]] = {
    # --- FX: both legs in {USD, EUR, JPY, GBP, CAD, CHF} -> major
    "EURUSD": (MAJOR_FX, "EUR+USD both major"),
    "GBPUSD": (MAJOR_FX, "GBP+USD both major"),
    "USDJPY": (MAJOR_FX, "USD+JPY both major"),
    "USDCHF": (MAJOR_FX, "USD+CHF both major"),
    "USDCAD": (MAJOR_FX, "USD+CAD both major"),
    "EURGBP": (MAJOR_FX, "EUR+GBP both major (was 20:1, conservative)"),
    "EURJPY": (MAJOR_FX, "EUR+JPY both major (was 20:1)"),
    "GBPJPY": (MAJOR_FX, "GBP+JPY both major (was 20:1)"),
    "EURCHF": (MAJOR_FX, "EUR+CHF both major (was 20:1)"),
    "CADJPY": (MAJOR_FX, "CAD+JPY both major (was 20:1)"),
    "CHFJPY": (MAJOR_FX, "CHF+JPY both major (was 20:1)"),
    "EURCAD": (MAJOR_FX, "EUR+CAD both major (was 20:1)"),
    "GBPCAD": (MAJOR_FX, "GBP+CAD both major (was 20:1)"),
    "GBPCHF": (MAJOR_FX, "GBP+CHF both major (was 20:1)"),
    "CADCHF": (MAJOR_FX, "CAD+CHF both major (was 20:1)"),
    # --- FX: any leg in AUD or NZD -> non-major
    "AUDUSD": (NON_MAJOR_FX, "AUD not in the major set (was 30:1, permissive)"),
    "NZDUSD": (NON_MAJOR_FX, "NZD not in the major set (was 30:1, permissive)"),
    "AUDJPY": (NON_MAJOR_FX, "AUD"),
    "EURAUD": (NON_MAJOR_FX, "AUD"),
    "GBPAUD": (NON_MAJOR_FX, "AUD"),
    "AUDCAD": (NON_MAJOR_FX, "AUD"),
    "AUDCHF": (NON_MAJOR_FX, "AUD"),
    "AUDNZD": (NON_MAJOR_FX, "AUD and NZD"),
    "NZDCAD": (NON_MAJOR_FX, "NZD"),
    "NZDJPY": (NON_MAJOR_FX, "NZD"),
    "NZDCHF": (NON_MAJOR_FX, "NZD"),
    "EURNZD": (NON_MAJOR_FX, "NZD"),
    # --- commodities
    "XAUUSD": (GOLD, "gold"),
    "XAGUSD": (COMMODITY, "silver: commodity other than gold (was 20:1, permissive)"),
    "WTIUSD": (COMMODITY, "commodity other than gold"),
    "BCOUSD": (COMMODITY, "commodity other than gold"),
    # --- indices
    "US500": (MAJOR_INDEX, "S&P 500"),
    "US100": (MAJOR_INDEX, "NASDAQ 100"),
    "US30": (MAJOR_INDEX, "Dow Jones Industrial Average"),
    "UK100": (MAJOR_INDEX, "FTSE 100"),
    "DE40": (MAJOR_INDEX, "DAX"),
    "FR40": (MAJOR_INDEX, "CAC 40"),
    "JP225": (MAJOR_INDEX, "Nikkei 225"),
    "AU200": (MAJOR_INDEX, "S&P/ASX 200"),
    "EU50": (MAJOR_INDEX, "EURO STOXX 50"),
    "HK50": (NON_MAJOR_INDEX, "Hang Seng is not on the major-index list (was 20:1)"),
}


#: The citation every ``OANDA_EXPECTED`` row carries.
OANDA_SOURCE = "OANDA practice instruments endpoint, marginRate, fixture 2026-09-30"
OANDA_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "oanda" / "practice_instruments_2026-09-30.json"
)

#: ``symbol: (leverage = 1/marginRate, OANDA name, marginRate verbatim, note)``.
#: Source for every row: :data:`OANDA_SOURCE`.
OANDA_EXPECTED: dict[str, tuple[float, str, str, str]] = {
    # --- government-bond CFDs: ESMA Annex II(e) other underlyings 5:1 (6)
    "DE10YB": (5.0, "DE10YB_EUR", "0.20", ""),
    "UK10YB": (5.0, "UK10YB_GBP", "0.20", ""),
    "USB02Y": (5.0, "USB02Y_USD", "0.20", ""),
    "USB05Y": (5.0, "USB05Y_USD", "0.20", ""),
    "USB10Y": (5.0, "USB10Y_USD", "0.20", ""),
    "USB30Y": (5.0, "USB30Y_USD", "0.20", ""),
    # --- commodities other than gold: Annex II(c) 10:1 (7)
    "CORNUSD": (10.0, "CORN_USD", "0.10", ""),
    "SOYBNUSD": (10.0, "SOYBN_USD", "0.10", ""),
    "SUGARUSD": (10.0, "SUGAR_USD", "0.10", ""),
    "WHEATUSD": (10.0, "WHEAT_USD", "0.10", ""),
    "XCUUSD": (10.0, "XCU_USD", "0.10", ""),
    "XPDUSD": (10.0, "XPD_USD", "0.10", ""),
    "XPTUSD": (10.0, "XPT_USD", "0.10", ""),
    # --- energy: Annex II(c) 10:1 (1)
    "NATGASUSD": (10.0, "NATGAS_USD", "0.10", ""),
    # --- FX, at least one leg outside the major set: Annex II(b) 20:1 unless OANDA is stricter (41)
    "AUDHKD": (10.0, "AUD_HKD", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "AUDSGD": (20.0, "AUD_SGD", "0.05", ""),
    "CADHKD": (10.0, "CAD_HKD", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "CADSGD": (20.0, "CAD_SGD", "0.05", ""),
    "CHFHKD": (10.0, "CHF_HKD", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "CHFZAR": (20.0, "CHF_ZAR", "0.05", ""),
    "EURCZK": (20.0, "EUR_CZK", "0.05", ""),
    "EURDKK": (10.0, "EUR_DKK", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "EURHKD": (10.0, "EUR_HKD", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "EURHUF": (20.0, "EUR_HUF", "0.05", ""),
    "EURNOK": (20.0, "EUR_NOK", "0.05", ""),
    "EURPLN": (20.0, "EUR_PLN", "0.05", ""),
    "EURSEK": (20.0, "EUR_SEK", "0.05", ""),
    "EURSGD": (20.0, "EUR_SGD", "0.05", ""),
    "EURTRY": (4.0, "EUR_TRY", "0.25", "ESMA allows 20:1; OANDA stricter"),
    "EURZAR": (20.0, "EUR_ZAR", "0.05", ""),
    "GBPHKD": (10.0, "GBP_HKD", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "GBPNZD": (20.0, "GBP_NZD", "0.05", ""),
    "GBPPLN": (20.0, "GBP_PLN", "0.05", ""),
    "GBPSGD": (20.0, "GBP_SGD", "0.05", ""),
    "GBPZAR": (20.0, "GBP_ZAR", "0.05", ""),
    "HKDJPY": (10.0, "HKD_JPY", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "NZDHKD": (10.0, "NZD_HKD", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "NZDSGD": (20.0, "NZD_SGD", "0.05", ""),
    "SGDCHF": (20.0, "SGD_CHF", "0.05", ""),
    "SGDJPY": (20.0, "SGD_JPY", "0.05", ""),
    "TRYJPY": (4.0, "TRY_JPY", "0.25", "ESMA allows 20:1; OANDA stricter"),
    "USDCNH": (20.0, "USD_CNH", "0.05", ""),
    "USDCZK": (20.0, "USD_CZK", "0.05", ""),
    "USDDKK": (20.0, "USD_DKK", "0.05", ""),
    "USDHKD": (10.0, "USD_HKD", "0.1", "ESMA allows 20:1; OANDA stricter"),
    "USDHUF": (20.0, "USD_HUF", "0.05", ""),
    "USDMXN": (20.0, "USD_MXN", "0.05", ""),
    "USDNOK": (20.0, "USD_NOK", "0.05", ""),
    "USDPLN": (20.0, "USD_PLN", "0.05", ""),
    "USDSEK": (20.0, "USD_SEK", "0.05", ""),
    "USDSGD": (20.0, "USD_SGD", "0.05", ""),
    "USDTHB": (20.0, "USD_THB", "0.05", ""),
    "USDTRY": (4.0, "USD_TRY", "0.25", "ESMA allows 20:1; OANDA stricter"),
    "USDZAR": (20.0, "USD_ZAR", "0.05", ""),
    "ZARJPY": (20.0, "ZAR_JPY", "0.05", ""),
    # --- indices: major (Annex II(b), 20:1) only if on the closed list, else Annex II(c) 10:1 (8)
    "CH20": (10.0, "CH20_CHF", "0.1", ""),
    "CHINAH": (10.0, "CHINAH_HKD", "0.1", ""),
    "CN50": (10.0, "CN50_USD", "0.10", ""),
    "ES35": (10.0, "ESPIX_EUR", "0.1", ""),
    "JP225Y": (20.0, "JP225Y_JPY", "0.05", ""),
    "NL25": (10.0, "NL25_EUR", "0.10", ""),
    "SG30": (10.0, "SG30_SGD", "0.10", ""),
    "US2000": (10.0, "US2000_USD", "0.10", ""),
    # --- metals: gold 20:1 (Annex II(b)); anything with silver in it 10:1 (Annex II(c)) (19)
    "XAGAUD": (10.0, "XAG_AUD", "0.1", ""),
    "XAGCAD": (10.0, "XAG_CAD", "0.1", ""),
    "XAGCHF": (10.0, "XAG_CHF", "0.1", ""),
    "XAGEUR": (10.0, "XAG_EUR", "0.1", ""),
    "XAGGBP": (10.0, "XAG_GBP", "0.1", ""),
    "XAGHKD": (10.0, "XAG_HKD", "0.1", ""),
    "XAGJPY": (10.0, "XAG_JPY", "0.1", ""),
    "XAGNZD": (10.0, "XAG_NZD", "0.1", ""),
    "XAGSGD": (10.0, "XAG_SGD", "0.1", ""),
    "XAUAUD": (20.0, "XAU_AUD", "0.05", ""),
    "XAUCAD": (20.0, "XAU_CAD", "0.05", ""),
    "XAUCHF": (20.0, "XAU_CHF", "0.05", ""),
    "XAUEUR": (20.0, "XAU_EUR", "0.05", ""),
    "XAUGBP": (20.0, "XAU_GBP", "0.05", ""),
    "XAUHKD": (20.0, "XAU_HKD", "0.05", ""),
    "XAUJPY": (20.0, "XAU_JPY", "0.05", ""),
    "XAUNZD": (20.0, "XAU_NZD", "0.05", ""),
    "XAUSGD": (20.0, "XAU_SGD", "0.05", ""),
    "XAUXAG": (10.0, "XAU_XAG", "0.1", ""),
}

ALL_EXPECTED: dict[str, float] = {
    **{sym: lev for sym, (lev, _why) in EXPECTED.items()},
    **{sym: row[0] for sym, row in OANDA_EXPECTED.items()},
}


def _fixture_margin_rates() -> dict[str, str]:
    payload = json.loads(OANDA_FIXTURE.read_text(encoding="utf-8"))
    return {e["name"]: e["marginRate"] for e in payload["instruments"]}


def test_the_table_covers_every_registered_instrument_and_nothing_else() -> None:
    assert len(EXPECTED) == 41
    assert len(OANDA_EXPECTED) == 82
    assert not set(EXPECTED) & set(OANDA_EXPECTED)
    assert len(ALL_EXPECTED) == 123
    assert sorted(ALL_EXPECTED) == all_symbols()


@pytest.mark.parametrize("symbol", sorted(EXPECTED))
def test_retail_leverage_cap(symbol: str) -> None:
    expected, why = EXPECTED[symbol]
    assert get(symbol).retail_leverage == expected, f"{symbol}: {why}"


@pytest.mark.parametrize("symbol", sorted(OANDA_EXPECTED))
def test_oanda_retail_leverage(symbol: str) -> None:
    expected, name, rate, note = OANDA_EXPECTED[symbol]
    assert get(symbol).retail_leverage == expected, (
        f"{symbol}: {OANDA_SOURCE}: {name} marginRate {rate} {note}"
    )


def test_every_oanda_row_cites_the_fixture_verbatim() -> None:
    rates = _fixture_margin_rates()
    wrong = {
        sym: (name, rate, rates.get(name))
        for sym, (_lev, name, rate, _note) in OANDA_EXPECTED.items()
        if rates.get(name) != rate
    }
    assert wrong == {}


def test_every_oanda_row_is_the_reciprocal_of_its_margin_rate() -> None:
    for sym, (lev, _name, rate, _note) in OANDA_EXPECTED.items():
        assert Decimal(1) / Decimal(rate) == Decimal(repr(lev)), sym


def test_oanda_agrees_with_every_hand_row() -> None:
    """For the 41 ESMA-derived rows, OANDA's 1/marginRate is the same number.

    Symbol -> OANDA name is written out here rather than imported, for the
    same reason as the rows: so the test does not read the code it checks.
    """
    names = {
        "EURUSD": "EUR_USD", "GBPUSD": "GBP_USD", "USDJPY": "USD_JPY",
        "USDCHF": "USD_CHF", "USDCAD": "USD_CAD", "EURGBP": "EUR_GBP",
        "EURJPY": "EUR_JPY", "GBPJPY": "GBP_JPY", "EURCHF": "EUR_CHF",
        "CADJPY": "CAD_JPY", "CHFJPY": "CHF_JPY", "EURCAD": "EUR_CAD",
        "GBPCAD": "GBP_CAD", "GBPCHF": "GBP_CHF", "CADCHF": "CAD_CHF",
        "AUDUSD": "AUD_USD", "NZDUSD": "NZD_USD", "AUDJPY": "AUD_JPY",
        "EURAUD": "EUR_AUD", "GBPAUD": "GBP_AUD", "AUDCAD": "AUD_CAD",
        "AUDCHF": "AUD_CHF", "AUDNZD": "AUD_NZD", "NZDCAD": "NZD_CAD",
        "NZDJPY": "NZD_JPY", "NZDCHF": "NZD_CHF", "EURNZD": "EUR_NZD",
        "XAUUSD": "XAU_USD", "XAGUSD": "XAG_USD", "WTIUSD": "WTICO_USD",
        "BCOUSD": "BCO_USD", "US500": "SPX500_USD", "US100": "NAS100_USD",
        "US30": "US30_USD", "UK100": "UK100_GBP", "DE40": "DE30_EUR",
        "FR40": "FR40_EUR", "JP225": "JP225_USD", "AU200": "AU200_AUD",
        "EU50": "EU50_EUR", "HK50": "HK33_HKD",
    }
    assert sorted(names) == sorted(EXPECTED)
    rates = _fixture_margin_rates()
    disagree = {
        sym: (EXPECTED[sym][0], float(round(Decimal(1) / Decimal(rates[name]), 6)))
        for sym, name in names.items()
        if float(round(Decimal(1) / Decimal(rates[name]), 6)) != EXPECTED[sym][0]
    }
    assert disagree == {}


def test_oanda_is_never_more_permissive_than_esma() -> None:
    looser = {}
    for sym, lev in ALL_EXPECTED.items():
        inst = get(sym)
        cap = esma_retail_leverage(sym, inst.asset_class, inst.base, inst.quote)
        if lev > cap:
            looser[sym] = (lev, cap)
    assert looser == {}


@pytest.mark.parametrize("symbol", sorted(OANDA_EXPECTED))
def test_a_row_that_differs_from_esma_says_so(symbol: str) -> None:
    lev, _name, _rate, note = OANDA_EXPECTED[symbol]
    inst = get(symbol)
    cap = esma_retail_leverage(symbol, inst.asset_class, inst.base, inst.quote)
    assert (lev != cap) == bool(note), f"{symbol}: {lev} vs ESMA {cap}, note {note!r}"
    if note:
        assert note == f"ESMA allows {cap:g}:1; OANDA stricter"


#: No skips in a golden module (the golden CI job fails on any), so the FX
#: subset is chosen at collection time.
FX_SYMBOLS = sorted(s for s in ALL_EXPECTED if get(s).is_fx)
HAND_FX_SYMBOLS = sorted(s for s in EXPECTED if get(s).is_fx)


def test_twenty_seven_hand_fx_pairs_and_sixty_eight_in_all() -> None:
    assert len(HAND_FX_SYMBOLS) == 27
    # OANDA's CURRENCY count in the 2026-09-30 fixture.
    assert len(FX_SYMBOLS) == 68


@pytest.mark.parametrize("symbol", HAND_FX_SYMBOLS)
def test_fx_asset_class_follows_the_regulatory_definition(symbol: str) -> None:
    instrument = get(symbol)
    expected = (
        AssetClass.FX_MAJOR if EXPECTED[symbol][0] == MAJOR_FX else AssetClass.FX_CROSS
    )
    assert instrument.asset_class is expected


@pytest.mark.parametrize("symbol", sorted(set(FX_SYMBOLS) - set(HAND_FX_SYMBOLS)))
def test_every_oanda_fx_pair_is_a_cross(symbol: str) -> None:
    """All 15 pairs made of two ESMA major currencies are among the 41, so every
    OANDA-derived pair has a leg outside {USD, EUR, JPY, GBP, CAD, CHF}."""
    assert get(symbol).asset_class is AssetClass.FX_CROSS
