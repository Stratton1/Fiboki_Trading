"""Providers: each one must declare what its data actually is, and be right.

The HistData tests are the heart of this file. They encode the two facts V1 got
wrong — bid-only prices and an EST-without-DST clock — as executable assertions,
so the bug cannot come back quietly.
"""
from __future__ import annotations

import lzma
from datetime import UTC, datetime

import pandas as pd
import pytest

from fiboki.core.enums import Timeframe
from fiboki.data.providers.base import (
    AuthenticationRequired,
    ProviderError,
    UnsupportedRequest,
)
from fiboki.data.providers.dukascopy import (
    DukascopyProvider,
    DukascopyTick,
    decode_ticks,
    encode_ticks,
    point_factor,
    tick_url,
    ticks_to_bars,
)
from fiboki.data.providers.histdata import (
    ABSENT_VOLUME,
    HISTDATA_TZ_LABEL,
    HistDataParquetProvider,
    bid_to_mid,
    convert_histdata_index,
    detect_timestamp_convention,
    read_histdata_csv,
    zero_volume_fraction,
)
from fiboki.data.providers.oanda import (
    GRANULARITY,
    OandaCandlesProvider,
    from_oanda_instrument,
    parse_candles_response,
    to_oanda_instrument,
)
from fiboki.data.schema import PriceBasis, validate_frame_shape

# ====================================================== HistData


def test_est_no_dst_conversion_adds_five_hours_in_winter_and_summer():
    """Fixed offset means the same +5h all year. That is the whole point."""
    naive = pd.DatetimeIndex(
        [pd.Timestamp("2020-01-15 12:00"), pd.Timestamp("2020-07-15 12:00")]
    )
    utc = convert_histdata_index(naive, already_mislabelled_utc=False)
    assert list(utc) == [
        pd.Timestamp("2020-01-15 17:00", tz="UTC"),
        pd.Timestamp("2020-07-15 17:00", tz="UTC"),
    ]


def test_mislabelled_utc_path_gives_the_same_answer():
    """The V1 store's mistake: naive EST stamped tz=UTC without a shift."""
    mislabelled = pd.DatetimeIndex(
        [pd.Timestamp("2020-07-15 12:00", tz="UTC")]
    )
    fixed = convert_histdata_index(mislabelled, already_mislabelled_utc=True)
    assert fixed[0] == pd.Timestamp("2020-07-15 17:00", tz="UTC")


def test_conversion_refuses_a_mismatched_declaration():
    naive = pd.DatetimeIndex([pd.Timestamp("2020-01-01")])
    with pytest.raises(ProviderError, match="tz-naive"):
        convert_histdata_index(naive, already_mislabelled_utc=True)
    aware = pd.DatetimeIndex([pd.Timestamp("2020-01-01", tz="UTC")])
    with pytest.raises(ProviderError, match="carry no zone"):
        convert_histdata_index(aware, already_mislabelled_utc=False)


def _weekly_index(open_hour_utc_winter: int, *, dst_aware: bool) -> pd.DatetimeIndex:
    """Build hourly bars that open weekly at 17:00 New York."""
    stamps: list[pd.Timestamp] = []
    week_start = pd.Timestamp("2021-01-03 00:00", tz="UTC")  # a Sunday
    for w in range(60):
        sunday = week_start + pd.Timedelta(weeks=w)
        if dst_aware:
            # True 17:00 New York: 22:00 UTC in winter, 21:00 UTC in summer.
            local = pd.Timestamp(
                f"{sunday.date()} 17:00", tz="America/New_York"
            )
            open_ts = local.tz_convert("UTC")
        else:
            open_ts = sunday + pd.Timedelta(hours=open_hour_utc_winter)
        stamps.extend(open_ts + pd.Timedelta(hours=h) for h in range(0, 100))
    return pd.DatetimeIndex(sorted(set(stamps)), tz="UTC")


def test_convention_detector_spots_a_fixed_offset_clock():
    idx = _weekly_index(22, dst_aware=False)
    evidence = detect_timestamp_convention(idx)
    assert evidence.looks_fixed_offset
    assert evidence.distinct_open_hours == 1
    assert "NOT true UTC" in evidence.verdict


