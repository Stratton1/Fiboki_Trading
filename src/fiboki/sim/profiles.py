"""Broker execution profiles — cost and friction models expressed as DATA.

Why this module exists
----------------------
V1 hardcoded ``spread = instrument.typical_spread_pips`` inside the backtest
loop and ``commission = 0.0`` inside the paper bot. Changing an assumption
meant editing engine code, so nobody ever measured how sensitive a result was
to its own cost assumptions. Every "edge" V1 reported was an edge *at one
un-named, un-versioned set of frictions*.

In V2 a profile is a frozen dataclass tree of small strategy objects. The
engine never asks "what is the spread for EURUSD" — it asks the profile. A
research run is therefore always stamped with the exact friction assumptions
that produced it, and swapping IDEALISED_RESEARCH for SEVERE_STRESS is a
one-line change that touches no strategy and no engine code.

Units and currency conventions (read before adding a model)
-----------------------------------------------------------
* Spread and slippage models return **price units** (not pips), because the
  fill model adds them directly to a price.
* Commission and financing models return an amount in an explicitly named
  currency (``"quote"`` means the instrument's quote currency). The engine
  converts to the account currency through ``fiboki.core.money``. There is no
  implicit 1.0 anywhere.
* Every stochastic model takes an explicit ``numpy.random.Generator``. Models
  never create their own RNG, so reproducibility is the caller's contract and
  cannot be broken by import order or by dictionary iteration order.

Determinism
-----------
``rng_for(seed, bar_index, sequence)`` derives an independent generator from a
counter-based seed triple. Two runs with the same seed produce the same draws
*regardless of the order in which orders are evaluated*, which means adding a
second instrument to a portfolio does not silently change the fills of the
first. This is stronger than a single shared stream and is the property
``tests/unit/test_engine_determinism.py`` pins down.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol, runtime_checkable

import numpy as np

from fiboki.core.enums import Direction
from fiboki.core.instruments import Instrument

#: The shape of :meth:`ExecutionProfile.fingerprint`. Stored on every backtest
#: record and then compared, so it needs to say which generation of this class
#: described it -- otherwise a reader cannot tell a different cost model from the
#: same cost model described differently. Bump it when a field is added, removed or
#: reinterpreted. See :mod:`fiboki.core.versioned_key`.
#: ``profile_v2``: the minimum stop distance became per asset class
#: (``min_stop_by_asset_class``), so a v1 fingerprint cannot describe a v2 profile.
PROFILE_FINGERPRINT_VERSION = "profile_v2"

__all__ = [
    "IBKR_REALISTIC",
    "IDEALISED_RESEARCH",
    "IG_REALISTIC",
    "OANDA_REALISTIC",
    "PROFILES",
    "SEVERE_STRESS",
    "CommissionModel",
    "ExecutionProfile",
    "FinancingModel",
    "FixedPipSpread",
    "FixedPointsSlippage",
    "MinStopPolicy",
    "MinStopRule",
    "NoSlippage",
    "ProbabilisticAdverseTickSlippage",
    "SlippageModel",
    "SpreadModel",
    "TimeOfDayWideningSpread",
    "TypicalMultiplierSpread",
    "expected_slippage_price",
    "get_profile",
    "rng_for",
]


# --------------------------------------------------------------------------
# Deterministic RNG derivation
# --------------------------------------------------------------------------


def rng_for(seed: int, bar_index: int, sequence: int = 0) -> np.random.Generator:
    """An independent generator keyed by (seed, bar_index, sequence).

    Counter-based rather than stream-based: the draw for bar 900 order 2 is
    identical whether or not bar 400 drew anything. That is what makes a
    portfolio backtest reproducible when instruments are added or removed.
    """
    return np.random.default_rng([int(seed), int(bar_index), int(sequence)])


# --------------------------------------------------------------------------
# Spread models
# --------------------------------------------------------------------------


@runtime_checkable
class SpreadModel(Protocol):
    """Returns the FULL spread in price units. The fill model halves it."""

    def spread_price(self, instrument: Instrument, hour_utc: int, mid_price: float) -> float: ...


@dataclass(frozen=True, slots=True)
class FixedPipSpread:
    """A constant spread in pips, regardless of instrument or time."""

    pips: float

    def spread_price(self, instrument: Instrument, hour_utc: int, mid_price: float) -> float:
        return self.pips * instrument.pip_size


@dataclass(frozen=True, slots=True)
class TypicalMultiplierSpread:
    """A multiple of the instrument's registered typical spread.

    This is the honest default: the registry already records a per-instrument
    typical spread as a market fact, and a broker profile is mostly a statement
    about how much worse (or better) than typical that broker is.
    """

    multiplier: float = 1.0
    floor_pips: float = 0.0

    def spread_price(self, instrument: Instrument, hour_utc: int, mid_price: float) -> float:
        pips = max(instrument.typical_spread_pips * self.multiplier, self.floor_pips)
        return pips * instrument.pip_size


@dataclass(frozen=True, slots=True)
class TimeOfDayWideningSpread:
    """Wraps another spread model and widens it inside UTC hour windows.

    ``windows`` is a tuple of ``(start_hour, end_hour, multiplier)`` with
    half-open ``[start, end)`` semantics; a window that wraps midnight (e.g.
    21->2) is expressed with ``start > end`` and matched accordingly. The first
    matching window wins, so windows are evaluated in declaration order and the
    result does not depend on dict ordering.
    """

    base: SpreadModel
    windows: tuple[tuple[int, int, float], ...] = ()

    def spread_price(self, instrument: Instrument, hour_utc: int, mid_price: float) -> float:
        base = self.base.spread_price(instrument, hour_utc, mid_price)
        for start, end, mult in self.windows:
            inside = start <= hour_utc < end if start < end else (hour_utc >= start or hour_utc < end)
            if inside:
                return base * mult
        return base


# --------------------------------------------------------------------------
# Slippage models
# --------------------------------------------------------------------------


@runtime_checkable
class SlippageModel(Protocol):
    """Returns ADVERSE slippage in price units (always >= 0).

    The fill model applies the sign: a long pays more, a short receives less.
    A model that could return a negative number would be modelling price
    improvement, which no honest research profile should assume.
    """

    def slippage_price(
        self,
        instrument: Instrument,
        direction: Direction,
        mid_price: float,
        rng: np.random.Generator,
    ) -> float: ...


@dataclass(frozen=True, slots=True)
class NoSlippage:
    """Zero slippage. Only defensible in IDEALISED_RESEARCH as a baseline."""

    def slippage_price(
        self,
        instrument: Instrument,
        direction: Direction,
        mid_price: float,
        rng: np.random.Generator,
    ) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class FixedPointsSlippage:
    """A deterministic adverse amount in pips on every fill."""

    pips: float

    def __post_init__(self) -> None:
        if self.pips < 0:
            raise ValueError("Slippage pips must be >= 0; negative means price improvement")

    def slippage_price(
        self,
        instrument: Instrument,
        direction: Direction,
        mid_price: float,
        rng: np.random.Generator,
    ) -> float:
        return self.pips * instrument.pip_size


@dataclass(frozen=True, slots=True)
class ProbabilisticAdverseTickSlippage:
    """With probability ``probability``, slip by 1..``max_ticks`` adverse ticks.

    Draws exactly two uniforms in a fixed order so the consumed entropy is
    constant per call; this keeps a derived generator's state predictable and
    makes the model auditable from a test.
    """

    probability: float = 0.35
    tick_pips: float = 0.5
    max_ticks: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be in [0, 1]")
        if self.max_ticks < 1:
            raise ValueError("max_ticks must be >= 1")
        if self.tick_pips < 0:
            raise ValueError("tick_pips must be >= 0")

    def slippage_price(
        self,
        instrument: Instrument,
        direction: Direction,
        mid_price: float,
        rng: np.random.Generator,
    ) -> float:
        draw = float(rng.random())
        magnitude = int(rng.integers(1, self.max_ticks + 1))
        if draw >= self.probability:
            return 0.0
        return magnitude * self.tick_pips * instrument.pip_size


def expected_slippage_price(model: SlippageModel, instrument: Instrument) -> float:
    """The EXPECTED adverse slippage of one fill under ``model``, in price units.

    Used by sizing (``fixed_fractional_v2``) to price the stop-out it is sizing
    against. Computed from the model's own parameters, never sampled, so the
    size a signal gets does not depend on a random draw:

    * :class:`NoSlippage` -> 0;
    * :class:`FixedPointsSlippage` -> ``pips * pip_size``;
    * :class:`ProbabilisticAdverseTickSlippage` ->
      ``probability * mean(1..max_ticks) * tick_pips * pip_size``, i.e.
      ``p * (max_ticks + 1) / 2 * tick_pips * pip_size``. For IG_REALISTIC that
      is ``0.30 * 2 * 0.4 = 0.24`` pips.

    An unknown model raises: guessing its mean would be inventing a cost.
    """
    if isinstance(model, NoSlippage):
        return 0.0
    if isinstance(model, FixedPointsSlippage):
        return model.pips * instrument.pip_size
    if isinstance(model, ProbabilisticAdverseTickSlippage):
        mean_ticks = (model.max_ticks + 1) / 2.0
        return model.probability * mean_ticks * model.tick_pips * instrument.pip_size
    raise TypeError(
        f"no expected value is defined for slippage model {type(model).__name__}; "
        "add one here rather than letting sizing assume zero"
    )


# --------------------------------------------------------------------------
# Commission
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommissionModel:
    """Per-leg commission. All three components are summed, then floored.

    * ``per_trade`` — a flat ticket charge per leg.
    * ``per_unit`` — charged per unit of size (IBKR-style per-share/per-lot).
    * ``bps_of_notional`` — basis points of ``price * size * contract_size``.
    * ``minimum`` — a floor applied to the summed amount when size > 0.

    ``currency`` is either the literal ``"quote"`` (the instrument's quote
    currency) or an ISO code. The engine converts; this model never does, so a
    commission schedule denominated in USD stays denominated in USD instead of
    being quietly renamed to the account currency the way V1 did.
    """

    per_trade: float = 0.0
    per_unit: float = 0.0
    bps_of_notional: float = 0.0
    minimum: float = 0.0
    currency: str = "quote"

    def commission(self, instrument: Instrument, size: float, price: float) -> float:
        if size <= 0:
            return 0.0
        notional = abs(price) * size * instrument.contract_size
        amount = (
            self.per_trade
            + self.per_unit * size
            + self.bps_of_notional * 1e-4 * notional
        )
        return max(amount, self.minimum)

    def currency_for(self, instrument: Instrument) -> str:
        return instrument.quote if self.currency == "quote" else self.currency.upper()


# --------------------------------------------------------------------------
# Financing / swap
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FinancingModel:
    """Overnight financing charged per night the position is held.

    Modelled as an annual rate in basis points applied to notional, divided by
    ``basis_days``. Longs and shorts differ: a retail CFD long typically pays
    benchmark + markup while a short receives benchmark - markup, and with
    benchmark rates below the markup the short pays too. A NEGATIVE bps figure
    means the position EARNS financing.

    Known approximation, stated rather than hidden: the rate is static over the
    whole sample. Real 2000-2025 financing swung from ~0 to ~5.5% and that
    matters for multi-week holds. A term-structure-aware model belongs here,
    not in the engine, when we have the rate history.
    """

    annual_bps_long: float = 0.0
    annual_bps_short: float = 0.0
    basis_days: float = 365.0
    currency: str = "quote"

    def nightly_charge(
        self, instrument: Instrument, direction: Direction, size: float, price: float
    ) -> float:
        """Positive = a cost to the position holder."""
        if size <= 0:
            return 0.0
        notional = abs(price) * size * instrument.contract_size
        bps = self.annual_bps_long if direction is Direction.LONG else self.annual_bps_short
        return notional * (bps * 1e-4) / self.basis_days

    def currency_for(self, instrument: Instrument) -> str:
        return instrument.quote if self.currency == "quote" else self.currency.upper()


# --------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------


class MinStopPolicy(str, Enum):
    """What to do when a signal's stop sits inside the broker's minimum distance."""

    REJECT = "reject"       # broker-accurate: IG rejects the ticket outright
    WIDEN = "widen"         # trader-accurate: push the stop out to the minimum
    ALLOW = "allow"         # research-only: pretend the constraint is not there


@dataclass(frozen=True, slots=True)
class MinStopRule:
    """A venue's minimum stop distance for one asset class.

    ``max(floor_pips, typical_spread_multiple * typical_spread_pips)`` in the
    instrument's pips. A flat pip figure is only meaningful within one asset
    class: 4 pips is 0.0004 on EURUSD but $0.04 on XAUUSD and 4 points on
    JP225, where the typical spread alone is 7. Expressing the non-FX minimum as
    a multiple of the registered typical spread keeps it on the instrument's own
    price scale.

    Known approximation: these are per-class rules, not per-instrument venue
    specifications. The measured replacement belongs to E-4 (the quote
    recorder), and until then the numbers are stated here, not hidden.
    """

    floor_pips: float = 0.0
    typical_spread_multiple: float = 0.0

    def __post_init__(self) -> None:
        if self.floor_pips < 0 or self.typical_spread_multiple < 0:
            raise ValueError("a minimum stop rule cannot be negative")

    def distance_pips(self, instrument: Instrument) -> float:
        return max(
            self.floor_pips, self.typical_spread_multiple * instrument.typical_spread_pips
        )


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """A complete, named, versioned statement of execution friction."""

    name: str
    spread: SpreadModel
    slippage: SlippageModel
    commission: CommissionModel
    financing: FinancingModel

    min_deal_size: float = 0.0
    #: Flat minimum stop in pips, used only when ``min_stop_by_asset_class`` is
    #: empty. Retained for the profiles that have no minimum (0.0).
    min_stop_distance_pips: float = 0.0
    #: ``(asset_class value, rule)`` pairs. When set, it is authoritative and an
    #: asset class it does not name RAISES rather than falling back to the flat
    #: figure, because a guessed minimum stop is a guessed rejection rate.
    min_stop_by_asset_class: tuple[tuple[str, MinStopRule], ...] = ()
    min_stop_policy: MinStopPolicy = MinStopPolicy.REJECT
    guaranteed_stop_premium_pips: float = 0.0
    use_guaranteed_stops: bool = False

    partial_fill_probability: float = 0.0
    partial_fill_min_fraction: float = 1.0
    rejection_probability: float = 0.0

    latency_bars: int = 0
    stale_price_max_gap_multiple: float = 3.0

    seed: int = 20260919
    notes: str = ""

    def __post_init__(self) -> None:
        for name_, v in (
            ("partial_fill_probability", self.partial_fill_probability),
            ("rejection_probability", self.rejection_probability),
        ):
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name_} must be in [0, 1], got {v}")
        if not 0.0 < self.partial_fill_min_fraction <= 1.0:
            raise ValueError("partial_fill_min_fraction must be in (0, 1]")
        if self.latency_bars < 0:
            raise ValueError("latency_bars must be >= 0")
        if self.stale_price_max_gap_multiple <= 1.0:
            raise ValueError(
                "stale_price_max_gap_multiple must exceed 1.0; a value of 1.0 would "
                "flag every normal bar as stale"
            )

    # -- derived helpers -------------------------------------------------

    def spread_price(self, instrument: Instrument, hour_utc: int, mid_price: float) -> float:
        s = self.spread.spread_price(instrument, hour_utc, mid_price)
        if s < 0:
            raise ValueError(f"Profile {self.name} produced a negative spread {s}")
        return s

    def half_spread_price(self, instrument: Instrument, hour_utc: int, mid_price: float) -> float:
        return self.spread_price(instrument, hour_utc, mid_price) / 2.0

    def min_stop_distance_price(self, instrument: Instrument) -> float:
        if self.min_stop_by_asset_class:
            rules = dict(self.min_stop_by_asset_class)
            key = instrument.asset_class.value
            if key not in rules:
                raise KeyError(
                    f"profile {self.name} declares per-class minimum stops but none "
                    f"for {key!r} ({instrument.symbol}); add one rather than guess"
                )
            return rules[key].distance_pips(instrument) * instrument.pip_size
        return self.min_stop_distance_pips * instrument.pip_size

    def expected_slippage_price(self, instrument: Instrument) -> float:
        """Expected adverse slippage of ONE fill. See :func:`expected_slippage_price`."""
        return expected_slippage_price(self.slippage, instrument)

    def guaranteed_stop_premium_price(self, instrument: Instrument) -> float:
        return self.guaranteed_stop_premium_pips * instrument.pip_size

    def with_seed(self, seed: int) -> ExecutionProfile:
        return replace(self, seed=seed)

    def fingerprint(self) -> dict[str, object]:
        """A sortable, JSON-able description used to stamp a result.

        Carries ``key_version`` because it is PERSISTED and then compared: the
        fingerprint's SHAPE is a function of this class, so without the stamp a
        reader cannot tell a different configuration from the same configuration
        described by a different generation of the code. The stamp is
        :data:`PROFILE_FINGERPRINT_VERSION`, owned by this module: the
        profile's fingerprint shape is a function of this class and can change
        without the backtest engine changing. See :mod:`fiboki.core.versioned_key`.
        """
        return {
            "key_version": PROFILE_FINGERPRINT_VERSION,
            "name": self.name,
            "spread": repr(self.spread),
            "slippage": repr(self.slippage),
            "commission": repr(self.commission),
            "financing": repr(self.financing),
            "min_deal_size": self.min_deal_size,
            "min_stop_distance_pips": self.min_stop_distance_pips,
            "min_stop_by_asset_class": [
                [cls, rule.floor_pips, rule.typical_spread_multiple]
                for cls, rule in self.min_stop_by_asset_class
            ],
            "min_stop_policy": self.min_stop_policy.value,
            "guaranteed_stop_premium_pips": self.guaranteed_stop_premium_pips,
            "use_guaranteed_stops": self.use_guaranteed_stops,
            "partial_fill_probability": self.partial_fill_probability,
            "partial_fill_min_fraction": self.partial_fill_min_fraction,
            "rejection_probability": self.rejection_probability,
            "latency_bars": self.latency_bars,
            "stale_price_max_gap_multiple": self.stale_price_max_gap_multiple,
            "seed": self.seed,
        }


# --------------------------------------------------------------------------
# The canonical profiles
# --------------------------------------------------------------------------

def _per_class_min_stop(
    *, fx_floor_pips: float, spread_multiple: float
) -> tuple[tuple[str, MinStopRule], ...]:
    """The per-asset-class minimum stop table a spread-bet/CFD profile uses.

    FX keeps its flat pip floor, so an FX result is unchanged by the move from a
    single flat figure. Every other class is a multiple of the instrument's own
    typical spread (IG: XAUUSD 3 x 30 pips = $0.90, US500 3 x 0.4 = 1.2 points,
    JP225 3 x 7 = 21 points, WTI 3 x 3 = 9 cents).
    """
    fx = MinStopRule(floor_pips=fx_floor_pips)
    other = MinStopRule(typical_spread_multiple=spread_multiple)
    return tuple(
        sorted(
            {
                "fx_major": fx,
                "fx_cross": fx,
                "metal": other,
                "index": other,
                "energy": other,
                "equity": other,
                "crypto": other,
            }.items()
        )
    )


#: Frictionless. Its ONLY legitimate use is as the numerator of a cost-impact
#: ratio: "this strategy keeps 38% of its idealised profit under IG_REALISTIC".
#: A result quoted from this profile alone is not a result.
IDEALISED_RESEARCH = ExecutionProfile(
    name="IDEALISED_RESEARCH",
    spread=FixedPipSpread(0.0),
    slippage=NoSlippage(),
    commission=CommissionModel(),
    financing=FinancingModel(),
    min_deal_size=0.0,
    min_stop_distance_pips=0.0,
    min_stop_policy=MinStopPolicy.ALLOW,
    latency_bars=0,
    notes=(
        "Zero friction baseline. Not a tradeable assumption. Use only to measure "
        "how much of an edge the frictions eat."
    ),
)

#: IG UK spread-bet / CFD retail. Spread-only pricing on FX and metals (no
#: commission), a real minimum stop distance, and financing at roughly the
#: instrument's registered annual bps with a long/short markup split.
IG_REALISTIC = ExecutionProfile(
    name="IG_REALISTIC",
    spread=TimeOfDayWideningSpread(
        base=TypicalMultiplierSpread(multiplier=1.0, floor_pips=0.6),
        windows=(
            (21, 23, 3.0),   # rollover: IG spreads blow out around 21:00-23:00 UTC
            (23, 6, 1.8),    # thin Asian liquidity
        ),
    ),
    slippage=ProbabilisticAdverseTickSlippage(probability=0.30, tick_pips=0.4, max_ticks=3),
    commission=CommissionModel(),  # FX/metal CFDs are spread-only at IG
    financing=FinancingModel(annual_bps_long=290.0, annual_bps_short=110.0, basis_days=365.0),
    min_deal_size=0.0,
    min_stop_distance_pips=4.0,
    min_stop_by_asset_class=_per_class_min_stop(fx_floor_pips=4.0, spread_multiple=3.0),
    min_stop_policy=MinStopPolicy.REJECT,
    guaranteed_stop_premium_pips=3.0,
    use_guaranteed_stops=False,
    partial_fill_probability=0.0,   # retail CFD tickets are all-or-nothing
    rejection_probability=0.005,
    latency_bars=0,
    seed=20260919,
    notes=(
        "IG UK retail CFD. Spread-only, minimum stop 4 pips on FX and 3x the "
        "typical spread elsewhere, rollover "
        "widening at 21:00-23:00 UTC. Financing is static over the sample — a "
        "known approximation, see FinancingModel."
    ),
)

#: OANDA-style: tighter typical spreads, no minimum stop distance, no
#: commission on the core pricing model, financing closer to interbank.
OANDA_REALISTIC = ExecutionProfile(
    name="OANDA_REALISTIC",
    spread=TimeOfDayWideningSpread(
        base=TypicalMultiplierSpread(multiplier=0.85, floor_pips=0.4),
        windows=((21, 23, 2.5), (23, 6, 1.5)),
    ),
    slippage=ProbabilisticAdverseTickSlippage(probability=0.25, tick_pips=0.3, max_ticks=3),
    commission=CommissionModel(),
    financing=FinancingModel(annual_bps_long=250.0, annual_bps_short=90.0, basis_days=365.0),
    min_deal_size=1.0,
    min_stop_distance_pips=0.0,
    min_stop_policy=MinStopPolicy.ALLOW,
    partial_fill_probability=0.02,
    partial_fill_min_fraction=0.5,
    rejection_probability=0.003,
    latency_bars=0,
    seed=20260919,
    notes="OANDA core pricing: tight spread, no commission, no min stop distance.",
)

#: IBKR IDEALPRO: near-interbank spread but an explicit commission schedule —
#: 0.20 bps of notional with a USD 2.00 per-ticket minimum.
IBKR_REALISTIC = ExecutionProfile(
    name="IBKR_REALISTIC",
    spread=TimeOfDayWideningSpread(
        base=TypicalMultiplierSpread(multiplier=0.45, floor_pips=0.2),
        windows=((21, 23, 2.0), (23, 6, 1.3)),
    ),
    slippage=ProbabilisticAdverseTickSlippage(probability=0.20, tick_pips=0.2, max_ticks=2),
    commission=CommissionModel(bps_of_notional=0.20, minimum=2.0, currency="USD"),
    financing=FinancingModel(annual_bps_long=150.0, annual_bps_short=40.0, basis_days=360.0),
    min_deal_size=1.0,
    min_stop_distance_pips=0.0,
    min_stop_policy=MinStopPolicy.ALLOW,
    partial_fill_probability=0.05,
    partial_fill_min_fraction=0.25,
    rejection_probability=0.002,
    latency_bars=0,
    seed=20260919,
    notes=(
        "IBKR IDEALPRO. Commission is denominated in USD and converted by the "
        "engine, NOT assumed to be the account currency."
    ),
)

#: The pessimistic bound. If an edge survives this it is probably real; if it
#: only survives IDEALISED_RESEARCH it is a spread-capture artefact.
SEVERE_STRESS = ExecutionProfile(
    name="SEVERE_STRESS",
    spread=TimeOfDayWideningSpread(
        base=TypicalMultiplierSpread(multiplier=3.0, floor_pips=2.0),
        windows=((21, 23, 5.0), (23, 6, 3.0)),
    ),
    slippage=ProbabilisticAdverseTickSlippage(probability=0.70, tick_pips=1.0, max_ticks=5),
    commission=CommissionModel(bps_of_notional=0.50, minimum=3.0, currency="USD"),
    financing=FinancingModel(annual_bps_long=500.0, annual_bps_short=350.0, basis_days=360.0),
    min_deal_size=1.0,
    min_stop_distance_pips=10.0,
    min_stop_by_asset_class=_per_class_min_stop(fx_floor_pips=10.0, spread_multiple=6.0),
    min_stop_policy=MinStopPolicy.REJECT,
    guaranteed_stop_premium_pips=6.0,
    partial_fill_probability=0.15,
    partial_fill_min_fraction=0.30,
    rejection_probability=0.03,
    latency_bars=1,
    seed=20260919,
    notes=(
        "Deliberately harsh: 3x spreads, 70% slippage incidence, one bar of "
        "execution latency, 3% rejection. A survivability test, not a forecast."
    ),
)


PROFILES: dict[str, ExecutionProfile] = {
    p.name: p
    for p in (
        IDEALISED_RESEARCH,
        IG_REALISTIC,
        OANDA_REALISTIC,
        IBKR_REALISTIC,
        SEVERE_STRESS,
    )
}


def get_profile(name: str) -> ExecutionProfile:
    key = name.upper()
    if key not in PROFILES:
        raise KeyError(
            f"Unknown execution profile {name!r}. Known: {sorted(PROFILES)}. "
            "V2 will not invent a cost model."
        )
    return PROFILES[key]
