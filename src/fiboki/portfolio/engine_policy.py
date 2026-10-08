"""Portfolio construction for the backtest engine: the paper runtime's sizing path.

The paper runtime (``workers/runtime.SignalEvaluator``) allocates each bar's
signals through :class:`~fiboki.portfolio.construction.PortfolioConstructor`
against the venue's own book and then sizes each accepted one ONCE at
``tier base risk x weight``. The backtest engine used a flat
``risk_fraction``, so research and paper answered "how big" differently for the
same signal on the same book. This module closes that gap.

Why a policy object and not a call from the engine
--------------------------------------------------
``backtest`` (rank 50) may not import ``portfolio`` (rank 60):
``tests/unit/test_layering.py``. The engine therefore declares
:class:`fiboki.backtest.engine.ConstructionPolicy` and this module satisfies
it. Nothing here re-decides an allocation: :meth:`BacktestConstructionPolicy.
allocate` builds the same :class:`CandidateSignal` list and the same
:class:`PortfolioSnapshot` the runtime builds, and hands both to the one
``PortfolioConstructor.allocate``.

Each helper below mirrors, expression for expression, the runtime code named in
its docstring. ``workers/`` is outside this change, so the runtime still calls
its own copies; ``tests/integration/test_construction_parity.py`` drives both
paths over the same bars and demands byte-identical size and reason ledgers,
which is what holds the copies together. (Pointing the runtime at these helpers
is a follow-up in ``workers/``; the parity test is the guard until then.)

What a backtest cannot have, stated
-----------------------------------
* **Conviction**: none, by construction. Historical LLM convictions are
  inadmissible (plan D-A3), so every candidate carries ``conviction=None``, the
  conviction step reads ``missing`` and multiplies by exactly 1.0. There is no
  parameter anywhere on this path through which a conviction could arrive
  (``tests/unit/test_engine_construction.py`` asserts it).
* **Regime**: the engine has no regime source, so unless one is supplied every
  candidate's regime is ``unknown`` and ``regime_scalars["unknown"]`` (0.6)
  applies, exactly as it does on a paper session run without a market-state
  engine. The fingerprint says which.
* **Lifecycle, health, tier**: a backtest has no lifecycle service and no
  forward record, so they are constants of the policy (PAPER, 1.0,
  PROBATIONARY by default: the runtime's own defaults) and fingerprinted.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from typing import Any

import pandas as pd

from fiboki.backtest.engine import AllocationDecision, ConstructionRequest
from fiboki.backtest.position import BarSlice
from fiboki.core.contracts import AccountState, Position
from fiboki.core.enums import StrategyLifecycle
from fiboki.core.instruments import Instrument
from fiboki.core.instruments import get as get_instrument
from fiboki.core.tier import TierReading
from fiboki.portfolio.construction import (
    Allocation,
    CandidateSignal,
    ConstructionConfig,
    CorrelationMatrix,
    PortfolioConstructor,
    PortfolioSnapshot,
    StrategyTier,
    VolatilityParityAllocator,
)
from fiboki.sim.profiles import ExecutionProfile

__all__ = [
    "CONVICTION_SOURCE_NONE",
    "REGIME_SOURCE_NONE",
    "BacktestConstructionPolicy",
    "allocation_record",
    "book_snapshot",
    "construction_config_digest",
    "instrument_annualised_vol",
    "open_risk_by_instrument",
    "position_cost_per_unit",
]

#: Stamped on every backtest construction fingerprint.
CONVICTION_SOURCE_NONE = (
    "none: historical LLM convictions are inadmissible in a backtest (plan D-A3); "
    "the conviction step reads 'missing' and multiplies by 1.0"
)
REGIME_SOURCE_NONE = (
    "none: the engine has no regime source, every candidate is 'unknown' and "
    "regime_scalars['unknown'] applies"
)
_VOL_UNMEASURED = "unmeasured: realised portfolio vol reads 0.0, vol targeting is neutral"


# --------------------------------------------------------------------------
# Pure helpers, each a mirror of the runtime expression it names
# --------------------------------------------------------------------------


def instrument_annualised_vol(history: Callable[[str], pd.DataFrame], symbol: str) -> float:
    """Annualised close-to-close vol of the bars seen so far.

    Mirrors ``SignalEvaluator._vol``: 10% when the history is unreadable or has
    fewer than 20 returns; otherwise the last 250 returns, annualised by the
    median bar spacing over the WHOLE history, scaled to trading days (5/7).
    Read only by the volatility-parity allocator.
    """
    try:
        closes = history(symbol)["close"].astype(float)
    except Exception:
        return 0.10
    rets = closes.ffill().pct_change(fill_method=None).dropna().tail(250)
    if len(rets) < 20:
        return 0.10
    index = closes.index
    periods = 252.0
    if isinstance(index, pd.DatetimeIndex) and len(index) > 2:
        step = pd.Series(index).diff().dropna().median()
        if step > pd.Timedelta(0):
            periods = max(1.0, (pd.Timedelta(days=365.25) / step) * (5.0 / 7.0))
    vol = float(rets.std(ddof=1)) * math.sqrt(periods)
    return vol if math.isfinite(vol) and vol > 0 else 0.10


def position_cost_per_unit(
    pos: Position, instrument: Instrument, cost_profile: ExecutionProfile | None
) -> float:
    """The sizing rule's stop-out cost for an OPEN position, price units.

    Mirrors ``RiskContextBuilder._position_cost_per_unit``: the profile's full
    spread at the position's ENTRY hour and entry price plus two expected
    slippages; ``0`` with no profile (``fixed_fractional_v1``).
    """
    if cost_profile is None:
        return 0.0
    entry_time = pd.Timestamp(pos.entry_time)
    spread = cost_profile.spread_price(instrument, int(entry_time.hour), float(pos.entry_price))
    return float(spread + 2.0 * cost_profile.expected_slippage_price(instrument))


def open_risk_by_instrument(
    positions: Sequence[Position],
    rate: Callable[[str], float],
    cost_profile: ExecutionProfile | None = None,
) -> dict[str, float]:
    """Risk to stop per instrument, ACCOUNT currency, cost-inclusive.

    Mirrors ``RiskContextBuilder.open_risk_by_instrument``: ``(|entry - stop| +
    stop-out cost) x size x contract_size x rate(quote)``, summed per instrument
    in book order. The same definition as a plan's ``risk_amount``.
    """
    out: dict[str, float] = {}
    for pos in positions:
        instrument = get_instrument(pos.instrument)
        r = float(rate(instrument.quote))
        out[pos.instrument] = out.get(pos.instrument, 0.0) + (
            (abs(pos.entry_price - pos.stop_loss)
             + position_cost_per_unit(pos, instrument, cost_profile))
            * pos.size
            * instrument.contract_size
            * r
        )
    return out


def book_snapshot(
    *,
    account: AccountState,
    positions: Sequence[Position],
    as_of: pd.Timestamp,
    rate: Callable[[str], float],
    correlation: CorrelationMatrix,
    realised_portfolio_vol: float,
    regime_of: Callable[[str], str] | None = None,
    cost_profile: ExecutionProfile | None = None,
) -> PortfolioSnapshot:
    """The :class:`PortfolioSnapshot` of a book, as the runtime builds it.

    Mirrors ``RiskContextBuilder.snapshot``: signed notionals per instrument,
    strategy and net currency (base +, quote -) at ``rate(quote)``; margin
    available ``max(0, equity - margin_used)``; the book-level regime read off
    the FIRST open position (``unknown`` with no source or an empty book); and
    the open risk to stop MEASURED (cost-inclusive under ``cost_profile``), so
    the open-risk budgets run.
    """
    positions = tuple(positions)
    instrument_exposure: dict[str, float] = {}
    strategy_exposure: dict[str, float] = {}
    currency_exposure: dict[str, float] = {}
    for pos in positions:
        instrument = get_instrument(pos.instrument)
        r = float(rate(instrument.quote))
        notional = (pos.entry_price * pos.size * instrument.contract_size * r) * pos.direction.sign
        instrument_exposure[pos.instrument] = instrument_exposure.get(pos.instrument, 0.0) + notional
        strategy_exposure[pos.strategy_id] = strategy_exposure.get(pos.strategy_id, 0.0) + notional
        currency_exposure[instrument.base] = currency_exposure.get(instrument.base, 0.0) + notional
        currency_exposure[instrument.quote] = (
            currency_exposure.get(instrument.quote, 0.0) - notional
        )
    return PortfolioSnapshot(
        account=account,
        as_of=as_of,
        open_positions=positions,
        instrument_exposure=instrument_exposure,
        strategy_exposure=strategy_exposure,
        currency_exposure=currency_exposure,
        instrument_correlation=correlation,
        realised_portfolio_vol=realised_portfolio_vol,
        margin_available=max(0.0, account.equity - account.margin_used),
        regime=(
            regime_of(positions[0].instrument)
            if regime_of is not None and positions
            else "unknown"
        ),
        open_risk_by_instrument=open_risk_by_instrument(positions, rate, cost_profile),
    )


def allocation_record(allocation: Allocation) -> dict[str, Any]:
    """The compact "why this size" row for one allocation. JSON-serialisable.

    ``trace`` is EVERY step that ran, in order, including the ones whose factor
    was exactly 1.0 and the batch passes, as ``[step, factor, detail]``. The
    signal id is omitted on purpose: it is a random UUID and would make two
    identical decisions compare unequal.
    """
    c = allocation.candidate
    return {
        "instrument": c.symbol,
        "strategy_id": c.strategy_id,
        "direction": c.signal.direction.value,
        "tier": c.tier.value,
        "base_risk_pct": allocation.base_risk_pct,
        "weight": allocation.weight,
        "risk_pct": allocation.risk_pct,
        "dropped": allocation.dropped,
        "drop_reason": allocation.drop_reason,
        "trace": [[r.step, r.factor, r.detail] for r in allocation.trace],
    }


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(_canonical(k)): _canonical(v) for k, v in sorted(
            value.items(), key=lambda kv: str(_canonical(kv[0]))
        )}
    if isinstance(value, frozenset | set):
        return sorted(str(_canonical(v)) for v in value)
    if isinstance(value, tuple | list):
        return [_canonical(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _canonical(getattr(value, f.name)) for f in fields(value)}
    return value


def construction_config_digest(config: ConstructionConfig) -> str:
    """SHA-256 of every field of a :class:`ConstructionConfig`, canonically.

    ``config.version`` names the policy generation; this names the NUMBERS, so
    two runs with the same version but a different drawdown band never share a
    fingerprint (or an evaluation cache entry).
    """
    blob = json.dumps(_canonical(config), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# The policy the engine is handed
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestConstructionPolicy:
    """Satisfies :class:`fiboki.backtest.engine.ConstructionPolicy`.

    The defaults are the paper runtime's: ``ConstructionConfig()``
    (construction_v2), the equal-risk allocator, PROBATIONARY, PAPER, health
    1.0, no regime source. ``realised_vol`` is injected by the composition
    root (``validation.engine_evaluator.research_construction_policy`` wires
    ``risk.accounting.realised_portfolio_vol``, the function the runtime calls;
    ``portfolio`` may not import ``risk``). Left ``None``, realised vol is
    unmeasured and vol targeting is neutral, and the fingerprint says so.
    """

    config: ConstructionConfig = field(default_factory=ConstructionConfig)
    allocator: str = "equal_risk"
    tier: StrategyTier = StrategyTier.PROBATIONARY
    lifecycle: StrategyLifecycle = StrategyLifecycle.PAPER
    health: float = 1.0
    #: Instrument correlation. ``None``: unmeasured, every pair of different
    #: instruments reads ``config.unmeasured_correlation`` (0.30), which is what
    #: a single-instrument paper session measures too.
    correlation: CorrelationMatrix | None = None
    #: ``equity curve -> annualised vol`` (a fraction), or ``None``.
    realised_vol: Callable[[pd.Series], float] | None = field(default=None, compare=False)
    realised_vol_source: str = _VOL_UNMEASURED
    #: ``(instrument, bar slice) -> regime label``, or ``None`` (``unknown``).
    regime_source: Callable[[str, BarSlice], str] | None = field(default=None, compare=False)
    regime_source_label: str = REGIME_SOURCE_NONE

    def __post_init__(self) -> None:
        if not 0.0 <= self.health <= 1.0:
            raise ValueError("health must lie in [0, 1]")
        if self.realised_vol is not None and self.realised_vol_source == _VOL_UNMEASURED:
            raise ValueError("a realised_vol function needs a realised_vol_source label")
        if self.regime_source is not None and self.regime_source_label == REGIME_SOURCE_NONE:
            raise ValueError("a regime_source needs a regime_source_label")
        PortfolioConstructor(self.allocator, self.config)  # raises on an unknown allocator

    # -- the engine's two calls ------------------------------------------

    def fingerprint(self) -> dict[str, object]:
        cfg = self.config
        corr = self.correlation
        return {
            "construction_version": cfg.version,
            "config_sha256": construction_config_digest(cfg),
            "allocator": self.allocator,
            "tier": self.tier.value,
            "lifecycle": self.lifecycle.value,
            "health": self.health,
            **cfg.conviction.stamp(),
            "conviction_source": CONVICTION_SOURCE_NONE,
            "regime_source": self.regime_source_label,
            "instrument_correlation": (
                f"unmeasured (unmeasured_correlation={cfg.unmeasured_correlation})"
                if corr is None
                else "supplied:" + hashlib.sha256(
                    json.dumps(_canonical(corr), sort_keys=True).encode("utf-8")
                ).hexdigest()
            ),
            "realised_portfolio_vol": self.realised_vol_source,
            "instrument_vol": (
                "measured: SignalEvaluator._vol over the bars seen so far"
                if self._reads_instrument_vol
                else f"not computed: the {self.allocator} allocator does not read it"
            ),
            "risk_ceiling": "sizer.risk_fraction",
        }

    def allocate(self, request: ConstructionRequest) -> tuple[AllocationDecision, ...]:
        constructor = self._constructor(request.risk_ceiling_fraction)
        view = request.view
        regime_of: Callable[[str], str] | None = None
        if self.regime_source is not None:
            source = self.regime_source

            def regime_of(symbol: str) -> str:
                return source(symbol, view)

        candidates = [
            CandidateSignal(
                signal=signal,
                annualised_vol=(
                    instrument_annualised_vol(request.history, signal.instrument)
                    if self._reads_instrument_vol
                    # The CandidateSignal default. Only the volatility-parity
                    # allocator reads the field; it reaches no weight and no
                    # ledger row otherwise, and the estimate costs a pass over
                    # the whole history per signal.
                    else 0.10
                ),
                health=self.health,
                lifecycle=self.lifecycle,
                tier=self.tier,
                regime=regime_of(signal.instrument) if regime_of is not None else None,
                # Inadmissible in a backtest (plan D-A3). Not a default: a
                # literal, asserted over the AST.
                conviction=None,
            )
            for signal in request.signals
        ]
        snapshot = book_snapshot(
            account=request.account,
            positions=request.open_positions,
            as_of=request.as_of,
            rate=request.rate,
            correlation=(
                self.correlation
                if self.correlation is not None
                else CorrelationMatrix(default=self.config.unmeasured_correlation)
            ),
            realised_portfolio_vol=self._realised_vol(request),
            regime_of=regime_of,
            cost_profile=request.sizing_cost_profile,
        )
        result = constructor.allocate(candidates, snapshot)
        out: list[AllocationDecision] = []
        for signal in request.signals:
            allocation = result.allocation_for(signal.signal_id)
            if allocation is None:  # pragma: no cover - allocate covers every candidate
                raise RuntimeError(f"no allocation for {signal.signal_id}")
            out.append(
                AllocationDecision(
                    signal_id=signal.signal_id,
                    base_risk_pct=allocation.base_risk_pct,
                    weight=allocation.weight,
                    dropped=allocation.dropped,
                    drop_reason=allocation.drop_reason,
                    record=allocation_record(allocation),
                )
            )
        return tuple(out)

    # -- internals -------------------------------------------------------

    @property
    def _reads_instrument_vol(self) -> bool:
        return self.allocator == VolatilityParityAllocator.name

    def _constructor(self, ceiling_fraction: float | None) -> PortfolioConstructor:
        """The constructor with the sizer's risk fraction as a CEILING on the tier.

        Mirrors ``SignalEvaluator.__init__``: ``ceiling = risk_fraction * 100``,
        combined with any configured ceiling by ``min``.
        """
        config = self.config
        if ceiling_fraction is not None:
            ceiling = ceiling_fraction * 100.0
            current = config.risk_ceiling_pct
            config = replace(
                config,
                risk_ceiling_pct=ceiling if current is None else min(current, ceiling),
            )
        # T1 (shadow only). Irrelevant to the size: with no conviction reading
        # the conviction factor is 1.0 whatever the tier.
        return PortfolioConstructor(
            self.allocator, config, agent_tier=TierReading.default("backtest")
        )

    def _realised_vol(self, request: ConstructionRequest) -> float:
        """Mirrors ``RiskContextBuilder.realised_vol``: 0.0 when unmeasured or on error."""
        if self.realised_vol is None:
            return 0.0
        try:
            return float(self.realised_vol(request.equity_curve()))
        except Exception:
            return 0.0
