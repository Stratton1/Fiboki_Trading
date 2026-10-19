"""Stressed copies of an execution profile, for the robustness diagnostics.

An execution-realism stress must run through the REAL engine and the REAL cost
models (``stats/stress.py`` rescales the costs already recorded on each trade,
which cannot show a fill that would not have happened, a signal acted on a bar
later, or a size that a wider spread would have shrunk). So the stresses here
are new :class:`~fiboki.sim.profiles.ExecutionProfile` VALUES, built from a
named profile by scaling the parameters of its own concrete models:

* spread x k: every spread model's multiplier, pips, bps and floor scale by k
  (the time-of-day windows keep their own multipliers on top);
* slippage x k: the deterministic amount (pips, bps) or the tick size of the
  probabilistic model scales by k, its probability does not;
* entry delay: ``latency_bars`` (the engine's own ``actionable_index = i + 1 +
  latency_bars``, so a delayed order fills at the open of a later bar);
* trade skip: ``rejection_probability`` with a named seed (the fill model's
  counter-based ``rng_for`` draw, so a skip is a broker reject the engine
  records, and the same seed skips the same orders).

Scaling the parameters, not wrapping the models, keeps
:func:`~fiboki.sim.profiles.expected_slippage_price` (which sizing reads)
defined: it dispatches on the concrete model type and refuses an unknown one.
The stressed profile's NAME says what was done (``IG_REALISTIC~s1.5xl2.0x``),
and its fingerprint (spread and slippage ``repr``, latency, rejection and seed)
lands on every evaluation it produces, so a stressed number can never be read
as the as-researched one.

Known approximation: a profile whose slippage model is :class:`NoSlippage`
cannot be slippage-stressed (zero times k is zero); :func:`slippage_is_modelled`
says so and the diagnostic reports it rather than inventing a basis.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from fiboki.sim.profiles import (
    BpsOfMidSpread,
    BpsSlippage,
    ExecutionProfile,
    FixedPipSpread,
    FixedPointsSlippage,
    NoSlippage,
    ProbabilisticAdverseTickSlippage,
    TimeOfDayWideningSpread,
    TypicalMultiplierSpread,
)

__all__ = ["scale_slippage", "scale_spread", "slippage_is_modelled", "stressed_profile"]


def scale_spread(model: Any, k: float) -> Any:
    """The same spread model with every price-setting parameter scaled by ``k``."""
    if k <= 0:
        raise ValueError("a spread multiplier must be positive")
    if isinstance(model, FixedPipSpread):
        return replace(model, pips=model.pips * k)
    if isinstance(model, TypicalMultiplierSpread):
        return replace(model, multiplier=model.multiplier * k, floor_pips=model.floor_pips * k)
    if isinstance(model, BpsOfMidSpread):
        return replace(model, multiplier=model.multiplier * k, floor_bps=model.floor_bps * k)
    if isinstance(model, TimeOfDayWideningSpread):
        return replace(model, base=scale_spread(model.base, k))
    raise TypeError(
        f"no scaling is defined for spread model {type(model).__name__}; add one here "
        "rather than stress a cost model by a rule nobody wrote"
    )


def scale_slippage(model: Any, k: float) -> Any:
    """The same slippage model with its adverse amount scaled by ``k``."""
    if k < 0:
        raise ValueError("a slippage multiplier must be non-negative")
    if isinstance(model, NoSlippage):
        return model
    if isinstance(model, FixedPointsSlippage):
        return replace(model, pips=model.pips * k)
    if isinstance(model, ProbabilisticAdverseTickSlippage):
        return replace(model, tick_pips=model.tick_pips * k)
    if isinstance(model, BpsSlippage):
        return replace(model, bps=model.bps * k)
    raise TypeError(
        f"no scaling is defined for slippage model {type(model).__name__}; add one here "
        "rather than stress a cost model by a rule nobody wrote"
    )


def slippage_is_modelled(profile: ExecutionProfile) -> bool:
    """False when the profile charges no slippage at all (a multiplier changes nothing)."""
    return not isinstance(profile.slippage, NoSlippage)


def stressed_profile(
    profile: ExecutionProfile,
    *,
    spread_x: float = 1.0,
    slippage_x: float = 1.0,
    latency_bars: int | None = None,
    rejection_probability: float | None = None,
    seed: int | None = None,
) -> ExecutionProfile:
    """A copy of ``profile`` under the named stresses; the identity when none apply."""
    out = profile
    tags: list[str] = []
    if spread_x != 1.0:
        out = replace(out, spread=scale_spread(out.spread, float(spread_x)))
        tags.append(f"s{spread_x:g}x")
    if slippage_x != 1.0:
        out = replace(out, slippage=scale_slippage(out.slippage, float(slippage_x)))
        tags.append(f"l{slippage_x:g}x")
    if latency_bars is not None and latency_bars != profile.latency_bars:
        out = replace(out, latency_bars=int(latency_bars))
        tags.append(f"d{int(latency_bars)}")
    if rejection_probability is not None and rejection_probability != profile.rejection_probability:
        out = replace(out, rejection_probability=float(rejection_probability))
        tags.append(f"r{rejection_probability:g}")
    if seed is not None and seed != profile.seed:
        out = replace(out, seed=int(seed))
        tags.append(f"seed{int(seed)}")
    if tags:
        out = replace(out, name=f"{profile.name}~{''.join(tags)}")
    return out
