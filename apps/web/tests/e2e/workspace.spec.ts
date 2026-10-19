import { expect, test, type Page } from "@playwright/test";
import { API, mockShell, source } from "./fixtures";
import { mockWorkspace, presetLayout } from "./workspace-fixtures";

/**
 * Wave 2A · Workspace (WS, F10): tiling from the typed registry, split, close,
 * maximise and restore, Ctrl/⌘+digit and Tab across headers, TWS-style link
 * groups, and server-side named layouts (unsaved marker, debounced autosave,
 * 409 on a stale version). Layout writes go to an in-memory model of the API.
 */

test.use({ viewport: { width: 1440, height: 900 } });

async function open(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByTestId("workspace")).toBeVisible();
  await expect(page.getByTestId("ws-canvas")).toBeVisible();
}

const trade = (id: string, instrument: string) => ({
  trade_id: id,
  strategy_id: "donchian_breakout_atr",
  instrument,
  direction: "long",
  provenance: "paper",
  entry_time: "2026-10-18T08:00:00Z",
  exit_time: "2026-10-18T10:00:00Z",
  exit_reason: "take_profit",
  size: { value: 1, provenance: "paper", unit: "lots", as_of: null, sample_size: null, caveats: [], estimated: false },
  entry_price: { value: 1.1, provenance: "paper", unit: "", as_of: null, sample_size: null, caveats: [], estimated: false },
  exit_price: { value: 1.101, provenance: "paper", unit: "", as_of: null, sample_size: null, caveats: [], estimated: false },
  gross_pnl: { value: 10, provenance: "paper", unit: "GBP", as_of: null, sample_size: null, caveats: [], estimated: false },
  costs: { value: 1, provenance: "paper", unit: "GBP", as_of: null, sample_size: null, caveats: [], estimated: false },
  net_pnl: { value: 9, provenance: "paper", unit: "GBP", as_of: null, sample_size: null, caveats: [], estimated: false },
  r_multiple: { value: 0.9, provenance: "paper", unit: "R", as_of: null, sample_size: null, caveats: [], estimated: false },
});

test.beforeEach(async ({ page }) => {
  await mockShell(page);
});

test("a new name opens the FX monitoring preset, unsaved, one tile per panel", async ({ page }) => {
  await mockWorkspace(page);
  await open(page, "/workspace?layout=Fresh");
  await expect(page.getByTestId("ws-save-status")).toHaveText("NOT SAVED YET");
  await expect(page.getByTestId("ws-tile")).toHaveCount(5);
  await expect(page.locator('[data-testid="ws-tile"][data-kind="chart"]')).toHaveAttribute("data-group", "amber");
  await expect(page.locator('[data-kind="chart"] [data-testid="ws-tile-symbol"]')).toHaveText("EURUSD");
});

test("split marks the layout unsaved and autosaves it as a new version", async ({ page }) => {
  const mock = await mockWorkspace(page);
  await open(page, "/workspace?layout=Desk");
  await page.locator('[data-kind="news"] [data-testid="ws-split-h"]').click();
  await expect(page.getByTestId("ws-tile")).toHaveCount(6);
  await expect(page.getByTestId("ws-save-status")).toContainText("UNSAVED");
  await expect(page.getByTestId("ws-save-status")).toContainText("SAVED v1", { timeout: 6_000 });
  expect(mock.writes).toHaveLength(1);
  expect(mock.writes[0]).toMatchObject({ name: "Desk", base: 0, action: "autosave" });
  // Close the new panel: the next autosave is based on v1.
  await page.locator('[data-testid="ws-tile"]').nth(3).locator('[data-testid="ws-close"]').click();
  await expect(page.getByTestId("ws-tile")).toHaveCount(5);
  await expect(page.getByTestId("ws-save-status")).toContainText("SAVED v2", { timeout: 6_000 });
  expect(mock.writes[1]).toMatchObject({ base: 1 });
});

