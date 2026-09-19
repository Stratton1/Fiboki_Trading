"""The evaluation surface the validation ladder is defined against.

The ladder does not know how a strategy is turned into trades. It knows only
that *something* can answer the question "run THIS parameterisation over THIS
window and tell me what happened". That question is the
:class:`Evaluator` protocol, and everything the ladder does is expressed in
terms of it.

Why an indirection rather than calling :func:`fiboki.backtest.engine.run_backtest`
directly
--------------------------------------------------------------------------------
Because the thing that makes V2's walk-forward honest is that the *parameters
selected on the train window are the ones evaluated on the test window*. That
requires a materialisation step -- turning a ``StrategyDocument`` plus a
parameter mapping into an executable strategy -- which the V2 DSL does not yet
have: ``StrategyDocument.parameters`` declares sweep DOMAINS, but nothing binds a
chosen value back into a rule's literal threshold, so there is currently no way
to turn ``{"rsi_period": 21}`` into a runnable strategy. Until that binder
exists, the ladder is wired through this protocol: the production wiring is then
ONE adapter (materialise the document, call ``run_backtest``, return a
``WindowEvaluation``) rather than a rewrite of the ladder, and the ladder's own
logic is testable against evaluators whose truth is known exactly.

Return conventions
------------------
``WindowEvaluation.returns`` is the series every statistic is computed from, and
``returns_basis`` says what one element MEANS:

``"period"``
    one element per bar in the window, on a calendar shared by every
    parameterisation of that window. This is the basis the deflation rung needs:
    a ``T x N`` trials matrix can only be assembled when the columns are aligned.
``"trade"``
    one element per closed trade. Cheaper to produce, but two parameterisations
    produce different-length series, so PBO / SPA / clustering cannot be run and
    the deflation rung fails CLOSED rather than guessing.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd

from fiboki.core.contracts import Trade
from fiboki.stats.sharpe import sharpe_moments

__all__ = [
    "Candidate",
    "DateWindow",
    "Evaluator",
    "ParameterGrid",
    "WindowEvaluation",
    "canonical_params",
    "params_key",
]


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


def _ts(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


@dataclass(frozen=True, slots=True)
class DateWindow:
    """A half-open UTC interval ``[start, end)``.

    Half-open on purpose: consecutive windows produced by :meth:`slices` tile the
    parent exactly, with no bar belonging to two of them. An inclusive end would
    put the boundary bar in both the train and the test window, which is a small
    leak that compounds across folds.
    """

    name: str
    start: pd.Timestamp
    end: pd.Timestamp

    def __init__(self, name: str, start: Any, end: Any) -> None:
        s, e = _ts(start), _ts(end)
        if e <= s:
            raise ValueError(f"window {name!r}: end {e} must be after start {s}")
        object.__setattr__(self, "name", str(name))
        object.__setattr__(self, "start", s)
        object.__setattr__(self, "end", e)

    @property
    def duration(self) -> pd.Timedelta:
        return self.end - self.start

    @property
    def days(self) -> float:
        return self.duration.total_seconds() / 86_400.0

    def overlaps(self, other: DateWindow) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, ts: Any) -> bool:
        t = _ts(ts)
        return self.start <= t < self.end

    def split(self, fraction: float, *, head_name: str, tail_name: str) -> tuple[DateWindow, DateWindow]:
        """Cut the window so the TAIL is ``fraction`` of the elapsed time."""
        if not 0.0 < fraction < 1.0:
            raise ValueError("fraction must be in (0, 1)")
        cut = self.start + pd.Timedelta(seconds=self.duration.total_seconds() * (1.0 - fraction))
        return (
            DateWindow(head_name, self.start, cut),
            DateWindow(tail_name, cut, self.end),
        )

    def slices(self, n: int, *, prefix: str = "slice") -> tuple[DateWindow, ...]:
        """``n`` equal-duration, non-overlapping sub-windows tiling this one."""
        if n < 1:
            raise ValueError("n must be >= 1")
        total = self.duration.total_seconds()
        edges = [self.start + pd.Timedelta(seconds=total * i / n) for i in range(n)]
        edges.append(self.end)
        return tuple(
            DateWindow(f"{prefix}_{i}", edges[i], edges[i + 1]) for i in range(n)
        )

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "start": self.start.isoformat(), "end": self.end.isoformat()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DateWindow:
        return cls(str(raw["name"]), raw["start"], raw["end"])

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.name}[{self.start.date()} .. {self.end.date()})"


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------


def canonical_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Sorted, JSON-safe view of a parameter set."""
    out: dict[str, Any] = {}
    for key in sorted(params):
        value = params[key]
        if isinstance(value, bool | str | int) or value is None:
            out[key] = value
        elif isinstance(value, float):
            out[key] = float(value)
        else:
            out[key] = str(value)
    return out


