import { test, expect, Page } from "@playwright/test";

/**
 * Chart Trust E2E — Backtest detail page
 *
 * Verifies:
 *   1. Chart container renders with a canvas element (KLineChart uses <canvas>)
 *   2. Marker toggle checkboxes are present and functional
 *   3. No-data fallback renders when market data is unavailable
 *
 * Required env vars:
 *   FIBOKI_TEST_EMAIL / FIBOKI_E2E_USERNAME
 *   FIBOKI_TEST_PASSWORD / FIBOKI_E2E_PASSWORD
 *   FIBOKEI_API_URL (defaults to http://localhost:8000)
 *
 * For deterministic data, set FIBOKEI_DEV_SEED=1 on the backend.
 *
 * Run:
 *   npx playwright test --project=chart-trust
 */

const USERNAME =
  process.env.FIBOKI_TEST_EMAIL || process.env.FIBOKI_E2E_USERNAME;
const PASSWORD =
  process.env.FIBOKI_TEST_PASSWORD || process.env.FIBOKI_E2E_PASSWORD;
const API_URL =
  process.env.FIBOKEI_API_URL || "http://localhost:8000";
const IS_CI = !!process.env.CI;

// ── Helpers ─────────────────────────────────────────────────────────

async function login(page: Page): Promise<string> {
  await page.goto("/login");
  await page.fill("#username", USERNAME!);
  await page.fill("#password", PASSWORD!);
  await page.click('button[type="submit"]');
  await expect(page).not.toHaveURL(/\/login/, { timeout: 10_000 });
  const token = await page.evaluate(() => localStorage.getItem("fibokei_token"));
  return token ?? "";
}

async function seedAndGetBacktestId(token: string): Promise<number | null> {
  try {
    const res = await fetch(`${API_URL}/api/v1/dev/seed/backtest`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.backtest_run_id ?? null;
  } catch {
    return null;
  }
}

// ── Tests ───────────────────────────────────────────────────────────

test.describe("backtest chart trust", () => {
  if (IS_CI && (!USERNAME || !PASSWORD)) {
    test("CI requires auth credentials", () => {
      throw new Error(
        "CI mode: FIBOKI_TEST_EMAIL and FIBOKI_TEST_PASSWORD must be set"
      );
    });
  }

  test.skip(!USERNAME || !PASSWORD, "Skipped: no auth credentials provided");

  let authToken = "";
  let backtestId: number | null = null;

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    authToken = await login(page);
    backtestId = await seedAndGetBacktestId(authToken);
    await page.close();
  });

  test("chart container renders canvas on backtest detail", async ({
    page,
  }) => {
    // Need a backtest ID to navigate to
    if (!backtestId) {
      // Try to find one from the list
      await login(page);
      await page.goto("/backtests");
      await page.waitForLoadState("networkidle");
      const firstLink = page.locator("a[href^='/backtests/']").first();
      if (!(await firstLink.isVisible())) {
        if (IS_CI) {
          throw new Error("CI: no backtests available for chart test");
        }
        test.skip(true, "No backtests available");
        return;
      }
      await firstLink.click();
    } else {
      await login(page);
      await page.goto(`/backtests/${backtestId}`);
    }

    await page.waitForLoadState("networkidle");

    // The page should show the strategy name
    await expect(page.locator("h2")).toBeVisible({ timeout: 10_000 });

    // Chart section: either a canvas (data available) or amber fallback (no data)
    const chartCanvas = page.locator(".h-\\[450px\\] canvas");
    const noDataFallback = page.locator("text=No market data available");

    // One of them must be visible
    const hasCanvas = await chartCanvas.isVisible().catch(() => false);
    const hasFallback = await noDataFallback.isVisible().catch(() => false);

    expect(hasCanvas || hasFallback).toBe(true);

    if (hasCanvas) {
      // Canvas should have non-zero dimensions (i.e. klinecharts rendered)
      const box = await chartCanvas.first().boundingBox();
      expect(box).not.toBeNull();
      expect(box!.width).toBeGreaterThan(100);
      expect(box!.height).toBeGreaterThan(100);
    }
  });

  test("marker toggle checkboxes change state", async ({ page }) => {
    if (!backtestId) {
      test.skip(true, "No seeded backtest for toggle test");
      return;
    }

    await login(page);
    await page.goto(`/backtests/${backtestId}`);
    await page.waitForLoadState("networkidle");

    // Check if chart section exists (needs market data)
    const chartSection = page.locator("text=Price Chart with Trade Markers");
    if (!(await chartSection.isVisible().catch(() => false))) {
      test.skip(true, "No market data — chart section not rendered");
      return;
    }

    // Find the three toggle checkboxes
    const entriesCheckbox = page.locator("label", { hasText: "Entries" }).locator("input[type='checkbox']");
    const exitsCheckbox = page.locator("label", { hasText: "Exits" }).locator("input[type='checkbox']");
    const linesCheckbox = page.locator("label", { hasText: "Connecting lines" }).locator("input[type='checkbox']");

    // Defaults: entries ON, exits ON, lines OFF
    await expect(entriesCheckbox).toBeChecked();
    await expect(exitsCheckbox).toBeChecked();
    await expect(linesCheckbox).not.toBeChecked();

    // Toggle lines ON
    await linesCheckbox.check();
    await expect(linesCheckbox).toBeChecked();

    // Toggle entries OFF
    await entriesCheckbox.uncheck();
    await expect(entriesCheckbox).not.toBeChecked();

    // Verify localStorage was updated
    const linesStored = await page.evaluate(() =>
      localStorage.getItem("fibokei_marker_lines")
    );
    expect(linesStored).toBe("true");

    const entriesStored = await page.evaluate(() =>
      localStorage.getItem("fibokei_marker_entries")
    );
    expect(entriesStored).toBe("false");
  });

  test("no-data fallback shows amber warning", async ({ page }) => {
    await login(page);
    // Navigate to a backtest — if market data is missing, the fallback appears
    // This test validates the fallback UI structure itself
    if (!backtestId) {
      test.skip(true, "No seeded backtest available");
      return;
    }

    await page.goto(`/backtests/${backtestId}`);
    await page.waitForLoadState("networkidle");

    // Either the chart renders or the fallback shows — both are valid
    const noDataFallback = page.locator("text=No market data available");
    const chartCanvas = page.locator(".h-\\[450px\\] canvas");

    const hasFallback = await noDataFallback.isVisible().catch(() => false);
    const hasCanvas = await chartCanvas.isVisible().catch(() => false);

    // At least one path must render
    expect(hasFallback || hasCanvas).toBe(true);

    if (hasFallback) {
      // Verify the fallback has proper styling
      const fallbackBox = page.locator(".bg-amber-50.border-amber-200");
      await expect(fallbackBox).toBeVisible();
    }
  });
});
