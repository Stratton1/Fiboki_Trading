# Fiboki — Full System Audit and v2.0 Overhaul Plan

**Date:** 19 September 2026
**Audited commit:** `ccc3af2` on `main`, plus six uncommitted working-tree changes
**Method:** fresh-eyes audit. All pre-existing agent instruction files (`CLAUDE.md`, `GEMINI.md`, `RULES.md`, `.claude/skills/`) and all prior status documents were treated as *evidence of intent only*, never as authority. Five independent auditors worked in parallel on the quant core, the execution/risk/security layer, the frontend, engineering discipline, and external research. Every material number below was either measured in this session or carries a file:line or URL citation.
**Supersedes:** `CURRENT_STATUS.md` (2026-07-09), `LIVE_READINESS_REPORT.md`, `forensic-ig-realism-audit.md`, `GROK_CURSOR_ONBOARDING_AUDIT.md` and the 2,400-line `roadmap.md` as the authoritative statement of current state.

---

## 1. Executive summary

Fiboki is a genuinely substantial piece of engineering — roughly 32,000 lines of backend Python and 20,000 lines of TypeScript, with a declarative strategy factory, a five-rung robustness ladder, an operator console with real honesty cues, and a working IG demo integration. It is not a toy. Several of its components are better than what most solo-built trading platforms ever reach.

It is also, today, **not able to tell you whether any of its strategies make money**, and it must not be pointed at real capital. That is the finding, and it is not a close call.

Three problems compound, all in the same direction — towards flattering results:

**The measurement layer overstates performance.** Spread is charged on entry but never on exit, so roughly half of every round-trip cost is missing. Commission is configured but never applied. Overnight financing is not modelled at all, on a platform whose positions are held for days. Sharpe is annualised by `√252` regardless of how often a strategy actually trades, which on the H4 systems that dominate the results overstates it by about 2.5× and on small-sample rows by up to 9×. Drawdown is realised-only, so it cannot see how far an open position went underwater.

**The selection layer has no defence against noise.** The research grid is 23,040 strategy × instrument × timeframe combinations. There is no multiple-testing correction anywhere in the codebase — I grepped for every standard term and found nothing. At a naive 5% threshold you expect roughly 1,150 combinations to look significant with zero real skill. Worse, the "held-out" out-of-sample test runs on the same data window that was used to decide whether the combination was worth testing at all, so it cannot be evidence about anything.

**The safety layer is not what the documentation believes it is.** The portfolio risk engine — the thing the project's own non-negotiables call mandatory — has *zero call sites in production code*. It is computed for display and never enforced before an order. A second broker integration (Tradovate) can be pointed at the live API by a single environment variable, because the live-gate check is a string-equality comparison that a trailing slash defeats. And `FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` is committed, as a literal value, in `render.yaml`.

Set against that, four things are genuinely good and should survive into v2: the strategy factory (declarative, content-hashed, 43 specs with zero duplicate rule signatures), the execution adapter abstraction and the signal→attempts audit schema, the hardcoded IG demo base URL that makes real-money execution a compile-time impossibility on that path, and the operator-honesty instinct visible throughout the frontend.

