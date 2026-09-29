"""The concrete :class:`~fiboki.validation.evaluation.Evaluator`: the real engine.

``validation/evaluation.py`` defines the ladder against a protocol -- "run THIS
parameterisation over THAT window and tell me what happened" -- and until now
nothing in the repository implemented it against
:mod:`fiboki.backtest.engine`. The reason was stated in that module's docstring:
``StrategyDocument.parameters`` declared sweep DOMAINS but nothing bound a chosen
value back into a rule, so ``{"rsi_period": 21}`` could not be turned into a
runnable strategy. :meth:`fiboki.strategy.dsl.StrategyDocument.bind` now does
that, and this module is the adapter the protocol was designed to receive.

What one evaluation is
----------------------
One call is one backtest of ONE binding of ONE document over ONE window, on one
instrument and timeframe, with one broker profile, against one dataset version.
It returns a :class:`~fiboki.validation.evaluation.WindowEvaluation` carrying
BOTH of the things the ladder needs and that a trade list alone cannot provide:

``returns`` on a ``"period"`` basis
    One element per bar of the window, on a calendar SHARED by every
    parameterisation of that window, because every parameterisation is run over
    the same bars. That is what makes the ``T x N`` trials matrix -- and
    therefore PBO, SPA, StepM and the correlation clustering behind the deflated
    Sharpe's ``N`` -- assemblable at all. Trade-basis returns cannot be aligned,
    and rung 5 correctly refuses them.

``trades``
    The real :class:`~fiboki.core.contracts.Trade` rows, so rung 4 can apply the
    cost, delay and deletion stresses to the actual fills rather than to a
    summary statistic.

Warmup, and why the window is still the window
----------------------------------------------
A strategy needs history before its first signal, and different bindings need
different amounts of it (``slow_ema=300`` needs three times the warmup of
``slow_ema=100``). So the engine is fed a PROLOGUE of bars before
``window.start`` and the adapter refuses to emit a signal dated before it. Two
consequences, both deliberate:

* every trade in the result was entered inside the window, so a fold cannot
  inherit a position from the fold before it; and
* the period-return calendar is exactly the window's bars, identical across
  bindings whatever prologue each of them needed.

Determinism and the cache
-------------------------
The engine is deterministic, so the same (binding, window, engine config,
dataset) is the same answer forever. The cache keys on a content hash of exactly
those things -- including the bound document's own content hash and a digest of
the bars -- and persists to disk, so a campaign that re-runs a rung, or a second
candidate that shares a fold, pays for each distinct evaluation once. Nothing
about the cache can change an answer: a cache hit reconstructs the same
``WindowEvaluation``, and ``tests/unit/test_engine_evaluator.py`` asserts that
against an uncached run.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fiboki.backtest.engine import (
    SIZING_POLICIES,
    SIZING_POLICY_V2,
    BacktestConfig,
    BacktestResult,
    BarContext,
    ConstructionPolicy,
    FixedFractionalSizer,
    run_backtest,
)
from fiboki.backtest.exits import BlackoutSource, ExitPolicy, exit_policy_from_document
from fiboki.core.contracts import Signal, Trade
from fiboki.core.enums import Direction, ExitReason, Provenance, Timeframe
from fiboki.core.money import FxRateSource, IdentityFxSource
from fiboki.portfolio.engine_policy import BacktestConstructionPolicy
from fiboki.risk.accounting import realised_portfolio_vol
from fiboki.sim.profiles import ExecutionProfile, get_profile
from fiboki.strategy.compiler import CompiledStrategy, compile_strategy
from fiboki.strategy.dsl import StrategyDocument
from fiboki.validation.evaluation import (
    Candidate,
    DateWindow,
    ParameterGrid,
    WindowEvaluation,
    canonical_params,
    params_key,
)

__all__ = [
    "EngineEvaluator",
    "EvaluationCache",
    "EvaluatorConfig",
    "UnfingerprintableBlackout",
    "WindowedStrategyRunner",
    "blackout_fingerprint",
    "frame_digest",
    "research_construction_policy",
]


#: ``RiskContextBuilder.vol_min_observations``: the paper runtime's threshold
#: below which realised portfolio vol is "unmeasured" (0.0, neutral).
RUNTIME_VOL_MIN_OBSERVATIONS = 20


def research_construction_policy() -> BacktestConstructionPolicy:
    """The portfolio construction research sizes with by default: the paper runtime's.

    ``construction_v2`` (``ConstructionConfig()``), the equal-risk allocator,
    PROBATIONARY / PAPER / health 1.0, no regime source (``unknown``), no
    conviction (inadmissible, plan D-A3), and realised portfolio vol measured
    by the SAME function the runtime's ``RiskContextBuilder`` calls, over the
    engine's own marked-to-market equity curve. ``EvaluatorConfig.risk_fraction``
    is a ceiling on the tier base risk, as ``SizingPolicy.risk_fraction`` is in
    paper.
    """
    return BacktestConstructionPolicy(
        realised_vol=partial(
            realised_portfolio_vol, min_observations=RUNTIME_VOL_MIN_OBSERVATIONS
        ),
        realised_vol_source=(
            "risk.accounting.realised_portfolio_vol(min_observations="
            f"{RUNTIME_VOL_MIN_OBSERVATIONS}) over the engine's equity curve"
        ),
    )


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvaluatorConfig:
    """Everything about the RUN that is not the strategy or the window.

    Every field lands in the fingerprint recorded on each evaluation, because a
    number produced under a different cost model or a different account currency
    is a different number and a report that cannot tell them apart is not
    evidence.
    """

    instrument: str
    timeframe: Timeframe
    initial_balance: float = 10_000.0
    #: GBP because the operator's account is GBP. Research used to run in USD
    #: while paper ran in GBP, so every monetary limit and every size differed
    #: between the two for the 30 of 41 instruments not quoted in GBP. A
    #: non-GBP-quoted instrument therefore needs a real rate source: see
    #: :func:`fiboki.validation.run.build_research_fx_source`.
    account_ccy: str = "GBP"
    risk_fraction: float = 0.01
    #: The sizing rule. ``fixed_fractional_v2`` prices the spread and the
    #: expected slippage of both legs into the risk per unit
    #: (:func:`fiboki.backtest.engine.stop_out_cost_per_unit`); v1 is kept only
    #: so an old configuration can be re-run and compared.
    sizing_policy: str = SIZING_POLICY_V2
    profile_name: str = "IG_REALISTIC"
    charge_financing: bool = True
    close_at_end_of_data: bool = True
    #: Extra bars of history handed to the engine before the window, ON TOP of
    #: the compiled warmup. Covers indicators whose warmup is a lower bound.
    warmup_margin_bars: int = 8
    #: Refuse a window that cannot hold at least this many post-warmup bars. A
    #: window that is nearly all warmup produces a number about nothing.
    min_window_bars: int = 30

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if not 0.0 < self.risk_fraction < 1.0:
            raise ValueError("risk_fraction must be in (0, 1)")
        if self.sizing_policy not in SIZING_POLICIES:
            raise ValueError(
                f"unknown sizing policy {self.sizing_policy!r}; known {SIZING_POLICIES}"
            )
        get_profile(self.profile_name)  # raises on an unknown profile

    @property
    def profile(self) -> ExecutionProfile:
        return get_profile(self.profile_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument.upper(),
            "timeframe": self.timeframe.value,
            "initial_balance": float(self.initial_balance),
            "account_ccy": self.account_ccy.upper(),
            "risk_fraction": float(self.risk_fraction),
            "sizing_policy": self.sizing_policy,
            "profile_name": self.profile_name.upper(),
            "charge_financing": bool(self.charge_financing),
            "close_at_end_of_data": bool(self.close_at_end_of_data),
            "warmup_margin_bars": int(self.warmup_margin_bars),
            "min_window_bars": int(self.min_window_bars),
        }


# --------------------------------------------------------------------------
# The engine adapter
# --------------------------------------------------------------------------


class WindowedStrategyRunner:
    """Adapts a compiled document to the engine, gated to one window.

    Indicators are computed ONCE over the whole prologue-plus-window frame. That
    is safe only because every indicator in the library is proved causal by
    ``tests/unit/test_indicator_causality.py``: the value on row i is identical
    whether it was computed over the whole frame or over ``frame[:i+1]``. The
    shortcut is taken knowingly and cited, not assumed.

    The window gate is the second half of the warmup story: the prologue exists
    so indicators are warm, NOT so the strategy can trade before the window it
    was asked about.
    """

    def __init__(
        self,
        compiled: CompiledStrategy,
        symbol: str,
        frame: pd.DataFrame,
        timeframe: Timeframe,
        window: DateWindow,
    ) -> None:
        self.compiled = compiled
        self.symbol = symbol
        self.timeframe = timeframe
        self.window = window
        self.prepared = compiled.prepare(frame)
        self._index = {ts: i for i, ts in enumerate(self.prepared.index)}
        self.signals_emitted = 0
        self.bars_offered = 0

    def on_bar(self, ctx: BarContext) -> Sequence[Signal]:
        ts = ctx.timestamp
        if not self.window.contains(ts):
            return ()
        idx = self._index.get(ts)
        if idx is None:  # pragma: no cover - single-instrument frames always hit
            return ()
        self.bars_offered += 1
        signal = self.compiled.generate_signal(
            self.prepared, idx, instrument=self.symbol, timeframe=self.timeframe
        )
        if signal is None:
            return ()
        self.signals_emitted += 1
        return (signal,)


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def frame_digest(frame: pd.DataFrame) -> str:
    """A content digest of the bars an evaluation may read.

    Part of every cache key, so a cache built against one set of bars can never
    answer a question about another -- even under the same dataset version id,
    which is a label and not a checksum.
    """
    cols = [c for c in ("open", "high", "low", "close") if c in frame.columns]
    arr = np.ascontiguousarray(frame[cols].to_numpy(dtype=np.float64))
    return hashlib.sha256(arr.tobytes() + frame.index.asi8.tobytes()).hexdigest()


class UnfingerprintableBlackout(ValueError):
    """A blackout source whose content cannot be hashed was given a cache."""


def blackout_fingerprint(source: Any) -> dict[str, Any] | None:
    """Content fingerprint of a blackout source, for provenance and cache keys.

    ``None`` means no source. An economic calendar (anything with
    ``all_events()``) is hashed over its events' canonical JSON, so two
    calendars with the same events share a fingerprint and a calendar that
    gains, loses or moves one event does not. A source that exposes neither
    ``all_events()`` nor ``fingerprint()`` is reported as ``unhashable``;
    :class:`EngineEvaluator` refuses to put such a source behind a cache,
    because two different ones would then share cache entries.
    """
    if source is None:
        return None
    kind = type(source).__name__
    all_events = getattr(source, "all_events", None)
    if callable(all_events):
        rows = [e.to_dict() for e in all_events()]
        blob = json.dumps(rows, sort_keys=True, default=str)
        return {
            "kind": kind,
            "n_events": len(rows),
            "events_sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
        }
    custom = getattr(source, "fingerprint", None)
    if callable(custom):
        return {"kind": kind, "fingerprint": custom()}
    return {"kind": kind, "content": "unhashable"}


class EvaluationCache:
    """Content-addressed store of completed evaluations.

    Append-only in practice: a key names the exact inputs, so a value under it
    can never need replacing. Corrupt or unreadable entries are treated as
    MISSES rather than errors, because the worst a miss can do is cost time,
    while trusting a half-written file could change a result.
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        assert self.directory is not None
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        payload = self._memory.get(key)
        if payload is None and self.directory is not None:
            path = self._path(key)
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):  # pragma: no cover - defensive
                    payload = None
                else:
                    self._memory[key] = payload
        if payload is None:
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def put(self, key: str, payload: Mapping[str, Any]) -> None:
        data = dict(payload)
        self._memory[key] = data
        if self.directory is None:
            return
        path = self._path(key)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        tmp.replace(path)  # atomic: a reader never sees a partial file

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "entries": len(self._memory)}


