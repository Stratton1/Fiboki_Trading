"""Canonical enumerations. Values are persisted, so never rename a member's value."""
from __future__ import annotations

from enum import Enum


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1

    @property
    def opposite(self) -> Direction:
        return Direction.SHORT if self is Direction.LONG else Direction.LONG


class AssetClass(str, Enum):
    FX_MAJOR = "fx_major"
    FX_CROSS = "fx_cross"
    METAL = "metal"
    ENERGY = "energy"
    INDEX = "index"
    CRYPTO = "crypto"
    EQUITY = "equity"


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def minutes(self) -> int:
        return {"M1": 1, "M5": 5, "M15": 15, "M30": 30,
                "H1": 60, "H4": 240, "D1": 1440}[self.value]

    @property
    def bars_per_year(self) -> float:
        """Approximate tradeable bars per year (FX week = 120h)."""
        hours_per_year = 120.0 * 52.0
        return hours_per_year * 60.0 / self.minutes


class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    TIME_STOP = "time_stop"
    OPPOSITE_SIGNAL = "opposite_signal"
    INVALIDATION = "invalidation"
    SESSION_CLOSE = "session_close"
    RISK_HALT = "risk_halt"
    END_OF_DATA = "end_of_data"
    MARGIN_CALL = "margin_call"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class ExecutionMode(str, Enum):
    """Where an order actually goes. Deliberately ordered by increasing danger."""
    BACKTEST = "backtest"
    PAPER = "paper"
    SHADOW = "shadow"
    DEMO = "demo"
    LIVE = "live"

    @property
    def touches_real_money(self) -> bool:
        return self is ExecutionMode.LIVE

    @property
    def touches_broker(self) -> bool:
        return self in (ExecutionMode.SHADOW, ExecutionMode.DEMO, ExecutionMode.LIVE)


class StrategyLifecycle(str, Enum):
    DISCOVERY = "discovery"
    RESEARCH = "research"
    VALIDATING = "validating"
    CANDIDATE = "candidate"
    PAPER = "paper"
    SHADOW = "shadow"
    DEMO = "demo"
    APPROVED = "approved"
    LIVE = "live"
    WATCH = "watch"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class DataQuality(str, Enum):
    RAW = "raw"
    VALIDATED = "validated"
    REPAIRED = "repaired"
    SUSPECT = "suspect"
    REJECTED = "rejected"


class Provenance(str, Enum):
    """Where a number came from. Rendered next to every figure in the UI."""
    BACKTEST = "backtest"
    WALKFORWARD = "walkforward"
    OUT_OF_SAMPLE = "out_of_sample"
    HOLDOUT = "holdout"
    PAPER = "paper"
    SHADOW = "shadow"
    BROKER_DEMO = "broker_demo"
    BROKER_LIVE = "broker_live"
