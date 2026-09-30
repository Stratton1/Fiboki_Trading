import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import { API, exposurePage, exposureRow, figure, mockRisk, riskState, source } from "./fixtures";

/**
 * Risk & Exposure v2 (Wave 4a): one screen at /trading/risk and
 * /trading/exposure answering "how close are we to any limit?".
 *
 * The limit board draws the API's numbers and nothing else: exposure
 * utilisation is the API's own; daily-loss and drawdown utilisation are
 * derived from the value and limit the API sends, by the rule the API uses to
 * list a breach. A limit the API does not report is NOT REPORTED, never a bar.
 */

const row = (page: Page, key: string) => page.locator(`[data-testid="limit-row"][data-key="${key}"]`);

/** Day P&L −1.64% against a 2% limit (82% used); drawdown 3.2% against 10% (32%). */
const RISK = {
  daily_loss_pct: figure(-1.64, "paper", "pct"),
  drawdown_pct: figure(3.2, "paper", "pct"),
};

test.describe("limit board", () => {
  test("every reported limit is a bar with the mocked value, limit, headroom and state", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("limit-board")).toBeVisible();

    // Exposure: the API's utilisation, its limit, headroom = limit − exposure.
    const usd = row(page, "exposure:currency:USD");
    await expect(usd).toHaveAttribute("data-state", "critical");
    await expect(usd).toHaveAttribute("data-utilisation", "94.0");
    await expect(usd.getByTestId("limit-used")).toContainText("94.0%");
    await expect(usd.getByTestId("limit-value")).toHaveText("14.10%");
    await expect(usd.getByTestId("limit-limit")).toHaveText("15.00%");
    await expect(usd.getByTestId("limit-headroom")).toHaveText("0.90 pp");
    await expect(usd.getByTestId("limit-state")).toHaveText("◆CRITICAL");
    await expect(usd.getByTestId("limit-bar")).toHaveAttribute("data-length", "94.0");

    await expect(row(page, "exposure:instrument:EURUSD")).toHaveAttribute("data-state", "warn");
    await expect(row(page, "exposure:instrument:EURUSD").getByTestId("limit-state")).toHaveText("◐NEAR");
    await expect(row(page, "exposure:currency:JPY")).toHaveAttribute("data-state", "ok");

    // Daily loss: 1.64 of 2.00 used, headroom = day P&L + limit = 0.36 pp.
    const daily = row(page, "risk:daily_loss");
    await expect(daily).toHaveAttribute("data-utilisation", "82.0");
    await expect(daily).toHaveAttribute("data-derived", "true");
    await expect(daily).toHaveAttribute("data-state", "warn");
    await expect(daily.getByTestId("limit-value")).toContainText("−1.64%");
    await expect(daily.getByTestId("limit-headroom")).toHaveText("0.36 pp");

    const dd = row(page, "risk:drawdown");
    await expect(dd).toHaveAttribute("data-utilisation", "32.0");
    await expect(dd).toHaveAttribute("data-state", "ok");
    await expect(dd.getByTestId("limit-headroom")).toHaveText("6.80 pp");

    // Not reported by the API: listed, with the reason, and never a bar.
    for (const key of ["risk:weekly_loss", "risk:margin", "none:account_risk", "none:correlated_exposure", "none:data_quality"]) {
      await expect(row(page, key)).toHaveAttribute("data-state", "absent");
      await expect(row(page, key).getByTestId("limit-bar")).toHaveCount(0);
      await expect(row(page, key).getByTestId("limit-state")).toHaveText("⊘NOT REPORTED");
    }

    // The headline is the row closest to its limit.
    await expect(page.getByTestId("limit-hero-label")).toContainText("USD");
    await expect(page.getByTestId("limit-hero-used")).toContainText("94.0%");
    await expect(page.getByTestId("limit-hero-incomplete")).toHaveCount(0);
  });

  test("rows sort by utilisation inside their family; families by their most-used row", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.goto("/trading/risk");
    const families = page.locator('[data-testid="limit-board"] [data-family]:is(section, figure)');
    await expect(families.first()).toHaveAttribute("data-family", "exposure");
    expect(await families.evaluateAll((els) => els.map((el) => el.getAttribute("data-family")))).toEqual([
      "exposure",
      "loss",
      "drawdown",
      "account",
      "margin",
      "correlation",
      "data_quality",
    ]);
    const exposure = page.locator('[data-testid="limit-row"][data-family="exposure"]');
    expect(await exposure.evaluateAll((els) => els.map((el) => el.getAttribute("data-utilisation")))).toEqual([
      "94.0",
      "75.0",
      "66.0",
      "50.0",
      "24.0",
      "16.0",
    ]);
  });

  test("a breach is BREACHED, over the limit, and the API's breach list is shown", async ({ page }) => {
    await mockRisk(page, {
      risk: {
        drawdown_pct: figure(12.5, "paper", "pct"),
        breaches: ["Drawdown 12.5% exceeds the 10.0% limit."],
      },
      exposure: [exposureRow("instrument:EURUSD", 11, 10, 110, { breached: true })],
    });
    await page.goto("/trading/risk");
    const dd = row(page, "risk:drawdown");
    await expect(dd).toHaveAttribute("data-state", "breached");
    await expect(dd.getByTestId("limit-headroom")).toHaveText("over by 2.50 pp");
    await expect(dd.getByTestId("limit-bar")).toHaveAttribute("data-length", "100.0");
    await expect(row(page, "exposure:instrument:EURUSD")).toHaveAttribute("data-state", "breached");
    await expect(page.getByTestId("gateway-breaches")).toContainText("Drawdown 12.5% exceeds the 10.0% limit.");
    // Both are breached; the larger overshoot heads the board.
    await expect(page.getByTestId("limit-hero-label")).toContainText("Total drawdown");
    await expect(page.getByTestId("limit-hero")).toHaveAttribute("data-state", "breached");
  });

  test("a figure the API could not supply is absent with its reason, never a zero bar", async ({ page }) => {
    const reason = "No paper account exists (the trade record is seed), so drawdown and daily loss are unknown, not zero.";
    const missing = figure(null, "paper", "pct", {
      caveats: [{ code: "value_unavailable", severity: "warning", message: reason, affects: "", direction: "unknown" }],
    });
    await mockRisk(page, { risk: { daily_loss_pct: missing, drawdown_pct: missing } });
    await page.goto("/trading/risk");
    for (const key of ["risk:daily_loss", "risk:drawdown"]) {
      await expect(row(page, key)).toHaveAttribute("data-state", "absent");
      await expect(row(page, key).getByTestId("limit-bar")).toHaveCount(0);
      await expect(row(page, key)).toContainText(reason);
      await expect(row(page, key)).not.toContainText("0.00%");
    }
  });

  test("a failed source is an error in its family and the headline says it is incomplete", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.route(`${API}/api/trading/exposure`, (route: Route) =>
      route.fulfill({
        status: 503,
        json: { code: "journal_unreadable", detail: "The journal could not be read.", correlation_id: "cid-x", context: {} },
      }),
    );
    await page.goto("/trading/risk");
    const error = page.locator('[data-family="exposure"]').getByTestId("limit-family-error");
    await expect(error).toContainText("journal_unreadable");
    await expect(error).toContainText("cid-x");
    // Unknown goes first, above the measured families.
    await expect(page.locator('[data-testid="limit-board"] [data-family]:is(section, figure)').first()).toHaveAttribute(
      "data-family",
      "exposure",
    );
    await expect(page.getByTestId("limit-hero-incomplete")).toContainText("/api/trading/exposure");
    await expect(page.getByTestId("limit-hero-label")).toContainText("Daily loss");
  });

  test("an empty exposure answer is EMPTY, not a failure", async ({ page }) => {
    await mockRisk(page, { risk: RISK, exposure: [] });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("limit-family-empty")).toBeVisible();
    await expect(page.locator("#exposure").getByTestId("state-empty")).toBeVisible();
  });

  test("the drawdown throttle is NOT REPORTED: steps named, none marked", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.goto("/trading/risk");
    const meter = page.getByTestId("throttle-meter");
    await expect(meter).toHaveAttribute("data-state", "absent");
    await expect(meter.getByTestId("throttle-step")).toHaveText([
      /×1\.0/,
      /×0\.6/,
      /×0\.3/,
      /PAUSE/,
      /FLATTEN/,
    ]);
    await expect(meter.locator('[aria-current="step"]')).toHaveCount(0);
    await expect(meter.getByTestId("throttle-absent")).toContainText("3.20%");
  });
});

