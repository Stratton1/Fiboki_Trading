# Fiboki V2: Agentic Integration Plan

**Status:** approved plan, execution in progress. **Date:** 2026-09-28. **Owner:** Joe.
**Inputs:** code-level due diligence on six external repositories (research/reports A to E, 46k words, all claims file:line cited), the V2 codebase at `v2/integration`, and the operating rules in `AGENTS.md`.

This document replaces the question "how do we bolt Vibe-Trading, TradingAgents, freqtrade, FinceptTerminal, backtrader and AI-Trader onto Fiboki" with the answer the evidence supports: **none of their code enters Fiboki; eleven of their patterns do, in a fixed order, each gated by a test, and the LLM's influence on money is bounded by policy constants that live outside the agent package.**

---

## 1. Executive summary

Joe's goal: agents that run continuously, scan markets, news and data for entries, and trade automatically. The honest version of that goal, which this plan builds, is:

1. **Deterministic systems find and take entries.** Compiled DSL strategies on closed bars, sized once, permitted by the 18-check risk gateway, routed by the adapter. This exists.
2. **Agents run continuously around that core**, on a schedule, against a real model (local first), doing what the evidence says they are good at: reading text, classifying events, proposing hypotheses, auditing results, triaging incidents, writing briefs. Every action lands in the append-only audit chain.
3. **Agent output reaches trading only through two bounded, policy-owned, off-by-default channels**: an *event veto* (block new entries during an unscheduled high-severity event) and a *conviction dampener* (reduce, never increase, size when a structured debate disagrees with a signal). Both are shadow-logged for months before either is switched on, and each must beat a deterministic baseline in a pre-registered test or it is deleted.
4. **Never:** order authority, sizing authority, risk-limit authority, kill-switch authority. This is enforced by the five structural mechanisms already in `fiboki.agents` and by AST tests; the plan adds no execution noun to any enum.

Why not simply integrate the repositories:

| Repo | Licence | Verdict | Decisive reason |
|---|---|---|---|
| Vibe-Trading | MIT | Port patterns | LLM is the order originator, capped by code: the inverse of the cardinal rule. Lockfile conflicts with every numerical pin and with `click==8.1.8`. |
| TradingAgents | Apache-2.0 | Port one pattern (debate) | Unsatisfiable with `pandas==2.2.3` (measured). Equities-only: every Fiboki instrument scored against SPY, EURUSD classed as a stock. Paper evidence is one quarter, three tickers, gross of costs; independent 20-year replication is negative. |
| freqtrade | GPL-3.0 | Port operational patterns | Crypto-only (CCXT has no OANDA/IG/IBKR). Its order path has no client ID, persists after dispatch, and treats a timeout as retryable: the V1 failure Fiboki was rebuilt to prevent. GPL forecloses copying. |
| backtrader | GPL-3.0 | Reference only | Frozen since 2023, crashes on 3.11 in one path, OANDA/IB stores unimportable. |
| FinceptTerminal | AGPL-3.0 + contradictory commercial overlay | Reject | Licence texts contradict each other at the same commit; connectors are not point-in-time (FRED without vintages, COT without release lag); Qt/C++ desktop app. |
| AI-Trader | No LICENSE file | Reference only | Not an agent: a hosted copy-trading venue; no FX; leaderboard accepts backdated fills. |

The evidence on LLM trading performance (report D) is the load-bearing finding: LLM *decision* agents do not beat simple baselines net of costs over long windows and wide universes, in-sample LLM backtests are inflated by weight contamination (up to 67% in one correction study), vendor cutoffs are unreliable by up to eight months, and no credible post-cutoff costed study exists for FX, gold or indices at H1 to D1. The one design that holds up is an LLM as a *bounded modifier* of a deterministic strategy, which is what §4 builds.

---

## 2. Decisions taken (and what changed from before)

