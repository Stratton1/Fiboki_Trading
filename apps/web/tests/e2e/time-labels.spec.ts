import { expect, test, type Route } from "@playwright/test";
import { API, figure, mockApi, tradesPage } from "./fixtures";

/**
 * Every time on screen says which zone it is in, and the platform's as-of
 * times are shown rather than dropped.
 *
 * `formatTimestamp` used to render UTC with no label, so an operator on BST
 * read a 14:00 UTC fill as 14:00 local. `Figure.as_of` and `SourceNote.as_of`
 * were in every payload and never rendered.
 */

test.describe("time labels", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("trade close times are labelled UTC", async ({ page }) => {
    await page.goto("/trading/execution");
    const firstRow = page.locator("tbody tr").first();
    // exit_time 2026-09-18T14:00:00Z in the fixture.
    await expect(firstRow).toContainText("18/09/2026, 14:00 UTC");
  });

  test("the source note shows the payload's as-of time", async ({ page }) => {
    await page.goto("/trading/execution");
    // source("seed") carries as_of 2026-09-19T12:00:00Z.
    await expect(page.getByTestId("source-note-as-of").first()).toHaveText(
      "as of 19/09/2026, 12:00 UTC",
    );
  });

  test("a figure's as-of is carried to its hover title", async ({ page }) => {
    await page.route(`${API}/api/trading/trades*`, (route: Route) => {
      const payload = tradesPage();
      payload.items = payload.items.map((item) => ({
        ...item,
        net_pnl: figure(item.net_pnl.value, item.provenance, "GBP", {
          as_of: "2026-09-18T14:05:00Z",
        }),
      }));
      return route.fulfill({ json: payload });
    });
    await page.goto("/trading/execution");
    const value = page.locator("tbody tr").first().getByTestId("figure-value").nth(1);
    await expect(value).toHaveAttribute("title", "as of 18/09/2026, 14:05 UTC");
  });
});

test.describe("time labels in a non-UTC browser", () => {
  // London in September is UTC+1, so a timestamp misread as local shifts by an hour.
  test.use({ timezoneId: "Europe/London" });

  test("a zone-less timestamp is read as UTC, not browser-local", async ({ page }) => {
    await mockApi(page);
    await page.route(`${API}/api/trading/trades*`, (route: Route) => {
      const payload = tradesPage();
      payload.items = payload.items.map((item) => ({ ...item, exit_time: "2026-09-18T14:00:00" }));
      return route.fulfill({ json: payload });
    });
    await page.goto("/trading/execution");
    await expect(page.locator("tbody tr").first()).toContainText("18/09/2026, 14:00 UTC");
  });

  test("a zoned timestamp is still rendered in UTC", async ({ page }) => {
    await mockApi(page);
    await page.goto("/trading/execution");
    await expect(page.locator("tbody tr").first()).toContainText("18/09/2026, 14:00 UTC");
  });
});
