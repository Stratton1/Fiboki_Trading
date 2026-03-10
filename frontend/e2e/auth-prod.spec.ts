import { test, expect, Page, BrowserContext } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";

/**
 * Fiboki Authenticated Production Verification
 *
 * Logs in via the real login form using credentials from env vars,
 * then verifies every stable page loads correctly with real backend data.
 *
 * Required env vars:
 *   FIBOKI_E2E_USERNAME   — production username
 *   FIBOKI_E2E_PASSWORD   — production password
 *   BASE_URL              — target URL (defaults to https://fiboki.uk)
 *
 * Run:
 *   FIBOKI_E2E_USERNAME=x FIBOKI_E2E_PASSWORD=y npm run verify:prod
 *
 * What this verifies:
 *   - Real login flow works end-to-end
 *   - Session persists across page navigation
 *   - Each page renders its header/content without errors
 *   - Charts page can load starter dataset (EURUSD/H1) if available
 *   - System diagnostics are visible
 *   - No unexpected redirects back to /login
 *
 * Screenshots are saved to frontend/screenshots/auth-prod/
 */

const SCREENSHOT_DIR = path.join(__dirname, "..", "screenshots", "auth-prod");
const AUTH_STATE_FILE = path.join(__dirname, "..", ".auth-state.json");

const USERNAME = process.env.FIBOKI_E2E_USERNAME;
const PASSWORD = process.env.FIBOKI_E2E_PASSWORD;

// ── Helpers ──────────────────────────────────────────────────────────

async function screenshot(page: Page, name: string) {
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `${name}.png`),
    fullPage: false,
  });
}

/** Wait for page hydration and any SWR fetches to settle. */
async function settle(page: Page) {
  await page.waitForLoadState("domcontentloaded");
  // Race networkidle against a 5s ceiling — chart pages have persistent connections
  await Promise.race([
    page.waitForLoadState("networkidle"),
    page.waitForTimeout(5000),
  ]);
  await page.waitForTimeout(1000);
}

/** Check there are no "Application error" crash screens. */
async function assertNoCrash(page: Page) {
  const body = await page.textContent("body");
  expect(body).not.toContain("Application error");
  expect(body).not.toContain("Internal Server Error");
}

// ── Setup ────────────────────────────────────────────────────────────

test.beforeAll(() => {
  if (!USERNAME || !PASSWORD) {
    throw new Error(
      "Missing FIBOKI_E2E_USERNAME and/or FIBOKI_E2E_PASSWORD env vars.\n" +
      "Run with: FIBOKI_E2E_USERNAME=x FIBOKI_E2E_PASSWORD=y npm run verify:prod"
    );
  }
  // Remove old screenshots so stale files don't persist
  if (fs.existsSync(SCREENSHOT_DIR)) {
    fs.rmSync(SCREENSHOT_DIR, { recursive: true });
  }
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
});

test.afterAll(() => {
  // Clean up auth state file
  if (fs.existsSync(AUTH_STATE_FILE)) {
    fs.unlinkSync(AUTH_STATE_FILE);
  }
});

// ── Login ────────────────────────────────────────────────────────────

