"""Reconciliation between Fiboki internal state and IG broker state.

Compares positions tracked by Fiboki paper bots with what IG actually reports,
flagging discrepancies for operator review.
"""

import logging
from dataclasses import dataclass

from fibokei.core.instruments import get_symbol_by_epic
from fibokei.execution.ig_adapter import IGExecutionAdapter

logger = logging.getLogger(__name__)


@dataclass
class PositionMismatch:
    """A discrepancy between Fiboki and broker state."""

    type: str  # "missing_at_broker", "missing_in_fiboki", "size_mismatch", "direction_mismatch"
    instrument: str
    fiboki_deal_id: str | None = None
    broker_deal_id: str | None = None
    detail: str = ""


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""

    fiboki_position_count: int
    broker_position_count: int
    matched: int
    mismatches: list[PositionMismatch]

    @property
    def is_clean(self) -> bool:
        return len(self.mismatches) == 0


def reconcile_positions(
    fiboki_positions: list[dict],
    adapter: IGExecutionAdapter,
) -> ReconciliationResult:
    """Compare Fiboki's tracked positions against IG broker positions.

    Args:
        fiboki_positions: List of dicts with keys: deal_id, instrument, direction, size.
        adapter: Authenticated IG adapter to query broker state.

    Returns:
        ReconciliationResult with any mismatches found.
    """
    broker_positions = adapter.get_positions()
    mismatches: list[PositionMismatch] = []
    matched = 0

    # Index broker positions by deal_id
    broker_by_id: dict[str, dict] = {}
    for bp in broker_positions:
        deal_id = bp.get("deal_id", "")
        if deal_id:
            broker_by_id[deal_id] = bp

    # Check each Fiboki position exists at broker
    fiboki_deal_ids = set()
    for fp in fiboki_positions:
        deal_id = fp.get("deal_id", "")
        fiboki_deal_ids.add(deal_id)

        if deal_id not in broker_by_id:
            mismatches.append(PositionMismatch(
                type="missing_at_broker",
                instrument=fp.get("instrument", ""),
                fiboki_deal_id=deal_id,
                detail=f"Fiboki tracks position {deal_id} but broker has no matching position",
            ))
            continue

        bp = broker_by_id[deal_id]
        # Direction check
        fp_dir = fp.get("direction", "").upper()
        bp_dir = bp.get("direction", "").upper()
        if fp_dir and bp_dir and fp_dir != bp_dir:
            mismatches.append(PositionMismatch(
                type="direction_mismatch",
                instrument=fp.get("instrument", ""),
                fiboki_deal_id=deal_id,
                broker_deal_id=deal_id,
                detail=f"Fiboki={fp_dir}, Broker={bp_dir}",
            ))
            continue

        # Size check
        fp_size = float(fp.get("size", 0))
        bp_size = float(bp.get("size", 0))
        if fp_size > 0 and bp_size > 0 and abs(fp_size - bp_size) > 0.001:
            mismatches.append(PositionMismatch(
                type="size_mismatch",
                instrument=fp.get("instrument", ""),
                fiboki_deal_id=deal_id,
                broker_deal_id=deal_id,
                detail=f"Fiboki size={fp_size}, Broker size={bp_size}",
            ))
            continue

        matched += 1

    # Check for broker positions not tracked by Fiboki
    for deal_id, bp in broker_by_id.items():
        if deal_id not in fiboki_deal_ids:
            mismatches.append(PositionMismatch(
                type="missing_in_fiboki",
                instrument=bp.get("instrument", ""),
                broker_deal_id=deal_id,
                detail=f"Broker has position {deal_id} not tracked by Fiboki",
            ))

    result = ReconciliationResult(
        fiboki_position_count=len(fiboki_positions),
        broker_position_count=len(broker_positions),
        matched=matched,
        mismatches=mismatches,
    )

    if result.is_clean:
        logger.info("Reconciliation clean: %d positions matched", matched)
    else:
        logger.warning(
            "Reconciliation found %d mismatches (fiboki=%d, broker=%d, matched=%d)",
            len(mismatches), len(fiboki_positions), len(broker_positions), matched,
        )
        for m in mismatches:
            logger.warning("  [%s] %s: %s", m.type, m.instrument, m.detail)

    return result
