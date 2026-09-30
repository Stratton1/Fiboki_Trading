import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import {
  API,
  barTime,
  chartBar,
  chartBars,
  chartOverlays,
  mockChart,
  mockShell,
} from "./fixtures";

/**
 * The chart workstation, /markets/[symbol] (plan §6, report G C1–C18, 4c-1).
 *
 * Canvas pixels are not what these assert: the page exposes what it drew as
 * DOM (the summary line's counts, the legend, the crosshair readout and the
 * "View data" tables), and every one of those numbers must be the mocked
 * API's. That is also the proof the browser computes nothing: change the
 * fixture and every figure on screen changes with it.
 */

const EURUSD = "/markets/EURUSD";

async function ready(page: Page) {
  await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
  const chart = page.getByTestId("price-chart");
  await expect(chart).toHaveAttribute("data-ready", "true");
  await expect(chart.locator("canvas").first()).toBeVisible();
  await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-overlays", "success");
}

const fmt5 = (value: number) =>
  new Intl.NumberFormat("en-GB", { minimumFractionDigits: 5, maximumFractionDigits: 5 }).format(value);

test.describe("chart workstation", () => {
  test("draws the candles from the mocked bars on a canvas", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await expect(page.getByTestId("chart-symbol")).toHaveText("EURUSD");
    const summary = page.getByTestId("chart-summary");
    await expect(summary).toHaveAttribute("data-bars", "120");
    await expect(summary).toHaveAttribute("data-volume", "true");
    // The library draws into canvases inside the chart's box, and the box
    // has a real size (never a zero-height, blank canvas).
    const box = await page.getByTestId("price-chart").boundingBox();
    expect(box?.height).toBeGreaterThan(200);
    expect(box?.width).toBeGreaterThan(300);
    expect(await page.getByTestId("price-chart").locator("canvas").count()).toBeGreaterThan(1);
    await expect(page.getByTestId("dataset-version")).toHaveAttribute(
      "data-version",
      "dsv_eurusd_h1_0007abcdef",
    );
    await expect(page.getByTestId("bars-provenance").getByTestId("provenance-chip")).toHaveAttribute(
      "data-provenance",
      "source",
    );
    await expect(page.getByTestId("journal-provenance").getByTestId("provenance-chip")).toHaveAttribute(
      "data-provenance",
      "paper",
    );
    expect(errors).toEqual([]);
  });

  test("switching the timeframe refetches bars and overlays with that timeframe", async ({ page }) => {
    const requests = await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const first = requests.map((u) => `${u.pathname}?${u.searchParams.toString()}`);
    expect(first).toContain("/api/markets/bars/EURUSD?timeframe=H1&limit=500");
    expect(first).toContain("/api/markets/overlays/EURUSD?timeframe=H1&limit=500");

    const bars = page.waitForRequest((r) => r.url().includes("/api/markets/bars/EURUSD?timeframe=H4"));
    const overlays = page.waitForRequest((r) =>
      r.url().includes("/api/markets/overlays/EURUSD?timeframe=H4"),
    );
    await page.getByTestId("chart-timeframe").getByRole("radio", { name: "H4" }).click();
    await Promise.all([bars, overlays]);
    await expect(page).toHaveURL(/\/markets\/EURUSD\?tf=H4$/);
    await expect(page.getByTestId("chart-workstation")).toHaveAttribute("data-timeframe", "H4");
    await ready(page);
    await expect(page.getByTestId("chart-timeframe").getByRole("radio", { name: "H4" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  test("a deep link with ?tf=D1 reads D1 first", async ({ page }) => {
    const requests = await mockChart(page);
    await page.goto(`${EURUSD}?tf=D1`);
    await ready(page);
    const bars = requests.filter((u) => u.pathname === "/api/markets/bars/EURUSD");
    expect(bars.map((u) => u.searchParams.get("timeframe"))).toEqual(["D1"]);
    await expect(page.getByTestId("chart-timeframe").getByRole("radio", { name: "D1" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  test("signals, fills, levels and regimes are placed and counted as the API sent them", async ({
    page,
  }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const summary = page.getByTestId("chart-summary");
    // Four signals were sent; one decided before the first bar is outside the
    // window, counted and said so, never silently dropped.
    await expect(summary).toHaveAttribute("data-signals", "3");
    await expect(summary).toHaveAttribute("data-fills", "3");
    await expect(summary).toHaveAttribute("data-levels", "3");
    await expect(summary).toHaveAttribute("data-regimes", "3");
    await expect(page.getByTestId("legend-unplaced")).toContainText("1 signal");
    await expect(page.getByTestId("legend-layer-signals")).toHaveAttribute("data-count", "3");
    await expect(page.getByTestId("legend-layer-regimes")).toHaveAttribute("data-count", "3");
    // Series: price-pane ichimoku (3 lines, one display-only) and ATR in its
    // own pane are shown; the state column starts hidden. Panes: price,
    // volume, atr_14.
    await expect(summary).toHaveAttribute("data-series-total", "5");
    await expect(summary).toHaveAttribute("data-series", "4");
    await expect(summary).toHaveAttribute("data-panes", "3");
    // Sections the API reports unavailable are listed with the API's reason.
    const unavailable = page.getByTestId("legend-unavailable");
    await expect(unavailable.filter({ hasText: "backtest_fills" })).toContainText(
      "records experiment metrics, not per-trade fills",
    );
    await expect(unavailable.filter({ hasText: "headlines" })).toContainText("No headline store");
    await expect(page.getByTestId("legend-not-drawn")).toContainText("1 calendar event(s)");
  });

  test("the legend toggles layers and series groups", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const summary = page.getByTestId("chart-summary");
    const state = page.locator('[data-testid="legend-series-group"][data-pane^="state:"]');
    await expect(state).toHaveAttribute("aria-pressed", "false");
    await state.click();
    await expect(state).toHaveAttribute("aria-pressed", "true");
    await expect(summary).toHaveAttribute("data-series", "5");
    await expect(summary).toHaveAttribute("data-panes", "4");
    const regimes = page.getByTestId("legend-layer-regimes");
    await regimes.click();
    await expect(regimes).toHaveAttribute("aria-pressed", "false");
    // Hiding the ATR pane removes its pane and its values from the readout.
    await page.locator('[data-testid="legend-series-group"][data-pane="atr_14"]').click();
    await expect(summary).toHaveAttribute("data-panes", "3");
    await expect(page.locator('[data-testid="readout-series"][data-series="atr_14:atr_14"]')).toHaveCount(0);
  });

  test("the readout shows the latest bar, and the keyboard moves it bar by bar", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const readout = page.getByTestId("crosshair-readout");
    const last = chartBar(119);
    await expect(readout).toHaveAttribute("data-source", "latest");
    await expect(readout).toHaveAttribute("data-index", "119");
    await expect(readout.getByTestId("readout-c")).toHaveText(fmt5(last.c));

    await page.getByTestId("price-chart").focus();
    await page.keyboard.press("Home");
    await expect(readout).toHaveAttribute("data-source", "keyboard");
    await expect(readout).toHaveAttribute("data-index", "0");
    const bar0 = chartBar(0);
    await expect(readout.getByTestId("readout-o")).toHaveText(fmt5(bar0.o));
    await expect(readout.getByTestId("readout-h")).toHaveText(fmt5(bar0.h));
    await expect(readout.getByTestId("readout-l")).toHaveText(fmt5(bar0.l));
    await expect(readout.getByTestId("readout-c")).toHaveText(fmt5(bar0.c));
    await expect(readout.getByTestId("readout-time")).toHaveText("Mon 21 Sep 2026 00:00 UTC");
    // Kijun is in warm-up at bar 0: the API sent null, and it reads "no data".
    const kijun = readout.locator('[data-series="ichimoku_9_26_52_26_26:ichimoku_9_26_52_26_26_kijun"]');
    await expect(kijun.getByTestId("readout-series-value")).toHaveText("no data");

    await page.keyboard.press("ArrowRight");
    await expect(readout).toHaveAttribute("data-index", "1");
    const tenkan = readout.locator('[data-series="ichimoku_9_26_52_26_26:ichimoku_9_26_52_26_26_tenkan"]');
    await expect(tenkan.getByTestId("readout-series-value")).toHaveText(fmt5(chartBar(1).c - 0.0001));

    await page.keyboard.press("Shift+ArrowRight");
    await expect(readout).toHaveAttribute("data-index", "11");
    // Bar 5's volume is null in the fixture: "no data", never 0.
    for (let i = 0; i < 6; i += 1) await page.keyboard.press("ArrowLeft");
    await expect(readout).toHaveAttribute("data-index", "5");
    await expect(readout.getByTestId("readout-v")).toHaveText("no data");

    await page.keyboard.press("Escape");
    await expect(readout).toHaveAttribute("data-source", "latest");
  });

  test("the pointer moves the crosshair readout", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const box = await page.getByTestId("price-chart").boundingBox();
    if (!box) throw new Error("no chart box");
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.3);
    await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.3);
    const readout = page.getByTestId("crosshair-readout");
    await expect(readout).toHaveAttribute("data-source", "pointer");
    const index = Number(await readout.getAttribute("data-index"));
    expect(index).toBeGreaterThanOrEqual(0);
    expect(index).toBeLessThan(120);
    await expect(readout.getByTestId("readout-c")).toHaveText(fmt5(chartBar(index).c));
  });

  test("View data shows the same numbers the API sent", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("chart-view-data").click();
    const panel = page.getByTestId("chart-data-panel");
    await expect(panel).toBeVisible();
    const bars = page.getByTestId("chart-data-bars");
    await expect(bars.getByTestId("grid-count")).toContainText("120 rows");
    const row0 = bars.locator(`tr[data-row-id="${barTime(0)}"]`);
    const bar0 = chartBar(0);
    await expect(row0.locator('td[data-column="o"]')).toHaveText(fmt5(bar0.o));
    await expect(row0.locator('td[data-column="c"]')).toHaveText(fmt5(bar0.c));
    await expect(row0.locator('td[data-column="v"]')).toHaveText("100");

    await page.getByTestId("chart-data-tab-fills").click();
    const fills = page.getByTestId("chart-data-fills");
    await expect(fills.getByTestId("grid-count")).toContainText("3 rows");
    const exit = fills.locator('tr[data-row-id="ps_001|trd_0001|exit"]');
    await expect(exit.locator('td[data-column="price"]')).toContainText("1.10450");
    await expect(exit.locator('td[data-column="price"]').getByTestId("provenance-chip")).toHaveAttribute(
      "data-provenance",
      "paper",
    );
    await expect(exit.locator('td[data-column="pnl"]')).toContainText("+£12.50");

    await page.getByTestId("chart-data-tab-levels").click();
    const levels = page.getByTestId("chart-data-levels");
    await expect(levels.locator('tr[data-row-id="pos_0001|stop"] td[data-column="price"]')).toContainText(
      "1.11800",
    );

    await page.getByTestId("chart-data-tab-signals").click();
    const signals = page.getByTestId("chart-data-signals");
    await expect(signals.getByTestId("grid-count")).toContainText("3 rows");
    await expect(signals).toContainText("max_open_positions");

    await page.getByTestId("chart-data-tab-regimes").click();
    await expect(page.getByTestId("chart-data-regimes").getByTestId("grid-count")).toContainText("3 rows");

    // The chart stays drawn beside the table, in a resizable split.
    await expect(page.getByTestId("price-chart").locator("canvas").first()).toBeVisible();
    await expect(page.getByRole("separator")).toBeVisible();
  });

  test("the Sources popover lists every source the API attached", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("chart-sources").click();
    const popover = page.getByTestId("chart-sources-popover");
    await expect(popover).toBeVisible();
    const kinds = await popover
      .getByTestId("source-row")
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-kind")));
    expect(kinds.sort()).toEqual(["bar_store", "marketstate", "official_calendar", "paper_journal"]);
    await expect(popover.getByTestId("sources-bars")).toContainText("dsv_eurusd_h1_0007abcdef");
    await expect(popover).toContainText("rc_v1_3f2a");
  });

  test("overlays computed on a different dataset version are flagged", async ({ page }) => {
    await mockChart(page, { overlays: () => chartOverlays({ version: "dsv_other_0001" }) });
    await page.goto(EURUSD);
    await ready(page);
    await expect(page.getByTestId("dataset-mismatch")).toContainText("dsv_other_00");
  });

  test("no bars: the empty state, and no chart", async ({ page }) => {
    await mockChart(page, { bars: () => chartBars({ count: 0 }) });
    await page.goto(EURUSD);
    const empty = page.getByTestId("state-empty");
    await expect(empty).toBeVisible();
    await expect(empty).toContainText("No bars for EURUSD H1");
    await expect(page.getByTestId("price-chart")).toHaveCount(0);
    await expect(page.getByTestId("chart-view-data")).toBeDisabled();
  });

  test("no market data mounted: the absent state, with the API's words", async ({ page }) => {
    await mockChart(page, {
      barsStatus: 503,
      bars: () => ({
        code: "market_data_not_mounted",
        detail:
          "No market-data root is mounted on this deployment, so no bars can be served. This is a missing data source, not a quiet market.",
        correlation_id: "cid-bars",
        context: {},
      }),
    });
    await page.goto(EURUSD);
    const absent = page.getByTestId("state-absent");
    await expect(absent).toBeVisible();
    await expect(absent).toContainText("No market-data root is mounted");
    await expect(absent.getByTestId("view-state-absent")).toBeVisible();
    await expect(page.getByTestId("price-chart")).toHaveCount(0);
  });

  test("a failed bars read is an error with its code, never a blank canvas", async ({ page }) => {
    await mockChart(page, {
      barsStatus: 503,
      bars: () => ({
        code: "bars_unavailable",
        detail: "The bar dataset could not be read.",
        correlation_id: "cid-bars-2",
        context: { error_class: "FileNotFoundError" },
      }),
    });
    await page.goto(EURUSD);
    const error = page.getByTestId("state-error");
    await expect(error).toBeVisible();
    await expect(error).toContainText("The bar dataset could not be read.");
    await expect(error).toContainText("bars_unavailable");
    await expect(error).toContainText("cid-bars-2");
    await expect(page.getByTestId("price-chart")).toHaveCount(0);
  });

  test("a failed overlays read keeps the candles and says the layers are not loaded", async ({ page }) => {
    await mockChart(page, {
      overlaysStatus: 500,
      overlays: () => ({
        code: "internal_error",
        detail: "Overlay computation failed.",
        correlation_id: "cid-ov",
        context: {},
      }),
    });
    await page.goto(EURUSD);
    await expect(page.getByTestId("price-chart")).toHaveAttribute("data-ready", "true");
    const error = page.getByTestId("overlays-error");
    await expect(error).toContainText("Overlay computation failed.");
    await expect(error).toContainText("cid-ov");
    await expect(page.getByTestId("chart-summary")).toHaveAttribute("data-signals", "");
    await expect(page.getByTestId("legend-layer-signals")).toContainText("…");
    await expect(page.getByTestId("overlays-retry")).toBeVisible();
  });

  test("switching theme and the colour-blind preset recolours the chart without an error", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.emulateMedia({ colorScheme: "dark" });
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    // The chart's background is the --chart-bg token: read a pixel from the
    // library's own canvas in each theme.
    const background = () =>
      page.getByTestId("price-chart").evaluate((el) => {
        const canvas = el.querySelector("canvas");
        const ctx = canvas?.getContext("2d");
        if (!canvas || !ctx) return null;
        const [r, g, b] = ctx.getImageData(2, 2, 1, 1).data;
        return ((r as number) + (g as number) + (b as number)) / 3;
      });
    const dark = await background();
    expect(dark).not.toBeNull();
    expect(dark as number).toBeLessThan(80);
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
    await expect.poll(background).toBeGreaterThan(200);
    await page.evaluate(() => document.documentElement.setAttribute("data-pnl", "cvd"));
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await expect.poll(background).toBeLessThan(80);
    await expect(page.getByTestId("price-chart").locator("canvas").first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("the workstation triggers no CSP violation, drawn and with its data open", async ({ page }) => {
    await page.addInitScript(() => {
      const seen: string[] = [];
      (window as unknown as { __csp: string[] }).__csp = seen;
      document.addEventListener("securitypolicyviolation", (event) => {
        seen.push(`${event.violatedDirective} ${event.blockedURI} ${event.sourceFile}:${event.lineNumber}`);
      });
    });
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    await page.getByTestId("chart-view-data").click();
    await expect(page.getByTestId("chart-data-panel")).toBeVisible();
    await page.getByTestId("chart-sources").click();
    await expect(page.getByTestId("chart-sources-popover")).toBeVisible();
    await page.keyboard.press("Escape");
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
    const violations = await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp);
    expect(violations).toEqual([]);
  });

  for (const theme of ["dark", "light"] as const) {
    test(`axe: zero serious violations with the chart drawn (${theme})`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "one axe sweep, on the desktop project");
      await page.emulateMedia({ colorScheme: theme });
      await mockChart(page);
      await page.goto(EURUSD);
      await ready(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const audit = async (label: string) => {
        const results = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
          .analyze();
        const blocking = results.violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => ({ id: v.id, help: v.help, targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")) }));
        expect(blocking, `${label}: serious/critical axe violations`).toEqual([]);
      };
      await audit(`chart (${theme})`);
      await page.getByTestId("chart-view-data").click();
      await expect(page.getByTestId("chart-data-bars").getByTestId("grid-count")).toBeVisible();
      await audit(`chart with data (${theme})`);
      await page.getByTestId("chart-key").click();
      await expect(page.getByTestId("chart-key-popover")).toBeVisible();
      await audit(`chart key (${theme})`);
    });
  }

  test("the instrument list links each symbol to its chart", async ({ page }) => {
    await mockChart(page);
    await page.goto("/markets");
    await page.getByTestId("instrument-chart-link").filter({ hasText: "USDJPY" }).click();
    await expect(page).toHaveURL(/\/markets\/USDJPY$/);
    await expect(page.getByTestId("chart-symbol")).toHaveText("USDJPY");
  });

  test("the palette opens a chart by symbol", async ({ page }) => {
    await mockChart(page);
    await page.goto("/");
    await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
    await page.keyboard.press("ControlOrMeta+KeyK");
    await page.getByTestId("palette-input").fill("gbpusd");
    await expect(page.getByTestId("palette-open-chart")).toHaveText("Open chart: GBPUSD");
    await page.getByTestId("palette-open-chart").click();
    await expect(page).toHaveURL(/\/markets\/GBPUSD$/);
    await expect(page.getByTestId("chart-workstation")).toHaveAttribute("data-symbol", "GBPUSD");
  });
});

test.describe("chart workstation at phone width", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("the timeframe control stays reachable, the readout stacks, no horizontal scroll", async ({
    page,
  }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const h4 = page.getByTestId("chart-timeframe").getByRole("radio", { name: "H4" });
    await expect(h4).toBeInViewport();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    const chart = await page.getByTestId("price-chart").boundingBox();
    expect(chart?.width).toBeLessThanOrEqual(390);
    expect(chart?.height).toBeGreaterThan(250);
    const readout = page.getByTestId("crosshair-readout");
    await readout.scrollIntoViewIfNeeded();
    await expect(readout).toBeInViewport();
    await h4.click();
    await expect(page).toHaveURL(/tf=H4/);
  });
});

test("an unmocked chart route answers with its error state, not a blank page", async ({ page }) => {
  // What the all-routes sweeps (a11y, CSP) see: no API behind /markets/EURUSD.
  await mockShell(page);
  await page.route(`${API}/api/markets/**`, (route: Route) =>
    route.fulfill({
      status: 503,
      json: { code: "unavailable", detail: "Down.", correlation_id: "cid-x", context: {} },
    }),
  );
  await page.goto(EURUSD);
  await expect(page.getByTestId("state-error")).toBeVisible();
});

test.describe("dataset chip (inventory F-8)", () => {
  test("the id is cut in the middle, keeping the timeframe, and shown whole on hover and on focus", async ({ page }) => {
    await mockChart(page);
    await page.goto(EURUSD);
    await ready(page);
    const chip = page.getByTestId("dataset-version");
    await expect(chip).toHaveAttribute("data-version", "dsv_eurusd_h1_0007abcdef");
    const short = chip.getByTestId("dataset-version-short");
    const full = chip.getByTestId("dataset-version-full");
    // Was "dsv_eurusd_h" (first 12): the timeframe was lost.
    await expect(short).toHaveText("dsv_eurusd_h1_…abcdef");
    await expect(short).toBeVisible();
    const width = async () => {
      const box = await full.boundingBox();
      if (box === null) throw new Error("the full id has no box");
      return box.width;
    };
    expect(await width()).toBeLessThanOrEqual(1);
    // Keyboard: the chip is focusable and focus reveals the whole id.
    await chip.focus();
    await expect(short).toBeHidden();
    expect(await width()).toBeGreaterThan(60);
    await expect(full).toHaveText("dsv_eurusd_h1_0007abcdef");
    await chip.blur();
    await expect(short).toBeVisible();
    // Pointer: hover reveals it too.
    await chip.hover();
    await expect(short).toBeHidden();
    expect(await width()).toBeGreaterThan(60);
  });

  test("a short id is shown whole", async ({ page }) => {
    await mockChart(page, { bars: () => chartBars({ version: "dsv_short_01" }) });
    await page.goto(EURUSD);
    await ready(page);
    await expect(page.getByTestId("dataset-version-short")).toHaveText("dsv_short_01");
    await expect(page.getByTestId("dataset-version")).toHaveAttribute("data-truncated", "false");
  });
});
