import { expect, test } from "@playwright/test";
import { mockShell } from "./fixtures";
import { mockResearch } from "./research-fixtures";

/**
 * Review screenshots of the research analytics screens at 1920, 1440 and 390
 * wide, dark and light, with the backend-produced fixtures. Skipped unless
 * RESEARCH_SCREENS names an output directory:
 *
 *   RESEARCH_SCREENS=/tmp/research-screens npx playwright test research-screens --project=desktop
 */
const OUT = process.env.RESEARCH_SCREENS;

const PAGES: [string, string][] = [
  ["tearsheet", "/research/runs/bt_0001"],
  ["compare", "/research/compare?runs=bt_0001,bt_0002,bt_0003"],
  ["parameter-lab", "/research/parameter-lab?strategy=ichimoku_kumo_trend"],
  ["jobs", "/research/jobs"],
  ["walk-forward", "/research/walk-forward?strategy=donchian_breakout_atr"],
  ["strategy", "/research/strategies/ichimoku_kumo_trend"],
  ["correlations", "/research/correlations"],
  ["runs", "/research/runs"],
];
const WIDTHS = [1920, 1440, 390];

for (const theme of ["dark", "light"] as const) {
  test.describe(`research screens ${theme}`, () => {
    test.use({ colorScheme: theme });
    for (const width of WIDTHS) {
      for (const [name, path] of PAGES) {
        test(`${name} ${width}`, async ({ page }, info) => {
          test.skip(!OUT || info.project.name !== "desktop", "set RESEARCH_SCREENS to capture");
          await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
          await mockShell(page);
          await mockResearch(page);
          await page.goto(path);
          await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
          await page.locator('[data-testid="state-loading"]').first().waitFor({ state: "detached", timeout: 8_000 }).catch(() => undefined);
          await page.evaluate(() => document.fonts.ready);
          await page.waitForTimeout(300);
          await page.screenshot({ path: `${OUT}/${name}-${width}-${theme}.png`, fullPage: true });
        });
      }
    }
  });
}
