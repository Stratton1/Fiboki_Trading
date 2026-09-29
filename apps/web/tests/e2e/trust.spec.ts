import { expect, test, type Page, type Route } from "@playwright/test";
import {
  API,
  figure,
  healthReport,
  killSwitchView,
  mockShell,
  modeBanner,
  riskState,
  source,
} from "./fixtures";
import { MockStream } from "./sse";

/**
 * One spec per trust defect from report G §1.2 fixed in Wave 3. Each test is
 * the defect's exact failure, asserted not to happen.
 */

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "trust defects are viewport-independent");
});

async function tokenColour(page: Page, token: string): Promise<string> {
  return page.evaluate((name) => {
    const probe = document.createElement("div");
    probe.style.color = `var(${name})`;
    document.body.append(probe);
    const colour = getComputedStyle(probe).color;
    probe.remove();
    return colour;
  }, token);
}

test.describe("W-03: the worker entry is toned by the platform's verdict", () => {
  test("over the stream: the platform says stale at a young age, the bar is critical", async ({
    page,
  }) => {
    const stream = new MockStream({ risk: { state: riskState() } });
    stream.workerAgeS = 30;
    stream.workerState = "stale";
    await mockShell(page);
    await stream.mirrorRest(page);
    await stream.install(page);
    await page.goto("/trading/risk");
    const worker = page.getByTestId("status-worker");
    await expect(worker).toHaveAttribute("data-worker-state", "stale");
    await expect(worker).toHaveAttribute("data-tone", "critical");
    await expect(worker).toContainText("stale");
  });

  test("over REST: a health report whose worker check is down is critical, however young the age", async ({
    page,
  }) => {
    await mockShell(page);
    await page.route(`${API}/api/health`, (route: Route) =>
      route.fulfill({
        json: healthReport({
          worker_heartbeat_age_seconds: 12,
          checks: [
            { name: "worker_heartbeat", status: "down", detail: "stale", critical: true, latency_ms: null },
          ],
        }),
      }),
    );
    await page.goto("/");
    const worker = page.getByTestId("status-worker");
    await expect(worker).toHaveAttribute("data-tone", "critical");
    await expect(worker).toContainText("stale");
  });

  test("over REST: the platform says ok, the bar is ok", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/health`, (route: Route) =>
      route.fulfill({
        json: healthReport({
          checks: [
            { name: "worker_heartbeat", status: "ok", detail: "beat 12s ago", critical: true, latency_ms: null },
          ],
        }),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("status-worker")).toHaveAttribute("data-tone", "ok");
  });

  test("a heartbeat three hours old is never green, with or without a verdict", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/health`, (route: Route) =>
      route.fulfill({ json: healthReport({ worker_heartbeat_age_seconds: 3 * 3600 }) }),
    );
    await page.goto("/");
    const worker = page.getByTestId("status-worker");
    await expect(worker).toHaveAttribute("data-tone", "critical");
    await expect(worker).toContainText("3h ago");
  });
});

