"""THE single sizing authority.

V1 sized the same signal three times: the paper bot sized off fleet equity, the
router re-sized off allocated capital, and the IG adapter re-sized off the live
broker balance. Three answers, one position, and an internal ledger that
disagreed with the broker *by construction* -- no bug was required.

V2 has exactly one function that turns a :class:`~fiboki.core.contracts.Signal`
into a size: :func:`size_trade`. It returns a
:class:`~fiboki.core.contracts.TradePlan`, and a TradePlan's size is final.
Everything downstream -- the risk gateway, the execution service, every broker
adapter -- may *refuse* a plan, but may never re-derive its size. Adapters
convert units (lots, contracts, venue-specific granularity) and nothing else.

``tests/unit/test_sizing_authority.py`` asserts the number survives unchanged
from :func:`size_trade` all the way to the ``Order`` handed to a venue.

The rule (``fixed_fractional_v2``, the default)
------------------------------------------------
    risk_account        = equity * risk_fraction * portfolio_weight
    cost_per_unit       = spread + 2 * E[slippage]          (price units)
    risk_per_unit       = (stop_distance + cost_per_unit) * contract_size * fx(quote->account)
    size                = risk_account / risk_per_unit
    size                = min(size, equity * leverage / notional_per_unit)
    size                = round_DOWN_to_step(size)

``cost_per_unit`` is :func:`fiboki.backtest.engine.stop_out_cost_per_unit`: the
spread paid across both legs plus the expected slippage of the market entry and
the stop exit, from the policy's cost profile. It exists because a position
stopped exactly at its level loses the stop distance PLUS those costs, so v1
(which divided by the bare stop distance) risked more than its stated fraction
on every trade -- about 30% more on a 5-pip EURUSD stop under IG_REALISTIC.
``fixed_fractional_v1`` is kept, selectable by ``policy_id``, so a plan made
under it stays explicable; ``TradePlan.sizing_basis`` names the rule.

``TradePlan.risk_amount`` is the risk AT THE STOP INCLUDING those costs under
v2, which is exactly what the risk gateway's ``max_per_trade_risk`` and
``max_account_risk`` checks compare against the limit, so the gateway and the
sizer agree on what "1% at risk" means.

Rounding is always DOWN (:func:`fiboki.core.money.round_size`). Rounding up
would manufacture risk the rule did not intend, and "one step" is a material
amount on an instrument whose step is 1.0 units.

Honest limitation: ``risk_fraction`` is the fraction of equity lost *if the stop
fills exactly at its level*. Gaps exceed it. This is a sizing intent, not a loss
guarantee, and the realised MAE distribution is where you find out how often the
intent failed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fiboki.backtest.engine import (
    DEFAULT_SIZING_COST_PROFILE,
    SIZING_POLICIES,
    SIZING_POLICY_V1,
    SIZING_POLICY_V2,
    stop_out_cost_per_unit,
)
from fiboki.core.contracts import AccountState, Signal, TradePlan
from fiboki.core.instruments import Instrument
from fiboki.core.money import round_size
from fiboki.sim.profiles import ExecutionProfile

__all__ = [
    "DEFAULT_SIZING_COST_PROFILE",
    "SIZING_POLICY_V1",
    "SIZING_POLICY_V2",
    "PortfolioSizer",
    "SizingOutcome",
    "SizingPolicy",
    "SizingRejection",
    "size_trade",
]


class SizingRejection(str):
    """A named reason a signal could not be sized. Never a silent zero."""

    NON_POSITIVE_EQUITY = "non_positive_equity"
    NON_POSITIVE_STOP_DISTANCE = "non_positive_stop_distance"
    NON_POSITIVE_RISK_BUDGET = "non_positive_risk_budget"
    NON_POSITIVE_RISK_PER_UNIT = "non_positive_risk_per_unit"
    BELOW_MIN_SIZE = "below_min_size"
    ROUNDED_TO_ZERO = "rounded_to_zero"
    INVALID_FX_RATE = "invalid_fx_rate"
    ZERO_PORTFOLIO_WEIGHT = "zero_portfolio_weight"


@dataclass(frozen=True, slots=True)
class SizingPolicy:
    """The risk rule. Versioned so a stored plan can name the rule that made it."""

    risk_fraction: float = 0.01
    #: ``None`` means "use the instrument's registered retail leverage cap".
    max_leverage: float | None = None
    policy_id: str = SIZING_POLICY_V2
    #: The frictions a v2 policy prices its stop-out with. ``None`` means
    #: :data:`~fiboki.backtest.engine.DEFAULT_SIZING_COST_PROFILE`
    #: (IG_REALISTIC), the same default the backtest sizer uses, so paper and
    #: backtest agree unless someone names a profile. Ignored by v1.
    cost_profile: ExecutionProfile | None = None

    def __post_init__(self) -> None:
        if self.policy_id not in SIZING_POLICIES:
            raise ValueError(
                f"unknown sizing policy {self.policy_id!r}; known {SIZING_POLICIES}. "
                "A stored plan names its rule, so an unnamed rule cannot be audited."
            )
        if not 0.0 < self.risk_fraction <= 1.0:
            raise ValueError(
                f"risk_fraction must be in (0, 1], got {self.risk_fraction}. "
                "A sizing rule that can risk more than the account is not a rule."
            )
        if self.max_leverage is not None and self.max_leverage <= 0:
            raise ValueError("max_leverage must be positive when set")

    @property
    def resolved_cost_profile(self) -> ExecutionProfile | None:
        """The profile v2 prices costs with; ``None`` under v1 (no costs priced)."""
        if self.policy_id == SIZING_POLICY_V1:
            return None
        return self.cost_profile or DEFAULT_SIZING_COST_PROFILE

    def cost_per_unit(self, instrument: Instrument, signal: Signal) -> float:
        """Price units added to the stop distance: 0 under v1."""
        profile = self.resolved_cost_profile
        if profile is None:
            return 0.0
        return stop_out_cost_per_unit(profile, instrument, signal)

    @property
    def basis(self) -> str:
        """What ``TradePlan.sizing_basis`` records: the rule and, under v2, its costs."""
        profile = self.resolved_cost_profile
        return self.policy_id if profile is None else f"{self.policy_id}:{profile.name}"

    def fingerprint(self) -> dict[str, Any]:
        profile = self.resolved_cost_profile
        return {
            "policy_id": self.policy_id,
            "risk_fraction": self.risk_fraction,
            "max_leverage": self.max_leverage,
            "cost_profile": profile.name if profile is not None else None,
            "cost_profile_defaulted": (
                profile is not None and self.cost_profile is None
            ),
        }

    def leverage_for(self, instrument: Instrument) -> float:
        """Never exceed the instrument's registered retail cap, whatever is asked.

        A caller may request *less* leverage than the venue permits. It may not
        request more: the cap is a regulatory fact about the instrument, not a
        strategy preference.
        """
        if self.max_leverage is None:
            return instrument.retail_leverage
        return min(self.max_leverage, instrument.retail_leverage)


@dataclass(frozen=True, slots=True)
class SizingOutcome:
    """Either a plan, or a named reason there is no plan. Never both, never neither."""

    plan: TradePlan | None
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.reason is None):
            raise ValueError("SizingOutcome carries exactly one of plan or reason")

    @property
    def sized(self) -> bool:
        return self.plan is not None

    def require(self) -> TradePlan:
        if self.plan is None:
            raise ValueError(f"Signal was not sized: {self.reason} ({self.detail})")
        return self.plan


def _snap_to_step(size: float, step: float, rel_tol: float = 1e-9) -> float:
    """Absorb IEEE-754 representation error before flooring to the size step.

    ``1000 / (0.0050 * 0.80)`` is 249999.99999999997, not 250000. Flooring that
    loses a unit of exposure for a reason with no economic content -- and the
    loss would depend on the exact FX rate, so the same rule would answer
    differently on different days. The adjustment is at most one part in a
    billion, orders of magnitude below any instrument's step, so it cannot
    manufacture risk.

    Deliberately identical in behaviour to the backtest engine's private helper;
    ``tests/unit/test_sizing_authority.py`` pins the two together so the paper
    and backtest ledgers cannot drift apart on a rounding edge.
    """
    if step <= 0 or size <= 0:
        return size
    nearest = round(size / step)
    if nearest > 0 and abs(size - nearest * step) <= rel_tol * max(size, step):
        return nearest * step
    return size


def size_trade(
    *,
    signal: Signal,
    instrument: Instrument,
    account: AccountState,
    fx_quote_to_account: float,
    policy: SizingPolicy,
    portfolio_weight: float = 1.0,
    account_ccy: str | None = None,
) -> SizingOutcome:
    """Compute the size ONCE. The returned plan's size is final and authoritative.

    Parameters
    ----------
    fx_quote_to_account:
        Rate converting the instrument's QUOTE currency into the account
        currency at decision time. Supplied by the caller from an explicit
        :class:`~fiboki.core.money.FxRateSource`; this function will not invent
        one, because inventing 1.0 is exactly how V1 mis-stated every
        USD-quoted result on a GBP account.
    portfolio_weight:
        The fraction of the per-trade risk budget this signal was allocated by
        :mod:`fiboki.portfolio.construction`. 1.0 means "the full budget".
    """
    ccy = account_ccy or account.currency
    detail: dict[str, Any] = {
        "policy_id": policy.policy_id,
        "sizing_basis": policy.basis,
        "risk_fraction": policy.risk_fraction,
        "portfolio_weight": portfolio_weight,
        "equity": account.equity,
        "fx_quote_to_account": fx_quote_to_account,
        "instrument": instrument.symbol,
    }

    if account.equity <= 0:
        return SizingOutcome(None, SizingRejection.NON_POSITIVE_EQUITY, detail)
    if not (fx_quote_to_account > 0) or fx_quote_to_account != fx_quote_to_account:
        return SizingOutcome(None, SizingRejection.INVALID_FX_RATE, detail)
    if portfolio_weight <= 0:
        return SizingOutcome(None, SizingRejection.ZERO_PORTFOLIO_WEIGHT, detail)

    stop_distance = signal.stop_distance
    detail["stop_distance"] = stop_distance
    if stop_distance <= 0:
        return SizingOutcome(None, SizingRejection.NON_POSITIVE_STOP_DISTANCE, detail)

    risk_account = account.equity * policy.risk_fraction * portfolio_weight
    detail["risk_budget_account"] = risk_account
    if risk_account <= 0:
        return SizingOutcome(None, SizingRejection.NON_POSITIVE_RISK_BUDGET, detail)

    cost_per_unit = policy.cost_per_unit(instrument, signal)
    detail["cost_per_unit_price"] = cost_per_unit
    per_unit_price = stop_distance + cost_per_unit
    risk_per_unit = per_unit_price * instrument.contract_size * fx_quote_to_account
    detail["risk_per_unit_account"] = risk_per_unit
    if risk_per_unit <= 0:
        return SizingOutcome(None, SizingRejection.NON_POSITIVE_RISK_PER_UNIT, detail)

    raw = risk_account / risk_per_unit
    detail["size_from_risk"] = raw

    leverage = policy.leverage_for(instrument)
    detail["leverage_cap"] = leverage
    notional_per_unit = (
        signal.reference_price * instrument.contract_size * fx_quote_to_account
    )
    detail["notional_per_unit_account"] = notional_per_unit
    leverage_capped = raw
    if notional_per_unit > 0:
        leverage_capped = min(raw, account.equity * leverage / notional_per_unit)
    detail["size_after_leverage_cap"] = leverage_capped
    detail["leverage_binding"] = leverage_capped < raw

    size = round_size(instrument, _snap_to_step(leverage_capped, instrument.size_step))
    detail["size_final"] = size

    if size <= 0:
        return SizingOutcome(None, SizingRejection.ROUNDED_TO_ZERO, detail)
    if size < instrument.min_size:
        detail["min_size"] = instrument.min_size
        return SizingOutcome(None, SizingRejection.BELOW_MIN_SIZE, detail)

    # Recomputed from the ROUNDED size so the recorded risk is the risk actually
    # taken, not the risk the unrounded arithmetic wanted to take. Under v2 it
    # includes the expected stop-out costs, which is what the gateway's
    # per-trade and account risk checks then compare against their limits.
    realised_risk = per_unit_price * size * instrument.contract_size * fx_quote_to_account
    detail["risk_amount_account"] = realised_risk

    plan = TradePlan(
        signal=signal,
        size=size,
        account_ccy=ccy,
        risk_amount=realised_risk,
        sizing_basis=policy.basis,
        max_leverage_applied=leverage,
        portfolio_weight=portfolio_weight,
    )
    return SizingOutcome(plan, None, detail)


@dataclass(frozen=True, slots=True)
class PortfolioSizer:
    """Adapts :func:`size_trade` to the backtest engine's ``Sizer`` protocol.

    This exists so that backtest, paper and live all route through the same
    arithmetic. If you find yourself writing a second sizer, you are rebuilding
    the V1 failure.
    """

    policy: SizingPolicy = field(default_factory=SizingPolicy)
    portfolio_weight: float = 1.0

    def fingerprint(self) -> dict[str, Any]:
        return {
            "sizer": type(self).__name__,
            **self.policy.fingerprint(),
            "portfolio_weight": self.portfolio_weight,
        }

    def size_for(
        self,
        signal: Signal,
        instrument: Instrument,
        account: AccountState,
        fx_quote_to_account: float,
    ) -> float:
        outcome = size_trade(
            signal=signal,
            instrument=instrument,
            account=account,
            fx_quote_to_account=fx_quote_to_account,
            policy=self.policy,
            portfolio_weight=self.portfolio_weight,
        )
        return outcome.plan.size if outcome.plan is not None else 0.0
