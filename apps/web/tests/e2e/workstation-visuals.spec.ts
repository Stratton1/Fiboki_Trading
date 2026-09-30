import { expect, test, type Route } from "@playwright/test";
import { API, figure, mockShell, positionsPage, source } from "./fixtures";

/**
 * The visuals from WORKSTATION_INVENTORY.md §6 that need no backend change,
 * and the Settings units fix (F-15). Each draws what the API sends: the tests
 * change the fixture and read the drawing back from the DOM.
 *
 *  V-4  rung meter per strategy on Validation and Candidates
 *  V-5  distance-to-stop meter on Portfolio (Risk's drawer: risk-exposure.spec.ts)
 *  V-8  instrument × regime-measure grid, unknown hatched
 */

const page1 = (items: unknown[]) => ({
  items,
  total: items.length,
  offset: 0,
  limit: 100,
  source: source("live"),
  caveats: [],
});

const report = (id: string, passed: number | null, total: number | null, extra: Record<string, unknown> = {}) => ({
  strategy_id: id,
  verdict: passed === null ? "not_validated" : "reject",
  report_version: passed === null ? "" : "1",
  dataset_version_id: "",
  gate_set_version: passed === null ? "" : "gates_v3",
  rungs_passed: figure(passed, "holdout", "count"),
  rungs_total: figure(total, "holdout", "count"),
  binding_constraint: passed === null ? "" : "RUNG 2 WALK-FORWARD: walk-forward efficiency 0.21 below 0.5",
  provenance_labels: {},
  available: passed !== null,
  detail: "",
  ...extra,
});

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "visuals, desktop project");
});