test.describe("W-04: 'data as of' is the oldest on screen, probes excluded", () => {
  test("the oldest view wins; the health check's own timestamp does not count", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/health`, (route: Route) =>
      route.fulfill({ json: healthReport({ checked_at: "2026-09-19T12:05:00Z" }) }),
    );
    await page.route(`${API}/api/trading/risk`, (route: Route) =>
      route.fulfill({
        json: {
          data: riskState(),
          source: { kind: "live", detail: "Risk.", as_of: "2026-09-19T09:00:00Z" },
          caveats: [],
        },
      }),
    );
    await page.route(`${API}/api/system/kill-switch`, (route: Route) =>
      route.fulfill({
        json: { ...killSwitchView(), source: { ...source("live"), as_of: "2026-09-19T11:30:00Z" } },
      }),
    );
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-status")).toBeVisible();
    const asOf = page.getByTestId("status-as-of");
    await expect(asOf).toHaveAttribute("data-as-of", "2026-09-19T09:00:00Z");
    await expect(asOf).toHaveAttribute("data-path", "/api/trading/risk");
    await expect(asOf).toContainText("09:00:00 UTC");

    // The inspector lists every view's as-of, oldest first.
    await asOf.click();
    const detail = page.getByTestId("inspector-as-of");
    await expect(detail).toContainText("/api/trading/risk");
    await expect(detail).toContainText("/api/system/kill-switch");
    await expect(detail).not.toContainText("/api/health");
  });

  test("a screen with no data views does not borrow the health check's time", async ({ page }) => {
    await mockShell(page);
    await page.goto("/system/legend");
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "paper");
    await expect(page.getByTestId("status-api")).toContainText("API ok");
    await expect(page.getByTestId("status-as-of")).toHaveText("no data as-of on screen");
  });
});

test.describe("W-05: a view that neither polls nor streams goes stale by age", () => {
  test("the candidates list, loaded and left, is marked stale with its age after five minutes", async ({
    page,
  }) => {
    await page.clock.install();
    await mockShell(page);
    await page.goto("/trading/candidates");
    const panel = page.locator('[data-testid="state-success"][data-label="candidates"]');
    await expect(panel).toHaveAttribute("data-freshness", "fresh");
    await page.clock.fastForward(4 * 60_000);
    await expect(panel).toHaveAttribute("data-freshness", "fresh");
    await page.clock.fastForward(90_000);
    await expect(panel).toHaveAttribute("data-freshness", "stale");
    const badge = panel.getByTestId("state-stale");
    await expect(badge).toContainText("STALE");
    await expect(badge.getByTestId("state-stale-age")).toContainText("loaded 6m ago");
    // The numbers stay; Retry re-reads and the view is fresh again.
    await expect(page.getByTestId("candidate-row")).toHaveCount(2);
    await panel.getByTestId("state-stale-retry").click();
    await expect(panel).toHaveAttribute("data-freshness", "fresh");
  });
});

test.describe("W-06: a badge's tone comes from its value", () => {
  const validation = (verdict: string, available: boolean) => ({
    strategy_id: `s_${verdict}`,
    verdict,
    report_version: "v1",
    dataset_version_id: "ds1",
    gate_set_version: "gates_v1",
    rungs_passed: figure(2, "holdout", "count"),
    rungs_total: figure(5, "holdout", "count"),
    binding_constraint: "",
    provenance_labels: {},
    available,
    detail: "",
  });

  test("REJECT is never drawn with the OK badge", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/research/validation`, (route: Route) =>
      route.fulfill({
        json: {
          items: [
            validation("reject", true),
            validation("promote", true),
            validation("incomplete", true),
            validation("not_validated", false),
            validation("something_new", true),
          ],
          total: 5,
          offset: 0,
          limit: 100,
          source: source("live"),
          caveats: [],
        },
      }),
    );
    await page.goto("/research/validation");
    const badge = (verdict: string) =>
      page.locator(`[data-testid="verdict-badge"][data-value="${verdict}"]`);
    await expect(badge("reject")).toHaveAttribute("data-tone", "down");
    await expect(badge("reject")).toHaveClass(/badge--down/);
    await expect(badge("promote")).toHaveAttribute("data-tone", "ok");
    await expect(badge("incomplete")).toHaveAttribute("data-tone", "degraded");
    await expect(badge("not_validated")).toHaveAttribute("data-tone", "unknown");
    await expect(badge("something_new")).toHaveAttribute("data-tone", "unknown");
    await expect(badge("something_new")).toContainText("UNRECOGNISED");
  });

  test("PENDING data quality is not OK", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/markets/data-quality`, (route: Route) =>
      route.fulfill({
        json: {
          items: [
            {
              instrument: "EURUSD",
              available: true,
              quality: "pending",
              detail: "Mounted; not wired.",
              bars: figure(null, "backtest", "count"),
              gaps: figure(null, "backtest", "count"),
              stale_runs: figure(null, "backtest", "count"),
            },
          ],
          total: 1,
          offset: 0,
          limit: 50,
          source: source("live"),
          caveats: [],
        },
      }),
    );
    await page.goto("/markets/data-quality");
    const badge = page.getByTestId("quality-badge");
    await expect(badge).toHaveAttribute("data-tone", "unknown");
    await expect(badge).not.toHaveClass(/badge--ok/);
  });
});

test.describe("W-07: the banner and the panel agree after an arm, without the stream", () => {
  test("the banner shows KILL SWITCH ARMED as soon as the dialog closes, not on the next poll", async ({
    page,
  }) => {
    await mockShell(page);
    let armed = false;
    await page.route(`${API}/api/system/kill-switch`, (route: Route) =>
      route.fulfill({
        json: armed ? killSwitchView({ active: true, mode: "pause", operator: "joe" }) : killSwitchView(),
      }),
    );
    await page.route(`${API}/api/system/execution-mode`, (route: Route) =>
      route.fulfill({
        json: modeBanner(armed ? { kill_switch_active: true, kill_switch_mode: "pause" } : {}),
      }),
    );
    await page.route(`${API}/api/system/kill-switch/arm`, async (route: Route) => {
      armed = true;
      await route.fulfill({ json: killSwitchView({ active: true, mode: "pause" }) });
    });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("mode-banner-killswitch-off")).toBeVisible();
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-pause").click();
    await page.getByTestId("confirm-reason").fill("spread blowout on the open");
    await page.getByTestId("confirm-submit").click();
    await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "true");
    // The mode poll is 30 s; the banner must follow in well under that.
    await expect(page.getByTestId("mode-banner-killswitch")).toBeVisible({ timeout: 3_000 });
  });
});

test.describe("W-10: charts draw in tokens, never loss red for LIVE", () => {
  test("a LIVE exposure bar is magenta (the mode colour), not the loss colour", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/trading/exposure`, (route: Route) =>
      route.fulfill({
        json: {
          items: [
            {
              key: "EURUSD",
              label: "EURUSD",
              exposure_pct: figure(4, "broker_live", "pct"),
              limit_pct: figure(10, "broker_live", "pct"),
              utilisation_pct: figure(40, "broker_live", "pct"),
              breached: false,
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
    await page.goto("/trading/exposure");
    const bar = page.locator(".chart__bar").first();
    await expect(bar).toHaveAttribute("data-ink", "broker_live");
    const fill = await bar.evaluate((el) => getComputedStyle(el).fill);
    expect(fill).toBe(await tokenColour(page, "--mode-live"));
    expect(fill).not.toBe(await tokenColour(page, "--pnl-down"));
  });

  test("the correlation heatmap is blue/orange, and its values are readable as text", async ({
    page,
  }) => {
    await mockShell(page);
    await page.route(`${API}/api/markets/correlations`, (route: Route) =>
      route.fulfill({
        json: {
          data: {
            instruments: ["EURUSD", "GBPUSD"],
            matrix: [
              [1, -1],
              [-1, 1],
            ],
            window_bars: 90,
            available: true,
          },
          source: source("live"),
          caveats: [],
        },
      }),
    );
    await page.goto("/markets/correlations");
    const cells = page.getByTestId("heat-cell");
    await expect(cells).toHaveCount(4);
    const pos = await cells.nth(0).evaluate((el) => getComputedStyle(el).fill);
    const neg = await cells.nth(1).evaluate((el) => getComputedStyle(el).fill);
    expect(pos).toBe(await tokenColour(page, "--div-pos"));
    expect(neg).toBe(await tokenColour(page, "--div-neg"));
    expect(pos).not.toBe(await tokenColour(page, "--pnl-down"));
    await page.getByTestId("chart-data").locator("summary").click();
    await expect(page.getByTestId("chart-data")).toContainText("\u22121.00");
  });
});

test.describe("W-11/W-12: a line chart breaks at a gap and is readable without a mouse", () => {
  test("a missing point is a hatched gap; arrows read values; View data lists them", async ({
    page,
  }) => {
    await mockShell(page);
    const point = (t: string, v: number | null) => ({ t, v });
    await page.route(`${API}/api/trading/portfolio`, (route: Route) =>
      route.fulfill({
        json: {
          data: {
            balance: figure(10000, "paper", "GBP"),
            equity: figure(10100, "paper", "GBP"),
            realised_pnl: figure(100, "paper", "GBP"),
            unrealised_pnl: figure(0, "paper", "GBP"),
            open_positions: figure(0, "paper", "count"),
            max_drawdown_pct: figure(1.2, "paper", "pct"),
            provenance_mix: { paper: 3 },
            equity_curve: {
              name: "Equity",
              provenance: "paper",
              unit: "GBP",
              points: [
                point("2026-09-15T00:00:00Z", 10000),
                point("2026-09-16T00:00:00Z", 10050),
                point("2026-09-17T00:00:00Z", null),
                point("2026-09-18T00:00:00Z", 10020),
                point("2026-09-19T00:00:00Z", 10100),
              ],
              caveats: [],
            },
          },
          source: source("live"),
          caveats: [],
        },
      }),
    );
    await page.goto("/trading/portfolio");
    const chart = page.getByTestId("chart").first();
    await expect(chart.getByTestId("chart-gap")).toHaveCount(1);
    await expect(chart.getByTestId("chart-line")).toHaveCount(2);
    await expect(chart).toContainText("1 missing");
    // Hex colours are gone: the line takes the PAPER token.
    const stroke = await chart.getByTestId("chart-line").first().evaluate((el) => getComputedStyle(el).stroke);
    expect(stroke).toBe(await tokenColour(page, "--prov-paper"));

    await chart.getByTestId("chart-plot").focus();
    await page.keyboard.press("ArrowRight");
    await expect(chart.getByTestId("chart-readout")).toHaveText("15/09/2026, 00:00 UTC: £10,000.00");
    await page.keyboard.press("End");
    await expect(chart.getByTestId("chart-readout")).toHaveText("19/09/2026, 00:00 UTC: £10,100.00");

    await chart.getByTestId("chart-data").locator("summary").click();
    const table = chart.getByTestId("chart-data").locator("table");
    await expect(table.locator("tbody tr")).toHaveCount(5);
    await expect(table.locator("tbody tr").nth(2)).toContainText("no data");
  });
});

test.describe("W-13: colour-blind profit is not the accent; OOS is not WF", () => {
  test("in the blue/orange preset, profit and the accent are different colours", async ({ page }) => {
    await mockShell(page);
    await page.goto("/");
    await page.evaluate(() => document.documentElement.setAttribute("data-pnl", "cvd"));
    expect(await tokenColour(page, "--pnl-up")).not.toBe(await tokenColour(page, "--accent"));
  });

  test("the OOS chip has a corner flag the WF chip does not", async ({ page }) => {
    await mockShell(page);
    await page.goto("/system/legend");
    const flag = (provenance: string) =>
      page
        .locator(`[data-testid="legend-provenance"] [data-provenance="${provenance}"]`)
        .first()
        .evaluate((el) => getComputedStyle(el, "::after").borderTopWidth);
    expect(await flag("out_of_sample")).toBe("6px");
    expect(await flag("walkforward")).not.toBe("6px");
    await expect(
      page.locator('[data-testid="legend-provenance"] [data-provenance="out_of_sample"]').first(),
    ).toHaveAttribute("data-outline", "solid-flagged");
  });
});

test.describe("Parameter Lab never waits forever", () => {
  test("a failed strategy list says there is nothing to show, not 'Loading'", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/research/strategies`, (route: Route) =>
      route.fulfill({
        status: 503,
        json: { code: "unavailable", detail: "down", correlation_id: "cid-s", context: {} },
      }),
    );
    await page.goto("/research/parameter-lab");
    await expect(page.getByTestId("parameter-lab-no-strategy")).toHaveAttribute(
      "data-reason",
      "strategies-failed",
    );
    await expect(page.getByTestId("state-loading")).toHaveCount(0);
  });

  test("an empty strategy list is an empty answer", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/research/strategies`, (route: Route) =>
      route.fulfill({
        json: { items: [], total: 0, offset: 0, limit: 100, source: source("live"), caveats: [] },
      }),
    );
    await page.goto("/research/parameter-lab");
    await expect(page.getByTestId("parameter-lab-no-strategy")).toHaveAttribute(
      "data-reason",
      "no-strategies",
    );
    await expect(page.getByTestId("state-loading")).toHaveCount(0);
  });
});

