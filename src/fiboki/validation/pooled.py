"""Pooled-universe candidates: one strategy document, its whole universe, ONE candidate.

Why this exists
---------------
E-2 (research/reports/RESEARCH_LEDGER.md, "E-2 result") showed that no gate set
can have power against a 0.08 per-trade Sharpe on ONE series: the documents
produce a median of about 330 trades on twenty years of H4 bars, and a one-sided
95% test of a 0.08 edge needs 425 (MinTRL). The constraint is trades per
candidate, not thresholds. Pooling one document across the instruments it was
written for multiplies the trades by roughly the number of instruments.

What pooling is here, exactly
-----------------------------
A pooled candidate is a document evaluated on each series of its universe by the
EXISTING engine path (one :class:`~fiboki.validation.engine_evaluator.EngineEvaluator`
per series, its own instrument, costs, spread, financing, FX conversion, calendar
and bars), and the per-series results summed. There is no second backtester: every
trade in a pooled evaluation is a trade the single-series research path produces,
byte for byte, for the same binding and window.

* **Equal risk per instrument.** Each series is a SLEEVE with its own account of
  the same opening balance, sized by the same fixed-fractional rule, so every
  sleeve risks the same fraction of the same capital on each trade. The pooled
  P&L is the sum of the sleeves' P&L; the pooled opening equity is the sum of
  their opening equities.
* **Costs stay per instrument.** Each :class:`~fiboki.core.contracts.Trade`
  carries its own instrument's spread, slippage, commission and financing, so the
  rung-4 cost stresses act on each instrument's real costs.
* **One calendar.** The pooled per-period series is indexed on the UNION of the
  sleeves' bar timestamps inside the window; a sleeve contributes zero on a bar
  it does not have. Every binding of the document shares that calendar (it depends
  only on the bars), which is what the trials matrix of rungs 3 and 5 requires.

Approximation, stated: the sleeves do not share an account. Portfolio-level
interactions across instruments (one equity base, cross-instrument construction,
a book-level concurrency cap) are not modelled; a pooled result is the sum of
independent, equal-capital single-series runs.

The effective sample size
-------------------------
Trades on correlated series at the same time are not independent: sixteen USD
pairs long the dollar in the same week are closer to one bet than to sixteen.
Counting them as sixteen would let a candidate clear a trade-count gate on
correlation alone. So every place the ladder reads a trade COUNT as a SAMPLE SIZE
reads ``N_eff = N / DEFF`` instead (``WindowEvaluation.n_trades_effective``):

    DEFF = 1' S 1 / trace(S) = 1 + (m - 1) * rho_bar

where ``S`` is the sample covariance matrix of the ``m`` sleeves' per-period
P&L and ``rho_bar`` the variance-weighted mean pairwise correlation. This is
Kish's design effect for cluster sampling: the variance of the pooled sum over
the variance it would have if the sleeves were independent. Independent sleeves
give ``DEFF = 1`` (``N_eff = N``); identical sleeves give ``DEFF = m``
(``N_eff = N / m``). It is computed on DAILY and on WEEKLY (Monday-start UTC)
sums of the per-bar P&L and the LARGER is used, because sessions close at
different times (index CFDs, metals and FX do not share a daily close) and
asynchronous sampling biases a daily correlation towards zero (the Epps effect);
it is floored at 1, so negative correlation never buys a larger sample than the
trades themselves.

Where ``N_eff`` replaces ``N``, and where it does not:

* rung 0: the ``n_trades`` gate value, the rung-0 trade floor, and the MinTRL
  gate's numerator (``n_trades_over_min_trl = N_eff / max(150, MinTRL_95)``);
* rung 2: ``walk_forward_min_oos_trades`` (the 30-trade OOS-fold floor);
* rung 6: ``holdout_min_trades``;
* NOT the expectancy or the MinTRL moments (a mean and its shape use every
  trade; only the count of independent observations is corrected);
* NOT the deflated Sharpe, PBO, SPA, StepM or the purged-CV paths: those are
  computed on the pooled PER-PERIOD series, whose variance already contains every
  cross-sleeve covariance, and whose length is a number of periods, not trades.
  Substituting ``N_eff`` there would count the correlation twice;
* NOT the deflation's trial count. ``N_eff`` is a sample size; the number of
  trials a search made is a different quantity, and a pooled candidate's trials
  are counted in full (``LadderConfig.external_trial_count``).

Approximation, stated: the design effect is cross-sectional. Serial dependence
of one sleeve's trade outcomes is not corrected, exactly as the single-series
ladder does not correct it; E-4's process P0 measures whether the combination is
calibrated (the variance of the defaults' z-statistic at zero edge).

The holdout
-----------
Each series keeps its own holdout segment in the real registry.
:class:`PooledHoldoutRegistry` presents the ladder with one composite segment
whose research window ends where the EARLIEST constituent holdout begins and
whose holdout window starts where the LATEST begins, so no rung before 6 touches
any constituent's holdout and rung 6 reads only data that is holdout for every
constituent. A claim is checked against EVERY constituent first and then written
to every constituent: a pooled look at the holdout spends the document's one look
on each series in it.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fiboki.core.enums import Provenance
from fiboki.validation.evaluation import DateWindow, Evaluator, WindowEvaluation
from fiboki.validation.holdout import (
    HoldoutAlreadyConsumed,
    HoldoutLeak,
    HoldoutRegistry,
    HoldoutSegment,
    HoldoutToken,
    UnknownHoldout,
)
from fiboki.validation.ladder import Rung, default_rungs
from fiboki.validation.report import RungOutcome, RungResult

__all__ = [
    "DEFF_FREQUENCIES",
    "MIN_PERIODS_FOR_DEFF",
    "DeferredHoldoutRung",
    "DesignEffect",
    "PooledEvaluator",
    "PooledHoldoutRegistry",
    "PooledHoldoutSegment",
    "PooledSleeve",
    "design_effect",
    "pooled_dataset_version_id",
    "rungs_with_deferred_holdout",
]

_NS_PER_DAY = 86_400 * 1_000_000_000
#: 1970-01-01 was a Thursday; adding three days makes integer weeks start on Monday.
_MONDAY_SHIFT_DAYS = 3

#: The aggregation frequencies the design effect is measured at; the larger wins.
DEFF_FREQUENCIES = ("daily", "weekly")
#: Fewer aggregated periods than this and a frequency's covariance is not used.
#: Below it at BOTH frequencies the design effect is the conservative ``m``
#: (every sleeve counted as one observation stream).
MIN_PERIODS_FOR_DEFF = {"daily": 60, "weekly": 26}


# --------------------------------------------------------------------------
# The design effect
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DesignEffect:
    """``DEFF`` and how it was obtained, for the report."""

    deff: float
    m_sleeves: int
    m_active: int
    by_frequency: dict[str, dict[str, Any]]
    basis: str

    def effective(self, n_trades: int) -> float:
        """``n_trades / deff``: the trade count as a sample of independent trades."""
        return float(n_trades) / self.deff

    def to_dict(self) -> dict[str, Any]:
        return {
            "deff": self.deff,
            "m_sleeves": self.m_sleeves,
            "m_active": self.m_active,
            "by_frequency": {k: dict(v) for k, v in self.by_frequency.items()},
            "basis": self.basis,
            "formula": "DEFF = 1'S1 / trace(S) = 1 + (m-1)*rho_bar; max over daily and weekly sums; floor 1",
        }


def _bucket_sums(pnl: np.ndarray, timestamps_ns: np.ndarray, frequency: str) -> np.ndarray:
    days = timestamps_ns // _NS_PER_DAY
    if frequency == "daily":
        keys = days
    elif frequency == "weekly":
        keys = (days + _MONDAY_SHIFT_DAYS) // 7
    else:  # pragma: no cover - programming error
        raise ValueError(f"unknown frequency {frequency!r}")
    if keys.size == 0:
        return np.zeros((0, pnl.shape[1]), dtype=float)
    _, first = np.unique(keys, return_index=True)
    return np.add.reduceat(pnl, np.sort(first), axis=0)


def design_effect(pnl: np.ndarray, timestamps_ns: np.ndarray) -> DesignEffect:
    """Kish's design effect of summing ``m`` sleeves' P&L, from their co-movement.

    ``pnl`` is ``T x m``: per-bar P&L of each sleeve on a shared calendar
    ``timestamps_ns`` (UTC nanoseconds, ascending), zero where a sleeve has no
    bar. Returns ``DEFF = 1'S1 / trace(S)`` on daily and weekly sums, the larger
    of the two, floored at 1. Only sleeves whose aggregated P&L moves are
    counted (``m_active``); a sleeve that never traded carries no trades and
    no information. With fewer than two active sleeves ``DEFF`` is 1. When
    neither frequency has enough periods (:data:`MIN_PERIODS_FOR_DEFF`) the
    design effect is ``m_active``: the most conservative value the formula can
    take, stated as such.

    Deterministic: no resampling, so the same evaluation always has the same
    effective count.
    """
    pnl = np.asarray(pnl, dtype=float)
    ts = np.asarray(timestamps_ns, dtype=np.int64)
    if pnl.ndim != 2 or pnl.shape[0] != ts.size:
        raise ValueError("pnl must be T x m and match the timestamps")
    if not np.isfinite(pnl).all():
        raise ValueError("per-bar P&L contains non-finite values")
    m = int(pnl.shape[1])
    by_freq: dict[str, dict[str, Any]] = {}
    estimates: list[float] = []
    m_active_any = 0
    for freq in DEFF_FREQUENCIES:
        sums = _bucket_sums(pnl, ts, freq)
        n_periods = int(sums.shape[0])
        var = sums.var(axis=0, ddof=1) if n_periods > 1 else np.zeros(m)
        active = np.flatnonzero(var > 0.0)
        m_active_any = max(m_active_any, int(active.size))
        row: dict[str, Any] = {"n_periods": n_periods, "m_active": int(active.size)}
        if active.size < 2:
            row["deff"] = 1.0
            row["note"] = "fewer than two sleeves moved: nothing to correlate"
            by_freq[freq] = row
            estimates.append(1.0)
            continue
        if n_periods < MIN_PERIODS_FOR_DEFF[freq]:
            row["deff"] = None
            row["note"] = f"{n_periods} periods, below {MIN_PERIODS_FOR_DEFF[freq]}: not used"
            by_freq[freq] = row
            continue
        cov = np.cov(sums[:, active], rowvar=False, ddof=1)
        raw = float(cov.sum() / np.trace(cov))
        k = int(active.size)
        row["deff_raw"] = raw
        row["rho_bar"] = (raw - 1.0) / (k - 1)
        row["deff"] = max(1.0, raw)
        by_freq[freq] = row
        estimates.append(max(1.0, raw))
    if m_active_any < 2:
        return DesignEffect(1.0, m, m_active_any, by_freq, "fewer than two active sleeves")
    if not estimates:
        return DesignEffect(
            float(m_active_any), m, m_active_any, by_freq,
            "too few periods at every frequency: DEFF = m_active (conservative)",
        )
    deff = max(1.0, *estimates)
    winner = max(
        (f for f in DEFF_FREQUENCIES if by_freq[f].get("deff") is not None),
        key=lambda f: (float(by_freq[f]["deff"]), f == "weekly"),
    )
    return DesignEffect(deff, m, m_active_any, by_freq, f"max over frequencies -> {winner}")


# --------------------------------------------------------------------------
# The pooled evaluator
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PooledSleeve:
    """One series of a pooled candidate: its evaluator, its bars, its dataset."""

    instrument: str
    evaluator: Evaluator = field(repr=False)
    bar_index: pd.DatetimeIndex = field(repr=False)
    dataset_version_id: str

    def window_bars(self, window: DateWindow) -> pd.DatetimeIndex:
        lo = int(self.bar_index.searchsorted(window.start, side="left"))
        hi = int(self.bar_index.searchsorted(window.end, side="left"))
        return self.bar_index[lo:hi]


def pooled_dataset_version_id(dataset_version_ids: Sequence[str]) -> str:
    """A stable id for a set of dataset versions, independent of their order."""
    ids = sorted(str(v) for v in dataset_version_ids)
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate dataset version ids in a pool: {ids}")
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return f"pooled:{len(ids)}:{digest[:24]}"


@dataclass(slots=True)
class PooledEvaluator:
    """Runs one binding on every sleeve and returns ONE pooled evaluation.

    Sleeves are evaluated in instrument order (the order they are given in is
    normalised), so the pooled trade list, the calendar and every sum are the
    same on every run.
    """

    sleeves: tuple[PooledSleeve, ...]
    dataset_version_id: str = ""

    def __post_init__(self) -> None:
        if len(self.sleeves) < 2:
            raise ValueError("a pooled candidate needs at least two sleeves")
        names = [s.instrument.upper() for s in self.sleeves]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate instruments in a pool: {names}")
        self.sleeves = tuple(sorted(self.sleeves, key=lambda s: s.instrument.upper()))
        expected = pooled_dataset_version_id([s.dataset_version_id for s in self.sleeves])
        if not self.dataset_version_id:
            self.dataset_version_id = expected
        elif self.dataset_version_id != expected:
            raise ValueError(
                f"dataset_version_id {self.dataset_version_id!r} does not name these "
                f"sleeves' datasets (expected {expected!r})"
            )

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(s.instrument.upper() for s in self.sleeves)

    def __call__(self, params: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
        evaluations: list[WindowEvaluation] = []
        bars: list[np.ndarray] = []
        for sleeve in self.sleeves:
            ev = sleeve.evaluator(params, window)
            if ev.returns_basis != "period":
                raise ValueError(
                    f"{sleeve.instrument}: a pooled candidate needs period returns on the "
                    f"sleeve's bar calendar, got {ev.returns_basis!r}"
                )
            idx = sleeve.window_bars(window)
            if len(idx) != ev.returns.size:
                raise ValueError(
                    f"{sleeve.instrument}: {ev.returns.size} period returns for "
                    f"{len(idx)} bars in {window}; the sleeve's calendar is not its evaluator's"
                )
            evaluations.append(ev)
            bars.append(np.asarray(idx.asi8, dtype=np.int64))

        union = np.unique(np.concatenate(bars)) if bars else np.zeros(0, dtype=np.int64)
        pnl = np.zeros((union.size, len(self.sleeves)), dtype=float)
        for j, (ev, b) in enumerate(zip(evaluations, bars, strict=True)):
            pnl[np.searchsorted(union, b), j] = ev.returns
        returns = pnl.sum(axis=1)

        n_trades = int(sum(ev.n_trades for ev in evaluations))
        deff = design_effect(pnl, union)
        n_eff = deff.effective(n_trades)
        trades = tuple(
            sorted(
                (t for ev in evaluations for t in ev.trades),
                key=lambda t: (t.entry_time, t.exit_time, t.instrument),
            )
        )
        net_profit = float(math.fsum(ev.net_profit for ev in evaluations))
        content = {str(ev.meta.get("strategy_content_hash", "")) for ev in evaluations}
        if len(content) > 1:
            raise ValueError(
                f"the sleeves ran different strategies ({sorted(content)}); a pooled "
                "candidate is ONE binding of ONE document"
            )
        openings = [ev.meta.get("opening_equity") for ev in evaluations]
        meta: dict[str, Any] = {
            "pooled": True,
            "dataset_version_id": self.dataset_version_id,
            "strategy_content_hash": content.pop() if content else "",
            "engine_config_hash": hashlib.sha256(
                json.dumps(
                    [str(ev.meta.get("engine_config_hash", "")) for ev in evaluations]
                ).encode("utf-8")
            ).hexdigest(),
            "n_trades_effective": n_eff,
            "design_effect": deff.to_dict(),
            "calendar": "union of the sleeves' bar timestamps inside the window",
            "n_periods": int(union.size),
            "weighting": "equal risk: one equal-capital sleeve per instrument, P&L summed",
            "sleeves": [
                {
                    "instrument": s.instrument.upper(),
                    "dataset_version_id": s.dataset_version_id,
                    "n_trades": int(ev.n_trades),
                    "net_profit": float(ev.net_profit),
                    "n_bars": int(b.size),
                    "engine_config_hash": ev.meta.get("engine_config_hash"),
                    "cache_key": ev.meta.get("cache_key"),
                    "ledger_sha256": ev.meta.get("ledger_sha256"),
                }
                for s, ev, b in zip(self.sleeves, evaluations, bars, strict=True)
            ],
        }
        if all(o is not None for o in openings):
            # Sum of the sleeves' opening equity, so walk-forward measures the
            # pooled book's LOG growth (validation.ladder._growth_rate).
            meta["opening_equity"] = float(math.fsum(float(o) for o in openings if o is not None))
        notes = "; ".join(
            f"{s.instrument}: {ev.notes}"
            for s, ev in zip(self.sleeves, evaluations, strict=True)
            if ev.notes
        )
        return WindowEvaluation(
            window=window,
            params=dict(params),
            n_trades=n_trades,
            net_profit=net_profit,
            returns=returns,
            returns_basis="period",
            trades=trades,
            notes=notes,
            meta=meta,
        )


# --------------------------------------------------------------------------
# The holdout across constituents
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PooledHoldoutSegment(HoldoutSegment):
    """A composite segment whose research window may end BEFORE its holdout starts.

    ``research_end`` is the earliest constituent holdout start; ``holdout_start``
    the latest. The span between them is research data for some constituents and
    holdout for others, so the pooled ladder reads none of it.
    """

    research_end: pd.Timestamp | None = None
    constituents: tuple[str, ...] = ()

    @property
    def research_window(self) -> DateWindow:
        end = self.research_end if self.research_end is not None else self.holdout_start
        return DateWindow(f"research::{self.dataset_version_id}", self.data_start, end)

    def to_dict(self) -> dict[str, Any]:
        out = HoldoutSegment.to_dict(self)
        out["research_window"] = self.research_window.to_dict()
        out["constituents"] = list(self.constituents)
        out["note"] = (
            "composite of the constituents' own segments: research ends at the earliest "
            "constituent holdout start, holdout starts at the latest"
        )
        return out


class PooledHoldoutRegistry:
    """One composite holdout over several real ones (:class:`HoldoutLedger`).

    Every leakage check and every claim goes to the real registry underneath,
    once per constituent. ``db_path`` is the underlying registry's, so
    ``ValidationLadder.run_measuring`` still refuses a persistent one.
    """

    def __init__(self, inner: HoldoutRegistry, constituents: Sequence[str]) -> None:
        ids = tuple(sorted(str(c) for c in constituents))
        if len(ids) < 2:
            raise ValueError("a pooled holdout needs at least two constituents")
        self.inner = inner
        self.constituents = ids
        self.dataset_version_id = pooled_dataset_version_id(ids)
        self._tokens: dict[str, tuple[HoldoutToken, ...]] = {}
        segments = [inner.segment(c) for c in ids]  # raises UnknownHoldout
        research_end = min(s.holdout_start for s in segments)
        holdout_start = max(s.holdout_start for s in segments)
        data_start = max(s.data_start for s in segments)
        data_end = min(s.data_end for s in segments)
        if not data_start < research_end:
            raise ValueError("the constituents share no research span")
        if not holdout_start < data_end:
            raise ValueError("the constituents share no holdout span")
        self._segment = PooledHoldoutSegment(
            dataset_version_id=self.dataset_version_id,
            data_start=data_start,
            data_end=data_end,
            holdout_start=holdout_start,
            holdout_fraction=float(max(s.holdout_fraction for s in segments)),
            label="pooled: " + ", ".join(s.label or s.dataset_version_id for s in segments),
            research_end=research_end,
            constituents=ids,
        )

    @property
    def db_path(self) -> Path | None:
        return self.inner.db_path

    def segment(self, dataset_version_id: str) -> HoldoutSegment:
        if dataset_version_id == self.dataset_version_id:
            return self._segment
        raise UnknownHoldout(
            f"{dataset_version_id!r} is not this pool ({self.dataset_version_id})"
        )

    def assert_untouched(self, dataset_version_id: str, *windows: DateWindow) -> None:
        self.segment(dataset_version_id)
        for constituent in self.constituents:
            self.inner.assert_untouched(constituent, *windows)
        holdout = self._segment.window
        for window in windows:
            if window.overlaps(holdout):
                raise HoldoutLeak(
                    f"window {window} overlaps the pooled holdout {holdout}"
                )

    def claim(
        self,
        dataset_version_id: str,
        strategy_content_hash: str,
        *,
        strategy_id: str = "",
        experiment_id: str = "",
        actor: str = "",
        code_version: str = "",
        note: str = "",
        key_version: str | None = None,
    ) -> HoldoutToken:
        """Spend the look on EVERY constituent, after checking all of them.

        Checked first, for every constituent: the keys are comparable and the
        hash has not looked. Only then is each constituent claimed, in order,
        each claim written before any evaluation. A crash part-way leaves some
        constituents spent and none re-openable, which is the safe direction.
        """
        self.segment(dataset_version_id)
        for constituent in self.constituents:
            self.inner.assert_keys_comparable(constituent, key_version)
            existing = self.inner.consumption(constituent, strategy_content_hash)
            if existing is not None:
                raise HoldoutAlreadyConsumed(existing)
        tokens = tuple(
            self.inner.claim(
                constituent,
                strategy_content_hash,
                strategy_id=strategy_id,
                experiment_id=experiment_id,
                actor=actor,
                code_version=code_version,
                note=f"{note}; pooled {self.dataset_version_id}".strip("; "),
                key_version=key_version,
            )
            for constituent in self.constituents
        )
        composite = "pooled_" + hashlib.sha256(
            "|".join(t.token for t in tokens).encode("utf-8")
        ).hexdigest()[:32]
        self._tokens[composite] = tokens
        return HoldoutToken(
            token=composite, segment=self._segment, strategy_content_hash=strategy_content_hash
        )

    def record_outcome(self, token: HoldoutToken | str, outcome: dict[str, Any]) -> None:
        key = token.token if isinstance(token, HoldoutToken) else str(token)
        tokens = self._tokens.get(key)
        if tokens is None:
            raise UnknownHoldout(f"unknown pooled holdout token {key!r}")
        for t in tokens:
            self.inner.record_outcome(
                t, {**dict(outcome), "pooled_candidate": self.dataset_version_id}
            )

    def is_consumed(self, strategy_content_hash: str) -> dict[str, bool]:
        return {
            c: self.inner.is_consumed(c, strategy_content_hash) for c in self.constituents
        }


# --------------------------------------------------------------------------
# A ladder that does not spend the holdout
# --------------------------------------------------------------------------


class DeferredHoldoutRung(Rung):
    """RUNG 6, NOT RUN: the holdout is deferred to a separate, one-candidate step.

    Returns ``NOT_REACHED`` with a reason, so a candidate that clears rungs 0
    to 5 is reported INCOMPLETE (never PROMOTE) and nothing is claimed. Used by
    E-4's main stage: the pre-registration spends the holdout only for the single
    pre-chosen survivor, in a step of its own.
    """

    index = 6
    name = "HOLDOUT"
    provenance = Provenance.HOLDOUT

    def run(self, ctx: Any) -> RungResult:
        return RungResult(
            index=self.index,
            name=self.name,
            outcome=RungOutcome.NOT_REACHED,
            reason=(
                "holdout deferred: this run does not spend the one look; it is "
                "spent, if at all, in a separate step for one pre-chosen candidate"
            ),
            metrics={"holdout": "deferred", "window": ctx.holdout_window.to_dict()},
            provenance=self.provenance,
            is_evidence=self.is_evidence,
        )


def rungs_with_deferred_holdout() -> tuple[Rung, ...]:
    """Rungs 0 to 5 as the ladder runs them, then :class:`DeferredHoldoutRung`."""
    rungs = default_rungs()
    if rungs[-1].index != 6:  # pragma: no cover - the ladder's own shape
        raise RuntimeError("the default ladder no longer ends with the holdout rung")
    return (*rungs[:-1], DeferredHoldoutRung())