**The verdict on direction: refactor, do not rebuild.** No mature open-source engine has an IG adapter, and only one (LEAN, in C#) ships any mainstream FX/CFD broker integration at all. Rebuilding on NautilusTrader would cost 4–7 months and land you mid-way through that project's breaking v1→v2 transition. The disciplined refactor is 6–10 weeks of part-time work and fixes the things that actually matter.

**The verdict on broker: leave IG.** Not because the integration is bad — it is better than the open-source alternatives — but because IG's 10,000-datapoints-per-week historical allowance is roughly two orders of magnitude too small for this platform's research matrix, and its demo environment runs on undocumented limits that change without notice, which is precisely the symptom you have been fighting. The recommendation is OANDA Europe on a spread-betting sub-account, with Interactive Brokers UK as the fallback at larger capital.

---

## 2. What was actually verified, and what was not

This matters, because the single most damaging pattern in Fiboki's history is documents that assert things nobody re-checked. Four separate prior documents claim the test suite passes; the most recent one says it cannot even complete.

**Verified by running it in this session:** backtest determinism (byte-identical trade ledgers across repeated runs), per-backtest wall time, the full offline test suite, the frontend build/lint/typecheck, `ruff`, the Gen-1 results analysis, dependency resolution, and the local database contents.

**Verified by source inspection with file:line evidence:** every finding in sections 5–8 below.

**Verified by primary-source research with URLs:** the broker comparison and the statistical methodology.

**Not verified, and stated as such:** production behaviour on Railway. The local database has zero rows in `execution_audit`, `execution_attempts`, `broker_trades`, `paper_bots`, `paper_trades` and `trades`. Every piece of real execution history lives in production Postgres, which is not reachable from this session. That absence is itself a finding — there is no offline-inspectable record of what the platform has actually done — but it means nothing in this report should be read as a claim about what happened in production. Which deployment config is authoritative (Railway vs Render) also could not be determined from the repository, and several P0 severities depend on that answer.

---

## 3. Current status

The restored working copy is at `Documents/Claude/Projects/Fiboki` on the MacBook, unpacked from the 12 September archive. `HEAD` is `ccc3af2`, identical to what is deployed on Vercel. The last production deploy was 25 June 2026; nothing has shipped in the twelve weeks since.

Six files are modified and uncommitted, and have never been pushed. They include the numpy-scalar coercion fix in `research/pipeline.py` that unblocked the Phase-1 backfill on 4 July, its regression test, and a 9 July rewrite of `CURRENT_STATUS.md`. **The Phase-1 fix has only ever existed on that laptop.** It should be committed before anything else happens.

Scale, measured: 31,794 lines of backend Python across 16 packages; 19,953 lines of frontend TypeScript across 80 files; 103 backend test files containing 976 test functions; 10 Playwright specs; 52 markdown documents in `docs/`; 64 registered strategies; 67 defined instruments of which 60 have canonical data; 7.2 GB of HistData parquet.

What the research has actually completed: the Gen-1 sweep (490 backtests, 35 families × 7 FX majors × H1/H4, 2023–2024) and 25 rows of the Phase-1 ladder, all from a single strategy family. **The 23,040-combination grid has never been run to completion** — 25 done, 23,015 outstanding. The local lifecycle ledger holds 83 events: 60 rejected, 8 validated, 12 backtested, 3 generated, and **zero `promoted_to_paper`**. No candidate has ever been promoted to a paper bot in this database.

`backend/.env` does not exist, so `FIBOKEI_PROD_DATABASE_URL` was never set and the survivor-publish path to production has never run. The production Candidates page is empty by construction.

---

## 4. Confirming the results to date

You asked me to confirm the results and find the best-performing bots. Here is the honest answer, computed directly from `backend/results/phase7/gen1_research_results.csv` in this session.

**Of 490 backtested combinations, 371 lose money — 76%.** Only 119 are profitable at all. That is before the cost corrections in section 5, which remove roughly half the round-trip spread from every one of them.

**The published ranking does not enforce the platform's own 80-trade rule.** Rank 1 (`hyb_stoch_trend`, EURUSD H4) has 74 trades and is flagged `qualified=0`. Rank 5 has **6 trades** and a reported Sharpe of 8.08. Rank 12 has **2 trades**. Seven of the top twenty fail the trade-count rule but are ranked among those that pass. The promotion gates in `research/promotion.py:52` and `risk/promotion.py:34` do correctly hard-require 80 trades, so nothing unqualified could have reached paper — but the operator-facing leaderboard, which is what a human reads and trusts, does not.

Applying a sanity gate of at least 80 trades, profitable, profit factor above 1.2 and max drawdown under 15%, **14 of 490 combinations survive**:

| Family | Instrument | TF | Trades | PF | Max DD | Net | Sharpe reported | Sharpe honest |
|---|---|---|---:|---:|---:|---:|---:|---:|
| hyb_macd_ema_trend | USDJPY | H4 | 83 | 1.68 | 0.9% | £360 | 3.48 | **1.41** |
| hyb_macd_rsi | USDJPY | H4 | 98 | 1.53 | 0.9% | £322 | 2.79 | **1.23** |
| hyb_donchian_adx | USDCHF | H4 | 83 | 1.43 | 7.7% | £1,932 | 2.62 | **1.06** |
| trad_cci_meanrev | EURUSD | H4 | 243 | 1.24 | 12.7% | £3,489 | 1.87 | **1.30** |
| trad_rsi_meanrev | AUDUSD | H1 | 304 | 1.22 | 12.6% | £4,160 | 1.63 | **1.27** |
| trad_sma_trend | USDJPY | H4 | 279 | 1.30 | 1.2% | £454 | 1.48 | **1.10** |
| trad_macd_cross | USDJPY | H4 | 217 | 1.26 | 1.1% | £315 | 1.42 | **0.93** |
| trad_macd_zero | USDJPY | H4 | 214 | 1.23 | 2.0% | £338 | 1.28 | **0.83** |
| trad_cci_meanrev | USDCAD | H4 | 122 | 1.23 | 12.3% | £1,482 | 1.72 | **0.84** |
| hyb_donchian_adx | NZDUSD | H4 | 82 | 1.32 | 7.7% | £1,352 | 1.98 | **0.80** |
| hyb_macd_ema_trend | EURUSD | H4 | 95 | 1.27 | 6.6% | £1,396 | 1.75 | **0.76** |
| hyb_macd_rsi | EURUSD | H4 | 108 | 1.26 | 6.9% | £1,420 | 1.63 | **0.75** |
| hyb_macd_ema_trend | NZDUSD | H4 | 85 | 1.21 | 8.4% | £950 | 1.43 | **0.59** |
| trad_sr_breakout | USDJPY | H4 | 90 | 1.21 | 1.0% | £166 | 1.32 | **0.56** |

*"Sharpe honest" re-annualises the per-trade returns by √(trades per year) over the run's actual two-year window, instead of the `√252` the code applies unconditionally.*

Three observations, and one correction.

**H4 dominates.** Thirteen of the fourteen survivors are H4. The H1 families over-trade and carry far larger drawdowns. This is consistent with the original Gen-1 write-up and is probably the most robust single conclusion in the whole dataset.

**USDJPY is over-represented and this is a warning, not a result.** Six of fourteen survivors are USDJPY, and they carry implausibly low drawdowns — 0.9%, 1.0%, 1.1%, 1.2%, 2.0%. 2023–24 USDJPY was one of the strongest sustained directional trends in recent FX history. These are almost certainly a regime artefact rather than an edge, and the original write-up flagged exactly this risk. Treat any USDJPY H4 trend result as suspect until it survives a different regime.

**The correction.** One of my auditors reported that honest re-annualisation collapses every stored Sharpe to between 0.23 and 0.45. That number is wrong for this dataset: it was derived using a 25.6-year data span, whereas the Gen-1 run covers two years. Recomputed against the actual window, the overstatement is about 2.5× for typical H4 rows (reported 3.48 → honest 1.41) and up to roughly 9× for the tiny-sample rows. The defect is real and serious; the magnitude that auditor quoted for this file is not. I am flagging it because the failure mode being audited here — a confident number nobody re-checked — is the same one that produced the contradictory status documents, and an audit is not exempt from its own standard.

**What none of this survives.** Every figure above is in-sample, on two years of FX-only data, on a single parameter set per family, with no out-of-sample test, no walk-forward, no multiple-testing correction, and with roughly half the transaction costs missing. A gross Sharpe of 1.4 on 83 trades over two years, corrected for costs and deflated for 490 trials, is not a demonstrated edge. **There is currently no strategy in Fiboki that has been shown to make money.**

---

## 5. The quant core

*Determinism passes.* Repeated backtests produced byte-identical trade ledgers (SHA-256 over entry time, entry price, exit price, PnL and exit reason) for all six strategy/instrument combinations tested. There is no ambient RNG in the execution path and Monte Carlo is seeded. This is the hardest property to retrofit and Fiboki has it — within a single environment. Across environments it does not hold, because dependencies are unpinned (section 8).

*Measured cost:* 3.6–10.0 seconds per backtest. Extrapolated, the 23,040-combination sweep is roughly 51 core-hours before the robustness ladder, which multiplies it by a further 10–20×. `ruff check src` passes clean.

**P0 — Spread is charged on entry only; the exit is free.** `backtester/engine.py:115-117` calls `_apply_costs` once, on entry. `_get_exit_price` at `:199-210` returns the raw stop, the raw take-profit, or the raw close with no spread and no slippage. Measured on EURUSD H4: `bot01_sanyaku` net profit falls from £1,394.79 to £758.35 once the exit half-spread is charged — a 46% reduction, turning PF 1.045 into 1.024. Separately, `commission_per_trade` (`config.py:18`) is declared and never read anywhere in the engine, and there is no overnight financing model at all, on a platform whose `max_bars_in_trade=50` on H4 means positions are held about eight days.

**P0 — Sharpe and Sortino are annualised by `√252` regardless of trade frequency.** `metrics.py:229` and `:256` apply `math.sqrt(252)` to *per-trade* returns. The correct factor is `√(trades per year)`. This also disarms the guard-rail: the `_SHARPE_WARN = 4.0` alarm at `metrics.py:350` never fires, because the inflation is baked in just below it.

**P0 — The composite score is sign-blind and rewards smooth losers.** `research/scorer.py:63-81` scores stability as the R² of a linear fit with no check on the slope's sign — a straight-line march to ruin scores 1.0, full marks. `_score_drawdown` at `:49-54` rewards small drawdowns with no reference to returns. `_score_profit_factor` at `:35-36` maps `NaN` to the cap, so a profit factor of NaN also scores full marks. Measured consequence: on EURUSD H4, `bot02_kijun_pullback` (net **−£909**, PF 0.774) scores **0.3007**, beating `bot01_sanyaku` (net **+£1,395**, PF 1.045) at 0.2586. In the shipped Gen-1 file, the best-scoring *losing* combination reaches 0.3487, above the `ladder_min_composite = 0.30` pre-screen — so losing combinations are being admitted to the expensive robustness ladder.

**P0 — Selection and validation share the same data window, and there is no multiple-testing correction.** In `research/pipeline.py`, the backtest at `:174` and the score at `:181` run on the full DataFrame, the keep/discard decision at `:198` uses that score, and then walk-forward (`:206`), the "held-out" OOS split (`:215`), sensitivity (`:233`) and cost stress (`:240`) all re-test **the same full DataFrame**. The 30% OOS segment was already inside the data that drove selection. A grep for `bonferroni|deflated|white.*reality|fdr|benjamini|data snoop` across the entire package returns zero hits.

**P1 — Stops and take-profits fill at their exact level even when price gapped through.** `engine.py:203-207` returns `position.stop_loss` unconditionally. Measured: a long at 1.1000 with a stop at 1.0950, on a bar that gaps to open 1.0800, fills at 1.0950 — a phantom 150-pip gain on one trade. There are 1,125 weekly-open bars in the EURUSD H1 sample; every stop sitting inside a weekend gap is credited at its exact level.

**P1 — Drawdown is realised-only.** `engine.py:91` updates equity only when a trade closes, so the equity curve is a step function and `_compute_drawdown` cannot see open-position excursion. Maximum adverse excursion *is* tracked at `position.py:63` and never used. Measured on one sample: reported max DD 9.65% against a worst single-trade open excursion of 2.18% of capital that never reached the curve.

**P1 — `SwingDetector` is a centred window and breaks backtest/paper parity.** `indicators/swing.py:38-51` computes a swing at bar *i* using bars *i+1 … i+lookback*. Confirmed by mutation testing: corrupting future bars changed `last_swing_high` on 3 of 15 prior bars. Fifteen of twenty-one hand-coded strategies consume these columns across 108 references. The backtest impact appears small (prefix-consistency tests matched exactly on three strategies), but the *parity* impact is serious: because the loop runs only to `n - lookback`, the last five bars can never have a swing detected, so the paper bot evaluating at `len(prepared) - 1` (`paper/bot.py:113`) sees a stale forward-filled value where the backtest saw a fresh one. **The two engines see different indicator values for the same bar.**

**P1 — `chikou_span` is a genuine future value sitting in every strategy's DataFrame.** `indicators/ichimoku.py:67` sets `df["chikou_span"] = close.shift(-26)`. To the authors' credit, no strategy reads it — `bot01:118`, `bot04:61` and `bot13:141` all correctly compare `close[idx]` against `close[idx-26]`. But the column is exposed through the API and the CLI and handed to every strategy, and nothing prevents the next one from reading it. Relatedly, Senkou A and B are displaced using `chikou_shift` rather than a dedicated `senkou_shift` (`ichimoku.py:57-63`) — numerically identical at defaults, but a sensitivity sweep over `chikou_shift` silently moves the cloud too.

**P1 — Walk-forward fits nothing.** `research/walk_forward.py:88-101` runs the *same* fixed-parameter strategy on train and test windows. No parameter is estimated on train and carried to test. It is a useful regime-stability statistic mislabelled as protection against overfitting.

**P1 — Monte Carlo freezes position sizes.** `monte_carlo.py:104-110` resamples realised PnL, so a simulation that opens with a run of large losses does not shrink subsequent positions the way live compounding would. This understates ruin probability, and `ruin_probability <= 0.05` is a hard ladder gate. The moving-block bootstrap itself (block size 5) is a good choice and preserves short-run structure.

**P1 — Cross-currency conversion is a no-op except for JPY.** `sizing.py:84-112` returns `1.0/price` for JPY pairs and `1.0` for everything else, including an explicit CHF branch commented "Approximate CHF ≈ USD for simplicity". For a GBP account, all USD-quoted instruments — including all of XAUUSD — report PnL in the wrong currency, with an error that ranged 1.20–1.43 over the sample. That is a 20%+ swing correlated with the very macro regimes the strategies trade.

**P1 — The 80-trade rule does not gate ranking.** `research/matrix.py:131-134` ranks everything; `research/filter.py` applies the filter afterwards as a label. `_score_sample` makes a shortfall nearly free — 74 trades costs 0.0075 of composite.

**P2 — One strategy can never trade.** `factory_trad_obv_confirm_v1` produces zero trades on FX because canonical FX parquet has volume identically zero, so OBV is zero everywhere. It occupies a slot in the 64 and consumes 360 grid cells that can only ever return nothing. Its sibling `factory_trad_vwap_bias_v1` *does* trade, but only because `volume.py:46` silently falls back to a typical-price rolling mean when volume is absent — it is not a VWAP strategy on FX, it is an undisclosed moving-average strategy wearing a VWAP label.

**P2 — Fibonacci anchoring is direction-blind.** `indicators/fibonacci.py:64` skips whenever the last swing high is below the last swing low, and always measures downward from the high, so uptrend and downtrend retracements are anchored identically. `FibonacciExtension.compute` is a no-op stub; extensions are computed ad-hoc inside `bot11`, contradicting the centralised-indicators rule.

**Also noted:** the engine's warmup is always 98 bars because `getattr(self.strategy, "warmup_period", 78)` reads an attribute the `Strategy` base class never defines (`engine.py:50-53`); `max_open_trades` is unused because the engine structurally holds one position, so portfolio effects are absent from backtests entirely; and `metrics.py:313-341` builds a synthetic hourly date index for monthly/yearly returns regardless of the real timeframe, mislabelling every period bucket on H4 data.

---

## 6. Execution, risk and security

**The one control that is genuinely strong:** `execution/ig_client.py:72` assigns `IG_DEMO_BASE` in the constructor and never reassigns it. Even an execution account configured with `environment="live"` and `live_allowed=True` routes to `demo-api.ig.com`. A compile-time impossibility at the bottom of the stack is worth more than any number of environment flags, and it should be kept permanently.

**P0 — The Tradovate client has a single-environment-variable bypass to the live API.** `execution/tradovate_client.py:160-169` lets `FIBOKEI_TRADOVATE_BASE_URL` override the base URL. The live gate at `:188-196` is an exact string comparison against `TRADOVATE_LIVE_BASE`. A trailing slash, or any casing variance, produces a URL that is not `==` the constant, so the gate never fires, `_env` stays `"demo"`, and orders flow to the live Tradovate API. This is one misconfigured variable reaching real money. The module also self-documents its endpoint paths as `TODO_VERIFY` placeholders.

**P0 — `FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` is committed in `render.yaml:35-36`** as a literal value rather than `sync: false`. It is the top of the live ladder for three of the four broker paths.

**P0 — The portfolio risk engine is never called.** `RiskEngine.check_trade_allowed`, `check_drawdown_limits` and `check_fleet_trade_allowed` (`risk/engine.py:54`, `:93`, `:122`) have **zero call sites** outside their own definitions. `PaperWorker.__init__` constructs one at `worker.py:133` and never uses it. `PaperBot.on_candle_close` goes straight from signal to sizing to dispatch (`paper/bot.py:186-238`) with no risk consultation. Daily and weekly stops, max-drawdown halts, concentration limits and fleet caps are rendered on `/system/limits` and enforced nowhere. The project's stated non-negotiable that "portfolio-aware risk controls are mandatory" is, in the running system, false.

**P0 — Two workers can run concurrently and place duplicate orders.** `render.yaml` defines both a web service and a worker service; the API starts an in-process worker thread unless `FIBOKEI_WORKER_EXTERNAL=true` (`api/app.py:334-337`), and that variable is absent from the web service's env. There is no lease, no advisory lock, no ownership column and no unique constraint. Both `worker_id`s default to the literal `"railway-worker"` (`worker.py:928`), so the two would overwrite each other's heartbeat row and the duplication would be **invisible on the System page**.

**P0 — No order idempotency, and open positions are never recovered.** Broker deal IDs live only in memory (`paper/bot.py:69`) and are never persisted; `Position.to_dict()` has no field for them. `recover()` (`worker.py:181-205`) restores state, bar count and last-evaluated-bar, but **not the position object** — so a bot persisted as `position_open` comes back with `self.position = None`, cannot re-enter (entry requires `MONITORING`) and cannot exit (exit requires a position). The broker position is orphaned permanently, protected only by whatever stop was submitted at open. And nothing is written before dispatch, so a crash between the IG POST and the audit write leaves a real position with zero record in Fiboki; on restart the bot will open a second one. No client `dealReference` is sent, so IG cannot deduplicate either. Compounding it, `_fetch_confirmation_with_retry` returns `PENDING_CONFIRMATION` on total failure (`ig_adapter.py:592`) and the router treats anything that is not ACCEPTED/FILLED as a rejection (`router.py:401`) — so a position IG *did* open, but failed to confirm, is recorded as rejected and the deal ID is discarded.

**P0 — Reconciliation compares the wrong key and never runs.** `reconcile_account` is reachable only through a GET endpoint a human must click. It is never called on startup or on a timer. And `api/routes/execution.py:629` builds Fiboki positions keyed on `position_json["trade_id"]` — the internal `uuid4` — while broker positions are keyed on IG's `dealId`. These key spaces never intersect, so reconciliation reports every position missing on both sides and **cannot produce a clean result even on a perfectly healthy system**.

**P1 — Any authenticated user can enable live execution via HTTP.** `UserModel.role` exists and is referenced only in the `/auth/me` response. There is no `require_admin` dependency anywhere. Every logged-in session can create execution accounts with `environment="live"` and `live_allowed=True`, PATCH `live_allowed`, deactivate the kill switch, delete all bots and reset the account.

**P1 — The kill switch stops new orders and abandons open positions.** `dispatch_open` checks it; nothing flattens existing positions when it is thrown. Its fail-closed behaviour on exception (`router.py:147-152`, `worker.py:164-166`) is correct and commendable — the semantics just need deciding and naming.

**P1 — CSRF is possible on no-body POST endpoints.** The session cookie is `SameSite=None` (necessary for the Vercel↔Railway split), and a plain HTML form POST with no body and no custom headers is a CORS-simple request that skips preflight. Affected: kill-switch deactivate, bot stop/pause/resume/restart, restart-all, reset-all, account reset. There is no CSRF token and no Origin validation.

**P1 — No rate limiting anywhere, including login.** Unlimited password guesses against two known usernames, with no lockout and no failed-attempt alerting. Seeded passwords default to the literal `changeme` (`api/seed.py:14-15`) and startup validation does not check that the password variables are set.

**P1 — Worker restart silently blinds every bot for ~100 candles.** `recover()` restores `bars_seen` but leaves the in-memory DataFrame empty, and because `_last_evaluated_bar` is also restored the warmup branch is skipped. `run_preparation` then throws on a one-row frame and is swallowed by a bare `except Exception: return None` (`paper/bot.py:108-111`). On H4 that is roughly two weeks of silence with no error surfaced and a heartbeat still reporting the bot as active.

### Paper is not the same engine as the backtest

The paper bot reimplements the entry/exit path rather than sharing the backtester's. Every divergence runs in the optimistic direction:

| Dimension | Backtester | Paper bot |
|---|---|---|
| Entry spread | Half-spread applied (`engine.py:115`) | **None** — fills at the proposed price (`bot.py:190`) |
| Min stop floor | `atr × 2.0` passed to sizing (`engine.py:128`) | **Not passed** — tight stops produce larger positions |
| Currency adjustment | Applied to every closed trade (`engine.py:87`) | **Never applied** — JPY pairs overstate PnL ~150× |
| Sizing base | Per-strategy equity, isolated | Shared fleet equity — one bot's loss shrinks every other bot's size |
| Sizing authority | One | **Three** — bot, router, and IG adapter each size independently |
| Bankruptcy guard | `if equity <= 0: break` | **None** — balance can go unboundedly negative |
| Unrealised PnL | Not modelled | Reads a key nothing ever writes; permanently zero |
| Data source | Canonical parquet | yfinance, while orders are placed at IG prices |

The triple-sizing row is the most corrosive for operator trust: `Position.position_size` (equity-based) drives the recorded PnL while `attempt.requested_size` (allocation-based) drives the actual broker order, and the IG adapter then re-sizes a third time from the live IG balance. The paper ledger and the broker disagree about position size for the *same signal*, by construction, so neither is a reliable account of what the strategy did.

### IG adapter

Genuinely good: it refuses to deal on unfetched market specs, validates stops against IG's own minimum distance, defaults `FIBOKEI_IG_REQUIRE_STOP` on so no naked orders go out, caps size by instrument class, and refreshes the session at five hours against IG's six-hour TTL with a lock against re-auth storms. These read as lessons learned from real incidents.

Gaps: no rate limiting or quota accounting at all, despite the `/prices` quota having already been exhausted in production — the mitigation was to switch the price feed to yfinance rather than to throttle. Epic coverage is 61 of 67 instruments (the six crypto pairs and DXY are silently untradeable). Market orders only, guaranteed stops hardcoded off, so gap risk is unbounded on overnight holds. And the runtime epic remapping free-text-searches IG's market list and takes the first tradeable hit, which can trade the wrong instrument.

---

## 7. Frontend and operator experience

Build passes in 15.6 seconds across 20 routes. `tsc --noEmit` fails with one error, confined to a test file. `npm run lint` fails with 17 errors and 33 warnings — including two `react-hooks/purity` violations where `Math.random()` and `Date.now()` are called during render in `TradingChart.tsx:87`, which can orphan a chart instance across re-renders. Because the build passes, these failures have been normalised and the signal is dead.

**The most serious UX defect is a provenance lie.** `/trades` — the page an operator opens to answer "what did my bots actually do?" — contains **no paper trades at all**. It renders backtest trades under a tab labelled "Paper / Backtest". This is verifiable in the backend: `TradeResponse.backtest_run_id` is a required non-nullable int (`api/routes/trades.py:33`) and `TradeModel` rows are only ever created inside `save_backtest_result` (`db/repository.py:207`); paper fills live in a separate `paper_trades` table. The dashboard compounds it by rendering the same endpoint under the heading **"Recent Execution"**, directly beneath "Fleet PnL (live)".

**Second: bot-creation copy hardcodes "paper" while the platform may be routing to IG demo.** A native `confirm()` at `research/candidates/page.tsx:48` literally promises "Paper trading only — no live execution" regardless of actual mode. Fourteen of nineteen dashboard pages never read execution mode at all. The global `ExecutionModeBanner` does render — but inside the scrolling container, so on the 1,815-line research page it scrolls out of view long before the operator reaches the button.

**Third: an API outage is visually indistinguishable from a healthy, idle fleet.** Of 77 `useSWR` call sites, only 10 destructure `error`. Page code overwhelmingly does `data?.field ?? 0`. If the backend is down, the dashboard renders a confident "£0.00 balance, 0/0 bots running, +£0.00 Fleet PnL" with hardcoded `Online` and `Connected` badges and a "Last updated just now" footer.

**Fourth: the kill switch on `/system` has no confirmation** — a bare icon button, one click, no loading state, no error handling — while the dashboard implements it properly with a modal that enumerates consequences. And the activate branch is gated behind `execMode === "ig_demo"`, so in paper mode the System page can deactivate but not activate.

**Fifth: the analytics realism caveats are hardcoded prose.** The qualitative claims check out — I confirmed the paper engine contains zero spread handling — but `analytics/page.tsx:172` prints "Estimated realistic return after cost adjustments: **190–230%**" as a frozen string literal directly beside a live computed value, implying both derive from the same data.

Structurally: 14 nav items serve 5 real jobs, with four competing surfaces answering "what should I promote?" and six distinct paths to approve a bot to paper, each with different copy, different gating and different confirm patterns. There is no responsive layout — the sidebar has no breakpoint, leaving a 71px content column at phone width. A 4.5 MB Plotly bundle (including mapbox-gl and turf) ships to every analytics route to draw line charts and a histogram. Steady-state polling on `/bots` is about 36 requests per minute from one idle tab.

**What deserves protecting.** The Assumptions panel on `backtests/[id]`, the zero-trade warning, the Diagnostics section that flags Sharpe above 5 and win rates above 90% with "verify not peeking at future data", the LEGACY £10K badge, the below-threshold promotion gate with an acknowledgement checkbox, the demo-ready criteria stated in full, and the validation funnel with per-rung rejection counts. This is a more honest surface than most commercial platforms. The task is to make the data as honest as the prose.

---

## 8. Engineering discipline

**The offline test suite hang has a one-line cause and a one-line fix.** `tests/conftest.py:53-65` builds a `TestClient(app)`, which runs the FastAPI lifespan, which calls `_start_worker_thread()` (`api/app.py:241`). That function only opts out when `FIBOKEI_WORKER_EXTERNAL` is set, and conftest never sets it. So every one of ~200 API tests spawns a real daemon thread polling yfinance over live network. Daemon threads are never joined, so they accumulate until the suite crawls to a stop. `--timeout-method=signal` cannot rescue it because SIGALRM lands on a main thread parked in a futex.

Measured, on the same tree and venv: default run **timed out at 240s with 22 tests incomplete**; with `FIBOKEI_WORKER_EXTERNAL=true`, the same 22 tests **passed in 27 seconds**. The full offline suite then completes:

```
1 failed, 1078 passed, 4 skipped, 30 warnings in 328.09s
```

Three months of working around this with a "122-test core subset" cost more than the fix would have.

**The single failure is a contradiction committed into the tree.** `tests/test_tp_hit_negative_pnl.py:129` asserts a take-profit hit can produce negative PnL. Commit `78a29f2` added `sanitize_take_profits()` precisely to abolish that, and `tests/test_tp_side_guard.py:16` asserts the opposite contract. The fix landed, the stale test was never retired, and because the suite was unrunnable nobody saw it go red.

**P0 — Dependencies are unpinned, so "deterministic backtests" is not a property this repo has.** `pyproject.toml:11` and `:13` specify `pandas>=2.0` and `numpy>=1.24`. Both my venv and an independently built one resolved to **pandas 3.0.6 and numpy 2.4.6** — a major version beyond what the author had in mind. There is no lockfile of any kind. Every CI run and every deploy resolves a different dependency set. Two of the six uncommitted working-tree changes are already hot-fixes for pandas-3 and numpy-scalar breakage, so the drift is actively biting.

**P0 — Nothing gates a deploy.** CI runs lint, test and frontend-build, then a `smoke` job that curls the *already-live* production API afterwards. Railway and Vercel deploy on git push independent of GitHub Actions; a red test job stops nothing. The health endpoint it polls returns a hardcoded `{"status": "ok", "version": "1.0.0"}` with no database or worker check — it returns 200 with the database down, and its version string disagrees with `pyproject.toml`.

**CI also runs the suite in exactly the configuration that hangs**, with no job timeout. The `network` and `slow` markers are declared in `pyproject.toml` and used **zero times** across 105 test files, so the `-m "not network"` command the status doc tells operators to use filters nothing. `mypy` is declared as a dev dependency and never run. Lint covers `src/` only. Playwright exists, with four scripts defined, and never runs in CI.

**Coverage is inverted against risk.** Total 69%. Risk 94%, indicators 93% — but `worker.py`, the process that actually trades, is at **22%**; `paper/bot.py` 33%; `paper/orchestrator.py` 0%; `ig_client.py` 58%; `cli.py` and `diag.py` 0%.

**Test quality is better than expected in one respect and weak in another.** An AST scan of all 976 test functions found exactly **one** with no assertion — a genuinely clean result. The look-ahead tests use the correct mutate-the-future technique. But there are **no golden-value indicator tests against an external reference** — `test_ichimoku.py` contains zero pinned constants for the flagship indicator and recomputes with the same formula on the same data. There are **no backtest regression pins**: `test_backtester_determinism.py` runs the same backtest twice in one process, which catches RNG issues and nothing else, so any change to trade count or PnL still passes. And there are **no paper-vs-backtest parity tests** at all, despite parity being an architectural rule.

**Deploy config drift.** Three definitions, one live. Root `render.yaml` (web + worker + database), `backend/render.yaml` (web + database, same service names, no worker) and the Railway configs. `RAILWAY_FORENSIC_REPORT.md`, browser-verified in June, states there is **no separate worker service** — it runs as a daemon thread inside the API — which directly contradicts `GROK_CURSOR_ONBOARDING_AUDIT.md` and `deployment.md`. Alembic is committed and copied into the image but **no start command ever runs `alembic upgrade head`**; schema comes from `create_all` plus a hand-rolled `_ensure_new_columns` shim that covers four tables and misses four others.

**Security debt beyond section 6:** `ecdsa` 0.19.2 carries PYSEC-2026-1325 **with no fix version available**, and it sits in the JWT signing path via `python-jose`. `npm audit --omit=dev` reports 8 vulnerabilities, 3 critical, including `next` at 16.1.6 inside the vulnerable range, plus `plotly.js` and `maplibre-gl`. `klinecharts` is pinned as `^10.0.0-beta1` — a caret range on a beta for the primary chart engine.

**Observability, concretely: if the worker dies at 3am, nothing tells you.** There is no Sentry in `worker.py` (zero references). There is no `worker_down` or `heartbeat_stale` event in `alerts/events.py` — the seven defined events are all trade or bot lifecycle, so the Telegram channel has nothing to send. Heartbeat freshness is computed *only when a human loads the System page*. The health endpoint cannot report the failure. And because the worker is a thread inside the API, the API stays green. You would find out when you next happened to look.

**Docs sprawl and contradiction.** 52 markdown files. `forensic-ig-realism-audit.md` and `operator-polish-report.md` both claim "all 661 backend tests pass"; `LIVE_READINESS_REPORT.md` issued a **"GO"** verdict on 615 passing tests; `CURRENT_STATUS.md` four months later says the suite cannot complete offline. My run reconciles them: the suite *can* pass, and the March runs were presumably online, where the rogue worker threads' network calls returned instead of blocking. **The "GO" rested on a suite whose green depended on live internet.**

---

## 9. Broker decision

**Leave IG.** Not because the adapter is poor — it is better-engineered than `trading-ig`, the main community library, whose maintainer describes it as maintained "in his spare time, with very little time for support". Leave because of two structural facts.

First, IG's historical allowance is **10,000 data points on a rolling seven-day basis** (corroborated by the `ig-trading-historical-data` package docs and the Excel Price Feed user guide; IG's own page says "a finite weekly limit of datapoints"). One instrument-year of H1 is about 6,200 candles. Your 60-instrument × H1+H4 matrix needs on the order of half a million candles for a single year. That is roughly 50 weeks of quota for one pass. This is not a caching problem; the API was designed for order routing with incidental chart top-ups.

