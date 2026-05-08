"""Reconciliation between Fiboki internal state and broker state.

Compares positions tracked by Fiboki paper bots with what the broker
actually reports, flagging discrepancies for operator review.

Phase 1 of the multi-broker fan-out architecture: the function now accepts
any :class:`ExecutionAdapter` (IG, Tradovate, or paper) so reconciliation
can be invoked per-target.

Phase 5 adds :func:`reconcile_account` for per-execution-account checks
that surface a typed status (clean / mismatch / unavailable /
credentials_missing / unsupported) and never require live broker
credentials at app startup.
"""

import logging
from dataclasses import dataclass, field

from fibokei.execution.adapter import ExecutionAdapter

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
    adapter: ExecutionAdapter,
) -> ReconciliationResult:
    """Compare Fiboki's tracked positions against broker positions.

    Args:
        fiboki_positions: List of dicts with keys: deal_id, instrument, direction, size.
        adapter: Authenticated execution adapter (IG, Tradovate, or paper)
            to query broker state.

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


# ── Phase 5: per-account reconciliation status ──────────────────────────────

# Status vocabulary returned by :func:`reconcile_account`:
RECON_STATUS_CLEAN = "clean"
RECON_STATUS_MISMATCH = "mismatch"
RECON_STATUS_UNAVAILABLE = "unavailable"
RECON_STATUS_CREDENTIALS_MISSING = "credentials_missing"
RECON_STATUS_UNSUPPORTED = "unsupported"


@dataclass
class AccountReconciliationStatus:
    """Per-account reconciliation summary for the System page.

    The status vocabulary lets the frontend pick a colour and message
    without parsing free-form strings:

    * ``clean`` — Fiboki and broker positions match exactly.
    * ``mismatch`` — at least one discrepancy (count in ``mismatch_count``).
    * ``unavailable`` — broker reachable but request failed (network, auth
      timeout, etc).
    * ``credentials_missing`` — operator hasn't configured creds yet.
    * ``unsupported`` — Phase 5 broker reconciliation not implemented (e.g.
      future broker scaffolds).
    """

    account_id: int
    account_name: str
    broker: str
    environment: str
    status: str
    fiboki_position_count: int = 0
    broker_position_count: int = 0
    matched: int = 0
    mismatch_count: int = 0
    detail: str = ""
    mismatches: list[PositionMismatch] = field(default_factory=list)


def reconcile_account(
    account,
    fiboki_positions: list[dict],
    *,
    adapter: ExecutionAdapter | None = None,
) -> AccountReconciliationStatus:
    """Run reconciliation for a single execution account.

    Builds the right adapter for the account if one isn't supplied. Never
    raises — networking/credential errors are converted into a typed status.
    Paper accounts always reconcile clean against the supplied
    ``fiboki_positions`` (which is already the source of truth).
    """
    broker = getattr(account, "broker", "paper")
    environment = getattr(account, "environment", "paper")
    account_id = getattr(account, "id", 0)
    account_name = getattr(account, "name", str(broker))
    base = AccountReconciliationStatus(
        account_id=account_id,
        account_name=account_name,
        broker=broker,
        environment=environment,
        status=RECON_STATUS_CLEAN,
        fiboki_position_count=len(fiboki_positions),
    )

    # Build the adapter lazily so that callers only need to supply the
    # account row; the factory imports stay local so cyclic imports are
    # avoided.
    if adapter is None:
        try:
            if broker == "paper":
                from fibokei.execution.paper_adapter import PaperExecutionAdapter
                adapter = PaperExecutionAdapter()
            elif broker == "ig":
                from fibokei.execution.ig_adapter import IGExecutionAdapter
                adapter = IGExecutionAdapter()
            elif broker == "tradovate":
                from fibokei.execution.tradovate_adapter import (
                    TradovateExecutionAdapter,
                )
                adapter = TradovateExecutionAdapter()
            else:
                base.status = RECON_STATUS_UNSUPPORTED
                base.detail = f"No reconciler for broker '{broker}'"
                return base
        except Exception as e:  # pragma: no cover — defensive
            base.status = RECON_STATUS_UNAVAILABLE
            base.detail = f"Adapter construction failed: {e}"
            return base

    # Paper: positions table is already the source of truth.
    if broker == "paper":
        base.broker_position_count = len(fiboki_positions)
        base.matched = len(fiboki_positions)
        base.detail = "Paper account — Fiboki state is authoritative"
        return base

    # Real broker: probe credentials/positions but never raise.
    try:
        result = reconcile_positions(fiboki_positions, adapter)
    except Exception as e:
        msg = str(e)
        if "MISSING_CREDENTIALS" in msg or "credentials" in msg.lower():
            base.status = RECON_STATUS_CREDENTIALS_MISSING
        else:
            base.status = RECON_STATUS_UNAVAILABLE
        base.detail = msg
        return base

    base.broker_position_count = result.broker_position_count
    base.matched = result.matched
    base.mismatch_count = len(result.mismatches)
    base.mismatches = list(result.mismatches)
    if result.is_clean:
        base.status = RECON_STATUS_CLEAN
        base.detail = "All positions match"
    else:
        base.status = RECON_STATUS_MISMATCH
        base.detail = f"{len(result.mismatches)} mismatch(es)"
    return base
