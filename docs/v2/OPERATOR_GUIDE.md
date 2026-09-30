# Fiboki V2: the whole platform, explained

**For:** Joe and Tom, the operators. **As of:** 2026-09-30, `v2/integration` at `0af5424`.
Every claim here names where it is enforced or measured. `PLATFORM_STATUS.md` is the short
"what works and how it performs" sheet; `WORKSTATION_INVENTORY.md` lists every page and button;
this document is the map between them.

## 1. What Fiboki is, in one paragraph

A research-first trading operating system that runs on your Mac. It ingests market data, lets
you write trading rules as declarative documents, tests those rules through a statistical
ladder designed to reject luck, trades the survivors on paper against your OANDA practice
account with the same sizing and risk engine a live account would use, watches markets and
news with local language models that can annotate but never trade, and shows all of it in an
operator workstation whose numbers always carry their provenance. Nothing in it can reach a
live broker today; five independent controls stand between the code and real money, two of
them constants in the source.

## 2. The layers, top to bottom

```
 Workstation (Next.js, browser)        display, controls, workflow; no trading maths
 ───────────────────────────────────────────────────────────────────────────────
 API (FastAPI, :8000)                   typed contracts; every number a Figure with provenance
 ───────────────────────────────────────────────────────────────────────────────
 ALPHA      strategy documents (DSL) → compiled rules → signals on closed bars only
 PORTFOLIO  construction: tiers, health, confidence, correlation, caps, vol target,
            drawdown throttle, regime scalars, conviction (down-only)  → position sizes
 RISK       the gateway: the checks in RiskGateway.CHECKS run on every intent; kill switch
 EXECUTION  paper venue today; OANDA adapter (practice host only); live compiled out
 ───────────────────────────────────────────────────────────────────────────────
 Data       content-addressed, versioned store (OANDA MID 2005→, HistData BID kept),
            official calendar, headlines, macro (ALFRED, CFTC, ECB, BoE, ONS, NY Fed)
 Agents     local models (Ollama/llama.cpp) with 33 read-only tools, hash-chained audit,
            influence tier T1 (annotate, shadow); never in the order path
 Services   launchd: api, worker, web, news, paper (+ llama, optional)
```

The dependency direction points downwards only (`tests/unit/test_layering.py`). Strategy code
cannot import broker code; agent code cannot import execution, risk or portfolio code (AST
tests). An `Order` is constructed in exactly one place.

## 3. How a strategy travels

1. **Written as a document** (`research/strategies/*.json`, six seeds; `research/generated/`,
   100 grammar-generated): family, universe, timeframes, regime gate, setup, entry, stop,
   take-profit, trailing, position management, declared parameter domains, and a hypothesis
   that must state the published evidence *against* the idea. `is_reparameterisation` decides
   mechanically whether a new document is a different bet.
2. **Compiled** to rules over centralised indicators (`indicators/`), evaluated on closed
   candles only.
3. **Backtested** by `engine_v3_realism`: UTC mid bars, both-leg spread, slippage, gap-aware
   fills, business-day financing with triple days, FCA/ESMA leverage by currency set,
   cost-inclusive sizing, portfolio construction identical to paper (byte-identical parity
   test), deterministic (`rng_for(seed, bar, sequence)`).
4. **Climbs the validation ladder** (`validation/ladder.py`): rung 0 sanity (minimum trades,
   positive expectancy at defaults), 1 in-sample screen, 2 walk-forward, 3 purged CV, 4
   robustness (parameter plateau, 2× spread), 5 deflation (DSR against the honest trial count,
   PBO, SPA), 6 one-look holdout. The gate set is versioned (`v2.0.0-audit`); every report,
   pass or fail, is stored in the append-only experiment ledger with its trial accounting.
5. **Campaigned** (`research/run_discovery_campaign.py`): every document × instrument ×
   timeframe cell, pre-registered in `research/reports/RESEARCH_LEDGER.md` before compute,
   with the external prior trials declared by hand (the one number nothing can enforce).
6. **Promoted** only by an operator, through the caveat checklist, into the lifecycle
   (`/lifecycle/[hash]`), then **paper-traded** from a committed, hashed wiring file
   (`entrypoints/wiring/paper_forward_v1.json`) against OANDA practice candles and quotes.
7. **Live** requires five controls, two of them source constants. None is set.

Where it stands: K1 to K5 (2,577 cells, 20,904 trials) produced no survivor. The binding
constraint is the uncalibrated 400-trade minimum, not the rules (§7).

## 4. Data

- **Store** (`var/datastore`): content-addressed, versioned, integrity-checked; every derived
  dataset has lineage back to its source. Since 2026-09-30 it holds OANDA practice MID bars
  for all 123 registered instruments (H1, H4, D1, 2005-01 to yesterday, 20 million bars) beside
  the HistData BID series. Research reads the newest validated version per pair, so OANDA now.
- **Instruments** (`core/instruments.py`): all 123 the practice account offers, built from
  OANDA's own instrument endpoint (pip, precision, sizes, margin rate), cross-checked against
  the ESMA leverage classes, pinned by golden tests. Exotics are registered with truthful costs
  and kept out of seed universes.
