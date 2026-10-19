import { expect, test } from "@playwright/test";
import { mockShell } from "./fixtures";
import { mockMonitors, mockWorkspace, presetLayout } from "./workspace-fixtures";

/** Wave 2A · News monitor (NWS, /markets/news): the backend-written headline fixture. */

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await mockShell(page);
  await mockMonitors(page);
});

test("headlines newest first by our clock, with currency, source and chart links", async ({ page }) => {
  await page.goto("/markets/news");
  const items = page.getByTestId("news-item");
  await expect(items).toHaveCount(5);
  await expect(items.first()).toContainText("Dollar steadies");
  await expect(items.first()).toHaveAttribute("data-source", "finnhub");
  const fed = page.locator('[data-testid="news-item"][data-source="fed_rss"]');
  await expect(fed).toContainText("USD");
  await expect(fed.getByTestId("news-chart").first()).toHaveAttribute("href", /\/markets\/[A-Z]*USD[A-Z]*$/);
  await expect(page.getByTestId("panel-stamp")).toContainText("RECORDING");
  await expect(fed.getByTestId("news-title")).toHaveAttribute("rel", "noopener noreferrer");
});

test("EURUSD NEWS on the command line opens the monitor filtered by instrument", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
  await page.getByTestId("command-line-input").fill("EURUSD NEWS");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/markets\/news\?instrument=EURUSD/);
  await expect(page.getByTestId("news-instrument")).toContainText("EURUSD");
  await expect(page.locator('[data-testid="news-item"][data-source="boj_rss"]')).toHaveCount(0);
});

test("NWS opens the monitor; the currency filter is a server filter", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
  await page.getByTestId("command-line-input").fill("NWS");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/markets\/news$/);
  const request = page.waitForRequest((r) => r.url().includes("/api/markets/news") && r.url().includes("currency=JPY"));
  await page.getByTestId("news-ccy-JPY").click();
  await request;
  await expect(page).toHaveURL(/currency=JPY/);
});

test("in the workspace the news tile follows its amber group", async ({ page }) => {
  await mockWorkspace(page, { saved: { Desk: presetLayout("fx-monitoring") } });
  await page.goto("/workspace?layout=Desk");
  const tile = page.locator('[data-testid="ws-tile"][data-kind="news"]');
  await expect(tile.getByTestId("news-instrument")).toContainText("EURUSD");
  await expect(tile.getByTestId("news-item").first()).toBeVisible();
});