test("double-click maximises, Esc restores; Ctrl+digit focuses and Tab walks the headers", async ({ page }) => {
  await mockWorkspace(page, { saved: { Desk: presetLayout("incident") } });
  await open(page, "/workspace?layout=Desk");
  await expect(page.getByTestId("ws-tile")).toHaveCount(4);
  await page.locator('[data-kind="fleet"] .ws-tile__head').dblclick();
  await expect(page.getByTestId("ws-tile")).toHaveCount(1);
  await expect(page.getByTestId("ws-canvas")).toHaveAttribute("data-maximised", /.+/);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("ws-tile")).toHaveCount(4);
  await page.keyboard.press("Control+2");
  await expect(page.locator('[data-kind="alerts"] [data-testid="ws-tile-title"]')).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator('[data-kind="fleet"] [data-testid="ws-tile-title"]')).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.locator('[data-kind="alerts"] [data-testid="ws-tile-title"]')).toBeFocused();
});

test("a source in a colour group retargets its destinations", async ({ page }) => {
  await page.route(`${API}/api/trading/trades*`, (route) =>
    route.fulfill({
      json: { items: [trade("t1", "GBPUSD"), trade("t2", "USDJPY")], total: 2, offset: 0, limit: 40, source: source("live"), caveats: [] },
    }),
  );
  const layout = {
    schema: "fiboki.workspace.layout/1",
    root: { type: "split", id: "s0", orientation: "horizontal", sizes: [40, 30, 30], children: [{ type: "panel", panel: "j" }, { type: "panel", panel: "c" }, { type: "panel", panel: "n" }] },
    panels: [
      { id: "j", kind: "journal", title: "", link: { group: "amber", role: "source" }, symbol: null, settings: {} },
      { id: "c", kind: "chart", title: "", link: { group: "amber", role: "destination" }, symbol: "EURUSD", settings: {} },
      { id: "n", kind: "chart", title: "Unlinked", link: { group: "none", role: "both" }, symbol: "EURUSD", settings: {} },
    ],
  };
  await mockWorkspace(page, { saved: { Linked: layout } });
  await open(page, "/workspace?layout=Linked");
  await page.getByTestId("ws-journal").getByRole("button", { name: "GBPUSD" }).click();
  await expect(page.locator('[data-panel="c"] [data-testid="ws-tile-symbol"]')).toHaveText("GBPUSD");
  await expect(page.locator('[data-panel="n"] [data-testid="ws-tile-symbol"]')).toHaveText("EURUSD");
  await expect(page.locator('[data-panel="c"] [data-testid="ws-chart"]')).toHaveAttribute("data-symbol", "GBPUSD");
});

test("a write based on a stale version is refused and the operator chooses", async ({ page }) => {
  const mock = await mockWorkspace(page, { saved: { Desk: presetLayout("research") } });
  await open(page, "/workspace?layout=Desk");
  await expect(page.getByTestId("ws-save-status")).toContainText("SAVED v1");
  mock.saveElsewhere("Desk");
  await page.locator('[data-kind="notes"] [data-testid="ws-split-v"]').click();
  await expect(page.getByTestId("ws-conflict")).toBeVisible({ timeout: 6_000 });
  await expect(page.getByTestId("ws-save-status")).toContainText("CONFLICT · server v2");
  await page.getByTestId("ws-conflict-mine").click();
  await expect(page.getByTestId("ws-save-status")).toContainText("SAVED v3");
  expect(mock.writes.at(-1)).toMatchObject({ base: 2, action: "save" });
});

test("LAYOUT <name> on the command line and F10 open the workspace", async ({ page }) => {
  await mockWorkspace(page, { saved: { Desk: presetLayout("incident") } });
  await page.goto("/");
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
  await page.getByTestId("command-line-input").fill("LAYOUT Desk");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/workspace\?layout=Desk$/);
  await expect(page.getByTestId("ws-tile")).toHaveCount(4);
  await page.goto("/");
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
  await page.keyboard.press("F10");
  await expect(page).toHaveURL(/\/workspace$/);
});

test.describe("phone width", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("tiles stack in reading order", async ({ page }) => {
    await mockWorkspace(page, { saved: { Desk: presetLayout("incident") } });
    await open(page, "/workspace?layout=Desk");
    await expect(page.getByTestId("ws-stack")).toBeVisible();
    await expect(page.getByTestId("ws-tile")).toHaveCount(4);
    const scroll = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(scroll).toBeLessThanOrEqual(1);
  });
});