test.describe("V-4 rung meter", () => {
  test("Validation: filled to the rung reached, the stopping rung marked, the binding gate named", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/research/validation`, (route: Route) =>
      route.fulfill({
        json: page1([
          report("ichimoku_kumo_trend", 2, 7),
          report("all_passed", 7, 7, { verdict: "promote", binding_constraint: "none" }),
          report("donchian_breakout_atr", null, null),
        ]),
      }),
    );
    await page.goto("/research/validation");
    const meters = page.getByTestId("rung-meter");
    await expect(meters).toHaveCount(3);
    const first = meters.nth(0);
    await expect(first).toHaveAttribute("data-state", "stopped");
    await expect(first).toHaveAttribute("data-stopped-at", "2");
    expect(await first.getByTestId("rung-segment").evaluateAll((els) => els.map((el) => el.getAttribute("data-state")))).toEqual([
      "pass",
      "pass",
      "stopped",
      "not_reached",
      "not_reached",
      "not_reached",
      "not_reached",
    ]);
    await expect(first.getByTestId("rung-meter-summary")).toHaveText("2 of 7 rungs passed; stopped at rung 2");
    await expect(first.getByTestId("rung-meter-binding")).toContainText("RUNG 2 WALK-FORWARD");
    await expect(first.getByTestId("provenance-chip")).toHaveAttribute("data-provenance", "holdout");
    const complete = meters.nth(1);
    await expect(complete).toHaveAttribute("data-state", "complete");
    await expect(complete.locator('[data-state="pass"][data-testid="rung-segment"]')).toHaveCount(7);
    await expect(complete.getByTestId("rung-meter-binding")).toHaveCount(0);
    // No report: NOT EVALUATED, and no segments at all (not "rung zero").
    const none = meters.nth(2);
    await expect(none).toHaveAttribute("data-state", "not_evaluated");
    await expect(none.getByTestId("rung-segment")).toHaveCount(0);
    await expect(none).toContainText("NOT EVALUATED");
  });

  test("Candidates: each candidate's meter, joined by strategy id; a missing report row says so", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/research/validation`, (route: Route) =>
      route.fulfill({ json: page1([report("ichimoku_kumo_trend", 5, 7)]) }),
    );
    await page.goto("/trading/candidates");
    const rows = page.getByTestId("candidate-row");
    await expect(rows).toHaveCount(2);
    const ichimoku = page.locator('[data-testid="candidate-row"][data-row-id="ichimoku_kumo_trend"]');
    await expect(ichimoku.getByTestId("rung-meter")).toHaveAttribute("data-passed", "5");
    await expect(ichimoku.getByTestId("rung-meter")).toHaveAttribute("data-stopped-at", "5");
    const donchian = page.locator('[data-testid="candidate-row"][data-row-id="donchian_breakout_atr"]');
    await expect(donchian.getByTestId("candidate-ladder-missing")).toBeVisible();
  });

  test("Candidates: a failed validation read is said in the column, never drawn as rung zero", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/research/validation`, (route: Route) =>
      route.fulfill({
        status: 503,
        json: { code: "reports_unreadable", detail: "down", correlation_id: "c", context: {} },
      }),
    );
    await page.goto("/trading/candidates");
    await expect(page.getByTestId("candidate-ladder-unavailable").first()).toContainText("could not be read");
    await expect(page.getByTestId("rung-meter")).toHaveCount(0);
  });
});

test.describe("V-5 distance to stop on Portfolio", () => {
  test("each open position has a meter placed from entry, mark and stop, marked †", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/trading/positions`, (route: Route) => route.fulfill({ json: positionsPage() }));
    await page.route(`${API}/api/trading/portfolio`, (route: Route) =>
      route.fulfill({
        json: {
          data: {
            balance: figure(10000, "paper", "GBP"),
            equity: figure(10030, "paper", "GBP"),
            realised_pnl: figure(0, "paper", "GBP"),
            unrealised_pnl: figure(30, "paper", "GBP"),
            open_positions: figure(2, "paper", "count"),
            max_drawdown_pct: figure(1, "paper", "pct"),
            provenance_mix: { paper: 2 },
            equity_curve: { name: "Equity", provenance: "paper", unit: "GBP", points: [], caveats: [] },
          },
          source: source("live"),
          caveats: [],
        },
      }),
    );
    await page.goto("/trading/portfolio");
    const rows = page.getByTestId("portfolio-position-row");
    await expect(rows).toHaveCount(2);
    // (1.102 − 1.095) ÷ (1.1 − 1.095) = 1.4: past the entry, in profit.
    await expect(rows.first().getByTestId("stop-meter")).toHaveAttribute("data-position", "1.400");
    await expect(rows.first().getByTestId("stop-meter-dagger")).toHaveText("†");
    await expect(page.getByTestId("stop-meter-note")).toContainText("(mark − stop) ÷ (entry − stop)");
  });
});

