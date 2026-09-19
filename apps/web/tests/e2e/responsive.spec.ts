import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";

/**
 * Responsive from 360px.
 *
 * V1's sidebar had no breakpoint at all, which left a 71px content column at
 * phone width. These run at 390px (iPhone 14) and 360px (the narrowest Android
 * the product supports).
 */

const PAGES = [
  "/",
  "/trading/execution",
  "/trading/candidates",
  "/trading/risk",
  "/research/strategies",
  "/system/services",
];

test.describe("mobile 390px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("the nav is a drawer, closed by default", async ({ page }) => {
    await page.goto("/trading/execution");
    const nav = page.getByTestId("primary-nav");
    await expect(nav).toHaveAttribute("data-open", "false");
    await expect(page.getByTestId("nav-toggle")).toBeVisible();

    // Off-screen, not merely narrow.
    const box = await nav.boundingBox();
    if (box === null) throw new Error("the nav has no bounding box");
    expect(box.x + box.width).toBeLessThanOrEqual(1);
  });

  test("the drawer opens, then closes on navigation", async ({ page }) => {
    await page.goto("/trading/execution");
    await page.getByTestId("nav-toggle").click();
    const nav = page.getByTestId("primary-nav");
    await expect(nav).toHaveAttribute("data-open", "true");
    await expect(nav.getByRole("link", { name: "Candidates" })).toBeVisible();

    await nav.getByRole("link", { name: "Candidates" }).click();
    await expect(page).toHaveURL(/\/trading\/candidates/);
    await expect(nav).toHaveAttribute("data-open", "false");
  });

  test("the content column is usable, not a 71px sliver", async ({ page }) => {
    await page.goto("/trading/execution");
    const main = page.locator("main.content");
    const box = await main.boundingBox();
    if (box === null) throw new Error("the content column has no bounding box");
    // Full width minus the 16px gutters, give or take a scrollbar.
    expect(box.width).toBeGreaterThan(330);
  });

  for (const path of PAGES) {
    test(`no horizontal page scroll on ${path}`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByTestId("mode-banner")).toBeVisible();
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);
    });
  }

  test("the banner stays sticky on a phone", async ({ page }) => {
    await page.goto("/trading/execution");
    await page.evaluate(() => window.scrollTo(0, 3000));
    await page.waitForTimeout(120);
    await expect(page.getByTestId("mode-banner")).toBeInViewport();
  });

  test("the kill switch confirm dialog fits the viewport", async ({ page }) => {
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toBeVisible();
    const box = await dialog.boundingBox();
    if (box === null) throw new Error("the dialog has no bounding box");
    expect(box.width).toBeLessThanOrEqual(390);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("wide tables scroll inside their container, not the page", async ({ page }) => {
    await page.goto("/trading/execution");
    await expect(page.getByTestId("state-success")).toBeVisible();
    const canScroll = await page
      .locator(".table-wrap")
      .first()
      .evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(canScroll).toBe(true);
    const pageOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(pageOverflow).toBeLessThanOrEqual(1);
  });
});

test.describe("mobile 360px", () => {
  test.use({ viewport: { width: 360, height: 780 } });

  test("still has no horizontal page scroll at the narrowest supported width", async ({
    page,
  }) => {
    await mockApi(page);
    for (const path of ["/", "/trading/execution", "/trading/candidates"]) {
      await page.goto(path);
      await expect(page.getByTestId("mode-banner")).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);
    }
  });
});
