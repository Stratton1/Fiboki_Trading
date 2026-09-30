import { readFileSync } from "node:fs";
import { expect, test, type Page, type Route } from "@playwright/test";
import { API, figure, manyTrades, mockShell, tradesPage } from "./fixtures";

/**
 * grid.spec (Wave 3 gate, plan §7): the DataGrid on the trades, candidates,
 * instruments and audit screens.
 *
 *  - 10,000 rows render as a virtual window, scroll to the end, and keep
 *    aria-rowcount honest;
 *  - sort by value with "no data" last in both directions; the unit is in the
 *    header, taken from the rows; numeric headers are right-aligned;
 *  - text filter, column visibility and pinning, saved column sets;
 *  - keyboard row navigation with a roving tab stop;
 *  - a live update changes the cell and keeps the selection on the same row;
 *  - CSV export carries provenance and never turns "no data" into 0;
 *  - `?row=` selects a row; an unknown key says so.
 */

test.use({ viewport: { width: 1280, height: 900 } });

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "grid behaviour, desktop project");
  await mockShell(page);
});

const rows = (page: Page) => page.getByTestId("grid-row");

async function tradesWith(page: Page, payload: unknown) {
  await page.route(`${API}/api/trading/trades*`, (route: Route) => route.fulfill({ json: payload }));
}

async function netValues(page: Page): Promise<string[]> {
  return rows(page).locator('td[data-column="net"]').allInnerTexts();
}

test.describe("virtualisation", () => {
  test("10,000 rows: a small window is rendered, the end is reachable, the count is honest", async ({
    page,
  }) => {
    await tradesWith(page, manyTrades(10_000));
    await page.goto("/trading/execution");
    await expect(page.getByTestId("grid-count")).toHaveText("10,000 rows");
    await expect(rows(page).first()).toBeVisible();
    const rendered = await rows(page).count();
    expect(rendered).toBeGreaterThan(5);
    expect(rendered).toBeLessThan(120);
    await expect(page.getByRole("grid", { name: "trades" })).toHaveAttribute("aria-rowcount", "10001");

    const scroller = page.locator(".grid__scroll").first();
    await scroller.evaluate((el) => el.scrollTo(0, el.scrollHeight));
    await expect(page.locator('[data-row-id="trd_09999"]')).toBeVisible();
    await expect(page.locator('[data-row-id="trd_09999"]')).toHaveAttribute("aria-rowindex", "10001");
    expect(await rows(page).count()).toBeLessThan(120);
  });

  test("End on a focused row jumps to row 10,000 and focuses it", async ({ page }) => {
    await tradesWith(page, manyTrades(10_000));
    await page.goto("/trading/execution");
    await rows(page).first().focus();
    await page.keyboard.press("End");
    const last = page.locator('[data-row-id="trd_09999"]');
    await expect(last).toBeFocused();
    await expect(last).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Home");
    await expect(page.locator('[data-row-id="trd_00000"]')).toBeFocused();
  });
});

test.describe("sort, units and alignment", () => {
  test("sorting by Net P&L orders by value, with 'no data' last both ways", async ({ page }) => {
    const payload = tradesPage();
    payload.items[1] = { ...payload.items[1]!, net_pnl: figure(null, "paper", "GBP") };
    await tradesWith(page, payload);
    await page.goto("/trading/execution");
    await expect(rows(page)).toHaveCount(4);
    const header = page.locator('th[data-column="net"]');

    await page.getByTestId("grid-sort-net").click();
    await expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(await netValues(page)).toEqual(["−95.50", "+118.00", "+412.50", "no data"]);

    await page.getByTestId("grid-sort-net").click();
    await expect(header).toHaveAttribute("aria-sort", "descending");
    expect(await netValues(page)).toEqual(["+412.50", "+118.00", "−95.50", "no data"]);
  });

  test("the unit is in the header, from the rows; numeric headers are right-aligned", async ({
    page,
  }) => {
    await page.goto("/trading/execution");
    await expect(rows(page)).toHaveCount(4);
    const net = page.locator('th[data-column="net"]');
    await expect(net).toContainText("Net P&L");
    await expect(net.getByTestId("grid-unit")).toHaveText("(GBP)");
    await expect(page.locator('th[data-column="r"]').getByTestId("grid-unit")).toHaveText("(R)");
    expect(await net.evaluate((el) => getComputedStyle(el).textAlign)).toBe("right");
    // Cells are bare numbers under the unit, right-aligned, with a real minus.
    const cell = rows(page).nth(1).locator('td[data-column="net"]');
    await expect(cell).toHaveText("−233.25");
    expect(await cell.evaluate((el) => getComputedStyle(el).textAlign)).toBe("right");
  });

  test("rows with different units say 'mixed units' and keep their own unit", async ({ page }) => {
    const payload = tradesPage();
    payload.items[0] = { ...payload.items[0]!, net_pnl: figure(10, "backtest", "USD") };
    await tradesWith(page, payload);
    await page.goto("/trading/execution");
    await expect(page.locator('th[data-column="net"]').getByTestId("grid-unit")).toHaveText(
      "(mixed units)",
    );
    await expect(rows(page).first().locator('td[data-column="net"]')).toHaveText("+$10.00");
  });
});

