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

## Wave 2A status (Workspace; rebuilt 19 October 2026 on `wave/workspace-v2`)

Built, each end to end (API, tests, generated types, UI, Playwright):

- **Workspace** (`/workspace`, WS, F10, `LAYOUT <name>`): a tiling tree over a typed panel
  registry (13 kinds: chart, watchlist, news, calendar, fleet, risk, exposure, alerts,
  journal, research runs, market pulse, system board, notes). The kinds are a `Literal` in
  `api/workspace_layouts.py`, generated into the client, and `components/workspace/registry.tsx`
  is `Record<PanelKind, …>`, so a kind cannot exist on one side only. Split right/down,
  drag-resize (react-resizable-panels), close, add; double-click a header to maximise, Esc
  restores; Ctrl/⌘+1..9 focuses a panel; Tab/⇧Tab move across tile headers, Enter or ↓ enters
  a panel. Below 760 px the tiles stack in reading order. Each panel body is its own lazy chunk.
- **Link groups** (TWS style): amber, cyan, violet, white (never red/green). A panel is a
  source, destination or both; a source's selection retargets every destination in its group.
- **Named layouts per operator, server-side** (`api/workspace_store.py`,
  `<state>/workspace/workspace.sqlite`): versioned, append-only revision trail, API audit row
  per write, 409 on a stale base version (the UI offers "Load theirs" or "Keep mine"),
  unsaved marker, 1.5 s debounced autosave (recorded as `autosave`), ⌘/Ctrl+S, history popover,
  delete as a tombstone. Presets in code: FX monitoring, Research, Incident.
- **Watchlists** (WL, `/markets/watchlist`): CRUD server-side (versioned; four built-in lists
  until saved over); rows from the quote recorder's log (`data/quote_view.py`, a cached tail
  reader) with bid/ask/mid/spread, change and range, a 48-bucket sparkline, session state from
  the instrument's session calendar, data age and the API's verdict (`live`, `stale` after
  90 s with the session open, `closed`, `none`). Statistics in `marketstate/quote_stats.py`
  (pure, hand-checked tests).
- **News monitor** (NWS, `/markets/news`, `<SYM> NEWS`): the headline store read-only, ordered
  by `observed_at`, filtered on the server by instrument, currency and source; chart links per
  headline; recorder state (`stale` after 900 s).
- **Economic calendar** (ECO, `/markets/calendar`, `<SYM> CAL`): the NEWEST official calendar
  version (`latest_official_calendar_version`, the paper path), never the research pin
  (asserted by test); impact/currency/instrument filters; the API decides upcoming / in
  progress / released; countdowns; chart links `?event=<id>` (URL-encoded), and the chart
  page shows a one-line event focus for that id (`GET /api/markets/calendar/events/{id}`).
  Coverage gaps (AUD, CAD, CHF, NZD; beyond the declared end) are caveats, never empty calm.
- **Market Pulse** rebuilt (`/market-pulse`): movers, currency strength (mean signed change
  per currency across recorded FX pairs), realised volatility and session clocks over every
  recorded instrument, all from `GET /api/markets/pulse`; definitions shipped with the data.
- **Price and level alerts**: rules (above, below, cross; mid/bid/ask; once or re-arm) in
  `obs/price_alerts.py` (`<state>/alerts/price_alerts.sqlite`, append-only notifications and
  revisions), CRUD over `/api/alerts/rules` (versioned, audited). Evaluated by a PROCESS, never
  the API: the quote recorder evaluates after every poll (`fiboki quotes record`, failures
  never stop recording) and `fiboki alerts price-evaluate --once|--loop` catches up from the
  log; re-reading quotes never fires twice. NTF (`/alerts/notifications`): history with the
  API's unread count and a server-side read marker (forward only); evaluator state (`stale`
  after 180 s); opt-in browser notifications (off by default, no backlog replay).
- **Data grid**: column order (move up/down), server-side column sets per operator
  (`/api/workspace/column-sets/{grid}`, versioned, default set), CSV export in display order,
  and API-decided conditional formatting (`api/grid_format.py` evaluates rules and sends
  `tones` per cell; `/api/workspace/format-rules/{grid}` is the legend).

Not built / known limits:

- The chart workstation itself does not scroll to or highlight `?event=`; it shows the event
  line above the chart and draws calendar events as it already did. Highlighting is Wave 2B's.
- Browser notifications fire only while NTF or an alerts tile is open (no service worker, no
  background push). Ctrl/⌘+1..9 can be taken by the browser for tab switching in some
  browsers; the panels stay reachable by Tab.
- Quotes are OANDA practice top of book polled every 30 s: moves between polls are invisible
  (an alert crossing inside a poll fires only if the polled price ended across), and spot
  crypto is not recorded by that recorder, so crypto rows read `none`.
- The recorder hook evaluates a poll's quotes when the next poll starts: alert latency is up
  to one poll interval.
- Currency strength is a stated simple mean, not a trade-weighted index; realised volatility
  is not annualised.
- No new launchd service: evaluation rides on the existing `quotes` service.
- `scripts/first-load.mjs` now decodes percent-encoded chunk paths (`%5Bhash%5D`); without it
  the size report could not run on this build.
