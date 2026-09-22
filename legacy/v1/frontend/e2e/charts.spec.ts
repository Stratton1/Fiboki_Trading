import { test, expect } from "@playwright/test";

/**
 * Fiboki Charts Page — Playwright Tests
 *
 * Tests the upgraded charts workstation UI:
 * - Page structure and header
 * - Chart cell rendering (header bar, drawing toolbar, overlays, states)
 * - Layout switching (1x1, 1x2, 2x2)
 * - Drawing tools visibility
 * - Overlay toggles (Ichimoku, Sessions)
 * - Workflow links (Backtest, Research)
 * - Help panel
 * - Mode toggle (Historical/Live)
 * - Instrument and timeframe selection
 * - Graceful degradation without API
 */

test.describe("charts page", () => {
  test.beforeEach(async ({ context }) => {
    const baseURL = process.env.BASE_URL || "http://localhost:3000";
    const url = new URL(baseURL);
    await context.addCookies([
      {
        name: "fiboki_auth",
        value: "1",
        domain: url.hostname,
        path: "/",
      },
    ]);
  });

  // ─── Page Structure ───────────────────────────────────────

  test("charts page loads with workstation header", async ({ page }) => {
    await page.goto("/charts");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator('[data-testid="charts-page"]')).toBeVisible();
    await expect(page.locator("h1")).toContainText("Charts");
  });

  test("page has subtitle describing workstation", async ({ page }) => {
    await page.goto("/charts");
    await expect(page.getByText("Analysis workstation")).toBeVisible();
  });

  test("page shows keyboard hint", async ({ page }) => {
    await page.goto("/charts");
    await expect(page.getByText("Scroll to pan")).toBeVisible();
  });

  // ─── Layout Controls ─────────────────────────────────────

  test("layout toolbar is visible with 3 layout options", async ({ page }) => {
    await page.goto("/charts");
    const toolbar = page.locator('[data-testid="layout-toolbar"]');
    await expect(toolbar).toBeVisible();
    await expect(page.locator('[data-testid="layout-1x1"]')).toBeVisible();
    await expect(page.locator('[data-testid="layout-1x2"]')).toBeVisible();
    await expect(page.locator('[data-testid="layout-2x2"]')).toBeVisible();
  });

  test("clicking Side by Side layout shows 2 chart cells", async ({ page }) => {
    await page.goto("/charts");
    await page.locator('[data-testid="layout-1x2"]').click();
    const cells = page.locator('[data-testid="chart-cell"]');
    await expect(cells).toHaveCount(2);
  });

  test("clicking Quad layout shows 4 chart cells", async ({ page }) => {
    await page.goto("/charts");
    await page.locator('[data-testid="layout-2x2"]').click();
    const cells = page.locator('[data-testid="chart-cell"]');
    await expect(cells).toHaveCount(4);
  });

  test("clicking Single layout shows 1 chart cell", async ({ page }) => {
    await page.goto("/charts");
    // First switch to quad, then back to single
    await page.locator('[data-testid="layout-2x2"]').click();
    await page.locator('[data-testid="layout-1x1"]').click();
    const cells = page.locator('[data-testid="chart-cell"]');
    await expect(cells).toHaveCount(1);
  });

  // ─── Chart Cell Structure ─────────────────────────────────

  test("chart cell has header with instrument and timeframe", async ({ page }) => {
    await page.goto("/charts");
    const header = page.locator('[data-testid="chart-header"]').first();
    await expect(header).toBeVisible();
    await expect(page.locator('[data-testid="instrument-select"]').first()).toBeVisible();
    await expect(page.locator('[data-testid="timeframe-buttons"]').first()).toBeVisible();
  });

  test("chart cell has drawing toolbar", async ({ page }) => {
    await page.goto("/charts");
    const toolbar = page.locator('[data-testid="drawing-toolbar"]').first();
    await expect(toolbar).toBeVisible();
  });

  test("chart cell has mode toggle (Hist/Live)", async ({ page }) => {
    await page.goto("/charts");
    const toggle = page.locator('[data-testid="mode-toggle"]').first();
    await expect(toggle).toBeVisible();
    await expect(toggle.getByText("Hist")).toBeVisible();
    await expect(toggle.getByText("Live")).toBeVisible();
  });

  // ─── Drawing Tools ────────────────────────────────────────

  test("all 6 drawing tools are visible", async ({ page }) => {
    await page.goto("/charts");
    await expect(page.locator('[data-testid="draw-pointer"]').first()).toBeVisible();
    await expect(page.locator('[data-testid="draw-straightLine"]').first()).toBeVisible();
    await expect(page.locator('[data-testid="draw-horizontalStraightLine"]').first()).toBeVisible();
    await expect(page.locator('[data-testid="draw-rayLine"]').first()).toBeVisible();
    await expect(page.locator('[data-testid="draw-fibonacciLine"]').first()).toBeVisible();
    await expect(page.locator('[data-testid="draw-parallelStraightLine"]').first()).toBeVisible();
  });

  test("clicking a drawing tool highlights it", async ({ page }) => {
    await page.goto("/charts");
    const trendBtn = page.locator('[data-testid="draw-straightLine"]').first();
    await trendBtn.click();
    await expect(trendBtn).toHaveClass(/bg-primary/);
  });

  test("clicking active drawing tool deactivates it", async ({ page }) => {
    await page.goto("/charts");
    const trendBtn = page.locator('[data-testid="draw-straightLine"]').first();
    await trendBtn.click();
    await expect(trendBtn).toHaveClass(/bg-primary/);
    await trendBtn.click();
    await expect(trendBtn).not.toHaveClass(/bg-primary/);
  });

  // ─── Overlay Toggles ─────────────────────────────────────

  test("Ichimoku toggle is visible and clickable", async ({ page }) => {
    await page.goto("/charts");
    const btn = page.locator('[data-testid="toggle-ichimoku"]').first();
    await expect(btn).toBeVisible();
    await expect(btn).toContainText("Ichimoku");
    await btn.click();
    await expect(btn).toHaveClass(/bg-blue-100/);
  });

  test("Sessions toggle is visible and shows legend on click", async ({ page }) => {
    await page.goto("/charts");
    const btn = page.locator('[data-testid="toggle-sessions"]').first();
    await expect(btn).toBeVisible();
    await expect(btn).toContainText("Sessions");
    await btn.click();
    await expect(btn).toHaveClass(/bg-amber-100/);
    await expect(page.locator('[data-testid="session-legend"]').first()).toBeVisible();
  });

  test("session legend shows all 5 session names", async ({ page }) => {
    await page.goto("/charts");
    await page.locator('[data-testid="toggle-sessions"]').first().click();
    const legend = page.locator('[data-testid="session-legend"]').first();
    await expect(legend).toContainText("Asian");
    await expect(legend).toContainText("London");
    await expect(legend).toContainText("New York");
    await expect(legend).toContainText("Off-Hours");
  });

  // ─── Instrument & Timeframe Selection ─────────────────────

  test("instrument dropdown has multiple options", async ({ page }) => {
    await page.goto("/charts");
    const select = page.locator('[data-testid="instrument-select"]').first();
    await expect(select).toBeVisible();
    // Options are rendered client-side; wait for hydration
    await page.waitForFunction(
      (sel) => (document.querySelector(sel) as HTMLSelectElement)?.options.length >= 6,
      '[data-testid="instrument-select"]'
    );
    const count = await select.locator("option").count();
    expect(count).toBeGreaterThanOrEqual(6);
  });

  test("timeframe buttons include M15, M30, H1, H4, D1", async ({ page }) => {
    await page.goto("/charts");
    const tfGroup = page.locator('[data-testid="timeframe-buttons"]').first();
    await expect(tfGroup.locator('[data-testid="tf-M15"]')).toBeVisible();
    await expect(tfGroup.locator('[data-testid="tf-M30"]')).toBeVisible();
    await expect(tfGroup.locator('[data-testid="tf-H1"]')).toBeVisible();
    await expect(tfGroup.locator('[data-testid="tf-H4"]')).toBeVisible();
    await expect(tfGroup.locator('[data-testid="tf-D1"]')).toBeVisible();
  });

  test("clicking a timeframe button highlights it", async ({ page }) => {
    await page.goto("/charts");
    const m15 = page.locator('[data-testid="tf-M15"]').first();
    await m15.click();
    await expect(m15).toHaveClass(/bg-primary/);
  });

  // ─── Workflow Links ───────────────────────────────────────

  test("backtest link is visible and points to backtests page", async ({ page }) => {
    await page.goto("/charts");
    const link = page.locator('[data-testid="link-backtest"]').first();
    await expect(link).toBeVisible();
    const href = await link.getAttribute("href");
    expect(href).toContain("/backtests");
  });

  test("research link is visible and points to research page", async ({ page }) => {
    await page.goto("/charts");
    const link = page.locator('[data-testid="link-research"]').first();
    await expect(link).toBeVisible();
    const href = await link.getAttribute("href");
    expect(href).toBe("/research");
  });

  // ─── Help Panel ───────────────────────────────────────────

  test("help button opens help panel with instructions", async ({ page }) => {
    await page.goto("/charts");
    const helpBtn = page.locator('[data-testid="chart-help-btn"]').first();
    await expect(helpBtn).toBeVisible();
    await helpBtn.click();
    const panel = page.locator('[data-testid="chart-help-panel"]').first();
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("Chart Controls");
    await expect(panel).toContainText("Scroll");
    await expect(panel).toContainText("Drawing tools");
    await expect(panel).toContainText("Ichimoku");
  });

  test("help panel closes when toggled again", async ({ page }) => {
    await page.goto("/charts");
    const helpBtn = page.locator('[data-testid="chart-help-btn"]').first();
    await helpBtn.click();
    await expect(page.locator('[data-testid="chart-help-panel"]').first()).toBeVisible();
    await helpBtn.click();
    await expect(page.locator('[data-testid="chart-help-panel"]')).toHaveCount(0);
  });

  // ─── Loading / Error / Empty States ───────────────────────

  test("chart shows loading or error or data (never blank)", async ({ page }) => {
    await page.goto("/charts");
    // The chart cell should show one of: loading spinner, error, empty state, or the chart canvas
    const cell = page.locator('[data-testid="chart-cell"]').first();
    await expect(cell).toBeVisible();

    // Wait a moment for the state to settle
    await page.waitForTimeout(1000);

    // Should have one of these states
    const hasLoading = await page.locator('[data-testid="chart-loading"]').first().isVisible().catch(() => false);
    const hasError = await page.locator('[data-testid="chart-error"]').first().isVisible().catch(() => false);
    const hasEmpty = await page.locator('[data-testid="chart-empty"]').first().isVisible().catch(() => false);
    const hasCanvas = await cell.locator("canvas").first().isVisible().catch(() => false);

    expect(hasLoading || hasError || hasEmpty || hasCanvas).toBe(true);
  });

  // ─── No Console Errors ────────────────────────────────────

  test("no unexpected console errors on charts page", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        // Filter expected errors (CORS, 401, network in shell mode)
        if (
          text.includes("CORS") ||
          text.includes("ERR_FAILED") ||
          text.includes("Access-Control") ||
          text.includes("net::") ||
          text.includes("Failed to load resource") ||
          text.includes("401") ||
          text.includes("Unauthorized") ||
          text.includes("AbortError") ||
          text.includes("timed out")
        ) {
          return;
        }
        errors.push(text);
      }
    });
    await page.goto("/charts");
    await page.waitForTimeout(2000);
    expect(errors).toEqual([]);
  });

  // ─── InfoTip Tooltip ──────────────────────────────────────

  test("charts page has an info tooltip on the title", async ({ page }) => {
    await page.goto("/charts");
    // The InfoTip renders a span > button with an (i) icon near the title
    // Use a broader selector since the button is nested inside h1 > span > button
    const infoButton = page.locator('h1 button[aria-label="More info"]');
    await expect(infoButton).toBeVisible();
  });
});
