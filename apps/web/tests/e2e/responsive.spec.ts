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

/**
 * Wave 1 shell: 390 / 768 / 1280 / 1920. Below 1024px the rail is a drawer and
 * the comfortable density is chosen automatically; at and above it the rail
 * is a 56px icon column expanding to 232px, and the density is regular.
 */
const SHELL_WIDTHS = [
  { width: 390, height: 844, density: "comfortable", drawer: true },
  { width: 768, height: 1024, density: "comfortable", drawer: true },
  { width: 1280, height: 800, density: "regular", drawer: false },
  { width: 1920, height: 1080, density: "regular", drawer: false },
] as const;

const SHELL_PAGES = ["/", "/trading/candidates", "/trading/execution", "/system/legend", "/research"];

for (const size of SHELL_WIDTHS) {
  test.describe(`shell at ${size.width}px`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });
    test.beforeEach(async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "viewport set per test; one project is enough");
      await mockApi(page);
    });

    test(`density is ${size.density} automatically`, async ({ page }) => {
      await page.goto("/");
      const html = page.locator("html");
      await expect(html).toHaveAttribute("data-density-pref", "auto");
      await expect(html).toHaveAttribute("data-density", size.density);
      const rowHeight = await page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue("--row-h").trim(),
      );
      expect(rowHeight).toBe(size.density === "comfortable" ? "40px" : "32px");
    });

    test(size.drawer ? "the rail is a closed drawer" : "the rail is a 56px icon column", async ({
      page,
    }) => {
      await page.goto("/trading/risk");
      const nav = page.getByTestId("primary-nav");
      const box = await nav.boundingBox();
      if (box === null) throw new Error("the rail has no bounding box");
      if (size.drawer) {
        await expect(page.getByTestId("nav-toggle")).toBeVisible();
        expect(box.x + box.width).toBeLessThanOrEqual(1);
      } else {
        await expect(page.getByTestId("nav-toggle")).toBeHidden();
        expect(Math.round(box.width)).toBe(56);
        await expect(page.getByTestId("rail-risk")).toHaveAttribute("data-active", "true");
        await page.getByTestId("rail-toggle").click();
        await expect.poll(async () => Math.round((await nav.boundingBox())?.width ?? -1)).toBe(232);
        await expect(nav.getByRole("link", { name: "Exposure" })).toBeVisible();
        await page.getByTestId("rail-toggle").click();
        await expect.poll(async () => Math.round((await nav.boundingBox())?.width ?? -1)).toBe(56);
      }
    });

    test("no horizontal page scroll, banner and status bar on screen", async ({ page }) => {
      for (const path of SHELL_PAGES) {
        await page.goto(path);
        await expect(page.getByTestId("mode-banner")).toBeInViewport();
        await expect(page.getByTestId("status-bar")).toBeInViewport();
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(1);
      }
    });

    test("the page header lists the section's views", async ({ page }) => {
      await page.goto("/research/experiments");
      const header = page.getByTestId("page-header");
      await expect(header).toContainText("Research Lab");
      await expect(header.getByRole("link", { name: "Experiments" })).toHaveAttribute(
        "aria-current",
        "page",
      );
    });
  });
}

test.describe("density preference", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "viewport set per test");
  });

  test("an explicit choice survives a narrow viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => window.localStorage.setItem("fiboki.density", "compact"));
    await mockApi(page);
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-density", "compact");
    await expect(page.locator("html")).toHaveAttribute("data-density-pref", "compact");
  });

  test("automatic density follows the viewport across 1024px", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await mockApi(page);
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-density", "regular");
    await page.setViewportSize({ width: 900, height: 800 });
    await expect(page.locator("html")).toHaveAttribute("data-density", "comfortable");
  });
});