test.describe("Numeric headers sit over their numbers", () => {
  test("a ListPage figure column's header is right-aligned", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/research/validation`, (route: Route) =>
      route.fulfill({
        json: {
          items: [
            {
              strategy_id: "s1",
              verdict: "promote",
              report_version: "v1",
              dataset_version_id: "d",
              gate_set_version: "g",
              rungs_passed: figure(5, "holdout", "count"),
              rungs_total: figure(5, "holdout", "count"),
              binding_constraint: "",
              provenance_labels: {},
              available: true,
              detail: "",
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
    await page.goto("/research/validation");
    const header = page.locator('th[data-numeric="true"]').first();
    await expect(header).toHaveText("Rungs passed");
    expect(await header.evaluate((el) => getComputedStyle(el).textAlign)).toBe("right");
  });
});

test.describe("Numbers: sign after rounding", () => {
  test("a loss that rounds to zero shows no minus, no ▼ and no loss colour", async ({ page }) => {
    await mockShell(page);
    await page.goto("/system/legend");
    const specimens = page.getByTestId("legend-number");
    await expect(specimens.nth(2)).toHaveText("0.00%");
    await expect(specimens.nth(2)).toHaveAttribute("data-direction", "flat");
    await expect(specimens.nth(1)).toHaveText("▼−£233.25");
    await expect(specimens.nth(0)).toHaveText("▲+£12,345.50");
  });
});