test.describe("filter, columns and saved sets", () => {
  test("the text filter narrows rows and says how many match", async ({ page }) => {
    await page.goto("/trading/execution");
    await expect(rows(page)).toHaveCount(4);
    await page.getByTestId("grid-filter").fill("trd_0002");
    await expect(rows(page)).toHaveCount(1);
    await expect(page.getByTestId("grid-count")).toHaveText("1 of 4 rows match");
    await page.getByTestId("grid-filter").fill("no such trade");
    await expect(page.getByTestId("grid-no-match")).toContainText("the filter hides them");
  });

  test("hide a column, pin a column; the layout survives a reload; saved sets apply", async ({
    page,
  }) => {
    await page.goto("/trading/execution");
    await expect(rows(page)).toHaveCount(4);
    await page.getByTestId("grid-columns").click();
    await page.getByTestId("grid-show-reason").uncheck();
    await expect(page.locator('th[data-column="reason"]')).toHaveCount(0);
    await page.getByTestId("grid-pin-trade_id").click();
    await expect(page.getByTestId("grid-pin-trade_id")).toHaveAttribute("aria-pressed", "true");
    const pinned = page.locator('th[data-column="trade_id"]');
    await expect(pinned).toHaveClass(/grid__pinned/);
    expect(await pinned.evaluate((el) => getComputedStyle(el).position)).toBe("sticky");

    await page.getByTestId("grid-set-name").fill("compact");
    await page.getByTestId("grid-set-save").click();
    await expect(page.getByTestId("grid-sets")).toContainText("compact");
    await page.getByTestId("grid-reset").click();
    await expect(page.locator('th[data-column="reason"]')).toHaveCount(1);
    await page.getByTestId("grid-set-apply-compact").click();
    await expect(page.locator('th[data-column="reason"]')).toHaveCount(0);

    await page.keyboard.press("Escape");
    await page.reload();
    await expect(rows(page)).toHaveCount(4);
    await expect(page.locator('th[data-column="reason"]')).toHaveCount(0);
    await expect(page.locator('th[data-column="trade_id"]')).toHaveClass(/grid__pinned/);
  });
});

test.describe("keyboard", () => {
  test("↓ j ↑ k move a single tab stop; the selection is the focused row", async ({ page }) => {
    await page.goto("/trading/execution");
    await expect(rows(page)).toHaveCount(4);
    // One tab stop for the whole grid.
    await expect(page.locator('[data-testid="grid-row"][tabindex="0"]')).toHaveCount(1);
    await rows(page).first().focus();
    await page.keyboard.press("ArrowDown");
    await expect(rows(page).nth(1)).toBeFocused();
    await page.keyboard.press("j");
    await expect(rows(page).nth(2)).toBeFocused();
    await expect(rows(page).nth(2)).toHaveAttribute("aria-selected", "true");
    await expect(rows(page).nth(2)).toHaveAttribute("tabindex", "0");
    await page.keyboard.press("k");
    await page.keyboard.press("ArrowUp");
    await expect(rows(page).first()).toBeFocused();
    await expect(page.locator('[data-testid="grid-row"][tabindex="0"]')).toHaveCount(1);
    // Tab leaves the grid rather than walking every row.
    await page.keyboard.press("Tab");
    await expect(rows(page).nth(1)).not.toBeFocused();
  });
});

test.describe("live updates", () => {
  test("a refreshed cell changes in place and the selection stays on the same trade", async ({
    page,
  }) => {
    await page.clock.install();
    let version = 0;
    await page.route(`${API}/api/trading/trades*`, (route: Route) => {
      const payload = tradesPage();
      if (version > 0) {
        // trd_0003's P&L changes, and it becomes the largest, so a sort by
        // Net P&L moves it to the top.
        payload.items[2] = { ...payload.items[2]!, net_pnl: figure(999, "out_of_sample", "GBP") };
      }
      return route.fulfill({ json: payload });
    });
    await page.goto("/trading/execution");
    await expect(rows(page)).toHaveCount(4);
    await page.getByTestId("grid-sort-net").click();
    await page.getByTestId("grid-sort-net").click(); // descending
    const target = page.locator('[data-row-id="trd_0003"]');
    await target.focus();
    await expect(target).toHaveAttribute("aria-selected", "true");
    await expect(target.locator('td[data-column="net"]')).toHaveText("+118.00");
    const indexBefore = await target.getAttribute("aria-rowindex");

    version = 1;
    await page.clock.fastForward(31_000);
    await expect(target.locator('td[data-column="net"]')).toHaveText("+999.00");
    // Same row, same key: still selected and still focused, though it moved.
    await expect(target).toHaveAttribute("aria-selected", "true");
    await expect(target).toBeFocused();
    expect(await target.getAttribute("aria-rowindex")).not.toBe(indexBefore);
    await expect(page.locator('[data-testid="grid-row"][aria-selected="true"]')).toHaveCount(1);
  });
});

