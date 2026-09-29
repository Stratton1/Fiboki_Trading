# Fiboki V2: What the platform can do, and how it performs

**As of:** 2026-09-29, `v2/integration`. Every claim below names its evidence. "Not verified" means exactly that.

## 1. What it can do today (verified by tests in this tree)

**Research.** Ingest HistData/OANDA bars into a content-addressed, integrity-checked store; compile declarative strategy documents (Donchian, Fibonacci pullback, RSI, Ichimoku, MACD seeds) on closed candles only; backtest with both-leg costs, gap-aware fills, business-day financing with triple days, FCA/ESMA leverage caps by currency set, cost-inclusive sizing (`fixed_fractional_v2`), portfolio construction (tiers, correlation, drawdown throttle, regime), and bar-indexed entry locks; validate through a seven-rung ladder (sanity, screen, walk-forward, purged CV, robustness, DSR/PBO/SPA deflation, one-look holdout) with a versioned gate set; run discovery campaigns with honest trial accounting; keep every derived key versioned and every ledger append-only. Full suite: 4,802 passed before the engine mirror landed (final figure in the closing report).

**Paper trading.** Replay paper sessions against the risk gateway (the checks in `RiskGateway.CHECKS`: exposure, correlation, daily/weekly loss, drawdown, margin after trade, data freshness, abnormal spread, event blackout from the official calendar, entry locks, event veto in shadow, kill switch), sizing identically to the backtest (byte-identical parity test). **Forward paper trading** against live OANDA practice candles and quotes through `fiboki paper forward` from a hashed wiring file, with startup and periodic reconciliation, a durable kill switch honoured mid-run, and a launchd service. Verified end-to-end with recorded OANDA responses; **not yet run against a real OANDA account** (needs your practice token).

**Agents (local models).** llama.cpp or Ollama on loopback with pinned weights; a nightly research cycle (director, researcher, mutation, auditor, queued backtest/validation, critic, librarian), incident-triggered failure investigation, headline scanning every 15 minutes with a tool-less event classifier writing to a quarantined store, forecasts with deterministic Brier scoring, thesis debates per H4/D1 close (opt-in per instrument), an offline eval harness, and a run manifest on every run. **No agent has yet run against a real model**; every workflow is verified with the offline scripted provider. First run: USER_ACTIONS P7.

**Data.** Twelve central-bank feeds plus BIS, GDELT and Finnhub (optional) into an append-only headline store with first-seen timestamps; six point-in-time macro providers (ALFRED vintages, CFTC with release-time stamping, ECB, BoE, ONS, NY Fed); a 339-event official economic calendar (2024-01 to 2026-12); CFTC, OANDA books (if still served) and Myfxbook positioning; a source registry with licence status for all 31 sources.

**Operator workstation.** Sign-in and roles; live SSE stream with freshness states (never a blank or a zero after first data); mode frame per execution mode (live is magenta with "REAL MONEY"); provenance chip grammar (hollow = simulated, filled = executed, MIXED with counts); kill switch with PAUSE (reason) and FLATTEN (typed); promote with per-caveat acknowledgement and server-computed consequences; attention queue and incidents; a virtualised data grid, command palette and keyboard chords; a11y at zero serious axe violations; contrast 268/268 pairs; Playwright 478 passed.

**Operations.** `fiboki doctor` (16 checks; reads `~/.fiboki/env` as the services do), desktop installer, backup/restore with manifest, launchd services (api, worker, web, news, llama, paper), a desktop launcher, alert delivery with an outbox, a watchdog process. **Running now on the MacBook** from the runtime checkout `~/fiboki`: api, worker, web (production build) and news, all four verified serving (BUILD_LOG 2026-09-29, last entry); `llama` and `paper` wait on a llama.cpp model and an OANDA practice token.

## 2. What it cannot do yet

- Trade live money: by design, five independent controls, none set; `fiboki worker run live` refuses. Live enablement is Gate C in the roadmap and needs OANDA spread-bet API confirmation.
- Let an agent change a position size or block an entry: tier T1 (shadow). Both channels compute and log what they would have done; enabling requires the pre-registered comparison (`research/preregistration/*.json`) to pass and a signed tier record.
- Author strategies from agents into the ladder automatically: the S0–S9 pipeline is specified (report G §3.5) and the T4 tier value exists; the pipeline is the next agent wave.
- Chart workstation: the overlays endpoint exists; the Lightweight Charts UI is the next frontend wave.
- Backtests before 2024 with event blackouts: the calendar starts 2024-01-01 (backfill is on the backlog).

## 3. How it performs (the honest answer)

**No strategy in this repository has a demonstrated edge.** Three discovery campaigns have now run: K1 (326 trials), K2 (3,700 trials, superseded engine) and **K3 (2026-09-29, `engine_v3_realism`, GBP account, official calendar, portfolio-construction sizing: 542 cells over 13 instruments on H4, 1,128 backtests, true trial count 7,376, 70 minutes on the MacBook)**. K3 produced zero survivors with the holdout unspent. The one cell that reached the deflation rung (a Donchian mutant) had a deflated Sharpe of 0.0016 against a per-bar threshold of 0.030, nowhere near.

**What K3 actually says, read carefully.** 420 of the 542 cells died at rung 0 on `min_trades` (400 required; median observed 68, maximum 391) and 106 on non-positive expectancy at defaults. So for 78% of the search the ladder never tested the edge at all: on twenty years of H4 bars these rule families, with the event blackouts and regime gates they declare, simply do not trade often enough to be judged at the current evidence bar. That is a finding about sample size and about the gate set (the E-1 calibration study has not run, so 400 is an uncalibrated number), not proof that the effects are absent. It is also the strongest argument yet for not tuning: a gate lowered until something passes is the V1 mistake. **K4** (2026-09-30, `tsmom_dual_horizon`, the first new bet since the roster froze, pre-registered in `research/reports/RESEARCH_LEDGER.md`): 80 cells on the same H4 series, true N 8,496, all dead at rung 0 (47 short of 400 trades with a median of 315; 33 with non-positive default expectancy). Nothing survived. The D1 version of that bet is untested because the store has no D1 bars.

**Paper journal figures are replays, not forward performance.** The API serves 283 PAPER trades from recorded replay sessions; the equity includes an unrealised mark on one long-held Donchian position, labelled as such. Forward paper trading starts the day you add a practice token.

**Agent quality is unmeasured.** The measurement apparatus exists (evals, forecast scoring, shadow ledgers, bake-off harness spec) and nothing has been measured, because no real model has run yet.

## 4. What would change the answer

1. Re-run K1/K2 under `engine_v3_realism` with the official calendar (deterministic; compute-bound).
2. Run E-1 (gate calibration) so the gate set has a known false-discovery rate and power.
3. Start forward paper trading and the quote recorder; after four weeks, replace the static spread model with measured hour-of-week spreads.
4. Run the first-model acceptance protocol (20 recorded runs per workflow) and the bake-off; only then trust an agent's output for anything beyond research notes.
