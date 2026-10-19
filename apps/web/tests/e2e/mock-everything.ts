import { type Page, type Route } from "@playwright/test";
import { mockChartWave2B } from "./chart2-fixtures";
import {
  API,
  attentionItem,
  attentionPage,
  backtestRuns,
  backtestTrades,
  chartBars,
  chartOverlays,
  exposurePage,
  figure,
  incidentEnvelope,
  killSwitchHistory,
  killSwitchView,
  mockShell,
  modeBanner,
  positionsPage,
  riskState,
  source,
} from "./fixtures";

const page1 = <T>(items: T[], detail = "Inventory fixture.") => ({
  items,
  total: items.length,
  offset: 0,
  limit: 100,
  source: source("live", detail),
  caveats: [],
});
const env = <T>(data: T, detail = "Inventory fixture.") => ({
  data,
  source: source("live", detail),
  caveats: [],
});

function strategy(id: string, name: string, family: string, hypothesis: string) {
  return {
    strategy_id: id,
    name,
    family,
    author: "joe",
    hypothesis,
    timeframes: ["H1", "H4"],
    universe: ["EURUSD", "GBPUSD", "USDJPY"],
    content_hash: `${id.slice(0, 6)}0123456789abcdef`,
    schema_version: "1",
    complexity: figure(7, "backtest", "count"),
    parameter_count: figure(4, "backtest", "count"),
    rule_count: figure(3, "backtest", "count"),
    parent_strategy_ids: [],
    notes: "",
  };
}

export const STRATEGIES = [
  strategy(
    "ichimoku_kumo_trend",
    "Ichimoku kumo trend",
    "trend",
    "Price closing outside the cloud continues; Deng, Sakurai and Ueda (2021) found no significant profitability after data snooping.",
  ),
  strategy("donchian_breakout_atr", "Donchian breakout ATR", "breakout", "Channel breakouts with ATR stops."),
];


// -------------------------------------------------------- the walkthrough


/**
 * Every endpoint a page reads, answered with a happy-path payload, so a page
 * renders its SUCCESS state. Shared by the inventory walkthrough, the axe
 * sweep on data-filled pages and the CSP sweep (programme item 17: the a11y
 * sweep used to run mostly against error states). An unanticipated API call
 * answers 599 and is recorded in `unexpected`.
 */