| # | Decision | Previously | Why |
|---|---|---|---|
| D-A1 | No third-party trading framework becomes a dependency or sidecar. | Open question. | Pins, licences, architecture (reports A to C). |
| D-A2 | Agent output influences trading only via veto-only and down-only channels, constants in `portfolio`/`risk` policy, `enabled=False` by default, shadow first. | No channel existed. | Report D §3.3, §6; TradingAgents design in report B §6.3. |
| D-A3 | Historical LLM signals are never backtested for promotion. Evaluation is forward (shadow) only. Pre-cutoff backfills are exploratory and cannot promote. | Not stated. | Contamination literature (report D §1.2, §5.5). |
| D-A4 | The economic calendar is deterministic and licence-clean (official publisher schedules), not an agent and not a scraped aggregator. | Empty calendar, fail-open. | Report D §4.1; ForexFactory/Investing.com terms forbid extraction. **Shipped this wave.** |
| D-A5 | News is recorded first, classified second. A Fiboki-owned append-only headline recorder (central-bank RSS plus one licensed API) with first-seen UTC starts now; no cheap FX-grade point-in-time news archive exists to buy. | Nothing. | Report D §4.2. |
| D-A6 | Local-first models (Ollama) with pinned weights hash; remote providers allowed for research roles only, cost-capped per session (USD 0.25 today). | Providers existed, none wired. | Reproducibility; report D §5.4. |
| D-A7 | Dukascopy bulk pulls are paused until its licence is clarified in writing; HistData and OANDA candles are the price sources. | Dukascopy provider in use. | Report D §4.5 (website terms forbid bots and database construction). |
| D-A8 | The two operators' capital arrangement is a legal question to settle before any shared or pooled trading. | Not raised. | Report D §3.4 (perimeter). |

---

## 3. What was shipped in this wave (commits 9465012..ce7101f)

Prerequisites for any autonomous operation, all test-backed, full suite 3,604 passed:

- Holdout registry refuses incomparable keys; every derived key is versioned (`9465012`).
- Audit ledger is safe across processes (flock, verify-before-append, fork refusal). Agent clock pinned (`ToolContext.as_of`). Settings strict; every `FIBOKI_*` variable declared (`141c6a6`).
- Dated official economic calendar: 339 events, 2024 to 2026-12 complete, 2027 partial; CLI `fiboki calendar status|check`; validation and paper sessions refuse an empty calendar (`c06e284`).
- API tells the truth: worker heartbeat from the heartbeat table; paper journal served with PAPER provenance; seed never wears PAPER; promote requires per-caveat acknowledgement; consequences server-computed (`f8ec1e6`).
- Live plumbing: OANDA polling bar feed (closed bars only, never gap-fills, calendar-aware), fail-closed startup reconcile, read-only retry proven by AST (`8abd4bf`).
- Frontend trust fixes: no blanking on poll, caveat checklist, MIXED provenance, UTC labels (`c40c886`).

Open from this wave: calendar not yet passed into the paper gateway or `campaign.run_cell`; `EngineEvaluator` cache key omits the blackout source; no HTTP `Transport` or live spread source exists, so nothing composes a live worker yet (ARCHITECTURE §12); exit code 75 now has two meanings and `deploy/README.md` should say so.

---

## 4. Target architecture: where agents sit

```
                     ┌──────────────── fiboki.agents (no execution capability) ────────────────┐
   scheduled jobs →  │ roles: research_director … market_regime_analyst, failure_investigator, │
   (orchestrator)    │        + event_classifier (new, NO tools), thesis_advocate, thesis_arbiter│
                     │ tools: 12 reads + research writes + queued jobs; ToolContext.as_of pinned │
                     │ outputs: JSON only, schema extra="forbid", audit chain, run manifest hash │
                     └───────┬─────────────────────────┬──────────────────────────┬────────────┘
                             │ research artefacts       │ EventAnnotation (quarantined store)   │ ConvictionReading
                             ▼                          ▼                                       ▼
                    research ledger            marketstate.events (deterministic policy)  portfolio.construction
                    (hypotheses, DSL,          veto-only: blocks NEW entries when            _step_conviction:
                     critiques, forecasts)     severity ≥ policy threshold; missing = no veto  factor ∈ [floor, 1.0], never > 1,
                                                                                               missing/stale = 1.0, enabled=False
                             │                          │                                       │
                             ▼                          ▼                                       ▼
                    validation ladder ──────►  ALPHA (DSL signals) → PORTFOLIO (size once) → RISK (18 checks) → EXECUTION
```

Invariants added by this plan, each with a test:

