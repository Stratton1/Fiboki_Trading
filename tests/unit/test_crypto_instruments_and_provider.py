"""Spot crypto: the registry against the recorded venue, the 24/7 calendar, the provider.

Every venue fact is checked against ``tests/fixtures/coinbase/`` (recorded
2026-10-10 from the public Exchange API), the way the OANDA rows are checked
against their fixture: nothing here reads the network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from fiboki.backtest.locks import SessionBarClock
from fiboki.backtest.position import financing_nights
from fiboki.core import instruments as registry
from fiboki.core.enums import AssetClass, Direction, Timeframe
from fiboki.data.calendars import CRYPTO_CALENDAR, calendar_for
from fiboki.data.integrity import validate
from fiboki.data.providers.base import ProviderError, UnsupportedRequest
from fiboki.data.providers.coinbase import (
    COINBASE_MAX_CANDLES,
    CoinbaseCandlesProvider,
    assert_coinbase_host,
    frame_from_candles,
    h1_to_h4,
    page_rows,
)
from fiboki.data.schema import PriceBasis
from fiboki.sim.fills import ContinuousSessionCalendar
from fiboki.sim.profiles import COINBASE_UK_SPOT

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "coinbase"
PRODUCTS = json.loads((FIXTURES / "products_2026-10-10.json").read_text())["products"]
TICKER = json.loads((FIXTURES / "ticker_snapshot_2026-10-10.json").read_text())


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


# ----------------------------------------------------------------- registry


def test_the_crypto_rows_are_the_recorded_products() -> None:
    by_id = {p["id"]: p for p in PRODUCTS}
    assert sorted(registry.CRYPTO_REGISTERED) == [
        "ADAGBP", "BTCGBP", "DOGEGBP", "DOTGBP", "ETHGBP", "LINKGBP", "LTCGBP", "SOLGBP",
    ]
    for symbol in registry.CRYPTO_REGISTERED:
        inst = registry.get(symbol)
        product = by_id[registry.coinbase_product_for(symbol)]
        assert product["status"] == "online" and product["trading_disabled"] is False
        assert product["margin_enabled"] is False, "no margin: cannot be shorted"
        assert (inst.base, inst.quote) == (product["base_currency"], product["quote_currency"])
        assert inst.pip_size == float(product["quote_increment"])
        assert inst.min_size == inst.size_step == float(product["base_increment"])
        assert inst.asset_class is AssetClass.CRYPTO and inst.venue == "coinbase"
        assert registry.symbol_for_coinbase_product(product["id"]) == symbol


def test_xrp_gbp_is_not_registered_because_it_is_delisted() -> None:
    xrp = next(p for p in PRODUCTS if p["id"] == "XRP-GBP")
    assert xrp["status"] == "delisted"
    assert not registry.exists("XRPGBP")


def test_the_spread_snapshot_is_the_ticker_fixture_median() -> None:
    for symbol in registry.CRYPTO_REGISTERED:
        product = registry.coinbase_product_for(symbol)
        recorded = TICKER["stats"][product]["median_spread_bps"]
        assert registry.get(symbol).typical_spread_bps == recorded
        assert "snapshot" in registry.SPREAD_PROVENANCE[symbol]


def test_crypto_is_not_an_oanda_instrument() -> None:
    with pytest.raises(KeyError):
        registry.oanda_name_for("BTCGBP")
    with pytest.raises(KeyError):
        registry.coinbase_product_for("EURUSD")


# ----------------------------------------------------------------- 24/7


@pytest.mark.parametrize("when", [
    "2026-10-10T03:00:00Z",  # Saturday night
    "2026-10-11T21:30:00Z",  # Sunday, inside FX's closed hours
    "2026-12-25T12:00:00Z",  # Christmas: FX holiday, crypto open
    "2027-01-01T00:00:00Z",
])
def test_crypto_never_closes(when: str) -> None:
    ts = pd.Timestamp(when)
    assert calendar_for("BTCGBP") is CRYPTO_CALENDAR
    assert CRYPTO_CALENDAR.is_open(ts)
    assert ContinuousSessionCalendar().is_open(registry.get("BTCGBP"), ts)


def test_a_weekend_gap_in_crypto_is_a_real_gap() -> None:
    start, end = pd.Timestamp("2026-10-09T20:00Z"), pd.Timestamp("2026-10-12T00:00Z")
    expected = CRYPTO_CALENDAR.expected_bar_starts(start, end, Timeframe.H4)
    assert len(expected) == 12, "every H4 slot of the weekend should have printed"
    fx = calendar_for("EURUSD").expected_bar_starts(start, end, Timeframe.H4)
    assert len(fx) < len(expected)


def test_lock_bars_count_every_slot_on_the_crypto_clock() -> None:
    clock = SessionBarClock.for_instrument("ETHGBP", Timeframe.H4)
    friday, monday = pd.Timestamp("2026-10-09T20:00Z"), pd.Timestamp("2026-10-12T00:00Z")
    assert clock.bars_between(friday, monday) == 13
    fx = SessionBarClock.for_instrument("EURUSD", Timeframe.H4)
    assert fx.bars_between(friday, monday) < 13


def test_crypto_financing_nights_are_calendar_nights_and_the_profile_charges_none() -> None:
    nights = financing_nights(pd.Timestamp("2026-10-09T12:00Z"), pd.Timestamp("2026-10-12T12:00Z"),
                              21, AssetClass.CRYPTO)
    assert nights == 3
    assert COINBASE_UK_SPOT.financing.nightly_charge(
        registry.get("BTCGBP"), Direction.LONG, 1.0, 60_000.0
    ) == 0.0


# ------------------------------------------------------------------ provider


def test_a_recorded_page_parses_oldest_first_with_real_volume() -> None:
    fx = _fixture("candles_BTC-GBP_3600.json")
    rows = page_rows(fx["response"], label="fixture")
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)
    frame, report = frame_from_candles(rows, instrument="BTCGBP", timeframe=Timeframe.H1,
                                       now=pd.Timestamp(fx["recorded_at"]))
    assert report["incomplete_dropped"] == 0 and len(frame) == len(rows)
    assert set(frame["price_basis"]) == {PriceBasis.LAST_AS_MID.value}
    assert "tick_volume" not in frame.columns
    newest = max(fx["response"], key=lambda r: r[0])
    # [time, low, high, open, close, volume]; volume in satoshi (base_increment 1e-8).
    last = frame.iloc[-1]
    assert (last["low"], last["high"], last["open"], last["close"]) == tuple(newest[1:5])
    assert last["volume"] == round(newest[5] / 1e-8)
    assert frame.index.tz is not None and str(frame.index.tz) == "UTC"


def test_the_forming_candle_is_dropped_by_the_clock() -> None:
    fx = _fixture("candles_ETH-GBP_3600.json")
    rows = page_rows(fx["response"], label="fixture")
    newest = pd.Timestamp(rows[-1][0], unit="s", tz="UTC")
    _, report = frame_from_candles(rows, instrument="ETHGBP", timeframe=Timeframe.H1,
                                   now=newest + pd.Timedelta(minutes=30))
    assert report["incomplete_dropped"] == 1


def test_a_thin_book_has_gaps_that_are_reported_not_filled() -> None:
    fx = _fixture("candles_DOGE-GBP_3600.json")
    rows = page_rows(fx["response"], label="fixture")
    frame, _ = frame_from_candles(rows, instrument="DOGEGBP", timeframe=Timeframe.H1,
                                  now=pd.Timestamp(fx["recorded_at"]))
    report = validate(frame, calendar=calendar_for("DOGEGBP"))
    span = int((frame.index[-1] - frame.index[0]) / pd.Timedelta(hours=1)) + 1
    assert len(frame) < span, "the recorded window has hours with no trades"
    assert any(not g.expected for g in report.gaps)
    assert report.quality.value == "validated", "a gap is a warning, not a rejection"


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"message": "NotFound"}, "Coinbase answered"),
        ([[1, 2, 3]], "malformed"),
        ([[3, 1, 2, 1, 2, 0], [1, 1, 2, 1, 2, 0], [2, 1, 2, 1, 2, 0]], "not strictly monotonic"),
        ([[2, 1, 2, 1, 2, 0], [2, 1, 2, 1, 2, 0]], "not strictly monotonic"),
    ],
)
def test_a_bad_page_is_refused(payload: Any, match: str) -> None:
    with pytest.raises(ProviderError, match=match):
        page_rows(payload, label="x")


def test_the_transport_wrapper_is_unwrapped() -> None:
    assert page_rows({"_body": [[2, 1, 2, 1, 2, 0], [1, 1, 2, 1, 2, 0]]}, label="x")[0][0] == 1


def test_h4_is_built_from_closed_hours_only() -> None:
    idx = pd.date_range("2026-10-10T00:00Z", periods=10, freq="h")
    rows = [(int(t.timestamp()), 99.0, 101.0, 100.0, 100.5, 1.0) for t in idx]
    h1, _ = frame_from_candles(rows, instrument="BTCGBP", timeframe=Timeframe.H1,
                               now=pd.Timestamp("2026-10-10T10:00Z"))
    h4, dropped = h1_to_h4(h1, now=pd.Timestamp("2026-10-10T10:00Z"))
    assert list(h4.index.hour) == [0, 4] and dropped == 1  # 08:00 bucket is half formed
    assert (h4["volume"] == 4 * round(1.0 / 1e-8)).all()


def test_request_params_are_public_bounded_and_native_only() -> None:
    p = CoinbaseCandlesProvider()
    url, params = p.request_params("BTCGBP", Timeframe.H1, start=pd.Timestamp("2026-01-01T00:00Z"),
                                   end=pd.Timestamp("2026-01-13T11:00Z"))
    assert url == "https://api.exchange.coinbase.com/products/BTC-GBP/candles"
    assert params == {"granularity": 3600, "start": "2026-01-01T00:00:00Z",
                      "end": "2026-01-13T11:00:00Z"}
    with pytest.raises(ProviderError, match="300"):
        p.request_params("BTCGBP", Timeframe.H1, start=pd.Timestamp("2026-01-01T00:00Z"),
                         end=pd.Timestamp("2026-01-13T12:00Z"))
    with pytest.raises(UnsupportedRequest):
        p.request_params("BTCGBP", Timeframe.H4, start=pd.Timestamp("2026-01-01T00:00Z"),
                         end=pd.Timestamp("2026-01-02T00:00Z"))
    assert not p.capabilities.requires_credentials
    assert p.capabilities.native_price_basis is PriceBasis.LAST


@pytest.mark.parametrize("url", [
    "http://api.exchange.coinbase.com", "https://api.exchange.coinbase.com.evil.io",
    "https://api.coinbase.com",
])
def test_only_the_public_exchange_host_is_accepted(url: str) -> None:
    with pytest.raises(ProviderError):
        assert_coinbase_host(url)


class _FakeHttp:
    """Serves a synthetic hourly series by the request's start/end, newest first."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> Any:
        assert "Authorization" not in headers
        self.requests.append(dict(params))
        start, end = pd.Timestamp(params["start"]), pd.Timestamp(params["end"])
        rows = [[int(t.timestamp()), r.low, r.high, r.open, r.close, 2.0]
                for t, r in self.frame.iterrows() if start <= t <= end]
        rows.reverse()

        class R:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> Any:
                return rows

        return R()


