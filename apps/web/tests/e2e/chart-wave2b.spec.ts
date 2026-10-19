import { expect, test, type Page, type Route } from "@playwright/test";
import { mockChartWave2B } from "./chart2-fixtures";
import { API, barTime, chartBar, mockChart, mockShell } from "./fixtures";

/**
 * Wave 2B: the chart workstation's replay, grid, data window, templates,
 * go-to-date and time zone, layer toolbar, zones and alerts, events,
 * snapshot, trade detail and the P1 #9 defect fixes. Every value asserted is
 * a mocked API's (tests/e2e/chart2-fixtures.ts); the browser computes none.
 */

const EURUSD = "/markets/EURUSD";

async function ready(page: Page) {
  const chart = page.getByTestId("price-chart").first();
  await expect(chart).toHaveAttribute("data-ready", "true");
  await expect(chart.locator("canvas").first()).toBeVisible();
}

test.describe("replay", () => {
  test("steps closed bar by closed bar from the API, never ahead of the cursor", async ({ page }) => {
    await mockChart(page);
    const cursor = barTime(50);
    await page.goto(`${EURUSD}?mode=replay&cursor=${encodeURIComponent(cursor)}`);
    await ready(page);
    const replay = page.getByTestId("replay");
    await expect(replay).toHaveAttribute("data-cursor", cursor);
    // 50 bars have closed by bar 50's open: 0..49.
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-bars", "50");
    await expect(page.getByTestId("replay-status")).toContainText("50 of 120 bars closed");
    await page.getByTestId("replay-step").click();
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-bars", "51");
    await page.getByTestId("replay-back").click();
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-bars", "50");
    // Keyboard: "." steps forward.
    await page.locator("body").press(".");
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-bars", "51");
    // Play advances on its own; pause stops it.
    await page.getByTestId("replay-speed").selectOption("8");
    await page.getByTestId("replay-play").click();
    await expect(page.getByTestId("chart-summary")).not.toHaveAttribute("data-bars", "51");
    await page.getByTestId("replay-play").click();
    await expect(replay).toHaveAttribute("data-playing", "false");
    await expect(page.getByTestId("chart-freshness")).toHaveAttribute("data-freshness", "replay");
  });

  test("jump to a date asks the API for that cursor; exit returns to live", async ({ page }) => {
    const mock = await mockChart(page);
    await page.goto(`${EURUSD}?mode=replay&cursor=${encodeURIComponent(barTime(50))}&run=bt_0001`);
    await ready(page);
    await page.getByTestId("replay-jump").fill(barTime(80).slice(0, 16));
    await page.getByTestId("replay-go").click();
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-bars", "80");
    await expect(page.getByTestId("replay-trades-detail")).toContainText("entered by the cursor");
    expect(mock.some((u) => u.pathname.startsWith("/api/markets/bars/"))).toBe(false);
    await page.getByTestId("replay-exit").click();
    await expect(page.getByTestId("replay")).toHaveCount(0);
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-bars", "120");
  });
});

test.describe("multi-chart grid", () => {
  test("layouts, links and a compare line, all from the API", async ({ page }) => {
    await mockChart(page);
    await page.goto("/markets/grid?layout=2x2&symbols=EURUSD,GBPUSD&cmp=,EURUSD");
    const grid = page.getByTestId("chart-grid");
    await expect(page.getByTestId("grid-cell")).toHaveCount(4);
    await expect(page.getByTestId("grid-cell").nth(1)).toHaveAttribute("data-symbol", "GBPUSD");
    await expect(page.getByTestId("grid-cell").nth(1)).toHaveAttribute("data-compare", "EURUSD");
    await expect(page.getByTestId("grid-cell").nth(1).getByTestId("grid-readout")).toContainText("EURUSD +1.19%");
    await expect(grid).toHaveAttribute("data-sync", "crosshair,time");
    await page.getByTestId("grid-sync-symbol").click();
    await expect(grid).toHaveAttribute("data-sync", "crosshair,symbol,time");
    await expect(page.getByTestId("grid-cell").nth(1)).toHaveAttribute("data-symbol", "EURUSD");
    await page.locator('[data-testid="grid-layout"] input[value="3x3"]').check({ force: true });
    await expect(page.getByTestId("grid-cell")).toHaveCount(9);
    await expect(page).toHaveURL(/layout=3x3/);
  });

  test("crosshair sync mirrors the pointer's bar on the other charts", async ({ page }) => {
    await mockChart(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/markets/grid?layout=1x2&symbols=EURUSD,GBPUSD&tfs=H1,H1");
    await expect(page.getByTestId("price-chart")).toHaveCount(2);
    await expect(page.getByTestId("price-chart").nth(1)).toHaveAttribute("data-ready", "true");
    const box = await page.getByTestId("price-chart").first().boundingBox();
    if (!box) throw new Error("no chart box");
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4);
    await expect(page.getByTestId("price-chart").nth(1)).toHaveAttribute("data-sync-index", /\d+/);
  });
});