- `Capability` gains `WRITE_EVENT_ANNOTATION`, `WRITE_FORECAST`, `WRITE_DEBATE_TURN`, `WRITE_CONVICTION` (all pass `assert_no_execution_capability`). No new `JobType`.
- `EventAnnotation` and `ConvictionReading` are frozen contracts in `core/contracts.py` containing no free text and no prices, stops or sizes.
- The policy constants (`EventVetoPolicy`, `ConvictionPolicy`) live in `marketstate`/`portfolio`, are versioned, stamped on every allocation and risk decision, and default to disabled.
- AST tests: `ConvictionReading` is constructed only in the runtime adapter and consumed only in `_step_conviction`; `EventAnnotation` is consumed only in the event policy; `fiboki.agents` still imports nothing from `broker`, `risk`, `portfolio`.
- The event classifier role has **no tools** and never sees strategy IP (context minimisation against prompt injection: it reads untrusted text, so it must have no consequential action and no exfiltration channel).

---

## 5. Execution waves

Engineer-days assume one engineer who knows the codebase; with Opus subagents executing and Fable reviewing, calendar time compresses but review does not.

### Wave 2: agents on real models, read-only (≈ 14 days)

| Item | Pattern source | Fiboki home | Days | Acceptance |
|---|---|---|---|---|
| Wire `LocalHTTPProvider` to a running Ollama; provider smoke test; weights hash + model id in every audit record | D-A6 | `agents/providers.py`, `workers/runtime.py` | 2 | First real-model run of `run_failure_investigation` recorded in the ledger with cost and tokens |
| Run manifest hash (system prompts, tool schemas, package versions) stamped per workflow run | Vibe #2 | `agents/audit.py`, `agents/workflows.py` | 1.5 | Changing a role prompt changes the manifest; test |
| Offline eval harness over recorded runs: PASS/FAIL/NOT_EVALUABLE, missing evidence is never a pass | Vibe #5 | new `agents/evals/` | 4 | Cases: critic cited a validation report id; no step phrased a trade instruction; every number in a note cites a call id |
| Pre-registered forecast record + deterministic scorer (Brier, hit rate per role/model after `horizon_end_at`) | AI-Trader B | `agents/tools.py`, `research/artefacts.py`, scoring job | 4 | Forecast vocabulary cannot read as an order; scores feed `ModelRouter` |
| Register research handlers; scheduled `run_research_cycle` nightly on local model | ROADMAP §6 | `workers/runtime.py`, orchestrator | 2 | One full cycle end to end with real model, all steps audited |
| Calendar into paper gateway and `campaign.run_cell`; `EngineEvaluator` cache key includes blackout source | this wave's gap | `workers/runtime.py`, `discovery/campaign.py`, `validation/engine_evaluator.py` | 1 | Paper session summary shows `wired_into_gateway: true`; a blackout blocks an entry in a replay test |

### Wave 3: data the agents will read (≈ 14 days, deterministic, no LLM)

| Item | Days | Acceptance |
|---|---|---|
| Headline recorder: central-bank RSS (Fed, ECB, BoE, BoJ, SNB, RBA) + one licensed API (Finnhub personal or Marketaux Basic), append-only store with `observed_at` (first seen), vendor timestamp, source, URL hash; dedupe; runs under worker supervision | 4 | Recorder survives restarts without duplicates; 7 days of continuous capture |
| Point-in-time macro providers: ALFRED vintages, CFTC COT with Friday release stamping, ECB SDMX, BoE IADB, ONS, NY Fed; each declares `available_at` semantics | 6 | Provider tests with recorded transports; look-ahead test: a query as-of a date never returns a value released after it |
| `READ_NEWS_SNAPSHOT` capability and `query_news` tool: point-in-time only (`available_at <= ctx.as_of`), headlines delivered as quoted data | 2 | Tool refuses when `ctx.as_of` is None |
| Bar-indexed cooldown and stop-streak locks declared in the DSL, enforced identically in engine, paper and gateway | 3 | Parity test; locks survive a restart via the intent ledger |
| Ops Telegram bot: read-only commands plus `/killswitch_arm` only; mandatory user allow-list; every command audited | 3 | No disarm, no force-entry, no force-exit reachable |

### Wave 4: the two bounded channels, shadow only (≈ 20 days)