# --------------------------------------------------------------------------
# Serialisation of one evaluation
# --------------------------------------------------------------------------


def _trade_to_dict(t: Trade) -> dict[str, Any]:
    return {
        "instrument": t.instrument,
        "direction": t.direction.value,
        "size": t.size,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat(),
        "exit_reason": t.exit_reason.value,
        "gross_pnl": t.gross_pnl,
        "spread_cost": t.spread_cost,
        "commission": t.commission,
        "slippage_cost": t.slippage_cost,
        "financing_cost": t.financing_cost,
        "net_pnl": t.net_pnl,
        "account_ccy": t.account_ccy,
        "strategy_id": t.strategy_id,
        "bars_held": t.bars_held,
        "max_adverse_excursion": t.max_adverse_excursion,
        "max_favourable_excursion": t.max_favourable_excursion,
        "provenance": t.provenance.value,
        "fx_rate_used": t.fx_rate_used,
    }


def _trade_from_dict(raw: Mapping[str, Any]) -> Trade:
    return Trade(
        instrument=str(raw["instrument"]),
        direction=Direction(raw["direction"]),
        size=float(raw["size"]),
        entry_price=float(raw["entry_price"]),
        exit_price=float(raw["exit_price"]),
        entry_time=pd.Timestamp(raw["entry_time"]),
        exit_time=pd.Timestamp(raw["exit_time"]),
        exit_reason=ExitReason(raw["exit_reason"]),
        gross_pnl=float(raw["gross_pnl"]),
        spread_cost=float(raw["spread_cost"]),
        commission=float(raw["commission"]),
        slippage_cost=float(raw["slippage_cost"]),
        financing_cost=float(raw["financing_cost"]),
        net_pnl=float(raw["net_pnl"]),
        account_ccy=str(raw["account_ccy"]),
        strategy_id=str(raw["strategy_id"]),
        bars_held=int(raw["bars_held"]),
        max_adverse_excursion=float(raw["max_adverse_excursion"]),
        max_favourable_excursion=float(raw["max_favourable_excursion"]),
        provenance=Provenance(raw["provenance"]),
        fx_rate_used=float(raw["fx_rate_used"]),
    )


