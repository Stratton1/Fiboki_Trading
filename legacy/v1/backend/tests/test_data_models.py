"""Tests for core data models."""

from datetime import datetime, timezone

import pytest

from fibokei.core.models import (
    AssetClass,
    DatasetMeta,
    Direction,
    Instrument,
    OHLCVBar,
    Timeframe,
)
from fibokei.core.instruments import (
    INSTRUMENTS,
    get_instrument,
    get_instruments_by_class,
)


class TestEnums:
    def test_timeframe_values(self):
        assert Timeframe.M1 == "M1"
        assert Timeframe.H4 == "H4"
        assert len(Timeframe) == 7

    def test_asset_class_values(self):
        assert AssetClass.FOREX_MAJOR == "forex_major"
        assert AssetClass.CRYPTO == "crypto"
        assert len(AssetClass) == 9

    def test_direction_values(self):
        assert Direction.LONG == "LONG"
        assert Direction.SHORT == "SHORT"


class TestInstrument:
    def test_create_instrument(self):
        inst = Instrument(
            symbol="EURUSD",
            name="Euro / US Dollar",
            asset_class=AssetClass.FOREX_MAJOR,
        )
        assert inst.symbol == "EURUSD"
        assert inst.pip_value is None
        assert inst.ig_epic is None

    def test_get_instrument(self):
        inst = get_instrument("EURUSD")
        assert inst.symbol == "EURUSD"
        assert inst.asset_class == AssetClass.FOREX_MAJOR

    def test_get_instrument_not_found(self):
        with pytest.raises(KeyError, match="Unknown instrument"):
            get_instrument("FAKE")

    def test_get_instruments_by_class(self):
        metals = get_instruments_by_class(AssetClass.COMMODITY_METAL)
        assert len(metals) == 2
        symbols = {m.symbol for m in metals}
        assert "XAUUSD" in symbols
        assert "XAGUSD" in symbols

    def test_total_instruments(self):
        assert len(INSTRUMENTS) == 67

    def test_all_histdata_instruments_in_registry(self):
        """Every symbol in _HISTDATA_MAP should exist in INSTRUMENTS."""
        from fibokei.data.providers.symbol_map import list_mapped_symbols
        from fibokei.data.providers.base import ProviderID

        histdata_symbols = set(list_mapped_symbols(ProviderID.HISTDATA))
        registry_symbols = {inst.symbol for inst in INSTRUMENTS}
        missing = histdata_symbols - registry_symbols
        assert not missing, f"HistData symbols missing from registry: {missing}"

    def test_alternate_provider_instruments_flagged(self):
        """Instruments without HistData should have has_canonical_data=False."""
        alternate = {"NATGAS", "US30", "BTCUSD", "ETHUSD", "SOLUSD", "LTCUSD", "XRPUSD"}
        for inst in INSTRUMENTS:
            if inst.symbol in alternate:
                assert not inst.has_canonical_data, (
                    f"{inst.symbol} should have has_canonical_data=False"
                )


class TestOHLCVBar:
    def test_valid_bar(self):
        bar = OHLCVBar(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=1.10,
            high=1.12,
            low=1.09,
            close=1.11,
            volume=1000,
        )
        assert bar.open == 1.10
        assert bar.volume == 1000

    def test_high_less_than_low_raises(self):
        with pytest.raises(ValueError):
            OHLCVBar(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                open=1.085,
                high=1.08,
                low=1.09,
                close=1.085,
            )

    def test_default_volume(self):
        bar = OHLCVBar(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=1.10,
            high=1.12,
            low=1.09,
            close=1.11,
        )
        assert bar.volume == 0.0


class TestDatasetMeta:
    def test_create_meta(self):
        meta = DatasetMeta(
            instrument="EURUSD",
            timeframe=Timeframe.H1,
            source_id="test",
            bar_count=500,
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 3, 1, tzinfo=timezone.utc),
        )
        assert meta.timezone == "UTC"
        assert meta.status == "raw_only"
        assert meta.ingest_version == "1.0"