test.describe("exposure matrix", () => {
  test("the heat cells and the data table agree, cell for cell", async ({ page }) => {
    await mockRisk(page, {
      risk: RISK,
      exposure: [...exposurePage().items, exposureRow("currency:GBP", null, 15, null)],
    });
    await page.goto("/trading/exposure");
    const cells = page.getByTestId("exposure-cell");
    await expect(cells).toHaveCount(7);
    const fromCells = await cells.evaluateAll((els) =>
      els.map((el) => [el.getAttribute("data-key"), el.getAttribute("data-used"), el.getAttribute("data-state")]),
    );
    await page.getByTestId("exposure-data").locator("summary").click();
    const rows = page.getByTestId("exposure-data-row");
    await expect(rows).toHaveCount(7);
    const fromTable = await rows.evaluateAll((els) =>
      els.map((el) => [el.getAttribute("data-key"), el.getAttribute("data-used")]),
    );
    expect(fromTable).toEqual(fromCells.map(([key, used]) => [key, used]));
    expect(fromCells).toContainEqual(["currency:USD", "94.0%", "critical"]);
    // No equity to divide by: hatched "no data", never a zero cell.
    expect(fromCells).toContainEqual(["currency:GBP", "no data", "absent"]);
    await expect(page.locator('[data-testid="exposure-cell"][data-key="currency:GBP"] rect.xheat')).toHaveAttribute(
      "data-bin",
      "none",
    );
    // The numbers are the matrix's own text, not only its shade.
    await expect(page.locator('[data-testid="exposure-cell"][data-key="instrument:EURUSD"]')).toContainText("75.0%");
    await expect(page.getByTestId("correlated-risk")).toHaveAttribute("data-state", "absent");
  });

  test("/trading/exposure is the same screen, opened at the exposure matrix", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.goto("/trading/exposure");
    await expect(page.getByTestId("risk-exposure")).toHaveAttribute("data-focus", "exposure");
    await expect(page.getByTestId("limit-board")).toBeVisible();
    await expect(page.getByTestId("exposure-matrix")).toBeInViewport();
  });
});