Second, the demo rejections you have been fighting are expected behaviour, not a bug you can engineer around. The `trading-ig` FAQ states that live limits are published but "the limits for DEMO are lower, and have been known to change randomly and without notice". A paper venue whose rejection behaviour reflects neither live behaviour nor itself week-to-week actively corrupts the evaluation record you intend to make a go/no-go decision from.

**Primary recommendation: OANDA Europe Limited (FCA 542574), v20 REST API, spread-betting sub-account.** It is the only FCA-regulated venue in the comparison combining a free well-documented REST plus streaming API, an identical practice environment at `api-fxpractice.oanda.com` (a one-line switch), **no weekly quota** (2 new connections/sec, 100 requests/sec on established connections), bid/ask/mid-selectable OHLC candles at up to 5,000 per request with H1 and H4 native and history back to roughly 2005, UK spread-bet tax treatment, and guaranteed stop-loss orders as a first-class API order type. At 100 req/s × 5,000 candles the entire 60-instrument matrix pulls in minutes, free.

Three caveats you must resolve before committing. **One:** OANDA's own UK help page states the live pricing feed "could be different from the historical data" because of pricing segments and account types — so candles come from OANDA's engine but not necessarily your account's tier. Mitigate by logging your own executable bid/ask from the pricing stream in paper mode and diffing it against the candle endpoint for a month. **Two:** that the v20 REST API can place orders on a spread-betting-enabled sub-account is strongly implied but stated nowhere I could find — **verify on demo before committing**, because it decides whether you get tax-free treatment. **Three:** the Python ecosystem is stale (`oandapyV20`'s last release was August 2021, declaring Python 3.6–3.9). Write your own thin typed adapter over `httpx` instead — it is roughly a day's work for a FastAPI codebase and removes a dead dependency from the critical path.