test.describe("data window, layers, templates, go-to-date, time zone", () => {
  test("the data window lists every indicator value at the bar and pins one from the API", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("data-window-toggle").click();
    const dw = page.getByTestId("data-window");
    await expect(dw).toHaveAttribute("data-index", "119");
    await expect(page.getByTestId("dw-close")).toHaveText("1.12360");
    // All five series, the hidden state column included and marked.
    await expect(page.getByTestId("data-window-values").locator("dt")).toHaveCount(5);
    await expect(page.getByTestId("data-window-values")).toContainText("(hidden)");
    await page.getByTestId("data-window-pin").click();
    await expect(page.getByTestId("data-window-pinned")).toBeVisible();
    await expect(page.getByTestId("dw-pinned-close")).toHaveText(String(chartBar(119).c.toFixed(5)));
  });

  test("the layer toolbar hides and shows each layer, and all at once", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("layers-menu").click();
    await page.getByTestId("layer-events").click();
    await expect(page.getByTestId("price-chart")).toHaveAttribute("data-events", "hidden");
    await page.keyboard.press("Escape");
    await page.getByTestId("layer-all").click();
    await expect(page.getByTestId("layer-toolbar")).toHaveAttribute("data-on", "0");
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-series", "0");
    await page.getByTestId("layer-all").click();
    await expect(page.getByTestId("layer-toolbar")).toHaveAttribute("data-on", "8");
  });

  test("a template saves the layers server-side and applies them back", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("layer-all").click();
    await page.getByTestId("chart-templates").click();
    await expect(page.getByTestId("templates-empty")).toBeVisible();
    await page.getByTestId("template-name").fill("Bare");
    await page.getByTestId("template-save").click();
    await expect(page.getByTestId("template-row")).toHaveAttribute("data-name", "Bare");
    await page.keyboard.press("Escape");
    await page.getByTestId("layer-all").click();
    await expect(page.getByTestId("layer-toolbar")).toHaveAttribute("data-on", "8");
    await page.getByTestId("chart-templates").click();
    await page.getByTestId("template-apply").click();
    await expect(page.getByTestId("layer-toolbar")).toHaveAttribute("data-on", "0");
  });

  test("go to a date on the chart scrolls there; outside it, the window is re-centred by the API", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("goto-input").fill(barTime(20).slice(0, 16));
    await page.getByTestId("goto-go").click();
    await expect(page.getByTestId("price-chart")).toHaveAttribute("data-goto", "20");
    const centred = page.waitForRequest((r) => r.url().includes("/api/markets/bars/EURUSD") && r.url().includes("around="));
    await page.getByTestId("goto-input").fill("2026-06-01T12:00");
    await page.getByTestId("goto-go").click();
    await centred;
    await expect(page).toHaveURL(/at=2026-06-01T12%3A00%3A00Z|at=2026-06-01T12:00:00Z/);
    await expect(page.getByTestId("goto-latest")).toBeVisible();
  });

  test("the time-zone selector relabels the readout only; requests stay UTC", async ({ page }) => {
    const requests = await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await expect(page.getByTestId("readout-time")).toContainText("UTC");
    await page.getByTestId("chart-tz").selectOption("Asia/Tokyo");
    await expect(page.getByTestId("readout-time")).not.toContainText("UTC");
    await expect(page.getByTestId("price-chart")).toHaveAttribute("data-time-zone", "Asia/Tokyo");
    expect(requests.every((u) => !u.search.includes("Tokyo"))).toBe(true);
  });
});

