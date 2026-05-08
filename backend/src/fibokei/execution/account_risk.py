"""Phase 4 account-aware risk checks for the multi-broker router.

Each ``ExecutionAccountModel`` row carries its own per-account limits:

* ``max_daily_loss_pct``
* ``max_weekly_loss_pct``
* ``max_open_positions``
* ``is_enabled``
* ``live_allowed`` / ``environment`` (handled separately by the router gate)

This module computes the *current* state of those limits from the
parent-child audit tables and returns a typed ``RiskDecision``. The router
calls :func:`evaluate` for each enabled target *after* sizing succeeds and
*before* dispatching to the broker adapter.

Failure semantics (matching Joe's brief):

* Account daily-stop blocks **only** that account.
* Account weekly-stop blocks **only** that account.
* Max-open-positions blocks **only** that account.
* Account ``is_enabled=False`` blocks **only** that account.
* Global kill switch is checked separately and blocks everything.
* One target's risk failure never blocks sibling targets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import func, select


# ── Risk decision ────────────────────────────────────────────


RISK_OK = "ok"
RISK_BLOCK_DAILY_STOP = "DAILY_STOP"
RISK_BLOCK_WEEKLY_STOP = "WEEKLY_STOP"
RISK_BLOCK_MAX_OPEN = "MAX_OPEN_POSITIONS"
RISK_BLOCK_ACCOUNT_DISABLED = "ACCOUNT_DISABLED"


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of an account-level risk check.

    ``allowed`` is the short-circuit return; the router uses it directly.
    The ``reason`` and ``code`` are recorded on the rejected
    :class:`ExecutionAttempt` so operators see *why* an attempt was blocked.
    """

    allowed: bool
    code: str = RISK_OK
    reason: str = ""
    detail: dict | None = None


@dataclass(frozen=True)
class AccountRiskState:
    """Snapshot of the live risk-relevant state for one execution account."""

    account_id: int
    account_name: str
    is_enabled: bool
    allocated_capital: float
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    max_open_positions: int
    open_positions: int
    daily_realised_pnl: float
    weekly_realised_pnl: float
    daily_dd_pct: float
    weekly_dd_pct: float
    blocked: bool
    block_reason: str = ""
    block_code: str = ""


# ── Engine ────────────────────────────────────────────────────


class AccountRiskEngine:
    """Per-account risk evaluator backed by the parent-child audit tables.

    The engine reads from ``execution_attempts`` to compute realised PnL
    and open-position counts per account. Pre-Phase-3 deployments without
    fill_price will return zero, which is safe — the engine will not
    block trades unless real losses appear in the new tables.

    The session_factory pattern matches how the router builds db_targets.
    """

    def __init__(self, session_factory: Callable):
        self._sf = session_factory

    def state_for(self, account_id: int) -> Optional[AccountRiskState]:
        """Return a live snapshot of risk-relevant state for the account.

        Returns ``None`` if the account row no longer exists (e.g. deleted
        between router build and dispatch).
        """
        from fibokei.db.models import (
            ExecutionAccountModel,
            ExecutionAttemptModel,
        )

        with self._sf() as session:
            acct = session.get(ExecutionAccountModel, account_id)
            if acct is None:
                return None

            now = datetime.now(timezone.utc)
            day_start = now - timedelta(days=1)
            week_start = now - timedelta(days=7)

            # Realised PnL is approximated from filled_price - requested_price
            # × filled_size for ``closed`` attempts. Phase 5 will add a
            # dedicated PnL recorder; for now we use 0 when fields are null.
            stmt = (
                select(
                    func.coalesce(func.count(ExecutionAttemptModel.id), 0),
                    func.coalesce(
                        func.sum(
                            (ExecutionAttemptModel.filled_price
                             - ExecutionAttemptModel.requested_price)
                            * ExecutionAttemptModel.filled_size
                        ),
                        0.0,
                    ),
                )
                .where(ExecutionAttemptModel.execution_account_id == account_id)
                .where(ExecutionAttemptModel.status == "closed")
            )
            daily_count, daily_pnl = session.execute(
                stmt.where(ExecutionAttemptModel.created_at >= day_start)
            ).one()
            weekly_count, weekly_pnl = session.execute(
                stmt.where(ExecutionAttemptModel.created_at >= week_start)
            ).one()

            # Open positions: count attempts whose status is filled/submitted
            # without a corresponding 'closed' attempt sharing the same deal id.
            # Phase 4 approximation — refined in Phase 5 alongside reconciliation.
            open_stmt = (
                select(func.count(ExecutionAttemptModel.id))
                .where(ExecutionAttemptModel.execution_account_id == account_id)
                .where(ExecutionAttemptModel.status.in_(("filled", "submitted")))
            )
            open_positions = int(session.execute(open_stmt).scalar() or 0)

            allocated = float(acct.allocated_capital or 0.0)
            daily_pnl_f = float(daily_pnl or 0.0)
            weekly_pnl_f = float(weekly_pnl or 0.0)
            daily_dd_pct = (
                abs(min(daily_pnl_f, 0.0)) / allocated * 100 if allocated > 0 else 0.0
            )
            weekly_dd_pct = (
                abs(min(weekly_pnl_f, 0.0)) / allocated * 100 if allocated > 0 else 0.0
            )

            blocked = False
            reason = ""
            code = ""
            if not acct.is_enabled:
                blocked, reason, code = True, "Account disabled", RISK_BLOCK_ACCOUNT_DISABLED
            elif daily_dd_pct >= float(acct.max_daily_loss_pct or 0):
                blocked = True
                reason = f"Daily DD {daily_dd_pct:.1f}% ≥ limit {acct.max_daily_loss_pct}%"
                code = RISK_BLOCK_DAILY_STOP
            elif weekly_dd_pct >= float(acct.max_weekly_loss_pct or 0):
                blocked = True
                reason = f"Weekly DD {weekly_dd_pct:.1f}% ≥ limit {acct.max_weekly_loss_pct}%"
                code = RISK_BLOCK_WEEKLY_STOP
            elif open_positions >= int(acct.max_open_positions or 0):
                blocked = True
                reason = (
                    f"Open positions {open_positions} ≥ limit {acct.max_open_positions}"
                )
                code = RISK_BLOCK_MAX_OPEN

            return AccountRiskState(
                account_id=acct.id,
                account_name=acct.name,
                is_enabled=bool(acct.is_enabled),
                allocated_capital=allocated,
                max_daily_loss_pct=float(acct.max_daily_loss_pct or 0),
                max_weekly_loss_pct=float(acct.max_weekly_loss_pct or 0),
                max_open_positions=int(acct.max_open_positions or 0),
                open_positions=open_positions,
                daily_realised_pnl=daily_pnl_f,
                weekly_realised_pnl=weekly_pnl_f,
                daily_dd_pct=daily_dd_pct,
                weekly_dd_pct=weekly_dd_pct,
                blocked=blocked,
                block_reason=reason,
                block_code=code,
            )

    def evaluate(self, account_id: int) -> RiskDecision:
        """Return a fast pass/fail decision for the router."""
        state = self.state_for(account_id)
        if state is None:
            return RiskDecision(allowed=True)
        if state.blocked:
            return RiskDecision(
                allowed=False,
                code=state.block_code,
                reason=state.block_reason,
                detail={
                    "account_id": account_id,
                    "open_positions": state.open_positions,
                    "daily_dd_pct": state.daily_dd_pct,
                    "weekly_dd_pct": state.weekly_dd_pct,
                },
            )
        return RiskDecision(allowed=True)
