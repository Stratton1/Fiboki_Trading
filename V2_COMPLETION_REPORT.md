# Fiboki V2 — Completion Report

**Programme:** Phases A–L, executed continuously on 19 September 2026.
**Verified at completion:** 3,112 tests passing, 2 skipped, 85 of them hand-calculated golden financial tests. `ruff check src` clean. 65,458 lines of source across 22 packages, 36,077 lines of tests.
**Repository:** a self-contained git repository with six checkpoint commits, built in isolation. It has not been merged into `Fiboki_Trading` — see USER_ACTIONS C1.

Status labels are used strictly throughout: **IMPLEMENTED** means the code exists and its tests pass. **VERIFIED** means I ran it and checked the result myself. **PARTIALLY VERIFIED** means tested in part, with the untested part named. **UNWIRED** means implemented and tested but nothing calls it in a running system. **BLOCKED** means waiting on an external dependency. **REQUIRES FORWARD DATA** means it cannot be verified until the platform has been running for a period.

---

## 1. The headline

Fiboki V2 is an honest research platform that currently says: **no strategy qualifies.**

Campaign K1 ran 326 true trials on XAUUSD H4 and produced zero survivors. Twenty-six candidates died at the sanity rung, four at walk-forward, none reached deflation, and the holdout has never been consumed by anything. Nothing was tuned to produce a different answer.

That is the programme working. The point of the rebuild was to stop the platform from manufacturing false positives, and the most direct evidence that it has stopped is that it now returns nothing when there is nothing.

Two results from the build are worth more than any strategy ranking.

**The V1-versus-V2 procedure contrast, measured on real market data.** On XAUUSD H4, the V1 procedure — evaluating fixed default parameters on the test window — reports a positive out-of-sample rate of 0.0394 and a 50% window hit rate for the Ichimoku seed. The V2 procedure, which selects parameters on the training window and transfers *that* selection to the test window, loses in every single fold: walk-forward efficiency −150.96%, hit rate 0 of 4. Same strategy, same data, same engine. The difference is entirely the honesty of the procedure.

**Implementing the strategies correctly made two of them worse.** Once the engine honoured the exit vocabulary the documents actually declared, the Ichimoku seed went from +$720 to −$52 and its trade count fell from 71 to 49, because `avoid_rollover_hour` — declared `true` by every seed document and silently ignored by the engine — removes one H4 entry bar in six. The Donchian seed went from 6 trades in thirteen years to 583, because its ATR chandelier trail had never executed. Neither change was a tuning decision; both were the engine stopping lying about what the strategy said.

---

## 2. What each phase delivered

| Phase | Deliverable | Status |
|---|---|---|
| A — Freeze and archive V1 | `docs/v2/V1_FORENSIC_BASELINE.md` with the component classification matrix and the full defect inventory, including data defects | VERIFIED |
| B — Design V2 | 15 architecture and standards documents in `docs/v2/` | IMPLEMENTED |
| C — Data foundations | Raw/canonical layers, integrity without silent repair, content-addressed dataset versioning, live recorder, execution telemetry, provider interfaces, V1 migration | VERIFIED |
| D — Simulator | Both-leg costs, gap-aware fills, configurable intrabar policy, financing, five broker profiles, full DSL exit vocabulary, 85 golden tests | VERIFIED |
| E — Strategy SDK/DSL | Versioned pydantic schema with mandatory hypothesis and stop, parameter references and binding, deterministic compiler, content hashing, 5 seeds | VERIFIED |
| F — Validation | DSR, PBO/CSCV, SPA/StepM, stationary bootstrap, purged CV, stress, stability; 7-rung ladder; holdout registry that refuses reuse | VERIFIED |
| G — AI agents | 12 roles, 19 capabilities, 25 tools, stateless orchestrator, append-only audit, local-first providers | IMPLEMENTED |
| H — Market intelligence | 71 causal features, 5-axis regime classification, cross-asset, calendar interface, state engine | VERIFIED |
| I — Portfolio/risk/execution | Single sizing authority, correlation-aware construction, mandatory 18-check gateway, kill switch, broker adapters, 5 live controls | VERIFIED |
| J — Operator platform | FastAPI with structural provenance enforcement; Next.js workstation, 28 pages, 218 KB gzipped client JS | VERIFIED |
| K — Discovery | Hypotheses with refutation criteria, 9 mutation operators, novelty detection, campaign runner with true-trial-count propagation | VERIFIED |
| L — Forward readiness | 13 lifecycle states, 105 legal transitions, 11-dimension divergence monitor, 3 pre-registered stopping rules | IMPLEMENTED, UNWIRED in part |

