import { expect, test, type Page, type Route } from "@playwright/test";
import { API, liveBanner, mockShell, modeBanner } from "./fixtures";

/**
 * Screenshot baselines for the visual grammar: provenance chips, mode frames
 * and the token sheet, in both themes.
 *
 * Baselines are committed for Chromium, desktop project, on Linux only (the
 * CI environment). Font rasterisation differs across operating systems, so on
 * macOS or Windows these are skipped rather than compared against pixels they
 * could never match. Regenerate with:
 *
 *   npx playwright test visual --project=desktop --update-snapshots
 */

test.use({ viewport: { width: 1280, height: 900 } });

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "baselines are for the desktop project only");
  test.skip(process.platform !== "linux", "baselines are committed for Linux Chromium only");
  await page.clock.setFixedTime(new Date("2026-09-28T14:02:31Z"));
  await mockShell(page);
});

async function ready(page: Page) {
  await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
  await page.evaluate(() => document.fonts.ready);
}

for (const theme of ["dark", "light"] as const) {
  test.describe(`${theme} theme`, () => {
    // Tall enough that each legend card is on screen without scrolling, so
    // the sticky banner and status bar never overlap an element screenshot.
    test.use({ colorScheme: theme, viewport: { width: 1280, height: 2200 } });

    test("provenance chips", async ({ page }) => {
      await page.goto("/system/legend");
      await ready(page);
      await expect(page.getByTestId("legend-provenance")).toHaveScreenshot(`chips-${theme}.png`);
    });

    test("mode frames", async ({ page }) => {
      await page.goto("/system/legend");
      await ready(page);
      await expect(page.getByTestId("legend-modes")).toHaveScreenshot(`mode-frames-${theme}.png`);
    });

    test("token sheet", async ({ page }) => {
      await page.goto("/system/legend");
      await ready(page);
      await page.getByTestId("legend-tab-tokens").click();
      await expect(page.getByTestId("legend-tokens")).toHaveScreenshot(`tokens-${theme}.png`);
    });
  });
}

for (const [name, payload] of [
  ["live", liveBanner()],
  [
    "demo",
    modeBanner({
      mode: "demo",
      provenance: "broker_demo",
      severity: "caution",
      touches_broker: true,
    }),
  ],
  ["paper", modeBanner()],
] as const) {
  test(`shell frame in ${name} mode`, async ({ page }) => {
    await page.route(`${API}/api/system/execution-mode`, (route: Route) =>
      route.fulfill({ json: payload }),
    );
    await page.goto("/system/legend");
    await ready(page);
    await expect(page.getByTestId("mode-frame")).toHaveAttribute("data-mode", name);
    // The clock is frozen above, and the as-of comes from the fixture, so the
    // whole viewport (frame, banner, rail, status bar) is compared.
    await expect(page).toHaveScreenshot(`shell-${name}.png`);
  });
}
