"""Core data models for Fiboki."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, field_validator


class Timeframe(str, Enum):
    M1 = "M1"
    M2 = "M2"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"


class AssetClass(str, Enum):
    FOREX_MAJOR = "forex_major"
    FOREX_CROSS = "forex_cross"
    FOREX_G10_CROSS = "forex_g10_cross"
    FOREX_SCANDINAVIAN = "forex_scandinavian"
    FOREX_EM = "forex_em"
    COMMODITY_METAL = "commodity_metal"
    COMMODITY_ENERGY = "commodity_energy"
    INDEX = "index"
    CRYPTO = "crypto"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Instrument(BaseModel):
    symbol: str
    name: str
    asset_class: AssetClass
    has_canonical_data: bool = True
    pip_value: float | None = None
    ig_epic: str | None = None


class OHLCVBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @field_validator("high")
    @classmethod
    def high_gte_low(cls, v: float, info) -> float:
        if "low" in info.data and v < info.data["low"]:
            raise ValueError("high must be >= low")
        return v

    @field_validator("open", "close")
    @classmethod
    def price_in_range(cls, v: float, info) -> float:
        if "low" in info.data and "high" in info.data:
            if v < info.data["low"] or v > info.data["high"]:
                raise ValueError("open/close must be between low and high")
        return v


class DatasetMeta(BaseModel):
    instrument: str
    timeframe: Timeframe
    source_id: str
    timezone: str = "UTC"
    ingest_version: str = "1.0"
    bar_count: int
    start: datetime
    end: datetime
    status: str = "raw_only"
