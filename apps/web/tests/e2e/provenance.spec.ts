import { expect, test } from "@playwright/test";
import { API, candidatesPage, mockApi, tradesPage } from "./fixtures";

/**
 * A ProvenanceChip beside every number, on the views that V1 got wrong.
 *
 * V1's /trades page was titled "Paper / Backtest" and contained only backtest
 * trades. These tests assert the opposite property: the label lives on the row,
 * and the row's label matches the data the API actually sent.
 */

test.describe("provenance chips", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("the execution (trades) view labels every row from the data", async ({ page }) => {
    await page.goto("/trading/execution");
    await expect(page.getByTestId("state-success")).toBeVisible();

    const expected = tradesPage().items.map((t) => t.provenance);
    const rows = page.locator("tbody tr");
    await expect(rows).toHaveCount(expected.length);

    for (const [index, provenance] of expected.entries()) {
      const chip = rows.nth(index).getByTestId("provenance-chip").first();
      await expect(chip).toHaveAttribute("data-provenance", provenance);
    }
  });

  test("the trades view actually contains paper rows, not only backtest", async ({ page }) => {
    await page.goto("/trading/execution");
    await expect(
      page.locator('[data-testid="provenance-chip"][data-provenance="paper"]').first(),
    ).toBeVisible();
    await expect(
      page.locator('[data-testid="provenance-chip"][data-provenance="backtest"]').first(),
    ).toBeVisible();
  });

  test("a mixed result is flagged as mixed", async ({ page }) => {
    await page.goto("/trading/execution");
    await expect(
      page.locator('[data-testid="caveat"][data-code="mixed_provenance_result"]'),
    ).toBeVisible();
  });

  test("provenance comes from the data, not the page", async ({ page }) => {
    // Same page, different payload: every chip must follow the payload.
    await page.route(`${API}/api/trading/trades*`, (route) => {
      const payload = tradesPage();
      payload.items = payload.items.map((item) => ({
        ...item,
        provenance: "broker_demo",
      }));
      return route.fulfill({ json: payload });
    });
    await page.goto("/trading/execution");
    await expect(page.getByTestId("state-success")).toBeVisible();
    // The grid is loaded on first use (Wave 3): wait for its rows, not just the panel.
    await expect(page.getByTestId("grid-row")).toHaveCount(tradesPage().items.length);
    const chips = page.locator("tbody tr").locator('[data-testid="provenance-chip"]');
    const count = await chips.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i += 1) {
      await expect(chips.nth(i)).toHaveAttribute("data-provenance", "broker_demo");
    }
  });

  test("every candidate figure carries a chip", async ({ page }) => {
    await page.goto("/trading/candidates");
    await expect(page.getByTestId("state-success")).toBeVisible();

    const rows = page.getByTestId("candidate-row");
    await expect(rows).toHaveCount(candidatesPage().items.length);

    const firstRow = rows.first();
    // Six numeric columns: trades, win rate, expectancy, net P&L, Sharpe, max DD.
    await expect(firstRow.getByTestId("provenance-chip")).toHaveCount(6);
    await expect(firstRow.getByTestId("figure")).toHaveCount(6);
    for (const chip of await firstRow.getByTestId("provenance-chip").all()) {
      await expect(chip).toHaveAttribute("data-provenance", "out_of_sample");
    }
  });

  test("a chip never renders without a number beside it", async ({ page }) => {
    await page.goto("/trading/candidates");
    await expect(page.getByTestId("state-success")).toBeVisible();
    // The grid is loaded on first use (Wave 3): wait for its rows.
    await expect(page.getByTestId("candidate-row")).toHaveCount(candidatesPage().items.length);
    const figures = page.getByTestId("figure");
    const total = await figures.count();
    expect(total).toBeGreaterThan(0);
    for (let i = 0; i < total; i += 1) {
      const figure = figures.nth(i);
      const hasValue = await figure.getByTestId("figure-value").count();
      const hasMissing = await figure.getByTestId("figure-missing").count();
      expect(hasValue + hasMissing).toBeGreaterThan(0);
    }
  });
});
