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
 *   FIBOKI_TEST_EMAIL     — production username (fallback: FIBOKI_E2E_USERNAME)
 *   FIBOKI_TEST_PASSWORD  — production password (fallback: FIBOKI_E2E_PASSWORD)
 *   BASE_URL              — target URL (defaults to https://fiboki.uk)
 *
 * Run:
 *   FIBOKI_TEST_EMAIL=x FIBOKI_TEST_PASSWORD=y npm run verify:prod
 *
 * What this verifies:
 *   - Real login flow works end-to-end
 *   - Session persists across page navigation
 *   - Each page renders its header/content without errors
 *   - Charts page can load starter dataset (EURUSD/H1) if available
 *   - System diagnostics are visible
 *   - No unexpected redirects back to /login
 *
 * Screenshots are saved to frontend/screenshots/auth-prod/ and audit/
 */

const SCREENSHOT_DIR = path.join(__dirname, "..", "screenshots", "auth-prod");
const AUDIT_DIR = path.join(__dirname, "..", "..", "audit");
const AUTH_STATE_FILE = path.join(__dirname, "..", ".auth-state.json");

const USERNAME =
  process.env.FIBOKI_TEST_EMAIL || process.env.FIBOKI_E2E_USERNAME;
const PASSWORD =
  process.env.FIBOKI_TEST_PASSWORD || process.env.FIBOKI_E2E_PASSWORD;

// ── Helpers ──────────────────────────────────────────────────────────

async function screenshot(page: Page, authProdName: string, auditName?: string) {
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `${authProdName}.png`),
    fullPage: false,
  });
  if (auditName) {
    await page.screenshot({
      path: path.join(AUDIT_DIR, `${auditName}.png`),
      fullPage: false,
    });
  }
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
      "Missing credentials. Set FIBOKI_TEST_EMAIL and FIBOKI_TEST_PASSWORD env vars.\n" +
        "Run with: FIBOKI_TEST_EMAIL=x FIBOKI_TEST_PASSWORD=y npm run verify:prod"
    );
  }
  // Remove old screenshots so stale files don't persist
  if (fs.existsSync(SCREENSHOT_DIR)) {
    fs.rmSync(SCREENSHOT_DIR, { recursive: true });
  }
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  fs.mkdirSync(AUDIT_DIR, { recursive: true });
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

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await expect(page.locator("h1")).toContainText("Welcome back");
    await expect(page.locator("text=BALANCE").first()).toBeVisible();
    await expect(page.locator("text=EQUITY").first()).toBeVisible();

    await screenshot(page, "02-dashboard", "01-dashboard");
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

    await expect(page.locator("h1")).toContainText("Trading Chart");

    await screenshot(page, "03-charts", "02-charts-loading");
    await context.close();
  });

  test("charts page: EURUSD/H1 starter data check", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/charts");
    await settle(page);

    const body = await page.textContent("body");
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
      console.log(
        "✓ EURUSD/H1 chart: Canvas/KLineChart element detected — data appears loaded"
      );
    } else {
      console.log(
        "? EURUSD/H1 chart: Indeterminate state — no error but no chart canvas found"
      );
    }

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
    await expect(page.locator("text=RUN BACKTEST").first()).toBeVisible();

    await screenshot(page, "05-backtests", "05-backtests");
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

    await screenshot(page, "06-research", "07-research");
    await context.close();
  });

  // ── Scenarios ──────────────────────────────────────────────────

  test("scenarios page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/scenarios");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await screenshot(page, "07-scenarios", "08-scenarios");
    await context.close();
  });

  // ── Jobs ───────────────────────────────────────────────────────

  test("jobs page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/jobs");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await screenshot(page, "08-jobs", "09-jobs");
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
    await expect(page.locator("text=BALANCE").first()).toBeVisible();

    await screenshot(page, "09-bots", "10-paper-bots");
    await context.close();
  });

  // ── Exposure ───────────────────────────────────────────────────

  test("exposure page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/exposure");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await screenshot(page, "10-exposure", "11-exposure");
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

    await screenshot(page, "11-trades", "12-trade-history");
    await context.close();
  });

  // ── Trade Detail (first trade) ─────────────────────────────────

  test("trade detail page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/trades");
    await settle(page);

    // Try to click the first trade row to navigate to detail
    const tradeRow = page.locator("table tbody tr").first();
    const hasRows = (await tradeRow.count()) > 0;

    if (hasRows) {
      await tradeRow.click();
      await settle(page);
      await assertNoCrash(page);
      await screenshot(page, "12-trade-detail", "12b-trade-detail");
    } else {
      console.log("⚠ No trade rows found — skipping trade detail screenshot");
      // Capture the empty trades page as the detail screenshot
      await screenshot(page, "12-trade-detail");
    }

    await context.close();
  });

  // ── Alerts ─────────────────────────────────────────────────────

  test("alerts page loads", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    await page.goto("/alerts");
    await settle(page);

    expect(page.url()).not.toContain("/login");
    await assertNoCrash(page);

    await screenshot(page, "13-alerts", "13-alerts");
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

    await screenshot(page, "14-settings", "14-settings");
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
    await expect(page.locator("text=BACKEND HEALTH").first()).toBeVisible();
    await expect(page.locator("text=ENGINE STATUS").first()).toBeVisible();
    await expect(page.locator("text=DIAGNOSTICS").first()).toBeVisible();

    const bodyText = await page.textContent("body");
    const healthVisible =
      bodyText?.includes("Healthy") || bodyText?.includes("Unhealthy");
    expect(healthVisible).toBeTruthy();

    await screenshot(page, "15-system", "15-system");
    await context.close();
  });

  // ── Session persistence check ──────────────────────────────────

  test("session persists across navigation", async ({ browser }) => {
    const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const page = await context.newPage();

    const routes = [
      "/",
      "/charts",
      "/backtests",
      "/research",
      "/scenarios",
      "/jobs",
      "/bots",
      "/exposure",
      "/trades",
      "/alerts",
      "/settings",
      "/system",
    ];
    for (const route of routes) {
      await page.goto(route);
      await page.waitForLoadState("domcontentloaded");
      expect(page.url()).not.toContain("/login");
    }
    await context.close();
  });
});