test.describe("kill switch and positions", () => {
  test("⇧K opens the shell's kill-switch dialog and no single key arms it", async ({ page }) => {
    const mutations: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET") mutations.push(`${request.method()} ${request.url()}`);
    });
    await mockRisk(page, { risk: RISK });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "false");
    await page.keyboard.press("Shift+KeyK");
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId("confirm-choice-pause")).toHaveAttribute("data-selected", "false");
    await expect(page.getByTestId("confirm-choice-flatten")).toHaveAttribute("data-selected", "false");
    for (const key of ["Enter", "Space", "KeyP", "KeyF", "Shift+KeyK", "Enter"]) {
      await page.keyboard.press(key);
    }
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-cancel").click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "false");
    expect(mutations).toEqual([]);
    // One control on the screen, the shell's.
    await expect(page.getByTestId("kill-switch-arm")).toHaveCount(1);
  });

  test("the positions drawer lists the open book from the API", async ({ page }) => {
    let reads = 0;
    await mockRisk(page, { risk: RISK });
    await page.route(`${API}/api/trading/positions`, (route: Route) => {
      reads += 1;
      return route.fulfill({
        json: {
          items: [
            {
              position_id: "pos_0001",
              strategy_id: "ichimoku_kumo_trend",
              instrument: "EURUSD",
              direction: "long",
              provenance: "paper",
              entry_time: "2026-09-19T08:00:00Z",
              size: figure(0.5, "paper", "lots"),
              entry_price: figure(1.1, "paper"),
              mark_price: figure(null, "paper"),
              stop_loss: figure(1.095, "paper"),
              take_profit: figure(1.12, "paper"),
              unrealised_pnl: figure(-12.25, "paper", "GBP"),
              distance_to_stop_pct: figure(0.64, "paper", "pct"),
            },
          ],
          total: 1,
          offset: 0,
          limit: 100,
          source: source("live"),
          caveats: [],
        },
      });
    });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("limit-board")).toBeVisible();
    expect(reads).toBe(0);
    await page.getByTestId("positions-open").click();
    const sheet = page.getByTestId("positions-sheet");
    await expect(sheet).toBeVisible();
    const position = sheet.getByTestId("position-row");
    await expect(position).toHaveCount(1);
    await expect(position).toContainText("EURUSD");
    await expect(position).toContainText("−£12.25");
    // A mark the session did not persist is "no data", not the entry price.
    await expect(position.getByTestId("figure-missing")).toHaveText("no data");
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    await expect(page.getByTestId("positions-open")).toContainText("(1)");
  });
});