def test_convention_detector_accepts_a_real_utc_series():
    idx = _weekly_index(0, dst_aware=True)
    evidence = detect_timestamp_convention(idx)
    assert not evidence.looks_fixed_offset
    assert evidence.distinct_open_hours == 2
    assert set(evidence.weekly_open_hours) <= {21, 22}


def test_convention_detector_needs_a_tz_aware_index():
    with pytest.raises(ProviderError, match="tz-aware"):
        detect_timestamp_convention(pd.DatetimeIndex(["2020-01-01"] * 200))


def test_histdata_csv_reader_declares_bid_and_shifts_to_utc(tmp_path):
    path = tmp_path / "DAT_ASCII_EURUSD_M1_202001.csv"
    path.write_text(
        "\n".join(
            [
                "20200102 170000;1.12000;1.12010;1.11990;1.12005;0",
                "20200102 170100;1.12005;1.12020;1.12000;1.12015;0",
                "20200102 170200;1.12015;1.12030;1.12010;1.12025;0",
            ]
        ),
        encoding="utf-8",
    )
    frame = read_histdata_csv(path, instrument="EURUSD")
    validate_frame_shape(frame)
    assert set(frame["price_basis"]) == {"bid"}
    assert frame.index[0] == pd.Timestamp("2020-01-02 22:00", tz="UTC")
    # All-zero source volume becomes the explicit absent marker.
    assert (frame["volume"] == ABSENT_VOLUME).all()


def test_histdata_csv_reader_rejects_an_empty_file(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ProviderError, match="no parsable rows"):
        read_histdata_csv(path, instrument="EURUSD")


def test_histdata_root_must_exist(tmp_path):
    with pytest.raises(ProviderError, match="does not exist"):
        HistDataParquetProvider(tmp_path / "nope")


def test_histdata_capabilities_are_honest(tmp_path):
    (tmp_path / "EURUSD").mkdir()
    caps = HistDataParquetProvider(tmp_path).capabilities
    assert caps.native_price_basis is PriceBasis.BID
    assert caps.native_timezone == HISTDATA_TZ_LABEL
    assert caps.supports_real_volume is False
    assert caps.supports_bid_ask is False
    assert caps.can_emit_incomplete_bars is False


def test_bid_to_mid_is_explicit_and_stamps_synthetic():
    from tests.data_fixtures import make_bars

    bid = make_bars(price_basis=PriceBasis.BID)
    mid = bid_to_mid(bid, assumed_spread_pips=0.9, pip_size=0.0001)
    assert set(mid["price_basis"]) == {"synthetic_mid"}
    assert mid["close"].iloc[0] == pytest.approx(bid["close"].iloc[0] + 0.000045)
    # The original bid is retained, not thrown away.
    assert mid["bid_close"].iloc[0] == pytest.approx(bid["close"].iloc[0])
    validate_frame_shape(mid)


def test_bid_to_mid_refuses_a_negative_spread():
    from tests.data_fixtures import make_bars

    with pytest.raises(ValueError, match="non-negative"):
        bid_to_mid(make_bars(), assumed_spread_pips=-1.0, pip_size=0.0001)


def test_zero_volume_fraction_reports_blindness():
    from tests.data_fixtures import make_bars

    frame = make_bars()
    assert zero_volume_fraction(frame) == 0.0
    frame["volume"] = ABSENT_VOLUME
    assert zero_volume_fraction(frame) == 1.0


# ====================================================== Dukascopy


def test_tick_url_uses_a_zero_based_month():
    when = datetime(2021, 1, 4, 9, tzinfo=UTC)
    assert tick_url("EURUSD", when).endswith("/EURUSD/2021/00/04/09h_ticks.bi5")
    december = datetime(2021, 12, 31, 23, tzinfo=UTC)
    assert tick_url("EURUSD", december).endswith("/EURUSD/2021/11/31/23h_ticks.bi5")


