"""Execution telemetry: the measurement that makes backtest-vs-live divergence real.

A backtest says "entered long EURUSD at 1.10420". A live account says "entered
long EURUSD at 1.10437, 94ms after the signal, for 80% of the requested size,
because the rest was rejected". Without a record of the second number, the
divergence between research and reality is a matter of opinion.

This store captures, per execution attempt:

    signal_ts     when the closed bar produced the signal
    decision_ts   when the strategy/risk stack finished deciding
    submit_ts     when the order left us
    ack_ts        when the broker acknowledged it
    fill_ts       when it was filled (or rejected)

    requested vs filled price   -> slippage
    requested vs filled size    -> partial fills
    rejected size + broker error -> capacity and rejection reality
    spread at decision          -> whether the cost model was close
    market regime               -> where the divergence concentrates

Append-only, on the same CRC-framed segment format as the quote recorder, for
the same reason: this process will be killed and must not lose or corrupt what
it already wrote.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core import instruments as instrument_registry
from fiboki.core.enums import Direction, ExecutionMode, OrderType
from fiboki.data.recorder import AppendOnlyLog, LogReader, ReadReport


class MarketRegime(str, Enum):
    """Coarse state at decision time. Divergence is rarely regime-neutral."""

    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"
    QUIET = "quiet"
    NEWS = "news"
    ILLIQUID = "illiquid"
    UNKNOWN = "unknown"


class ExecutionOutcome(str, Enum):
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ExecutionTelemetryRecord:
    """One execution attempt, from signal to whatever actually happened."""

    event_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    direction: Direction
    execution_mode: ExecutionMode
    order_type: OrderType
    outcome: ExecutionOutcome

    signal_ts: datetime
    decision_ts: datetime
    submit_ts: datetime | None = None
    broker_ack_ts: datetime | None = None
    fill_ts: datetime | None = None

    bar_timestamp: datetime | None = None
    dataset_version_id: str | None = None

    requested_price: float | None = None
    filled_price: float | None = None
    requested_size: float = 0.0
    filled_size: float = 0.0
    rejected_size: float = 0.0

    spread_at_decision: float | None = None
    bid_at_decision: float | None = None
    ask_at_decision: float | None = None
    market_regime: MarketRegime = MarketRegime.UNKNOWN

    broker_error_code: str | None = None
    broker_error_message: str | None = None
    broker_order_id: str | None = None
    notes: str = ""

    # -- derived -----------------------------------------------------

    @property
    def slippage_price(self) -> float | None:
        """Signed slippage in price units. Positive = worse than requested."""
        if self.requested_price is None or self.filled_price is None:
            return None
        return (self.filled_price - self.requested_price) * self.direction.sign

    @property
    def slippage_pips(self) -> float | None:
        raw = self.slippage_price
        if raw is None:
            return None
        try:
            pip = instrument_registry.get(self.instrument).pip_size
        except KeyError:
            return None
        return raw / pip

    @property
    def fill_ratio(self) -> float | None:
        if not self.requested_size:
            return None
        return self.filled_size / self.requested_size

    def _ms(self, a: datetime | None, b: datetime | None) -> float | None:
        if a is None or b is None:
            return None
        return (b - a).total_seconds() * 1000.0

    @property
    def decision_latency_ms(self) -> float | None:
        return self._ms(self.signal_ts, self.decision_ts)

    @property
    def submit_latency_ms(self) -> float | None:
        return self._ms(self.decision_ts, self.submit_ts)

    @property
    def ack_latency_ms(self) -> float | None:
        return self._ms(self.submit_ts, self.broker_ack_ts)

    @property
    def fill_latency_ms(self) -> float | None:
        return self._ms(self.submit_ts, self.fill_ts)

    @property
    def total_latency_ms(self) -> float | None:
        return self._ms(self.signal_ts, self.fill_ts)

    # -- serialisation -----------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        d = asdict(self)
        for key in (
            "signal_ts", "decision_ts", "submit_ts", "broker_ack_ts", "fill_ts",
            "bar_timestamp",
        ):
            value = getattr(self, key)
            d[key] = value.astimezone(UTC).isoformat() if value else None
        d["direction"] = self.direction.value
        d["execution_mode"] = self.execution_mode.value
        d["order_type"] = self.order_type.value
        d["outcome"] = self.outcome.value
        d["market_regime"] = self.market_regime.value
        d["slippage_price"] = self.slippage_price
        d["slippage_pips"] = self.slippage_pips
        d["fill_ratio"] = self.fill_ratio
        d["decision_latency_ms"] = self.decision_latency_ms
        d["submit_latency_ms"] = self.submit_latency_ms
        d["ack_latency_ms"] = self.ack_latency_ms
        d["fill_latency_ms"] = self.fill_latency_ms
        d["total_latency_ms"] = self.total_latency_ms
        return d

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> ExecutionTelemetryRecord:
        def _dt(key: str) -> datetime | None:
            value = raw.get(key)
            return datetime.fromisoformat(value) if value else None

        return cls(
            event_id=raw["event_id"],
            strategy_id=raw["strategy_id"],
            instrument=raw["instrument"],
            timeframe=raw["timeframe"],
            direction=Direction(raw["direction"]),
            execution_mode=ExecutionMode(raw["execution_mode"]),
            order_type=OrderType(raw["order_type"]),
            outcome=ExecutionOutcome(raw["outcome"]),
            signal_ts=_dt("signal_ts"),  # type: ignore[arg-type]
            decision_ts=_dt("decision_ts"),  # type: ignore[arg-type]
            submit_ts=_dt("submit_ts"),
            broker_ack_ts=_dt("broker_ack_ts"),
            fill_ts=_dt("fill_ts"),
            bar_timestamp=_dt("bar_timestamp"),
            dataset_version_id=raw.get("dataset_version_id"),
            requested_price=raw.get("requested_price"),
            filled_price=raw.get("filled_price"),
            requested_size=float(raw.get("requested_size", 0.0)),
            filled_size=float(raw.get("filled_size", 0.0)),
            rejected_size=float(raw.get("rejected_size", 0.0)),
            spread_at_decision=raw.get("spread_at_decision"),
            bid_at_decision=raw.get("bid_at_decision"),
            ask_at_decision=raw.get("ask_at_decision"),
            market_regime=MarketRegime(raw.get("market_regime", "unknown")),
            broker_error_code=raw.get("broker_error_code"),
            broker_error_message=raw.get("broker_error_message"),
            broker_order_id=raw.get("broker_order_id"),
            notes=raw.get("notes", ""),
        )


class TelemetryStore:
    """Append-only execution telemetry."""

    def __init__(
        self,
        directory: str | Path,
        *,
        max_records_per_segment: int = 20_000,
        fsync_every: int = 1,
    ) -> None:
        self.directory = Path(directory)
        self.log = AppendOnlyLog(
            directory,
            max_records_per_segment=max_records_per_segment,
            fsync_every=fsync_every,
            stream_name="execution_telemetry",
        )

    def record(self, event: ExecutionTelemetryRecord) -> None:
        self.log.append(event.to_payload())

    def close(self) -> None:
        self.log.close()

    def __enter__(self) -> TelemetryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class TelemetryReader:
    def __init__(self, directory: str | Path) -> None:
        self.reader = LogReader(directory)

    def records(
        self, *, tolerate_interior_corruption: bool = False
    ) -> tuple[list[ExecutionTelemetryRecord], ReadReport]:
        raw, report = self.reader.read_all(
            tolerate_interior_corruption=tolerate_interior_corruption
        )
        return [ExecutionTelemetryRecord.from_payload(r) for r in raw], report

    def frame(self, **kwargs: Any) -> tuple[pd.DataFrame, ReadReport]:
        records, report = self.records(**kwargs)
        if not records:
            return pd.DataFrame(), report
        rows = []
        for r in records:
            rows.append(
                {
                    "event_id": r.event_id,
                    "strategy_id": r.strategy_id,
                    "instrument": r.instrument,
                    "timeframe": r.timeframe,
                    "direction": r.direction.value,
                    "execution_mode": r.execution_mode.value,
                    "outcome": r.outcome.value,
                    "signal_ts": pd.Timestamp(r.signal_ts),
                    "decision_ts": pd.Timestamp(r.decision_ts),
                    "fill_ts": pd.Timestamp(r.fill_ts) if r.fill_ts else pd.NaT,
                    "requested_price": r.requested_price,
                    "filled_price": r.filled_price,
                    "slippage_pips": r.slippage_pips,
                    "requested_size": r.requested_size,
                    "filled_size": r.filled_size,
                    "rejected_size": r.rejected_size,
                    "fill_ratio": r.fill_ratio,
                    "spread_at_decision": r.spread_at_decision,
                    "market_regime": r.market_regime.value,
                    "decision_latency_ms": r.decision_latency_ms,
                    "submit_latency_ms": r.submit_latency_ms,
                    "ack_latency_ms": r.ack_latency_ms,
                    "fill_latency_ms": r.fill_latency_ms,
                    "total_latency_ms": r.total_latency_ms,
                    "broker_error_code": r.broker_error_code,
                    "dataset_version_id": r.dataset_version_id,
                }
            )
        df = pd.DataFrame(rows).set_index("signal_ts").sort_index(kind="stable")
        return df, report


def slippage_summary(frame: pd.DataFrame, *, by: str | list[str] = "instrument") -> pd.DataFrame:
    """Realised slippage and fill quality — the backtest-realism scorecard.

    Compare ``median_slippage_pips`` here against the static spread assumption
    used in backtests. A persistent gap means the backtest is optimistic by that
    much per trade, and should be said out loud rather than absorbed.
    """
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "attempts", "fills", "reject_rate", "median_slippage_pips",
                "p90_slippage_pips", "mean_fill_ratio", "median_total_latency_ms",
            ]
        )
    grouped = frame.groupby(by)
    out = pd.DataFrame(
        {
            "attempts": grouped.size(),
            "fills": grouped["outcome"].apply(lambda s: int((s == "filled").sum())),
            "reject_rate": grouped["outcome"].apply(
                lambda s: float((s == "rejected").mean())
            ),
            "median_slippage_pips": grouped["slippage_pips"].median(),
            "p90_slippage_pips": grouped["slippage_pips"].quantile(0.90),
            "mean_fill_ratio": grouped["fill_ratio"].mean(),
            "median_total_latency_ms": grouped["total_latency_ms"].median(),
        }
    )
    return out


def divergence_report(
    telemetry: pd.DataFrame, *, assumed_spread_pips: dict[str, float]
) -> pd.DataFrame:
    """Assumed cost vs realised cost, per instrument.

    ``assumed_spread_pips`` is normally
    ``{sym: instruments.get(sym).typical_spread_pips}``. A positive
    ``excess_cost_pips`` means live trading is more expensive than the backtest
    assumed and every stored expectancy for that instrument is overstated.
    """
    if telemetry.empty:
        return pd.DataFrame(
            columns=["assumed_spread_pips", "realised_slippage_pips", "excess_cost_pips", "n"]
        )
    rows = []
    for instrument, group in telemetry.groupby("instrument"):
        assumed = float(assumed_spread_pips.get(str(instrument), np.nan))
        realised = float(group["slippage_pips"].median())
        rows.append(
            {
                "instrument": instrument,
                "assumed_spread_pips": assumed,
                "realised_slippage_pips": realised,
                # A market order crosses half the spread; that half is what the
                # backtest already charged, so the excess is what is left over.
                "excess_cost_pips": realised - assumed / 2.0,
                "n": int(len(group)),
            }
        )
    return pd.DataFrame(rows).set_index("instrument")