def params_key(params: Mapping[str, Any]) -> str:
    """A stable, hashable identity for a parameter set."""
    return "|".join(f"{k}={canonical_params(params)[k]!r}" for k in sorted(params))


@dataclass(frozen=True, slots=True)
class ParameterGrid:
    """An explicit, ordered sweep domain.

    The order of :meth:`points` is part of the contract: selection ties are
    broken by taking the FIRST point, so a sweep is reproducible rather than
    dependent on dict iteration order or on which worker finished first.
    """

    axes: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    truncated_from: int = 0
    """Size of the full cartesian product when this grid was subsampled, else 0."""

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for name, values in self.axes:
            if name in seen:
                raise ValueError(f"duplicate parameter axis {name!r}")
            if not values:
                raise ValueError(f"parameter axis {name!r} has an empty domain")
            seen.add(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.axes)

    @property
    def numeric_names(self) -> tuple[str, ...]:
        out = []
        for name, values in self.axes:
            if all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
                out.append(name)
        return tuple(out)

    def full_size(self) -> int:
        size = 1
        for _, values in self.axes:
            size *= len(values)
        return size

    def points(self) -> list[dict[str, Any]]:
        if not self.axes:
            return [{}]
        names = [name for name, _ in self.axes]
        domains = [values for _, values in self.axes]
        return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*domains)]

    def __len__(self) -> int:
        return self.full_size()

    def to_dict(self) -> dict[str, Any]:
        return {
            "axes": [
                {"name": name, "values": [_jsonable(v) for v in values]}
                for name, values in self.axes
            ],
            "full_size": self.full_size(),
            "truncated_from": self.truncated_from,
        }

    # ------------------------------------------------------------ builders

    @classmethod
    def from_axes(cls, axes: Mapping[str, Sequence[Any]]) -> ParameterGrid:
        return cls(tuple((name, tuple(axes[name])) for name in sorted(axes)))

    @classmethod
    def from_document(
        cls,
        document: Any,
        *,
        include: Iterable[str] | None = None,
        max_values_per_axis: int = 5,
        max_points: int = 64,
    ) -> ParameterGrid:
        """Read the sweep domains the STRATEGY declared, not ones we invented.

        A ``float`` parameter with no ``step`` has a continuous domain and cannot
        be enumerated; it is thinned to ``max_values_per_axis`` evenly spaced
        values between its declared bounds, inclusive of both ends, so the sweep
        still covers what the document said it covers.
        """
        specs: Mapping[str, Any] = getattr(document, "parameters", {}) or {}
        wanted = set(include) if include is not None else None
        axes: list[tuple[str, tuple[Any, ...]]] = []
        for name in sorted(specs):
            if wanted is not None and name not in wanted:
                continue
            spec = specs[name]
            try:
                values = tuple(spec.domain())
            except ValueError:
                lo, hi = float(spec.min_value), float(spec.max_value)
                k = max(2, int(max_values_per_axis))
                values = tuple(round(lo + (hi - lo) * i / (k - 1), 10) for i in range(k))
            values = _thin(values, max_values_per_axis, default=getattr(spec, "default", None))
            axes.append((name, values))
        grid = cls(tuple(axes))
        return grid.capped(max_points)

    def capped(self, max_points: int) -> ParameterGrid:
        """Thin axes, widest first, until the product fits ``max_points``.

        Deterministic and coverage-preserving: an axis is thinned by evenly
        spaced selection that always keeps both endpoints, never by sampling.

        An axis is never REMOVED, so the floor is ``2 ** n_axes`` points and a
        very small ``max_points`` will not be honoured. That is deliberate:
        dropping a declared parameter would make the report describe a sweep that
        did not happen. ``truncated_from`` records the full product either way.
        """
        if max_points < 1:
            raise ValueError("max_points must be >= 1")
        full = self.full_size()
        if full <= max_points or not self.axes:
            return self
        axes = [list(values) for _, values in self.axes]
        names = [name for name, _ in self.axes]
        while math.prod(len(a) for a in axes) > max_points:
            widest = max(range(len(axes)), key=lambda i: (len(axes[i]), -i))
            if len(axes[widest]) <= 2:
                # Every axis is down to its endpoints; the product cannot be
                # reduced further without dropping a parameter entirely, which
                # would silently change what was swept. Stop and report the
                # honest size instead.
                break
            axes[widest] = list(_thin(tuple(axes[widest]), len(axes[widest]) - 1))
        return ParameterGrid(
            tuple((n, tuple(v)) for n, v in zip(names, axes, strict=True)),
            truncated_from=full,
        )


