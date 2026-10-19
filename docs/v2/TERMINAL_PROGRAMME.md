# Terminal programme (from 18 October 2026)

Goal: make the operator workstation a professional, terminal-class tool for a single operator
running FX and crypto research and paper trading. Inputs: a survey of 13 terminals (Bloomberg,
LSEG Workspace, FactSet, TradingView, Koyfin, thinkorswim, IBKR TWS/Desktop, MetaTrader 5,
NinjaTrader 8, cTrader, Sierra Chart, OpenBB Workspace, QuantConnect) and a full audit of the app
on 18 October (verdict: about 25 to 30% of the way; scores visual 5, IA 3, interaction 6,
completeness 2.5, performance 6, accessibility 8).

Non-negotiable rules stay: no trading logic in the frontend (no indicator maths, signals, P&L,
stops, risk bands or breach decisions), typed API contracts, paper only, provenance honest,
closed candles only, lightweight-charts for price charts, size budgets enforced.

## Audit findings to fix (P0 first)

P0
1. Command has no money view: add equity, day P&L, open positions, running/halted bots strip.
   Home route has ~0.1 kB budget headroom: split the page or re-budget with justification.
2. No fleet screen: `/trading/fleet`, one row per bot (state, last signal, open position, day
   P&L, health, links to strategy, chart, journal). F2 opens it.
3. Cross-links almost absent: one entity model (strategy, run, trade, instrument, session,
   candidate) with routes, each linking to the related ones (journal trade -> chart at trade;
   candidate -> lifecycle, runs, chart; strategy -> runs, tearsheet, chart).
4. Frontend risk logic: `lib/limits-core.ts` (~146) decides breaches and invents 70/90% bands;
   `lib/freshness.ts:25-27` invents worker staleness. Move utilisation, headroom, band, breach
   and staleness to the API; the frontend only renders. Attention queue and bars must agree.
5. Palette ranks wrongly (EURUSD -> Exposure) and offers non-existent entities: search real
   entities, exact matches first.

P1
6. Command line knows only screen codes and pushes any text to `/markets/<TEXT>`: instrument
   and strategy completion, `<symbol> <function>` grammar (EURUSD H4, EURUSD BT, EURUSD NEWS),
   history (up-arrow, last 50, survives reload), function directory.
7. Layout shift: desktop up to 0.43, mobile up to 1.46: skeletons reserve final height.
8. Too much chrome: four stacked bands plus a rail repeating F-keys; content starts ~y=110,
   chart ~y=290 at 1440; mobile chart at y~540. Merge F-key row, collapse page intros.
9. Chart: clipped y-axis top label, duplicated mobile label, trades table TP/EXIT cut at 1440,
   three chip rows above the chart, dark-theme up/down candles both amber.
10. Shortcut sheet taller than viewport, sticky header covers rows.
11. Stale-data cues inconsistent (status bar vs Workers page ages); page-level stale banner,
    dim stale panels.
12. Fragmented system pages (Services, Workers, Broker health, Data health, Alerts): one System
    board; Risk repeats Exposure; collapse NOT REPORTED rows.
13. Market Pulse is a spread bar chart of 20 of 40 instruments: make it a real pulse (movers,
    currency strength, volatility, sessions) over the whole universe.

P2
14. Type scale: ~5 sizes (11, 12, 13, 15, 20); remove fractional and 9 px.
15. Chip noise: provenance once per column/panel when uniform.
16. Wasted width at 1920; R-multiple chart with 4 observations as full bars.
17. Test fragility: CSP sweep 30 s for 33 routes; a11y sweep mostly on error states.
18. Lifecycle entity page has no equity, runs, trades or chart link.

## Features to add (top 30 by value for effort, from the survey)

1 freshness stamps and source badges on every panel; 2 command history and recents;
3 autocomplete under 100 ms; 4 function directory and cheat sheet from the same registry;
5 maximise/restore panel (double-click, Esc); 6 column chooser, saved sets, CSV export
everywhere; 7 conditional formatting by rule; 8 metric tooltips with definition and formula;
9 result provenance (params, dataset version, cost assumptions, run time); 10 drawdown
(underwater), monthly returns heatmap, trade histogram on every backtest result; 11 pass/fail
checks beside ladder numbers; 12 chart templates; 13 hide-all toggles per layer; 14 data
window; 15 go-to-date and time zone; 16 saved named layouts with autosave and unsaved marker;
17 colour-group linking (source/destination); 18 multi-chart grid with symbol and timeframe
sync; 19 crosshair and time-axis sync; 20 watchlist with live quotes and sparklines, saved
columns; 21 currency exposure heatmap; 22 risk limit usage bars (API-computed); 23 parameter
heatmap; 24 MAE/MFE columns and scatter; 25 walk-forward view; 26 economic calendar panel with
impact filter linked to chart markers; 27 correlation matrix with window selector; 28 Monte
Carlo of trade sequence (backend); 29 bar-by-bar replay (backend-driven, closed candles,
paper and backtest trades appear as they would have); 30 alert manager with history.

Also from the audit's missing list: research job queue with progress, strategy comparison,
news monitor with instrument filter, price alerts, notification history, per-strategy and
per-instrument P&L attribution, session clocks, currency strength map.

Not applicable while paper-only: live order ticket, DOM ladder, Level 2.

## Waves

- Wave 1A Operator core (shell, command, Command page, fleet, cross-links, risk logic to API,
  System board, chrome, type scale, CLS, stale cues, palette).
