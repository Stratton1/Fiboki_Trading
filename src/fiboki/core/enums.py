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
    #: Government-bond CFDs (OANDA DE10YB, UK10YB, USB02Y/05Y/10Y/30Y). ESMA
    #: 2018/796 Annex II puts them under "other underlyings" (5:1).
    BOND = "bond"
    #: Commodities other than gold that are neither energy nor a registered
    #: METAL: the agricultural CFDs and copper, platinum and palladium.
    COMMODITY = "commodity"


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
    """Why a position closed.

    **The values are persisted**, in stored trades, backtest records and the
    paper ledger, so a member is never renamed or removed. The set is OPEN:
    members are added as the engine learns to distinguish outcomes it used to
    conflate, and a reader must treat an unrecognised value as "some exit
    reason I have not been taught", not as a corrupt row. Anything that
    switches on this enum needs a default branch; anything that persists a
    distribution of it needs to tolerate new keys appearing.

    ``BREAKEVEN`` was added after the initial vocabulary. Before it, a stop-out
    at a level the breakeven rule had moved was reported as ``TRAILING_STOP``,
    which is the right *shape* (the stop had moved) and the wrong *cause* (no
    trail was involved, and on a strategy with no trail declared at all the
    label was simply false). **Rows already stored as ``trailing_stop`` stay
    valid** — they were written by an engine that could not tell the two apart,
    and they are not rewritten. Whether a given stored result predates the
    distinction is answered by the stored engine version and exit-policy
    fingerprint (see :mod:`fiboki.backtest.version` and
    ``BacktestRecord.exit_policy_fingerprint``), not by guessing from the
    labels.
    """

    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    #: A stop-out at a level the breakeven rule moved, with no trail involved.
    BREAKEVEN = "breakeven"
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