def _frame_price_basis(frame: pd.DataFrame) -> str:
    """The frame's single price basis, or ``assumed_mid`` when it carries none."""
    if "price_basis" not in frame.columns:
        return "assumed_mid"
    bases = sorted({str(getattr(b, "value", b)).lower() for b in frame["price_basis"].unique()})
    return bases[0] if len(bases) == 1 else "mixed:" + ",".join(bases)


def _evaluation_to_dict(ev: WindowEvaluation) -> dict[str, Any]:
    return {
        "window": ev.window.to_dict(),
        "params": dict(ev.params),
        "n_trades": int(ev.n_trades),
        "net_profit": float(ev.net_profit),
        "returns": [float(x) for x in ev.returns],
        "returns_basis": ev.returns_basis,
        "trades": [_trade_to_dict(t) for t in ev.trades],
        "notes": ev.notes,
        "meta": dict(ev.meta),
    }


def _evaluation_from_dict(raw: Mapping[str, Any], window: DateWindow) -> WindowEvaluation:
    return WindowEvaluation(
        window=window,
        params=dict(raw["params"]),
        n_trades=int(raw["n_trades"]),
        net_profit=float(raw["net_profit"]),
        returns=np.asarray(raw["returns"], dtype=float),
        returns_basis=str(raw["returns_basis"]),
        trades=tuple(_trade_from_dict(t) for t in raw["trades"]),
        notes=str(raw.get("notes", "")),
        meta=dict(raw.get("meta", {})),
    )


