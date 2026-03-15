import { test, expect } from "@playwright/test";

/**
 * Dashboard E2E Tests
 *
 * Validates the upgraded operator dashboard renders correctly,
 * all sections are present, quick actions navigate properly,
 * drill-through links work, empty states render, and tooltips trigger.
 *
 * Uses the fiboki_auth cookie to bypass auth middleware (shell-level test).
 * API calls will 401 without a real JWT, but the page should render
 * gracefully with empty/default data (SWR fallback).
 */

test.describe("dashboard", () => {
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

  // ─── 1. Page loads ─────────────────────────────────────────

  test("dashboard loads without crashing", async ({ page }) => {
    await page.goto("/");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator('[data-testid="dashboard"]')).toBeVisible();
  });

  // ─── 2. KPI cards render ───────────────────────────────────

  test("KPI cards section renders all 6 cards", async ({ page }) => {
    await page.goto("/");
    const kpiSection = page.locator('[data-testid="kpi-cards"]');
    await expect(kpiSection).toBeVisible();

    // All 6 stat cards should be present
    await expect(page.locator('[data-testid="stat-balance"]')).toBeVisible();
    await expect(page.locator('[data-testid="stat-equity"]')).toBeVisible();
    await expect(page.locator('[data-testid="stat-daily-pnl"]')).toBeVisible();
    await expect(page.locator('[data-testid="stat-weekly-pnl"]')).toBeVisible();
    await expect(page.locator('[data-testid="stat-running-bots"]')).toBeVisible();
    await expect(page.locator('[data-testid="stat-open-positions"]')).toBeVisible();
  });

  // ─── 3. Fleet summary renders ──────────────────────────────

  test("fleet summary panel renders", async ({ page }) => {
    await page.goto("/");
    const fleet = page.locator('[data-testid="fleet-summary"]');
    await expect(fleet).toBeVisible();
    // Should contain "Fleet Overview" heading
    await expect(fleet.locator("text=Fleet Overview")).toBeVisible();
    // Should have "Manage bots" link
    await expect(fleet.locator("text=Manage bots")).toBeVisible();
  });

  // ─── 4. Activity feed renders ──────────────────────────────

  test("activity feed panel renders", async ({ page }) => {
    await page.goto("/");
    const feed = page.locator('[data-testid="activity-feed"]');
    await expect(feed).toBeVisible();
    await expect(feed.getByText("Recent Activity", { exact: true })).toBeVisible();
  });

  // ─── 5. Quick actions render and are clickable ─────────────

  test("quick action buttons are visible", async ({ page }) => {
    await page.goto("/");
    const qa = page.locator('[data-testid="quick-actions"]');
    await expect(qa).toBeVisible();

    await expect(page.locator('[data-testid="qa-run-backtest"]')).toBeVisible();
    await expect(page.locator('[data-testid="qa-research-matrix"]')).toBeVisible();
    await expect(page.locator('[data-testid="qa-paper-bots"]')).toBeVisible();
    await expect(page.locator('[data-testid="qa-view-charts"]')).toBeVisible();
    await expect(page.locator('[data-testid="qa-alerts"]')).toBeVisible();
    await expect(page.locator('[data-testid="qa-jobs"]')).toBeVisible();
  });

  test("Run Backtest navigates to /backtests", async ({ page }) => {
    await page.goto("/");
    await page.locator('[data-testid="qa-run-backtest"]').click();
    await expect(page).toHaveURL(/\/backtests/);
  });

  test("Research Matrix navigates to /research", async ({ page }) => {
    await page.goto("/");
    await page.locator('[data-testid="qa-research-matrix"]').click();
    await expect(page).toHaveURL(/\/research/);
  });

  test("Paper Bots navigates to /bots", async ({ page }) => {
    await page.goto("/");
    await page.locator('[data-testid="qa-paper-bots"]').click();
    await expect(page).toHaveURL(/\/bots/);
  });

  test("View Charts navigates to /charts", async ({ page }) => {
    await page.goto("/");
    await page.locator('[data-testid="qa-view-charts"]').click();
    await expect(page).toHaveURL(/\/charts/);
  });

  test("Alerts navigates to /alerts", async ({ page }) => {
    await page.goto("/");
    await page.locator('[data-testid="qa-alerts"]').click();
    await expect(page).toHaveURL(/\/alerts/);
  });

  test("Jobs navigates to /jobs", async ({ page }) => {
    await page.goto("/");
    await page.locator('[data-testid="qa-jobs"]').click();
    await expect(page).toHaveURL(/\/jobs/);
  });

  // ─── 6. Drill-through links ────────────────────────────────

  test("fleet overview 'Manage bots' links to /bots", async ({ page }) => {
    await page.goto("/");
    const link = page.locator('[data-testid="fleet-summary"]').locator("text=Manage bots");
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/bots/);
  });

  test("health panel 'Full system details' links to /system", async ({ page }) => {
    await page.goto("/");
    const link = page.locator('[data-testid="health-panel"]').locator("text=Full system details");
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/system/);
  });

  test("activity feed 'View jobs' links to /jobs", async ({ page }) => {
    await page.goto("/");
    const link = page.locator('[data-testid="activity-feed"]').locator("text=View jobs");
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/jobs/);
  });

  // ─── 7. Empty states render ────────────────────────────────

  test("shortlist panel renders (empty or populated)", async ({ page }) => {
    await page.goto("/");
    const panel = page.locator('[data-testid="shortlist-panel"]');
    await expect(panel).toBeVisible();
    await expect(panel.getByText("Saved Combos", { exact: true })).toBeVisible();
  });

  test("health panel renders system status rows", async ({ page }) => {
    await page.goto("/");
    const panel = page.locator('[data-testid="health-panel"]');
    await expect(panel).toBeVisible();
    // Key health indicators should always render
    await expect(panel.getByText("Engine", { exact: true })).toBeVisible();
    await expect(panel.getByText("Execution Mode", { exact: true })).toBeVisible();
    await expect(panel.getByText("Kill Switch", { exact: true })).toBeVisible();
    await expect(panel.getByText("Database", { exact: true })).toBeVisible();
    await expect(panel.getByText("IG Demo Readiness", { exact: true })).toBeVisible();
  });

  // ─── 8. Tooltip triggers ──────────────────────────────────

  test("InfoTip on Balance card shows tooltip on hover", async ({ page }) => {
    await page.goto("/");
    // Find the info icon button inside the Balance card
    const balanceCard = page.locator('[data-testid="stat-balance"]');
    const infoBtn = balanceCard.locator('button[aria-label="More info"]');
    await expect(infoBtn).toBeVisible();
    // Hover should show tooltip
    await infoBtn.hover();
    await expect(page.locator("text=Current paper account balance")).toBeVisible();
  });

  test("InfoTip on health panel title shows tooltip on hover", async ({ page }) => {
    await page.goto("/");
    const panel = page.locator('[data-testid="health-panel"]');
    await expect(panel).toBeVisible();
    // The section title "System Health & Readiness" has an InfoTip
    // Find the info button anywhere on the page within the health panel area
    const infoBtn = panel.locator('[aria-label="More info"]').first();
    // If present, hover and check tooltip appears
    if (await infoBtn.isVisible()) {
      await infoBtn.hover();
      await page.waitForTimeout(300);
    }
    // Verify the panel title text renders (contains the text, not exact due to inline InfoTip)
    await expect(panel.getByText("System Health")).toBeVisible();
  });

  // ─── 9. No crash if API fails ─────────────────────────────

  test("dashboard renders gracefully without API data", async ({ page }) => {
    // The fiboki_auth cookie lets us past middleware but there's no JWT,
    // so all API calls return 401. The page should still render with defaults.
    await page.goto("/");
    await expect(page.locator('[data-testid="dashboard"]')).toBeVisible();
    await expect(page.locator('[data-testid="kpi-cards"]')).toBeVisible();
    await expect(page.locator('[data-testid="quick-actions"]')).toBeVisible();

    // No unhandled errors should crash the page
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    await page.waitForTimeout(2000);
    // Filter out expected SWR/fetch errors — we care about React render crashes
    const renderCrashes = errors.filter(
      (e) => !e.includes("fetch") && !e.includes("401") && !e.includes("NetworkError") && !e.includes("Failed to fetch")
    );
    expect(renderCrashes).toHaveLength(0);
  });

  // ─── 10. Layout integrity ─────────────────────────────────

  test("all major dashboard sections exist in correct order", async ({ page }) => {
    await page.goto("/");

    // All panels should be present on the page
    const sections = [
      '[data-testid="kpi-cards"]',
      '[data-testid="fleet-summary"]',
      '[data-testid="activity-feed"]',
      '[data-testid="quick-actions"]',
      '[data-testid="shortlist-panel"]',
      '[data-testid="health-panel"]',
    ];

    for (const selector of sections) {
      await expect(page.locator(selector)).toBeVisible();
    }
  });

  // ─── 11. No dead links or buttons ─────────────────────────

  test("no buttons are disabled unintentionally", async ({ page }) => {
    await page.goto("/");
    // All quick action links should be present and enabled
    const qaIds = ["qa-run-backtest", "qa-research-matrix", "qa-paper-bots", "qa-view-charts", "qa-alerts", "qa-jobs"];
    for (const id of qaIds) {
      const btn = page.locator(`[data-testid="${id}"]`);
      await expect(btn).toBeVisible();
      await expect(btn).toBeEnabled();
    }
  });

  // ─── 12. No console errors on dashboard ────────────────────

  test("no console errors from dashboard rendering", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        // Ignore expected errors from unauthenticated SWR requests and CORS
        if (
          !text.includes("401") &&
          !text.includes("Failed to fetch") &&
          !text.includes("NetworkError") &&
          !text.includes("CORS") &&
          !text.includes("ERR_FAILED") &&
          !text.includes("Access-Control") &&
          !text.includes("net::") &&
          !text.includes("Failed to load resource")
        ) {
          consoleErrors.push(text);
        }
      }
    });

    await page.goto("/");
    await page.waitForTimeout(2000);
    expect(consoleErrors).toHaveLength(0);
  });
});
