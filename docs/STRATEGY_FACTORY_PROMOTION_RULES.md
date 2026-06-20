# Strategy Factory — Promotion Rules

**Purpose:** the single, explicit gate that decides how far a
strategy-instrument-timeframe combination may advance. Encoded as deterministic
code in `backend/src/fibokei/research/promotion.py` (`evaluate_promotion`) and
locked by `tests/test_promotion.py`. This document explains the policy; the code
is the source of truth.

## Core principles

- **Recommends, never executes.** The evaluator returns a recommended lifecycle
  state and the reasons. It flips no flags, places no orders, touches no broker.
- **`live` is never auto-recommended.** The furthest the gate returns is
  `demo_candidate`, with `requires_human_approval = True`. Going live is a
  separate, explicit, per-bot human decision plus Safety Governor sign-off,
  recorded as `proposed_live` / `approved_live` events in the lifecycle ledger.
- **Robustness over raw profit.** A combo cannot pass the watchlist unless it
  holds up out-of-sample *and* under Monte Carlo resampling. Missing evidence
  caps the recommendation — you cannot skip a gate by not running it.
- **Thresholds are explicit and auditable** (`PromotionThresholds`), aligned
  with the validation modules so the policy never drifts from the maths.

## Lifecycle states

```
rejected → research_watchlist → paper_candidate → paper_running → demo_candidate → live
                                                                          └── human + Safety Governor only
```

These mirror the `bot_lifecycle_events` ledger vocabulary. Every transition an
agent or human makes is appended to that append-only ledger with full provenance
(`research_run_id`, `oos_result_id`, `monte_carlo_result_id`, `approval_status`,
actor, reason) so any promotion can be reconstructed.

## Gates

### 1. Hard in-sample gates (fail any → `rejected`)

| Gate | Default | Source |
|------|---------|--------|
| Min trades | ≥ 80 | `scorer.min_trades_full` |
| Profit factor | ≥ 1.10 | conservative floor |
| Max drawdown | ≤ 25% | capital preservation |
| Positive expectancy | net profit > 0 | — |

### 2. Watchlist floor

Passing the hard gates with composite score ≥ **0.30** → `research_watchlist`.
Below the floor → `rejected`. (Composite score = `research/scorer.py`.)

### 3. Robustness gates (→ `paper_candidate`)

All must hold, else the combo stays capped at `research_watchlist`:

| Gate | Default | Source |
|------|---------|--------|
| OOS retention | OOS score ≥ 50% of IS | `research/oos.py` (`robust`) |
| Monte Carlo profit probability | ≥ 0.70 | `research/monte_carlo.py` (`robust`) |
| Monte Carlo ruin probability | ≤ 0.05 (sims with DD>50%) | `research/monte_carlo.py` |
| No realism warnings | none present | backtest realism flags |
| No fleet concentration | `concentration_ok` | portfolio risk |
| Composite score | ≥ 0.40 | paper-candidacy floor |

Absent OOS or Monte Carlo evidence is itself blocking — the recommendation is
capped at `research_watchlist` with a note explaining what's missing.

### 4. Paper → `demo_candidate` (human-gated)

A `paper_candidate` promoted to live paper trading (`paper_running`) earns
`demo_candidate` only after a real paper record: ≥ **40** paper trades with
non-negative expectancy. The decision sets `requires_human_approval = True`.

### 5. `demo_candidate` → `live`

**Not automatable.** Requires an explicit per-bot human approval and Safety
Governor sign-off, gated behind the existing execution feature flags
(paper-first; live hard-blocked by default). Recorded as `proposed_live` then
`approved_live` in the ledger.

## Usage

```python
from fibokei.research.promotion import evaluate_promotion

decision = evaluate_promotion(
    strategy_id="factory_hyb_macd_ema_trend_v1",
    instrument="USDJPY", timeframe="H4",
    metrics=backtest_metrics,            # total_trades, profit_factor, max_drawdown_pct, total_net_profit
    composite_score=0.657,
    oos_robust=oos_result.robust,        # from research/oos.py
    mc_profit_probability=mc.profit_probability,   # from research/monte_carlo.py
    mc_ruin_probability=mc.ruin_probability,
    realism_warnings=metrics.get("realism_warnings"),
    concentration_ok=fleet_check_ok,
)
# decision.recommended_state, decision.passed, decision.failed, decision.notes,
# decision.requires_human_approval
```

Tune policy via `PromotionThresholds(...)` — never by editing call sites.

## Applying it to the Phase-7 shortlist

The Gen-1 results (`STRATEGY_FACTORY_GEN1_RESULTS_2026-06-20.md`) are **in-sample
only**, so under these rules every shortlist combo currently caps at
`research_watchlist` — none can reach `paper_candidate` until OOS + Monte Carlo
are run. That is the intended next step: run `research/oos.py` and
`research/monte_carlo.py` on the ~8 shortlist combos, feed the outputs into
`evaluate_promotion`, and only the survivors become paper candidates.

## Known limitations (carried from RULES)

Backtest realism still has static spreads, zero slippage by default, no overnight
financing, and approximate USD→GBP. The realism-warnings gate is the hook to
block promotion when those approximations would materially distort a result;
expanding what populates `realism_warnings` is a tracked improvement.