test.describe("CSV export", () => {
  test("carries a provenance and unit column per figure, and 'no data' is empty, not 0", async ({
    page,
  }) => {
    const payload = tradesPage();
    payload.items[1] = { ...payload.items[1]!, net_pnl: figure(null, "paper", "GBP") };
    await tradesWith(page, payload);
    await page.goto("/trading/execution");
    await expect(rows(page)).toHaveCount(4);
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("grid-export").click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^fiboki-trades-.*\.csv$/);
    const text = readFileSync((await download.path())!, "utf8");
    const [header, ...lines] = text.trim().split(/\r\n/);
    const cols = header!.split(",");
    for (const name of ["net", "net_unit", "net_provenance", "source_kind", "source_as_of", "source_path", "exported_at_utc"]) {
      expect(cols).toContain(name);
    }
    const at = (line: string, name: string) => line.split(",")[cols.indexOf(name)];
    const paperRow = lines.find((line) => line.startsWith("paper,trd_0002"))!;
    expect(at(paperRow, "net")).toBe("");
    expect(at(paperRow, "net_provenance")).toBe("paper");
    expect(at(paperRow, "net_unit")).toBe("GBP");
    const backtestRow = lines.find((line) => line.startsWith("backtest,trd_0001"))!;
    expect(at(backtestRow, "net")).toBe("412.5");
    expect(at(backtestRow, "source_kind")).toBe("seed");
    expect(lines).toHaveLength(4);
  });
});

test.describe("the other grids", () => {
  test("candidates keep a chip per figure and explain ineligibility in a popover", async ({
    page,
  }) => {
    await page.goto("/trading/candidates");
    const candidate = page.getByTestId("candidate-row");
    await expect(candidate).toHaveCount(2);
    await expect(candidate.first().getByTestId("provenance-chip")).toHaveCount(6);
    const why = page.getByTestId("candidate-why-donchian_breakout_atr-trigger");
    await why.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("candidate-why-donchian_breakout_atr")).toContainText(
      "primary ranking requires 80",
    );
    // A blocked Promote stays focusable and says why (aria-disabled, not disabled).
    const promote = page.getByTestId("promote-donchian_breakout_atr");
    await expect(promote).toBeDisabled();
    await expect(promote).toHaveAttribute("aria-description", /Not eligible/);
  });

  test("instruments: ?row selects the instrument; an unknown one is said so", async ({ page }) => {
    await page.goto("/markets?row=USDJPY");
    await expect(page.locator('[data-row-id="USDJPY"]')).toHaveAttribute("aria-selected", "true");
    await page.goto("/markets?row=XAUXAG");
    await expect(page.getByTestId("grid-row-missing")).toContainText("XAUXAG");
  });

  test("audit log: rows by sequence, sortable, and ?row selects an entry", async ({ page }) => {
    await page.goto("/system/logs?row=2");
    await expect(rows(page)).toHaveCount(3);
    await expect(page.locator('[data-row-id="2"]')).toHaveAttribute("aria-selected", "true");
    await page.getByTestId("grid-sort-seq").click();
    await expect(page.locator('th[data-column="seq"]')).toHaveAttribute("aria-sort", "ascending");
    await expect(rows(page).first()).toHaveAttribute("data-row-id", "1");
    await page.getByTestId("grid-sort-seq").click();
    await expect(rows(page).first()).toHaveAttribute("data-row-id", "3");
  });

  test("audit log at 1440 px: the time and the outcome badge fit their columns (inventory F-9)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/system/logs");
    await expect(rows(page)).toHaveCount(3);
    for (const column of ["at", "outcome"]) {
      const cells = page.locator(`td[data-column="${column}"]`);
      await expect(cells.first()).toBeVisible();
      const clipped = await cells.evaluateAll((els) =>
        els
          .map((el) => {
            const content = el.firstElementChild as HTMLElement | null;
            const inner = content ? content.getBoundingClientRect() : el.getBoundingClientRect();
            const cell = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            const room = cell.right - parseFloat(style.paddingRight);
            return el.scrollWidth > el.clientWidth + 1 || inner.right > room + 0.5 ? el.textContent : null;
          })
          .filter((text) => text !== null),
      );
      expect(clipped, `${column}: no cell is cut`).toEqual([]);
    }
    // The full stamp is also in the title, for a narrower screen.
    await expect(page.locator('td[data-column="at"] span').first()).toHaveAttribute("title", /UTC$/);
  });
});
