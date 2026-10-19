import { expect, test } from "@playwright/test";
import { mockShell } from "./fixtures";
import { mockMonitors, mockWorkspace, presetLayout } from "./workspace-fixtures";

/**
 * Wave 2A · Watchlist (WL, /markets/watchlist) and the upgraded data grid:
 * rows from the backend's recorded-quote fixture, the API's verdicts and
 * conditional-format tones, sparklines, list edits, column order, server-side
 * column sets and CSV export.
 */

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await mockShell(page);
});

test("rows show the recorded quote, the API's verdict and its tones", async ({ page }) => {
  await mockMonitors(page);
  await page.goto("/markets/watchlist");
  const rows = page.getByTestId("wl-row");
  await expect(rows).toHaveCount(7);
  const chf = page.locator('[data-testid="wl-row"][data-row-id="USDCHF"]');
  await expect(chf.getByTestId("wl-state")).toContainText("STALE");
  await expect(page.locator('[data-row-id="USDCAD"]').getByTestId("wl-state")).toContainText("NO DATA");
  await expect(page.locator('[data-row-id="GBPUSD"] td[data-column="spread"]')).toHaveAttribute("data-tone", "warn");
  await expect(page.locator('[data-row-id="EURUSD"] td[data-column="change"]')).toHaveAttribute("data-tone", "rise");
  await expect(page.locator('[data-row-id="EURUSD"] [data-testid="sparkline"]')).toHaveAttribute("data-points", /[1-9]/);
  await expect(page.getByTestId("panel-stamp").first()).toContainText("as of 09:00:00");
  await page.getByTestId("grid-format").click();
  await expect(page.getByTestId("grid-format-rules")).toContainText("Spread at least twice");
});

test("editing a list writes it with its base version; an unknown symbol cannot be added", async ({ page }) => {
  const mock = await mockMonitors(page);
  await page.goto("/markets/watchlist");
  await expect(page.getByTestId("wl-row")).toHaveCount(7);
  await page.getByTestId("wl-add").fill("NOTREAL");
  await expect(page.getByTestId("wl-add-go")).toBeDisabled();
  await page.getByTestId("wl-remove").selectOption("GBPUSD");
  await expect.poll(() => mock.watchlistWrites.length).toBe(1);
  expect(mock.watchlistWrites[0]?.name).toBe("Majors");
  expect(mock.watchlistWrites[0]?.body).toMatchObject({ base_version: 0 });
  expect(mock.watchlistWrites[0]?.body.symbols as string[]).not.toContain("GBPUSD");
  expect(mock.watchlistWrites[0]?.body.symbols as string[]).toContain("EURUSD");
});

test("columns reorder, save as a server-side set, and export in that order", async ({ page }) => {
  const mock = await mockMonitors(page);
  await page.goto("/markets/watchlist");
  await expect(page.getByTestId("wl-row")).toHaveCount(7);
  await page.getByTestId("grid-columns").click();
  await page.getByTestId("grid-up-spread").click();
  const order = () => page.locator('[data-testid="wl-grid"] th').evaluateAll((ths) => ths.map((th) => th.getAttribute("data-column")));
  await expect.poll(async () => { const o = await order(); return o.indexOf("spread") < o.indexOf("ask"); }).toBe(true);
  await page.getByTestId("grid-set-name").fill("Tight");
  await page.getByTestId("grid-set-save").click();
  await expect.poll(() => mock.columnSetWrites.length).toBe(1);
  const write = mock.columnSetWrites[0] as { base_version: number; sets: { name: string; order: string[] }[] };
  expect(write.base_version).toBe(0);
  expect(write.sets[0]?.name).toBe("Tight");
  const saved = write.sets[0]?.order ?? [];
  expect(saved.indexOf("spread")).toBeLessThan(saved.indexOf("ask"));
  await expect(page.getByTestId("grid-sets")).toContainText("Tight");
  await page.keyboard.press("Escape");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("grid-export").click()]);
  const path = await download.path();
  const text = path ? (await import("node:fs")).readFileSync(path, "utf8") : "";
  const header = text.split("\r\n")[0] ?? "";
  const cols = header.split(",");
  expect(cols.indexOf("spread")).toBeLessThan(cols.indexOf("ask"));
  expect(header).toContain("source_kind");
});

test("in the workspace a watchlist row retargets its amber group", async ({ page }) => {
  await mockMonitors(page);
  await mockWorkspace(page, { saved: { Desk: presetLayout("fx-monitoring") } });
  await page.goto("/workspace?layout=Desk");
  const wl = page.locator('[data-testid="ws-tile"][data-kind="watchlist"]');
  await expect(wl.getByTestId("wl-row")).toHaveCount(7);
  await wl.locator('[data-row-id="GBPUSD"]').getByTestId("wl-symbol").click();
  await expect(page.locator('[data-kind="chart"] [data-testid="ws-tile-symbol"]')).toHaveText("GBPUSD");
});