test.describe("zones, events, snapshot, trade detail", () => {
  test("a horizontal level creates a price alert; the API decides the direction", async ({ page }, info) => {
    test.skip(info.project.name !== "desktop", "selects the level with a pointer sweep sized for desktop");
    await mockChart(page);
    const level = { id: "lvl_1", kind: "hline", points: [{ t: barTime(10), price: 1.115 }], timeframe: null, text: "", locked: false, hidden: false, fib_extensions: false };
    await page.route(
      (url) => url.pathname.startsWith("/api/markets/drawings/"),
      (route: Route) =>
        route.request().method() === "GET"
          ? route.fulfill({
              json: {
                data: { symbol: "EURUSD", owner: "joe", version: 1, updated_at: null, updated_by: "joe", drawings: [level], can_edit: true, others: [], max_drawings: 500 },
                source: { kind: "live", detail: "fixture", as_of: null },
                caveats: [],
              },
            })
          : route.fallback(),
    );
    await page.goto(EURUSD);
    await ready(page);
    await expect(page.getByTestId("drawing-status")).toHaveAttribute("data-status", "saved");
    // Select the level by keyboard-free click on its price: use the API's
    // own selection path through the drawing layer (click on the line).
    const chart = page.getByTestId("price-chart");
    await chart.evaluate((el) => el.focus());
    await page.evaluate(() => undefined);
    const post = page.waitForRequest((r) => r.url().endsWith("/api/alerts/price") && r.method() === "POST");
    // The edit bar offers "alert" once the level is selected.
    const box = await chart.boundingBox();
    if (!box) throw new Error("no chart");
    for (let y = box.y + 10; y < box.y + box.height * 0.75; y += 3) {
      await page.mouse.click(box.x + box.width * 0.4, y);
      if (await page.getByTestId("alert-from-level").isVisible()) break;
    }
    await page.getByTestId("alert-from-level").click();
    const body = (await post).postDataJSON() as { price: number; drawing_id: string; origin: string };
    expect(body).toMatchObject({ price: 1.115, drawing_id: "lvl_1", origin: "level" });
    await expect(chart).toHaveAttribute("data-alerts", "1");
  });

  test("?event=<id> centres the chart on the official calendar's event", async ({ page }) => {
    await mockChart(page);
    const centred = page.waitForRequest((r) => r.url().includes("/api/markets/bars/EURUSD") && r.url().includes("around="));
    await page.goto(`${EURUSD}?event=ev_1`);
    await centred;
    await expect(page.getByTestId("event-focus")).toContainText("HIGH · USD Non-farm payrolls");
    await expect(page.getByTestId("event-focus")).toContainText("1.1.0");
    await ready(page);
    await expect(page.getByTestId("price-chart")).toHaveAttribute("data-events", "1");
  });

  test("an unknown event says so", async ({ page }) => {
    await mockChart(page);
    await page.goto(`${EURUSD}?event=nope`);
    await expect(page.getByTestId("event-focus-error")).toContainText("unknown_event");
  });

  test("the snapshot is a PNG named and stamped by the API", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const download = page.waitForEvent("download");
    await page.getByTestId("chart-snapshot").click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("fiboki_EURUSD_H1_20261019T023000Z.png");
    await expect(page.getByTestId("snapshot-status")).toHaveAttribute("data-state", "done");
    await expect(page.getByTestId("snapshot-status")).toContainText("build deadbeef");
  });

  test("clicking a trade row scrolls the chart to it and shows MAE, MFE, R, reasons and costs from the API", async ({ page }) => {
    await mockChart(page);
    await page.goto(`${EURUSD}?run=bt_0001`);
    await ready(page);
    await page.locator('[data-testid="trade-row"][data-source="backtest"]').first().click();
    const detail = page.getByTestId("trade-detail");
    await expect(detail).toHaveAttribute("data-source", "backtest");
    await expect(detail.getByTestId("td-r")).toHaveAttribute("data-value", "2.55");
    await expect(detail.getByTestId("td-mae")).toHaveAttribute("data-value", "-6.5");
    await expect(detail.getByTestId("td-spread")).toHaveAttribute("data-value", "1");
    await expect(detail.getByTestId("td-exit-reason")).toHaveText("take profit");
    await expect(detail.getByTestId("td-entry-reason")).toContainText("not recorded");
    await expect(page.getByTestId("price-chart")).toHaveAttribute("data-selected-trade", /bt\|/);
  });
});

test.describe("defects (programme P1 #9)", () => {
  test("one tool row above the chart, the trades table fits at 1440, candles differ in lightness", async ({ page }) => {
    await mockChart(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${EURUSD}?run=bt_0001`);
    await ready(page);
    await expect(page.getByTestId("trade-counts")).toHaveAttribute("data-backtest", "3");
    const m = await page.evaluate(() => {
      const head = document.querySelector('[data-testid="chart-head"]') as HTMLElement;
      const scroll = document.querySelector(".cw-trades__scroll") as HTMLElement;
      const css = getComputedStyle(document.documentElement);
      return {
        headHeight: head.getBoundingClientRect().height,
        tableOverflow: scroll.scrollWidth - scroll.clientWidth,
        up: css.getPropertyValue("--chart-candle-up").trim(),
        down: css.getPropertyValue("--chart-candle-down").trim(),
      };
    });
    expect(m.headHeight).toBeLessThanOrEqual(40);
    expect(m.tableOverflow).toBeLessThanOrEqual(0);
    expect(m.up).not.toEqual(m.down);
    await expect(page.locator('[data-testid="trade-row"]').first().locator("td").last()).toBeVisible();
  });

  test("a one-output indicator is named once in the readout", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const atr = page.locator('[data-testid="readout-group"][data-group="atr_14|atr_14"]');
    await expect(atr.locator(".readout__key")).toHaveCount(0);
    await expect(atr.locator(".readout__group")).toHaveText("atr_14");
  });
});

test("viewers read templates but cannot save them", async ({ page }) => {
  await mockShell(page);
  await mockChartWave2B(page, { canEdit: false });
  await page.route(`${API}/api/markets/**`, (route: Route) => route.fallback());
  await mockChart(page);
  await mockChartWave2B(page, { canEdit: false });
  await page.goto(EURUSD);
  await ready(page);
  await page.getByTestId("chart-templates").click();
  await expect(page.getByTestId("template-menu")).toContainText("needs the operator role");
});
