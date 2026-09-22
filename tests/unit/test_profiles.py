"""Execution profiles: the cost models themselves, as data.

The point of this module is that a research result is only meaningful next to
the friction assumptions that produced it. These tests pin those assumptions
down so that a profile cannot be edited casually.
"""
from __future__ import annotations

import numpy as np
import pytest

from fiboki.core.enums import Direction
from fiboki.core.instruments import get as get_instrument
from fiboki.sim.profiles import (
    IBKR_REALISTIC,
    IDEALISED_RESEARCH,
    IG_REALISTIC,
    OANDA_REALISTIC,
    PROFILES,
    SEVERE_STRESS,
    CommissionModel,
    ExecutionProfile,
    FinancingModel,
    FixedPipSpread,
    FixedPointsSlippage,
    NoSlippage,
    ProbabilisticAdverseTickSlippage,
    TimeOfDayWideningSpread,
    TypicalMultiplierSpread,
    get_profile,
    rng_for,
)

EURUSD = get_instrument("EURUSD")
XAUUSD = get_instrument("XAUUSD")


# ==========================================================================
# Spread models
# ==========================================================================


def test_fixed_pip_spread_converts_pips_to_price_per_instrument():
    """2 pips is 0.0002 on EURUSD and 0.02 on XAUUSD. Same number, different price."""
    m = FixedPipSpread(2.0)
    assert m.spread_price(EURUSD, 10, 1.10) == pytest.approx(0.0002, abs=1e-15)
    assert m.spread_price(XAUUSD, 10, 2000.0) == pytest.approx(0.02, abs=1e-15)


def test_typical_multiplier_spread_reads_the_instrument_registry():
    """
    EURUSD typical spread is 0.9 pips.
      multiplier 1.0 -> 0.9 pips -> 0.00009
      multiplier 3.0 -> 2.7 pips -> 0.00027
      floor 2.0 pips with multiplier 1.0 -> 2.0 pips (the floor binds)
    """
    assert TypicalMultiplierSpread(1.0).spread_price(EURUSD, 10, 1.1) == pytest.approx(
        0.00009, abs=1e-15
    )
    assert TypicalMultiplierSpread(3.0).spread_price(EURUSD, 10, 1.1) == pytest.approx(
        0.00027, abs=1e-15
    )
    assert TypicalMultiplierSpread(1.0, floor_pips=2.0).spread_price(
        EURUSD, 10, 1.1
    ) == pytest.approx(0.0002, abs=1e-15)


def test_time_of_day_widening_applies_the_first_matching_window():
    """
    Base 1.0 pip. Windows ((21, 23, 3.0), (23, 6, 1.8)).
      hour 10 -> no window       -> 1.0 pip  = 0.0001
      hour 21 -> first window    -> 3.0 pips = 0.0003
      hour 22 -> first window    -> 3.0 pips = 0.0003
      hour 23 -> second (wraps)  -> 1.8 pips = 0.00018
      hour  2 -> second (wraps)  -> 1.8 pips = 0.00018
      hour  6 -> no window       -> 1.0 pip  = 0.0001
    """
    m = TimeOfDayWideningSpread(FixedPipSpread(1.0), ((21, 23, 3.0), (23, 6, 1.8)))
    expected = {10: 0.0001, 21: 0.0003, 22: 0.0003, 23: 0.00018, 2: 0.00018, 6: 0.0001}
    for hour, value in expected.items():
        assert m.spread_price(EURUSD, hour, 1.1) == pytest.approx(value, abs=1e-15), hour


# ==========================================================================
# Slippage models
# ==========================================================================


def test_no_slippage_is_zero_for_both_directions():
    m = NoSlippage()
    rng = rng_for(1, 1)
    assert m.slippage_price(EURUSD, Direction.LONG, 1.1, rng) == 0.0
    assert m.slippage_price(EURUSD, Direction.SHORT, 1.1, rng) == 0.0


def test_probabilistic_slippage_incidence_matches_its_probability():
    """
    probability = 0.30, tick 0.4 pips, max 3 ticks.
    Over 20,000 independent generators the hit rate must be ~30%, and every
    non-zero draw must be 1, 2 or 3 ticks: 0.00004, 0.00008 or 0.00012.
    """
    m = ProbabilisticAdverseTickSlippage(probability=0.30, tick_pips=0.4, max_ticks=3)
    values = [
        m.slippage_price(EURUSD, Direction.LONG, 1.1, rng_for(11, i))
        for i in range(20_000)
    ]
    arr = np.array(values)
    hit_rate = float((arr > 0).mean())
    # sd = sqrt(.3*.7/20000) = 0.0032, so 0.01 is > 3 sigma
    assert hit_rate == pytest.approx(0.30, abs=0.01)
    allowed = {0.0, 0.00004, 0.00008, 0.00012}
    assert all(any(abs(v - a) < 1e-12 for a in allowed) for v in arr)
    assert arr.min() >= 0.0, "slippage must never be favourable"


