# Fiboki V2 — Completion Report

**Programme:** Phases A–L, executed continuously on 19–20 September 2026.
**Verified at completion:** 3,315 tests passing, 2 skipped, including hand-calculated golden financial tests. `ruff check src tests` clean, `mypy` down to a single error in an excluded directory. 69,359 lines of source across 23 packages, 40,293 lines of tests.
**Verified on the target machine:** the tree was synced to the MacBook and the suite was run there under a fresh Python 3.11 environment — it passes.
**Repository:** a self-contained git repository with eight checkpoint commits, now present on the Mac at `Fiboki/v2` with full history.

Status labels are used strictly throughout: **IMPLEMENTED** means the code exists and its tests pass. **VERIFIED** means I ran it and checked the result myself. **PARTIALLY VERIFIED** means tested in part, with the untested part named. **UNWIRED** means implemented and tested but nothing calls it in a running system. **BLOCKED** means waiting on an external dependency. **REQUIRES FORWARD DATA** means it cannot be verified until the platform has been running for a period.

---

## 1. The headline

Fiboki V2 is an honest research platform that, after two campaigns and 4,026 trials, says: **no strategy qualifies.**

Campaign K1 ran 326 trials on one instrument. Campaign K2 ran 542 cells across 13 instruments and 539,059 bars of H4 data, with a true trial count of 3,700 fixed before any compute was spent and propagated into the deflation threshold. Both produced zero survivors. The holdout has never been consumed on any of the 16 datasets.

K2 matters more than K1 because of depth, not verdict. With 16 instruments the 400-trade minimum became reachable for 140 of 542 cells, so **the purged cross-validation, robustness and deflation rungs executed against real market data for the first time**. The deepest cell — a Donchian breakout mutant on GBPJPY — reached a per-bar Sharpe of 0.01026 against a noise threshold of 0.02183. That threshold is the Sharpe a search of 3,700 trials is expected to produce from pure chance. The best thing found across two campaigns was **below what the search would have manufactured from nothing**.

The regime breakdown on that same cell is the sharpest single result in the programme. It is a channel-breakout system, so it should earn its money when markets trend. Segmented by regime, its trending bucket produced +68 over 108 trades at a t-statistic of 0.05, while 120% of its net profit sat in a single neutral-direction bucket. The method worked exactly as intended and reported: this was the sample, not a mechanism.

Three results from the build are worth more than any strategy ranking.

**The V1-versus-V2 procedure contrast, measured on real market data.** On XAUUSD H4, the V1 procedure — evaluating fixed default parameters on the test window — reports a positive out-of-sample rate of 0.0394 and a 50% window hit rate for the Ichimoku seed. The V2 procedure, which selects parameters on the training window and transfers *that* selection to the test window, loses in every single fold: walk-forward efficiency −150.96%, hit rate 0 of 4. Same strategy, same data, same engine. The difference is entirely the honesty of the procedure.

**Implementing the strategies correctly made two of them worse.** Once the engine honoured the exit vocabulary the documents actually declared, the Ichimoku seed went from +$720 to −$52 and its trade count fell from 71 to 49, because `avoid_rollover_hour` — declared `true` by every seed document and silently ignored by the engine — removes one H4 entry bar in six. The Donchian seed went from 6 trades in thirteen years to 583, because its ATR chandelier trail had never executed. Neither change was a tuning decision; both were the engine stopping lying about what the strategy said.

**The deflation bar rises as the search grows, automatically.** Between K1 and K2 the same cell's required threshold rose about 24% purely because more things were tried. That is the mechanism V1 lacked entirely, and it is why V1's 23,040-combination search could rank noise with confidence.

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

**There are none, across two campaigns and 4,026 trials, and I will not manufacture one.**

The deepest cell reached was a Donchian breakout mutant on GBPJPY, which cleared the sanity, screen, walk-forward, purged-CV and robustness rungs and died at deflation with a per-bar Sharpe of 0.01026 against a required 0.02183. Its regime breakdown shows the edge was not where the mechanism says it should be. That is a rejection with an explanation, which is the most useful kind.

Across K2, rejection reasons were: insufficient trades 402 cells (74.2%), non-positive expectancy 115 (21.2%), walk-forward efficiency 17, no profitable parameterisation 4, out-of-sample hit rate 2, parameter plateau 1, deflated Sharpe 1.

One premise of mine needs qualifying, and the campaign agent was right to push back on it. I expected 16 instruments to dissolve the 400-trade constraint. It did not: the minimum was reachable for 140 of 542 cells, but insufficient trades still accounted for 74.2% of rejections against roughly 70% in K1. The Ichimoku and MACD families median 68 and 55 trades over twenty years and cannot reach 400 on any instrument here at any declared parameter setting. That is a fact about those strategies' trade frequency meeting a fixed gate, and it means the gate and the strategy library need to be designed together rather than independently.

