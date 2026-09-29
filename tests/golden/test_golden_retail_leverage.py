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

Every row below is written out by hand from those rules, not read from the
code. If this test fails, the code is wrong or this table is wrong, in that
order (AGENTS.md section 1).
"""
from __future__ import annotations

import pytest

from fiboki.core.enums import AssetClass
from fiboki.core.instruments import all_symbols, get

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


def test_the_table_covers_every_registered_instrument_and_nothing_else() -> None:
    assert len(EXPECTED) == 41
    assert sorted(EXPECTED) == all_symbols()


@pytest.mark.parametrize("symbol", sorted(EXPECTED))
def test_retail_leverage_cap(symbol: str) -> None:
    expected, why = EXPECTED[symbol]
    assert get(symbol).retail_leverage == expected, f"{symbol}: {why}"


#: No skips in a golden module (the golden CI job fails on any), so the FX
#: subset is chosen at collection time.
FX_SYMBOLS = sorted(s for s in EXPECTED if get(s).is_fx)


def test_twenty_seven_fx_pairs_are_registered() -> None:
    assert len(FX_SYMBOLS) == 27


@pytest.mark.parametrize("symbol", FX_SYMBOLS)
def test_fx_asset_class_follows_the_regulatory_definition(symbol: str) -> None:
    instrument = get(symbol)
    expected = (
        AssetClass.FX_MAJOR if EXPECTED[symbol][0] == MAJOR_FX else AssetClass.FX_CROSS
    )
    assert instrument.asset_class is expected
