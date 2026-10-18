import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockShell } from "./fixtures";
import { mockResearch, researchFixture } from "./research-fixtures";

/**
 * Research depth (Wave 1B): tearsheet, comparison, parameter surface, job
 * queue, walk-forward, strategy page and the universe correlation matrix,
 * against fixtures the real backend produced (research-fixtures/generate.py).
 * Every number asserted below is read from the fixture, never typed in, so
 * the screens are shown to print what the API sent.
 */

async function open(page: Page, path: string) {
  await mockShell(page);
  await mockResearch(page);
  await page.goto(path);
  await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
}

type Stat = { key: string; figure: { value: number | null } };

test.describe("research tearsheet", () => {
  test("stats, gate checks, definitions, curves, distributions and provenance", async ({ page }) => {
    const sheet = researchFixture<{
      data: {
        stats: Stat[];
        gate_report: { checks: unknown[] } | null;
        monthly: unknown[];
        trade_histogram: { counts: number[] };
        excursions: { trade_id: string[] };
        provenance: { cost_profile: string; strategy_hash: string };
        run: { run_id: string; strategy_id: string; instruments: string[] };
      };
    }>("tearsheet").data;
    await open(page, `/research/runs/${sheet.run.run_id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(sheet.run.run_id);

    const grid = page.getByTestId("stat-grid");
    await expect(grid.locator("[data-testid^='stat-']")).toHaveCount(sheet.stats.length);
    const trades = sheet.stats.find((s) => s.key === "n_trades")!;
    await expect(page.getByTestId("stat-n_trades")).toContainText(String(trades.figure.value));
    // Feature 11: the gate verdict sits beside the number it judged.
    await expect(page.getByTestId("stat-n_trades").getByTestId("gate-check")).toBeVisible();
    await expect(page.getByTestId("gate-table").locator("tbody tr")).toHaveCount(sheet.gate_report!.checks.length);

    // Feature 8: a metric's definition and formula, by keyboard.
    await page.getByTestId("def-sharpe").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("def-sharpe-definition")).toContainText("sqrt(days per year)");
    await page.keyboard.press("Escape");

    // Feature 10: underwater, monthly heatmap, histogram.
    await expect(page.getByTestId("ax-line-equity")).toBeVisible();
    await expect(page.getByTestId("underwater-chart").getByTestId("ax-line-underwater")).toBeVisible();
    await expect(page.getByTestId("month-cell")).toHaveCount(sheet.monthly.length);
    await expect(page.getByTestId("trade-histogram").getByTestId("ax-bin")).toHaveCount(sheet.trade_histogram.counts.length);
    await expect(page.getByTestId("drawdown-table").locator("tbody tr")).not.toHaveCount(0);

    // A shared cursor: arrow keys on the equity chart move every stacked chart.
    const equity = page.getByTestId("equity-chart").locator("svg[role='img']");
    await equity.focus();
    await page.keyboard.press("End");
    await expect(page.getByTestId("equity-chart").locator(".ax__readout-time")).toContainText("UTC");
    await expect(page.getByTestId("underwater-chart").locator(".ax__readout-time")).toContainText("UTC");

    // Feature 24: MAE/MFE per trade, scatter and table.
    await expect(page.getByTestId("excursion-scatter").getByTestId("ax-point")).toHaveCount(sheet.excursions.trade_id.length);
    await page.getByTestId("excursion-mfe").click();
    await expect(page.getByTestId("excursion-scatter")).toContainText("MFE against result");

    // Feature 9: provenance.
    const prov = page.getByTestId("provenance-block");
    await expect(prov).toContainText(sheet.provenance.cost_profile);
    await expect(prov).toContainText(sheet.provenance.strategy_hash.slice(0, 12));

    // Cross-links: the chart with this run's trades, and the strategy page.
    await expect(page.getByTestId("chart-link").first()).toHaveAttribute(
      "href",
      `/markets/${sheet.run.instruments[0]}?run=${sheet.run.run_id}`,
    );
    await expect(page.getByTestId("strategy-link")).toHaveAttribute("href", `/research/strategies/${sheet.run.strategy_id}`);
  });

  test("Monte Carlo shows its seed and the drawdown percentile table", async ({ page }) => {
    const mc = researchFixture<{ data: { seed: number; fan_steps: number[] } }>("monte_carlo").data;
    await open(page, "/research/runs/bt_0001");
    await expect(page.getByTestId("mc-seed-shown")).toContainText(`seed ${mc.seed}`);
    await expect(page.getByTestId("mc-drawdown-table").locator("tbody tr")).toHaveCount(6);
    await expect(page.getByTestId("mc-fan").locator("path.ax__fan")).toHaveCount(2);
    const requested = page.waitForRequest((r) => r.url().includes("/monte-carlo") && r.url().includes("seed=7"));
    await page.getByTestId("mc-seed").fill("7");
    await page.getByRole("button", { name: "Re-draw" }).click();
    await requested;
  });
});

test.describe("research comparison", () => {
  test("rebased equity, side-by-side stats and return correlation", async ({ page }) => {
    const cmp = researchFixture<{ data: { runs: { run: { run_id: string }; stats: Stat[] }[] } }>("compare").data;
    const ids = cmp.runs.map((r) => r.run.run_id);
    await open(page, `/research/compare?runs=${ids.join(",")}`);
    for (const id of ids) await expect(page.getByTestId(`ax-line-${id}`)).toBeVisible();
    await expect(page.getByTestId("compare-stats").locator("tbody tr")).toHaveCount(cmp.runs[0]!.stats.length + 1);
    await expect(page.getByTestId("corr-cell")).toHaveCount(ids.length * ids.length);
    await page.getByRole("button", { name: `Remove ${ids[2]}` }).click();
    await expect(page).toHaveURL(new RegExp(`runs=${ids[0]}(%2C|,)${ids[1]}$`));
  });

  test("runs list: tick two runs to compare them", async ({ page }) => {
    await open(page, "/research/runs");
    const picks = page.getByTestId("run-pick");
    await picks.nth(0).check();
    await expect(page.getByTestId("compare-selected")).toHaveAttribute("aria-disabled", "true");
    await picks.nth(1).check();
    await expect(page.getByTestId("compare-selected")).toHaveAttribute("href", /\/research\/compare\?runs=bt_\d{4},bt_\d{4}/);
  });
});

test.describe("parameter heatmap", () => {
  test("a 2D surface with the plateau region and selectable cells", async ({ page }) => {
    const s = researchFixture<{
      data: { x_values: number[]; y_values: number[]; cells: { on_plateau: boolean; n_runs: number }[]; runs_used: number };
    }>("surface").data;
    await open(page, "/research/parameter-lab?strategy=ichimoku_kumo_trend");
    const heat = page.getByTestId("surface-heatmap");
    // Cropped to the cells that were run (plus one step) by default; the full
    // declared domain on request.
    await expect(heat.getByTestId("surface-cell").first()).toBeVisible();
    const cropped = await heat.getByTestId("surface-cell").count();
    expect(cropped).toBeLessThan(s.x_values.length * s.y_values.length);
    expect(cropped).toBeGreaterThanOrEqual(s.runs_used);
    await page.getByTestId("surface-full").check();
    await expect(heat.getByTestId("surface-cell")).toHaveCount(s.x_values.length * s.y_values.length);
    await expect(page.getByTestId("surface-runs-used")).toContainText(String(s.runs_used));
    const plateau = s.cells.filter((c) => c.on_plateau).length;
    await expect(heat.locator("td[data-marks~='plateau']")).toHaveCount(plateau);
    await heat.locator("td[data-testid='surface-cell'] button").first().click();
    await expect(page.getByTestId("surface-selected")).toContainText("bt_");
    const req = page.waitForRequest((r) => r.url().includes("/surface") && r.url().includes("metric=total_return_pct"));
    await page.getByTestId("surface-metric").selectOption("total_return_pct");
    await req;
  });
});

test.describe("research job queue", () => {
  test("jobs by state, shard progress with ETAs, links to outputs", async ({ page }) => {
    const q = researchFixture<{ data: { counts: Record<string, number>; shards: unknown[] } }>("jobs").data;
    await open(page, "/research/jobs");
    await expect(page.getByTestId("count-succeeded")).toHaveText(String(q.counts.succeeded));
    await expect(page.getByTestId("count-dead_letter")).toHaveText(String(q.counts.dead_letter));
    await expect(page.getByTestId("jobs-live").locator("tr[data-status='pending']")).toHaveCount(q.counts.pending!);
    await expect(page.getByTestId("shard-row")).toHaveCount(q.shards.length);
    // A shard still in progress carries an estimated finish; a finished one does not.
    await expect(page.locator("tr[data-testid='shard-row']").filter({ hasText: "IN PROGRESS" }).first().getByTestId("shard-eta")).toContainText("est");
    await expect(page.getByTestId("jobs-done").locator("a[href^='/research/runs/bt_']").first()).toBeVisible();
  });
});

test.describe("walk-forward, strategy page and correlation", () => {
  test("walk-forward folds with the rung-2 gates", async ({ page }) => {
    const wf = researchFixture<{ data: { folds: unknown[]; outcome: string } }>("walk_forward").data;
    await open(page, "/research/walk-forward?strategy=donchian_breakout_atr");
    await expect(page.getByTestId("wf-fold")).toHaveCount(wf.folds.length);
    await expect(page.getByTestId("wf-outcome")).toHaveAttribute("data-value", wf.outcome);
    await expect(page.getByTestId("wf-gates").getByTestId("gate-check").first()).toBeVisible();
  });

  test("strategy page links its runs, ladder and lifecycle", async ({ page }) => {
    await open(page, "/research/strategies/ichimoku_kumo_trend");
    await expect(page.getByTestId("strategy-runs").locator("tbody tr")).not.toHaveCount(0);
    await expect(page.getByTestId("ladder-history")).toBeVisible();
    await expect(page.getByTestId("link-walk-forward")).toHaveAttribute("href", /walk-forward\?strategy=ichimoku_kumo_trend/);
    await expect(page.getByTestId("link-runs")).toHaveAttribute("href", "/research/runs?strategy=ichimoku_kumo_trend");
  });

  test("universe correlation with a window selector", async ({ page }) => {
    const c = researchFixture<{ data: { instruments: string[] } }>("correlations").data;
    await open(page, "/research/correlations");
    await expect(page.getByTestId("universe-corr-cell")).toHaveCount(c.instruments.length ** 2);
    const req = page.waitForRequest((r) => r.url().includes("/api/research/correlations") && r.url().includes("window=250"));
    await page.getByTestId("corr-window-250").click();
    await req;
    await expect(page).toHaveURL(/window=250/);
  });
});

const A11Y_ROUTES = [
  "/research/runs/bt_0001",
  "/research/compare?runs=bt_0001,bt_0002,bt_0003",
  "/research/parameter-lab?strategy=ichimoku_kumo_trend",
  "/research/jobs",
  "/research/walk-forward?strategy=donchian_breakout_atr",
  "/research/strategies/ichimoku_kumo_trend",
  "/research/correlations",
  "/research/runs",
];

for (const theme of ["dark", "light"] as const) {
  test.describe(`research a11y with data, ${theme}`, () => {
    test.use({ colorScheme: theme });
    for (const route of A11Y_ROUTES) {
      test(route, async ({ page }, info) => {
        test.skip(info.project.name !== "desktop", "one sweep, on the desktop project");
        test.setTimeout(90_000);
        // Tall enough that no control sits under the fixed status bar while axe
        // measures target sizes: the audit is of the content, not the scroll position.
        await page.setViewportSize({ width: 1440, height: 2000 });
        await open(page, route);
        await page.locator('[data-testid="state-loading"]').first().waitFor({ state: "detached", timeout: 8_000 }).catch(() => undefined);
        await page.evaluate(() => document.fonts.ready);
        const results = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
          .analyze();
        const blocking = results.violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => ({ id: v.id, help: v.help, targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")) }));
        expect(blocking, `${route} (${theme})`).toEqual([]);
      });
    }
  });
}