test.describe("accessibility", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "one sweep, on the desktop project");
  });

  for (const theme of ["dark", "light"] as const) {
    test(`axe: zero serious with data, the matrix table and the drawer open (${theme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await mockRisk(page, { risk: RISK, exposure: [...exposurePage().items, exposureRow("currency:GBP", null, 15, null)] });
      await page.goto("/trading/risk");
      await expect(page.getByTestId("limit-board")).toBeVisible();
      await expect(page.getByTestId("exposure-matrix")).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.getByTestId("exposure-data").locator("summary").click();
      const audit = async (label: string) => {
        const results = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
          .analyze();
        const blocking = results.violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => ({ id: v.id, targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")) }));
        expect(blocking, label).toEqual([]);
      };
      await audit("board and matrix");
      await page.getByTestId("positions-open").click();
      await expect(page.getByTestId("position-row").first()).toBeVisible();
      await audit("positions drawer");
    });
  }
});

test.describe("at 390 px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("bars stack full width, the matrix scrolls inside itself, the page never scrolls sideways", async ({ page }) => {
    const wide = ["EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD"].map((ccy, i) =>
      exposureRow(`currency:${ccy}`, i * 2, 15, (i * 2 * 100) / 15),
    );
    await mockRisk(page, { risk: RISK, exposure: wide });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("limit-board")).toBeVisible();
    const bar = row(page, "risk:daily_loss").getByTestId("limit-bar");
    const box = await bar.boundingBox();
    if (box === null) throw new Error("no bar");
    expect(box.width).toBeGreaterThan(280);
    const scroller = page.getByTestId("exposure-matrix-scroll");
    await scroller.scrollIntoViewIfNeeded();
    expect(await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    // The kill switch is on the screen and usable at this width.
    await expect(page.getByTestId("kill-switch-arm")).toBeEnabled();
  });
});

test.describe("inventory fixes and visuals (2026-09-30)", () => {
  test("the positions drawer draws a distance-to-stop meter per row, marked † with its formula", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.goto("/trading/risk");
    await page.getByTestId("positions-open").click();
    const sheet = page.getByTestId("positions-sheet");
    const first = sheet.locator('[data-testid="position-row"][data-position-id="pos_0001"]');
    const meter = first.getByTestId("stop-meter");
    // Long: entry 1.10000, mark 1.10200, stop 1.09500 → (1.102 − 1.095) ÷ (1.1 − 1.095) = 1.4.
    await expect(meter).toHaveAttribute("data-position", "1.400");
    await expect(meter).toHaveAttribute("data-state", "between");
    // The API's own distance to stop is still the number beside it.
    await expect(first).toContainText("0.64%");
    const dagger = meter.getByTestId("stop-meter-dagger");
    await expect(dagger).toHaveText("†");
    await expect(dagger).toHaveAttribute("aria-label", /\(mark − stop\) ÷ \(entry − stop\)/);
    await dagger.hover();
    await expect(page.locator('[data-testid="tooltip"][data-open]')).toContainText("(mark − stop) ÷ (entry − stop)");
    await expect(sheet.getByTestId("stop-meter-note")).toContainText("(mark − stop) ÷ (entry − stop)");
  });

  test("a position with no stop has no meter, and says why", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.route(`${API}/api/trading/positions`, (route: Route) =>
      route.fulfill({
        json: {
          items: [
            {
              position_id: "pos_nostop",
              strategy_id: "ichimoku_kumo_trend",
              instrument: "EURUSD",
              direction: "short",
              provenance: "paper",
              entry_time: "2026-09-19T08:00:00Z",
              size: figure(0.5, "paper", "lots"),
              entry_price: figure(1.1, "paper"),
              mark_price: figure(1.099, "paper"),
              stop_loss: figure(null, "paper"),
              take_profit: figure(1.09, "paper"),
              unrealised_pnl: figure(5, "paper", "GBP"),
              distance_to_stop_pct: figure(null, "paper", "pct"),
            },
          ],
          total: 1,
          offset: 0,
          limit: 100,
          source: source("live"),
          caveats: [],
        },
      }),
    );
    await page.goto("/trading/risk");
    await page.getByTestId("positions-open").click();
    const meter = page.getByTestId("positions-sheet").getByTestId("stop-meter");
    await expect(meter).toHaveAttribute("data-state", "none");
    await expect(meter).toHaveText("no stop reported");
  });

  test("the drawer's close button says what it closes (inventory F-5)", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.goto("/trading/risk");
    await page.getByTestId("positions-open").click();
    const close = page.getByTestId("positions-sheet-close");
    await expect(close).toHaveAttribute("aria-label", "Close Open positions");
    await expect(page.getByRole("button", { name: "Close inspector" })).toHaveCount(0);
    await close.click();
    await expect(page.getByTestId("positions-sheet")).toHaveCount(0);
    // The status bar's inspector names its own content too.
    await page.getByTestId("status-api").click();
    await expect(page.getByTestId("inspector-close")).toHaveAttribute("aria-label", "Close Platform health");
  });

  test("the kill-switch timeline is on Risk & Exposure too (V-2)", async ({ page }) => {
    await mockRisk(page, { risk: RISK });
    await page.goto("/trading/risk");
    const timeline = page.getByTestId("ks-timeline");
    await expect(timeline).toHaveAttribute("data-events", "2");
    await expect(timeline.getByTestId("ks-event").first()).toHaveAttribute("data-operator", "joe");
    await expect(timeline.getByTestId("ks-armed-span")).toHaveCount(1);
    await expect(timeline.getByTestId("ks-armed-span")).toHaveAttribute("data-open", "false");
  });
});

void riskState;