You lose crypto entirely (FCA retail ban, not an OANDA choice) and single stocks on v20. FX majors and crosses, gold, silver, oil and indices are all covered.

**Fallback: Interactive Brokers (U.K.) Limited (FCA 208159)**, and it becomes the better choice above roughly £25k. Best-in-class Python (`ib_async`, 1.7k stars, actively maintained), genuine execution-quality data for spot FX because IDEALPRO history comes from the same interbank aggregation that fills you, and a paper account that can inherit live data subscriptions. Against it: historical pacing is hostile (60 requests per 10 minutes, H1/H4 pulls capped at roughly one-month windows, making a full matrix rebuild a multi-day batch job), no spread betting so everything is CGT-assessable, spot FX minimum order sizes of 20,000 units make a single position an unacceptable fraction of a £1–2k account, and the USD 2.00 minimum commission on forex CFDs means round-trip cost is about 15% of a 1% risk budget on a £2,000 account. It also needs a running IB Gateway process, which is real infrastructure work against a Railway-hosted backend.

**Honourable mention: Pepperstone via cTrader Open API** has the best parity data of anything here — raw bid and ask *tick* history direct from the broker's own server, which would let you reconstruct candles with actual quoted spreads and close one of Fiboki's documented approximations outright. It is rejected as primary only on integration ergonomics: Protobuf over TCP with no official Python SDK is a meaningfully heavier lift than JSON REST. Worth revisiting.

