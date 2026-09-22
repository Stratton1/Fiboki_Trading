import { test, BrowserContext } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";

/**
 * Fiboki Screenshot Capture
 *
 * Captures a screenshot of every stable frontend page for visual review.
 *
 * Approach:
 *   - Sets the fiboki_auth marker cookie to bypass Next.js middleware
 *   - Mocks /auth/me to return a fake session (prevents 401 redirect)
 *   - Other API calls return plausible empty/stub responses so pages
 *     render their full shell with "no data" states rather than crashing
 *
 * What screenshots show:
 *   - Authenticated page shells with sidebar, headers, and layout
 *   - Stub data states (mocked instruments/strategies/health)
 *   - NOT real trading data or live backend responses
 *
 * Run:
 *   npm run screenshots             # against localhost:3000
 *   npm run screenshots:prod        # against https://fiboki.uk
 */

const SCREENSHOT_DIR = path.join(__dirname, "..", "screenshots");

const PAGES: { name: string; path: string }[] = [
  { name: "login", path: "/login" },
  { name: "dashboard", path: "/" },
  { name: "charts", path: "/charts" },
  { name: "backtests", path: "/backtests" },
  { name: "research", path: "/research" },
  { name: "scenarios", path: "/scenarios" },
  { name: "jobs", path: "/jobs" },
  { name: "bots", path: "/bots" },
  { name: "exposure", path: "/exposure" },
  { name: "trades", path: "/trades" },
  { name: "alerts", path: "/alerts" },
  { name: "settings", path: "/settings" },
  { name: "system", path: "/system" },
];

/** Set marker cookie and mock API responses so pages render shells. */
async function setupAuthContext(context: BrowserContext) {
  const baseURL = process.env.BASE_URL || "http://localhost:3000";
  const url = new URL(baseURL);

  // Marker cookie for Next.js middleware
  await context.addCookies([
    { name: "fiboki_auth", value: "1", domain: url.hostname, path: "/" },
  ]);

  // Route handler that matches URL patterns and returns correct response shapes.
  // Playwright routes are checked in registration order — first match wins.
  await context.route("**/api/v1/**", (route) => {
    const url = route.request().url();
    const respond = (body: unknown) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });

    // Auth
    if (url.includes("/auth/me"))
      return respond({ user_id: 1, username: "operator", role: "admin" });
    if (url.includes("/auth/logout"))
      return respond({ detail: "Logged out" });

    // System
    if (url.includes("/system/health"))
      return respond({ status: "ok", version: "1.0.0" });
    if (url.includes("/system/status"))
      return respond({
        api_version: "1.0.0",
        database: "connected",
        paper_engine: "standby",
        strategies_loaded: 12,
      });

    // Instruments (array)
    if (url.includes("/instruments"))
      return respond([
        { symbol: "EURUSD", name: "Euro / US Dollar", asset_class: "forex_major", has_canonical_data: true },
        { symbol: "GBPUSD", name: "British Pound / US Dollar", asset_class: "forex_major", has_canonical_data: true },
        { symbol: "USDJPY", name: "US Dollar / Japanese Yen", asset_class: "forex_major", has_canonical_data: true },
      ]);

    // Strategies (array — keys must match what pages destructure)
    if (url.includes("/strategies"))
      return respond([
        { strategy_id: "bot01_sanyaku", strategy_name: "Sanyaku", family: "ichimoku", complexity: "basic", supports_long: true, supports_short: true },
        { strategy_id: "bot02_kijun_pullback", strategy_name: "Kijun Pullback", family: "ichimoku", complexity: "basic", supports_long: true, supports_short: true },
      ]);

    // Market data (charts page expects { candles, ichimoku })
    if (url.includes("/market-data/"))
      return respond({ instrument: "EURUSD", timeframe: "H1", candles: [], ichimoku: [] });

    // Paper trading account (object)
    if (url.includes("/paper/account"))
      return respond({
        balance: 10000, equity: 10000, initial_balance: 10000,
        total_pnl: 0, total_pnl_pct: 0, daily_pnl: 0, weekly_pnl: 0,
        open_positions: 0, total_trades: 0,
      });

    // Paper bots (array)
    if (url.includes("/paper/bots"))
      return respond([]);

    // Backtests (array)
    if (url.includes("/backtests"))
      return respond([]);

    // Trades (paginated)
    if (url.includes("/trades"))
      return respond({ items: [], total: 0, page: 1, size: 50 });

    // Research (array)
    if (url.includes("/research"))
      return respond([]);

    // Exposure
    if (url.includes("/paper/fleet/risk"))
      return respond({
        fleet_limits: { max_bots_per_instrument: 5, max_total_positions: 20, correlation_threshold: 0.85 },
        fleet_status: { open_positions: 0, active_bots: 0 },
        instrument_alerts: [], correlation_alerts: [], underperformers: [],
      });
    if (url.includes("/paper/exposure"))
      return respond({ by_instrument: [], by_asset_class: [], by_direction: [], risk_utilisation: 0 });

    // Jobs
    if (url.includes("/jobs"))
      return respond([]);

    // Scenarios
    if (url.includes("/scenarios"))
      return respond([]);

    // Alerts
    if (url.includes("/alerts"))
      return respond([]);

    // Watchlists
    if (url.includes("/watchlists"))
      return respond([]);

    // Variations
    if (url.includes("/variations"))
      return respond({ items: [], total: 0 });

    // Fallback — safe empty array
    return respond([]);
  });
}

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
});

// ── Login page (unauthenticated — no mocking needed) ──────────────

test("capture login page", async ({ page }) => {
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, "login.png"),
    fullPage: false,
  });
});

// ── Authenticated pages (mocked session) ──────────────────────────

test.describe("authenticated pages", () => {
  test.beforeEach(async ({ context }) => {
    await setupAuthContext(context);
  });

  for (const { name, path: route } of PAGES.filter((p) => p.name !== "login")) {
    test(`capture ${name}`, async ({ page }) => {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      // Extra settle time for client-side hydration
      await page.waitForTimeout(800);
      await page.screenshot({
        path: path.join(SCREENSHOT_DIR, `${name}.png`),
        fullPage: false,
      });
    });
  }
});
