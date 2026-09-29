import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { API, liveBanner, mockShell, modeBanner } from "./fixtures";
import { allRoutes } from "./routes";

/**
 * Accessibility is a gate (plan §3): axe at zero serious or critical
 * violations on every route, in the dark and the light theme, including the
 * open states of the shell's dialogs and popovers.
 *
 * Routes whose API is not mocked render their explicit error state, which is
 * itself a screen an operator must be able to read.
 */

const ROUTES = allRoutes();
const THEMES = ["dark", "light"] as const;

async function audit(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const blocking = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
    }));
  expect(blocking, `${label}: serious/critical axe violations`).toEqual([]);
}

async function settle(page: Page) {
  await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
  // Most panels settle to success, empty or error. One legitimately stays in
  // its loading state (parameter-lab waits on a strategy list that fails here),
  // and a loading panel must be accessible too, so this waits but does not fail.
  await page
    .locator('[data-testid="state-loading"]')
    .first()
    .waitFor({ state: "detached", timeout: 3_000 })
    .catch(() => undefined);
  await page.evaluate(() => document.fonts.ready);
}

for (const theme of THEMES) {
  test.describe(`axe, ${theme} theme`, () => {
    test.use({ colorScheme: theme });
    test.beforeEach(({}, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "one full sweep, on the desktop project");
    });

    for (const route of ROUTES) {
      test(`${route}`, async ({ page }) => {
        await mockShell(page);
        await page.goto(route);
        await settle(page);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await audit(page, `${route} (${theme})`);
      });
    }

    test("open confirm dialog", async ({ page }) => {
      await mockShell(page);
      await page.goto("/trading/risk");
      await page.getByTestId("kill-switch-arm").click();
      await page.getByTestId("confirm-choice-flatten").click();
      await expect(page.getByTestId("confirm-consequences")).toBeVisible();
      await audit(page, `confirm dialog (${theme})`);
    });

    test("open promote dialog with caveat checklist", async ({ page }) => {
      await mockShell(page);
      await page.goto("/trading/candidates");
      await page.getByTestId("promote-ichimoku_kumo_trend").click();
      await expect(page.getByTestId("confirm-acknowledgements")).toBeVisible();
      await audit(page, `promote dialog (${theme})`);
    });

    test("open MIXED provenance and display popovers", async ({ page }) => {
      await mockShell(page);
      await page.goto("/trading/execution");
      await page.getByTestId("chart").getByTestId("provenance-chip").click();
      await expect(page.getByTestId("provenance-mixed-popover")).toBeVisible();
      await audit(page, `mixed popover (${theme})`);
      await page.keyboard.press("Escape");
      await page.getByTestId("display-settings-trigger").click();
      await expect(page.getByTestId("pref-density")).toBeVisible();
      await audit(page, `display popover (${theme})`);
    });

    test("legend controls and inspector", async ({ page }) => {
      await mockShell(page);
      await page.goto("/system/legend");
      await settle(page);
      await page.getByTestId("legend-tab-tokens").click();
      await audit(page, `legend tokens (${theme})`);
      await page.getByTestId("legend-tab-controls").click();
      await expect(page.getByTestId("legend-controls")).toBeVisible();
      await audit(page, `legend controls (${theme})`);
      await page.getByTestId("status-api").click();
      await expect(page.getByTestId("inspector")).toBeVisible();
      await audit(page, `inspector (${theme})`);
    });

    for (const [name, payload] of [
      ["live", liveBanner()],
      ["demo", modeBanner({ mode: "demo", provenance: "broker_demo", severity: "caution" })],
      ["shadow", modeBanner({ mode: "shadow", provenance: "shadow" })],
      ["backtest", modeBanner({ mode: "backtest", provenance: "backtest" })],
    ] as const) {
      test(`banner in ${name} mode`, async ({ page }) => {
        await mockShell(page);
        await page.route(`${API}/api/system/execution-mode`, (route) =>
          route.fulfill({ json: payload }),
        );
        await page.goto("/");
        await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", name);
        await settle(page);
        await audit(page, `${name} banner (${theme})`);
      });
    }

    test("banner when the mode is unknown", async ({ page }) => {
      await mockShell(page);
      await page.route(`${API}/api/system/execution-mode`, (route) =>
        route.fulfill({
          status: 503,
          json: { code: "unavailable", detail: "down", correlation_id: "x", context: {} },
        }),
      );
      await page.goto("/");
      await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "unknown");
      await audit(page, `unknown banner (${theme})`);
    });
  });
}

test.describe("axe at phone width", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "mobile project only");
  });

  for (const route of ["/", "/trading/candidates", "/trading/risk"]) {
    test(`${route} with the drawer open`, async ({ page }) => {
      await mockShell(page);
      await page.goto(route);
      await settle(page);
      await audit(page, `${route} closed drawer`);
      await page.getByTestId("nav-toggle").click();
      await expect(page.getByTestId("primary-nav")).toHaveAttribute("data-open", "true");
      await audit(page, `${route} open drawer`);
    });
  }
});
