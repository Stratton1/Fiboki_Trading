import { expect, test, type Page } from "@playwright/test";
import {
  backtestTrades,
  barTime,
  chartBar,
  chartBars,
  chartOverlays,
  figure,
  instrumentsPage,
  mockChart,
  mockDrawings,
  mockShell,
  source,
} from "./fixtures";

/**
 * The chart workstation's backtest trade overlay and operator drawings
 * (components/charts/ChartWorkbench.tsx, routers/chart.py).
 *
 * The canvas is not read: the page exposes what it drew as DOM (trade counts,
 * segment counts, the trades table, the visible bar range, the selected
 * trade's marker position, the drawing store's save state), and every figure
 * there must be the mocked API's. Drawings are checked through what the page
 * SENT to the mocked store, which is what persists.
 */

const EURUSD = "/markets/EURUSD";

async function ready(page: Page) {
  const chart = page.getByTestId("price-chart");
  await expect(chart).toHaveAttribute("data-ready", "true");
  await expect(page.getByTestId("chart-workbench")).toBeVisible();
  await expect(page.getByTestId("drawing-status")).not.toHaveAttribute("data-status", "loading");
  // On a phone the chart sits below the header and legend: bring it to the
  // middle of the viewport, clear of the fixed status bar, before pointing at it.
  await chart.evaluate((el) => el.scrollIntoView({ block: "center" }));
}

/** A point inside the price pane, as fractions of the chart box (the price pane is the top ~65%). */
async function at(page: Page, fx: number, fy: number) {
  const box = await page.getByTestId("price-chart").boundingBox();
  if (!box) throw new Error("no chart box");
  return { x: box.x + box.width * fx, y: box.y + box.height * fy };
}

async function drag(page: Page, from: [number, number], to: [number, number]) {
  const a = await at(page, ...from);
  const b = await at(page, ...to);
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 4 });
  await page.mouse.move(b.x, b.y, { steps: 4 });
  await page.mouse.up();
}