**Ruled out:** CMC (no retail API), Alpaca (wrong asset classes, not FCA), Trading 212 (API appears scoped to Invest/ISA), Saxo (120 req/min is 40× tighter than OANDA, and no spread betting found on UK accounts), Capital.com (a 40-instrument WebSocket cap against your 60-instrument universe, and no read-only API keys), Darwinex (MT4 bridge adds a failure domain), FXCM (UK entity status unverified).

**On tax, which may outrank everything technical.** Spread betting is CGT-exempt for UK retail under current HMRC treatment; CFD and spot FX gains are chargeable above the £3,000 annual allowance at 18% or 24%. The mirror image is that spread-bet losses cannot be offset against other capital gains, which matters if you expect early losses. This is genuinely consequential for the OANDA-vs-IBKR choice and is the kind of question where I would want an accountant's sign-off rather than mine.

**What to do with the IG adapter:** keep it, behind its flag, dormant. It proves your broker abstraction actually abstracts, and a second implementation is architecturally valuable. Just stop investing in it, and remove IG from every data-sourcing and parity-testing path.

---

## 10. Rebuild or refactor

**Refactor. Decisively.**

The case for rebuilding on an established engine collapses on one verifiable fact: **no mature open-source engine has an IG adapter, and only LEAN ships any mainstream FX/CFD broker integration at all.** NautilusTrader — the only engine genuinely better than a custom bar-loop on architecture, determinism and backtest/live parity — lists twenty stable integrations, of which exactly one (Interactive Brokers) is non-crypto. It is also mid-way through a breaking v1→v2 transition right now (v1.231.0 was the final 1.x in August; v2.0 is at release candidate). Porting 64 strategies and writing a broker adapter from scratch, into a framework undergoing a major migration, is 4–7 months of work to gain capabilities you do not currently need. LEAN means C#, which throws away your Python leverage.