def test_slippage_is_reproducible_from_the_seed_triple():
    m = ProbabilisticAdverseTickSlippage()
    a = m.slippage_price(EURUSD, Direction.LONG, 1.1, rng_for(99, 7, 3))
    b = m.slippage_price(EURUSD, Direction.LONG, 1.1, rng_for(99, 7, 3))
    assert a == b


def test_rng_derivation_is_counter_based_not_stream_based():
    """
    (seed, bar, seq) must be independent of call order, so adding an instrument
    to a portfolio cannot change the fills of the instruments already in it.
    """
    forward = [rng_for(5, i).random() for i in range(50)]
    backward = [rng_for(5, i).random() for i in reversed(range(50))]
    assert forward == list(reversed(backward))
    assert rng_for(5, 10, 0).random() != rng_for(5, 10, 1).random()
    assert rng_for(5, 10, 0).random() != rng_for(6, 10, 0).random()


# ==========================================================================
# Commission
# ==========================================================================


def test_commission_components_sum_then_floor():
    """
    per_trade 1.00 + per_unit 0.0002 * 10,000 (= 2.00)
      + 0.5 bps of notional (1.1 * 10,000 = 11,000 -> 11,000 * 5e-5 = 0.55)
      = 3.55, which is above the 2.00 minimum, so the charge is 3.55.
    """
    m = CommissionModel(per_trade=1.00, per_unit=0.0002, bps_of_notional=0.5, minimum=2.00)
    assert m.commission(EURUSD, 10_000, 1.10) == pytest.approx(3.55, abs=1e-9)


def test_commission_minimum_binds_on_a_small_ticket():
    """0.20 bps on a 1,100 notional is 0.022, below the 2.00 minimum."""
    m = CommissionModel(bps_of_notional=0.20, minimum=2.00)
    assert m.commission(EURUSD, 1_000, 1.10) == pytest.approx(2.00, abs=1e-12)


def test_zero_size_is_never_charged():
    m = CommissionModel(per_trade=5.0, minimum=10.0)
    assert m.commission(EURUSD, 0.0, 1.10) == 0.0


def test_commission_currency_is_explicit_not_assumed():
    """'quote' resolves per instrument; an ISO code stays put."""
    assert CommissionModel().currency_for(EURUSD) == "USD"
    assert CommissionModel().currency_for(get_instrument("EURGBP")) == "GBP"
    assert CommissionModel(currency="USD").currency_for(get_instrument("EURGBP")) == "USD"


# ==========================================================================
# Financing
# ==========================================================================


def test_financing_nightly_charge_is_hand_checkable():
    """
    annual_bps_long = 365, basis_days = 365 -> 1 bp per night.
    notional = 1.10 * 10,000 * 1.0 = 11,000 -> 11,000 * 1e-4 = 1.10 per night.
    """
    m = FinancingModel(annual_bps_long=365.0, basis_days=365.0)
    assert m.nightly_charge(EURUSD, Direction.LONG, 10_000, 1.10) == pytest.approx(
        1.10, abs=1e-9
    )


def test_long_and_short_financing_differ():
    m = FinancingModel(annual_bps_long=365.0, annual_bps_short=73.0, basis_days=365.0)
    long_ = m.nightly_charge(EURUSD, Direction.LONG, 10_000, 1.10)
    short = m.nightly_charge(EURUSD, Direction.SHORT, 10_000, 1.10)
    assert long_ == pytest.approx(1.10, abs=1e-9)
    assert short == pytest.approx(0.22, abs=1e-9)


def test_a_negative_bps_means_the_position_earns_financing():
    m = FinancingModel(annual_bps_short=-365.0, basis_days=365.0)
    assert m.nightly_charge(EURUSD, Direction.SHORT, 10_000, 1.10) == pytest.approx(
        -1.10, abs=1e-9
    )


# ==========================================================================
# The named profiles
# ==========================================================================


def test_all_five_named_profiles_are_registered():
    assert sorted(PROFILES) == [
        "IBKR_REALISTIC",
        "IDEALISED_RESEARCH",
        "IG_REALISTIC",
        "OANDA_REALISTIC",
        "SEVERE_STRESS",
    ]
    for name in PROFILES:
        assert get_profile(name.lower()).name == name


def test_an_unknown_profile_raises_rather_than_defaulting():
    with pytest.raises(KeyError, match="will not invent a cost model"):
        get_profile("CHEAP_AND_CHEERFUL")