test.describe("backtest trades on the chart", () => {
  test("picking a run draws its trades, markers and SL/TP segments, with provenance", async ({ page }) => {
    const requests = await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const picker = page.getByTestId("run-picker");
    await expect(picker.locator("option")).toHaveCount(3);
    await expect(page.getByTestId("trade-counts")).toHaveAttribute("data-backtest", "0");

    const read = page.waitForRequest((r) => r.url().includes("/api/markets/backtests/EURUSD/bt_0001/trades"));
    await picker.selectOption("bt_0001");
    const url = new URL((await read).url());
    expect(url.searchParams.get("timeframe")).toBe("H1");
    expect(url.searchParams.get("from")).toBe(barTime(0));
    expect(url.searchParams.get("to")).toBe(barTime(119));
    await expect(page).toHaveURL(/run=bt_0001/);

    const counts = page.getByTestId("trade-counts");
    await expect(counts).toHaveAttribute("data-backtest", "3");
    await expect(counts).toHaveAttribute("data-paper", "1");
    // SL + TP on two trades, and on the third: SL, the final (moved) stop and two targets.
    await expect(counts).toHaveAttribute("data-segments", "8");
    const prov = page.getByTestId("run-provenance");
    await expect(prov).toHaveAttribute("data-run", "bt_0001");
    await expect(prov).toContainText("run bt_0001");
    await expect(prov).toContainText("gate fail");
    await expect(prov.getByTestId("provenance-chip")).toHaveAttribute("data-provenance", "backtest");

    const rows = page.locator('[data-testid="trade-row"][data-source="backtest"]');
    await expect(rows).toHaveCount(3);
    const t1 = rows.first();
    await expect(t1).toHaveAttribute("data-entry-index", "30");
    await expect(t1).toHaveAttribute("data-exit-index", "40");
    await expect(t1).toContainText("+£25.50");
    await expect(t1).toContainText("BT");
    await expect(rows.nth(1)).toContainText("−£12.00");
    expect(requests.some((u) => u.pathname === "/api/markets/backtests/EURUSD")).toBe(true);
  });

  test("a run filed before stops were recorded says so, and draws no invented level", async ({ page }) => {
    await mockChart(page);
    await page.goto(`${EURUSD}?run=bt_old`);
    await ready(page);
    await expect(page.getByTestId("run-stale")).toContainText("stale engine");
    await expect(page.getByTestId("run-no-stops")).toBeVisible();
    const counts = page.getByTestId("trade-counts");
    await expect(counts).toHaveAttribute("data-backtest", "1");
    await expect(counts).toHaveAttribute("data-segments", "0");
    const row = page.locator('[data-testid="trade-row"][data-source="backtest"]');
    // SL and TP share one stacked column (Wave 2B, the 1440 px fit): both "—".
    await expect(row.locator("td").nth(5).locator("span")).toHaveText(["—", "—"]);
    await expect(page.getByTestId("trades-panel")).toContainText("filed before the engine recorded");
  });

  test("paper, backtest or both: the toggle filters the chart and the table", async ({ page }) => {
    await mockChart(page);
    await page.goto(`${EURUSD}?run=bt_0001`);
    await ready(page);
    const rows = page.getByTestId("trade-row");
    await expect(rows).toHaveCount(4);
    await page.getByTestId("trade-source").getByText("Paper", { exact: true }).click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toHaveAttribute("data-source", "paper");
    await page.getByTestId("trade-source").getByText("Backtest", { exact: true }).click();
    await expect(rows).toHaveCount(3);
    await expect(page.locator('[data-testid="trade-row"][data-source="paper"]')).toHaveCount(0);
    await page.getByTestId("trade-source").getByText("Both", { exact: true }).click();
    await expect(rows).toHaveCount(4);
  });

  test("a table row brings its trade into view; hovering it shows the API's figures", async ({ page }) => {
    await mockChart(page);
    await page.goto(`${EURUSD}?run=bt_0001`);
    await ready(page);
    await expect(page.getByTestId("trade-counts")).toHaveAttribute("data-backtest", "3");
    const chart = page.getByTestId("price-chart");
    // Zoom in at the right edge so the first backtest trade (bars 30 to 40) is off screen.
    const mid = await at(page, 0.9, 0.3);
    await page.mouse.move(mid.x, mid.y);
    for (let i = 0; i < 6; i += 1) await page.mouse.wheel(0, -400);
    await expect
      .poll(async () => Number(await chart.getAttribute("data-range-from")))
      .toBeGreaterThan(45);

    const row = page.locator('[data-testid="trade-row"][data-key="bt|bt_0001|bt_t1"]');
    await row.click();
    await expect(row).toHaveAttribute("aria-selected", "true");
    await expect
      .poll(async () => Number(await chart.getAttribute("data-range-from")))
      .toBeLessThanOrEqual(30);
    expect(Number(await chart.getAttribute("data-range-to"))).toBeGreaterThanOrEqual(40);
    await expect(chart).toHaveAttribute("data-selected-trade", "bt|bt_0001|bt_t1");

    // The row may have scrolled the page (a phone stacks the table below the chart).
    await chart.evaluate((el) => el.scrollIntoView({ block: "center" }));
    const entry = await chart.getAttribute("data-selected-entry");
    expect(entry).toMatch(/^\d+,\d+$/);
    const [x, y] = (entry as string).split(",").map(Number) as [number, number];
    const box = await chart.boundingBox();
    if (!box) throw new Error("no box");
    await page.mouse.move(box.x + x + 1, box.y + y);
    const tip = page.getByTestId("trade-tooltip");
    await expect(tip).toBeVisible();
    await expect(tip).toHaveAttribute("data-trade", "bt_t1");
    await expect(tip).toHaveAttribute("data-source", "backtest");
    await expect(tip).toContainText("+£25.50");
    await expect(tip).toContainText("take profit");
    await expect(tip).toContainText("Backtest run bt_0001");

    // Keyboard: rows are reachable and Enter picks one.
    await row.focus();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(page.locator('[data-testid="trade-row"][data-key="bt|bt_0001|bt_t2"]')).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

test.describe("drawing tools", () => {
  const cases: { tool: string; points: [[number, number], [number, number]] | [[number, number]] }[] = [
    { tool: "trend", points: [[0.3, 0.4], [0.55, 0.2]] },
    { tool: "ray", points: [[0.3, 0.45], [0.5, 0.3]] },
    { tool: "rect", points: [[0.35, 0.2], [0.5, 0.35]] },
    { tool: "fib", points: [[0.4, 0.45], [0.6, 0.15]] },
    { tool: "hline", points: [[0.5, 0.25]] },
    { tool: "vline", points: [[0.45, 0.3]] },
  ];
  for (const c of cases) {
    test(`${c.tool}: drawn on the chart and saved as (time, price) anchors`, async ({ page }) => {
      await mockChart(page);
      const sets = await mockDrawings(page);
      await page.goto(EURUSD);
      await ready(page);
      await page.getByTestId(`tool-${c.tool}`).click();
      await expect(page.getByTestId(`tool-${c.tool}`)).toHaveAttribute("aria-pressed", "true");
      if (c.points.length === 2) {
        await drag(page, c.points[0], c.points[1]);
      } else {
        const p = await at(page, ...c.points[0]);
        await page.mouse.click(p.x, p.y);
      }
      await expect(page.getByTestId("drawing-status")).toHaveAttribute("data-status", "saved");
      await expect(page.getByTestId("drawing-status")).toContainText("1 drawing · saved v1");
      const set = sets.get("EURUSD");
      const saved = set?.drawings as { kind: string; points: { t: string; price: number }[]; timeframe: string | null }[];
      expect(saved).toHaveLength(1);
      expect(saved[0]?.kind).toBe(c.tool);
      expect(saved[0]?.points).toHaveLength(c.points.length);
      for (const point of saved[0]?.points ?? []) {
        expect(point.t).toMatch(/^2026-09-\d\dT\d\d:00:00Z$/);
        expect(Number.isFinite(point.price)).toBe(true);
      }
      expect(saved[0]?.timeframe).toBeNull();
      // A one-shot tool hands back to select.
      await expect(page.getByTestId("tool-select")).toHaveAttribute("aria-pressed", "true");
    });
  }

  test("text note: placed with a click, typed, saved; Escape cancels another", async ({ page }) => {
    await mockChart(page);
    const sets = await mockDrawings(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("tool-text").click();
    const p = await at(page, 0.4, 0.3);
    await page.mouse.click(p.x, p.y);
    const input = page.getByTestId("note-input");
    await expect(input).toBeFocused();
    await input.fill("Watch the 1.1150 shelf");
    await input.press("Enter");
    await expect(page.getByTestId("drawing-status")).toContainText("1 drawing · saved v1");
    const saved = sets.get("EURUSD")?.drawings as { kind: string; text: string }[];
    expect(saved[0]).toMatchObject({ kind: "text", text: "Watch the 1.1150 shelf" });

    await page.getByTestId("tool-text").click();
    await page.mouse.click(p.x + 40, p.y + 40);
    await page.getByTestId("note-input").press("Escape");
    await expect(page.getByTestId("note-editor")).toHaveCount(0);
    expect(sets.get("EURUSD")?.writes).toHaveLength(1);
  });

  test("measure: price, percent and bars read out, never saved", async ({ page }) => {
    await mockChart(page);
    const sets = await mockDrawings(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("tool-measure").click();
    await drag(page, [0.3, 0.45], [0.6, 0.2]);
    const readout = page.getByTestId("measure-readout");
    await expect(readout).toContainText(/Measured \+[\d.]+ \(\+\d+\.\d\d%\) over \d+ bars/);
    await expect(readout).toContainText("not saved");
    expect(sets.get("EURUSD")?.writes ?? []).toHaveLength(0);
    await page.keyboard.press("Escape");
    await expect(readout).toHaveText("");
  });

  test("select, move and delete; undo and redo are saved as versions", async ({ page }) => {
    await mockChart(page);
    const sets = await mockDrawings(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("tool-hline").click();
    const p = await at(page, 0.5, 0.3);
    await page.mouse.click(p.x, p.y);
    await expect(page.getByTestId("drawing-status")).toContainText("saved v1");
    const before = (sets.get("EURUSD")?.drawings as { points: { price: number }[] }[])[0]?.points[0]?.price;

    // Grab the line and move it down.
    await page.mouse.move(p.x, p.y);
    await page.mouse.down();
    await page.mouse.move(p.x, p.y + 30, { steps: 5 });
    await page.mouse.up();
    await expect(page.getByTestId("drawing-status")).toContainText("saved v2");
    const after = (sets.get("EURUSD")?.drawings as { points: { price: number }[] }[])[0]?.points[0]?.price;
    expect(after).toBeLessThan(before as number);

    await page.keyboard.press("Delete");
    await expect(page.getByTestId("drawing-status")).toContainText("0 drawings · saved v3");
    await page.keyboard.press("Control+z");
    await expect(page.getByTestId("drawing-status")).toContainText("1 drawing · saved v4");
    await page.keyboard.press("Control+Shift+z");
    await expect(page.getByTestId("drawing-status")).toContainText("0 drawings · saved v5");
    expect(sets.get("EURUSD")?.writes.map((w) => w.method)).toEqual(["PUT", "PUT", "PUT", "PUT", "PUT"]);
  });

  test("drawings persist across a reload, and are anchored by time on another timeframe", async ({ page }) => {
    await mockChart(page);
    const sets = await mockDrawings(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("tool-trend").click();
    await drag(page, [0.3, 0.4], [0.55, 0.2]);
    await expect(page.getByTestId("drawing-status")).toContainText("1 drawing · saved v1");
    const saved = JSON.stringify(sets.get("EURUSD")?.drawings);

    await page.reload();
    await ready(page);
    await expect(page.getByTestId("drawing-status")).toContainText("1 drawing · saved v1");
    await page.getByTestId("chart-timeframe").getByRole("radio", { name: "H4" }).click();
    await ready(page);
    // Same set, unchanged by the switch: anchors are times and prices, not bars.
    await expect(page.getByTestId("drawing-status")).toContainText("1 drawing · saved v1");
    expect(JSON.stringify(sets.get("EURUSD")?.drawings)).toBe(saved);
  });

  test("a stale version is refused (409): nothing overwritten, the newer set is shown", async ({ page }) => {
    await mockChart(page);
    const sets = await mockDrawings(page);
    await page.goto(EURUSD);
    await ready(page);
    // Another window saves a set behind this page's back.
    const store = sets.get("EURUSD");
    if (!store) throw new Error("no store");
    store.version = 7;
    store.drawings = [
      { id: "other", kind: "hline", points: [{ t: barTime(10), price: 1.105 }], timeframe: null, text: "", locked: false, hidden: false, fib_extensions: false },
    ];
    await page.getByTestId("tool-hline").click();
    const p = await at(page, 0.5, 0.3);
    await page.mouse.click(p.x, p.y);
    await expect(page.getByTestId("drawing-status")).toHaveAttribute("data-status", "saved");
    await expect(page.getByTestId("drawing-status")).toContainText("1 drawing · saved v7");
    expect(store.drawings).toHaveLength(1);
    expect((store.drawings[0] as { id: string }).id).toBe("other");
  });

  test("keyboard shortcuts pick tools, hide and lock", async ({ page }) => {
    await mockChart(page);
    const sets = await mockDrawings(page);
    await page.goto(EURUSD);
    await ready(page);
    for (const [key, tool] of [
      ["Alt+t", "trend"],
      ["Alt+r", "ray"],
      ["Alt+v", "vline"],
      ["Alt+b", "rect"],
      ["Alt+f", "fib"],
      ["Alt+n", "text"],
      ["Alt+m", "measure"],
      ["Alt+h", "hline"],
    ] as const) {
      await page.keyboard.press(key);
      await expect(page.getByTestId(`tool-${tool}`)).toHaveAttribute("aria-pressed", "true");
    }
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("tool-select")).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Alt+h");
    const p = await at(page, 0.5, 0.3);
    await page.mouse.click(p.x, p.y);
    await expect(page.getByTestId("drawing-status")).toContainText("saved v1");
    await page.keyboard.press("Alt+Shift+h");
    await expect(page.getByTestId("drawing-hide")).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Alt+Shift+h");
    await expect(page.getByTestId("drawing-hide")).toHaveAttribute("aria-pressed", "false");
    // The new line is selected: Alt Shift L locks it, and a locked line cannot be deleted.
    await page.keyboard.press("Alt+Shift+l");
    await expect(page.getByTestId("drawing-status")).toContainText("saved v2");
    expect((sets.get("EURUSD")?.drawings as { locked: boolean }[])[0]?.locked).toBe(true);
    await page.keyboard.press("Delete");
    await expect(page.getByTestId("drawing-delete")).toHaveAttribute("aria-disabled", "true");
    expect(sets.get("EURUSD")?.drawings).toHaveLength(1);
  });

  test("a viewer sees drawings but cannot draw", async ({ page }) => {
    await mockChart(page);
    await mockShell(page, { operator: { role: "viewer", display_name: "Guest", can_draw: false, can_promote: false, can_arm_kill_switch: false } });
    const sets = await mockDrawings(page, {
      canEdit: false,
      others: [
        {
          owner: "tom",
          version: 3,
          updated_at: "2026-10-18T09:00:00Z",
          drawings: [
            { id: "t1", kind: "hline", points: [{ t: barTime(40), price: 1.11 }], timeframe: null, text: "", locked: false, hidden: false, fib_extensions: false },
          ],
        },
      ],
    });
    await page.goto(EURUSD);
    await ready(page);
    const rail = page.getByTestId("drawing-toolbar");
    await expect(rail).toHaveAttribute("data-can-edit", "false");
    await expect(page.getByTestId("tool-trend")).toHaveAttribute("aria-disabled", "true");
    await expect(page.getByTestId("tool-trend")).toHaveAttribute("title", /viewer/);
    await expect(page.getByTestId("drawing-status")).toContainText("view only");
    // aria-disabled keeps the button focusable (its reason stays reachable); a click does nothing.
    await page.getByTestId("tool-trend").click({ force: true });
    await expect(page.getByTestId("tool-select")).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Alt+t");
    await expect(page.getByTestId("tool-select")).toHaveAttribute("aria-pressed", "true");
    await drag(page, [0.3, 0.4], [0.55, 0.2]);
    expect(sets.get("EURUSD")?.writes ?? []).toHaveLength(0);
    // Measuring is display-only, so a viewer may.
    await expect(page.getByTestId("tool-measure")).toHaveAttribute("aria-disabled", "false");
  });
});

test.describe("crypto and phone", () => {
  test("BTCGBP opens 24/7, weekend bars and all; the crypto paper session's trades show, labelled", async ({
    page,
  }) => {
    await mockChart(page, {
      // H1 bars from Friday 9 Oct 2026 through the weekend: no gap.
      bars: () => {
        const env = chartBars({ symbol: "BTCGBP", version: "dsv_btcgbp_h1_0001" });
        env.data.bars = env.data.bars.map((b, i) => ({
          ...b,
          t: barTime(i, "2026-10-09T12:00:00Z"),
          o: 50_000 + i * 10,
          h: 50_040 + i * 10,
          l: 49_970 + i * 10,
          c: 50_020 + i * 10,
        }));
        return env;
      },
      overlays: () => {
        const env = chartOverlays();
        const fill = (t: string, role: string, side: string, price: number, pnl: number | null) => ({
          kind: "fill",
          t,
          role,
          side,
          price: figure(price, "paper"),
          trade_id: "t_btc",
          strategy_id: "donchian_breakout_atr__ccore1",
          session_id: "forward-paper_forward_crypto_v1",
          session_label: "paper_forward_crypto_v1",
          exit_reason: role === "exit" ? "take_profit" : null,
          net_pnl: pnl === null ? null : figure(pnl, "paper", "GBP"),
          provenance: "paper",
          dataset_version_id: null,
          source: { kind: "paper_journal", detail: "session forward-paper_forward_crypto_v1 (H4), forward session paper_forward_crypto_v1, live feed" },
        });
        env.data.symbol = "BTCGBP";
        env.data.signals = [];
        env.data.series = [];
        env.data.regimes = [];
        env.data.levels = [];
        // A Saturday entry and a Sunday exit.
        env.data.fills = [
          fill(barTime(20, "2026-10-09T12:00:00Z"), "entry", "buy", 50_200, null),
          fill(barTime(40, "2026-10-09T12:00:00Z"), "exit", "sell", 50_410, 41.5),
        ];
        return env;
      },
    });
    await page.route("**/api/markets/instruments", (route) => {
      const page0 = instrumentsPage();
      page0.items.push({
        ...page0.items[0],
        symbol: "BTCGBP",
        asset_class: "crypto",
        base: "BTC",
        quote: "GBP",
        trading_hours: "24/7",
        pip_size: figure(0.01, "backtest"),
        venue: "coinbase",
        shortable: false,
      } as (typeof page0.items)[number]);
      return route.fulfill({ json: { ...page0, source: source("live") } });
    });
    await page.goto("/markets/BTCGBP");
    await ready(page);
    await expect(page.getByTestId("chart-symbol")).toHaveText("BTCGBP");
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-bars", "120");
    await expect(page.getByTestId("instrument-hours")).toContainText("24/7");
    await expect(page.getByTestId("instrument-hours")).toContainText("long only");
    const row = page.locator('[data-testid="trade-row"][data-source="paper"]');
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("10 Oct 08:00"); // Saturday
    await expect(row).toContainText("+£41.50");
    await expect(row).toHaveAttribute("data-key", "paper|forward-paper_forward_crypto_v1|t_btc");
    // Saturday and Sunday bars are on the axis: the readout walks through them.
    const chart = page.getByTestId("price-chart");
    await chart.focus();
    await page.keyboard.press("Home");
    for (let i = 0; i < 24; i += 1) await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("crosshair-readout")).toContainText("Sat 10 Oct 2026 12:00 UTC");
    expect(chartBar(0).t).toBeTruthy();
  });

  test("phone: tools, chart, run picker and table stack; nothing scrolls sideways", async ({ page }, info) => {
    test.skip(info.project.name !== "mobile", "phone layout");
    await mockChart(page);
    await page.goto(`${EURUSD}?run=bt_0001`);
    await ready(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    const rail = await page.getByTestId("drawing-toolbar").boundingBox();
    const chart = await page.getByTestId("price-chart").boundingBox();
    const picker = await page.getByTestId("run-picker").boundingBox();
    const table = await page.getByTestId("trades-panel").boundingBox();
    if (!rail || !chart || !picker || !table) throw new Error("missing box");
    expect(chart.width).toBeLessThanOrEqual(390);
    expect(chart.height).toBeGreaterThan(250);
    expect(rail.y).toBeLessThan(chart.y);
    expect(chart.y).toBeLessThan(picker.y);
    expect(picker.y).toBeLessThan(table.y);
    expect(picker.x + picker.width).toBeLessThanOrEqual(391);
  });
});

test.describe("performance (C16)", () => {
  test("2,000 bars and 400 backtest trades: pan and zoom stay smooth, and nothing shifts", async ({ page }, info) => {
    test.skip(info.project.name !== "desktop", "measured once, at desktop size");
    await page.addInitScript(() => {
      const w = window as unknown as { __perf: { longTasks: number[]; shifts: number } };
      w.__perf = { longTasks: [], shifts: 0 };
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) w.__perf.longTasks.push(e.duration);
      }).observe({ type: "longtask", buffered: true });
      new PerformanceObserver((list) => {
        for (const e of list.getEntries() as (PerformanceEntry & { value: number; hadRecentInput: boolean })[]) {
          if (!e.hadRecentInput) w.__perf.shifts += e.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
    });
    const COUNT = 2_000;
    await mockChart(page, {
      bars: () => chartBars({ count: COUNT }),
      overlays: () => chartOverlays({ count: COUNT }),
    });
    await page.route(
      (url) => url.pathname.startsWith("/api/markets/backtests/EURUSD/bt_0001/trades"),
      (route) => {
        const one = backtestTrades("bt_0001");
        const template = one.data.trades[0] as Record<string, unknown>;
        const trades = Array.from({ length: 400 }, (_, k) => {
          const entry = 5 + k * 4;
          return {
            ...template,
            trade_id: `p_${k}`,
            direction: k % 2 === 0 ? "long" : "short",
            entry_time: barTime(entry),
            exit_time: barTime(entry + 3),
            entry_price: figure(chartBar(entry).o, "backtest"),
            exit_price: figure(chartBar(entry + 3).c, "backtest"),
            net_pnl: figure(k % 3 === 0 ? -5 : 7, "backtest", "GBP"),
          };
        });
        return route.fulfill({ json: { ...one, data: { ...one.data, trades } } });
      },
    );
    const started = Date.now();
    await page.goto(`${EURUSD}?run=bt_0001`);
    await ready(page);
    await expect(page.getByTestId("trade-counts")).toHaveAttribute("data-backtest", "400");
    const loadMs = Date.now() - started;

    // Pan right to left across the chart, then zoom out and in, counting frames.
    const a = await at(page, 0.7, 0.3);
    const b = await at(page, 0.2, 0.3);
    await page.evaluate(() => {
      const w = window as unknown as { __frames: number[]; __raf: boolean };
      w.__frames = [];
      w.__raf = true;
      const tick = (t: number) => {
        w.__frames.push(t);
        if (w.__raf) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    for (let pass = 0; pass < 3; pass += 1) {
      await page.mouse.move(a.x, a.y);
      await page.mouse.down();
      await page.mouse.move(b.x, b.y, { steps: 30 });
      await page.mouse.up();
    }
    for (let i = 0; i < 5; i += 1) await page.mouse.wheel(0, 300);
    for (let i = 0; i < 5; i += 1) await page.mouse.wheel(0, -300);
    const result = await page.evaluate(() => {
      const w = window as unknown as {
        __frames: number[];
        __raf: boolean;
        __perf: { longTasks: number[]; shifts: number };
      };
      w.__raf = false;
      const gaps = w.__frames.slice(1).map((t, i) => t - (w.__frames[i] as number)).sort((x, y) => x - y);
      return {
        frames: gaps.length,
        median: gaps.length > 0 ? (gaps[Math.floor(gaps.length / 2)] as number) : Number.NaN,
        p95: gaps.length > 0 ? (gaps[Math.floor(gaps.length * 0.95)] as number) : Number.NaN,
        longest: Math.max(0, ...w.__perf.longTasks),
        shifts: w.__perf.shifts,
      };
    });
    test.info().annotations.push({
      type: "perf",
      description: `load ${loadMs} ms; ${result.frames} frames, median ${result.median.toFixed(1)} ms, p95 ${result.p95.toFixed(1)} ms; longest task ${result.longest.toFixed(0)} ms; CLS ${result.shifts.toFixed(4)}`,
    });
    // 60 fps is a 16.7 ms frame: the median must be one frame. Measured alone
    // on the 2-CPU CI box (2026-10-18): median 16.7 ms, p95 16.8 ms, longest
    // task 114 to 178 ms. The tail bound is loose because the box is shared
    // with other workers; the annotation carries the measured numbers.
    expect(result.median).toBeLessThan(20);
    expect(result.p95).toBeLessThan(100);
    // The chart engine and the tools arrive after first paint without moving
    // the page: CLS inside the "good" bound (0.1). Measured 2026-10-18 at
    // 1280 px: 0.013 to 0.017 with the tools, 0.013 to 0.070 before them (the
    // header's meta row filling in, which this change does not touch).
    expect(result.shifts).toBeLessThan(0.1);
  });
});
