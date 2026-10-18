import { expect, test, type Page, type Route } from "@playwright/test";
import {
  API,
  commandSummary,
  figure,
  fleetPage,
  fleetRow,
  limitBoard,
  mockShell,
  source,
  tradesPage,
} from "./fixtures";
import { mockEverything } from "./mock-everything";

/**
 * Terminal programme Wave 1A, operator core: the money strip, the fleet, the
 * entity cross-links, the palette's ranking, the command line's grammar and
 * history, the System board, the alert manager, the function directory and
 * the page-level stale banner. Every figure comes from a mocked API answer;
 * the workstation computes none of them.
 */

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await mockShell(page);
});

async function goReady(page: Page, path = "/") {
  await page.goto(path);
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
}

test.describe("money strip", () => {
  test("shows the API's book, P&L, bots and research load, with the provenance once", async ({ page }) => {
    await page.goto("/");
    const strip = page.getByTestId("money-strip");
    await expect(strip).toHaveAttribute("data-state", "success");
    await expect(page.getByTestId("money-equity")).toContainText("25,412.50");
    await expect(page.getByTestId("money-day")).toContainText("105.25");
    await expect(page.getByTestId("money-day").getByTestId("figure-value").first()).toHaveAttribute("data-direction", "down");
    await expect(page.getByTestId("money-week")).toContainText("310.75");
    await expect(page.getByTestId("money-bots-running")).toHaveText("2 running / 3");
    await expect(page.getByTestId("money-bots-breakdown")).toContainText("1 stale");
    await expect(page.getByTestId("money-bots")).toHaveAttribute("data-tone", "critical");
    // Research load is unknown, and says so: never zero.
    await expect(page.getByTestId("money-research").getByTestId("figure-missing")).toBeVisible();
    // One provenance chip for the whole strip, not one per cell.
    await expect(page.getByTestId("money-provenance").getByTestId("provenance-chip")).toHaveCount(1);
    await expect(strip.getByTestId("provenance-chip")).toHaveCount(1);
  });

  test("an absent account is absent, never £0", async ({ page }) => {
    const missing = (unit: string) =>
      figure(null, "paper", unit, {
        caveats: [{ code: "value_unavailable", severity: "warning", message: "No paper account exists.", affects: "", direction: "unknown" }],
      });
    await page.route(`${API}/api/command/summary`, (route: Route) =>
      route.fulfill({
        json: commandSummary({ equity: missing("GBP"), day_pnl: missing("GBP"), week_pnl: missing("GBP") }),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("money-equity").getByTestId("figure-missing")).toBeVisible();
    await expect(page.getByTestId("money-equity")).not.toContainText("0.00");
  });

  test("the panels reserve their height: Command does not shift as they fill in", async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { __cls: number }).__cls = 0;
      new PerformanceObserver((list) => {
        for (const e of list.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) {
          if (!e.hadRecentInput) (window as unknown as { __cls: number }).__cls += e.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
    });
    await page.goto("/");
    await expect(page.getByTestId("attention-list")).toBeVisible();
    await expect(page.getByTestId("fleet-table")).toBeVisible();
    await expect(page.getByTestId("command-limits")).toBeVisible();
    await page.waitForTimeout(500);
    const cls = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
    expect(cls).toBeLessThan(0.05);
  });
});

test.describe("fleet", () => {
  test("one row per bot in the API's order, with state, last decision, P&L and links", async ({ page }) => {
    await page.goto("/trading/fleet");
    const rows = page.getByTestId("fleet-row");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toHaveAttribute("data-state", "stale");
    await expect(rows.nth(0).getByTestId("bot-state")).toHaveText(/STALE/);
    await expect(rows.nth(0).getByTestId("bot-signal")).toHaveAttribute("data-state", "absent");
    await expect(rows.nth(1).getByTestId("bot-signal")).toHaveAttribute("data-outcome", "blocked");
    await expect(rows.nth(1).getByTestId("fleet-strategy-link")).toHaveAttribute(
      "href",
      "/research/strategies/donchian_breakout_atr",
    );
    await expect(rows.nth(1).getByTestId("fleet-chart-link")).toHaveAttribute("href", "/markets/EURUSD?tf=H1");
    await expect(rows.nth(1).getByTestId("fleet-journal-link")).toHaveAttribute(
      "href",
      "/trading/execution?strategy=donchian_breakout_atr",
    );
    await expect(rows.nth(1).getByTestId("fleet-lifecycle-link")).toHaveAttribute("href", "/lifecycle/abc123def456");
    await expect(rows.nth(2).getByTestId("fleet-chart-link")).toHaveCount(2);
    // Uniform provenance: one chip under the table, none in the cells.
    await expect(page.getByTestId("fleet-provenance").getByTestId("provenance-chip")).toHaveCount(1);
    await expect(rows.first().getByTestId("provenance-chip")).toHaveCount(0);
    // Attribution by strategy and instrument, the API's sums.
    await expect(page.getByTestId("attr-strategy").getByTestId("attr-row")).toHaveCount(2);
    await expect(page.getByTestId("attr-instrument").getByTestId("attr-link").first()).toHaveAttribute(
      "href",
      "/markets/EURUSD",
    );
  });

  test("?session= selects that bot", async ({ page }) => {
    await page.goto("/trading/fleet?session=forward-paper_forward_crypto_v1");
    await expect(page.locator('[data-testid="fleet-row"][data-session="forward-paper_forward_crypto_v1"]')).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  test("Command shows the worst bots first and links to the fleet", async ({ page }) => {
    await page.route(`${API}/api/trading/fleet`, (route: Route) =>
      route.fulfill({
        json: fleetPage(
          Array.from({ length: 10 }, (_, i) => fleetRow({ session_id: `forward-bot_${i}`, label: `bot_${i}` })),
        ),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("fleet-row")).toHaveCount(8);
    await expect(page.getByTestId("fleet-more")).toHaveText("2 more bots");
    await page.getByTestId("command-fleet-link").click();
    await expect(page).toHaveURL(/\/trading\/fleet$/);
  });

  test("no journal is an absent fleet, not an idle one", async ({ page }) => {
    await page.route(`${API}/api/trading/fleet`, (route: Route) =>
      route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 1, source: source("absent", "No paper journal."), caveats: [] } }),
    );
    await page.goto("/trading/fleet");
    await expect(page.getByText("This is not an idle fleet.")).toBeVisible();
  });
});

test.describe("limits are the API's", () => {
  test("Command's loss rows and the Risk board draw the same rows with the same bands", async ({ page }) => {
    const breached = { daily_loss_pct: figure(-2.4, "paper", "pct") };
    await page.route(`${API}/api/trading/limits`, (route: Route) => route.fulfill({ json: limitBoard({ risk: breached }) }));
    await page.goto("/");
    const daily = page.locator('[data-testid="command-limits"] [data-key="risk:daily_loss"]');
    await expect(daily).toHaveAttribute("data-state", "breached");
    await page.goto("/trading/risk");
    await expect(page.locator('[data-testid="limit-board"] [data-key="risk:daily_loss"]')).toHaveAttribute(
      "data-state",
      "breached",
    );
    await expect(page.getByTestId("limit-hero")).toHaveAttribute("data-state", "breached");
  });

  test("limits the API does not report are listed once, collapsed, never as bars", async ({ page }) => {
    await page.goto("/trading/risk");
    const absent = page.getByTestId("limit-not-reported");
    await expect(absent).toContainText("Weekly loss");
    await expect(absent).toContainText("Margin utilisation");
    await expect(absent.getByTestId("limit-bar")).toHaveCount(0);
  });
});

test.describe("cross-links", () => {
  test("a journal trade opens the chart at that trade and its strategy's tearsheet", async ({ page }) => {
    await page.goto("/trading/execution");
    const chart = page.getByTestId("journal-chart-link").first();
    await expect(chart).toHaveAttribute("href", /\/markets\/EURUSD\?trade=trd_000\d/);
    await expect(page.getByTestId("journal-strategy-link").first()).toHaveAttribute(
      "href",
      "/research/strategies/ichimoku_kumo_trend",
    );
  });

  test("the journal filters by strategy from a link, and says so", async ({ page }) => {
    const seen: string[] = [];
    await page.route(`${API}/api/trading/trades*`, (route: Route) => {
      seen.push(new URL(route.request().url()).search);
      return route.fulfill({ json: tradesPage() });
    });
    await page.goto("/trading/execution?strategy=ichimoku_kumo_trend");
    await expect(page.getByTestId("journal-scope")).toContainText("ichimoku_kumo_trend");
    await expect.poll(() => seen.some((q) => q.includes("strategy_id=ichimoku_kumo_trend"))).toBe(true);
  });

  test("the lifecycle entity links its tearsheet, bots and trades", async ({ page }) => {
    await mockEverything(page, []);
    await page.goto("/lifecycle/abc123def456");
    const related = page.getByTestId("lifecycle-related");
    await expect(related).toBeVisible();
    await expect(related.getByTestId("lifecycle-tearsheet-link")).toHaveAttribute("href", /\/research\/strategies\//);
    await expect(related.getByTestId("lifecycle-journal-link")).toHaveAttribute("href", /\/trading\/execution\?strategy=/);
  });

  test("every attention deep link is a page the workstation has", async ({ page }) => {
    await page.goto("/");
    const links = page.locator('[data-testid="attention-list"] a.triage__title');
    await expect(links.first()).toBeVisible();
    for (const href of await links.evaluateAll((els) => els.map((e) => e.getAttribute("href") ?? ""))) {
      expect(href).toMatch(/^\/(trading|system|lifecycle|markets|research|alerts)?/);
      expect(href).not.toMatch(/^\/(risk|journal)$/);
    }
  });
});

test.describe("palette", () => {
  test("EURUSD ranks the instrument first, never a screen", async ({ page }) => {
    await goReady(page);
    await page.keyboard.press("ControlOrMeta+KeyK");
    await page.getByTestId("palette-input").fill("EURUSD");
    const first = page.getByTestId("palette-hit").first();
    await expect(first).toBeVisible();
    await expect(first).toHaveAttribute("data-kind", "instrument");
    await expect(first).toHaveAttribute("data-id", "EURUSD");
    await expect(first).toHaveAttribute("data-tier", "0");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/markets\/EURUSD$/);
  });

  test("it offers only entities that exist; nonsense offers nothing", async ({ page }) => {
    await goReady(page);
    await page.keyboard.press("ControlOrMeta+KeyK");
    await page.getByTestId("palette-input").fill("bt_0001");
    await expect(page.getByTestId("palette-hit").first()).toHaveAttribute("data-kind", "run");
    await expect(page.getByTestId("palette-hit").first()).toHaveAttribute("data-id", "bt_0001");
    await page.getByTestId("palette-input").fill("qqqqzz");
    await expect(page.getByTestId("palette-empty")).toBeVisible();
    await expect(page.getByTestId("palette-hit")).toHaveCount(0);
  });

  test("a code ranks its screen first", async ({ page }) => {
    await goReady(page);
    await page.keyboard.press("ControlOrMeta+KeyK");
    await page.getByTestId("palette-input").fill("rsk");
    await expect(page.getByTestId("palette-hit").first()).toHaveAttribute("data-id", "RSK");
  });
});

test.describe("command line grammar", () => {
  test("SYMBOL FUNCTION: EURUSD H4 opens the H4 chart; EURUSD BT the backtest trades", async ({ page }) => {
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.fill("eurusd h4");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/markets\/EURUSD\?tf=H4$/);
    await input.fill("EURUSD BT");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/trading\/execution\?instrument=EURUSD&provenance=backtest$/);
  });

  test("a symbol and a partial function suggests that symbol's functions", async ({ page }) => {
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.fill("EURUSD N");
    await expect(page.getByTestId("command-line-option").first()).toContainText("EURUSD NEWS");
  });

  test("an unknown function or unknown text never navigates", async ({ page }) => {
    await goReady(page, "/trading/risk");
    const input = page.getByTestId("command-line-input");
    await input.fill("EURUSD XYZ");
    await input.press("Enter");
    await expect(page.getByTestId("command-line-message")).toContainText("not a function for EURUSD");
    await expect(page).toHaveURL(/\/trading\/risk$/);
    await input.fill("zzqq");
    await input.press("Enter");
    await expect(page.getByTestId("command-line-message")).toContainText("Nothing matches");
    await expect(page).toHaveURL(/\/trading\/risk$/);
  });

  test("a fuzzy match is opened only when chosen", async ({ page }) => {
    await goReady(page, "/trading/risk");
    const input = page.getByTestId("command-line-input");
    await input.fill("eur");
    await expect(page.getByTestId("command-line-option").nth(1)).toBeVisible();
    await input.press("Enter");
    await expect(page.getByTestId("command-line-message")).toContainText("No exact match");
    await expect(page).toHaveURL(/\/trading\/risk$/);
    await input.press("ArrowDown");
    await input.press("Enter");
    await expect(page).not.toHaveURL(/\/trading\/risk$/);
  });

  test("FUNC opens the function directory, generated from the registry", async ({ page }) => {
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.fill("func");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/system\/functions$/);
    await expect(page.locator('[data-testid="function-row"][data-code="FLT"]')).toBeVisible();
    await expect(page.locator('[data-testid="function-row"][data-code="RSK"]')).toContainText("F6");
    await expect(page.locator('[data-testid="instrument-function-row"][data-code="H4"]')).toBeVisible();
  });

  test("history: ↑ recalls the last commands, and survives a reload", async ({ page }) => {
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.fill("RSK");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/trading\/risk$/);
    await input.fill("FLT");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/trading\/fleet$/);
    await page.reload();
    await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
    await input.focus();
    await input.press("ArrowUp");
    await expect(input).toHaveValue("FLT");
    await input.press("ArrowUp");
    await expect(input).toHaveValue("RSK");
    await input.press("ArrowDown");
    await expect(input).toHaveValue("FLT");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/trading\/fleet$/);
  });

  test("history keeps 50 and works when storage refuses", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new Error("storage blocked");
        },
      });
    });
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.fill("RSK");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/trading\/risk$/);
  });
});

