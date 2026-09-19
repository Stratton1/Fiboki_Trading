import { expect, test } from "@playwright/test";
import { API, killSwitchView, mockApi, modeBanner } from "./fixtures";

/**
 * The execution-mode banner: sticky, always present, and always the real mode.
 *
 * V1 hardcoded "Paper trading only — no live execution" into a native confirm()
 * regardless of the actual mode, and 14 of its 19 pages never read the mode at
 * all. These tests drive the API to demo and live and assert the banner follows.
 */

test.describe("execution mode banner", () => {
  test("reads paper from the API", async ({ page }) => {
    await mockApi(page);
    await page.goto("/trading/execution");
    const banner = page.getByTestId("mode-banner");
    await expect(banner).toHaveAttribute("data-mode", "paper");
    await expect(banner).toHaveAttribute("data-severity", "info");
  });

  test("follows the API into ig_demo without any page change", async ({ page }) => {
    await mockApi(page);
    await page.route(`${API}/api/system/execution-mode`, (route) =>
      route.fulfill({
        json: modeBanner({
          mode: "demo",
          provenance: "broker_demo",
          touches_broker: true,
          severity: "caution",
          headline: "DEMO — orders are reaching a broker demo account",
          detail: "Real orders are submitted to a demo venue.",
        }),
      }),
    );
    await page.goto("/trading/execution");
    const banner = page.getByTestId("mode-banner");
    await expect(banner).toHaveAttribute("data-mode", "demo");
    await expect(banner).toHaveAttribute("data-severity", "caution");
    await expect(banner).toContainText("broker demo account");
    await expect(page.getByTestId("mode-banner-mode")).toHaveText("DEMO");
  });

  test("shows danger and real-money in live", async ({ page }) => {
    await mockApi(page);
    await page.route(`${API}/api/system/execution-mode`, (route) =>
      route.fulfill({
        json: modeBanner({
          mode: "live",
          provenance: "broker_live",
          touches_broker: true,
          touches_real_money: true,
          severity: "danger",
          headline: "LIVE — orders are reaching a real-money account",
          detail: "Every control on this page moves real capital.",
        }),
      }),
    );
    await page.goto("/");
    const banner = page.getByTestId("mode-banner");
    await expect(banner).toHaveAttribute("data-mode", "live");
    await expect(banner).toHaveAttribute("data-real-money", "true");
    await expect(banner).toHaveAttribute("role", "alert");
  });

  test("cannot scroll out of view", async ({ page }) => {
    await mockApi(page);
    await page.goto("/trading/execution");
    const banner = page.getByTestId("mode-banner");
    await expect(banner).toBeVisible();

    await expect(banner).toHaveCSS("position", "sticky");

    const before = await banner.boundingBox();
    await page.evaluate(() => window.scrollTo(0, 4000));
    await page.waitForTimeout(150);
    const after = await banner.boundingBox();
    if (before === null || after === null) {
      throw new Error("the banner has no bounding box");
    }

    // Still on screen, and still at the top, after scrolling the page.
    await expect(banner).toBeInViewport();
    expect(Math.abs(after.y - before.y)).toBeLessThan(2);
  });

  test("is present on every section", async ({ page }) => {
    await mockApi(page);
    for (const path of [
      "/",
      "/trading/candidates",
      "/trading/execution",
      "/research/strategies",
      "/markets",
      "/system/settings",
      "/intelligence/agents",
    ]) {
      await page.goto(path);
      await expect(page.getByTestId("mode-banner")).toBeVisible();
    }
  });

  test("says MODE UNKNOWN rather than guessing when the API fails", async ({ page }) => {
    await page.route(`${API}/api/system/execution-mode`, (route) =>
      route.fulfill({
        status: 503,
        json: { code: "unavailable", detail: "down", correlation_id: "x", context: {} },
      }),
    );
    await page.goto("/");
    const banner = page.getByTestId("mode-banner");
    await expect(banner).toHaveAttribute("data-mode", "unknown");
    await expect(banner).toContainText("cannot say what mode it is in");
  });

  test("surfaces an armed kill switch in the banner", async ({ page }) => {
    await mockApi(page);
    await page.route(`${API}/api/system/execution-mode`, (route) =>
      route.fulfill({
        json: modeBanner({ kill_switch_active: true, kill_switch_mode: "flatten" }),
      }),
    );
    await page.route(`${API}/api/system/kill-switch`, (route) =>
      route.fulfill({
        json: killSwitchView({ active: true, mode: "flatten", requires_flatten: true }),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("mode-banner-killswitch")).toContainText("KILL SWITCH ARMED");
  });
});
