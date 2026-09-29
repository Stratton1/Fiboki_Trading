import { expect, test, type Route } from "@playwright/test";
import { API, figure, mockApi, source } from "./fixtures";

/**
 * URL state (plan D-F3, Wave 2): filters, the selected entity and the open
 * tab live in the query string, so a view can be linked and restored.
 */

test.describe("URL state", () => {
  test("a linked Execution filter is applied on load", async ({ page }) => {
    await mockApi(page);
    const requested: string[] = [];
    await page.route(`${API}/api/trading/trades*`, async (route: Route) => {
      requested.push(route.request().url());
      await route.fallback();
    });
    await page.goto("/trading/execution?provenance=paper");
    await expect(page.getByTestId("provenance-filter")).toHaveValue("paper");
    expect(requested.length).toBeGreaterThan(0);
    for (const url of requested) expect(url).toContain("provenance=paper");
  });

  test("changing the filter writes the URL, and a reload restores it", async ({ page }) => {
    await mockApi(page);
    const requested: string[] = [];
    await page.route(`${API}/api/trading/trades*`, async (route: Route) => {
      requested.push(route.request().url());
      await route.fallback();
    });
    await page.goto("/trading/execution");
    await page.getByTestId("provenance-filter").selectOption("paper");
    await expect(page).toHaveURL(/\?provenance=paper$/);
    await expect.poll(() => requested.at(-1)).toContain("provenance=paper");
    await page.reload();
    await expect(page.getByTestId("provenance-filter")).toHaveValue("paper");
    await page.getByTestId("provenance-filter").selectOption("all");
    await expect(page).toHaveURL(/\/trading\/execution$/);
  });

  test("the Parameter Lab's selected strategy is in the URL", async ({ page }) => {
    await mockApi(page);
    const strategy = (id: string) => ({ strategy_id: id, name: id.replace(/_/g, " ") });
    await page.route(`${API}/api/research/strategies`, (route: Route) =>
      route.fulfill({
        json: {
          items: [strategy("ichimoku_kumo_trend"), strategy("donchian_breakout_atr")],
          total: 2,
          offset: 0,
          limit: 100,
          source: source("seed"),
          caveats: [],
        },
      }),
    );
    const labs: string[] = [];
    await page.route(`${API}/api/research/parameter-lab/*`, (route: Route) => {
      labs.push(new URL(route.request().url()).pathname);
      return route.fulfill({
        json: {
          data: {
            strategy_id: "x",
            parameters: [],
            search_space_size: figure(12, "backtest", "count"),
            deflation_warning: "Twelve trials.",
          },
          source: source("seed"),
          caveats: [],
        },
      });
    });

    await page.goto("/research/parameter-lab?strategy=donchian_breakout_atr");
    await expect(page.getByTestId("strategy-select")).toHaveValue("donchian_breakout_atr");
    await expect.poll(() => labs.at(-1)).toBe("/api/research/parameter-lab/donchian_breakout_atr");

    await page.getByTestId("strategy-select").selectOption("ichimoku_kumo_trend");
    await expect(page).toHaveURL(/\?strategy=ichimoku_kumo_trend$/);
    await expect.poll(() => labs.at(-1)).toBe("/api/research/parameter-lab/ichimoku_kumo_trend");
  });

  test("the Legend's open tab is in the URL", async ({ page }) => {
    await mockApi(page);
    await page.goto("/system/legend?tab=tokens");
    await expect(page.getByTestId("legend-tokens")).toBeVisible();
    await page.getByTestId("legend-tab-meanings").click();
    await expect(page).toHaveURL(/\/system\/legend$/);
    await expect(page.getByTestId("legend-provenance")).toBeVisible();
  });
});
