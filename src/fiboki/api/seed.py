"""Deterministic demonstration data for surfaces with no provisioned source.

WHY THIS EXISTS AND WHAT IT IS NOT
----------------------------------
This repository ships no market data, no paper-engine journal and no broker
session. The operator workstation still has to be built, reviewed and tested
against something. This module generates that something from a fixed seed, so
runs are reproducible and Playwright can assert on values.

It is labelled everywhere it surfaces. :attr:`Platform.data_source` reports
``"seed"``, ``/api/system/services`` reports the datasets as absent, and the
health endpoint is DEGRADED rather than OK. Nothing here pretends to be a
measurement. Each generated trade still carries a real
:class:`~fiboki.core.enums.Provenance` drawn from a realistic mix, because the
whole point of the UI work is that a mixed-provenance table renders honestly.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fiboki.core.enums import Direction, ExitReason, Provenance

__all__ = ["PositionRow", "SeedClock", "TradeRow", "generate"]


def _rand(seed: str, index: int) -> float:
    """Deterministic uniform in [0, 1) from a string seed. No global RNG state."""
    digest = hashlib.sha256(f"{seed}:{index}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


@dataclass(frozen=True, slots=True)
class SeedClock:
    """Anchors generated timestamps so results are stable across processes."""

    now: datetime

    @classmethod
    def fixed(cls) -> SeedClock:
        return cls(datetime(2026, 9, 19, 12, 0, tzinfo=UTC))


@dataclass(frozen=True, slots=True)
class TradeRow:
    trade_id: str
    strategy_id: str
    instrument: str
    direction: Direction
    size: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: ExitReason
    net_pnl: float
    gross_pnl: float
    costs: float
    r_multiple: float
    provenance: Provenance
    account_ccy: str = "GBP"


@dataclass(frozen=True, slots=True)
class PositionRow:
    position_id: str
    strategy_id: str
    instrument: str
    direction: Direction
    size: float
    entry_price: float
    mark_price: float
    entry_time: datetime
    stop_loss: float
    take_profit: float | None
    unrealised_pnl: float
    provenance: Provenance


#: The provenance mix a real deployment in monitored paper would show: a large
#: backtest history, a smaller walk-forward and out-of-sample record, and a
#: young paper record. Rendering this correctly is the point of the exercise.
_MIX: tuple[tuple[Provenance, int], ...] = (
    (Provenance.BACKTEST, 46),
    (Provenance.WALKFORWARD, 14),
    (Provenance.OUT_OF_SAMPLE, 10),
    (Provenance.HOLDOUT, 4),
    (Provenance.PAPER, 22),
    (Provenance.SHADOW, 4),
)


def _provenance_for(index: int) -> Provenance:
    bucket = index % 100
    cursor = 0
    for provenance, weight in _MIX:
        cursor += weight
        if bucket < cursor:
            return provenance
    return Provenance.BACKTEST


def generate(
    strategy_ids: list[str],
    instruments: list[str],
    *,
    clock: SeedClock | None = None,
    n_trades: int = 260,
    n_positions: int = 7,
) -> tuple[list[TradeRow], list[PositionRow]]:
    clock = clock or SeedClock.fixed()
    trades: list[TradeRow] = []
    if not strategy_ids or not instruments:
        return [], []

    for i in range(n_trades):
        strategy = strategy_ids[i % len(strategy_ids)]
        instrument = instruments[(i * 7) % len(instruments)]
        provenance = _provenance_for(i)
        direction = Direction.LONG if _rand("dir", i) > 0.46 else Direction.SHORT
        base = 1.05 + _rand("px", i) * 0.4
        stop_distance = base * (0.004 + _rand("stop", i) * 0.006)
        # A deliberately unflattering edge: ~42% win rate, ~1.5R average win,
        # ~0.95R average loss. Expectancy is about +0.08R BEFORE costs, which
        # costs then erode to roughly break-even. A demonstration fixture that
        # prints a fat equity curve trains the operator to trust the wrong thing.
        win = _rand("win", i) < 0.42
        r_multiple = (1.0 + _rand("r", i) * 1.0) if win else -(0.85 + _rand("r2", i) * 0.2)
        size = round(0.4 + _rand("size", i) * 1.6, 2)
        risk = 250.0
        gross = r_multiple * risk
        costs = 4.5 + _rand("cost", i) * 9.0
        net = gross - costs
        exit_price = base + stop_distance * r_multiple * direction.sign
        exit_time = clock.now - timedelta(hours=int(_rand("t", i) * 24) + i * 3)
        entry_time = exit_time - timedelta(hours=2 + int(_rand("hold", i) * 40))
        trades.append(
            TradeRow(
                trade_id=f"trd_{i:04d}",
                strategy_id=strategy,
                instrument=instrument,
                direction=direction,
                size=size,
                entry_price=round(base, 5),
                exit_price=round(exit_price, 5),
                entry_time=entry_time,
                exit_time=exit_time,
                exit_reason=ExitReason.TAKE_PROFIT if win else ExitReason.STOP_LOSS,
                net_pnl=round(net, 2),
                gross_pnl=round(gross, 2),
                costs=round(costs, 2),
                r_multiple=round(r_multiple, 3),
                provenance=provenance,
            )
        )

    positions: list[PositionRow] = []
    for i in range(n_positions):
        strategy = strategy_ids[i % len(strategy_ids)]
        instrument = instruments[(i * 5) % len(instruments)]
        direction = Direction.LONG if _rand("pdir", i) > 0.5 else Direction.SHORT
        entry = 1.05 + _rand("ppx", i) * 0.4
        drift = (_rand("pdrift", i) - 0.5) * 0.012
        mark = entry * (1 + drift)
        size = round(0.5 + _rand("psize", i) * 1.5, 2)
        positions.append(
            PositionRow(
                position_id=f"pos_{i:03d}",
                strategy_id=strategy,
                instrument=instrument,
                direction=direction,
                size=size,
                entry_price=round(entry, 5),
                mark_price=round(mark, 5),
                entry_time=clock.now - timedelta(hours=3 + i * 11),
                stop_loss=round(entry * (1 - 0.006 * direction.sign), 5),
                take_profit=round(entry * (1 + 0.014 * direction.sign), 5),
                unrealised_pnl=round((mark - entry) * direction.sign * size * 10000, 2),
                # Open positions exist only where execution happens. In a paper
                # deployment they are PAPER; nothing here invents a broker fill.
                provenance=Provenance.PAPER,
            )
        )
    return trades, positions


def equity_curve(trades: list[TradeRow], starting: float = 25_000.0) -> list[tuple[datetime, float]]:
    ordered = sorted(trades, key=lambda t: t.exit_time)
    equity = starting
    out: list[tuple[datetime, float]] = []
    for trade in ordered:
        equity += trade.net_pnl
        out.append((trade.exit_time, round(equity, 2)))
    return out


def sharpe(returns: list[float], periods_per_year: float = 252.0) -> float | None:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (mean / sd) * math.sqrt(periods_per_year)


def max_drawdown_pct(curve: list[tuple[datetime, float]]) -> float | None:
    if not curve:
        return None
    peak = curve[0][1]
    worst = 0.0
    for _, value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100.0)
    return round(worst, 2)