- **Calendar and news**: official economic calendar (339 events, 2024-01 to 2026-12, USD EUR
  GBP JPY); twelve central-bank feeds plus BIS, GDELT and Finnhub into an append-only headline
  store with first-seen timestamps; point-in-time macro providers. Event blackouts run in the
  gateway and the engine where the calendar covers.
- **Approximations, named**: static spreads (a snapshot for the 82 new instruments), no
  measured slippage, financing from a class table, calendar coverage starts 2024.

## 5. Paper trading and risk

`uk.fiboki.paper` runs `fiboki paper forward` on Donchian/XAUUSD-EURUSD-GBPUSD H4 from the
hashed wiring: startup and periodic reconciliation, live practice pricing for spreads, entries
filled in the book at the next bar's open, every intent through the gateway with the
`limits_v2_paper` set (data tolerances relaxed, risk limits identical to live). The kill switch
is a durable journal read by every process: PAUSE (reason), FLATTEN (typed), lift (typed). The
drawdown throttle scales size ×0.6/×0.3, pauses at 15%, flattens at 20%. Nothing has traded
yet: a Donchian breakout has to occur first.

## 6. Agents

Local models only (Ollama `qwen3:4b` today; llama.cpp on the desktop), pinned by digest, every
call in a hash-chained ledger. Workflows: nightly research cycle (director → researcher →
mutation → auditor → queued backtest/validation → critic → librarian), failure investigation,
headline scan every hour with a tool-less event classifier writing to a quarantined store,
forecasts scored by Brier, thesis debates per close. Influence is a signed tier: T1 today
(annotate, shadow). The never-list: order origination, upsizing, risk-limit changes, kill-switch
disarm, mode changes, holdout selection. Quality is unmeasured until the 20-run acceptance
protocol and the offline evals run against the real model.

## 7. Why nothing has survived, precisely

Across K3, K4 and K5: 2,173 of 2,577 cells died at rung 0 on `min_trades >= 400`; the median
observed trade count is 68 (K3), 315 (K4) and 90 (K5), and in K5 not one cell reached 400. The
four cells that reached rung 4 died on the plateau ratio (1.6 to 1.8 against 1.25). None
reached deflation. On twenty years of H4 bars, a rule with a regime gate and an event blackout
trades every 100 to 300 bars; 400 trades needs one every 75 bars. So the gate demands a trading
frequency at which spreads dominate, and rejects everything slower before any statistical
test runs. The E-1 calibration study exists precisely to measure whether the gate set has
acceptable false-discovery rate and power. Its first real-data process ran on 2026-09-30
(`research/reports/e1/`): with a true per-trade Sharpe of 0.08 injected into a candidate, the
gate set promoted it 0 times in 400; at 0.12, four times. Size was 0 in 400. So the honest
statement is now sharper: the ladder as configured cannot see an edge of the size this platform
could plausibly find, because `min_trades` ends 60% of ladders before any edge matters and the
deflation charged 20,896 trials rejects nearly all of the rest. The decision waits for the second
process (the filing's rule), and no threshold is moved by looking at a real strategy's score
(pre-registration rule 5).

## 8. The workstation

Nine screens (`WORKSTATION_INVENTORY.md` has every control): Command (server-ranked attention
queue, loss-limit bars, kill-switch timeline, incidents), Trading (portfolio, positions with
distance-to-stop, execution, candidates with rung meters, risk & exposure limit board and
matrix), Research (strategies, validation, experiments, hypotheses, datasets, parameter lab),
Markets (explorer, chart workstation on Lightweight Charts with overlays from the API, regimes
grid, correlations, data quality), Intelligence (agents, runs, research memory), System
(services, workers, broker and data health, incidents, logs, settings, legend). Shell: mode
frame (paper cyan, live magenta "REAL MONEY"), provenance chips (hollow simulated, filled
executed), live SSE stream, ⌘K palette, g-chords, ⇧K opens the kill switch and never arms.
Gates in CI: typecheck, lint, byte budgets, contrast, axe, 490 Playwright tests.

## 9. Operations on the Mac

Two checkouts: development under `~/Documents/...` (edit, test, campaigns), runtime at
`~/fiboki` (launchd services; outside `~/Documents` because macOS TCC blocks LaunchAgents
there). Deploy = push from development, pull in runtime, `launchd-install.sh --load`. `Fiboki.app`
on the Desktop starts the services silently and opens the workstation. `fiboki doctor` is the
truth about the machine. Secrets in `~/.fiboki/env` (read without shell expansion), passwords in
the Keychain. `scripts/oanda-backfill.sh` refreshes the data store.

## 10. What is not built

Live execution (by design). Agent Desk screen. Phone kill-switch view. Chart replay, drawings,
calendar markers on the chart. Per-family holdout budgets. Measured spreads (needs four weeks
of the quote recorder). Pre-2024 calendar. E-1's second process (`perturbed_price_paths`) has not run. Nothing has traded
forward yet. No strategy has an edge demonstrated by this pipeline.
