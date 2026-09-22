# Autonomous Strategy Lab — Operating Model

**Date:** 2026-06-21
**Status:** review-only research loops. ZERO execution authority — no bot
creation, no broker, no flag flips, operator-only kill switch. Nothing here can
reach live money.

## Joe's target model → the 3-loop design

Joe's description ("scheduled agent that builds / amends / evolves / reviews /
tests each strat; first backfill all strats in batches across all combos; then
systematically add one new/hybrid/tweaked strat at a time, looping forever")
maps cleanly onto the three cooperating cron loops:

| Joe's phase | Loop | What runs |
|-------------|------|-----------|
| **Phase 1 — batched full backfill** | Discovery (batch mode) + Validation | Run **every existing gen-1/gen-2 strat × every instrument × every timeframe** through the robustness ladder, in resumable batches, until the whole grid is ledgered. Builds the complete baseline. |
| **Phase 2 — systematic expansion** | Discovery (incremental) + Validation + Decay-monitor | Introduce **one** new / hybrid / tweaked strat at a time (evolution engine), run it across the full combo grid, ladder it, ledger it, repeat forever. Decay-monitor re-checks survivors. |

Same two-stage shape I proposed; Joe's "build → review → test → edit → review →
evolve" per strat *is* the evolution engine's mutate→evaluate→select cycle, with
the ledger as the review trail. **Phase 2 does not start until Phase 1 is
complete and ledgered.**

## "All combos" made explicit

Full grid = **every strat × every instrument (60 symbols) × every timeframe**,
each run through:

1. Broad backtest (composite + Sharpe), keep ≥ 80 trades.
2. **Robustness ladder, reject on first failure:** walk-forward → held-out OOS
   → Monte Carlo → parameter sensitivity → cost stress.
3. **Pareto-frontier ranking** on (Sharpe, −maxDD, OOS-score, trades) — not a
   fixed-weight composite.
4. **Dedup by `content_hash`** — a spec already tested (same params) is never
   re-run; the ledger is the seen-set.
5. **Correlation/diversity gate** — cluster by trade-entry overlap, keep one per
   cluster, so the output isn't 40 near-identical MACD variants.

Survivors → promotion gate → `paper_candidate` / `research_watchlist` in the
append-only ledger for **human review only**.

## Compute reality (this is the binding constraint)

Registry = **64 strategies**; data = **60 symbols × 6 timeframes**.

- **Literal full grid:** 64 × 60 × 6 = **23,040** combos — *for the broad sweep
  alone*. The ladder multiplies this hugely (walk-forward is dozens of backtests
  per combo).
- **Backtest cost scales with bar count.** H4 ≈ 40k bars (~1s), H1 ≈ 156k
  (~4–6s), M30 ≈ 311k (~10s), M15/M5/M1 = 0.6–1.9M bars (tens of seconds to
  minutes each).

Honest estimates (broad sweep, single backtest per combo):

| Timeframe | Combos (64×60) | Est. sweep runtime |
|-----------|----------------|--------------------|
| H4 | 3,840 | ~1 hour |
| H1 | 3,840 | ~5–7 hours |
| M30 | 3,840 | ~10–13 hours |
| M15/M5/M1 | 11,520 | days each |

So a **literal 6-timeframe full grid is multiple days of pure sweep**, before the
ladder. Two consequences:

- **Ladder runs on H1 + H4 only** (the tradeable swing timeframes for these
  closed-candle strategies; sub-hourly is both noise-prone and compute-bound).
- **Sub-hourly sweeps are opportunistic overnight batches**, ranking-only, not
  laddered, unless a specific combo warrants it.

This isn't a limitation to hide — it's why the work must be **batched,
checkpointed and resumable** rather than one monolithic run.

## Batching / checkpoint / audit plan

- **Unit of work = one (strategy, instrument, timeframe) combo.** Each is
  independent and idempotent.
- **Checkpoint file** (`results/pipeline/checkpoint.jsonl`, append-only): one
  line per completed combo with its `content_hash`, rung reached, and verdict.
  On restart the driver **skips any combo already in the checkpoint** — so a
  killed/interrupted run resumes exactly where it stopped, losing nothing.
- **Dedup by content_hash:** the spec's hash is the key; identical params are
  never re-tested even across runs.
- **Ledger as memory:** every validated / rejected / shortlisted decision is an
  append-only `bot_lifecycle_events` row (research_run_id, oos/mc result refs,
  approval_status='pending', reason). This is the durable "what's been tried /
  what survived" record — the thing Phase 2 reads to avoid repeating work.
- **Batch size:** Phase 1 processes one (timeframe) × (strategy-block) at a time
  so each scheduled invocation is bounded (minutes–an hour), writes its
  checkpoint, and exits — stateless cron, no long-running orchestrator.

## What's wired

- `scripts/full_pipeline.py` — the end-to-end review-only pipeline (sweep →
  diversity → ladder → Pareto → promotion gate → ledger → report).
- `scripts/phase1_backfill.py` — resumable batched-backfill driver (Phase 1).
- `scripts/phase2_systematic.py` — per-strat incremental loop (Phase 2), refuses
  to run until Phase 1 backfill is marked complete.
- Scheduled (cron, review-only): a daily pipeline review task.

## Guardrails (non-negotiable)

Stateless cron functions, not a long-running brain · operator-only kill switch
(no agent path either direction) · walk-forward + held-out OOS are the primary
gates (MC is secondary) · Pareto over composite · correlation/diversity gate ·
**no agent path to execution — output is review-only, full stop.**
