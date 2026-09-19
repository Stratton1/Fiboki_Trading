"""The three pre-registered stopping rules, as executable code.

``DEPLOYMENT.md`` §10 and ``VALIDATION_STANDARD.md`` §7 have named these three
since the V2 audit, and both documents said plainly that none of them was
implemented. This module implements them.

    PSR floor              halt when the probabilistic Sharpe of the forward
                           returns, against a benchmark of HALF the backtested
                           Sharpe, falls to 0.50 or below.
    Bootstrap drawdown     halt at the 95th percentile of the block-bootstrap
                           max-drawdown distribution derived from the validation
                           run -- not at a round number somebody liked.
    CUSUM on excess return S_t = max(0, S_{t-1} + (mu_expected - r_t)), halting
                           when S_t exceeds a threshold h calibrated by
                           simulation so the in-control average run length is
                           about two years.

Why these three and not a drawdown limit alone
-----------------------------------------------
They fail in different directions on purpose. A drawdown limit catches a fast,
violent failure and is blind to slow decay -- a strategy that gives back its edge
in small increments may never draw down past a limit while ceasing to work
entirely. The CUSUM is the opposite: it accumulates small shortfalls and is
insensitive to a single bad week. The PSR floor is neither; it asks the question
an operator actually cares about, which is whether the forward record still
supports the claim that this strategy beats half of what the backtest promised.

What "pre-registered" means here, mechanically
-----------------------------------------------
A rule's parameters are fixed and written to an append-only, hash-chained store
BEFORE the strategy produces forward observations, and they cannot be edited
afterwards. The parameter objects are frozen dataclasses, so mutation raises; and
:meth:`PreRegistrationStore.register` never edits -- registering different
parameters for the same strategy and rule appends a NEW registration whose
``supersedes`` names the old one, leaving both visible. That is the whole point.
A stopping rule that can be quietly loosened after the data arrives is not a
stopping rule, it is a post-hoc rationalisation with a timestamp.

:meth:`PreRegistrationStore.is_pre_registered` answers the question that matters
-- whether the ACTIVE registration predates the first forward observation -- and
:mod:`fiboki.lifecycle.promotion` requires a true answer before APPROVED -> LIVE.

Reversal
--------
A firing latches. :class:`HaltRegistry` records it in an append-only journal and
keeps it set even when a later evaluation comes back clear, because the condition
clearing is exactly what a strategy in decline does between bad weeks. Releasing
it requires a named human and a written reason, with no timeout and no auto-reset
-- the same reasoning, and the same shape, as
:class:`fiboki.risk.killswitch.KillSwitch`.

Calibration is a design choice, and is documented as one
---------------------------------------------------------
The CUSUM threshold is not derived from a closed form. It is found by simulating
the in-control process and choosing the threshold whose simulated average run
length matches the target. Three choices inside that are ours, not the data's:
the two-year target itself; the Gaussian in-control model used for the
simulation; and the censoring horizon that makes the simulation finite. All three
are recorded on :class:`CusumCalibration` so that a later reader can disagree
with them specifically rather than with the number.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

from fiboki.lifecycle.monitor import max_drawdown
from fiboki.lifecycle.state import GENESIS_HASH, Evidence, EvidenceKind
from fiboki.stats.bootstrap import moving_block_bootstrap, optimal_block_length
from fiboki.stats.sharpe import probabilistic_sharpe_ratio, sharpe_moments

__all__ = [
    "ALL_RULE_KINDS",
    "BootstrapDrawdownParameters",
    "BootstrapDrawdownRule",
    "CusumCalibration",
    "CusumExcessReturnRule",
    "CusumParameters",
    "DrawdownLimitCalibration",
    "FileHaltJournal",
    "FilePreRegistrationStore",
    "HaltEvent",
    "HaltNotReleasable",
    "HaltRegistry",
    "InMemoryHaltJournal",
    "InMemoryPreRegistrationStore",
    "PreRegistrationStore",
    "PsrFloorParameters",
    "PsrFloorRule",
    "RuleEvaluation",
    "RuleObservation",
    "RuleRegistration",
    "RuleStatus",
    "StoppingRule",
    "StoppingRuleError",
    "StoppingRuleKind",
    "build_rule",
    "calibrate_cusum_threshold",
    "calibrate_drawdown_limit",
    "cusum_parameters_for",
    "cusum_path",
]


class StoppingRuleError(RuntimeError):
    """Base class for stopping-rule misuse."""


class HaltNotReleasable(StoppingRuleError):
    """A halt release was attempted without a named human and a reason."""


class StoppingRuleKind(str, Enum):
    PSR_FLOOR = "psr_floor"
    BOOTSTRAP_DRAWDOWN = "bootstrap_drawdown"
    CUSUM_EXCESS_RETURN = "cusum_excess_return"


ALL_RULE_KINDS: tuple[StoppingRuleKind, ...] = tuple(StoppingRuleKind)
"""All three. ``promotion.py`` requires a registration for every one of them."""


def _fingerprint(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _clean(series: Sequence[float] | np.ndarray | None) -> np.ndarray:
    if series is None:
        return np.empty(0, dtype=float)
    arr = np.asarray(series, dtype=float).ravel()
    return arr[np.isfinite(arr)]


# ==========================================================================
# Parameters
# ==========================================================================


@dataclass(frozen=True, slots=True)
class PsrFloorParameters:
    """Parameters of the PSR floor. Frozen: mutation raises."""

    backtest_sharpe: float
    """The backtested Sharpe at the OBSERVATION frequency of the forward
    returns. Passing an annualised Sharpe here against per-trade observations
    sets a benchmark nothing could ever clear."""
    benchmark_fraction: float = 0.50
    floor: float = 0.50
    min_observations: int = 30

    def __post_init__(self) -> None:
        if not self.backtest_sharpe > 0.0:
            raise ValueError(
                "a PSR floor needs a positive backtested Sharpe to halve; a "
                "strategy with no backtested edge should not have been promoted"
            )
        if not 0.0 < self.benchmark_fraction <= 1.0:
            raise ValueError("benchmark_fraction must be in (0, 1]")
        if not 0.0 < self.floor < 1.0:
            raise ValueError("floor is a probability and must be in (0, 1)")
        if self.min_observations < 2:
            raise ValueError("a Sharpe needs at least 2 observations")

    @property
    def benchmark_sharpe(self) -> float:
        return float(self.backtest_sharpe * self.benchmark_fraction)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["benchmark_sharpe"] = self.benchmark_sharpe
        return out


@dataclass(frozen=True, slots=True)
class BootstrapDrawdownParameters:
    """Parameters of the bootstrap drawdown limit, produced by calibration."""

    threshold: float
    """Fractional max drawdown, in [0, 1]. The ``quantile``-th percentile of the
    block-bootstrap max-drawdown distribution of the VALIDATION returns."""
    quantile: float = 0.95
    n_boot: int = 2000
    block_length: float = 1.0
    calibration_seed: int = 0
    calibration_n_observations: int = 0
    min_observations: int = 20

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold < 1.0:
            raise ValueError("threshold is a fractional drawdown and must be in (0, 1)")
        if not 0.5 < self.quantile < 1.0:
            raise ValueError("quantile must be in (0.5, 1)")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CusumParameters:
    """Parameters of the CUSUM, produced by :func:`cusum_parameters_for`."""

    mu_expected: float
    h: float
    sigma: float
    target_arl_observations: float
    simulated_arl_observations: float
    observations_per_year: float
    calibration_seed: int
    calibration_n_paths: int
    censoring_multiple: float
    censored_fraction: float
    median_run_length: float = 0.0
    false_alarm_probability_first_year: float = float("nan")
    """P(a false halt in the first year) under the in-control model. Reported
    because "one false halt every two years" is a MEAN, and the median run
    length is shorter than the mean, so the first false halt usually arrives
    sooner than the ARL suggests."""
    min_observations: int = 20

    def __post_init__(self) -> None:
        if not self.h > 0.0:
            raise ValueError("the CUSUM threshold h must be positive")
        if not self.sigma > 0.0:
            raise ValueError("the in-control sigma must be positive")

    @property
    def target_arl_years(self) -> float:
        if self.observations_per_year <= 0:
            return float("nan")
        return float(self.target_arl_observations / self.observations_per_year)

    @property
    def simulated_arl_years(self) -> float:
        if self.observations_per_year <= 0:
            return float("nan")
        return float(self.simulated_arl_observations / self.observations_per_year)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["target_arl_years"] = self.target_arl_years
        out["simulated_arl_years"] = self.simulated_arl_years
        return out


_PARAMETER_TYPES: dict[StoppingRuleKind, type] = {
    StoppingRuleKind.PSR_FLOOR: PsrFloorParameters,
    StoppingRuleKind.BOOTSTRAP_DRAWDOWN: BootstrapDrawdownParameters,
    StoppingRuleKind.CUSUM_EXCESS_RETURN: CusumParameters,
}


def _parameters_from_dict(kind: StoppingRuleKind, raw: Mapping[str, Any]) -> Any:
    cls = _PARAMETER_TYPES[kind]
    fields = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in raw.items() if k in fields})


# ==========================================================================
# Calibration
# ==========================================================================


@dataclass(frozen=True, slots=True)
class DrawdownLimitCalibration:
    """The block-bootstrap max-drawdown distribution, and the limit taken from it."""

    threshold: float
    quantile: float
    n_boot: int
    block_length: float
    seed: int
    n_observations: int
    median_drawdown: float
    distribution: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))

    def parameters(self, *, min_observations: int = 20) -> BootstrapDrawdownParameters:
        return BootstrapDrawdownParameters(
            threshold=self.threshold,
            quantile=self.quantile,
            n_boot=self.n_boot,
            block_length=self.block_length,
            calibration_seed=self.seed,
            calibration_n_observations=self.n_observations,
            min_observations=min_observations,
        )

    def describe(self) -> str:
        return (
            f"max drawdown limit {self.threshold:.2%} at the {self.quantile:.0%} "
            f"percentile of {self.n_boot} block-bootstrap paths "
            f"(block {self.block_length:.1f}, median path drawdown "
            f"{self.median_drawdown:.2%})"
        )


def calibrate_drawdown_limit(
    validation_returns: Sequence[float] | np.ndarray,
    *,
    quantile: float = 0.95,
    n_boot: int = 2000,
    block_length: int | None = None,
    seed: int = 20240919,
) -> DrawdownLimitCalibration:
    """The drawdown a strategy like this one would reach by luck alone.

    Resamples the VALIDATION returns in circular blocks -- preserving the serial
    dependence that makes drawdowns deeper than an iid resample would suggest --
    computes the compounded max drawdown of each path, and takes the
    ``quantile``-th percentile.

    The point of doing it this way rather than picking 20%: a round number is a
    statement about the operator's nerve, and a strategy that breaches it may
    simply be a strategy whose normal bad quarter is 22%. This threshold is a
    statement about *this* strategy's own resampled history, so a breach means
    something unusual for this strategy has happened.

    The honest caveat: the distribution is derived from the validation sample, so
    it inherits that sample's regime mix. A forward period containing a regime
    the validation window never saw can breach this limit without anything having
    decayed. That is a reason to investigate, which is what a halt is for.
    """
    arr = _clean(validation_returns)
    if arr.size < 30:
        raise ValueError(
            f"need at least 30 validation returns to calibrate a drawdown "
            f"distribution; got {arr.size}"
        )
    b = (
        max(1, int(round(optimal_block_length(arr).circular)))
        if block_length is None
        else int(block_length)
    )
    paths = moving_block_bootstrap(arr, n_boot=n_boot, block_length=b, rng=seed)
    draws = np.array([max_drawdown(row) for row in paths], dtype=float)
    threshold = float(np.quantile(draws, quantile))
    if not 0.0 < threshold < 1.0:
        raise ValueError(
            f"calibrated drawdown threshold {threshold:.4f} is outside (0, 1); "
            "the validation returns do not look like fractional returns"
        )
    return DrawdownLimitCalibration(
        threshold=threshold,
        quantile=float(quantile),
        n_boot=int(n_boot),
        block_length=float(b),
        seed=int(seed),
        n_observations=int(arr.size),
        median_drawdown=float(np.median(draws)),
        distribution=draws,
    )


@dataclass(frozen=True, slots=True)
class CusumCalibration:
    """A CUSUM threshold, the simulation that produced it, and its assumptions."""

    h: float
    sigma: float
    target_arl_observations: float
    simulated_arl_observations: float
    n_paths: int
    seed: int
    censoring_multiple: float
    censored_fraction: float
    analytic_h: float
    median_run_length: float = 0.0
    """The MEDIAN in-control run length. Materially shorter than the ARL,
    because the first-passage distribution is right-skewed -- an operator told
    only "one false halt every two years" will be surprised by the first one."""
    """``sigma * sqrt(target)``: the driftless-random-walk approximation used to
    centre the search grid. Reported so the simulated answer can be sanity-checked
    against it, not because it is the answer."""
    grid: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))
    grid_arl: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))
    run_lengths: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))

    def false_alarm_probability(self, n_observations: float) -> float:
        """P(a false halt within ``n_observations``) under the in-control model.

        The number an operator should be shown next to the ARL. At a two-year
        ARL the probability of a false halt in the first six months is NOT
        one-quarter; it is appreciably higher, because the run-length
        distribution is right-skewed.
        """
        if self.run_lengths.size == 0:
            return float("nan")
        return float(np.mean(self.run_lengths <= float(n_observations)))

    @property
    def arl_error(self) -> float:
        return float(
            abs(self.simulated_arl_observations - self.target_arl_observations)
            / self.target_arl_observations
        )

    def describe(self) -> str:
        return (
            f"h = {self.h:.6g} (sigma {self.sigma:.6g}); simulated in-control ARL "
            f"{self.simulated_arl_observations:.0f} observations against a target "
            f"of {self.target_arl_observations:.0f} "
            f"({self.arl_error:.1%} error, {self.n_paths} paths, "
            f"{self.censored_fraction:.1%} censored at "
            f"{self.censoring_multiple:g}x target). MEDIAN run length "
            f"{self.median_run_length:.0f}, appreciably shorter than the mean. "
            f"Analytic reference sigma*sqrt(ARL) = {self.analytic_h:.6g}."
        )


def _simulate_arl_grid(
    grid: np.ndarray,
    sigma: float,
    *,
    n_paths: int,
    max_length: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    """First-crossing times of a reflected random walk, for a GRID of thresholds.

    Under the null the observed return equals its expectation in distribution, so
    the CUSUM increment ``mu - r_t`` is mean-zero with standard deviation
    ``sigma``. ``S_t = max(0, S_{t-1} + e_t)`` is then a random walk reflected at
    zero, whose expected first passage to ``h`` is approximately ``(h/sigma)^2``
    -- finite, but with a heavy tail, which is why this is simulated rather than
    read off a table.

    One simulation serves the whole grid: the path of ``S`` does not depend on the
    threshold, only the stopping does. Returns ``(arl, censored_fraction)`` per
    grid point, with uncrossed paths counted at ``max_length``, which biases every
    ARL DOWNWARDS. The bias is reported rather than corrected.
    """
    rng = np.random.default_rng(seed)
    n_grid = grid.size
    cross = np.full((n_grid, n_paths), -1, dtype=np.int64)
    s: np.ndarray = np.zeros(n_paths, dtype=float)
    best_k = np.zeros(n_paths, dtype=np.int64)
    for t in range(1, max_length + 1):
        s = np.maximum(0.0, s + rng.normal(0.0, sigma, n_paths))
        k = np.searchsorted(grid, s, side="right").astype(np.int64)
        improved = np.flatnonzero(k > best_k)
        for p in improved:
            cross[best_k[p] : k[p], p] = t
            best_k[p] = k[p]
        if int(best_k.min()) >= n_grid:
            break
    censored = cross < 0
    times = np.where(censored, max_length, cross).astype(float)
    return times, censored


def calibrate_cusum_threshold(
    *,
    sigma: float,
    target_arl_observations: float,
    n_paths: int = 800,
    seed: int = 20240919,
    censoring_multiple: float = 20.0,
    grid_lo: float = 0.35,
    grid_hi: float = 1.60,
    grid_points: int = 27,
    verify_seed: int | None = None,
) -> CusumCalibration:
    """Find ``h`` whose simulated in-control ARL matches the target.

    **This calibration is a design choice.** Three things in it are ours:

    1. **The target.** Two years of in-control running before a false halt is a
       judgement about how much operator attention a false alarm costs, not a
       statistical fact. A shorter target halts sooner on real decay and more
       often on nothing.
    2. **The in-control model.** The simulation draws Gaussian increments. Real
       excess returns are skewed and fat-tailed, which makes the true ARL at a
       given ``h`` SHORTER than simulated -- so this calibration errs towards
       halting more often than advertised, which is the safe direction but is
       still an error.
    3. **The censoring horizon.** Paths that have not crossed by
       ``censoring_multiple * target`` are counted as having crossed there. That
       biases every simulated ARL downwards, and therefore biases the chosen
       ``h`` upwards -- towards halting LESS often. The two biases point in
       opposite directions and neither is corrected; both are reported.

    The returned ``h`` is the grid point interpolated to hit the target, verified
    by a second simulation on a different seed.
    """
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    if target_arl_observations < 10:
        raise ValueError("a target ARL below 10 observations is not a monitor")
    analytic = float(sigma * math.sqrt(target_arl_observations))
    grid = np.asarray(
        analytic * np.linspace(grid_lo, grid_hi, grid_points), dtype=float
    )
    max_length = int(censoring_multiple * target_arl_observations)
    times, censored = _simulate_arl_grid(
        grid, sigma, n_paths=n_paths, max_length=max_length, seed=seed
    )
    arl = times.mean(axis=1)
    # ARL is increasing in h; np.interp needs an increasing x, so interpolate
    # the target ARL onto the (arl -> h) mapping.
    order = np.argsort(arl)
    h = float(np.interp(float(target_arl_observations), arl[order], grid[order]))
    h = float(min(max(h, grid[0]), grid[-1]))

    verify = np.asarray([h], dtype=float)
    v_times, v_censored = _simulate_arl_grid(
        verify,
        sigma,
        n_paths=n_paths,
        max_length=max_length,
        seed=seed + 1 if verify_seed is None else verify_seed,
    )
    return CusumCalibration(
        h=h,
        sigma=float(sigma),
        target_arl_observations=float(target_arl_observations),
        simulated_arl_observations=float(v_times[0].mean()),
        median_run_length=float(np.median(v_times[0])),
        n_paths=int(n_paths),
        seed=int(seed),
        censoring_multiple=float(censoring_multiple),
        censored_fraction=float(v_censored[0].mean()),
        analytic_h=analytic,
        grid=grid,
        grid_arl=arl,
        run_lengths=v_times[0],
    )


def cusum_parameters_for(
    *,
    mu_expected: float,
    sigma: float,
    observations_per_year: float,
    target_arl_years: float = 2.0,
    min_observations: int = 20,
    **calibration: Any,
) -> CusumParameters:
    """Calibrate and package a CUSUM for a strategy at a given trade frequency."""
    target = float(target_arl_years) * float(observations_per_year)
    cal = calibrate_cusum_threshold(sigma=sigma, target_arl_observations=target, **calibration)
    return CusumParameters(
        mu_expected=float(mu_expected),
        h=cal.h,
        sigma=cal.sigma,
        target_arl_observations=cal.target_arl_observations,
        simulated_arl_observations=cal.simulated_arl_observations,
        median_run_length=cal.median_run_length,
        false_alarm_probability_first_year=cal.false_alarm_probability(
            observations_per_year
        ),
        observations_per_year=float(observations_per_year),
        calibration_seed=cal.seed,
        calibration_n_paths=cal.n_paths,
        censoring_multiple=cal.censoring_multiple,
        censored_fraction=cal.censored_fraction,
        min_observations=int(min_observations),
    )


def cusum_path(
    returns: Sequence[float] | np.ndarray, mu_expected: float
) -> np.ndarray:
    """``S_t = max(0, S_{t-1} + (mu_expected - r_t))``, with ``S_0 = 0``.

    Accumulates SHORTFALL against expectation. A strategy performing exactly as
    expected leaves ``S`` wandering near zero; one quietly underperforming drives
    it up in small steps that no drawdown limit would notice.
    """
    arr = _clean(returns)
    out = np.empty(arr.size, dtype=float)
    s = 0.0
    for i, r in enumerate(arr):
        s = max(0.0, s + (float(mu_expected) - float(r)))
        out[i] = s
    return out


# ==========================================================================
# Pre-registration
# ==========================================================================


@dataclass(frozen=True, slots=True)
class RuleRegistration:
    """One immutable registration of one rule's parameters for one strategy.

    Hash-chained exactly like a lifecycle transition, so an edited registration
    invalidates every registration after it and
    :meth:`PreRegistrationStore.verify` names the break.
    """

    registration_id: str
    sequence: int
    strategy_content_hash: str
    kind: StoppingRuleKind
    parameters_json: Mapping[str, Any]
    parameters_fingerprint: str
    registered_at: datetime
    registered_by: str
    reason: str
    supersedes: str = ""
    """``registration_id`` of the registration this replaces. Non-empty means the
    parameters were changed, which is visible for ever."""
    previous_hash: str = GENESIS_HASH
    record_hash: str = ""

    def parameters(self) -> Any:
        """The typed parameter object. Frozen, so it cannot be edited."""
        return _parameters_from_dict(self.kind, self.parameters_json)

    def payload(self) -> dict[str, Any]:
        return {
            "registration_id": self.registration_id,
            "sequence": int(self.sequence),
            "strategy_content_hash": self.strategy_content_hash,
            "kind": self.kind.value,
            "parameters": dict(self.parameters_json),
            "parameters_fingerprint": self.parameters_fingerprint,
            "registered_at": self.registered_at.astimezone(UTC).isoformat(),
            "registered_by": self.registered_by,
            "reason": self.reason,
            "supersedes": self.supersedes,
            "previous_hash": self.previous_hash,
        }

    def recompute_hash(self) -> str:
        return _fingerprint(self.payload())

    def as_evidence(self) -> Evidence:
        return Evidence(
            kind=EvidenceKind.PRE_REGISTRATION,
            id=self.registration_id,
            summary=(
                f"{self.kind.value} pre-registered by {self.registered_by} at "
                f"{self.registered_at.astimezone(UTC).isoformat()}"
            ),
            detail=self.payload(),
        )

    def to_dict(self) -> dict[str, Any]:
        out = self.payload()
        out["record_hash"] = self.record_hash
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RuleRegistration:
        return cls(
            registration_id=str(raw["registration_id"]),
            sequence=int(raw["sequence"]),
            strategy_content_hash=str(raw["strategy_content_hash"]),
            kind=StoppingRuleKind(raw["kind"]),
            parameters_json=dict(raw["parameters"]),
            parameters_fingerprint=str(raw["parameters_fingerprint"]),
            registered_at=datetime.fromisoformat(str(raw["registered_at"])),
            registered_by=str(raw["registered_by"]),
            reason=str(raw.get("reason", "")),
            supersedes=str(raw.get("supersedes", "")),
            previous_hash=str(raw.get("previous_hash", GENESIS_HASH)),
            record_hash=str(raw.get("record_hash", "")),
        )


class PreRegistrationStore(ABC):
    """Append-only store of rule registrations. No update, no delete."""

    @abstractmethod
    def _append(self, record: RuleRegistration) -> None: ...

    @abstractmethod
    def records(self) -> list[RuleRegistration]: ...

    # ------------------------------------------------------------- writes

    def register(
        self,
        *,
        strategy_content_hash: str,
        kind: StoppingRuleKind,
        parameters: Any,
        registered_by: str,
        reason: str,
        at: datetime | None = None,
    ) -> RuleRegistration:
        """Append a registration. NEVER edits an existing one.

        Registering different parameters for a strategy and rule that already has
        a registration is allowed -- a threshold calibrated against the wrong
        frequency has to be fixable -- but it appends a new record whose
        ``supersedes`` names the old one. Both remain in the store and in the
        hash chain, so "we changed the rule after we saw the data" is a question
        anyone can answer from the artefact.
        """
        if not str(registered_by).strip():
            raise StoppingRuleError(
                "a registration must name who made it: a pre-registration nobody "
                "signed is not a commitment"
            )
        if not str(reason).strip():
            raise StoppingRuleError("a registration must say why these parameters")
        if not isinstance(parameters, _PARAMETER_TYPES[kind]):
            raise StoppingRuleError(
                f"{kind.value} expects {_PARAMETER_TYPES[kind].__name__}, "
                f"got {type(parameters).__name__}"
            )
        payload = dict(parameters.to_dict())  # type: ignore[attr-defined]
        fingerprint = _fingerprint({"kind": kind.value, "parameters": payload})
        previous = self.active(strategy_content_hash, kind)
        if previous is not None and previous.parameters_fingerprint == fingerprint:
            return previous  # identical re-registration is a no-op, not a new row
        rows = self.records()
        when = at or datetime.now(tz=UTC)
        draft = RuleRegistration(
            registration_id=(
                f"reg_{strategy_content_hash[:12]}_{kind.value}_{len(rows)}"
            ),
            sequence=len(rows),
            strategy_content_hash=strategy_content_hash,
            kind=kind,
            parameters_json=payload,
            parameters_fingerprint=fingerprint,
            registered_at=when,
            registered_by=registered_by,
            reason=reason,
            supersedes=previous.registration_id if previous is not None else "",
            previous_hash=rows[-1].record_hash if rows else GENESIS_HASH,
        )
        sealed = replace(draft, record_hash=draft.recompute_hash())
        self._append(sealed)
        return sealed

    # -------------------------------------------------------------- reads

    def active(
        self, strategy_content_hash: str, kind: StoppingRuleKind
    ) -> RuleRegistration | None:
        """The latest registration for this strategy and rule, or ``None``."""
        matching = [
            r
            for r in self.records()
            if r.strategy_content_hash == strategy_content_hash and r.kind is kind
        ]
        return matching[-1] if matching else None

    def history(
        self, strategy_content_hash: str, kind: StoppingRuleKind | None = None
    ) -> list[RuleRegistration]:
        return [
            r
            for r in self.records()
            if r.strategy_content_hash == strategy_content_hash
            and (kind is None or r.kind is kind)
        ]

    def active_rules(self, strategy_content_hash: str) -> dict[StoppingRuleKind, RuleRegistration]:
        out: dict[StoppingRuleKind, RuleRegistration] = {}
        for kind in ALL_RULE_KINDS:
            found = self.active(strategy_content_hash, kind)
            if found is not None:
                out[kind] = found
        return out

    def missing_rules(self, strategy_content_hash: str) -> tuple[StoppingRuleKind, ...]:
        registered = set(self.active_rules(strategy_content_hash))
        return tuple(k for k in ALL_RULE_KINDS if k not in registered)

    def is_pre_registered(
        self, strategy_content_hash: str, *, first_forward_observation_at: datetime
    ) -> bool:
        """True only when ALL three rules were registered before forward data.

        The ACTIVE registration is what is checked, not the earliest one. A rule
        first registered in good time and then re-registered with kinder
        parameters once the returns were visible is not pre-registered, and
        answering on the first registration would let exactly that pass.
        """
        active = self.active_rules(strategy_content_hash)
        if set(active) != set(ALL_RULE_KINDS):
            return False
        return all(r.registered_at < first_forward_observation_at for r in active.values())

    def verify(self) -> tuple[bool, int | None, str]:
        """``(ok, broken_index, reason)`` over the whole chain."""
        previous = GENESIS_HASH
        for i, row in enumerate(self.records()):
            if row.sequence != i:
                return False, i, f"sequence {row.sequence} at position {i}"
            if row.previous_hash != previous:
                return False, i, "previous_hash does not match its predecessor"
            recomputed = row.recompute_hash()
            if recomputed != row.record_hash:
                return False, i, "record_hash does not match the payload"
            previous = row.record_hash
        return True, None, "chain intact"


class InMemoryPreRegistrationStore(PreRegistrationStore):
    def __init__(self, records: Iterable[RuleRegistration] = ()) -> None:
        self._records: list[RuleRegistration] = list(records)

    def _append(self, record: RuleRegistration) -> None:
        self._records.append(record)

    def records(self) -> list[RuleRegistration]:
        return list(self._records)


class FilePreRegistrationStore(PreRegistrationStore):
    """JSON-lines, fsynced. The registration must survive the crash it predicts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def _append(self, record: RuleRegistration) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"), default=str)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    def records(self) -> list[RuleRegistration]:
        return [
            RuleRegistration.from_dict(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


# ==========================================================================
# Evaluation
# ==========================================================================


class RuleStatus(str, Enum):
    CLEAR = "clear"
    FIRED = "fired"
    NOT_EVALUATED = "not_evaluated"
    """Not enough forward data. NEVER reported as clear."""

    @property
    def halts(self) -> bool:
        return self is RuleStatus.FIRED


@dataclass(frozen=True, slots=True)
class RuleObservation:
    """The forward record one rule is evaluated against."""

    strategy_content_hash: str
    returns: np.ndarray = field(default_factory=lambda: np.empty(0), repr=False)
    at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "returns", _clean(self.returns))

    @property
    def n(self) -> int:
        return int(self.returns.size)


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """One rule's verdict, with the arithmetic that produced it."""

    kind: StoppingRuleKind
    status: RuleStatus
    strategy_content_hash: str
    registration_id: str
    parameters_fingerprint: str
    statistic: float | None = None
    threshold: float | None = None
    comparison: str = ""
    n_observations: int = 0
    reason: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return self.status is RuleStatus.FIRED

    def describe(self) -> str:
        if self.status is RuleStatus.NOT_EVALUATED:
            return f"{self.kind.value}: NOT EVALUATED -- {self.reason}"
        verdict = "FIRED" if self.fired else "clear"
        return (
            f"{self.kind.value}: {self.statistic:.6g} {self.comparison} "
            f"{self.threshold:.6g} -> {verdict} (n={self.n_observations})"
            + (f" -- {self.reason}" if self.reason else "")
        )

    def as_evidence(self) -> Evidence:
        return Evidence(
            kind=EvidenceKind.STOPPING_RULE,
            id=f"{self.registration_id}@{self.at.astimezone(UTC).isoformat()}",
            summary=self.describe(),
            detail=self.to_dict(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "status": self.status.value,
            "strategy_content_hash": self.strategy_content_hash,
            "registration_id": self.registration_id,
            "parameters_fingerprint": self.parameters_fingerprint,
            "statistic": self.statistic,
            "threshold": self.threshold,
            "comparison": self.comparison,
            "n_observations": int(self.n_observations),
            "reason": self.reason,
            "at": self.at.astimezone(UTC).isoformat(),
            "detail": dict(self.detail),
            "description": self.describe(),
        }


# ==========================================================================
# The rules
# ==========================================================================


class StoppingRule(ABC):
    """A rule bound to ONE registration. Parameters come from the store.

    A rule cannot be constructed with loose parameters: it is built from a
    :class:`RuleRegistration`, so every evaluation carries the registration id and
    the parameter fingerprint it was judged under, and an evaluation produced
    under parameters nobody registered is not expressible.
    """

    kind: StoppingRuleKind

    def __init__(self, registration: RuleRegistration) -> None:
        if registration.kind is not self.kind:
            raise StoppingRuleError(
                f"{type(self).__name__} needs a {self.kind.value} registration, "
                f"got {registration.kind.value}"
            )
        self.registration = registration
        self.parameters = registration.parameters()

    @abstractmethod
    def _evaluate(self, observation: RuleObservation) -> RuleEvaluation: ...

    def evaluate(self, observation: RuleObservation) -> RuleEvaluation:
        if observation.strategy_content_hash != self.registration.strategy_content_hash:
            raise StoppingRuleError(
                "this rule is registered for strategy "
                f"{self.registration.strategy_content_hash[:12]}, not "
                f"{observation.strategy_content_hash[:12]}"
            )
        return self._evaluate(observation)

    # ------------------------------------------------------------- helpers

    def _not_evaluated(self, observation: RuleObservation, reason: str) -> RuleEvaluation:
        return RuleEvaluation(
            kind=self.kind,
            status=RuleStatus.NOT_EVALUATED,
            strategy_content_hash=observation.strategy_content_hash,
            registration_id=self.registration.registration_id,
            parameters_fingerprint=self.registration.parameters_fingerprint,
            n_observations=observation.n,
            reason=reason,
            at=observation.at,
        )

    def _verdict(
        self,
        observation: RuleObservation,
        *,
        fired: bool,
        statistic: float,
        threshold: float,
        comparison: str,
        reason: str = "",
        detail: Mapping[str, Any] | None = None,
    ) -> RuleEvaluation:
        return RuleEvaluation(
            kind=self.kind,
            status=RuleStatus.FIRED if fired else RuleStatus.CLEAR,
            strategy_content_hash=observation.strategy_content_hash,
            registration_id=self.registration.registration_id,
            parameters_fingerprint=self.registration.parameters_fingerprint,
            statistic=float(statistic),
            threshold=float(threshold),
            comparison=comparison,
            n_observations=observation.n,
            reason=reason,
            at=observation.at,
            detail=dict(detail or {}),
        )


class PsrFloorRule(StoppingRule):
    """Halt when the PSR against half the backtested Sharpe falls to the floor.

    ``PSR(SR*)`` is the probability that the TRUE Sharpe exceeds ``SR*``, given a
    finite, skewed, fat-tailed sample -- so at 0.50 the forward record is exactly
    balanced between "this strategy still beats half its backtest" and "it does
    not", and below 0.50 the balance has tipped.

    The comparison is ``<=``, not ``<``, and that matters at exactly one point:
    a forward Sharpe precisely equal to half the backtested Sharpe produces a PSR
    of 0.50 and **halts**. A strategy delivering half of what it promised has not
    earned the benefit of the doubt, and the boundary case is the one an operator
    is most likely to be arguing about.
    """

    kind = StoppingRuleKind.PSR_FLOOR

    def _evaluate(self, observation: RuleObservation) -> RuleEvaluation:
        params: PsrFloorParameters = self.parameters
        if observation.n < params.min_observations:
            return self._not_evaluated(
                observation,
                f"{observation.n} forward returns, {params.min_observations} required",
            )
        try:
            moments = sharpe_moments(observation.returns)
            psr = probabilistic_sharpe_ratio(
                moments.sr_hat,
                moments.n_obs,
                skew=moments.skew,
                kurtosis=moments.kurtosis,
                sr_benchmark=params.benchmark_sharpe,
            )
        except ValueError as exc:
            # A Sharpe that cannot be honestly computed is NOT a passing Sharpe.
            return self._not_evaluated(observation, f"PSR undefined: {exc}")
        if not (
            math.isfinite(psr)
            and math.isfinite(moments.sr_hat)
            and math.isfinite(moments.skew)
            and math.isfinite(moments.kurtosis)
        ):
            # A near-constant return series drives the moment estimators into
            # catastrophic cancellation: the Sharpe comes back as 1e15 and the
            # skew and kurtosis as NaN. `nan <= floor` is False, so without this
            # guard an uncomputable PSR would read as a PASSING one -- absence
            # wearing the costume of a value, which is the V1 failure this
            # codebase exists to refuse.
            return self._not_evaluated(
                observation,
                "PSR undefined: the forward returns have no usable dispersion, "
                f"so the moment set is non-finite (sr={moments.sr_hat:.3g}, "
                f"skew={moments.skew}, kurtosis={moments.kurtosis})",
            )
        return self._verdict(
            observation,
            fired=psr <= params.floor,
            statistic=psr,
            threshold=params.floor,
            comparison="<=",
            reason=(
                f"forward Sharpe {moments.sr_hat:.4g} against a benchmark of "
                f"{params.benchmark_sharpe:.4g} "
                f"({params.benchmark_fraction:g} x backtest "
                f"{params.backtest_sharpe:.4g})"
            ),
            detail={
                "forward_sharpe": moments.sr_hat,
                "benchmark_sharpe": params.benchmark_sharpe,
                "backtest_sharpe": params.backtest_sharpe,
                "skew": moments.skew,
                "kurtosis": moments.kurtosis,
            },
        )


class BootstrapDrawdownRule(StoppingRule):
    """Halt when the forward max drawdown exceeds the pre-registered percentile.

    The threshold is NOT a round number. It is the ``quantile``-th percentile of
    the block-bootstrap max-drawdown distribution derived from the validation
    run, computed by :func:`calibrate_drawdown_limit` before the strategy ran.
    """

    kind = StoppingRuleKind.BOOTSTRAP_DRAWDOWN

    def _evaluate(self, observation: RuleObservation) -> RuleEvaluation:
        params: BootstrapDrawdownParameters = self.parameters
        if observation.n < params.min_observations:
            return self._not_evaluated(
                observation,
                f"{observation.n} forward returns, {params.min_observations} required",
            )
        observed = max_drawdown(observation.returns)
        return self._verdict(
            observation,
            fired=observed > params.threshold,
            statistic=observed,
            threshold=params.threshold,
            comparison=">",
            reason=(
                f"forward max drawdown {observed:.2%} against the "
                f"{params.quantile:.0%} percentile of the validation bootstrap "
                f"({params.threshold:.2%}, {params.n_boot} paths, block "
                f"{params.block_length:g})"
            ),
            detail={
                "quantile": params.quantile,
                "n_boot": params.n_boot,
                "block_length": params.block_length,
                "calibration_seed": params.calibration_seed,
            },
        )


class CusumExcessReturnRule(StoppingRule):
    """Halt when accumulated shortfall against expectation exceeds ``h``.

    ``S_t = max(0, S_{t-1} + (mu_expected - r_t))``. The reflection at zero is
    what makes this a decay detector rather than a performance tally: good
    periods do not bank credit against future bad ones, so a strategy that
    delivers and then stops delivering is caught on the second half rather than
    averaged out over both.
    """

    kind = StoppingRuleKind.CUSUM_EXCESS_RETURN

    def _evaluate(self, observation: RuleObservation) -> RuleEvaluation:
        params: CusumParameters = self.parameters
        if observation.n < params.min_observations:
            return self._not_evaluated(
                observation,
                f"{observation.n} forward returns, {params.min_observations} required",
            )
        path = cusum_path(observation.returns, params.mu_expected)
        peak = float(path.max())
        crossed = np.flatnonzero(path > params.h)
        first = int(crossed[0]) if crossed.size else -1
        return self._verdict(
            observation,
            fired=first >= 0,
            statistic=peak,
            threshold=params.h,
            comparison=">",
            reason=(
                f"accumulated shortfall against an expected {params.mu_expected:.6g} "
                f"per observation; h calibrated to an in-control ARL of "
                f"{params.simulated_arl_observations:.0f} observations "
                f"(~{params.simulated_arl_years:.1f} years)"
            ),
            detail={
                "first_crossing_index": first,
                "final_s": float(path[-1]),
                "mu_expected": params.mu_expected,
                "target_arl_observations": params.target_arl_observations,
                "simulated_arl_observations": params.simulated_arl_observations,
                "calibration_note": (
                    "the ARL target, the Gaussian in-control model and the "
                    "censoring horizon are design choices, recorded on the "
                    "calibration rather than presented as facts"
                ),
            },
        )


_RULE_TYPES: dict[StoppingRuleKind, type[StoppingRule]] = {
    StoppingRuleKind.PSR_FLOOR: PsrFloorRule,
    StoppingRuleKind.BOOTSTRAP_DRAWDOWN: BootstrapDrawdownRule,
    StoppingRuleKind.CUSUM_EXCESS_RETURN: CusumExcessReturnRule,
}


def build_rule(registration: RuleRegistration) -> StoppingRule:
    """The rule a registration describes. The ONLY way to get a rule object."""
    return _RULE_TYPES[registration.kind](registration)


# ==========================================================================
# Halts, and their release
# ==========================================================================


@dataclass(frozen=True, slots=True)
class HaltEvent:
    """One immutable row in the halt journal."""

    action: str
    """``"halt"`` or ``"release"``."""
    strategy_content_hash: str
    kind: StoppingRuleKind
    at: datetime
    operator: str = ""
    reason: str = ""
    evaluation: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "action": self.action,
                "strategy_content_hash": self.strategy_content_hash,
                "kind": self.kind.value,
                "at": self.at.astimezone(UTC).isoformat(),
                "operator": self.operator,
                "reason": self.reason,
                "evaluation": dict(self.evaluation),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def from_json(cls, line: str) -> HaltEvent:
        raw = json.loads(line)
        return cls(
            action=str(raw["action"]),
            strategy_content_hash=str(raw["strategy_content_hash"]),
            kind=StoppingRuleKind(raw["kind"]),
            at=datetime.fromisoformat(str(raw["at"])),
            operator=str(raw.get("operator", "")),
            reason=str(raw.get("reason", "")),
            evaluation=dict(raw.get("evaluation") or {}),
        )


class InMemoryHaltJournal:
    def __init__(self) -> None:
        self._events: list[HaltEvent] = []

    def append(self, event: HaltEvent) -> None:
        self._events.append(event)

    def events(self) -> list[HaltEvent]:
        return list(self._events)


class FileHaltJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, event: HaltEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def events(self) -> list[HaltEvent]:
        return [
            HaltEvent.from_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class HaltRegistry:
    """Latched halts. A fired rule stays fired until a human releases it.

    Latching rather than levelling, deliberately, and the opposite choice from
    :class:`fiboki.obs.alerts.AlertDispatcher`'s level-based repeat suppression.
    An alert that stops repeating when the condition clears is right, because the
    alert is a notification. A halt that clears itself when the condition clears
    is wrong, because the condition clearing for a week is exactly what a
    decaying strategy does, and the halt is a decision that a human took the
    evidence seriously enough to stop.

    No timeout. No auto-reset. No "it clears when the drawdown recovers".
    """

    def __init__(self, journal: InMemoryHaltJournal | FileHaltJournal | None = None) -> None:
        self.journal = journal if journal is not None else InMemoryHaltJournal()

    def _state(self) -> dict[tuple[str, StoppingRuleKind], HaltEvent]:
        latched: dict[tuple[str, StoppingRuleKind], HaltEvent] = {}
        for event in self.journal.events():
            key = (event.strategy_content_hash, event.kind)
            if event.action == "halt":
                latched[key] = event
            elif event.action == "release":
                latched.pop(key, None)
        return latched

    def record_firing(self, evaluation: RuleEvaluation) -> HaltEvent | None:
        """Latch a halt. Returns ``None`` when the rule was already latched."""
        if not evaluation.fired:
            return None
        key = (evaluation.strategy_content_hash, evaluation.kind)
        if key in self._state():
            return None
        event = HaltEvent(
            action="halt",
            strategy_content_hash=evaluation.strategy_content_hash,
            kind=evaluation.kind,
            at=evaluation.at,
            operator="",
            reason=evaluation.describe(),
            evaluation=evaluation.to_dict(),
        )
        self.journal.append(event)
        return event

    def release(
        self,
        strategy_content_hash: str,
        kind: StoppingRuleKind,
        *,
        operator: str,
        reason: str,
        at: datetime | None = None,
    ) -> HaltEvent:
        """Explicit operator reversal. Both arguments are mandatory."""
        if not str(operator).strip() or not str(reason).strip():
            raise HaltNotReleasable(
                "releasing a halt requires a named operator and a written reason; "
                "a halt that anybody can clear silently is not a halt"
            )
        if (strategy_content_hash, kind) not in self._state():
            raise HaltNotReleasable(
                f"{kind.value} is not latched for "
                f"{strategy_content_hash[:12]}; there is nothing to release"
            )
        event = HaltEvent(
            action="release",
            strategy_content_hash=strategy_content_hash,
            kind=kind,
            at=at or datetime.now(tz=UTC),
            operator=operator,
            reason=reason,
        )
        self.journal.append(event)
        return event

    # -------------------------------------------------------------- reads

    def is_halted(self, strategy_content_hash: str, kind: StoppingRuleKind | None = None) -> bool:
        state = self._state()
        if kind is not None:
            return (strategy_content_hash, kind) in state
        return any(h == strategy_content_hash for h, _ in state)

    def latched(self, strategy_content_hash: str) -> tuple[StoppingRuleKind, ...]:
        return tuple(
            sorted(
                (k for h, k in self._state() if h == strategy_content_hash),
                key=lambda k: k.value,
            )
        )

    def events(self) -> list[HaltEvent]:
        return self.journal.events()