def _thin(values: tuple[Any, ...], keep: int, *, default: Any = None) -> tuple[Any, ...]:
    """Evenly spaced selection keeping both endpoints, and the default if given.

    Evenly spaced rather than random so that thinning a domain is reproducible:
    the same document always produces the same sweep, on every machine and in
    every re-run, which is a precondition for a deterministic backtest result.
    """
    keep = max(1, int(keep))
    if len(values) <= keep:
        return values
    if keep == 1:
        return (default,) if default in values else (values[len(values) // 2],)
    idx = sorted({round(i * (len(values) - 1) / (keep - 1)) for i in range(keep)})
    chosen = [values[i] for i in idx]
    if default is not None and default in values and default not in chosen:
        # Replace an interior pick with the document's declared default. The
        # endpoints are kept because they define the domain the author claimed.
        interior = [j for j in range(len(chosen)) if 0 < j < len(chosen) - 1]
        if interior:
            chosen[interior[len(interior) // 2]] = default
            chosen = sorted(set(chosen), key=values.index)
    return tuple(chosen)


def _jsonable(value: Any) -> Any:
    if isinstance(value, bool | str | int) or value is None:
        return value
    if isinstance(value, float):
        return float(value)
    return str(value)


# --------------------------------------------------------------------------
# Evaluations
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowEvaluation:
    """What one parameterisation did over one window."""

    window: DateWindow
    params: dict[str, Any]
    n_trades: int
    net_profit: float
    returns: np.ndarray = field(repr=False)
    returns_basis: str = "trade"
    trades: tuple[Trade, ...] = field(default=(), repr=False)
    notes: str = ""

    def __post_init__(self) -> None:
        if self.returns_basis not in ("trade", "period"):
            raise ValueError("returns_basis must be 'trade' or 'period'")
        object.__setattr__(self, "returns", np.asarray(self.returns, dtype=float))
        object.__setattr__(self, "params", canonical_params(self.params))

    # ------------------------------------------------------------ metrics

    @property
    def expectancy(self) -> float:
        """Mean P&L per trade. Zero trades has no expectancy, and says so."""
        if self.n_trades <= 0:
            return 0.0
        return self.net_profit / self.n_trades

    @property
    def sharpe(self) -> float:
        """Sharpe of ``returns`` at the frequency of ``returns_basis``.

        NOT annualised. Annualising a trade-based Sharpe requires a trade rate
        the evaluator has not been asked for, and V1's habit of annualising with
        a hardcoded factor is exactly how a 40-trade backtest acquired a Sharpe
        of 3.
        """
        if self.returns.size < 2:
            return 0.0
        sd = float(self.returns.std(ddof=1))
        if not math.isfinite(sd) or sd <= 0.0:
            return 0.0
        return float(sharpe_moments(self.returns).sr_hat)

    @property
    def profit_per_day(self) -> float:
        days = self.window.days
        return self.net_profit / days if days > 0 else 0.0

    def metric(self, name: str) -> float:
        try:
            return float({
                "sharpe": self.sharpe,
                "net_profit": self.net_profit,
                "expectancy": self.expectancy,
                "profit_per_day": self.profit_per_day,
                "n_trades": float(self.n_trades),
            }[name])
        except KeyError:  # pragma: no cover - programming error
            raise ValueError(f"unknown selection metric {name!r}") from None

    def degeneracies(self) -> tuple[str, ...]:
        """Ways this evaluation is not a number you may reason about."""
        bad: list[str] = []
        if self.n_trades <= 0:
            bad.append("no_trades")
        if not np.isfinite(self.returns).all():
            bad.append("non_finite_returns")
        if self.returns.size >= 2 and float(self.returns.std(ddof=1)) <= 0.0:
            bad.append("zero_variance_returns")
        if self.returns.size and float(np.abs(self.returns).max()) == 0.0:
            bad.append("all_zero_returns")
        if not math.isfinite(self.net_profit):
            bad.append("non_finite_net_profit")
        return tuple(bad)

    def summary(self) -> dict[str, Any]:
        return {
            "window": self.window.to_dict(),
            "params": dict(self.params),
            "n_trades": int(self.n_trades),
            "net_profit": float(self.net_profit),
            "expectancy": float(self.expectancy),
            "sharpe": float(self.sharpe),
            "profit_per_day": float(self.profit_per_day),
            "returns_basis": self.returns_basis,
            "n_returns": int(self.returns.size),
            "degeneracies": list(self.degeneracies()),
        }

    # ------------------------------------------------------------ builders

    @classmethod
    def from_trades(
        cls,
        window: DateWindow,
        params: Mapping[str, Any],
        trades: Sequence[Trade],
        *,
        notes: str = "",
    ) -> WindowEvaluation:
        pnl = np.array([float(t.net_pnl) for t in trades], dtype=float)
        return cls(
            window=window,
            params=dict(params),
            n_trades=len(trades),
            net_profit=float(pnl.sum()) if pnl.size else 0.0,
            returns=pnl,
            returns_basis="trade",
            trades=tuple(trades),
            notes=notes,
        )


class Evaluator(Protocol):
    """Run one parameterisation over one window."""

    def __call__(
        self, params: Mapping[str, Any], window: DateWindow
    ) -> WindowEvaluation: ...


# --------------------------------------------------------------------------
# Candidate
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """The identity of the thing being validated.

    ``content_hash`` is the strategy's SEMANTIC hash. It is what the holdout
    registry keys on, so a renamed strategy cannot buy itself a second holdout
    evaluation, and it is what the research memory keys on for exact-duplicate
    detection.
    """

    strategy_id: str
    content_hash: str
    default_params: dict[str, Any] = field(default_factory=dict)
    grid: ParameterGrid = field(default_factory=ParameterGrid)
    document: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("Candidate.strategy_id is mandatory")
        if not self.content_hash:
            raise ValueError(
                "Candidate.content_hash is mandatory: a validation report that "
                "cannot name the exact strategy it validated is not evidence."
            )
        object.__setattr__(self, "default_params", canonical_params(self.default_params))

    @classmethod
    def from_document(
        cls,
        document: Any,
        *,
        max_points: int = 64,
        max_values_per_axis: int = 5,
        include: Iterable[str] | None = None,
    ) -> Candidate:
        specs: Mapping[str, Any] = getattr(document, "parameters", {}) or {}
        defaults = {name: specs[name].default for name in sorted(specs)}
        return cls(
            strategy_id=str(document.strategy_id),
            content_hash=str(document.content_hash()),
            default_params=defaults,
            grid=ParameterGrid.from_document(
                document,
                include=include,
                max_values_per_axis=max_values_per_axis,
                max_points=max_points,
            ),
            document=document,
        )