- Wave 1B Research depth (tearsheet, job queue, comparison, parameter heatmap, walk-forward,
  Monte Carlo, MAE/MFE, pass/fail, provenance).
- Wave 2A Workspace (tiling layouts, linked groups, saved layouts, watchlist with sparklines,
  news monitor, economic calendar, alert manager, Market Pulse rebuild).
- Wave 2B Chart (multi-chart grid and sync, data window, templates, go-to-date, replay,
  alerts from levels, chart defects from P1 #9).
- Wave 3 Independent audit and critique, then retask fixes; repeat until the rubric is >= 8 in
  every area or the remaining gaps are named and justified.

## Wave 3O: Operator, paper-bot control, journal and audit (19 October 2026)

PAPER only. Every state, verdict, weight and probability is computed in the backend
(`src/fiboki/ops/`, routes in `src/fiboki/api/routers/ops.py`); the workstation renders.

Built (each end to end: backend, tests, typed client from the generated OpenAPI, UI):

1. **Per-bot pause / resume / drain** (`ops/controls.py`). A durable control ledger
   (`<state_dir>/paper_controls.jsonl`, `core.paths.bot_controls`) replayed by the paper runtime every
   cycle (`ForwardControls`, `ControlledEvaluator` in `entrypoints/paper_forward.py`): pause suppresses
   new entries (journalled as `entries_suppressed_by_control`) and keeps managing open positions; drain
   records `drain_complete` once the book is flat (bot `stopped`); resume. Operator, required reason
   (8-500 chars) and UTC time on every record; admin-only like the kill switch; audited in the API trail
   (allowed, refused, failed). Survives a restart (the ledger is the authority; the snapshot carries a
   copy for information). Fleet column, bot page `/trading/sessions/[session_id]`, command line
   `PAUSE|RESUME|DRAIN <bot>` (opens the dialog; never acts alone).
2. **Hash-chained paper audit log** (`ops/audit_chain.py`): every order intent, fill, open, close,
   amend/close instruction, control and risk-gateway decision, `prev_hash` + `hash` = sha256 over
   canonical JSON, in `<ledger_dir>/audit_chain.jsonl`. `GET /api/trading/audit-chain` verifies from
   genesis and names the first broken row; System-board panel and bot page.
3. **Backtest-versus-paper reconciliation** (`ops/reconcile.py`, runner `entrypoints/ops_jobs.py`):
   the same bound document, wiring and closed bars through the engine, compared trade for trade
   with a reason per divergence. A job: the API records the request; `fiboki ops jobs` runs it.
4. **Paper maturity report**, advisory (`ops/maturity.py`, `maturity_advisory_v1`): trades, days live,
   drawdown against the stored backtest's bootstrap band at the same trade count, realised versus
   expected cost per unit, reconciliation, audit chain. Not-evaluated blocks. Enables nothing.
5. **Self-correlation of candidates** with the paper fleet (backtest and paper bases), flag above 0.7.
6. **Risk of ruin and position size** under `/trading/risk`: `size_trade` itself for sizes, the
   bootstrap's `resample_with_compounding` for ruin, seeded.
7. **Advisory portfolio weights** on `/trading/allocation`: equal-risk, inverse-vol, min-correlation,
   equal-weight reference, with portfolio stats. Display only.
8. **Structured journal** on `/trading/execution`: note, tags, grade A-C, mistakes, screenshot link,
   intervention reason; SQLite store with every version kept and stale writes refused (409);
   server-side filters (`/api/trading/journal/trades`); keyboard editor (Enter on a row, 1/2/3/0,
   Ctrl+Enter, Esc).
9. **Weekend alert noise**: an amend/close refused with `market_closed` is one INFO
   `instruction_deferred` per position (was repeated CRITICAL `reconciliation_divergence`); a refusal
   once the market is open escalates as before; success clears the deferral.

Not built / known limits:

- The ops job runner has no launchd service; until `fiboki ops jobs --loop` runs (a user action), a
  requested reconciliation stays `queued`, and the screen says so.
- Reconciliation needs validated stored bars covering the session (`DataStore.read_latest`); a window the
  store does not cover is `not_evaluated`. The paper feed's sliding indicator window and the engine's
  whole-frame indicators can differ for recursive indicators; slippage draws differ BY CONSTRUCTION
  (`rng_for(seed, bar_index, …)` with bar indices counted from different origins) and are reported as
  `slippage_draw`. On the synthetic session every trade matches on entry, exit, prices and size.
- A divergence after the first one is labelled `cascade`; read `first_divergence_at` first.
- The paper audit chain is tamper-evident, not tamper-proof (a writer can rebuild it from the edited
  row on); control actions enter it when the runtime applies them, so a stopped process records them
  on its next start. The chain starts with this wave; older sessions show `absent`.
- Daily returns for correlation and weights are the closed-trade balance curve (no mark-to-market),
  on calendar days, zero on days without an exit.
- The risk calculator's user-typed R list is labelled `backtest` provenance with a
  `user_supplied_distribution` warning caveat (no provenance value exists for an operator hypothesis).
- Market-closed deferrals are kept in memory: a restart mid-weekend notifies once more. The amend path
  shares the close path's helper but is covered by tests only through the close path.
- `scripts/first-load.mjs` does not URL-decode `src` paths, so the byte-budget checks in
  `source-rules.spec.ts` fail on `/lifecycle/[hash]` (pre-existing); measured with decoded paths, every
  route is within budget (`/` 177.3 of 180 KiB; ops pages 181-191 of 230 KiB).