| Item | Days | Acceptance |
|---|---|---|
| `event_classifier` role (no tools): input is the headline store; output `EventAnnotation {event_type enum, currencies, severity 0..3, scheduled bool, confidence, source_ids, observed_at, available_at, model_id}` into a quarantined table | 4 | Schema refusal on any extra field; a headline containing an instruction is stored as data, never acted on (injection test) |
| `EventVetoPolicy` (deterministic): may only block NEW entries; missing annotation = no veto + alert; `enabled=False`; shadow evaluator records would-have-vetoed per trade with counterfactual P&L | 4 | Never touches sizing, stops, exits or the kill switch (AST) |
| `MarketBrief` builder (deterministic evidence pack with stable `evidence_id`s) | 2 | Reproducible from the persisted brief hash |
| `ThesisDebate` workflow: two `thesis_advocate` turns (≤2 rounds, ≤5 claims each, every claim cites evidence ids and names a falsifier), one `thesis_arbiter` that reads the brief as well as the transcript; `ConvictionReading {stance, strength 0..2, valid_until}` | 6 | Vague-phrase filter reuses the critic's; unparsed = no conviction; cost ≤ USD 0.25 per instrument-day on a small model |
| `ConvictionPolicy` + `_step_conviction` in `portfolio/construction.py`: factor ∈ [0.5, 1.0], disagreement only dampens, agreement is 1.0, stale/missing is 1.0, `enabled=False`; shadow logging with `Provenance.SHADOW` | 3 | Property test: factor ≤ 1 always; golden arithmetic test |
| Pre-registration artefacts for both channels: hypothesis, metric (adverse excursion and net expectancy on affected trades), minimum event count, decision date, model pin, baselines (no veto; deterministic vol/spread-spike filter; scheduled-calendar-only) | 1 | Filed before any shadow data is read |

### Wave 5: forward evaluation (calendar time, not engineer time)

Shadow runs for the pre-registered period (minimum: the later of N months or 80 affected trades). Decision at the pre-set date, not before. Either channel is enabled only if it beats its deterministic baseline; otherwise it is deleted, not tuned. Enabling is human-in-the-loop (operator sign-off, as for strategy promotion) and remains veto-only or down-only for a further period before any change to the policy floors is considered.

### Never (recorded so the question is not reopened casually)

LLM order authority; LLM sizing; LLM-set risk limits; LLM kill-switch control; LLM-authored executable code (the DSL is data); an API process that starts a trading runner; a `bash` tool for agents; free-text hand-offs between agents that feed a decision.

---

## 6. Costs and what to expect

- Running cost for the whole agent layer at design B (classify each headline once, map to instruments deterministically): about USD 12 to 55 per month on a small hosted model, or zero on a local model. Cost is not the constraint; evidence is.
- The realistic outcome, stated in advance: the event veto may add nothing beyond a deterministic volatility filter, and the conviction dampener may add nothing beyond the existing regime scalar. If so, both are removed and the agent layer remains a research, triage and reporting layer. That is still a good outcome: the research cycle, the eval harness and the forecast scoring make the agents' contribution measurable for the first time.

---

## 7. User actions this plan depends on

1. OANDA Europe practice account and confirmation that the v20 API can trade a spread-betting sub-account under its terms.
2. A decision on the news API tier (Finnhub personal is free; Marketaux Basic is about USD 29 per month).
3. Written clarification from Dukascopy before any further bulk pull; until then the provider stays paused.
4. A running Ollama with a pinned 20B to 30B model on the Mac for Wave 2.
5. A solicitor's view on the two-operator capital arrangement before any shared trading.

---

## 8. Risks

- **Contamination masquerading as edge.** Mitigated by forward-only evaluation, measured cutoff probes and pre-registration. Residual: none if the rules are followed.
- **Prompt injection through headlines.** Mitigated by a tool-less classifier, schema-only outputs, quarantined store, deterministic consumer. Residual: a wrong annotation, which can only block an entry.
- **Operator anchoring on fluent prose.** Mitigated by labelling every agent note "commentary, not a signal" and linking every claim to its run and dataset.
- **Scope creep toward LLM decisions after a good month.** Mitigated by this document and the AST tests. The decision to widen any channel requires the same pre-registration as the original.