The disciplined refactor is **6–10 weeks part-time** and addresses the things that actually determine whether Fiboki can be trusted. AI coding agents compress mechanical porting well, but they do not compress adapter debugging against a live venue, reconciliation edge cases, or re-earning trust in numbers — and those dominate a rebuild.

**Embed selectively rather than adopt wholesale:**

Use `backtesting.py` 0.6.6 as a *differential-testing oracle*. Implement five to eight representative strategies twice and assert that PnL, trade count and fill prices agree within tolerance on identical data. This is a weekend of work that either validates 32,000 lines or finds a career-defining bug.

Port NautilusTrader's *fill-model vocabulary*, not Nautilus itself: probabilistic fill on limit, probabilistic slippage of one adverse tick, a seeded RNG so reproducibility is a config field rather than an accident, and a bar-to-synthetic-L1 conversion that walks Open→High→Low→Close. Their rule that orders submitted from `on_bar` arrive only after all four OHLC points are processed is exactly the invariant a custom bar loop gets wrong.

Use vectorbt OSS for the 23,040-combination sweep only, then hand survivors to the event-driven engine for realistic execution. Note its fair-code licence permits this but prohibits selling a product that substantially *is* vectorbt.

Diarise a NautilusTrader re-evaluation for around September 2027, conditional on v2.0 reaching GA and an IG or cTrader adapter appearing.

---

## 11. What the evidence says about the strategies themselves

This is uncomfortable and you should have it plainly.

**Trend-following has real, replicated support.** Moskowitz, Ooi & Pedersen (2012, *JFE*) document time-series momentum across roughly 58 instruments; Hurst, Ooi & Pedersen extend it across about 110 years. Donchian breakout and MACD/EMA crossover are crude time-series momentum estimators, and *that* — not the indicator literature — is their theoretical basis. Framing them correctly changes what you should expect: modest Sharpe, long flat periods, fat left tail.

**Technical rules in FX, after data-snooping correction, largely do not survive.** Coakley, Marzano & Nankervis (2016, *IRFA*) tested **113,148 rules** on 22 currencies over 18 years with a Step-SPA correction. Traditional rules — moving averages, channel breakouts, filters — came back with p-values "very close to 1", meaning essentially zero robust profitability. Bollinger Band and RSI rules *did* remain significant. Critically, the study excludes bid-ask spreads, so real returns would be lower. Bajgrowicz & Scaillet (2012, *JFE*) concluded an investor "would never have been able to select ex ante the future best-performing rules". Marshall, Cahan & Cahan (2008) tested 7,846 rules on 5-minute data and found **none** profitable after correction.

**On Ichimoku specifically**, which is the platform's namesake: Deng et al. (2021, *IJFE*) tested Ichimoku strategies on four stock indices and four currency pairs, with default (9, 26, 52) parameters plus a sweep. Their conclusion — "several Ichimoku trading strategies may well prove to be profitable on stock index trading, but **none was found for currency trading**." That is before transaction costs. It is the best available evidence on Ichimoku in FX and it is negative.

**On Fibonacci**, no peer-reviewed study I could locate establishes that the specific ratios outperform arbitrary retracement levels. The burden of proof sits on the claim. The test is cheap and you should run it in-house before shipping any Fibonacci strategy to live capital: compare 0.618 against randomly drawn levels between 0.5 and 0.7 on the same swings. If it does not beat random, the ratio is decoration.

**The strategic consequence is counterintuitive but mathematically direct: having 64 strategies makes you worse off.** Every additional strategy inflates the trial count and raises the statistical bar every survivor must clear. Culling to 12–20 strategies with articulable economic rationale would *improve* your expected live performance even holding research quality constant.