---

## 3. Component migration matrix

| V1 component | Decision | Reason |
|---|---|---|
| Strategy factory (spec/compiler/primitives) | **KEEP**, rebuilt on | The strongest thing in V1 — declarative, content-hashed, 43 specs with zero duplicate rule signatures |
| `ExecutionAdapter` ABC and the signal→attempts audit schema | **KEEP** | Correct broker-neutral abstraction; the parent-child audit shape is right |
| Hardcoded IG demo base URL | **KEEP** permanently | V1's single most effective safety control; a compile-time impossibility beats any flag ladder |
| Determinism discipline (sorted iteration, seeded MC) | **KEEP** | Hardest property to retrofit; V1 had it |
| Canonical parquet data layer | **REFACTOR** | Right shape, wrong provenance handling — no versioning, silent repair, path resolution that walked up the tree |
| Indicator library | **REWRITE** | Centred swing detector, `chikou_span` future value in the strategy frame, direction-blind Fibonacci, silent VWAP degradation |
| Backtest engine | **REWRITE** | Entry-only spread, unused commission, no financing, exact-level fills through gaps, realised-only drawdown, one position |
| `metrics.py` | **REWRITE** | `sqrt(252)` regardless of trade frequency, NaN mapped to flattering values, synthetic hourly index |
| `research/scorer.py` | **REWRITE** | Sign-blind stability, NaN profit factor scoring full marks, drawdown rewarded without reference to returns |
| `research/walk_forward.py` | **REWRITE** | Fitted nothing — ran the same fixed parameters on both windows |
| `risk/engine.py` | **REWRITE** | Zero call sites; limits rendered on a dashboard and enforced nowhere |
| `PaperBot.on_candle_close` | **REWRITE** | Reimplemented the fill path; six divergences from the backtester, all optimistic |
| Tradovate client | **RETIRE** | Endpoints self-documented as `TODO_VERIFY`; single-env-var bypass to live |
| `BotOrchestrator`, legacy single-adapter path | **RETIRE** | Dead code doubling the surface of order placement |
| V1 frontend | **REWRITE** | Backtest trades labelled "Recent Execution"; outages rendering as zeros; 14 nav items for 5 jobs |
| V1 research results (`results/phase7`, checkpoints) | **ARCHIVE** | Invalid on data grounds alone, before engine defects are considered |
| 52 docs in `docs/` | **ARCHIVE** | Mutually contradictory; superseded by `docs/v2/` |

---

## 4. Tests and verification

3,112 passing, 2 skipped (both legitimate — a feature genuinely unavailable on a dataset without quotes). 85 golden tests carry hand-calculated arithmetic in comments so a human can check them with a calculator.

The tests that matter most, and what each proves:

**Cross-process determinism.** Three subprocesses with different `PYTHONHASHSEED` values produce an identical SHA-256 of the trade ledger, on a scenario exercising trailing stops, partial exits, time stops and cooldown. V1's determinism test ran the same backtest twice in one process, which catches only RNG issues.

**Causality.** Every indicator and all 71 market-state features are tested by corrupting future bars and demanding a bit-identical prefix — 213 assertions for features alone, plus a negative control that a deliberately leaky implementation must fail.

**Structural parity.** The backtest engine and the paper broker both drive one shared `PositionBook`; the ledgers are compared byte-for-byte across every broker profile and intrabar policy. The earlier parity test passed only because it compared a degenerate single-target case — worse than a failing test, and caught by the agent that built the replacement.

**No gateway bypass.** An AST walk of the entire source proves `Order` is constructible in exactly one function, and that function calls the risk gateway.

**No agent authority over execution.** The capability enum cannot express execution: an import-time assertion parses every member and raises for the whole package if a mutating verb meets an execution noun. A separate AST test proves no module under `agents/` imports the execution layer at all.

**Holdout discipline.** The registry writes its consumption row before evaluation and refuses any second claim on the same content hash.

**Published figures reproduced.** Expected maximum Sharpe at N=100, 1,000 and 23,040 with sd(SR)=0.5 gives 1.2653, 1.6276 and 2.0301 against the published 1.27, 1.63 and 2.03. I verified these myself rather than taking the report's word.

---

## 5. The validation methodology, and why it is different

The V1 platform searched 23,040 strategy-instrument-timeframe combinations and applied no multiple-testing correction of any kind. At a naive 5% threshold that yields roughly 1,152 spurious survivors from pure noise. Its "held-out" out-of-sample segment sat inside the window that had already decided the combination was worth testing.

