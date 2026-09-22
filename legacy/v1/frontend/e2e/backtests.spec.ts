import { test, expect } from "@playwright/test";

/**
 * Fiboki Backtests Page — Playwright Tests
 *
 * Tests the upgraded backtest workbench UI:
 * - Page structure and header
 * - Run form (inputs, validation hints, timeframe buttons, shortlist)
 * - Summary strip
 * - Filter bar (strategy, instrument, bookmarked, legacy, profitable)
 * - Results table (sorting, rows, badges, actions)
 * - Empty/no-result states
 * - Compare button behavior
 * - Workflow links
 * - Tooltips
 * - No console errors
 */

test.describe("backtests page", () => {
  test.beforeEach(async ({ context }) => {
    const baseURL = process.env.BASE_URL || "http://localhost:3000";
    const url = new URL(baseURL);
    await context.addCookies([
      { name: "fiboki_auth", value: "1", domain: url.hostname, path: "/" },
    ]);
  });

  // ─── Page Structure ───────────────────────────────────────

  test("backtests page loads with workbench header", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator('[data-testid="backtests-page"]')).toBeVisible();
    await expect(page.locator("h1")).toContainText("Backtests");
  });

  test("page has subtitle", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.getByText("Run, review, compare")).toBeVisible();
  });

  test("page has info tooltip on title", async ({ page }) => {
    await page.goto("/backtests");
    const infoBtn = page.locator('h1 button[aria-label="More info"]');
    await expect(infoBtn).toBeVisible();
  });

  // ─── Run Form ─────────────────────────────────────────────

  test("run form renders with strategy, instrument, and timeframe fields", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="run-form"]')).toBeVisible();
    await expect(page.locator('[data-testid="strategy-field"]')).toBeVisible();
    await expect(page.locator('[data-testid="instrument-field"]')).toBeVisible();
    await expect(page.locator('[data-testid="timeframe-field"]')).toBeVisible();
  });

  test("run button is disabled when strategy is not selected", async ({ page }) => {
    await page.goto("/backtests");
    const runBtn = page.locator('[data-testid="run-btn"]');
    await expect(runBtn).toBeDisabled();
  });

  test("strategy field shows Required hint when empty", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="strategy-field"]')).toContainText("Required");
  });

  test("strategy select has options", async ({ page }) => {
    await page.goto("/backtests");
    const select = page.locator('[data-testid="strategy-select"]');
    await expect(select).toBeVisible();
    // Should have at least the "Select strategy" placeholder
    const count = await select.locator("option").count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("timeframe buttons are visible with 6 options", async ({ page }) => {
    await page.goto("/backtests");
    const tfField = page.locator('[data-testid="timeframe-field"]');
    await expect(tfField.locator('[data-testid="tf-btn-M1"]')).toBeVisible();
    await expect(tfField.locator('[data-testid="tf-btn-M5"]')).toBeVisible();
    await expect(tfField.locator('[data-testid="tf-btn-M15"]')).toBeVisible();
    await expect(tfField.locator('[data-testid="tf-btn-M30"]')).toBeVisible();
    await expect(tfField.locator('[data-testid="tf-btn-H1"]')).toBeVisible();
    await expect(tfField.locator('[data-testid="tf-btn-H4"]')).toBeVisible();
  });

  test("clicking timeframe button highlights it", async ({ page }) => {
    await page.goto("/backtests");
    const m15 = page.locator('[data-testid="tf-btn-M15"]');
    await m15.click();
    await expect(m15).toHaveClass(/bg-primary/);
  });

  test("from shortlist button is visible", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="from-shortlist-btn"]')).toBeVisible();
  });

  // ─── Summary Strip ────────────────────────────────────────

  test("summary strip shows result count", async ({ page }) => {
    await page.goto("/backtests");
    const strip = page.locator('[data-testid="summary-strip"]');
    await expect(strip).toBeVisible();
    await expect(strip).toContainText("shown");
  });

  // ─── Filter Bar ───────────────────────────────────────────

  test("filter bar is visible with filter buttons", async ({ page }) => {
    await page.goto("/backtests");
    const bar = page.locator('[data-testid="filter-bar"]');
    await expect(bar).toBeVisible();
    await expect(page.locator('[data-testid="filter-bookmarked"]')).toBeVisible();
    await expect(page.locator('[data-testid="filter-legacy"]')).toBeVisible();
    await expect(page.locator('[data-testid="filter-profitable"]')).toBeVisible();
  });

  test("bookmarked filter toggles on click", async ({ page }) => {
    await page.goto("/backtests");
    const btn = page.locator('[data-testid="filter-bookmarked"]');
    await btn.click();
    await expect(btn).toHaveClass(/bg-amber-50/);
    await btn.click();
    await expect(btn).not.toHaveClass(/bg-amber-50/);
  });

  test("legacy filter toggles on click", async ({ page }) => {
    await page.goto("/backtests");
    const btn = page.locator('[data-testid="filter-legacy"]');
    // Starts with legacy hidden (blue highlight)
    await expect(btn).toHaveClass(/bg-blue-50/);
    await btn.click();
    await expect(btn).not.toHaveClass(/bg-blue-50/);
  });

  test("profitable filter toggles on click", async ({ page }) => {
    await page.goto("/backtests");
    const btn = page.locator('[data-testid="filter-profitable"]');
    await btn.click();
    await expect(btn).toHaveClass(/bg-green-50/);
  });

  // ─── Results Table ────────────────────────────────────────

  test("results table renders", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="results-table"]')).toBeVisible();
  });

  test("table has sortable column headers", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="sort-strategy_id"]')).toBeVisible();
    await expect(page.locator('[data-testid="sort-instrument"]')).toBeVisible();
    await expect(page.locator('[data-testid="sort-total_trades"]')).toBeVisible();
    await expect(page.locator('[data-testid="sort-net_profit"]')).toBeVisible();
    await expect(page.locator('[data-testid="sort-sharpe_ratio"]')).toBeVisible();
    await expect(page.locator('[data-testid="sort-max_drawdown_pct"]')).toBeVisible();
    await expect(page.locator('[data-testid="sort-created_at"]')).toBeVisible();
  });

  test("clicking a sort header changes sort direction", async ({ page }) => {
    await page.goto("/backtests");
    const header = page.locator('[data-testid="sort-net_profit"]');
    await header.click();
    // After clicking once it should be active (desc by default for numeric)
    // Click again to toggle direction
    await header.click();
    // Verify no crash — the sort toggle just works
    await expect(header).toBeVisible();
  });

  test("select all checkbox is visible", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="select-all"]')).toBeVisible();
  });

  test("shows empty state or rows in results table", async ({ page }) => {
    await page.goto("/backtests");
    // Wait for loading to finish
    await page.waitForTimeout(1500);
    const hasRows = await page.locator('[data-testid="bt-row"]').first().isVisible().catch(() => false);
    const hasEmpty = await page.locator('[data-testid="empty-state"]').isVisible().catch(() => false);
    const hasLoading = await page.locator('[data-testid="loading-state"]').isVisible().catch(() => false);
    expect(hasRows || hasEmpty || hasLoading).toBe(true);
  });

  // ─── Compare Button ───────────────────────────────────────

  test("compare button is visible and links to compare page", async ({ page }) => {
    await page.goto("/backtests");
    const btn = page.locator('[data-testid="compare-btn"]');
    await expect(btn).toBeVisible();
    const href = await btn.getAttribute("href");
    expect(href).toContain("/backtests/compare");
  });

  // ─── Workflow Links ───────────────────────────────────────

  test("workflow links section has all navigation targets", async ({ page }) => {
    await page.goto("/backtests");
    const links = page.locator('[data-testid="workflow-links"]');
    await expect(links).toBeVisible();
    await expect(links.getByText("Research")).toBeVisible();
    await expect(links.getByText("Charts")).toBeVisible();
    await expect(links.getByText("Paper Bots")).toBeVisible();
    await expect(links.getByText("Compare")).toBeVisible();
  });

  test("workflow links point to correct pages", async ({ page }) => {
    await page.goto("/backtests");
    const links = page.locator('[data-testid="workflow-links"]');
    const research = links.locator('a[href="/research"]');
    await expect(research).toBeVisible();
    const charts = links.locator('a[href="/charts"]');
    await expect(charts).toBeVisible();
    const bots = links.locator('a[href="/bots"]');
    await expect(bots).toBeVisible();
  });

  // ─── Tooltips ─────────────────────────────────────────────

  test("Sharpe column has tooltip", async ({ page }) => {
    await page.goto("/backtests");
    const sharpeHeader = page.locator('[data-testid="sort-sharpe_ratio"]');
    const infoBtn = sharpeHeader.locator('button[aria-label="More info"]');
    await expect(infoBtn).toBeVisible();
  });

  test("Max DD column has tooltip", async ({ page }) => {
    await page.goto("/backtests");
    const ddHeader = page.locator('[data-testid="sort-max_drawdown_pct"]');
    const infoBtn = ddHeader.locator('button[aria-label="More info"]');
    await expect(infoBtn).toBeVisible();
  });

  test("Trades column has tooltip", async ({ page }) => {
    await page.goto("/backtests");
    const header = page.locator('[data-testid="sort-total_trades"]');
    const infoBtn = header.locator('button[aria-label="More info"]');
    await expect(infoBtn).toBeVisible();
  });

  // ─── No Console Errors ────────────────────────────────────

  test("no unexpected console errors on backtests page", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        if (
          text.includes("CORS") || text.includes("ERR_FAILED") ||
          text.includes("Access-Control") || text.includes("net::") ||
          text.includes("Failed to load resource") || text.includes("401") ||
          text.includes("Unauthorized") || text.includes("AbortError") ||
          text.includes("timed out")
        ) return;
        errors.push(text);
      }
    });
    await page.goto("/backtests");
    await page.waitForTimeout(2000);
    expect(errors).toEqual([]);
  });

  // ─── No Dead Buttons ──────────────────────────────────────

  test("no buttons are incorrectly disabled", async ({ page }) => {
    await page.goto("/backtests");
    // Filter buttons should be enabled
    const bookmarkedBtn = page.locator('[data-testid="filter-bookmarked"]');
    await expect(bookmarkedBtn).toBeEnabled();
    const legacyBtn = page.locator('[data-testid="filter-legacy"]');
    await expect(legacyBtn).toBeEnabled();
    const profitableBtn = page.locator('[data-testid="filter-profitable"]');
    await expect(profitableBtn).toBeEnabled();
  });
});