---

## 12. The validation protocol v2

This is the part that decides whether Fiboki's numbers can ever be trusted, and it is where most of the effort should go.

**The problem, quantified at N = 23,040 trials:**

| Quantity | Value |
|---|---|
| Expected spurious survivors at α = 0.05 | **1,152** |
| Bonferroni critical t (one-sided) | 4.594 |
| Required per-trade Sharpe at 80 trades | **0.514** |
| Required per-trade Sharpe at 400 trades | 0.230 |
| Required per-trade Sharpe at 1,000 trades | 0.145 |

Read the last three rows together: the current 80-trade minimum combined with 23,040 trials demands a per-trade Sharpe that essentially no genuine H1/H4 FX system produces. **The gate as it stands is statistically vacuous.** Either the trade minimum rises substantially or the trial count falls substantially, and in practice you need both.

**Step 1 — Deflated Sharpe Ratio.** Compute the Probabilistic Sharpe Ratio against a threshold set to the expected maximum across trials:

```
SR₀ = √(V[SR_n]) · [ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ],  γ = 0.5772
DSR = PSR(SR₀)
```

`V[SR_n]` is the variance of Sharpe ratios across your trials — a number the pipeline already computes and currently discards. **Store it.** For calibration: at N = 23,040 with a cross-trial Sharpe dispersion of 0.5, **SR₀ ≈ 2.03 annualised**. Anything ranked first below that is indistinguishable from the best of 23,040 coin-flip sequences. Accept at DSR > 0.95. For the effective trial count, hierarchically cluster trial return series on correlation distance and use the number of clusters — do not use the paper's correlation adjustment, which collapses to implausibly small N at this scale.

**Step 2 — Probability of Backtest Overfitting via CSCV.** Partition the trial-returns matrix into S = 16 contiguous submatrices, giving 12,870 train/test combinations; for each, take the in-sample best and find its out-of-sample relative rank; PBO is the fraction where it lands below the OOS median. PBO ≥ 0.5 means your selection is worse than random. **Gate at PBO < 0.2 and record it on every research run.** This single number is the most honest summary of a 23,040-combination search you can produce, and it costs one matrix and a loop.

**Step 3 — Hansen SPA.** Test the null that *no* strategy beats the benchmark, correcting for the full search. `arch.bootstrap.SPA` with `bootstrap='stationary'`, `studentize=True` and a fixed seed; use the *consistent* p-value. Then `StepM` to identify which strategies survive under family-wise error control. Gate at p < 0.05 and membership in the StepM survivor set.

**Step 4 — Replace IID Monte Carlo with a stationary block bootstrap.** Automatic block length via `arch.bootstrap.optimal_block_length` (Politis-White, Patton correction); expect 20–60 bars on H1 data. Never resample bar returns independently — it destroys the serial dependence trend systems live on and inflates Sharpe.

**Step 5 — Purged and embargoed cross-validation.** Purge any training observation whose label window overlaps the test window (for trade labels that is the full trade duration, not the entry bar), and embargo afterwards, sized at the 95th percentile of trade duration rather than a flat percentage. Combinatorial purged CV with N = 6 groups and k = 2 gives 15 splits and 5 distinct backtest paths, which yields a *distribution* of OOS Sharpe — exactly what feeds the DSR's variance term.

**Step 6 — Walk-forward that actually fits.** Sweep parameters on train, select, evaluate that parameterisation on test. Run both anchored and rolling; disagreement between them is itself a finding. Require walk-forward efficiency ≥ 50% and profitability in ≥ 60% of OOS windows.

**Step 7 — Raise the trade minimum to 400**, preferring 1,000 where the timeframe allows. Minimum track record length at 95% confidence, with realistic skew and kurtosis, is 2.8 years to distinguish an observed Sharpe of 1.0 from zero — and **11.2 years to distinguish it from 0.5**. Distinguishing a good strategy from a mediocre one takes an order of magnitude more data than distinguishing it from nothing.

**Step 8 — Make 2× spread the base case, not the stress case.** Run a cost ladder at 1×, 1.5×, 2× and 3×. Given the documented approximations, the 1× case is already optimistic. Add overnight financing — even a crude `notional × (rate differential + markup) / 360` beats zero. Score parameter *plateaus* rather than points, and reject anything where the point estimate exceeds the neighbourhood mean by more than about 25%.

---

## 13. The v2.0 plan

Six phases. Phases 0–2 are prerequisites for trusting any number Fiboki produces; nothing later matters without them.

### Phase 0 — Stop the bleeding (days 1–3)

Commit the six outstanding working-tree changes, including the Phase-1 numpy fix that exists only on the laptop. Close the Tradovate live-URL bypass by asserting on parsed hostname rather than string equality, with a regression test. Remove `FIBOKEI_LIVE_EXECUTION_ENABLED: "true"` from both `render.yaml` files and switch to `sync: false`. Set `FIBOKEI_WORKER_EXTERNAL=true` on every API service and confirm exactly one worker is running. Add `os.environ.setdefault("FIBOKEI_WORKER_EXTERNAL", "true")` to `conftest.py` and retire the contradictory take-profit test, so the suite is green and runnable. Pin `pandas`, `numpy` and `pyarrow` to exact versions and generate a lockfile. Delete the 26 debris probe files and add `_*.db*` and `*.db-journal` to `.gitignore`.

### Phase 1 — Make the numbers honest (weeks 1–3)

Rewrite the cost model: spread on both legs, wire up `commission_per_trade`, add gap-aware stop and take-profit fills (`min(stop, bar.open)` for longs), and add per-instrument overnight financing. Rewrite `metrics.py`: annualise by actual trade frequency, mark open positions to market each bar so drawdown is real, carry an account currency and convert USD→GBP through a real FX series, and fix the synthetic hourly date index. Redesign the scorer from first principles: gate on profitability before anything else, make stability sign-aware, map NaN profit factor to zero rather than the cap, and make the 80-trade rule a hard filter on ranking rather than a 10% weighted term. Make `SwingDetector` causal by shifting it by the lookback, which also fixes backtest/paper parity by construction. Move `chikou_span` out of the strategy DataFrame into a display-only series.

Then **re-run the Gen-1 sweep from scratch** and compare. Expect most of the fourteen survivors in section 4 not to survive.

### Phase 2 — Make the selection honest (weeks 3–6)

Implement section 12 in full: carve a final-20% holdout that nothing touches until a survivor is evaluated on it exactly once; add DSR, PBO/CSCV and SPA/StepM as hard gates with their values recorded on every run; replace IID Monte Carlo with the stationary block bootstrap; make walk-forward actually fit parameters; raise the trade minimum to 400. Cull the strategy roster from 64 toward 12–20, with a written economic rationale for each survivor — this directly lowers the statistical bar every one of them must clear. Add the Fibonacci-versus-random-levels test and act on the result.

### Phase 3 — Make execution trustworthy (weeks 5–8, overlapping)

Extract a single shared `ExecutionEngine` that the backtester, paper and live paths all call, so parity is structural rather than aspirational — this eliminates most of the divergence table at a stroke. Establish one sizing authority: compute size once, carry it on the plan, and let adapters convert units but never re-decide. Wire `RiskEngine` into the pre-order path in both paper and broker routes, with fleet state passed down, and record rejections as `ExecutionAttempt` rows so they appear in the audit trail. Persist broker deal IDs, restore positions on recovery, generate a client `dealReference` per signal, and write a pending attempt row *before* dispatch. Fix reconciliation to key on the broker deal ID and run it on startup and hourly. Add worker leasing via a Postgres advisory lock. Add RBAC, login rate limiting, and Origin validation on mutating endpoints.