/** Every endpoint the workstation reads, answered with a happy-path payload. */
export async function mockEverything(page: Page, unexpected: string[]) {
  // Registered FIRST so every specific route below wins over it: anything
  // that reaches here is an API call this walkthrough did not anticipate.
  await page.route(`${API}/**`, (route: Route) => {
    const url = new URL(route.request().url());
    unexpected.push(`${route.request().method()} ${url.pathname}${url.search}`);
    return route.fulfill({
      status: 599,
      json: { code: "unmocked_in_walkthrough", detail: "Not mocked.", correlation_id: "cid-walk", context: {} },
    });
  });
  await mockShell(page);

  const json = (pattern: string, body: unknown) =>
    page.route(`${API}${pattern}`, (route: Route) => route.fulfill({ json: body }));

  // Command: the queue as the backend builds it, including an incident item
  // (acknowledgeable from the row) whose deep link is the incident read
  // model's own (routers/incidents.py: /system/incidents/{id}).
  await json(
    "/api/command/attention",
    attentionPage([
      attentionItem("incident:inc-1", "warning", "/system/incidents/inc-1", {
        category: "incident",
        title: "Worker heartbeat late",
      }),
      attentionItem("kill_switch:armed", "critical", "/risk", { title: "Daily loss at 82% of its limit" }),
      attentionItem("strategy_review:abc", "info", "/trading/candidates", { title: "Two candidates await review" }),
      // routers/command.py's own strategy-review link: /lifecycle/<content hash>.
      attentionItem("strategy_review:ichimoku_kumo_trend:paper", "info", "/lifecycle/ichimo0123456789abcdef", {
        category: "strategy_review",
        title: "Ichimoku kumo trend: review its lifecycle",
      }),
    ]),
  );
  // One incident (/system/incidents/<id>) and the kill-switch journal (the
  // timeline on Command and Risk & Exposure).
  await page.route(
    (url) => url.pathname.startsWith("/api/system/incidents/") && !url.pathname.endsWith("/ack") && !url.pathname.endsWith("/note"),
    (route: Route) => route.fulfill({ json: incidentEnvelope() }),
  );
  await page.route(
    (url) => url.pathname === "/api/system/kill-switch/history",
    (route: Route) => route.fulfill({ json: killSwitchHistory() }),
  );
  // The lifecycle store, addressed by content hash (/lifecycle/<hash>).
  await page.route(
    (url) => url.pathname.startsWith("/api/trading/lifecycle/strategies/") && !url.pathname.endsWith("/evaluation"),
    (route: Route) =>
      route.fulfill({
        json: env({
          strategy_id: "ichimoku_kumo_trend",
          strategy_content_hash: "ichimo0123456789abcdef",
          lifecycle: "paper",
          band: "healthy",
          degraded: false,
          ever_evaluated: true,
          entered_state_at: "2026-09-20T09:00:00Z",
          last_evaluated_at: "2026-09-29T09:00:00Z",
          latched_halts: [],
          missing_rule_registrations: [],
          health: figure(0.91, "paper", "ratio"),
          last_score: figure(0.09, "paper", "ratio"),
          last_confidence: figure(0.7, "paper", "ratio"),
          n_transitions: figure(2, "paper", "count"),
        }),
      }),
  );
  await page.route(
    (url) => url.pathname.startsWith("/api/trading/lifecycle/strategies/") && url.pathname.endsWith("/evaluation"),
    (route: Route) =>
      route.fulfill({
        json: env({
          strategy_content_hash: "ichimo0123456789abcdef",
          at: "2026-09-29T09:00:00Z",
          state_before: "paper",
          state_after: "paper",
          demoted: false,
          summary: "No rule fired.",
          score: figure(0.09, "paper", "ratio"),
          confidence: figure(0.7, "paper", "ratio"),
          rule_evaluations: [],
          divergence: [],
          latched_halts: [],
          notes: [],
        }),
      }),
  );

  // Markets
  await page.route(
    (url) => url.pathname === "/api/markets/instruments",
    (route: Route) =>
      route.fulfill({
        json: page1(
          ["EURUSD", "GBPUSD", "USDJPY"].map((symbol) => ({
            symbol,
            asset_class: "fx",
            base: symbol.slice(0, 3),
            quote: symbol.slice(3),
            trading_hours: "Sun 22:00 to Fri 22:00 UTC",
            pip_size: figure(symbol.endsWith("JPY") ? 0.01 : 0.0001, "backtest"),
            contract_size: figure(100000, "backtest", "count"),
            min_size: figure(0.01, "backtest", "lots"),
            size_step: figure(0.01, "backtest", "lots"),
            typical_spread_pips: figure(symbol === "EURUSD" ? 0.6 : 0.9, "backtest", "pips"),
            retail_leverage: figure(30, "backtest", "x"),
            annual_financing_bps: figure(-250, "backtest", "bps"),
          })),
        ),
      }),
  );
  await json(
    "/api/markets/regimes",
    page1([
      {
        instrument: "EURUSD",
        available: true,
        detail: "Classified on 500 H1 bars.",
        volatility: "normal",
        direction: "up",
        liquidity: "liquid",
        stress: "calm",
        persistence: "trending",
      },
      {
        instrument: "USDJPY",
        available: false,
        detail: "No bar history mounted for USDJPY.",
        volatility: null,
        direction: null,
        liquidity: null,
        stress: null,
        persistence: null,
      },
    ]),
  );
  await json(
    "/api/markets/correlations",
    env({
      instruments: ["EURUSD", "GBPUSD", "USDJPY"],
      matrix: [
        [1, 0.82, -0.41],
        [0.82, 1, -0.33],
        [-0.41, -0.33, 1],
      ],
      window_bars: 500,
      available: true,
    }),
  );
  await json(
    "/api/markets/data-quality",
    page1([
      {
        instrument: "EURUSD",
        available: true,
        quality: "validated",
        detail: "Validated.",
        bars: figure(52000, "backtest", "count"),
        gaps: figure(0, "backtest", "count"),
        stale_runs: figure(0, "backtest", "count"),
      },
      {
        instrument: "USDJPY",
        available: true,
        quality: "pending",
        detail: "Not yet validated.",
        bars: figure(51000, "backtest", "count"),
        gaps: figure(3, "backtest", "count"),
        stale_runs: figure(null, "backtest", "count"),
      },
    ]),
  );
  await page.route(
    (url) => url.pathname.startsWith("/api/markets/bars/"),
    (route: Route) => {
      const tf = new URL(route.request().url()).searchParams.get("timeframe") ?? "H1";
      return route.fulfill({ json: chartBars({ timeframe: tf }) });
    },
  );
  await page.route(
    (url) => url.pathname.startsWith("/api/markets/overlays/"),
    (route: Route) => route.fulfill({ json: chartOverlays() }),
  );
  await page.route(
    (url) => url.pathname.startsWith("/api/markets/backtests/"),
    (route: Route) => {
      const parts = new URL(route.request().url()).pathname.split("/");
      return route.fulfill({
        json: parts[6] === "trades" ? backtestTrades(decodeURIComponent(parts[5] ?? "")) : backtestRuns(),
      });
    },
  );
  await page.route(
    (url) => url.pathname.startsWith("/api/markets/drawings/"),
    (route: Route) =>
      route.request().method() === "GET"
        ? route.fulfill({
            json: {
              data: {
                symbol: "EURUSD",
                owner: "joe",
                version: 0,
                updated_at: null,
                updated_by: "",
                drawings: [],
                can_edit: true,
                others: [],
                max_drawings: 500,
              },
              source: source("live", "Operator drawings."),
              caveats: [],
            },
          })
        : route.fallback(),
  );
  // Wave 2B chart routes (tests/e2e/chart2-fixtures.ts).
  await mockChartWave2B(page);

  // Trading
  await json("/api/trading/risk", env(riskState(), "Limit set limits_v1_paper."));
  await json("/api/trading/exposure", exposurePage());
  await json("/api/trading/positions", positionsPage());
  await json(
    "/api/trading/portfolio",
    env({
      balance: figure(10250, "paper", "GBP"),
      equity: figure(10280.25, "paper", "GBP"),
      realised_pnl: figure(250, "paper", "GBP"),
      unrealised_pnl: figure(30.25, "paper", "GBP"),
      open_positions: figure(2, "paper", "count"),
      max_drawdown_pct: figure(1.1, "paper", "pct"),
      provenance_mix: { paper: 12 },
      equity_curve: {
        name: "Equity",
        provenance: "paper",
        unit: "GBP",
        points: Array.from({ length: 30 }, (_, i) => ({
          t: new Date(Date.parse("2026-09-01T00:00:00Z") + i * 86_400_000).toISOString(),
          v: 10000 + i * 9 + (i % 4 === 0 ? -20 : 0),
        })),
        caveats: [],
      },
    }),
  );

  // Research
  await json("/api/research/strategies", page1(STRATEGIES));
  await json(
    "/api/research/hypotheses",
    page1(
      STRATEGIES.map((s) => ({
        strategy_id: s.strategy_id,
        statement: s.hypothesis,
        family: s.family,
        structural_keywords: ["cloud", "trend"],
        tested: true,
        supporting_experiments: figure(3, "backtest", "count"),
      })),
    ),
  );
  await json(
    "/api/research/experiments",
    page1([
      {
        experiment_id: "exp_0001",
        strategy_id: "ichimoku_kumo_trend",
        actor: "joe",
        actor_kind: "human",
        outcome: "rejected",
        created_at: "2026-09-18T10:00:00Z",
        hypothesis: "baseline",
        dataset_version_id: "dsv_eurusd_h1_0007abcdef",
        verdict: "reject",
      },
    ]),
  );
  await json(
    "/api/research/validation",
    page1([
      {
        strategy_id: "ichimoku_kumo_trend",
        verdict: "reject",
        report_version: "1",
        dataset_version_id: "dsv_eurusd_h1_0007abcdef",
        gate_set_version: "gates_v3",
        rungs_passed: figure(2, "walkforward", "count"),
        rungs_total: figure(6, "walkforward", "count"),
        binding_constraint: "DSR 0.41 below 0.95",
        provenance_labels: {},
        available: true,
        detail: "Died at deflation.",
      },
      {
        strategy_id: "donchian_breakout_atr",
        verdict: "not_validated",
        report_version: "",
        dataset_version_id: "",
        gate_set_version: "",
        rungs_passed: figure(null, "walkforward", "count"),
        rungs_total: figure(null, "walkforward", "count"),
        binding_constraint: "",
        provenance_labels: {},
        available: false,
        detail: "No validation report.",
      },
    ]),
  );
  await json(
    "/api/research/datasets",
    page1([
      {
        version_id: "dsv_eurusd_h1_0007abcdef0123456789",
        short_id: "dsv_eurusd_h1",
        describe: "EURUSD H1 mid bars, 2016-2026.",
      },
    ]),
  );
  await page.route(
    (url) => url.pathname.startsWith("/api/research/parameter-lab/"),
    (route: Route) =>
      route.fulfill({
        json: env({
          strategy_id: "ichimoku_kumo_trend",
          parameters: [
            {
              name: "tenkan",
              current: figure(9, "backtest", "count"),
              minimum: figure(5, "backtest", "count"),
              maximum: figure(20, "backtest", "count"),
              step: figure(1, "backtest", "count"),
              kind: "int",
              description: "Conversion line period.",
            },
          ],
          search_space_size: figure(16, "backtest", "count"),
          deflation_warning: "16 combinations: every later Sharpe is deflated against 16 trials.",
        }),
      }),
  );

  // Intelligence
  await json(
    "/api/intelligence/agents",
    page1([
      { role: "researcher", purpose: "Proposes experiments.", capabilities: ["read_research"], can_execute: false },
    ]),
  );
  await json("/api/intelligence/runs", page1([{ run_id: "run_0001", status: "completed" }]));
  await json(
    "/api/intelligence/research-memory",
    env({
      available: true,
      detail: "Structural memory attached.",
      structures_recorded: figure(42, "backtest", "count"),
      rediscovery_rate: figure(12.5, "backtest", "pct"),
    }),
  );

  // System
  const service = (name: string, kind: string, healthy: boolean) => ({
    name,
    kind,
    detail: `${name} probed.`,
    healthy,
    latency: figure(3, "paper", "ms"),
  });
  await json("/api/system/services", page1([service("database", "live", true), service("news", "seed", true)]));
  await json("/api/system/data-health", page1([service("bar_store", "live", true), service("calendar", "absent", false)]));
  await json(
    "/api/system/workers",
    page1([
      { name: "paper_worker", state: "running", heartbeat_age: figure(4, "paper", "s"), detail: "Beating." },
      { name: "research_worker", state: "never_started", heartbeat_age: figure(null, "paper", "s"), detail: "Never started." },
    ]),
  );
  await json(
    "/api/system/broker-health",
    env({
      configured: false,
      controls: { live_execution_compiled_in: false, mode_is_live: false },
      detail: "Paper mode: no venue is contacted.",
      guard_allowed: false,
      mode: "paper",
      reasons: ["LIVE_EXECUTION_COMPILED_IN is False."],
      venue_url_host: "",
    }),
  );
  await json(
    "/api/system/settings",
    env({
      execution_mode: "paper",
      live_execution_compiled_in: false,
      allowed_origin_count: 1,
      cookie_secure: true,
      cookie_samesite: "lax",
      session_ttl_seconds: 43200,
      limits_version: "limits_v1_paper",
      limits: { max_daily_loss_pct: 2, max_drawdown_pct: 10 },
      realism_models: { slippage: "zero", spread: "static" },
      data_root_configured: true,
      experiment_db_configured: true,
      build_sha: "deadbeef",
    }),
  );
}
