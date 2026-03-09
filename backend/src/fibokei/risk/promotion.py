"""Promotion gates — Paper→Demo and Demo→Live validation."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class GateResult:
    """Result of a promotion gate check."""
    passed: bool
    gate_name: str
    checks: list[dict] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"]]

    @property
    def summary(self) -> str:
        failed = self.failed_checks
        if not failed:
            return f"{self.gate_name}: all checks passed"
        names = ", ".join(c["name"] for c in failed)
        return f"{self.gate_name}: {len(failed)} check(s) failed — {names}"


def check_paper_to_demo_gate(
    bot_created_at: datetime,
    total_trades: int,
    critical_errors: int,
    composite_score: float,
    *,
    min_days: int = 30,
    min_trades: int = 80,
    max_critical_errors: int = 0,
    min_composite_score: float = 0.55,
) -> GateResult:
    """Check if a paper bot meets Paper→Demo promotion criteria.

    Criteria:
    - Minimum runtime (default 30 days)
    - Minimum trade count (default 80)
    - No critical errors
    - Composite score above threshold
    """
    now = datetime.now(timezone.utc)
    if bot_created_at.tzinfo is None:
        bot_created_at = bot_created_at.replace(tzinfo=timezone.utc)
    runtime_days = (now - bot_created_at).days

    checks = [
        {
            "name": "min_runtime",
            "passed": runtime_days >= min_days,
            "required": f">= {min_days} days",
            "actual": f"{runtime_days} days",
        },
        {
            "name": "min_trades",
            "passed": total_trades >= min_trades,
            "required": f">= {min_trades}",
            "actual": str(total_trades),
        },
        {
            "name": "no_critical_errors",
            "passed": critical_errors <= max_critical_errors,
            "required": f"<= {max_critical_errors}",
            "actual": str(critical_errors),
        },
        {
            "name": "composite_score",
            "passed": composite_score >= min_composite_score,
            "required": f">= {min_composite_score}",
            "actual": f"{composite_score:.4f}",
        },
    ]

    return GateResult(
        passed=all(c["passed"] for c in checks),
        gate_name="paper_to_demo",
        checks=checks,
    )


def check_demo_to_live_gate(
    demo_started_at: datetime,
    total_trades: int,
    reconciliation_rate: float,
    avg_slippage_pips: float,
    manual_signoff: bool,
    *,
    min_days: int = 14,
    min_trades: int = 40,
    min_reconciliation_rate: float = 0.995,
    max_avg_slippage_pips: float = 2.0,
) -> GateResult:
    """Check if a demo bot meets Demo→Live promotion criteria.

    Criteria:
    - Minimum demo runtime (default 14 days)
    - Minimum trade count (default 40)
    - Reconciliation rate > 99.5%
    - Average slippage within tolerance
    - Manual sign-off required
    """
    now = datetime.now(timezone.utc)
    if demo_started_at.tzinfo is None:
        demo_started_at = demo_started_at.replace(tzinfo=timezone.utc)
    runtime_days = (now - demo_started_at).days

    checks = [
        {
            "name": "min_runtime",
            "passed": runtime_days >= min_days,
            "required": f">= {min_days} days",
            "actual": f"{runtime_days} days",
        },
        {
            "name": "min_trades",
            "passed": total_trades >= min_trades,
            "required": f">= {min_trades}",
            "actual": str(total_trades),
        },
        {
            "name": "reconciliation_rate",
            "passed": reconciliation_rate >= min_reconciliation_rate,
            "required": f">= {min_reconciliation_rate * 100:.1f}%",
            "actual": f"{reconciliation_rate * 100:.1f}%",
        },
        {
            "name": "slippage_tolerance",
            "passed": avg_slippage_pips <= max_avg_slippage_pips,
            "required": f"<= {max_avg_slippage_pips} pips",
            "actual": f"{avg_slippage_pips:.1f} pips",
        },
        {
            "name": "manual_signoff",
            "passed": manual_signoff,
            "required": "true",
            "actual": str(manual_signoff).lower(),
        },
    ]

    return GateResult(
        passed=all(c["passed"] for c in checks),
        gate_name="demo_to_live",
        checks=checks,
    )