### Phase 4 — Broker migration (weeks 7–10, overlapping)

Open an OANDA practice account and **first verify the two unknowns**: that the v20 REST API can place orders on a spread-betting sub-account, and how the candle endpoint's pricing compares to your own executable stream. Write a thin typed `httpx` adapter behind the existing `ExecutionAdapter` ABC. Start the price recorder immediately — an append-only store of executable bid/ask from the broker you will actually trade with. Its value is proportional to elapsed time, so it is the one thing worth starting before everything else is ready. Backfill the research matrix from OANDA candles, and keep Dukascopy as an independent cross-check for detecting bad bars.

### Phase 5 — Operator surface (weeks 8–12)

Add a `source` field to the trade contract and render provenance — backtest, paper, broker — as a column everywhere, killing the `/trades` mislabelling. Replace hardcoded "paper" copy with a live execution-mode hook and make the mode banner sticky. Add global SWR error surfacing so an outage stops looking like a flat fleet. Consolidate 14 nav items to six jobs: Operate, Fleet, Research, Backtests, Trades, Charts. One confirm dialog. Responsive shell. Swap the full Plotly bundle for `plotly.js-basic-dist-min`. Compute the realism caveats rather than hardcoding them.

### Phase 6 — Observability and gates (ongoing from week 1)

Initialise Sentry in `worker.py`. Add `WORKER_DOWN` and `HEARTBEAT_STALE` alert events wired to the existing Telegram dispatcher. Make `/health` verify database connectivity, migration revision, build SHA and heartbeat age. Turn off auto-deploy on push and gate deploys on green CI plus backtest regression pins plus a paper/backtest parity test plus a look-ahead sweep across the full strategy registry. Run `alembic upgrade head` as a release step and delete the `_ensure_new_columns` shim. Add backups.

---

## 14. Gates to live money

Do not compress these. Each answers a different question.

**Gate A — research to paper.** DSR > 0.95 with clustered N; PBO < 0.2; SPA consistent p < 0.05 and in the StepM survivor set; walk-forward efficiency ≥ 50% and profitable in ≥ 60% of OOS windows; ≥ 400 trades; edge survives 2× spread; point Sharpe no more than 1.25× the parameter-neighbourhood mean; and an automated config check asserting live execution is disabled.

**Gate B — paper to broker demo.** At least 30 days of continuous paper running with no unexplained heartbeat gaps. A deliberate worker kill in staging must produce both a Sentry event and a Telegram alert within one poll interval — **until a chaos test proves the alert fires, demo promotion is not safe**, because an unnoticed dead worker is indistinguishable from a flat strategy. Reconciliation clean for a full week on the corrected deal-ID key. The broker adapter tested against recorded real payloads, not only mocks.

**Gate C — demo to small live.** At least 3 months and 100 live-equivalent trades. Paper Sharpe within the 90% block-bootstrap confidence interval of the backtest Sharpe. Realised spread and slippage within 1.5× of modelled. Zero unreconciled fills. Every documented approximation either closed or explicitly accepted in writing. A kill-switch drill executed and timed. Start at the minimum size the broker permits.

**Gate D — scale.** Only after the minimum track record length for the *observed live* Sharpe, benchmarked against half the backtested Sharpe, has elapsed — typically 2–4 years. Scale in fractions, never in steps.

**Three stopping rules, pre-registered in code before any live capital:** halt when the probabilistic Sharpe ratio against half the backtested Sharpe falls below 0.50; halt at the 95th percentile of the bootstrap max-drawdown distribution rather than a round number; and run a CUSUM on excess return calibrated to a roughly two-year in-control run length, to catch slow decay a drawdown limit would miss. Every halt automatic, logged to the append-only ledger, and requiring an explicit operator action to reverse.

---

## 15. What not to do

Do not run the 23,040-combination grid before Phases 1 and 2. It costs roughly 51 core-hours plus a 10–20× ladder multiplier to produce results that the current cost model and selection process cannot make meaningful.

Do not add strategies. Every one raises the statistical bar for all the others. The roster should shrink.

Do not enable live execution on any path until Phase 3 is complete and Gate C is met, and keep the hardcoded demo base URL as a permanent belt-and-braces control even after live is enabled elsewhere.

Do not treat the fourteen survivors in section 4 as a shortlist. They are a *starting hypothesis list* for re-testing under the corrected engine, and the six USDJPY entries should be treated as regime artefacts until proven otherwise.

Do not rebuild on NautilusTrader now. Revisit in about twelve months.

Do not trust any of the 52 existing status documents. This one supersedes them; the rest should be stamped historical.

---

## 16. Risks

The largest risk is **that the corrected engine shows no edge anywhere**. It is a realistic outcome — 76% of combinations already lose money before cost corrections, the academic evidence on these specific rule families in FX after data-snooping correction is largely negative, and Ichimoku in currencies has an explicitly negative result in the literature. Phases 1 and 2 are precisely the cheapest way to find that out, which is the strongest argument for doing them first. Discovering it now costs weeks; discovering it with live capital costs money.

**Regime concentration** is the second risk: the strongest current results cluster on USDJPY during an exceptional trend. Regime-segmented validation is not optional.

**Broker migration risk** is real but bounded: the two OANDA unknowns (spread-bet sub-account API access, and pricing-segment parity) both resolve on a free demo in days. Resolve them before writing the adapter, not after. Note also that FTMO, a prop firm, completed its acquisition of OANDA on 1 December 2025 — watch for changes to API terms and the spread-bet offering.

**Operational risk** during the refactor: the platform currently has no deploy gate, no backups and no failure alerting. Phase 6 items should land alongside Phase 0, not at the end.

**Solo-maintainer risk:** 52,000 lines, 52 documents and three deployment configs is more surface than one person can hold. Every phase above should reduce surface, not add it.

---

## 17. Next actions

1. Commit the six outstanding changes, so the Phase-1 fix stops living only on one laptop.
2. Execute Phase 0 in full — it is three days and it removes the two real-money paths, makes the test suite runnable, and pins the dependencies that make determinism meaningful.
3. Open an OANDA practice account and resolve the two unknowns. Start the price recorder the same day.
4. Begin Phase 1. Re-run Gen-1 under the corrected engine and compare against section 4.

---

## 18. What to monitor

Until the corrected engine exists, the only number worth watching is whether Phase 0 and Phase 1 actually land. After that: PBO and DSR on every research run (if PBO drifts above 0.2 the selection process is broken again); realised-versus-modelled spread per instrument (the early warning that backtests have gone stale); reconciliation divergence count, which should be exactly zero; worker heartbeat age, alerted rather than displayed; and the paper-versus-backtest Sharpe gap for any strategy in Gate B.

---

## Appendix — explicitly not verified

Production state on Railway, Vercel and Sentry. Which deployment config is authoritative. Whether duplicate orders or orphaned positions have actually occurred. Actual production environment variables. Tradovate API correctness beyond its safety gating (the module self-documents endpoints as `TODO_VERIFY`). Cross-environment backtest determinism. The behavioural impact of the `bot07`/`bot12` future-row reads (confirmed by inspection; zero signals in the test window). 58 of the 64 strategies were not individually audited. Alembic migrations were not executed. Colour contrast was not measured. Several broker facts are marked unverified in the source research, notably OANDA GSLO availability specifically via the v20 API, Pepperstone's FRN against the FCA register, and IBKR Lite's UK availability; and `labs.ig.com` blocks automated fetching, so IG forum threads are reported by title and volume only, not content.
