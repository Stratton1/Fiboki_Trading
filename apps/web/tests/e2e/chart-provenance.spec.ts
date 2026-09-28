import { expect, test, type Route } from "@playwright/test";
import { API, figure, mockApi, source, tradesPage } from "./fixtures";

/**
 * A chart's provenance chip is derived from the data it draws.
 *
 * The execution page used to label the R-multiple distribution over a MIXED
 * trade list with the first row's provenance, falling back to "backtest"; the
 * exposure chart fell back to "paper"; market pulse and correlations hard-coded
 * "backtest". Each of those was a page making a provenance claim the data did
 * not make, which is the V1 failure the ProvenanceChip exists to prevent.
 */

const chartChip = (page: import("@playwright/test").Page) =>
  page.getByTestId("chart").getByTestId("provenance-chip");

test.describe("chart provenance is derived from the data", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("a distribution over mixed trades is labelled MIXED with counts", async ({ page }) => {
    await page.goto("/trading/execution");
    const chip = chartChip(page);
    await expect(chip).toHaveAttribute("data-provenance", "mixed");
    await expect(chip).toHaveText("MIXED");
    await expect(chip).toHaveAttribute(
      "data-counts",
      "backtest:1,walkforward:1,out_of_sample:1,paper:1",
    );
    const title = await chip.getAttribute("title");
    for (const part of ["BACKTEST 1", "PAPER 1", "OOS 1", "WALKFORWARD 1"]) {
      expect(title).toContain(part);
    }
  });

  test("a single-provenance result gets that provenance, whatever the first row", async ({
    page,
  }) => {
    await page.route(`${API}/api/trading/trades*`, (route: Route) => {
      const payload = tradesPage();
      payload.items = payload.items.map((item) => ({
        ...item,
        provenance: "paper",
        r_multiple: { ...item.r_multiple, provenance: "paper" },
      }));
      return route.fulfill({ json: payload });
    });
    await page.goto("/trading/execution");
    await expect(chartChip(page)).toHaveAttribute("data-provenance", "paper");
  });

  test("only plotted values count towards the label", async ({ page }) => {
    // The backtest row has no R-multiple, so it is not in the chart and must
    // not make the chart's label MIXED.
    await page.route(`${API}/api/trading/trades*`, (route: Route) => {
      const payload = tradesPage();
      payload.items = payload.items.map((item) =>
        item.provenance === "paper"
          ? item
          : { ...item, r_multiple: figure(null, item.provenance, "R") },
      );
      payload.items.push({
        ...payload.items[1]!,
        trade_id: "trd_0005",
        r_multiple: figure(0.4, "paper", "R"),
      });
      return route.fulfill({ json: payload });
    });
    await page.goto("/trading/execution");
    await expect(chartChip(page)).toHaveAttribute("data-provenance", "paper");
  });

  test("a chart with no labelled value says 'unlabelled source', not a guess", async ({
    page,
  }) => {
    await page.route(`${API}/api/trading/trades*`, (route: Route) => {
      const payload = tradesPage();
      payload.items = payload.items.map((item) => ({
        ...item,
        r_multiple: figure(null, item.provenance, "R"),
      }));
      return route.fulfill({ json: payload });
    });
    await page.goto("/trading/execution");
    const chip = chartChip(page);
    await expect(chip).toHaveAttribute("data-provenance", "unlabelled");
    await expect(chip).toHaveText("unlabelled source");
  });

  test("exposure is labelled from its rows, not from the first row", async ({ page }) => {
    const row = (key: string, provenance: string) => ({
      key,
      label: key.split(":")[1],
      exposure_pct: figure(12.5, provenance, "pct"),
      limit_pct: figure(25, provenance, "pct"),
      utilisation_pct: figure(50, provenance, "pct"),
      breached: false,
    });
    await page.route(`${API}/api/trading/exposure`, (route: Route) =>
      route.fulfill({
        json: {
          items: [row("instrument:EURUSD", "broker_demo"), row("instrument:GBPUSD", "paper")],
          total: 2,
          offset: 0,
          limit: 100,
          source: source("live"),
          caveats: [],
        },
      }),
    );
    await page.goto("/trading/exposure");
    const chip = chartChip(page);
    await expect(chip).toHaveAttribute("data-provenance", "mixed");
    await expect(chip).toHaveAttribute("data-counts", "paper:2,broker_demo:2");
  });

  test("market pulse shows the provenance the API put on each spread", async ({ page }) => {
    const instrument = (symbol: string) => ({
      symbol,
      asset_class: "fx",
      base: symbol.slice(0, 3),
      quote: symbol.slice(3),
      trading_hours: "24x5",
      pip_size: figure(0.0001, "shadow"),
      contract_size: figure(100000, "shadow", "units"),
      min_size: figure(0.01, "shadow", "lots"),
      size_step: figure(0.01, "shadow", "lots"),
      typical_spread_pips: figure(0.8, "shadow", "pips"),
      retail_leverage: figure(30, "shadow", "x"),
      annual_financing_bps: figure(150, "shadow", "bps"),
    });
    await page.route(`${API}/api/markets/instruments*`, (route: Route) =>
      route.fulfill({
        json: {
          items: [instrument("EURUSD"), instrument("GBPUSD")],
          total: 2,
          offset: 0,
          limit: 40,
          source: source("live"),
          caveats: [],
        },
      }),
    );
    await page.goto("/market-pulse");
    await expect(chartChip(page)).toHaveAttribute("data-provenance", "shadow");
  });

  test("correlations show the API's SourceNote, not a hard-coded backtest", async ({
    page,
  }) => {
    await page.route(`${API}/api/markets/correlations`, (route: Route) =>
      route.fulfill({
        json: {
          data: { instruments: [], matrix: [], window_bars: null, available: false },
          source: source("absent", "No market-data root mounted."),
          caveats: [],
        },
      }),
    );
    await page.goto("/markets/correlations");
    const chip = chartChip(page);
    await expect(chip).toHaveAttribute("data-provenance", "source");
    await expect(chip).toHaveAttribute("data-source-kind", "absent");
    await expect(chip).not.toHaveAttribute("data-provenance", "backtest");
  });
});