test.describe("V-8 regime grid", () => {
  test("instruments × measures, values printed, unknown and unrecognised hatched", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/markets/regimes`, (route: Route) =>
      route.fulfill({
        json: page1([
          {
            instrument: "EURUSD",
            available: true,
            detail: "Classified on 500 H1 bars.",
            volatility: "extreme",
            direction: "strong_down",
            liquidity: "deep",
            stress: "calm",
            persistence: "sideways_drift",
          },
          {
            instrument: "USDJPY",
            available: false,
            detail: "No bar history mounted for USDJPY.",
            volatility: null,
            direction: null,
            liquidity: null,
            stress: null,
            persistence: null,
          },
        ]),
      }),
    );
    await page.goto("/markets/regimes");
    const grid = page.getByTestId("regime-grid");
    await expect(grid.getByTestId("regime-grid-row")).toHaveCount(2);
    await expect(grid.getByTestId("regime-grid-count")).toContainText("1 of 2 instruments classified");
    const cell = (instrument: string, axis: string) =>
      grid.locator(`[data-testid="regime-grid-row"][data-instrument="${instrument}"] [data-testid="regime-cell"][data-axis="${axis}"]`);
    // Shade = position in the classifier's declared order: extreme is last (5), strong_down first (1).
    await expect(cell("EURUSD", "volatility")).toHaveAttribute("data-state", "known");
    await expect(cell("EURUSD", "volatility").locator("rect.xheat")).toHaveAttribute("data-bin", "5");
    await expect(cell("EURUSD", "direction").locator("rect.xheat")).toHaveAttribute("data-bin", "1");
    await expect(cell("EURUSD", "stress").locator("rect.xheat")).toHaveAttribute("data-bin", "1");
    await expect(cell("EURUSD", "volatility")).toContainText("extreme");
    // An unknown value is hatched and says unknown; never a shade of a real state.
    for (const axis of ["volatility", "direction", "persistence", "liquidity", "stress"]) {
      await expect(cell("USDJPY", axis)).toHaveAttribute("data-state", "unknown");
      await expect(cell("USDJPY", axis).locator("rect.xheat")).toHaveAttribute("data-bin", "none");
      await expect(cell("USDJPY", axis).locator("rect.regime-grid__hatch")).toHaveCount(1);
    }
    await expect(cell("USDJPY", "stress").locator("title")).toContainText("No bar history mounted");
    // A value outside the classifier's vocabulary is not guessed into place.
    await expect(cell("EURUSD", "persistence")).toHaveAttribute("data-state", "unrecognised");
    await expect(cell("EURUSD", "persistence").locator("rect.regime-grid__hatch")).toHaveCount(1);
    // The table below still lists every value, persistence included.
    await expect(page.locator("table")).toContainText("sideways_drift");
  });
});

test.describe("Settings units (inventory F-15)", () => {
  test("every limit shows its unit; realism codes say what they mean", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/system/settings`, (route: Route) =>
      route.fulfill({
        json: {
          data: {
            execution_mode: "paper",
            live_execution_compiled_in: false,
            allowed_origin_count: 1,
            cookie_secure: true,
            cookie_samesite: "lax",
            session_ttl_seconds: 43200,
            limits_version: "limits_v1_paper",
            limits: {
              max_daily_loss_pct: 2,
              max_price_age_seconds: 90,
              max_spread_multiple: 3,
              correlation_threshold: 0.6,
              event_blackout_minutes: 15,
              event_blackout_pre_minutes: null,
              margin_utilisation_after_trade: true,
              mystery_limit: 4,
            },
            realism_models: { slippage: "zero", spread: "static_typical", fx_conversion: "novel_model" },
            data_root_configured: true,
            experiment_db_configured: true,
            build_sha: "deadbeef",
          },
          source: source("live", "Process configuration."),
          caveats: [],
        },
      }),
    );
    await page.goto("/system/settings");
    const limit = (key: string) => page.locator(`[data-testid="limit-setting"][data-key="${key}"]`);
    await expect(limit("max_daily_loss_pct").getByTestId("limit-setting-value")).toHaveText("2%");
    await expect(limit("max_daily_loss_pct").getByTestId("limit-setting-unit")).toHaveText("% of equity");
    await expect(limit("max_price_age_seconds").getByTestId("limit-setting-value")).toHaveText("90 s");
    await expect(limit("max_spread_multiple").getByTestId("limit-setting-unit")).toHaveText("× the instrument's typical spread");
    await expect(limit("correlation_threshold").getByTestId("limit-setting-unit")).toHaveText(
      "correlation coefficient, 0 to 1",
    );
    await expect(limit("event_blackout_minutes").getByTestId("limit-setting-value")).toHaveText("15 min");
    await expect(limit("event_blackout_pre_minutes").getByTestId("limit-setting-unit")).toContainText("not set");
    await expect(limit("margin_utilisation_after_trade").getByTestId("limit-setting-value")).toHaveText("on");
    // An unknown key is not given a guessed unit.
    await expect(limit("mystery_limit").getByTestId("limit-setting-unit")).toHaveText("unit not stated by the API");
    const realism = (key: string) => page.locator(`[data-testid="realism-row"][data-key="${key}"]`);
    await expect(realism("slippage")).toContainText("every fill is assumed at the requested price");
    await expect(realism("spread")).toContainText("held constant");
    await expect(realism("fx_conversion")).toContainText("not described in the workstation");
  });
});
