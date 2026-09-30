import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import {
  API,
  attentionItem,
  attentionPage,
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
import { allRoutes } from "./routes";

/**
 * Inventory walkthrough (docs/v2/WORKSTATION_INVENTORY.md).
 *
 * For every page route on disk, against a fully mocked API (every endpoint a
 * page reads is answered here with a happy-path payload, so each page renders
 * its SUCCESS state, not the error state the a11y and CSP sweeps see for the
 * pages whose API the shared fixtures do not mock):
 *
 *  - the page is not blank (an h1 inside <main>, and text in <main>);
 *  - no console error, no page error, no failed request, no API call that
 *    this file did not expect (an unexpected call answers 599 and is listed);
 *  - every popover, menu, select, sheet and dialog trigger in the page, the
 *    page-header actions and the status bar is opened and closed with Escape;
 *    tabs are visited; <details> disclosures are opened and closed;
 *  - every control that would MUTATE (arm, disarm, promote, acknowledge) is
 *    opened and CANCELLED; any non-GET request to the API fails the test;
 *  - ⇧K opens the kill-switch dialog and is cancelled;
 *  - the palette's actions are listed (and "Open by id" for a sample id);
 *  - all nine g-chords are pressed and must land on their section;
 *  - a dark screenshot at 1440x900 and at 390x844 goes to
 *    test-results/inventory/<slug>-<w>x<h>.png, and the per-route record to
 *    test-results/inventory/findings/<slug>.json.
 *
 * Soft assertions are used for controls, so one run reports every finding on
 * a route rather than stopping at the first.
 */

const OUT = join(__dirname, "..", "..", "test-results", "inventory");
const CHORDS: ReadonlyArray<readonly [string, string]> = [
  ["c", "/"],
  ["f", "/trading/portfolio"],
  ["l", "/trading/candidates"],
  ["r", "/research"],
  ["m", "/markets"],
  ["x", "/trading/risk"],
  ["d", "/markets/data-quality"],
  ["s", "/system/services"],
  ["j", "/trading/execution"],
];
const POPUP =
  '[role="dialog"]:not([data-ending-style]), [role="alertdialog"]:not([data-ending-style]), [role="menu"]:not([data-ending-style]), [role="listbox"]:not([data-ending-style])';

test.use({ colorScheme: "dark", viewport: { width: 1440, height: 900 } });

function slug(route: string): string {
  return route === "/" ? "index" : route.slice(1).replace(/[/[\]?=&]/g, "_");
}

// ---------------------------------------------------------------- the API

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

const STRATEGIES = [
  strategy(
    "ichimoku_kumo_trend",
    "Ichimoku kumo trend",
    "trend",
    "Price closing outside the cloud continues; Deng, Sakurai and Ueda (2021) found no significant profitability after data snooping.",
  ),
  strategy("donchian_breakout_atr", "Donchian breakout ATR", "breakout", "Channel breakouts with ATR stops."),
];

/** Every endpoint the workstation reads, answered with a happy-path payload. */
async function mockEverything(page: Page, unexpected: string[]) {
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

// -------------------------------------------------------- the walkthrough

interface Record_ {
  route: string;
  heading: string | null;
  mainTextLength: number;
  popups: { trigger: string; opened: boolean; closed: boolean; note?: string }[];
  confirmDialogs: { trigger: string; opened: boolean; cancelled: boolean; note?: string }[];
  disclosures: { summary: string; toggled: boolean }[];
  tabs: string[];
  killSwitchShortcut: string;
  palette: { groups: { heading: string; items: string[] }[]; byId: string[] } | null;
  chords: { key: string; expected: string; landed: string }[];
  consoleErrors: string[];
  failedRequests: string[];
  httpErrors: string[];
  unexpectedApiCalls: string[];
  mutations: string[];
}

async function nameOf(el: Locator): Promise<string> {
  return el
    .evaluate((node) => {
      const e = node as HTMLElement;
      const id = e.getAttribute("data-testid");
      const label = e.getAttribute("aria-label");
      const text = (e.innerText || e.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60);
      return [id ? `[${id}]` : "", label ?? text].filter(Boolean).join(" ");
    })
    .catch(() => "(detached)");
}

async function popupCount(page: Page): Promise<number> {
  return page.locator(POPUP).filter({ visible: true }).count();
}

async function waitPopups(page: Page, want: (n: number) => boolean, ms = 2_500): Promise<boolean> {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (want(await popupCount(page))) return true;
    await page.waitForTimeout(80);
  }
  return want(await popupCount(page));
}

/** Wait out exit transitions, so the next click is not made while one runs. */
async function transitionsDone(page: Page) {
  await expect
    .poll(() => page.locator("[data-ending-style]").count(), { timeout: 2_000 })
    .toBe(0)
    .catch(() => undefined);
}

async function settle(page: Page) {
  await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
  await page
    .locator('[data-testid="state-loading"]')
    .first()
    .waitFor({ state: "detached", timeout: 5_000 })
    .catch(() => undefined);
  // Lazily loaded pieces (the DataGrid chunk, the price chart engine) must be
  // mounted before their controls are counted.
  for (const pending of ["grid-pending", "price-chart-pending", "chart-pending", "chart-data-pending"]) {
    await page
      .getByTestId(pending)
      .first()
      .waitFor({ state: "detached", timeout: 8_000 })
      .catch(() => undefined);
  }
  await page.evaluate(() => document.fonts.ready);
}

/** Open and Escape every popup trigger inside `scope` (skips disabled ones). */
async function walkPopups(page: Page, scope: string, rec: Record_, seen: Set<string>) {
  const triggers = page.locator(`${scope} [aria-haspopup]`);
  const n = await triggers.count();
  for (let i = 0; i < Math.min(n, 30); i += 1) {
    const trigger = triggers.nth(i);
    if (!(await trigger.isVisible().catch(() => false))) continue;
    const name = await nameOf(trigger);
    const key = `${scope}|${name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if ((await trigger.isDisabled().catch(() => false)) || (await trigger.getAttribute("aria-disabled")) === "true") {
      rec.popups.push({ trigger: name, opened: false, closed: true, note: "disabled" });
      continue;
    }
    await trigger.click();
    // Opened: a popup is visible, or the trigger says it is expanded (a
    // Base UI Select's listbox can be mid-transition when first counted).
    const opened =
      (await waitPopups(page, (c) => c > 0)) || (await trigger.getAttribute("aria-expanded")) === "true";
    await page.keyboard.press("Escape");
    const closed =
      (await waitPopups(page, (c) => c === 0)) && (await trigger.getAttribute("aria-expanded")) !== "true";
    await transitionsDone(page);
    rec.popups.push({ trigger: name, opened, closed });
    expect.soft(opened, `${rec.route}: "${name}" opened nothing`).toBe(true);
    expect.soft(closed, `${rec.route}: "${name}" did not close on Escape`).toBe(true);
    if (!closed) {
      await page.keyboard.press("Escape");
      await waitPopups(page, (c) => c === 0);
    }
  }
}

/** A button that opens a sheet or dialog without aria-haspopup. */
async function openAndEscape(page: Page, testId: string, rec: Record_) {
  const el = page.getByTestId(testId);
  if (!(await el.first().isVisible().catch(() => false))) return;
  await el.first().click();
  const opened = await waitPopups(page, (c) => c > 0);
  await page.keyboard.press("Escape");
  const closed = await waitPopups(page, (c) => c === 0);
  await transitionsDone(page);
  rec.popups.push({ trigger: `[${testId}]`, opened, closed });
  expect.soft(opened, `${rec.route}: [${testId}] opened nothing`).toBe(true);
  expect.soft(closed, `${rec.route}: [${testId}] did not close on Escape`).toBe(true);
}

/** Open a mutating control's confirm dialog and press Cancel. Never confirms. */
async function openAndCancel(page: Page, trigger: Locator, rec: Record_) {
  const name = await nameOf(trigger);
  if ((await trigger.isDisabled().catch(() => false)) || (await trigger.getAttribute("aria-disabled")) === "true") {
    await trigger.click({ force: true }).catch(() => undefined);
    const opened = (await page.getByTestId("confirm-dialog").count()) > 0;
    rec.confirmDialogs.push({ trigger: name, opened, cancelled: !opened, note: "blocked by design (disabled)" });
    expect.soft(opened, `${rec.route}: a blocked control "${name}" opened a dialog`).toBe(false);
    return;
  }
  await trigger.click();
  const dialog = page.getByTestId("confirm-dialog");
  const opened = await dialog
    .waitFor({ state: "visible", timeout: 3_000 })
    .then(() => true)
    .catch(() => false);
  if (opened) await page.getByTestId("confirm-cancel").click();
  const cancelled = opened
    ? await dialog
        .waitFor({ state: "detached", timeout: 3_000 })
        .then(() => true)
        .catch(() => false)
    : false;
  rec.confirmDialogs.push({ trigger: name, opened, cancelled });
  expect.soft(opened, `${rec.route}: "${name}" did not open its confirm dialog`).toBe(true);
  expect.soft(cancelled, `${rec.route}: "${name}" confirm dialog did not close on Cancel`).toBe(true);
}

async function walkDetails(page: Page, rec: Record_) {
  const summaries = page.locator("main details > summary");
  const n = await summaries.count();
  for (let i = 0; i < n; i += 1) {
    const summary = summaries.nth(i);
    if (!(await summary.isVisible().catch(() => false))) continue;
    const details = summary.locator("xpath=..");
    const before = await details.evaluate((d) => (d as HTMLDetailsElement).open);
    await summary.click();
    const after = await details.evaluate((d) => (d as HTMLDetailsElement).open);
    if (after !== before) await summary.click();
    rec.disclosures.push({ summary: await nameOf(summary), toggled: after !== before });
    expect.soft(after !== before, `${rec.route}: disclosure "${await nameOf(summary)}" did not toggle`).toBe(true);
  }
}

async function walkPalette(page: Page, rec: Record_) {
  await page.keyboard.press("ControlOrMeta+KeyK");
  const palette = page.getByTestId("command-palette");
  const opened = await palette
    .waitFor({ state: "visible", timeout: 3_000 })
    .then(() => true)
    .catch(() => false);
  expect.soft(opened, `${rec.route}: ⌘K did not open the palette`).toBe(true);
  if (!opened) return;
  const groups = await palette.locator("[cmdk-group]").evaluateAll((nodes) =>
    nodes.map((g) => ({
      heading: (g.querySelector("[cmdk-group-heading]")?.textContent ?? "").trim(),
      items: Array.from(g.querySelectorAll("[cmdk-item]")).map((i) =>
        (i.textContent ?? "").trim().replace(/\s+/g, " "),
      ),
    })),
  );
  await page.getByTestId("palette-input").fill("EURUSD");
  const byId = await palette
    .locator('[data-testid^="palette-open-"]')
    .evaluateAll((nodes) => nodes.map((n) => (n.textContent ?? "").trim()));
  rec.palette = { groups, byId };
  await page.keyboard.press("Escape");
  await expect.soft(palette, `${rec.route}: the palette did not close on Escape`).toHaveCount(0);
}

async function walkChords(page: Page, route: string, rec: Record_) {
  for (const [key, expected] of CHORDS) {
    await page.locator("body").click({ position: { x: 1, y: 1 }, force: true }).catch(() => undefined);
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await page.keyboard.press("g");
    await page.keyboard.press(key);
    const landed = await expect
      .poll(() => new URL(page.url()).pathname, { timeout: 4_000 })
      .toBe(expected)
      .then(() => expected)
      .catch(() => new URL(page.url()).pathname);
    rec.chords.push({ key, expected, landed });
    expect.soft(landed, `${route}: g ${key} should reach ${expected}`).toBe(expected);
  }
}

for (const route of allRoutes()) {
  test(`inventory: ${route}`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "one walk, on the desktop project; phone screenshots are taken inside");
    test.setTimeout(240_000);

    const rec: Record_ = {
      route,
      heading: null,
      mainTextLength: 0,
      popups: [],
      confirmDialogs: [],
      disclosures: [],
      tabs: [],
      killSwitchShortcut: "not tried",
      palette: null,
      chords: [],
      consoleErrors: [],
      failedRequests: [],
      httpErrors: [],
      unexpectedApiCalls: [],
      mutations: [],
    };
    let recording = true;
    page.on("console", (msg) => {
      if (recording && msg.type() === "error") rec.consoleErrors.push(msg.text().slice(0, 300));
    });
    page.on("pageerror", (err) => {
      if (recording) rec.consoleErrors.push(`pageerror: ${err.message.slice(0, 300)}`);
    });
    page.on("requestfailed", (req) => {
      const failure = req.failure()?.errorText ?? "";
      // Aborted = cancelled by a navigation or the held stream; not a failure.
      if (recording && !failure.includes("ERR_ABORTED") && !req.url().includes("/api/stream")) {
        rec.failedRequests.push(`${req.method()} ${req.url()} ${failure}`);
      }
    });
    page.on("response", (res) => {
      if (recording && res.status() >= 400 && !res.url().includes("/api/stream")) {
        rec.httpErrors.push(`${res.status()} ${res.request().method()} ${res.url()}`);
      }
    });
    page.on("request", (req) => {
      if (req.method() !== "GET" && req.url().startsWith(API)) rec.mutations.push(`${req.method()} ${req.url()}`);
    });

    await mockEverything(page, rec.unexpectedApiCalls);
    await page.goto(route);
    await settle(page);

    // Not blank: a heading and text in <main>.
    const main = page.locator("main#main");
    await expect(main).toBeVisible();
    const h1 = main.locator("h1").first();
    await expect(h1, `${route}: no h1 in <main>`).toBeVisible();
    rec.heading = (await h1.innerText()).trim();
    rec.mainTextLength = (await main.innerText()).trim().length;
    expect(rec.mainTextLength, `${route}: <main> is blank`).toBeGreaterThan(40);

    const seen = new Set<string>();
    const signingIn = route === "/login";

    // Tabs (Legend, and the chart's data tables once opened).
    if (route.startsWith("/markets/") && (await page.getByTestId("chart-view-data").isVisible().catch(() => false))) {
      await page.getByTestId("chart-view-data").click();
      await expect(page.getByTestId("chart-data-panel")).toBeVisible();
    }
    const tabs = main.locator('[role="tab"]');
    const tabCount = await tabs.count();
    if (tabCount > 0) {
      for (let i = 0; i < tabCount; i += 1) {
        const tab = tabs.nth(i);
        rec.tabs.push(await nameOf(tab));
        await tab.click();
        await page.waitForTimeout(300);
        if ((await tab.getAttribute("data-testid")) === "legend-tab-controls") {
          await expect(page.getByTestId("legend-controls")).toBeVisible();
        }
        await settle(page);
        await walkPopups(page, "main", rec, seen);
      }
    } else {
      await walkPopups(page, "main", rec, seen);
    }
    await walkDetails(page, rec);

    if (!signingIn) {
      await walkPopups(page, '[data-testid="page-actions"]', rec, seen);
      await walkPopups(page, '[data-testid="status-bar"]', rec, seen);
      await openAndEscape(page, "status-api", rec);
      await openAndEscape(page, "status-as-of", rec);
      if (route === "/system/legend") {
        await page.getByTestId("legend-tab-controls").click();
        await openAndEscape(page, "legend-open-inspector", rec);
      }

      // Mutating controls: opened and cancelled, never confirmed.
      for (const id of ["kill-switch-arm", "kill-switch-disarm"]) {
        const el = page.getByTestId(id);
        if (await el.isVisible().catch(() => false)) await openAndCancel(page, el, rec);
      }
      const promotes = page.locator('[data-testid^="promote-"]');
      for (let i = 0; i < (await promotes.count()); i += 1) await openAndCancel(page, promotes.nth(i), rec);
      const incidentAcks = page.locator('[data-testid^="incident-ack-"]');
      for (let i = 0; i < (await incidentAcks.count()); i += 1) await openAndCancel(page, incidentAcks.nth(i), rec);
      // Acknowledge from the attention queue and from the incident page: the
      // same shared confirm dialog as the Incidents table (inventory F-3).
      const attentionAcks = page.locator('button[data-testid^="attention-ack-"]');
      for (let i = 0; i < (await attentionAcks.count()); i += 1) await openAndCancel(page, attentionAcks.nth(i), rec);
      const detailAck = page.getByTestId("incident-detail-ack");
      if (await detailAck.isVisible().catch(() => false)) await openAndCancel(page, detailAck, rec);

      // ⇧K: opens the kill-switch dialog; cancelled.
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
      await page.keyboard.press("Shift+KeyK");
      const ks = page.getByTestId("confirm-dialog");
      const ksOpen = await ks
        .waitFor({ state: "visible", timeout: 3_000 })
        .then(() => true)
        .catch(() => false);
      if (ksOpen) {
        await page.getByTestId("confirm-cancel").click();
        await ks.waitFor({ state: "detached", timeout: 3_000 }).catch(() => undefined);
      }
      rec.killSwitchShortcut = ksOpen ? "opened and cancelled" : "did not open";
      expect.soft(ksOpen, `${route}: ⇧K did not open the kill-switch dialog`).toBe(true);

      await walkPalette(page, rec);
    }

    expect(rec.mutations, `${route}: a mutating request was sent`).toEqual([]);

    // Screenshot, desktop, back at the top of the page with nothing open.
    mkdirSync(join(OUT, "findings"), { recursive: true });
    await page.goto(route);
    await settle(page);
    await page.screenshot({ path: join(OUT, `${slug(route)}-1440x900.png`), fullPage: true });

    // Everything above ran on the page's own life; chords navigate away.
    recording = false;
    if (!signingIn) await walkChords(page, route, rec);

    // Phone.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(route);
    await settle(page);
    await page.screenshot({ path: join(OUT, `${slug(route)}-390x844.png`), fullPage: true });

    writeFileSync(join(OUT, "findings", `${slug(route)}.json`), JSON.stringify(rec, null, 2));

    expect.soft(rec.unexpectedApiCalls, `${route}: API calls the walkthrough did not mock`).toEqual([]);
    expect.soft(rec.failedRequests, `${route}: failed requests`).toEqual([]);
    expect.soft(rec.httpErrors, `${route}: HTTP error responses`).toEqual([]);
    expect.soft(rec.consoleErrors, `${route}: console errors`).toEqual([]);
  });
}

/** The disarm (lift the halt) dialog needs an armed switch: opened and cancelled. */
test("inventory: disarm dialog on an armed kill switch", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop project");
  const unexpected: string[] = [];
  const mutations: string[] = [];
  page.on("request", (req) => {
    if (req.method() !== "GET" && req.url().startsWith(API)) mutations.push(`${req.method()} ${req.url()}`);
  });
  await mockEverything(page, unexpected);
  const armed = { active: true, mode: "pause", operator: "joe", reason: "inventory walkthrough", blocks_new_risk: true };
  await page.route(`${API}/api/system/kill-switch`, (route: Route) => route.fulfill({ json: killSwitchView(armed) }));
  await page.route(`${API}/api/system/execution-mode`, (route: Route) =>
    route.fulfill({ json: modeBanner({ kill_switch_active: true, kill_switch_mode: "pause" }) }),
  );
  await page.goto("/trading/risk");
  await settle(page);
  await expect(page.getByTestId("mode-banner-killswitch")).toBeVisible();
  const rec = { route: "/trading/risk (armed)", confirmDialogs: [] } as unknown as Record_;
  await openAndCancel(page, page.getByTestId("kill-switch-disarm"), rec);
  await openAndCancel(page, page.getByTestId("kill-switch-arm"), rec);
  await page.screenshot({ path: join(OUT, "trading_risk_armed-1440x900.png"), fullPage: true });
  mkdirSync(join(OUT, "findings"), { recursive: true });
  writeFileSync(join(OUT, "findings", "_armed.json"), JSON.stringify({ ...rec, unexpected, mutations }, null, 2));
  expect(mutations).toEqual([]);
  expect.soft(unexpected).toEqual([]);
});

/**
 * The backend's attention queue and incident read model emit deep links to
 * entity routes (routers/incidents.py `/system/incidents/{id}`,
 * routers/command.py `/lifecycle/{content_hash}`). Until 2026-09-30 neither
 * page existed and both were 404s (inventory F-1, F-2); each must now reach a
 * page with a heading.
 */
test("backend deep links reach a page", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop project");
  const unexpected: string[] = [];
  await mockEverything(page, unexpected);
  const results: { link: string; status: number | null; heading: string | null }[] = [];
  for (const link of [
    "/system/incidents/inc-1",
    "/lifecycle/abc123def456",
    "/lifecycle/ichimo0123456789abcdef",
    "/risk",
    "/system",
    "/journal",
  ]) {
    const response = await page.goto(link);
    const heading = await page
      .locator("h1")
      .first()
      .innerText({ timeout: 3_000 })
      .catch(() => null);
    results.push({ link, status: response?.status() ?? null, heading });
    expect.soft(response?.status(), `${link} should resolve to a page`).toBeLessThan(400);
    expect.soft(heading ?? "", `${link} should render a heading, not a 404`).not.toMatch(/^(404)?$/);
  }
  mkdirSync(join(OUT, "findings"), { recursive: true });
  writeFileSync(join(OUT, "findings", "_deep-links.json"), JSON.stringify(results, null, 2));
});