def test_tick_url_requires_a_tz_aware_datetime():
    with pytest.raises(ProviderError, match="UTC"):
        tick_url("EURUSD", datetime(2021, 1, 4, 9))


def test_point_factor_per_instrument_family():
    assert point_factor("EURUSD") == 1e5
    assert point_factor("USDJPY") == 1e3
    assert point_factor("XAUUSD") == 1e3


def _sample_ticks(hour: datetime, n: int = 120) -> list[DukascopyTick]:
    out = []
    for i in range(n):
        bid = 1.10000 + i * 1e-5
        out.append(
            DukascopyTick(
                timestamp=hour + pd.Timedelta(seconds=30 * i).to_pytimedelta(),
                bid=round(bid, 5),
                ask=round(bid + 0.00010, 5),
                bid_volume=1.5,
                ask_volume=2.5,
            )
        )
    return out


def test_tick_encode_decode_roundtrip():
    hour = datetime(2021, 6, 1, 10, tzinfo=UTC)
    ticks = _sample_ticks(hour, 50)
    payload = encode_ticks(ticks, hour, factor=1e5)
    back = decode_ticks(payload, hour, factor=1e5)
    assert len(back) == len(ticks)
    for a, b in zip(ticks, back, strict=True):
        assert a.timestamp == b.timestamp
        assert a.bid == pytest.approx(b.bid)
        assert a.ask == pytest.approx(b.ask)


def test_truncated_tick_payload_is_refused_not_partially_decoded():
    hour = datetime(2021, 6, 1, 10, tzinfo=UTC)
    raw = lzma.decompress(encode_ticks(_sample_ticks(hour, 10), hour, factor=1e5))
    truncated = lzma.compress(raw[:-7])
    with pytest.raises(ProviderError, match="truncated"):
        decode_ticks(truncated, hour, factor=1e5)


def test_ticks_to_bars_keeps_both_sides_of_the_book():
    hour = datetime(2021, 6, 1, 10, tzinfo=UTC)
    ticks = _sample_ticks(hour, 120)  # one hour of 30-second ticks
    bars = ticks_to_bars(ticks, instrument="EURUSD", timeframe=Timeframe.M15)
    validate_frame_shape(bars)
    assert set(bars["price_basis"]) == {"synthetic_mid"}
    assert len(bars) == 4
    assert (bars["ask_close"] > bars["bid_close"]).all()
    assert bars["tick_volume"].sum() == 120
    assert bars["open"].iloc[0] == pytest.approx(
        (ticks[0].bid + ticks[0].ask) / 2
    )


def test_ticks_to_bars_on_the_bid_side_declares_bid():
    hour = datetime(2021, 6, 1, 10, tzinfo=UTC)
    bars = ticks_to_bars(
        _sample_ticks(hour, 60),
        instrument="EURUSD",
        timeframe=Timeframe.M30,
        price_basis=PriceBasis.BID,
    )
    assert set(bars["price_basis"]) == {"bid"}
    assert bars["close"].iloc[0] == pytest.approx(bars["bid_close"].iloc[0])


def test_dukascopy_offline_batch_declares_its_adjustments():
    hour = datetime(2021, 6, 1, 10, tzinfo=UTC)
    payload = encode_ticks(_sample_ticks(hour, 120), hour, factor=1e5)
    provider = DukascopyProvider()
    batch = provider.bars_from_tick_payloads(
        [(hour, payload)], instrument="EURUSD", timeframe=Timeframe.M15
    )
    kinds = {a.kind for a in batch.adjustments}
    assert kinds == {"price_basis_declaration", "tick_aggregation"}
    assert batch.metadata.timezone_of_origin == "UTC"
    assert batch.metadata.extra["tick_count"] == 120


def test_dukascopy_network_fetch_raises_rather_than_returning_empty():
    with pytest.raises(UnsupportedRequest, match="never look like"):
        DukascopyProvider().fetch_bars("EURUSD", Timeframe.H1)
    with pytest.raises(UnsupportedRequest):
        DukascopyProvider().available()


