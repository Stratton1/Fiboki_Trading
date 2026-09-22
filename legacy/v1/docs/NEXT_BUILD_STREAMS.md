# Fiboki — Next Build Streams (post IG-demo proof)

**Date:** 2026-06-19
**Context:** IG demo (Z5ZAV) executes real trades; balance moved £20,000→£20,554.
The execution layer is proven; the **product layer must now catch up**. The
target is the full chain: *Bot decided → order placed → IG executed → trade
recorded → chart marked → dashboard updated → Telegram notified → analytics.*

Status legend: ✅ shipped · 🟡 partial/foundation · ⛔ planned.

## Wave 1 — Broker reconciliation & IG trade history ⛔ (TOP PRIORITY)
*If IG records a trade, Fiboki must show it.* Plan in `IG_RECONCILIATION_PLAN.md`.
Key finding: the Gold fill is in the audit as `dealId=DIAAAAXSBQD3EAQ`, but your
IG reference is `SBQLDCAC` (IG's `dealReference`) — store/show both and import IG
transaction history for true broker PnL. Needs IG `/history/transactions` wired
on the worker. **Most credibility-producing item.**

## Wave 2 — Recent execution & clickable dashboard ⛔
Recent-execution panel (time, market, dir, source, size, entry/exit, PnL, bot,
deal ref, status). Cards route/filter to detail (Balance→ledger, Daily PnL→today,
Stale→recovery panel, IG Demo→broker page). Drawers as follow-up.

## Wave 3 — Operating phases & non-destructive reset 🟡
Phase model exists (`EvaluationPhaseModel`, archive/transition). **Shipped this
session:** phase transition now zeroes daily/weekly PnL and the worker reloads
the account on phase change (was carrying −£193 into Phase C). Remaining: phase
scoping on dashboard metric toggles (current/lifetime/since-reset). See
`OPERATING_PHASES.md`.

## Wave 4 — Chart workspace ⛔
Ichimoku overlay ON the chart (not below); session overlays; trade entry/exit
markers (paper/IG/backtest) with PnL badge + deal ref tooltip; click→detail;
chart-native backtest/research drawers. Highest *demo* value.

## Wave 5 — Bot fleet operations & stale recovery 🟡
**Shipped this session:** `POST /paper/bots/restore-stale` — restores errored
bots (state→monitoring, clears error) and classifies monitoring-but-stale bots
(`data_or_worker_gap`) under `needs_attention`. Remaining: worker-side auto-heal
(re-warm a stale monitoring bot in-process), richer health classes
(market_closed / worker_offline / missing_data / blocked_by_risk) and a UI panel.

## Wave 6 — Short/sell PnL correctness & Telegram reality ⛔
PnL/outcome must be direction-aware (short + price-down = profit). Telegram:
`🔴 SHORT opened … / ✅ SHORT closed profit +£554 — deal SBQLDCAC`. Add formatting
tests. (Note: backend trade PnL is already direction-correct via `Position`; the
risk is in UI/alert *presentation* — audit those.)

## Wave 7 — Research/backtest/scenario/job UX ⛔
Group recent/best/failed; direct result links; promotion recommendation; research
bubble chart (return × drawdown, size=trades, colour=stability); job retry + "view
exact result".

## Wave 8 — Agent system ✅ (foundation) 🟡 (expansion)
Wave-3 ledger + 8 skills specced (`AGENT_SKILLS_SPEC.md`); 4 priority skills now
exist as real files under `.claude/skills/`. Remaining: the broader skill/agent
set in this doc's spec + `.claude/agents/`.

---

## Direct answers to operator questions

**Promote from shortlist / see if already promoted / detect duplicates.**
Today: promote via "Promote to Paper" (research, score ≥ 0.55) or "Create Paper
Bot" (backtest); it stamps `source_type`/`source_id`. There is **no dedupe** and
no "already promoted" badge — promoting the same strategy+instrument+TF twice
makes duplicate bots. Fix (uses the Wave-3 ledger): write a `promoted_to_paper`
lifecycle event on promote; show "already promoted ✓ / N copies" and block/﻿warn
on duplicates. Planned.

**Tweaking/cloning/enhanced bots (the factory).** Foundation is in place: the
parameter-variation engine exists; the ledger + `strategy_lineage` track every
clone/mutation with provenance. Next: wire `fiboki_strategy_author` →
`fiboki_research_operator` → `fiboki_promotion_committee` to generate variants →
validate (no lookahead/overfit) → write lineage → recommend. Promote only
robustness-ranked candidates, never raw-profit.

**All IG-tradable instruments.** `ig_client.search_markets`/`get_market` can
enumerate the Z5ZAV universe. Caveat: research/backtest needs price history per
symbol (you have 60 canonical datasets) — trading-universe ≠ research-universe;
expanding research requires a data pull per new symbol.
