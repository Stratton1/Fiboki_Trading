# Fiboki — Next Phases: The Controlled Bot Factory (8-Wave Programme)

**Date:** 2026-06-19
**Objective:** Build a controlled bot factory that can discover, generate, test,
reject, promote, monitor, demote, clone and improve strategies — with live execution
strictly gated. Fast path = automation under strict gates, **not** bypassing gates.
No agent self-approves live money.

**Capital reality:** ~£180 live target. First live aim is *survival and proof of
safe operation*, not income. One overleveraged position can wipe the account.

---

## Wave 0 — Trust reset  ·  STATUS: ~80% done (this session)
- [x] Python 3.11 venv + clean dev install
- [x] `ruff` clean; 104-test core green
- [x] Registry truth (`STRATEGY_REGISTRY_AUDIT.md`)
- [x] Evidence docs: `CURRENT_STATUS`, `TEST_HEALTH_2026-06-19`, `IG_GATE_STATUS`, this file
- [ ] Test hygiene: add `network`/`slow` markers + mocks + default `--timeout`; capture true full-green
- [ ] Refresh production screenshots (Dashboard, Charts, Research, Backtests, Jobs, Bots, Scenarios, Exposure, Alerts, System)
- **Gate:** no agent work until suite is green or every failure triaged. *(Triaged; hygiene fix pending.)*

## Wave 1 — IG Gate 2 proof  ·  STATUS: blocked on rejection cause
Retrieve rejection reason (`IG_GATE_STATUS.md`) → fix true cause → one tiny demo
trade during market hours → prove open/sync/close/audit/slippage/reconcile.
- **Deliverables:** `IG_GATE_2_EVIDENCE.md`, `execution_audit_export.json`, screenshots.
- **Gate:** no live enablement until a demo trade is opened, synced, closed, audited, reconciled.

## Wave 2 — Strategy registry truth (productise)
`/strategies/registry-health` endpoint + `tier` metadata + UI Tradable/Research-only/
Disabled badge + regression test (21 registered / 12 canonical / 0 import errors).
- **Gate:** no autonomous cloning until tiers are explicit and asserted.

## Wave 3 — Append-only lifecycle + agent-run ledger (foundation)
Models: `AgentRunModel`, `BotLifecycleEventModel`, `StrategyLineageModel`. Records
every action (created/backtested/validated/rejected/promoted/demoted/proposed_live/
approved_live/disabled/cloned/mutated/override/agent_decision/safety_veto) with full
provenance (prompt hash, code-diff hash, dataset version, result IDs, actor, reason).
API + UI Lineage/Audit tab + immutability tests.
- **Gate:** no agent may create/modify a strategy without writing a ledger entry.

## Wave 4 — Claude Skills + MCP setup (bounded agency)
Skills: `fiboki_repo_guardian`, `fiboki_strategy_author`, `fiboki_quant_auditor`,
`fiboki_test_runner`, `fiboki_research_operator`, `fiboki_promotion_committee`,
`fiboki_ig_safety_officer`, `fiboki_docs_scribe`. MCP: GitHub, Filesystem, Postgres
(read; writes only via approved migrations), Playwright, Railway/Vercel logs.
- **Gate:** agent may create experimental strategies; cannot promote to demo/live without hard gates + human sign-off.

## Wave 5 — Autonomous Strategy Lab v1 + ranking engine
Pipeline: idea → generated strategy (via Strategy base class) → static checks → unit
tests → backtest → research matrix → OOS/walk-forward → Monte Carlo → scenario
sandbox → paper candidate → human review. Ranking is statistical, **not raw profit**:
trade count, profit factor, expectancy, max-DD, return/DD, OOS degradation,
walk-forward pass rate, MC confidence, slippage/spread sensitivity, fleet correlation,
session & timeframe consistency.

## Wave 6 — Paper fleet scale-up
20–50 paper bots, liquid instruments only, grouped by asset class, max 8 simultaneous
trades / 5% portfolio risk / drawdown hard stops. Daily reports, weekly promote/demote.

## Wave 7 — Demo fleet (post Gate 2)
3–5 demo bots, tiny size, FX majors first. Measure fill latency, slippage, rejects,
sync, stop/limit placement, unexpected closes, weekend/news exposure, reconciliation.

## Wave 8 — Live micro-capital
1–2 bots, FX majors, smallest exposure, daily loss cap tuned for ~£180, kill switch
verified, **human approval per live bot**. Prove safe operation, then scale.

---

## Four agent lanes (operating model)
1. **Builder** — code/tests/migrations/UI. No trading authority.
2. **Quant Auditor** — tries to *disprove* results (leakage, overfit, impossible
   Sharpe, tiny-stop leverage, spread artefacts).
3. **Operator** — runs jobs, collects evidence, prepares promotion recommendations.
4. **Safety Governor** — veto power over feature flags, live mode, kill switch, risk
   caps, ledger, human approval.

## Fastest *safe* order
baseline → registry → IG Gate 2 → ledger → Skills/MCP → Strategy Lab → paper fleet →
demo fleet → live micro-capital.