def _hourly(n: int, start: str = "2026-09-01T00:00Z") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    base = pd.Series(range(n), index=idx, dtype=float) + 1000.0
    return pd.DataFrame({"open": base, "high": base + 2, "low": base - 2, "close": base + 1})


def test_fetch_bars_pages_at_300_and_derives_h4() -> None:
    frame = _hourly(1000)
    now = frame.index[-1] + pd.Timedelta(minutes=10)  # the last hour is still forming
    http = _FakeHttp(frame)
    p = CoinbaseCandlesProvider(http_client=http, clock=lambda: now)
    batch = p.fetch_bars("BTCGBP", Timeframe.H1, count=700)
    assert len(batch.frame) == 700 and batch.frame.index[-1] == frame.index[-2]
    assert len(http.requests) == 3 and p.requests == 3
    assert all(
        (pd.Timestamp(r["end"]) - pd.Timestamp(r["start"])) / pd.Timedelta(hours=1)
        <= COINBASE_MAX_CANDLES - 1
        for r in http.requests
    )
    h4 = p.fetch_bars("BTCGBP", Timeframe.H4, count=10)
    assert len(h4.frame) == 10 and set(h4.frame["timeframe"]) == {"H4"}
    assert (h4.frame.index + pd.Timedelta(hours=4) <= now).all()
    assert h4.metadata.source == "coinbase_exchange_public"


def test_an_empty_answer_is_an_error_not_an_empty_frame() -> None:
    http = _FakeHttp(_hourly(10, start="2020-01-01T00:00Z"))
    p = CoinbaseCandlesProvider(http_client=http, clock=lambda: pd.Timestamp("2026-10-10T12:00Z"))
    with pytest.raises(ProviderError, match="no closed candles"):
        p.fetch_bars("BTCGBP", Timeframe.H1, count=5)


def test_the_ticker_fixture_is_six_samples_per_product() -> None:
    for stats in TICKER["stats"].values():
        assert stats["n"] == 6
        assert stats["max_spread_bps"] >= stats["median_spread_bps"] > 0
