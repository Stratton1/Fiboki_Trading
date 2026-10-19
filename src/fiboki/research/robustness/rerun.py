"""Robustness diagnostics that re-run the binding: warm-up, look-ahead, execution
stress, parameter perturbation, a random-entry benchmark and price noise.

Each takes a :class:`~fiboki.research.robustness.engine.BindingExecutor` (or, for
the look-ahead detector, any :class:`SignalModel`), a seed where it draws, and a
``progress(done, total, stage)`` callback the job uses to report how far it got.
Each is a pure function of its inputs: the same binding, bars, options and seed
give the same answer, which ``tests/unit/test_robustness_rerun.py`` pins.

They are ADVISORY. Nothing here is read by the ladder, a gate or a
pre-registration; the numbers are reported beside the verdict, never wired in.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import numpy as np
import pandas as pd

from fiboki.core.contracts import Signal
from fiboki.core.instruments import get as get_instrument
from fiboki.indicators import ATR
from fiboki.research.robustness.engine import BindingExecutor, RunOutcome
from fiboki.sim.stress_profiles import slippage_is_modelled, stressed_profile
from fiboki.strategy.compiler import compile_strategy

__all__ = [
    "Progress",
    "SignalModel",
    "execution_stress",
    "lookahead",
    "lookahead_scan",
    "parameter_perturbation",
    "price_noise",
    "versus_random",
    "warmup_stability",
]

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, stage: str) -> None:
    return None


def _q(values: Sequence[float], q: float) -> float | None:
    arr = np.array([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    return float(np.quantile(arr, q)) if arr.size else None


def _dist(values: Sequence[float | None]) -> dict[str, Any]:
    arr = np.array([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if arr.size == 0:
        return {"n": 0, "p5": None, "p25": None, "p50": None, "p75": None, "p95": None,
                "mean": None, "min": None, "max": None}
    q = np.quantile(arr, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {"n": int(arr.size), "p5": float(q[0]), "p25": float(q[1]), "p50": float(q[2]),
            "p75": float(q[3]), "p95": float(q[4]), "mean": float(arr.mean()),
            "min": float(arr.min()), "max": float(arr.max())}


def _retention(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base == 0:
        return None
    return value / base


# ============================================================ 1. warm-up


def _signal_key(sig: Signal | None) -> tuple[Any, ...] | None:
    if sig is None:
        return None
    return (sig.direction.value, round(float(sig.stop_price), 10),
            tuple(round(float(p), 10) for p in sig.take_profit_prices))


def warmup_stability(executor: BindingExecutor, *, extra_bars: Sequence[int] = (100, 300),
                     progress: Progress = _noop) -> dict[str, Any]:
    """Run the binding with N MORE or N FEWER bars of history and compare (Freqtrade's
    recursive analysis): do its indicators, signals and trades depend on where the
    data happens to start?

    Two runs per N, a LONG one (more history) and a SHORT one (N bars less),
    compared over the SHORT run's bars after its own declared warm-up:

    * ``extended``: the window as stored is the short run and N bars of earlier
      history are prepended for the long one (when that history exists);
    * ``late_start``: the stored run already starts at its data's first bar, so
      the long run is the window as stored and the short run starts cold N bars
      later.

    A difference inside the short run's own warm-up is expected and reported
    apart. A difference AFTER it means a recursive indicator (an EMA, a Wilder
    average) has not converged by the declared warm-up. ``stable`` judges the
    SIGNALS and the trades' timing and exits (not their P&L, which differs by
    construction because sizing compounds from a different starting point);
    ``indicators_converged`` says separately whether every indicator column
    agreed to 1e-9 relative.
    """
    compiled = compile_strategy(executor.bound)
    warm = int(compiled.warmup_period)
    total = 1 + 2 * len(extra_bars)
    progress(0, total, "baseline")
    base = executor.run()
    progress(1, total, "baseline")
    window = executor.window_frame(0)
    sym, tf = executor.binding.instrument, executor.timeframe
    results: list[dict[str, Any]] = []
    for j, n in enumerate(extra_bars):
        n = int(n)
        if executor.prologue_available >= n:
            mode, use = "extended", n
            long_frame, short_frame = executor.window_frame(n), window
            long_out, short_out = None, base
        else:
            mode, use = "late_start", min(n, max(0, len(window) - warm - 30))
            long_frame, short_frame = window, window.iloc[use:]
            long_out, short_out = base, None
        long_prep = compiled.prepare(long_frame)
        short_prep = compiled.prepare(short_frame)
        offset = len(long_prep) - len(short_prep)  # short rows are the long frame's tail
        long_sig = [_signal_key(compiled.generate_signal(long_prep, i + offset, instrument=sym, timeframe=tf))
                    for i in range(len(short_prep))]
        short_sig = [_signal_key(compiled.generate_signal(short_prep, i, instrument=sym, timeframe=tf))
                     for i in range(len(short_prep))]
        progress(2 + 2 * j, total, f"{mode} {use} bars: signals")
        cold = [i for i in range(min(warm, len(short_prep))) if long_sig[i] != short_sig[i]]
        after = [i for i in range(warm, len(short_prep)) if long_sig[i] != short_sig[i]]
        columns = [c for c in short_prep.columns
                   if c not in ("open", "high", "low", "close", "volume", "price_basis")
                   and pd.api.types.is_numeric_dtype(short_prep[c])]
        col_rows = []
        for c in columns:
            a = long_prep[c].to_numpy(dtype=float)[offset + warm:]
            b = short_prep[c].to_numpy(dtype=float)[warm:]
            both = np.isfinite(a) & np.isfinite(b)
            nan_mismatch = int((np.isfinite(a) != np.isfinite(b)).sum())
            diff = np.abs(a[both] - b[both])
            rel = diff / np.maximum(np.abs(a[both]), 1e-12)
            changed = np.flatnonzero(np.abs(a - b) > 1e-9 * np.maximum(np.abs(a), 1.0))
            col_rows.append({
                "column": c, "max_abs_diff": float(diff.max()) if diff.size else 0.0,
                "max_rel_diff": float(rel.max()) if rel.size else 0.0,
                "bars_differing": int(changed.size) + nan_mismatch,
                "last_differing_bar": (short_prep.index[warm + int(changed[-1])].isoformat()
                                       if changed.size else None),
            })
        other = executor.run(extra_bars=use) if mode == "extended" else executor.run(frame=short_frame)
        long_out = long_out or other
        short_out = short_out or other
        progress(3 + 2 * j, total, f"{mode} {use} bars: engine")
        cut = short_prep.index[min(warm, len(short_prep) - 1)]
        s_trades = {(t.entry_time, t.direction.value): t for t in short_out.trades if t.entry_time >= cut}
        l_trades = {(t.entry_time, t.direction.value): t for t in long_out.trades if t.entry_time >= cut}
        matched = sorted(set(s_trades) & set(l_trades))
        # Timing and exit, not money: the two runs reach a shared bar with different
        # equity (sizing compounds), so their P&L differs by construction.
        changed_trades = [k for k in matched if (s_trades[k].exit_time != l_trades[k].exit_time
                                                 or s_trades[k].exit_reason != l_trades[k].exit_reason
                                                 or abs(s_trades[k].exit_price - l_trades[k].exit_price) > 1e-9)]
        results.append({
            "mode": mode, "extra_bars_requested": n, "extra_bars": use,
            "compared_from": cut.isoformat(),
            "signals_compared": max(0, len(short_prep) - warm),
            "signals_changed_after_warmup": len(after),
            "first_changed_signal": short_prep.index[after[0]].isoformat() if after else None,
            "signals_changed_in_cold_region": len(cold),
            "indicator_columns": col_rows,
            "trades_short": len(s_trades), "trades_long": len(l_trades),
            "trades_matched": len(matched), "trades_only_short": len(set(s_trades) - set(l_trades)),
            "trades_only_long": len(set(l_trades) - set(s_trades)),
            "trades_changed": len(changed_trades),
            "metrics": other.metrics,
            "indicators_converged": all(c["bars_differing"] == 0 for c in col_rows),
            "stable": (not after and not changed_trades and set(s_trades) == set(l_trades)),
        })
    return {"warmup_period": warm, "baseline": base.brief(), "runs": results,
            "stable": all(r["stable"] for r in results),
            "prologue_available": executor.prologue_available}


# ============================================================ 2. look-ahead


class SignalModel(Protocol):
    """What the look-ahead detector needs: ``CompiledStrategy`` satisfies it."""

    @property
    def warmup_period(self) -> int: ...

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame: ...

    def generate_signal(self, df: pd.DataFrame, idx: int, *, instrument: str, timeframe: Any) -> Signal | None: ...


def lookahead_scan(model: SignalModel, frame: pd.DataFrame, *, instrument: str, timeframe: Any,
                   samples: int, seed: int, progress: Progress = _noop,
                   window_start: int = 0) -> dict[str, Any]:
    """Re-evaluate signals on data TRUNCATED at sampled bars (Freqtrade's lookahead-analysis).

    For each sampled bar ``i`` the model prepares ``frame[: i + 1]`` (nothing
    after ``i`` exists) and the signal and every indicator column at ``i`` are
    compared with those computed on the full frame. Any difference means the
    value at ``i`` used a later bar: look-ahead. Bars are drawn without
    replacement from the post-warm-up rows, by ``numpy.random.default_rng(seed)``.
    """
    full = model.prepare(frame)
    lo = max(int(model.warmup_period), int(window_start))
    candidates = np.arange(lo, len(frame) - 1)
    if candidates.size == 0:
        raise ValueError("no post-warm-up bars to sample")
    rng = np.random.default_rng(int(seed))
    picks = np.sort(rng.choice(candidates, size=min(int(samples), candidates.size), replace=False))
    columns = [c for c in full.columns if c not in frame.columns and pd.api.types.is_numeric_dtype(full[c])]
    flags: list[dict[str, Any]] = []
    checked_signals = 0
    for n, i in enumerate(picks):
        i = int(i)
        trunc = model.prepare(frame.iloc[: i + 1])
        s_full = _signal_key(model.generate_signal(full, i, instrument=instrument, timeframe=timeframe))
        s_trunc = _signal_key(model.generate_signal(trunc, i, instrument=instrument, timeframe=timeframe))
        if s_full is not None or s_trunc is not None:
            checked_signals += 1
        bad_cols = []
        for c in columns:
            a, b = full[c].iloc[i], trunc[c].iloc[i] if c in trunc.columns else np.nan
            fa, fb = bool(np.isfinite(a)), bool(np.isfinite(b))
            if fa != fb or (fa and abs(float(a) - float(b)) > 1e-9 * max(1.0, abs(float(a)))):
                bad_cols.append(c)
        if s_full != s_trunc or bad_cols:
            flags.append({
                "bar": frame.index[i].isoformat(), "index": i,
                "signal_full": None if s_full is None else s_full[0],
                "signal_truncated": None if s_trunc is None else s_trunc[0],
                "signal_changed": s_full != s_trunc, "columns_changed": bad_cols,
            })
        progress(n + 1, len(picks), "truncated re-evaluation")
    return {"samples": int(len(picks)), "seed": int(seed), "signals_at_samples": checked_signals,
            "columns_checked": columns, "flags": flags,
            "biased": bool(flags), "signal_flags": sum(1 for f in flags if f["signal_changed"])}


def lookahead(executor: BindingExecutor, *, samples: int = 60, seed: int = 20261019,
              progress: Progress = _noop) -> dict[str, Any]:
    """:func:`lookahead_scan` of the run's own compiled binding over its window."""
    compiled = compile_strategy(executor.bound)
    frame = executor.window_frame(0)
    return lookahead_scan(compiled, frame, instrument=executor.binding.instrument,
                          timeframe=executor.timeframe, samples=samples, seed=seed, progress=progress)


# ============================================================ 4. execution stress


def execution_stress(executor: BindingExecutor, *, skip_levels: Sequence[float] = (0.05, 0.10, 0.20),
                     skip_seeds: int = 5, delays: Sequence[int] = (1, 2),
                     multipliers: Sequence[float] = (1.0, 1.5, 2.0, 3.0), seed: int = 20261019,
                     progress: Progress = _noop) -> dict[str, Any]:
    """The binding through the real engine under stressed execution, cell by cell.

    * random trade skip: the profile's ``rejection_probability`` at each level,
      under ``skip_seeds`` seeds (``seed + s``), so a skipped order is a broker
      reject the fill model drew;
    * entry delay: ``latency_bars`` of 1 and 2, so the order fills at the open
      of a later bar at that bar's price;
    * a spread x slippage grid: every cost-model parameter scaled
      (``sim/stress_profiles.py``); sizing prices the stressed costs too.
    """
    base_profile = executor.base_profile
    total = 1 + len(skip_levels) * skip_seeds + len(delays) + len(multipliers) ** 2
    done = 0
    progress(done, total, "baseline")
    base = executor.run()
    done += 1
    progress(done, total, "baseline")
    b_net = base.metrics.get("net_profit")

    def row(kind: str, level: float, outs: list[RunOutcome]) -> dict[str, Any]:
        nets = [o.metrics.get("net_profit") for o in outs]
        return {
            "stress": kind, "level": float(level), "runs": len(outs),
            "profile": outs[0].profile_name,
            "net_profit": _dist(nets), "sharpe": _dist([o.metrics.get("sharpe") for o in outs]),
            "max_drawdown_pct": _dist([o.metrics.get("max_drawdown_pct") for o in outs]),
            "n_trades": _dist([o.metrics.get("n_trades") for o in outs]),
            "retention": _retention(_q(nets, 0.5), b_net),
            "profitable_share": float(np.mean([(n or 0) > 0 for n in nets])) if nets else None,
        }

    rows: list[dict[str, Any]] = []
    for level in skip_levels:
        outs = []
        for s in range(int(skip_seeds)):
            prof = stressed_profile(base_profile, rejection_probability=float(level), seed=int(seed) + s)
            outs.append(executor.run(profile=prof))
            done += 1
            progress(done, total, f"skip {level:.0%}")
        rows.append(row("trade_skip", level, outs))
    for d in delays:
        prof = stressed_profile(base_profile, latency_bars=base_profile.latency_bars + int(d))
        rows.append(row("entry_delay", d, [executor.run(profile=prof)]))
        done += 1
        progress(done, total, f"delay {d} bar(s)")
    grid: list[dict[str, Any]] = []
    for sx in multipliers:
        for lx in multipliers:
            if sx == 1.0 and lx == 1.0:
                out = base
            else:
                out = executor.run(profile=stressed_profile(base_profile, spread_x=sx, slippage_x=lx))
            grid.append({"spread_x": float(sx), "slippage_x": float(lx), "profile": out.profile_name,
                         "metrics": out.metrics,
                         "retention": _retention(out.metrics.get("net_profit"), b_net)})
            done += 1
            progress(done, total, f"spread {sx:g}x slippage {lx:g}x")
    notes = []
    if not slippage_is_modelled(base_profile):
        notes.append(f"{base_profile.name} charges no slippage, so the slippage axis is flat by construction.")
    notes.append(f"Baseline rejection probability of {base_profile.name}: {base_profile.rejection_probability:g}; "
                 "a skip level REPLACES it.")
    return {"baseline": base.brief(), "rows": rows, "grid": grid, "multipliers": [float(m) for m in multipliers],
            "skip_seeds": int(skip_seeds), "seed": int(seed), "notes": notes}


# ======================================================= 6. parameter perturbation


def _jitter(spec: Any, value: Any, factor: float) -> Any:
    kind = getattr(spec, "kind", "")
    lo, hi = getattr(spec, "min_value", None), getattr(spec, "max_value", None)
    x = float(value) * factor
    if lo is not None:
        x = max(float(lo), x)
    if hi is not None:
        x = min(float(hi), x)
    return int(round(x)) if kind == "int" else float(x)


def parameter_perturbation(executor: BindingExecutor, *, pct: float = 10.0, n: int = 40,
                           seed: int = 20261019, progress: Progress = _noop) -> dict[str, Any]:
    """Jitter every numeric parameter by a uniform factor in ``[1 - pct%, 1 + pct%]``.

    Draw ``i`` uses ``default_rng([seed, i])``, so adding draws never moves the
    earlier ones. Values are clamped to the declared domain and integers rounded.
    A draw the document refuses to bind (a declared constraint) is counted as
    invalid, not dropped silently. The objective is net profit over the window.
    """
    specs = {k: s for k, s in sorted((executor.template.parameters or {}).items())
             if getattr(s, "kind", "") in ("int", "float")}
    current = {**executor.template.default_values(), **executor.binding.parameters}
    if not specs:
        raise ValueError(f"{executor.template.strategy_id} declares no numeric parameter to perturb")
    progress(0, n + 1, "baseline")
    base = executor.run()
    progress(1, n + 1, "baseline")
    draws: list[dict[str, Any]] = []
    invalid = 0
    for i in range(int(n)):
        rng = np.random.default_rng([int(seed), i])
        params = {k: _jitter(s, current[k], 1.0 + float(rng.uniform(-pct, pct)) / 100.0)
                  for k, s in specs.items()}
        try:
            out = executor.run(params=params)
        except (ValueError, KeyError) as exc:
            invalid += 1
            draws.append({"params": params, "valid": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                          "net_profit": None, "sharpe": None, "n_trades": None})
        else:
            draws.append({"params": params, "valid": True, "error": "",
                          "net_profit": out.metrics.get("net_profit"), "sharpe": out.metrics.get("sharpe"),
                          "n_trades": out.metrics.get("n_trades")})
        progress(i + 2, n + 1, "perturbations")
    nets = [d["net_profit"] for d in draws if d["valid"]]
    b_net = base.metrics.get("net_profit")
    below = [v for v in nets if v is not None and b_net is not None and v < b_net]
    return {
        "pct": float(pct), "n": int(n), "seed": int(seed), "parameters": list(specs),
        "baseline": base.brief(), "draws": draws, "invalid": invalid,
        "objective": "net_profit", "net_profit": _dist(nets),
        "sharpe": _dist([d["sharpe"] for d in draws if d["valid"]]),
        "profitable_share": (float(np.mean([v > 0 for v in nets if v is not None])) if nets else None),
        "baseline_percentile": (100.0 * len(below) / len(nets)) if nets and b_net is not None else None,
    }


# ======================================================= 7. versus random


def versus_random(executor: BindingExecutor, *, n_runs: int = 200, seed: int = 20261019,
                  progress: Progress = _noop) -> dict[str, Any]:
    """Where the strategy ranks among random-entry systems with its own trade shape.

    Each random system has the SAME number of trades, holding times drawn from
    the strategy's own ``bars_held`` distribution and directions from its own
    direction mix, with entries uniform over the window. Every trade, the
    strategy's included, is measured the same way: the mid-to-mid move from
    entry to exit as a fraction of entry, less the profile's expected round
    trip friction (``stop_out_cost_per_unit``: spread plus two fills of expected
    slippage) at the entry hour. Sizing, stops and compounding are deliberately
    left out of BOTH sides, so the comparison is of entry timing and exit
    management against chance, not of position sizing.

    Known approximation: random trades may overlap one another; the strategy's
    may not (its ``max_concurrent`` cap). The percentile is the share of random
    systems whose total is below the strategy's (ties count half).
    """
    progress(0, n_runs + 1, "baseline")
    base = executor.run()
    progress(1, n_runs + 1, "baseline")
    frame = executor.window_frame(0)
    frame = frame.loc[(frame.index >= executor.window.start) & (frame.index < executor.window.end)]
    instr = get_instrument(executor.binding.instrument)
    profile = executor.base_profile
    opens = frame["open"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    hours = frame.index.hour.to_numpy()
    friction = np.array([profile.stop_out_cost_per_unit(instr, int(h), float(p)) / float(p)
                         for h, p in zip(hours, opens, strict=True)], dtype=float)
    trades = base.trades
    if not trades:
        raise ValueError("the baseline re-run produced no trades; there is nothing to compare with chance")
    holds = np.array([max(1, int(t.bars_held)) for t in trades], dtype=int)
    signs = np.array([t.direction.sign for t in trades], dtype=float)
    pos = {t: i for i, t in enumerate(frame.index)}

    def strat_ret(t: Any) -> float:
        i = pos.get(t.entry_time)
        fr = friction[i] if i is not None else float(np.median(friction))
        return t.direction.sign * (t.exit_price - t.entry_price) / t.entry_price - fr

    strategy_total = float(sum(strat_ret(t) for t in trades))
    n = len(trades)
    totals: list[float] = []
    last = len(frame) - 1
    for r in range(int(n_runs)):
        rng = np.random.default_rng([int(seed), r])
        h = rng.choice(holds, size=n, replace=True)
        s = rng.choice(signs, size=n, replace=True)
        starts = rng.integers(0, np.maximum(1, last - h + 1))
        exits = np.minimum(starts + h, last)
        # Enter at the open of bar ``start``, leave at the close of the bar
        # ``hold`` bars later: the same mid-to-mid measure as the strategy's.
        move = s * (closes[exits] - opens[starts]) / opens[starts]
        totals.append(float(np.sum(move - friction[starts])))
        if (r + 1) % 20 == 0 or r + 1 == n_runs:
            progress(r + 2, n_runs + 1, "random systems")
    arr = np.array(totals)
    below = float((arr < strategy_total).sum() + 0.5 * (arr == strategy_total).sum())
    return {
        "n_runs": int(n_runs), "seed": int(seed), "n_trades": n,
        "strategy_total_return": strategy_total, "strategy_mean_return": strategy_total / n,
        "random": _dist(totals), "percentile": 100.0 * below / len(arr),
        "holding_bars": _dist([float(x) for x in holds]),
        "long_share": float((signs > 0).mean()),
        "baseline": base.brief(),
    }


# ============================================================ 11. price noise


def noisy_frame(frame: pd.DataFrame, *, atr_fraction: float, rng: np.random.Generator,
                atr_period: int = 14) -> pd.DataFrame:
    """OHLC each shifted by an independent uniform draw in ``±atr_fraction x ATR``.

    ATR is :class:`fiboki.indicators.ATR` of the ORIGINAL bars at the previous
    bar (a scale, known at the bar's open). The bars are then made valid again:
    ``high = max(high', open', close')`` and ``low = min(low', open', close')``,
    and every price stays positive.
    """
    atr = ATR(atr_period).compute(frame)[f"atr_{atr_period}"].shift(1)
    scale = atr.bfill().to_numpy(dtype=float) * float(atr_fraction)
    out = frame.copy()
    n = len(frame)
    shifts = {c: rng.uniform(-1.0, 1.0, size=n) * scale for c in ("open", "high", "low", "close")}
    o = frame["open"].to_numpy(dtype=float) + shifts["open"]
    h = frame["high"].to_numpy(dtype=float) + shifts["high"]
    lo = frame["low"].to_numpy(dtype=float) + shifts["low"]
    c = frame["close"].to_numpy(dtype=float) + shifts["close"]
    floor = 1e-9
    o, h, lo, c = (np.maximum(x, floor) for x in (o, h, lo, c))
    h = np.maximum.reduce([h, o, c])
    lo = np.minimum.reduce([lo, o, c])
    out["open"], out["high"], out["low"], out["close"] = o, h, lo, c
    out.attrs = dict(frame.attrs)
    return out


def price_noise(executor: BindingExecutor, *, levels: Sequence[float] = (0.1, 0.25, 0.5),
                n: int = 8, seed: int = 20261019, progress: Progress = _noop) -> dict[str, Any]:
    """Re-run on ``n`` seeded noisy copies of the bars at each noise level.

    Degradation is the median net profit at a level over the baseline's. A
    strategy whose edge vanishes under a tenth of an ATR of noise is fitted to
    the exact path of these bars.
    """
    total = 1 + len(levels) * n
    progress(0, total, "baseline")
    base = executor.run()
    progress(1, total, "baseline")
    b_net = base.metrics.get("net_profit")
    frame = executor.window_frame(0)
    rows: list[dict[str, Any]] = []
    done = 1
    for li, level in enumerate(levels):
        outs: list[RunOutcome] = []
        for k in range(int(n)):
            noisy = noisy_frame(frame, atr_fraction=float(level), rng=np.random.default_rng([int(seed), li, k]))
            outs.append(executor.run(frame=noisy))
            done += 1
            progress(done, total, f"noise {level:g} ATR")
        nets = [o.metrics.get("net_profit") for o in outs]
        rows.append({
            "atr_fraction": float(level), "runs": len(outs), "net_profit": _dist(nets),
            "sharpe": _dist([o.metrics.get("sharpe") for o in outs]),
            "n_trades": _dist([o.metrics.get("n_trades") for o in outs]),
            "degradation": _retention(_q(nets, 0.5), b_net),
            "profitable_share": float(np.mean([(v or 0) > 0 for v in nets])) if nets else None,
        })
    return {"baseline": base.brief(), "rows": rows, "n": int(n), "seed": int(seed), "atr_period": 14}


def options_of(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """The options a job of ``kind`` runs with: defaults overlaid by ``raw`` (validated)."""
    defaults: dict[str, dict[str, Any]] = {
        "warmup": {"extra_bars": [100, 300]},
        "lookahead": {"samples": 60, "seed": 20261019},
        "execution": {"skip_levels": [0.05, 0.10, 0.20], "skip_seeds": 5, "delays": [1, 2],
                      "multipliers": [1.0, 1.5, 2.0, 3.0], "seed": 20261019},
        "perturbation": {"pct": 10.0, "n": 40, "seed": 20261019},
        "random": {"n_runs": 200, "seed": 20261019},
        "noise": {"levels": [0.1, 0.25, 0.5], "n": 8, "seed": 20261019},
    }
    if kind not in defaults:
        raise ValueError(f"unknown robustness diagnostic {kind!r}; known {sorted(defaults)}")
    out = dict(defaults[kind])
    for k, v in (raw or {}).items():
        if k not in out:
            raise ValueError(f"{kind}: unknown option {k!r}; known {sorted(out)}")
        out[k] = v
    return out


RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "warmup": warmup_stability,
    "lookahead": lookahead,
    "execution": execution_stress,
    "perturbation": parameter_perturbation,
    "random": versus_random,
    "noise": price_noise,
}