V2's ladder is seven fail-fast rungs against a versioned gate set (`v2.0.0-audit`): at least 400 trades, walk-forward efficiency ≥ 50%, out-of-sample window hit rate ≥ 60%, deflated Sharpe > 0.95, probability of backtest overfitting < 0.2, SPA consistent p < 0.05 with StepM survivor membership, the edge surviving 2× spread, and a point-to-plateau Sharpe ratio ≤ 1.25.

Three structural guarantees sit underneath. A gate nobody computed returns NOT_EVALUATED, which **blocks** promotion rather than passing silently. The final 20% of every dataset is owned by a registry that refuses a second look. And a campaign fixes its true trial count *before* any compute is spent, then passes it into deflation — so a cell knows it was one of 326 attempts, not one of 64.

One deliberate deviation from the brief, flagged by the agent who made it: the purged-CV path distribution is systematically narrower than the cross-trial Sharpe variance the deflated Sharpe expects, because paths share one return process while trials do not. Using it alone would make deflation *less* demanding, so the implementation takes the maximum of the two and records both. The path distribution can only tighten the bar, never loosen it.

---

## 6. Strategy and experiment inventory

Five seed strategies, each carrying a mandatory hypothesis with an explicit "evidence against" section: Ichimoku kumo trend, Donchian breakout with ATR trail, RSI band mean reversion, MACD/EMA trend hybrid, and a Fibonacci golden-pocket pullback.

That honesty is not decorative. Deng et al. (2021) tested Ichimoku on four indices and four currency pairs and concluded that some strategies may be profitable on stock indices but **none was found for currency trading** — before costs. Coakley, Marzano and Nankervis (2016) tested 113,148 rules on 22 currencies with a Step-SPA correction and found moving-average-family rules came back with p-values "very close to one". Every seed states the case against itself.

Campaign K1: 326 true trials, 30 mutants evaluated, 13 skipped as out of universe, 15 mutations refused before compute (8 on clashing parameter domains, 5 with no filters to remove, 2 on reference-valued bounds), 7 skipped as non-novel. Zero survivors. 222 ladder evaluations, 618 seconds cold and 12.9 seconds on a cached re-run — which doubles as a determinism check.

---

## 7. Best surviving candidates

**There are none, and I will not manufacture one.**

The nearest approaches were the Donchian breakout with ATR trail, which now clears the 400-trade production minimum and dies at walk-forward efficiency 38.86 against a required 50, and the MACD/EMA hybrid at 5.6. Neither is a finding. Both are strategies that failed a gate, and the gate is the point.

A necessary caveat on the rejections: the dominant reason was the 400-trade minimum, a bar written for a campaign across 60 instruments. A single instrument on a single timeframe mostly cannot reach it. Most cells were therefore rejected for having **too little evidence, not bad evidence**. Nothing here demonstrates that the underlying effects do not exist — only that this data cannot establish that they do.

---

## 8. Data inventory and what the migration found

Two instruments were available in the build container: EURUSD (H1 155,808 bars, H4 40,541) and XAUUSD (H1 99,945, H4 26,837), 2000–2025 and 2009–2025 respectively.

The migration found that V1's canonical store was corrupt in ways that invalidate its research independently of every engine defect:

Timestamps were **five hours out** everywhere — EST-without-DST stamps carrying a UTC label. The evidence is empirical rather than assumed: a genuinely UTC FX series shows two distinct weekly-open hours across a 25-year span as daylight saving shifts; these files show exactly one.

Prices are **bid, not mid**, and were treated as mid throughout V1.

Volume is **identically zero** on all four datasets, which means any volume-dependent strategy in V1 was computing on a flat feature and producing a plausible-looking backtest built on nothing.

EURUSD H1 contains a **negative-price sentinel bar** — OHLC all −0.0001, at 2001-09-11 20:00 — sitting in the canonical store. I verified this bar myself. It destroys every log return crossing it. V2's integrity layer classifies both EURUSD datasets as rejected and refuses to read them as clean.

---

## 9. Capability summary by domain

**Live data:** recorder IMPLEMENTED and crash-safe, BLOCKED on a broker credential. Telemetry store IMPLEMENTED. Provider decoding and URL construction real and tested for Dukascopy and OANDA; the HTTP call itself raises rather than returning empty, deliberately.

**AI agents:** 12 roles, 19 capabilities, 25 tools, stateless queue-based orchestrator, append-only audit ledger, provider adapters with a deterministic echo provider so the full workflow runs offline. IMPLEMENTED. Remote providers are interface-only in tests; no LLM has yet been run against the platform.