# ========================================================== OANDA


def _candle(time: str, *, complete: bool = True, base: float = 1.10400):
    return {
        "complete": complete,
        "volume": 1234,
        "time": time,
        "bid": {
            "o": f"{base:.5f}", "h": f"{base + 0.0008:.5f}",
            "l": f"{base - 0.0002:.5f}", "c": f"{base + 0.0006:.5f}",
        },
        "ask": {
            "o": f"{base + 0.00012:.5f}", "h": f"{base + 0.00092:.5f}",
            "l": f"{base - 0.00008:.5f}", "c": f"{base + 0.00072:.5f}",
        },
        "mid": {
            "o": f"{base + 0.00006:.5f}", "h": f"{base + 0.00086:.5f}",
            "l": f"{base - 0.00014:.5f}", "c": f"{base + 0.00066:.5f}",
        },
    }


def v20_payload(*, n: int = 5, last_incomplete: bool = True) -> dict:
    candles = []
    for i in range(n):
        candles.append(
            _candle(
                f"2026-01-05T{9 + i:02d}:00:00.000000000Z",
                complete=not (last_incomplete and i == n - 1),
                base=1.10400 + i * 0.001,
            )
        )
    return {"instrument": "EUR_USD", "granularity": "H1", "candles": candles}


def test_instrument_name_mapping():
    assert to_oanda_instrument("EURUSD") == "EUR_USD"
    assert to_oanda_instrument("XAUUSD") == "XAU_USD"
    assert from_oanda_instrument("EUR_USD") == "EURUSD"
    # Until 2026-09-30 this provider split six-character symbols and refused
    # everything else, so US30 raised. It now delegates to the one OANDA
    # mapping (core/instruments.oanda_name_for, generated from OANDA's own
    # instruments endpoint), which knows every CFD explicitly.
    assert to_oanda_instrument("US30") == "US30_USD"
    assert to_oanda_instrument("DE40") == "DE30_EUR"
    assert from_oanda_instrument("SPX500_USD") == "US500"
    # A v20 name is accepted unchanged.
    assert to_oanda_instrument("US30_USD") == "US30_USD"
    # Unknown on either side still refuses rather than guessing.
    with pytest.raises(ProviderError, match="cannot map"):
        to_oanda_instrument("NOTREAL")
    with pytest.raises(ProviderError):
        from_oanda_instrument("NOT_REAL")


def test_incomplete_candles_are_dropped_by_default():
    """Signals are evaluated on closed candles only."""
    frame, report = parse_candles_response(v20_payload(n=5, last_incomplete=True))
    assert report["candles_received"] == 5
    assert report["incomplete_dropped"] == 1
    assert len(frame) == 4
    assert frame.index.max() == pd.Timestamp("2026-01-05 12:00", tz="UTC")


def test_incomplete_candles_can_be_kept_only_deliberately():
    frame, report = parse_candles_response(
        v20_payload(n=5, last_incomplete=True), drop_incomplete=False
    )
    assert len(frame) == 5
    assert report["incomplete_kept"] == 1


def test_provider_warns_loudly_when_incomplete_candles_are_kept():
    provider = OandaCandlesProvider()
    batch = provider.batch_from_payload(
        v20_payload(n=4, last_incomplete=True), drop_incomplete=False
    )
    assert any("INCOMPLETE" in w for w in batch.warnings)
    assert any("must not reach a backtest" in w for w in batch.warnings)


def test_bid_ask_and_mid_columns_are_all_carried():
    frame, _ = parse_candles_response(v20_payload(last_incomplete=False))
    validate_frame_shape(frame)
    assert set(frame["price_basis"]) == {"mid"}
    assert (frame["ask_close"] > frame["bid_close"]).all()
    assert (frame["close"] > frame["bid_close"]).all()
    assert (frame["close"] < frame["ask_close"]).all()


