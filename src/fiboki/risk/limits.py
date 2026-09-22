"""Limit definitions as VERSIONED DATA.

V1 rendered limits on a dashboard and enforced them nowhere
(``RiskEngine.check_trade_allowed`` had zero call sites in production code).
Worse, when a limit was edited there was no record of which limit set produced
a historical decision, so a report could never answer "was this trade allowed
under the rules in force at the time?".

V2 makes a limit set an immutable, named, fingerprinted value. A
:class:`~fiboki.core.contracts.RiskDecision` is always taken against exactly one
:class:`LimitSet`, and the execution record stores its ``version`` and
``fingerprint``. Editing limits means publishing a NEW version; the old one
stays registered so old decisions remain explicable.

Percentages are percentages (5.0 means 5%), fractions are fractions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any

__all__ = [
    "CONSERVATIVE_LIMITS",
    "DEFAULT_LIMITS",
    "LIMIT_SETS",
    "PAPER_LIMITS",
    "LimitSet",
    "get_limit_set",
    "register_limit_set",
]


@dataclass(frozen=True, slots=True)
class LimitSet:
    """One complete, named statement of what the book is allowed to do."""

    version: str

    # -- risk budget -------------------------------------------------------
    #: Risk (to stop) of a single trade, as a percentage of equity.
    max_per_trade_risk_pct: float = 1.0
    #: Sum of open risk-to-stop across the whole book, percentage of equity.
    max_account_risk_pct: float = 5.0

    # -- concentration -----------------------------------------------------
    # HOW THESE NUMBERS WERE CHOSEN -- read before tightening them.
    #
    # These are NOTIONAL limits as a percentage of equity, and on a leveraged FX
    # book they are legitimately several hundred percent. The arithmetic is
    # forced, not chosen: risking ``max_per_trade_risk_pct`` over a stop of
    # ``d`` percent of price requires a notional of ``risk_pct / d`` times
    # equity. At 1% risk and a 30-pip EURUSD stop (0.27% of price) that is 3.7x
    # equity FOR ONE TRADE. A 15-pip stop is 7.3x.
    #
    # So a per-instrument notional cap below ~8x would not be "conservative" --
    # it would silently forbid tight-stop trading while appearing to permit it,
    # which is the worst of both worlds. The caps below allow roughly two
    # tight-stop positions per instrument and are sized as BACKSTOPS against the
    # one thing a stop cannot protect against: a gap straight through it.
    #
    # The controls that actually bind day to day are ``max_per_trade_risk_pct``,
    # ``max_account_risk_pct`` and ``max_margin_utilisation_pct``. The last is
    # the true ceiling: at 50% utilisation on a 30:1 instrument, total notional
    # cannot exceed 15x equity however permissive these caps are.
    #: Gross notional in one instrument, percentage of equity.
    max_instrument_exposure_pct: float = 1000.0
    #: Gross notional attributable to one strategy, percentage of equity.
    max_strategy_exposure_pct: float = 1500.0
    #: Net notional in one currency leg, percentage of equity.
    max_currency_exposure_pct: float = 1500.0
    #: Gross notional across positions correlated above
    #: ``correlation_threshold``, percentage of equity.
    max_correlated_exposure_pct: float = 2000.0
    correlation_threshold: float = 0.60

    # -- loss limits -------------------------------------------------------
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 6.0
    max_total_drawdown_pct: float = 20.0

    # -- margin ------------------------------------------------------------
    max_margin_utilisation_pct: float = 50.0

    # -- market data / venue quality ---------------------------------------
    #: A quote older than this many seconds is not tradeable.
    max_price_age_seconds: float = 90.0
    #: A bar older than this many seconds means the feed is behind.
    max_data_age_seconds: float = 300.0
    #: Spread above this multiple of the instrument's typical spread is abnormal.
    max_spread_multiple: float = 3.0
    #: Minimum broker health score in [0, 1].
    min_broker_health: float = 0.50
    #: Minutes either side of a flagged economic event during which no new risk
    #: is taken.
    event_blackout_minutes: float = 15.0

    notes: str = ""

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("A LimitSet must be versioned; unnamed limits cannot be audited")
        negatives = {
            k: v
            for k, v in asdict(self).items()
            if isinstance(v, int | float) and not isinstance(v, bool) and v < 0
        }
        if negatives:
            raise ValueError(f"LimitSet {self.version}: negative limits {negatives}")
        if not 0.0 <= self.correlation_threshold <= 1.0:
            raise ValueError("correlation_threshold must be in [0, 1]")
        if not 0.0 <= self.min_broker_health <= 1.0:
            raise ValueError("min_broker_health must be in [0, 1]")
        if self.max_per_trade_risk_pct > self.max_account_risk_pct:
            raise ValueError(
                f"LimitSet {self.version}: max_per_trade_risk_pct "
                f"({self.max_per_trade_risk_pct}) exceeds max_account_risk_pct "
                f"({self.max_account_risk_pct}); one trade could breach the book limit"
            )
        if self.max_daily_loss_pct > self.max_weekly_loss_pct:
            raise ValueError(
                f"LimitSet {self.version}: daily loss limit exceeds the weekly limit"
            )
        if self.max_weekly_loss_pct > self.max_total_drawdown_pct:
            raise ValueError(
                f"LimitSet {self.version}: weekly loss limit exceeds total drawdown limit"
            )

    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        """Stable digest of the limit VALUES (notes excluded).

        Two limit sets with different versions but identical numbers share a
        fingerprint, which is the correct behaviour: it is the numbers that
        decided the trade.
        """
        payload = {k: v for k, v in sorted(self.as_dict().items()) if k not in ("version", "notes")}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]

    def stamp(self) -> dict[str, str]:
        """What gets written onto every decision record."""
        return {"limits_version": self.version, "limits_fingerprint": self.fingerprint()}

    def derive(self, version: str, **changes: Any) -> LimitSet:
        """Publish a NEW version. Limit sets are never mutated in place."""
        if version == self.version:
            raise ValueError(
                "A derived limit set must carry a new version, otherwise two "
                "different rule sets share one name and the audit trail lies."
            )
        return replace(self, version=version, **changes)


#: The production default for monitored paper and demo trading.
DEFAULT_LIMITS = LimitSet(
    version="limits_v1_default",
    notes=(
        "Baseline for monitored paper/demo. 1% per trade, 5% book risk, 3%/6% "
        "daily/weekly loss, 20% total drawdown, 50% margin utilisation. "
        "Notional caps are gap backstops, not the primary risk control -- see "
        "the note on the concentration fields for how they were derived."
    ),
)

#: Tighter set for the first live-adjacent period.
CONSERVATIVE_LIMITS = LimitSet(
    version="limits_v1_conservative",
    max_per_trade_risk_pct=0.5,
    max_account_risk_pct=2.5,
    max_instrument_exposure_pct=500.0,
    max_strategy_exposure_pct=750.0,
    max_currency_exposure_pct=750.0,
    max_correlated_exposure_pct=1000.0,
    max_daily_loss_pct=1.5,
    max_weekly_loss_pct=3.0,
    max_total_drawdown_pct=10.0,
    max_margin_utilisation_pct=25.0,
    max_spread_multiple=2.0,
    min_broker_health=0.70,
    event_blackout_minutes=30.0,
    notes="For the first monitored period on a real venue. Half the default risk.",
)

#: Looser data-quality tolerances for paper mode, where a slightly stale quote
#: costs nothing real. Risk limits are deliberately NOT loosened.
PAPER_LIMITS = LimitSet(
    version="limits_v1_paper",
    max_price_age_seconds=600.0,
    max_data_age_seconds=1800.0,
    max_spread_multiple=5.0,
    min_broker_health=0.10,
    notes=(
        "Paper mode. Data-quality tolerances relaxed because a stale paper quote "
        "costs nothing; every risk limit is identical to the default set so "
        "paper and live risk behaviour stay comparable."
    ),
)


LIMIT_SETS: dict[str, LimitSet] = {
    s.version: s for s in (DEFAULT_LIMITS, CONSERVATIVE_LIMITS, PAPER_LIMITS)
}


def register_limit_set(limits: LimitSet, *, overwrite: bool = False) -> LimitSet:
    """Register a limit set under its version. Refuses silent redefinition."""
    existing = LIMIT_SETS.get(limits.version)
    if existing is not None and not overwrite and existing != limits:
        raise ValueError(
            f"Limit set {limits.version!r} is already registered with different "
            "values. Publish a new version instead of redefining an old one; "
            "historical decisions reference this name."
        )
    LIMIT_SETS[limits.version] = limits
    return limits


def get_limit_set(version: str) -> LimitSet:
    if version not in LIMIT_SETS:
        raise KeyError(
            f"Unknown limit set {version!r}. Known: {sorted(LIMIT_SETS)}. "
            "V2 will not fall back to a default limit set: a decision taken "
            "under unknown limits is not an auditable decision."
        )
    return LIMIT_SETS[version]
