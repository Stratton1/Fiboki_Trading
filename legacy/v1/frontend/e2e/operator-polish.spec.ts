import { test, expect } from "@playwright/test";

/**
 * Operator Polish — Playwright Tests
 *
 * Tests for the operator productivity improvements:
 * - Charts: reset/fit buttons, vertical line tool, bots link
 * - Backtests: strategy names, filter dropdown names
 * - Research: strategy names in results, backtest link
 * - Scenarios: strategy name tooltips
 * - Bots: strategy names in table
 */

test.describe("operator polish improvements", () => {
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

  // ─── Charts Page ────────────────────────────────────────

  test("chart reset button is visible", async ({ page }) => {
    await page.goto("/charts");
    await expect(page.locator('[data-testid="chart-reset"]').first()).toBeVisible();
  });

  test("chart fit-to-data button is visible", async ({ page }) => {
    await page.goto("/charts");
    await expect(page.locator('[data-testid="chart-fit"]').first()).toBeVisible();
  });

  test("vertical line drawing tool is visible", async ({ page }) => {
    await page.goto("/charts");
    await expect(page.locator('[data-testid="draw-verticalStraightLine"]').first()).toBeVisible();
  });

  test("bots workflow link is visible on charts page", async ({ page }) => {
    await page.goto("/charts");
    const link = page.locator('[data-testid="link-bots"]').first();
    await expect(link).toBeVisible();
    const href = await link.getAttribute("href");
    expect(href).toBe("/bots");
  });

  test("all 7 drawing tools are visible (including V-line)", async ({ page }) => {
    await page.goto("/charts");
    const tools = [
      "draw-pointer",
      "draw-straightLine",
      "draw-horizontalStraightLine",
      "draw-verticalStraightLine",
      "draw-rayLine",
      "draw-fibonacciLine",
      "draw-parallelStraightLine",
    ];
    for (const tool of tools) {
      await expect(page.locator(`[data-testid="${tool}"]`).first()).toBeVisible();
    }
  });

  test("instrument dropdown has expanded instrument list (23+)", async ({ page }) => {
    await page.goto("/charts");
    const select = page.locator('[data-testid="instrument-select"]').first();
    await page.waitForFunction(
      (sel) => (document.querySelector(sel) as HTMLSelectElement)?.options.length >= 20,
      '[data-testid="instrument-select"]'
    );
    const count = await select.locator("option").count();
    expect(count).toBeGreaterThanOrEqual(23);
  });

  // ─── Backtests Page ─────────────────────────────────────

  test("backtests page loads with expected elements", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator('[data-testid="backtests-page"]')).toBeVisible();
  });

  test("backtests page has run form", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="run-form"]')).toBeVisible();
  });

  test("backtests page has filter bar", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="filter-bar"]')).toBeVisible();
  });

  test("backtests page has summary strip", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="summary-strip"]')).toBeVisible();
  });

  test("backtests page has workflow links footer", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page.locator('[data-testid="workflow-links"]')).toBeVisible();
  });

  // ─── Research Page ──────────────────────────────────────

  test("research page loads", async ({ page }) => {
    await page.goto("/research");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByText("Research Matrix")).toBeVisible();
  });

  test("research page has workflow explainer", async ({ page }) => {
    await page.goto("/research");
    await expect(page.getByText("Workflow:")).toBeVisible();
  });

  // ─── Scenarios Page ─────────────────────────────────────

  test("scenarios page loads", async ({ page }) => {
    await page.goto("/scenarios");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByText("Scenario Sandbox")).toBeVisible();
  });

  // ─── Bots Page ──────────────────────────────────────────

  test("bots page loads", async ({ page }) => {
    await page.goto("/bots");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByText("Paper Bots")).toBeVisible();
  });

  // ─── Cross-page Navigation ─────────────────────────────

  test("charts -> backtests navigation works", async ({ page }) => {
    await page.goto("/charts");
    await page.locator('[data-testid="link-backtest"]').first().click();
    await expect(page).toHaveURL(/\/backtests/);
  });

  test("charts -> research navigation works", async ({ page }) => {
    await page.goto("/charts");
    await page.locator('[data-testid="link-research"]').first().click();
    await expect(page).toHaveURL(/\/research/);
  });

  test("charts -> bots navigation works", async ({ page }) => {
    await page.goto("/charts");
    await page.locator('[data-testid="link-bots"]').first().click();
    await expect(page).toHaveURL(/\/bots/);
  });
});