**Portfolio and risk:** single sizing authority VERIFIED end to end; 18-check gateway VERIFIED with an AST bypass proof; kill switch with distinct PAUSE and FLATTEN VERIFIED. Four `RiskContext` inputs — daily P&L, weekly P&L, correlated exposure and realised portfolio volatility — default to zero and have no production data source, so those checks execute and are named but currently run against zeros. PARTIALLY VERIFIED.

**Execution:** paper broker VERIFIED at byte-level parity with the backtester. OANDA and IG adapters IMPLEMENTED against documented REST shapes and fixture-tested only; first contact with a real endpoint will find discrepancies. Neither has a position manager, so a demo deployment today would not trail. Five independent live controls VERIFIED, every one-, two- and three-way subset asserted still blocked.

**Operator platform:** API VERIFIED — provenance is enforced structurally by a test that walks every response model and fails on a bare float. Web workstation VERIFIED to build, typecheck, lint and pass 102 Playwright tests. Trade and position rows are a deterministic seed fixture labelled as such and reported as a health degradation, not measurements.

---

## 10. Security assessment

Authentication is an httpOnly, Secure, SameSite cookie with Origin/Referer allow-listing on every mutating request and double-submit CSRF where absence is refusal rather than a pass. Login is rate-limited and returns an identical response for an unknown user and a wrong password. RBAC is real and applied: a test enumerates every registered route and walks its dependency tree, and the two mutating routes that are not admin-gated (login and logout) are allow-listed with stated reasons.

There is no endpoint that can enable live execution. The attempt returns 403, enumerates the five mode-guard controls and audits the attempt.

Agents operate under deny-by-default least privilege with no capability that grants execution, and the audit trail is hash-chained so tampering is detectable.

Dependencies are pinned exactly, including `click` — which was unpinned initially and broke every CLI invocation when a clean resolve produced 8.5.0.

Not defended against, and stated plainly: a compromised operator workstation, a malicious dependency inside a pinned version, and physical access to the machine holding the state directory.

---

## 11. Unresolved defects and technical debt

The IG and OANDA adapters have no position manager — the largest item before demo enablement.

The lifecycle service is implemented and tested but nothing schedules `evaluate()` outside the worker path that was wired late; treat it as PARTIALLY UNWIRED until you have watched it run.

`MarketStateEngine.ingest_bar` recomputes features over the retained window, so it is O(n²) on a long replay and was dropped from the long demo. It needs incremental features or a cap before production.

`research/structure.py`'s structural fingerprint has no token for session or event restrictions, so two documents differing only in when they may deal share a hash. The discovery layer works around it at the decision layer rather than in the fingerprint.

`ExitReason` has no `BREAKEVEN` member, so a breakeven stop-out currently reports as `TRAILING_STOP`. The enum's values are persisted, which is why it was not widened unilaterally. It needs a decision.

Backfilled prior trials are double-counted for one strategy in the K1 trial accounting. This makes deflation *more* demanding, so the error is in the safe direction, and it is recorded rather than quietly corrected.

Every stored backtest predating the exit-vocabulary change is invalid. The evaluation cache key was bumped so stale entries miss, and a supersession sweep marks old records rather than deleting them.

**A correction to my own earlier audit.** The audit of 19 September quoted minimum track record lengths of 2.8 years to separate an observed Sharpe of 1.0 from zero and 11.2 years from 0.5. Computed against the implemented function at Gaussian moments and 95% confidence, those are 5.1 and 17.2 years. The implemented values are more demanding, so the direction is safe — but the audit figures were wrong and the implementation is right.

---

## 12. Recommended next research

Extend the campaign across instruments and timeframes once the full data store is migrated. The 400-trade minimum is reachable across 60 instruments and mostly unreachable on one, which is why K1's rejections were dominated by insufficient evidence rather than by disproof.

Run the Fibonacci falsification experiment — compare 0.618 against random levels between 0.5 and 0.7 on the same swings. The hypothesis document specifies it; nobody has run it. If it fails, cut the family.

Start the price recorder now and revisit every backtest in six months against prices from the venue that will actually fill you. This is the only route to closing the parity gap honestly.

Resist adding strategies. Every one raises the deflation bar for all the others, which is why V2 ships five seeds where V1 had 64.

---

## 13. What I did not do

I did not merge V2 into your repository, deploy anything, open any account, spend any money, touch the V1 production systems, or enable any execution path beyond paper. I did not tune any strategy to produce a survivor, and I did not weaken a gate, a test or a threshold to make a result look better.

The consolidated list of everything that needs you is in `USER_ACTIONS.md`.