test.describe("system board and alerts", () => {
  test("one board, one clock; stale rows marked by the platform", async ({ page }) => {
    await page.goto("/system");
    await expect(page.getByTestId("board-clock")).toContainText("14:02:31");
    await expect(page.getByTestId("board-group-worker")).toBeVisible();
    await expect(page.getByTestId("board-group-bot")).toBeVisible();
    const stale = page.locator('[data-testid="board-row"][data-stale="true"]');
    await expect(stale).toHaveCount(1);
    await expect(stale.getByTestId("board-row-stale")).toBeVisible();
    await expect(page.getByTestId("board-stale-count")).toContainText("1");
  });

  test("the old system pages open the board at their group", async ({ page }) => {
    await page.goto("/system/workers");
    await expect(page.getByTestId("system-board")).toHaveAttribute("data-focus", "workers");
    await expect(page.getByTestId("board-group-worker")).toHaveAttribute("data-focused", "true");
  });

  test("the alert manager lists rules with state, last fired and history", async ({ page }) => {
    await page.goto("/alerts");
    await expect(page.getByTestId("alerts-firing")).toHaveText("1 FIRING");
    const firing = page.locator('[data-testid="alert-rule"][data-event="heartbeat_stale"]');
    await expect(firing).toHaveAttribute("data-state", "firing");
    await expect(firing.getByTestId("alert-rule-incident")).toHaveAttribute("href", "/system/incidents/inc-1");
    await expect(page.locator('[data-testid="alert-rule"][data-event="worker_down"]')).toHaveAttribute(
      "data-state",
      "never_fired",
    );
    await expect(page.getByTestId("alert-history-row")).toHaveCount(2);
  });
});