# --------------------------------------------------------------------------
# The evaluator
# --------------------------------------------------------------------------


@dataclass(slots=True)
class EngineEvaluator:
    """Runs :mod:`fiboki.backtest.engine` for the validation ladder.

    Construct once per (document, dataset, instrument, timeframe, broker
    profile); call it many times with different bindings and windows.
    """

    document: StrategyDocument
    frame: pd.DataFrame
    dataset_version_id: str
    config: EvaluatorConfig
    fx: FxRateSource = field(default_factory=IdentityFxSource)
    cache: EvaluationCache | None = None
    fx_label: str = "identity"
    #: An economic calendar for ``EventRestriction``. ``None`` means no blackout
    #: is applied at all — and note that a calendar with no dated events answers
    #: "not in blackout" for every bar in history, so supplying an empty one is
    #: the same as supplying none. See
    #: ``fiboki.marketstate.calendar.USER_ACTION_NOTE``.
    #:
    #: The source's CONTENT is part of :meth:`engine_fingerprint`, and so of
    #: :meth:`engine_config_hash` and every cache key: the same strategy on the
    #: same bars under a different calendar is a different question, and
    #: before this was keyed the cache would serve one calendar's answer for
    #: another's (``validation/run.py`` worked round it with a per-calendar
    #: cache subdirectory).
    blackout: BlackoutSource | None = None
    #: How the bars reached their price basis, when it was converted (for
    #: example ``{"from": "bid", "via": "bid_to_mid", "assumed_spread_pips":
    #: 0.9}``). Part of the engine fingerprint and so of every cache key.
    price_basis_lineage: Mapping[str, Any] = field(default_factory=dict)
    #: Portfolio construction on every decision bar. Defaults to the paper
    #: runtime's policy so research and paper size a signal identically;
    #: ``None`` is the flat ``risk_fraction`` path (``construction=none`` in the
    #: fingerprint), kept for regression pins and for comparison. Either way it
    #: is part of :meth:`engine_fingerprint` and so of every cache key.
    construction: ConstructionPolicy | None = field(
        default_factory=research_construction_policy
    )

    _price_basis: str = field(init=False, default="", repr=False)
    _blackout_fingerprint: dict[str, Any] | None = field(init=False, default=None, repr=False)
    _frame_digest: str = field(init=False, default="", repr=False)
    _bound: dict[str, StrategyDocument] = field(init=False, default_factory=dict, repr=False)
    _compiled: dict[str, CompiledStrategy] = field(init=False, default_factory=dict, repr=False)
    _engine_fingerprint: dict[str, Any] = field(init=False, default_factory=dict, repr=False)
    n_engine_runs: int = field(init=False, default=0)
    calls: list[tuple[str, str]] = field(init=False, default_factory=list, repr=False)

    def __post_init__(self) -> None:
        symbol = self.config.instrument.upper()
        if symbol not in self.document.universe:
            raise ValueError(
                f"{self.document.strategy_id}: {symbol} is not in the document's "
                f"universe {list(self.document.universe)}; running it anyway would "
                "validate a strategy nobody wrote"
            )
        if self.config.timeframe not in self.document.timeframes:
            raise ValueError(
                f"{self.document.strategy_id}: {self.config.timeframe.value} is not a "
                f"permitted timeframe {[t.value for t in self.document.timeframes]}"
            )
        if not isinstance(self.frame.index, pd.DatetimeIndex) or self.frame.index.tz is None:
            raise ValueError("bars must be indexed by tz-aware UTC timestamps")
        if not self.frame.index.is_monotonic_increasing:
            raise ValueError("bars must be sorted ascending")
        if not self.dataset_version_id:
            raise ValueError(
                "dataset_version_id is mandatory: an evaluation that cannot name the "
                "bytes it ran on is not reproducible and is not evidence"
            )
        self._frame_digest = frame_digest(self.frame)
        self._price_basis = _frame_price_basis(self.frame)
        self._engine_fingerprint = self._base_config("fingerprint_probe").fingerprint()
        self._blackout_fingerprint = blackout_fingerprint(self.blackout)
        if (
            self.cache is not None
            and self._blackout_fingerprint is not None
            and self._blackout_fingerprint.get("content") == "unhashable"
        ):
            raise UnfingerprintableBlackout(
                f"blackout source {type(self.blackout).__name__} exposes neither "
                "all_events() nor fingerprint(), so its content cannot enter the "
                "cache key; two different sources would share cached answers. "
                "Give it a fingerprint() or run without a cache."
            )

    # ------------------------------------------------------------ plumbing

    def _base_config(self, strategy_id: str) -> BacktestConfig:
        doc = self.document
        return BacktestConfig(
            initial_balance=float(self.config.initial_balance),
            account_ccy=self.config.account_ccy.upper(),
            profile=self.config.profile,
            max_concurrent=doc.position_management.max_concurrent_positions,
            max_per_instrument=doc.position_management.max_concurrent_positions,
            charge_financing=self.config.charge_financing,
            close_at_end_of_data=self.config.close_at_end_of_data,
            strategy_id=strategy_id,
            provenance=Provenance.BACKTEST,
            construction=self.construction,
        )

    def bind(self, params: Mapping[str, Any]) -> StrategyDocument:
        """The bound document for one grid point.

        The grid may address a SUBSET of the declared parameters (a campaign can
        choose to sweep three of six). The unswept ones take the document's own
        declared defaults, and the merge happens HERE, once, so the binding
        recorded on the evaluation is the complete one that actually ran --
        never a grid point that leaves the reader to guess the rest.
        """
        effective = {**self.document.default_values(), **dict(params)}
        key = params_key(effective)
        if key not in self._bound:
            self._bound[key] = self.document.bind(effective)
        return self._bound[key]

    def compiled_for(self, params: Mapping[str, Any]) -> CompiledStrategy:
        bound = self.bind(params)
        digest = bound.content_hash()
        if digest not in self._compiled:
            self._compiled[digest] = compile_strategy(bound)
        return self._compiled[digest]

    def engine_fingerprint(self) -> dict[str, Any]:
        """Everything about the execution model, minus the strategy's own name."""
        out = {k: v for k, v in self._engine_fingerprint.items() if k != "strategy_id"}
        out["evaluator"] = self.config.to_dict()
        out["fx_source"] = self.fx_label
        # What the bars ARE. ``assumed_mid`` means the frame carried no
        # ``price_basis`` column and the engine's assumption was taken; a BID
        # frame never gets this far (the engine refuses it), and a converted one
        # says ``synthetic_mid`` plus the conversion in ``price_basis_lineage``.
        out["price_basis"] = self._price_basis
        if self.price_basis_lineage:
            out["price_basis_lineage"] = dict(self.price_basis_lineage)
        # Only present when a source is set, so a no-calendar evaluation keeps
        # the hash (and the cache entries) it always had.
        if self._blackout_fingerprint is not None:
            out["blackout"] = dict(self._blackout_fingerprint)
        return out

    def engine_config_hash(self) -> str:
        blob = json.dumps(self.engine_fingerprint(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def cache_key(self, params: Mapping[str, Any], window: DateWindow) -> str:
        """Content hash of everything that can change the answer.

        The window's NAME is deliberately absent: the ladder asks about the same
        span under several names (a fold's train window and a later full-sample
        run can coincide), and those are the same question. Everything that is
        not the same question -- the bound strategy, the bytes, the cost model --
        is present.
        """
        bound = self.bind(params)
        payload = {
            # schema 2: the engine gained the DSL's full exit vocabulary
            # (multi-leg take profits, trailing stops, breakeven, time stops,
            # cooldown, reversal, event blackouts). Every schema-1 entry was
            # computed by an engine that could only honour the stop and the
            # first target, so those answers are about a different strategy and
            # must never be served for this question.
            "schema": 2,
            "strategy_content_hash": bound.content_hash(),
            "dataset_version_id": self.dataset_version_id,
            "frame_digest": self._frame_digest,
            "engine_config_hash": self.engine_config_hash(),
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
        }
        blob = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ evaluate

    def __call__(
        self, params: Mapping[str, Any], window: DateWindow
    ) -> WindowEvaluation:
        self.calls.append((params_key(params), window.name))
        key = self.cache_key(params, window)
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                return _evaluation_from_dict(cached, window)
        evaluation = self._evaluate(params, window, key)
        if self.cache is not None:
            self.cache.put(key, _evaluation_to_dict(evaluation))
        return evaluation

    def _evaluate(
        self, params: Mapping[str, Any], window: DateWindow, key: str
    ) -> WindowEvaluation:
        bound = self.bind(params)
        compiled = self.compiled_for(params)
        symbol = self.config.instrument.upper()

        index = self.frame.index
        lo = int(index.searchsorted(window.start, side="left"))
        hi = int(index.searchsorted(window.end, side="left"))  # half-open [start, end)
        n_window = hi - lo
        if n_window < self.config.min_window_bars:
            raise ValueError(
                f"window {window} holds {n_window} bars of {self.config.instrument} "
                f"{self.config.timeframe.value}, below the "
                f"{self.config.min_window_bars} this evaluator requires. A number "
                "computed from that is not about the market."
            )
        prologue = compiled.warmup_period + int(self.config.warmup_margin_bars)
        start = max(0, lo - prologue)
        fed = self.frame.iloc[start:hi]
        if lo - start < compiled.warmup_period:
            available = lo - start
            # Not fatal, but it means the early part of the window ran cold, and
            # a reader must be told rather than shown a number that looks whole.
            warmup_note = (
                f"PARTIAL WARMUP: {available} prologue bars for a "
                f"{compiled.warmup_period}-bar warmup; the first "
                f"{compiled.warmup_period - available} bars of this window could "
                "not produce a signal"
            )
        else:
            warmup_note = ""

        runner = WindowedStrategyRunner(
            compiled, symbol, fed, self.config.timeframe, window
        )
        config = self._base_config(bound.strategy_id)
        policy = exit_policy_from_document(bound)
        # ``price_basis`` travels with the OHLC so the ENGINE checks it: a BID
        # frame is refused there, not silently traded as mid.
        columns = ["open", "high", "low", "close"]
        if "price_basis" in fed.columns:
            columns.append("price_basis")
        result = run_backtest(
            data={symbol: fed[columns]},
            config=config,
            strategy=runner,
            # Sizing is priced on the SAME frictions the fills charge: the
            # evaluator's configured profile, named, not left to the default.
            sizer=FixedFractionalSizer(
                risk_fraction=float(self.config.risk_fraction),
                policy_id=self.config.sizing_policy,
                cost_profile=self.config.profile,
            ),
            fx=self.fx,
            exit_policy=policy,
            exit_series=self._exit_series(symbol, runner, policy),
            blackout=self.blackout,
        )
        self.n_engine_runs += 1

        returns = self._period_returns(result, window)
        trades = tuple(result.trades)
        net_profit = float(sum(t.net_pnl for t in trades))
        meta = {
            "dataset_version_id": self.dataset_version_id,
            "frame_digest": self._frame_digest,
            "engine_config_hash": self.engine_config_hash(),
            "engine_config": self.engine_fingerprint(),
            "strategy_id": bound.strategy_id,
            "strategy_content_hash": bound.content_hash(),
            "template_content_hash": self.document.content_hash(),
            "binding": dict(bound.binding or {}),
            "warmup_period": int(compiled.warmup_period),
            "prologue_bars": int(lo - start),
            "window_bars": int(n_window),
            "signals_emitted": int(runner.signals_emitted),
            "rejections": dict(result.rejections),
            "bankrupt": bool(result.bankrupt),
            "ledger_sha256": result.ledger_sha256(),
            "leg_ledger_sha256": result.leg_ledger_sha256(),
            "partial_exits": int(result.partial_exits),
            "exit_policy": result.exit_policy_fingerprint,
            "blackout_source": type(self.blackout).__name__ if self.blackout else None,
            "cache_key": key,
            "costs": result.costs.as_dict(),
            # The equity the run started from, so walk-forward can measure LOG
            # growth rather than money per day (validation.ladder._growth_rate).
            "opening_equity": float(self.config.initial_balance),
            "sizing": result.config_fingerprint.get("sizing"),
            # How sizes were decided: the construction policy (or "none") and
            # the size-and-reason ledger's hash and refusal count.
            "construction": result.config_fingerprint.get("construction"),
            "allocation_ledger_sha256": (
                result.allocation_ledger_sha256() if result.allocation_ledger else None
            ),
            "allocation_decisions": len(result.allocation_ledger),
            "allocation_dropped": int(result.rejections.get("allocation_dropped", 0)),
        }
        if warmup_note:
            meta["warmup_warning"] = warmup_note
        return WindowEvaluation(
            window=window,
            params=canonical_params(params),
            n_trades=len(trades),
            net_profit=net_profit,
            returns=returns,
            returns_basis="period",
            trades=trades,
            notes=warmup_note,
            meta=meta,
        )

    @staticmethod
    def _exit_series(
        symbol: str, runner: WindowedStrategyRunner, policy: ExitPolicy
    ) -> dict[str, pd.DataFrame] | None:
        """The indicator columns the exit policy trails on, already computed.

        ``runner.prepared`` is the same frame the strategy reads, indexed
        identically to the OHLC the engine is given, so a chandelier reads the
        ATR of the bar it is trailing and not of some neighbouring bar. The
        engine re-checks the index alignment and refuses a mismatch.
        """
        needed = policy.needs_series
        if not needed:
            return None
        missing = [c for c in needed if c not in runner.prepared.columns]
        if missing:
            raise KeyError(
                f"the exit policy trails on {missing}, which the compiled strategy "
                "does not produce. The document declares a trailing operand whose "
                "indicator is not required by any rule -- compile_strategy should "
                "have built it."
            )
        return {symbol: runner.prepared[list(needed)]}

    def _period_returns(self, result: BacktestResult, window: DateWindow) -> np.ndarray:
        """Per-bar equity change over the WINDOW's bars, in the account currency.

        Restricted to the window so that every parameterisation produces the same
        length on the same calendar, which is the precondition for the trials
        matrix rungs 3 and 5 are built on. Absolute rather than fractional so
        that the sum is the net profit exactly, and a reader can add the column
        up and check it.
        """
        curve = result.equity_curve
        if curve.empty:  # pragma: no cover - engine always writes one row per bar
            return np.zeros(0, dtype=float)
        mask = (curve.index >= window.start) & (curve.index < window.end)
        equity = curve.loc[mask, "equity"].to_numpy(dtype=float)
        if equity.size == 0:  # pragma: no cover - guarded by min_window_bars
            return np.zeros(0, dtype=float)
        opening = float(self.config.initial_balance)
        before = curve.loc[curve.index < window.start, "equity"]
        if not before.empty:
            opening = float(before.iloc[-1])
        return np.diff(equity, prepend=opening)

    # --------------------------------------------------------------- sweep

    def sweep(
        self, grid: ParameterGrid, window: DateWindow
    ) -> list[WindowEvaluation]:
        """Evaluate every declared grid point on one window, in grid order.

        Grid order is part of the contract: :meth:`select` breaks ties by taking
        the FIRST point, so a sweep's winner does not depend on dict iteration
        order or on which worker finished first. That is what makes the
        parameterisation the holdout registry is keyed on reproducible.
        """
        return [self(point, window) for point in grid.points()]

    @staticmethod
    def select(
        results: Sequence[WindowEvaluation], metric: str = "sharpe"
    ) -> int:
        """Index of the best evaluation under ``metric``. Ties go to the FIRST.

        Identical rule to :meth:`fiboki.validation.ladder.LadderContext.select`,
        exposed here so a campaign can fit-and-transfer outside the ladder and
        get the same answer the ladder would have got.
        """
        if not results:
            raise ValueError("cannot select from an empty sweep")
        return max(range(len(results)), key=lambda i: (results[i].metric(metric), -i))

    def fit_and_transfer(
        self,
        grid: ParameterGrid,
        train: DateWindow,
        test: DateWindow,
        *,
        metric: str = "sharpe",
    ) -> dict[str, Any]:
        """Sweep on ``train``, select, then evaluate THAT selection on ``test``.

        The one operation V1's walk-forward left out. Returned as data so a
        caller can check the transfer happened rather than take it on trust.
        """
        if train.overlaps(test):
            raise ValueError(
                f"train window {train} overlaps test window {test}; the transfer "
                "would be measured against data the selection had already seen"
            )
        trained = self.sweep(grid, train)
        chosen = self.select(trained, metric)
        selected = dict(trained[chosen].params)
        return {
            "train": train.to_dict(),
            "test": test.to_dict(),
            "selected_params": selected,
            "in_sample": trained[chosen],
            "out_of_sample": self(selected, test),
            "n_trials": len(trained),
        }

    # ----------------------------------------------------------- candidate

    def candidate(self, **kwargs: Any) -> Candidate:
        """The ladder's view of this evaluator's strategy.

        Keyed on the DEFAULT BINDING's content hash, not the template's: the
        holdout registry spends one look per strategy, and two bindings of one
        template are two strategies. Using the template hash would let every
        binding share -- and therefore burn -- a single look.
        """
        base = Candidate.from_document(self.document, **kwargs)
        return Candidate(
            strategy_id=base.strategy_id,
            content_hash=self.document.bind_defaults().content_hash(),
            default_params=base.default_params,
            grid=base.grid,
            document=self.document,
        )
