"""Promotion gate — turns research evidence into a lifecycle recommendation.

This is the single, deterministic place that decides *how far* a
strategy-instrument-timeframe combination is allowed to advance, based on the
evidence gathered by the research stack (backtest metrics, OOS split, Monte
Carlo, realism + concentration flags).

Hard rules (carried from RULES / NEXT_PHASES):
- It **recommends**, it never executes. No broker, no order, no flag flip here.
- It can never recommend ``live``. The most it returns is ``demo_candidate``;
  going live is a separate, explicit, per-bot human decision (and a Safety
  Governor sign-off), recorded as ``proposed_live`` / ``approved_live`` events
  in the lifecycle ledger.
- Robustness over raw profit: a combo only advances past the watchlist if it
  holds up out-of-sample and under Monte Carlo resampling.
- Thresholds are explicit and match the validation modules
  (``research/oos.py`` robust = OOS ≥ 50% of IS; ``research/monte_carlo.py``
  robust = profit probability ≥ 0.70; ``research/scorer.py`` min_trades = 80).

The lifecycle states mirror ``NEXT_PHASES.md`` and the ``bot_lifecycle_events``
ledger vocabulary: rejected → research_watchlist → paper_candidate →
paper_running → demo_candidate → (live, human-gated).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field


class LifecycleState(str, Enum):
    """Promotion lifecycle states (ordered)."""

    REJECTED = "rejected"
    RESEARCH_WATCHLIST = "research_watchlist"
    PAPER_CANDIDATE = "paper_candidate"
    PAPER_RUNNING = "paper_running"
    DEMO_CANDIDATE = "demo_candidate"
    LIVE = "live"  # never auto-recommended — human + safety sign-off only


class PromotionThresholds(BaseModel):
    """Explicit, auditable promotion thresholds.

    Defaults are deliberately conservative and aligned with the validation
    modules. Tune via config, never by editing call sites.
    """

    # Hard gates (in-sample) — fail any → rejected
    min_trades: int = Field(default=80, description="Min trades for ranking (scorer)")
    min_profit_factor: float = Field(default=1.10)
    max_drawdown_pct: float = Field(default=25.0)
    require_positive_expectancy: bool = True

    # Watchlist → paper_candidate (robustness)
    oos_min_retention: float = Field(
        default=0.5, description="OOS score >= this fraction of IS score"
    )
    mc_min_profit_probability: float = Field(default=0.70)
    mc_max_ruin_probability: float = Field(
        default=0.05, description="Fraction of MC sims with DD>50% must be <= this"
    )
    paper_candidate_min_score: float = Field(
        default=0.40, description="Composite score floor for paper candidacy"
    )
    watchlist_min_score: float = Field(default=0.30)

    # demo_candidate (after live paper running)
    demo_candidate_min_paper_trades: int = Field(default=40)


@dataclass
class PromotionDecision:
    """Outcome of evaluating one combination against the gates."""

    strategy_id: str
    instrument: str
    timeframe: str
    recommended_state: str = LifecycleState.REJECTED.value
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    requires_human_approval: bool = False


def evaluate_promotion(
    *,
    strategy_id: str,
    instrument: str,
    timeframe: str,
    metrics: dict,
    composite_score: float,
    oos_robust: bool | None = None,
    oos_retention: float | None = None,
    mc_profit_probability: float | None = None,
    mc_ruin_probability: float | None = None,
    realism_warnings: list[str] | None = None,
    concentration_ok: bool = True,
    paper_trades: int | None = None,
    paper_positive_expectancy: bool | None = None,
    thresholds: PromotionThresholds | None = None,
) -> PromotionDecision:
    """Recommend the furthest lifecycle state the evidence supports.

    Only ``metrics`` + ``composite_score`` are required (an in-sample backtest).
    OOS / Monte Carlo / paper inputs unlock the higher states when provided;
    absent evidence caps the recommendation lower (you cannot skip a gate by
    simply not running it).
    """
    t = thresholds or PromotionThresholds()
    d = PromotionDecision(strategy_id=strategy_id, instrument=instrument,
                          timeframe=timeframe)

    trades = int(metrics.get("total_trades", 0))
    pf = metrics.get("profit_factor") or 0.0
    dd = metrics.get("max_drawdown_pct") or 0.0
    net = metrics.get("total_net_profit", 0.0)

    # ── Hard in-sample gates ──────────────────────────────────────────
    hard_ok = True
    if trades >= t.min_trades:
        d.passed.append(f"trades>={t.min_trades} ({trades})")
    else:
        d.failed.append(f"trades<{t.min_trades} ({trades})")
        hard_ok = False
    if pf >= t.min_profit_factor:
        d.passed.append(f"profit_factor>={t.min_profit_factor} ({pf:.2f})")
    else:
        d.failed.append(f"profit_factor<{t.min_profit_factor} ({pf:.2f})")
        hard_ok = False
    if dd <= t.max_drawdown_pct:
        d.passed.append(f"max_dd<={t.max_drawdown_pct}% ({dd:.1f}%)")
    else:
        d.failed.append(f"max_dd>{t.max_drawdown_pct}% ({dd:.1f}%)")
        hard_ok = False
    if not t.require_positive_expectancy or net > 0:
        d.passed.append(f"net_profit>0 ({net:.0f})")
    else:
        d.failed.append(f"net_profit<=0 ({net:.0f})")
        hard_ok = False

    if not hard_ok:
        d.recommended_state = LifecycleState.REJECTED.value
        return d

    # Passed hard gates → at least research_watchlist (if score clears floor)
    if composite_score >= t.watchlist_min_score:
        d.passed.append(f"score>={t.watchlist_min_score} ({composite_score:.3f})")
        d.recommended_state = LifecycleState.RESEARCH_WATCHLIST.value
    else:
        d.failed.append(f"score<{t.watchlist_min_score} ({composite_score:.3f})")
        d.recommended_state = LifecycleState.REJECTED.value
        return d

    # ── Robustness gates → paper_candidate ────────────────────────────
    robust_blocking: list[str] = []

    # OOS: accept explicit bool, else derive from retention if provided.
    if oos_robust is None and oos_retention is not None:
        oos_robust = oos_retention >= t.oos_min_retention
    if oos_robust is None:
        robust_blocking.append("no OOS evidence")
    elif oos_robust:
        d.passed.append("oos_robust")
    else:
        robust_blocking.append("oos_not_robust")

    if mc_profit_probability is None:
        robust_blocking.append("no Monte Carlo evidence")
    else:
        if mc_profit_probability >= t.mc_min_profit_probability:
            d.passed.append(f"mc_profit_prob>={t.mc_min_profit_probability} "
                            f"({mc_profit_probability:.2f})")
        else:
            robust_blocking.append(
                f"mc_profit_prob<{t.mc_min_profit_probability} "
                f"({mc_profit_probability:.2f})")
        if mc_ruin_probability is not None and \
                mc_ruin_probability > t.mc_max_ruin_probability:
            robust_blocking.append(
                f"mc_ruin_prob>{t.mc_max_ruin_probability} "
                f"({mc_ruin_probability:.2f})")

    if realism_warnings:
        robust_blocking.append(f"realism_warnings ({len(realism_warnings)})")
    if not concentration_ok:
        robust_blocking.append("fleet_concentration")
    if composite_score < t.paper_candidate_min_score:
        robust_blocking.append(
            f"score<{t.paper_candidate_min_score} ({composite_score:.3f})")

    if robust_blocking:
        d.notes.extend(robust_blocking)
        d.notes.append("capped at research_watchlist until robustness gates pass")
        return d

    d.recommended_state = LifecycleState.PAPER_CANDIDATE.value

    # ── Paper track record → demo_candidate (never live) ──────────────
    if paper_trades is not None:
        if paper_trades >= t.demo_candidate_min_paper_trades and \
                (paper_positive_expectancy is not False):
            d.passed.append(f"paper_trades>={t.demo_candidate_min_paper_trades} "
                            f"({paper_trades})")
            d.recommended_state = LifecycleState.DEMO_CANDIDATE.value
            d.requires_human_approval = True
            d.notes.append(
                "demo_candidate: live execution requires explicit per-bot human "
                "approval + Safety Governor sign-off (proposed_live/approved_live)")
        else:
            d.notes.append("insufficient/negative paper record for demo candidacy")

    return d