test.describe.serial("authenticated production verification", () => {
  test("login succeeds", async ({ page, context }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");

    // Fill and submit the login form
    await page.fill("#username", USERNAME!);
    await page.fill("#password", PASSWORD!);
    await page.click('button[type="submit"]');

    // Wait for redirect to dashboard
    await page.waitForURL("**/", { timeout: 15_000 });
    await settle(page);

    // Should be on dashboard, not /login
    expect(page.url()).not.toContain("/login");

    // Dashboard header should be visible
    await expect(page.locator("h1")).toContainText("Welcome back");

    // Save auth state (cookies + localStorage) for subsequent tests
    await context.storageState({ path: AUTH_STATE_FILE });

    await screenshot(page, "01-login-success");
  });

  // ── Dashboard ──────────────────────────────────────────────────

  test("dashboard renders with real data", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/");
    await settle(page);

    // Should not redirect to login
    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    // Check for key dashboard elements
    await expect(page.locator("h1")).toContainText("Welcome back");
    // KPI cards should be present (Balance, Equity, etc.)
    await expect(page.locator("text=BALANCE").first()).toBeVisible();
    await expect(page.locator("text=EQUITY").first()).toBeVisible();

    await screenshot(page, "02-dashboard");
    await context.close();
  });

  // ── Charts ─────────────────────────────────────────────────────

  test("charts page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/charts");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    // Header should be visible
    await expect(page.locator("h1")).toContainText("Trading Chart");

    await screenshot(page, "03-charts");
    await context.close();
  });

  test("charts page: EURUSD/H1 starter data check", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/charts");
    await settle(page);

    // The chart toolbar should show EURUSD by default
    const body = await page.textContent("body");

    // Check whether chart rendered data or shows "no data" / error
    const hasNoData =
      body?.includes("Failed to load market data") ||
      body?.includes("No data") ||
      body?.includes("no data file");
    const hasChartCanvas =
      (await page.locator("canvas").count()) > 0 ||
      (await page.locator('[class*="klinecharts"]').count()) > 0;

    if (hasNoData) {
      console.log("⚠ EURUSD/H1 chart: No production data available yet");
    } else if (hasChartCanvas) {
      console.log("✓ EURUSD/H1 chart: Canvas/KLineChart element detected — data appears loaded");
    } else {
      console.log("? EURUSD/H1 chart: Indeterminate state — no error but no chart canvas found");
    }

    // This test reports status but does not fail — starter data may not be deployed yet
    await screenshot(page, "04-charts-eurusd-h1");
    await context.close();
  });

  // ── Backtests ──────────────────────────────────────────────────

  test("backtests page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/backtests");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await expect(page.locator("h1")).toContainText("Backtests");
    // Form controls should be present
    await expect(page.locator("text=RUN BACKTEST").first()).toBeVisible();

    await screenshot(page, "05-backtests");
    await context.close();
  });

  // ── Research ───────────────────────────────────────────────────

  test("research page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/research");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await expect(page.locator("h1")).toContainText("Research Matrix");

    await screenshot(page, "06-research");
    await context.close();
  });

  // ── Paper Bots ─────────────────────────────────────────────────

  test("bots page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/bots");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await expect(page.locator("h1")).toContainText("Paper Bots");
    // Account summary cards should be visible
    await expect(page.locator("text=BALANCE").first()).toBeVisible();

    await screenshot(page, "07-bots");
    await context.close();
  });

  // ── Trades ─────────────────────────────────────────────────────

  test("trades page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/trades");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await expect(page.locator("h1")).toContainText("Trade History");

    await screenshot(page, "08-trades");
    await context.close();
  });

  // ── Settings ───────────────────────────────────────────────────

  test("settings page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/settings");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await expect(page.locator("h1")).toContainText("Settings");
    // User section should show the logged-in username
    const bodyText = await page.textContent("body");
    expect(bodyText).toContain(USERNAME!);

    await screenshot(page, "09-settings");
    await context.close();
  });

  // ── System ─────────────────────────────────────────────────────

  test("system page loads with diagnostics", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/system");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await expect(page.locator("h1")).toContainText("System");

    // Key diagnostic sections should be visible
    await expect(page.locator("text=BACKEND HEALTH").first()).toBeVisible();
    await expect(page.locator("text=ENGINE STATUS").first()).toBeVisible();
    await expect(page.locator("text=DIAGNOSTICS").first()).toBeVisible();

    // Health indicator should show (either Healthy or Unhealthy)
    const bodyText = await page.textContent("body");
    const healthVisible = bodyText?.includes("Healthy") || bodyText?.includes("Unhealthy");
    expect(healthVisible).toBeTruthy();

    await screenshot(page, "10-system");
    await context.close();
  });

  // ── Session persistence check ──────────────────────────────────

  test("session persists across navigation", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    // Navigate through multiple pages quickly to confirm no auth dropout
    const routes = ["/", "/charts", "/backtests", "/bots", "/trades", "/settings", "/system"];
    for (const route of routes) {
      await page.goto(route);
      await page.waitForLoadState("domcontentloaded");
      expect(page.url()).not.toContain("/login");
    }
    await context.close();
  });
});
