"""Per-target position sizing for the multi-broker execution router.

Each ``ResolvedTarget`` carries its own ``allocated_capital`` and
``risk_per_trade_pct``. Sizing is computed using the canonical
``calculate_position_size`` from ``backtester.sizing`` (which already
honours IG-aligned leverage caps and JPY-pair pip adjustments) and then
post-processed per-broker:

* Paper / IG: float lot/contract size, preserving existing IG demo behaviour.
* Tradovate: integer contract count — rounded *down* to whole contracts and
  rejected if the result is zero. The brief is explicit: never silently
  approximate futures sizing.
"""

from __future__ import annotations

from typing import Optional

from fibokei.backtester.sizing import calculate_position_size
from fibokei.execution.targets import (
    BROKER_IG,
    BROKER_PAPER,
    BROKER_TRADOVATE,
    NormalisedTradePlan,
    ResolvedTarget,
)


def calculate_target_size(
    target: ResolvedTarget,
    plan: NormalisedTradePlan,
) -> Optional[float]:
    """Compute the order size for a specific target.

    Returns the size in the broker's native units, or ``None`` if the target
    cannot trade this plan at all (zero contracts, zero risk, etc.).

    Sizing inputs are taken from the **target**, never from the bot:
      - ``target.allocated_capital`` (operator-set static allocation)
      - ``target.risk_per_trade_pct``

    The bot's ``PaperAccount.equity`` is intentionally ignored under fan-out
    so that one bot fanning out to two brokers cannot cross-pollute sizing.
    """
    raw = calculate_position_size(
        capital=target.allocated_capital,
        risk_pct=target.risk_per_trade_pct,
        entry=plan.entry_price,
        stop=plan.stop_loss,
        instrument=plan.instrument,
    )
    if raw <= 0:
        return None

    if target.broker == BROKER_TRADOVATE:
        # Futures: round DOWN to whole contracts. Reject if zero.
        contracts = int(raw)
        if contracts <= 0:
            return None
        return float(contracts)

    if target.broker == BROKER_IG:
        # Preserve existing IG demo clamping behaviour. The IG adapter then
        # applies its own additional rounding/clamping for the IG REST API.
        return raw

    if target.broker == BROKER_PAPER:
        return raw

    # Unknown broker — return raw and let the adapter decide.
    return raw