An instrument pattern worth recording as a data finding rather than a research one: GBPJPY and EURJPY carry the highest median trade counts and cleared rung 0 most often, and DE40 has the fewest bars but the highest clear rate. The search is finding instruments whose bar statistics happen to fit a fixed trade minimum and a fixed ATR stop. Three ingested instruments — AUDJPY, UK100 and XAGUSD — are in no seed's declared universe and were never testable, which is a gap in the strategy library rather than in the data.

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

**Execution:** paper broker VERIFIED at byte-level parity with the backtester. OANDA and IG adapters IMPLEMENTED against documented REST shapes and fixture-tested only; first contact with a real endpoint will find discrepancies. Both now drive the shared `PositionBook` through `VenuePositionManager`, VERIFIED at byte-level parity against the backtester over in-process venues that speak the adapters' real request payloads — so a demo deployment trails, scales out and time-stops. That is still fixture evidence, not endpoint evidence. Five independent live controls VERIFIED, every one-, two- and three-way subset asserted still blocked.

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

**Closed since the first report.** The IG and OANDA adapters now drive the same `PositionBook` as the backtester and paper adapter, with byte-identical ledgers across all five execution paths under the full exit vocabulary — this was the largest item before demo enablement. The four dead `RiskContext` inputs now carry real data, with the day boundary derived from the clock rather than from a job having run (V1's daily reset lived inside a 21:00 summary block, so a worker down at 21:00 meant the daily stop could never fire again). `MarketStateEngine.replay()` takes 26,837 bars from roughly 3.55 hours to 2.06 seconds, bit-identically. `ExitReason.BREAKEVEN` exists with an explicit open-set persistence contract. The structural fingerprint now distinguishes session and event restrictions. `ruff` is at zero and `mypy` at one.

**Still open.**

`MarketStateEngine.ingest_bar` remains O(history) per call, and when `max_history_bars` binds, an expanding percentile silently becomes a rolling one. The exact incremental percentile primitive is built and proven bit-identical, but it has no production caller: making the whole feature path streaming requires a shared streaming kernel in `indicators/` and a re-stamp of every stored feature and regime value. That is a phase of work, not a patch, and the honest position is that the batch replay path is what makes long histories practical today.

All five seed strategies' structure hashes moved when the fingerprint learned to see session restrictions. **Prior novelty verdicts and any persisted `structure_hash` are stale and must be recomputed.** Both the old and new hashes are pinned in a test so a further unintended move fails loudly.

A position reconstructed from a venue after a restart cannot tell a breakeven stop from a trailing stop, cannot recover `bars_held` (so a time stop runs longer than the document says), and cannot distinguish legs already banked from legs never reached — the ladder is re-planned over the remaining size, producing more scale-outs than declared at the declared prices. Each is reported as a divergence rather than guessed.

The position manager's book is modelled, not dealt: entry price comes from the fill simulator rather than the venue, so every level derived from entry inherits that difference. It is recorded as `entry_divergence` on every amend and deliberately not corrected, because correcting it would make demo run a different strategy from the backtest.

`DataStore` returns microsecond-resolution timestamps while `Trade` uses nanoseconds, so regime analysis of stored bars fails on the mismatch until one side is converted. Worked around locally during K2; the proper fix belongs in the data layer.

The `misaligned_bar_start` integrity warning fires on 100% of H4 bars and 0% of H1 bars, because the alignment anchor is midnight UTC while the corrected HistData H4 grid is anchored at 21:00. It is a warning only and nothing acts on it, but a warning that always fires is a warning nobody reads.

The IG and OANDA adapters remain fixture-tested rather than endpoint-tested. First contact with a real demo endpoint will find discrepancies.

**A correction to my own earlier audit.** The audit of 19 September quoted minimum track record lengths of 2.8 years to separate an observed Sharpe of 1.0 from zero and 11.2 years from 0.5. Computed against the implemented function at Gaussian moments and 95% confidence, those are 5.1 and 17.2 years. The implemented values are more demanding, so the direction is safe — but the audit figures were wrong and the implementation is right.

## 12. Recommended next research

Extend the campaign across instruments and timeframes once the full data store is migrated. The 400-trade minimum is reachable across 60 instruments and mostly unreachable on one, which is why K1's rejections were dominated by insufficient evidence rather than by disproof.

Run the Fibonacci falsification experiment — compare 0.618 against random levels between 0.5 and 0.7 on the same swings. The hypothesis document specifies it; nobody has run it. If it fails, cut the family.

Start the price recorder now and revisit every backtest in six months against prices from the venue that will actually fill you. This is the only route to closing the parity gap honestly.

Resist adding strategies. Every one raises the deflation bar for all the others, which is why V2 ships five seeds where V1 had 64.

---

## 13. What I did not do

I did not merge V2 into your repository, deploy anything, open any account, spend any money, touch the V1 production systems, or enable any execution path beyond paper. I did not tune any strategy to produce a survivor, and I did not weaken a gate, a test or a threshold to make a result look better.

The consolidated list of everything that needs you is in `USER_ACTIONS.md`.
