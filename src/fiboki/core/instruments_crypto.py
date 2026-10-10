"""Spot cryptoassets on Coinbase (UK), GBP-quoted. Recorded 2026-10-10.

Why a separate table
--------------------
Every other registered instrument is an OANDA practice CFD, and OANDA offers no
crypto to a UK retail client (its 2026-09-30 instruments fixture lists none;
the FCA ban on retail crypto derivatives, COBS 22.6.5R, means it cannot). A UK
retail operator can hold crypto only as SPOT, bought outright on an
FCA-registered exchange. ``docs/v2/research/CRYPTO_VENUES_2026-10.md`` records
why the modelled venue is Coinbase (CB Payments Ltd, FCA-registered cryptoasset
firm) with GBP quote pairs.

What spot means here, and what follows from it
----------------------------------------------
* ``retail_leverage = 1.0``: an unleveraged cash purchase. Nothing here is a
  CFD, so the ESMA/FCA 2:1 crypto CFD cap (``core/instruments.py``) does not
  apply; it would only be reachable through a product UK retail may not buy.
* ``shortable = False``: selling spot short needs borrowed coins (margin).
  Every product below has ``margin_enabled: false`` in the recorded products
  fixture, and UK retail margin is in any case restricted. The risk gateway
  refuses a short entry on these instruments and the backtest engine refuses
  it the same way; no strategy is asked to know.
* ``annual_financing_bps = 0.0``: a spot holding pays no overnight financing.
  (Custody is free at the venue; the opportunity cost of cash is not modelled,
  as it is not for any other instrument.)
* ``trading_hours = "crypto_24_7"``: the venue never closes. Planned
  maintenance windows are not modelled.

The columns
-----------
``quote_increment`` (the tick) is the pip; ``base_increment`` is both the
minimum size and the size step. ``min_market_funds`` (GBP 0.72 on every product
below) is a minimum NOTIONAL that this registry has no field for; at any
realistic paper size it does not bind, and an order under it is the venue's to
refuse. ``typical_spread_bps`` is the median of six top-of-book samples taken
about ten seconds apart at ~19:27 UTC on Saturday 2026-10-10
(``tests/fixtures/coinbase/ticker_snapshot_2026-10-10.json``): a SNAPSHOT, not
a measured distribution. It is in basis points of mid because a crypto spread
is a proportion of a price that has moved by a factor of 400 since 2015; a
fixed price-unit spread would charge a 2015 BTC bar five per cent.
``typical_spread_pips`` is that same snapshot converted at the snapshot mid
(ticks), kept only for consumers that read pips; nothing in the crypto cost
model does.

``tests/unit/test_crypto_instruments.py`` reconciles every row against the
recorded products fixture field by field.
"""
from __future__ import annotations

#: Where the venue facts below were read from.
CRYPTO_TABLE_SOURCES: tuple[str, ...] = (
    "tests/fixtures/coinbase/products_2026-10-10.json",
    "tests/fixtures/coinbase/ticker_snapshot_2026-10-10.json",
)

#: The modelled venue. One name, used by the provider, the profile and the API.
CRYPTO_VENUE = "coinbase"

CRYPTO_TABLE_FIELDS: tuple[str, ...] = (
    "symbol",
    "product_id",
    "base",
    "quote",
    "quote_increment",
    "base_increment",
    "price_precision",
    "typical_spread_bps",
    "snapshot_mid",
)

#: (symbol, Coinbase product id, base, quote, quote_increment, base_increment,
#: price decimals, median spread bps, snapshot mid). XRP-GBP is NOT here: it is
#: ``status: delisted`` with ``trading_disabled: true`` in the fixture.
CRYPTO_INSTRUMENTS_2026_10_10: tuple[tuple[object, ...], ...] = (
    ("ADAGBP", "ADA-GBP", "ADA", "GBP", 0.00001, 0.01, 5, 5.245, 0.19099),
    ("BTCGBP", "BTC-GBP", "BTC", "GBP", 0.01, 0.00000001, 2, 0.823, 62721.82),
    ("DOGEGBP", "DOGE-GBP", "DOGE", "GBP", 0.00001, 0.1, 5, 3.073, 0.06511),
    ("DOTGBP", "DOT-GBP", "DOT", "GBP", 0.0001, 0.001, 4, 7.39, 0.9469),
    ("ETHGBP", "ETH-GBP", "ETH", "GBP", 0.01, 0.00000001, 2, 2.791, 1899.24),
    ("LINKGBP", "LINK-GBP", "LINK", "GBP", 0.00001, 0.01, 5, 4.274, 9.88299),
    ("LTCGBP", "LTC-GBP", "LTC", "GBP", 0.001, 0.00000001, 3, 4.645, 48.456),
    ("SOLGBP", "SOL-GBP", "SOL", "GBP", 0.01, 0.001, 2, 2.397, 83.45),
)

#: First daily candle the public candles endpoint returns for each product
#: (probed 2026-10-10 by paging D1 forward from 2014-12-01). Informational: the
#: backfill finds the listing itself; this is what it should find.
CRYPTO_LISTED_FROM: dict[str, str] = {
    "BTCGBP": "2015-04-21",
    "ETHGBP": "2018-09-07",
    "LTCGBP": "2018-09-07",
    "LINKGBP": "2020-07-21",
    "ADAGBP": "2021-03-18",
    "DOGEGBP": "2021-06-03",
    "DOTGBP": "2021-06-16",
    "SOLGBP": "2021-06-17",
}

#: Mean daily GBP turnover on the Coinbase GBP book over the 30 days to
#: 2026-10-10 (sum of D1 volume x close / 30). What ``crypto_majors_v1``
#: (``fiboki.research.universes``) is drawn from.
CRYPTO_GBP_TURNOVER_30D: dict[str, int] = {
    "BTCGBP": 3_841_614,
    "ETHGBP": 1_751_807,
    "SOLGBP": 994_623,
    "LINKGBP": 457_790,
    "ADAGBP": 190_549,
    "DOTGBP": 154_484,
    "LTCGBP": 96_527,
    "DOGEGBP": 80_355,
}