def test_synthetic_mid_is_computed_from_the_two_sides_and_labelled_as_such():
    frame, _ = parse_candles_response(
        v20_payload(last_incomplete=False), price_basis=PriceBasis.SYNTHETIC_MID
    )
    assert set(frame["price_basis"]) == {"synthetic_mid"}
    expected = (frame["bid_close"] + frame["ask_close"]) / 2
    assert frame["close"].to_numpy() == pytest.approx(expected.to_numpy())


def test_bid_basis_takes_the_bid_block():
    frame, _ = parse_candles_response(
        v20_payload(last_incomplete=False), price_basis=PriceBasis.BID
    )
    assert set(frame["price_basis"]) == {"bid"}
    assert frame["close"].to_numpy() == pytest.approx(frame["bid_close"].to_numpy())


def test_broker_volume_is_recorded_as_tick_volume_not_market_volume():
    frame, _ = parse_candles_response(v20_payload(last_incomplete=False))
    assert (frame["tick_volume"] == 1234).all()
    assert (frame["volume"] == -1).all(), "broker tick count must not become 'volume'"


def test_missing_price_component_raises_rather_than_producing_nan_bars():
    payload = v20_payload(n=2, last_incomplete=False)
    for c in payload["candles"]:
        del c["mid"]
    with pytest.raises(ProviderError, match="price component"):
        parse_candles_response(payload, price_basis=PriceBasis.MID)


def test_all_incomplete_response_raises_rather_than_looking_like_no_data():
    payload = {"instrument": "EUR_USD", "granularity": "H1", "candles": [
        _candle("2026-01-05T09:00:00.000000000Z", complete=False)
    ]}
    with pytest.raises(ProviderError, match="wasted a V1 research batch"):
        parse_candles_response(payload)


def test_unknown_granularity_is_refused():
    payload = v20_payload(last_incomplete=False)
    payload["granularity"] = "H3"
    with pytest.raises(ProviderError, match="unknown v20 granularity"):
        parse_candles_response(payload)


def test_request_params_are_built_correctly():
    provider = OandaCandlesProvider(price_basis=PriceBasis.SYNTHETIC_MID)
    url, params = provider.request_params(
        "EURUSD", Timeframe.H4,
        start=pd.Timestamp("2026-01-01", tz="UTC"),
        end=pd.Timestamp("2026-02-01", tz="UTC"),
    )
    assert url.endswith("/v3/instruments/EUR_USD/candles")
    assert params["granularity"] == "H4"
    assert params["price"] == "BA"
    assert params["from"].startswith("2026-01-01")


def test_request_params_reject_from_to_and_count_together():
    provider = OandaCandlesProvider()
    with pytest.raises(ProviderError, match="at most two"):
        provider.request_params(
            "EURUSD", Timeframe.H1,
            start=pd.Timestamp("2026-01-01", tz="UTC"),
            end=pd.Timestamp("2026-02-01", tz="UTC"),
            count=100,
        )


def test_every_fiboki_timeframe_maps_to_a_v20_granularity():
    assert set(GRANULARITY) == set(Timeframe)


def test_unsupported_timeframe_is_refused():
    caps = OandaCandlesProvider().capabilities
    with pytest.raises(UnsupportedRequest, match="does not provide"):
        object.__getattribute__(caps, "assert_timeframe")(_NotATimeframe())


class _NotATimeframe:
    value = "M90"


def test_fetch_without_credentials_raises_rather_than_returning_empty():
    provider = OandaCandlesProvider()
    with pytest.raises(AuthenticationRequired, match="no history"):
        provider.fetch_bars("EURUSD", Timeframe.H1)
    with pytest.raises(AuthenticationRequired):
        provider.available()


def test_batch_from_fixture_is_fully_described():
    provider = OandaCandlesProvider()
    batch = provider.batch_from_payload(v20_payload(), source_identifier="fixture-1")
    assert batch.metadata.source == "oanda-v20"
    assert batch.metadata.timezone_of_origin == "UTC"
    assert batch.metadata.price_basis is PriceBasis.MID
    assert {a.kind for a in batch.adjustments} == {
        "price_basis_declaration", "volume_semantics"
    }
    assert batch.extra["incomplete_dropped"] == 1
    assert len(batch) == 4
