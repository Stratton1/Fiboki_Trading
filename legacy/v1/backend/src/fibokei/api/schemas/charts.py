"""Pydantic schemas for chart data and annotations."""

from pydantic import BaseModel


class CandleBar(BaseModel):
    timestamp: int  # Unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class IchimokuSeries(BaseModel):
    timestamp: int
    tenkan: float | None = None
    kijun: float | None = None
    senkou_a: float | None = None
    senkou_b: float | None = None
    chikou: float | None = None


class MarketDataResponse(BaseModel):
    instrument: str
    timeframe: str
    candles: list[CandleBar]
    ichimoku: list[IchimokuSeries]
    total_bars: int = 0
    from_date: str | None = None
    to_date: str | None = None
    source: str | None = None
    mode: str = "historical"  # "historical" or "live"


class PricePoint(BaseModel):
    timestamp: str
    price: float


class TradeMarker(BaseModel):
    trade_id: str
    strategy_id: str
    direction: str
    entry: PricePoint
    exit: PricePoint | None = None
    stop_loss: list[PricePoint] = []
    take_profit: list[PricePoint] = []
    partial_exits: list[PricePoint] = []
    label: str = ""
    outcome: str = ""


class StrategyAnnotation(BaseModel):
    type: str
    timestamp: str
    price: float
    label: str = ""
    metadata: dict | None = None


class ChartAnnotationsResponse(BaseModel):
    trade_markers: list[TradeMarker]
    strategy_annotations: list[StrategyAnnotation]