def test_idealised_research_is_genuinely_frictionless():
    p = IDEALISED_RESEARCH
    assert p.spread_price(EURUSD, 10, 1.1) == 0.0
    assert p.commission.commission(EURUSD, 10_000, 1.1) == 0.0
    assert p.financing.nightly_charge(EURUSD, Direction.LONG, 10_000, 1.1) == 0.0
    assert p.slippage.slippage_price(EURUSD, Direction.LONG, 1.1, rng_for(1, 1)) == 0.0
    assert p.rejection_probability == 0.0
    assert p.latency_bars == 0


@pytest.mark.parametrize(
    "profile", [IG_REALISTIC, OANDA_REALISTIC, IBKR_REALISTIC, SEVERE_STRESS]
)
def test_every_realistic_profile_charges_something(profile: ExecutionProfile):
    """A 'realistic' profile with zero total friction is a mislabelled profile."""
    spread = profile.spread_price(EURUSD, 10, 1.10)
    commission = profile.commission.commission(EURUSD, 10_000, 1.10)
    assert spread + commission > 0.0
    assert profile.financing.annual_bps_long > 0.0


def test_severe_stress_is_strictly_harsher_than_ig_on_every_axis():
    """The stress profile must actually stress, or it measures nothing."""
    for hour in (2, 10, 22):
        assert SEVERE_STRESS.spread_price(EURUSD, hour, 1.10) > IG_REALISTIC.spread_price(
            EURUSD, hour, 1.10
        )
    assert SEVERE_STRESS.rejection_probability > IG_REALISTIC.rejection_probability
    assert SEVERE_STRESS.latency_bars >= IG_REALISTIC.latency_bars
    assert SEVERE_STRESS.min_stop_distance_pips >= IG_REALISTIC.min_stop_distance_pips
    assert (
        SEVERE_STRESS.financing.annual_bps_long > IG_REALISTIC.financing.annual_bps_long
    )


def test_ig_widens_at_the_rollover_hours():
    """IG spreads blow out around 21:00-23:00 UTC; the profile must say so."""
    quiet = IG_REALISTIC.spread_price(EURUSD, 10, 1.10)
    rollover = IG_REALISTIC.spread_price(EURUSD, 22, 1.10)
    asian = IG_REALISTIC.spread_price(EURUSD, 2, 1.10)
    assert rollover > asian > quiet


def test_profiles_are_frozen_so_a_run_cannot_mutate_the_assumptions():
    with pytest.raises(AttributeError):
        IG_REALISTIC.seed = 1  # type: ignore[misc]


def test_with_seed_returns_a_new_profile_and_leaves_the_original_alone():
    reseeded = IG_REALISTIC.with_seed(777)
    assert reseeded.seed == 777
    assert IG_REALISTIC.seed != 777
    assert reseeded.name == IG_REALISTIC.name


def test_the_fingerprint_captures_every_assumption_that_can_move_a_number():
    fp = IG_REALISTIC.fingerprint()
    for key in (
        "name", "spread", "slippage", "commission", "financing",
        "min_deal_size", "min_stop_distance_pips", "min_stop_policy",
        "partial_fill_probability", "rejection_probability", "latency_bars", "seed",
    ):
        assert key in fp
    assert fp != SEVERE_STRESS.fingerprint()


# ==========================================================================
# Validation
# ==========================================================================


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"rejection_probability": 1.5}, "must be in"),
        ({"partial_fill_probability": -0.1}, "must be in"),
        ({"partial_fill_min_fraction": 0.0}, "must be in"),
        ({"latency_bars": -1}, "must be >= 0"),
        ({"stale_price_max_gap_multiple": 1.0}, "must exceed 1.0"),
    ],
)
def test_incoherent_profile_settings_are_refused(kwargs, match):
    base = {
        "name": "BAD",
        "spread": FixedPipSpread(1.0),
        "slippage": NoSlippage(),
        "commission": CommissionModel(),
        "financing": FinancingModel(),
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        ExecutionProfile(**base)


def test_negative_fixed_slippage_is_refused():
    with pytest.raises(ValueError, match="price improvement"):
        FixedPointsSlippage(-0.1)


def test_probabilistic_slippage_validates_its_parameters():
    with pytest.raises(ValueError, match="probability"):
        ProbabilisticAdverseTickSlippage(probability=1.2)
    with pytest.raises(ValueError, match="max_ticks"):
        ProbabilisticAdverseTickSlippage(max_ticks=0)
    with pytest.raises(ValueError, match="tick_pips"):
        ProbabilisticAdverseTickSlippage(tick_pips=-1.0)
