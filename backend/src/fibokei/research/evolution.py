"""Evolutionary search over Strategy Factory specs.

A small, deterministic genetic algorithm that evolves ``StrategySpec`` genomes
across generations using parameter mutation, structural mutation (add / swap /
remove a confirmation or filter rule) and crossover. Structural growth is how a
traditional family becomes a hybrid, and a hybrid becomes a *triple* hybrid
(1 entry + 2 confirm/filter rules).

Fitness is **overfit-aware** by construction. A naive in-sample optimiser picks
tweaks that collapse out-of-sample (observed directly in Phase-9 tuning: an RSI
tweak jumped +0.27 in-sample but failed OOS). So the default fitness is
``min(in_sample_score, out_of_sample_score)`` with a trade-count gate — a genome
only scores well if it holds up on *both* halves of the data. This bakes the
promotion philosophy (robustness over raw profit) into the search itself.

Nothing here promotes a strategy. It produces ranked candidate specs; they still
pass through ``promotion.py`` (OOS + Monte Carlo gates) before paper candidacy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pandas as pd

from fibokei.backtester.config import BacktestConfig
from fibokei.backtester.engine import Backtester
from fibokei.backtester.metrics import compute_metrics
from fibokei.core.models import Timeframe
from fibokei.research.scorer import ScoringConfig, compute_composite_score
from fibokei.research.spec_tuning import PRIMITIVE_PARAM_GRID
from fibokei.strategies.factory.compiler import compile_spec
from fibokei.strategies.factory.spec import StrategySpec

# Pool of same-direction confirmation/filter primitives the search may add.
# (primitive, params, slot) where slot is "confirmation_rules" or "filters".
CONFIRM_POOL: list[tuple[str, dict, str]] = [
    ("macd_zero", {"fast": 12, "slow": 26, "signal": 9}, "confirmation_rules"),
    ("rsi_threshold", {"period": 14, "mode": "momentum", "level": 50},
     "confirmation_rules"),
    ("price_vs_ema", {"period": 50}, "filters"),
    ("price_vs_ema", {"period": 100}, "filters"),
    ("price_vs_sma", {"period": 200}, "filters"),
    ("adx_filter", {"period": 14, "threshold": 20}, "filters"),
    ("atr_min", {"min_pct": 0.0005}, "filters"),
]

STOP_CHOICES = [1.5, 2.0, 2.5, 3.0]
TARGET_CHOICES = [1.0, 1.5, 2.0, 3.0]


@dataclass
class EvoConfig:
    population: int = 16
    generations: int = 5
    elite: int = 4
    seed: int = 42
    max_extra_rules: int = 2  # 0=traditional, 1=hybrid, 2=triple hybrid
    min_trades: int = 80
    split_ratio: float = 0.7
    p_param: float = 0.5       # prob of a param mutation per offspring
    p_structural: float = 0.4  # prob of a structural mutation per offspring


@dataclass
class Genome:
    spec: StrategySpec
    is_score: float = 0.0
    oos_score: float = 0.0
    trades: int = 0
    fitness: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    status: str = "ok"


@dataclass
class EvolutionResult:
    seed_spec_id: str
    instrument: str
    timeframe: str
    best: list[Genome] = field(default_factory=list)
    generations_run: int = 0
    evaluated: int = 0


# ── Identity helpers ──────────────────────────────────────────────────

_counter = {"n": 0}


def _next_id(prefix: str) -> str:
    _counter["n"] += 1
    return f"{prefix}_e{_counter['n']}"


def _extra_rule_count(spec: StrategySpec) -> int:
    return len(spec.confirmation_rules) + len(spec.filters)


# ── Mutation operators ────────────────────────────────────────────────


def param_mutate(spec: StrategySpec, rng: random.Random) -> StrategySpec:
    """Jitter the entry params (from the primitive grid) + stop/target."""
    data = spec.model_dump(mode="python", exclude={"created_at"})
    prim = spec.entry_rules[0].primitive
    grid = PRIMITIVE_PARAM_GRID.get(prim, {})
    if grid:
        params = dict(data["entry_rules"][0].get("params", {}))
        for k, choices in grid.items():
            if rng.random() < 0.6:
                params[k] = rng.choice(choices)
        data["entry_rules"][0]["params"] = params
    data["stop"]["multiple"] = rng.choice(STOP_CHOICES)
    data["target"]["multiple"] = rng.choice(TARGET_CHOICES)
    data["parent_spec_id"] = spec.spec_id
    data["generation_method"] = "parameter_mutation"
    data["spec_id"] = _next_id(spec.spec_id.split("__")[0] + "__pm")
    return StrategySpec(**data)


def structural_add(spec: StrategySpec, rng: random.Random,
                   max_extra: int) -> StrategySpec:
    """Add a same-direction confirmation/filter (grows hybrid → triple)."""
    if _extra_rule_count(spec) >= max_extra:
        return param_mutate(spec, rng)
    data = spec.model_dump(mode="python", exclude={"created_at"})
    existing = {(r["primitive"], tuple(sorted(r.get("params", {}).items())))
                for r in data["confirmation_rules"] + data["filters"]
                + data["entry_rules"]}
    idxs = list(range(len(CONFIRM_POOL)))
    rng.shuffle(idxs)
    for i in idxs:
        prim, params, slot = CONFIRM_POOL[i]
        key = (prim, tuple(sorted(params.items())))
        if key in existing:
            continue
        data[slot] = list(data[slot]) + [{"primitive": prim, "params": params}]
        break
    data["parent_spec_id"] = spec.spec_id
    data["generation_method"] = "structural_mutation"
    data["spec_id"] = _next_id(spec.spec_id.split("__")[0] + "__sa")
    return StrategySpec(**data)


def structural_swap(spec: StrategySpec, rng: random.Random) -> StrategySpec:
    """Replace a confirmation/filter with a different pooled rule."""
    extras = [(slot, i) for slot in ("confirmation_rules", "filters")
              for i in range(len(getattr(spec, slot)))]
    if not extras:
        return structural_add(spec, rng, max_extra=2)
    data = spec.model_dump(mode="python", exclude={"created_at"})
    slot, idx = rng.choice(extras)
    prim, params, _ = rng.choice(CONFIRM_POOL)
    data[slot][idx] = {"primitive": prim, "params": params}
    data["parent_spec_id"] = spec.spec_id
    data["generation_method"] = "structural_mutation"
    data["spec_id"] = _next_id(spec.spec_id.split("__")[0] + "__ss")
    return StrategySpec(**data)


def structural_remove(spec: StrategySpec, rng: random.Random) -> StrategySpec:
    """Drop a confirmation/filter (simplify)."""
    extras = [(slot, i) for slot in ("confirmation_rules", "filters")
              for i in range(len(getattr(spec, slot)))]
    if not extras:
        return param_mutate(spec, rng)
    data = spec.model_dump(mode="python", exclude={"created_at"})
    slot, idx = rng.choice(extras)
    data[slot] = [r for j, r in enumerate(data[slot]) if j != idx]
    data["parent_spec_id"] = spec.spec_id
    data["generation_method"] = "structural_mutation"
    data["spec_id"] = _next_id(spec.spec_id.split("__")[0] + "__sr")
    return StrategySpec(**data)


def crossover(a: StrategySpec, b: StrategySpec, rng: random.Random,
              max_extra: int) -> StrategySpec:
    """Entry from one parent, confirm/filter rules from the other (capped)."""
    data = a.model_dump(mode="python", exclude={"created_at"})
    data["confirmation_rules"] = [r.model_dump() for r in b.confirmation_rules]
    data["filters"] = [r.model_dump() for r in b.filters]
    # Cap extra rules
    extra = data["confirmation_rules"] + data["filters"]
    if len(extra) > max_extra:
        data["confirmation_rules"] = data["confirmation_rules"][:max_extra]
        keep_filters = max_extra - len(data["confirmation_rules"])
        data["filters"] = data["filters"][:max(0, keep_filters)]
    # inherit b's stop/target sometimes
    if rng.random() < 0.5:
        data["stop"]["multiple"] = b.stop.multiple
        data["target"]["multiple"] = b.target.multiple
    data["parent_spec_id"] = a.spec_id
    data["generation_method"] = "crossover"
    data["spec_id"] = _next_id(a.spec_id.split("__")[0] + "__cx")
    return StrategySpec(**data)


# ── Fitness ───────────────────────────────────────────────────────────


def evaluate(spec: StrategySpec, df: pd.DataFrame, instrument: str,
             timeframe: Timeframe, cfg: EvoConfig,
             config: BacktestConfig, scoring: ScoringConfig) -> Genome:
    """Overfit-aware fitness: min(IS, OOS) composite, gated on trade count."""
    g = Genome(spec=spec)
    try:
        strat = compile_spec(spec)
        n = len(df)
        split = int(n * cfg.split_ratio)
        is_df, oos_df = df.iloc[:split], df.iloc[split:]

        def _score(d: pd.DataFrame) -> tuple[float, dict]:
            r = Backtester(strat, config).run(d, instrument, timeframe)
            m = compute_metrics(r)
            m["equity_curve"] = r.equity_curve
            m["initial_capital"] = config.initial_capital
            return compute_composite_score(m, scoring), m

        is_score, is_m = _score(is_df)
        oos_score, _ = _score(oos_df)
        full_score, full_m = _score(df)

        g.is_score = round(is_score, 4)
        g.oos_score = round(oos_score, 4)
        g.trades = int(full_m.get("total_trades", 0))
        g.profit_factor = round(full_m.get("profit_factor", 0.0) or 0.0, 3)
        g.max_drawdown_pct = round(full_m.get("max_drawdown_pct", 0.0), 2)

        base = min(is_score, oos_score)  # robustness: both halves must hold
        gate = 1.0 if g.trades >= cfg.min_trades else g.trades / cfg.min_trades
        g.fitness = round(base * gate, 4)
    except Exception as e:  # noqa: BLE001
        g.status = f"error: {e}"
        g.fitness = 0.0
    return g


# ── Evolution loop ────────────────────────────────────────────────────


def evolve(seed_spec: StrategySpec, df: pd.DataFrame, instrument: str,
           timeframe: Timeframe, cfg: EvoConfig | None = None,
           config: BacktestConfig | None = None,
           scoring: ScoringConfig | None = None) -> EvolutionResult:
    """Run the GA from a seed spec; return best genomes (fitness-desc)."""
    cfg = cfg or EvoConfig()
    config = config or BacktestConfig()
    scoring = scoring or ScoringConfig()
    rng = random.Random(cfg.seed)

    df = df.copy()
    df["instrument"] = instrument
    df["timeframe"] = timeframe.value

    result = EvolutionResult(seed_spec_id=seed_spec.spec_id,
                             instrument=instrument, timeframe=timeframe.value)

    # Initial population: seed + param/structural mutations of it.
    population = [seed_spec]
    while len(population) < cfg.population:
        op = rng.random()
        if op < 0.5:
            population.append(param_mutate(seed_spec, rng))
        else:
            population.append(structural_add(seed_spec, rng, cfg.max_extra_rules))

    seen: dict[str, Genome] = {}

    def _eval_all(specs: list[StrategySpec]) -> list[Genome]:
        out = []
        for s in specs:
            if s.content_hash in seen:
                out.append(seen[s.content_hash])
                continue
            g = evaluate(s, df, instrument, timeframe, cfg, config, scoring)
            seen[s.content_hash] = g
            result.evaluated += 1
            out.append(g)
        return out

    genomes = _eval_all(population)
    for _gen in range(cfg.generations):
        genomes.sort(key=lambda g: g.fitness, reverse=True)
        elite = genomes[:cfg.elite]
        children: list[StrategySpec] = []
        while len(children) < cfg.population - len(elite):
            parent = rng.choice(elite).spec
            r = rng.random()
            if r < cfg.p_param:
                child = param_mutate(parent, rng)
            elif r < cfg.p_param + cfg.p_structural:
                pick = rng.random()
                if pick < 0.5:
                    child = structural_add(parent, rng, cfg.max_extra_rules)
                elif pick < 0.8:
                    child = structural_swap(parent, rng)
                else:
                    child = structural_remove(parent, rng)
            else:
                mate = rng.choice(elite).spec
                child = crossover(parent, mate, rng, cfg.max_extra_rules)
            children.append(child)
        genomes = elite + _eval_all(children)
        result.generations_run += 1

    genomes.sort(key=lambda g: g.fitness, reverse=True)
    result.best = genomes[:cfg.elite * 2]
    return result


__all__ = [
    "CONFIRM_POOL",
    "EvoConfig",
    "EvolutionResult",
    "Genome",
    "crossover",
    "evaluate",
    "evolve",
    "param_mutate",
    "structural_add",
    "structural_remove",
    "structural_swap",
]