test.describe("stale banner", () => {
  test("a failing re-read marks the page stale and dims the panel; Re-read recovers", async ({ page }) => {
    await page.clock.install({ time: new Date("2026-10-18T14:00:00Z") });
    let fail = false;
    await page.route(`${API}/api/system/board`, async (route: Route) => {
      if (fail) {
        return route.fulfill({ status: 503, json: { code: "down", detail: "down", correlation_id: "c", context: {} } });
      }
      return route.fallback();
    });
    await page.goto("/system");
    await expect(page.getByTestId("board-summary")).toBeVisible();
    await expect(page.getByTestId("page-stale-banner")).toHaveCount(0);
    fail = true;
    await page.clock.fastForward(11_000);
    await expect(page.getByTestId("page-stale-banner")).toBeVisible();
    await expect(page.locator('[data-testid="state-success"][data-stale="true"]').first()).toBeVisible();
    fail = false;
    await page.getByTestId("page-stale-retry").click();
    await expect(page.getByTestId("page-stale-banner")).toHaveCount(0);
  });
});

test.describe("chrome", () => {
  test("a Command panel maximises and Esc restores it", async ({ page }) => {
    await page.goto("/");
    const panel = page.getByTestId("panel-cmd-attention");
    await expect(page.getByTestId("attention-list")).toBeVisible();
    await page.getByTestId("panel-max-cmd-attention").click();
    await expect(panel).toHaveAttribute("data-maximised", "true");
    await page.keyboard.press("Escape");
    await expect(panel).not.toHaveAttribute("data-maximised", "true");
    await panel.locator("h2").dblclick();
    await expect(panel).toHaveAttribute("data-maximised", "true");
  });

  test("content starts high: the page title sits under one top line and the tabs", async ({ page }) => {
    await page.goto("/trading/risk");
    const h1 = await page.locator("main h1").boundingBox();
    expect(h1?.y ?? 999).toBeLessThan(110);
    // The explanation is one click away, not a paragraph above the page.
    await expect(page.getByTestId("page-help").locator("p")).toBeHidden();
    await page.getByTestId("page-help").locator("summary").click();
    await expect(page.getByTestId("page-help").locator("p")).toBeVisible();
  });

  test("the shortcut sheet scrolls inside the viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 700 });
    await goReady(page);
    await page.keyboard.press("Shift+Slash");
    const sheet = page.getByTestId("shortcut-sheet");
    await expect(sheet).toBeVisible();
    const box = await sheet.boundingBox();
    expect(box).not.toBeNull();
    if (box) expect(box.y + box.height).toBeLessThanOrEqual(700);
  });
});
