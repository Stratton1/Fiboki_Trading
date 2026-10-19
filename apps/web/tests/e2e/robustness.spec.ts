import { expect, test, type Page } from "@playwright/test";
import { mockShell } from "./fixtures";
import { mockResearch } from "./research-fixtures";
import { mockRobustness, robustnessFixture } from "./robustness-fixtures";

/**
 * Wave 3R: robustness and integrity diagnostics on the tearsheet and the
 * strategy page, against fixtures the real backend produced
 * (robustness-fixtures/generate.py). Every count asserted is read from the
 * fixture, so the screens are shown to print what the API sent.
 */

async function open(page: Page, path: string, state?: "pending" | "not_run") {
  await mockShell(page);
  await mockResearch(page);
  if (state) await mockRobustness(page, state);
  await page.goto(path);
  await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
}

type Data<T> = { data: T };

test.describe("tearsheet robustness panel", () => {
  test("ledger tabs: bands, breakdowns, consistency and fills, keyboard first", async ({ page }) => {
    const bands = robustnessFixture<Data<{ bands: unknown[]; boundary: { basis: string } }>>("bands").data;
    const bd = robustnessFixture<Data<{ groups: Record<string, unknown[]> }>>("breakdowns").data;
    const fills = robustnessFixture<Data<{ table: { trade_id: string[] } }>>("fills").data;
    await open(page, "/research/runs/bt_0001");
    const panel = page.getByTestId("robustness");
    await panel.scrollIntoViewIfNeeded();
    await expect(page.getByTestId("robustness-advisory")).toContainText("None of them is read by a gate");
    await expect(page.getByTestId("bands-table").locator("tbody tr")).toHaveCount(bands.bands.length);
    await expect(page.getByTestId("holdout-boundary").first()).toHaveAttribute("data-basis", bands.boundary.basis);

    // Arrow keys move between tabs (roving tabindex).
    await page.getByTestId("rb-tab-bands").focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("rb-tab-breakdowns")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("rb-tab-breakdowns")).toBeFocused();
    await expect(page.getByTestId("bd-table").locator("tbody tr")).toHaveCount(bd.groups.exit_reason!.length);
    await page.getByTestId("bd-group-session").click();
    await expect(page.getByTestId("bd-table").locator("tbody tr")).toHaveCount(bd.groups.session!.length);

    await page.getByTestId("rb-tab-breakdowns").focus();
    await page.keyboard.press("End");
    await expect(page.getByTestId("rb-tab-noise")).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Home");
    await expect(page.getByTestId("rb-tab-bands")).toHaveAttribute("aria-selected", "true");

    await page.getByTestId("rb-tab-consistency").click();
    await expect(page.getByTestId("cons-periods").locator("tbody tr")).toHaveCount(4);
    const req = page.waitForRequest((r) => r.url().includes("/robustness/consistency") && r.url().includes("k=6"));
    await page.getByTestId("cons-k-6").click();
    await req;

    await page.getByTestId("rb-tab-fills").click();
    await expect(page.getByTestId("fill-table").locator("tbody tr")).toHaveCount(fills.table.trade_id.length);
  });

  test("re-run tabs show each computed diagnostic with its provenance", async ({ page }) => {
    const exec = robustnessFixture<Data<{ execution: { multipliers: string[]; rows: unknown[] } }>>("rerun_execution").data;
    await open(page, "/research/runs/bt_0001");
    await page.getByTestId("robustness").scrollIntoViewIfNeeded();
    for (const kind of ["warmup", "lookahead", "execution", "perturbation", "random", "noise"]) {
      await page.getByTestId(`rb-tab-${kind}`).click();
      const body = page.getByTestId(`rb-panel-${kind}`);
      await expect(body.getByTestId("rb-status")).toHaveAttribute("data-status", "done");
      await expect(body.getByTestId("rb-provenance")).toContainText("engine_v");
      await expect(body.getByTestId(`rb-${kind}`)).toBeVisible();
    }
    await page.getByTestId("rb-tab-execution").click();
    await expect(page.getByTestId("rb-grid-cell")).toHaveCount(exec.execution.multipliers.length ** 2);
    await expect(page.getByTestId("rb-stress-rows").locator("tbody tr")).toHaveCount(exec.execution.rows.length);
    await page.getByTestId("rb-tab-lookahead").click();
    await expect(page.getByTestId("rb-lookahead-verdict")).toHaveAttribute("data-value", "false");
    await page.getByTestId("rb-tab-warmup").click();
    await expect(page.getByTestId("rb-warmup-indicators").first()).toBeVisible();
  });

  test("a queued diagnostic shows QUEUED and its button is disabled", async ({ page }) => {
    await open(page, "/research/runs/bt_0001", "pending");
    await page.getByTestId("robustness").scrollIntoViewIfNeeded();
    await page.getByTestId("rb-tab-warmup").click();
    await expect(page.getByTestId("rb-status-badge")).toContainText("QUEUED");
    await expect(page.getByTestId("rb-queue-btn")).toBeDisabled();
    await expect(page.getByTestId("rb-not-computed")).toBeVisible();
  });

  test("queueing a diagnostic posts the operator's reason", async ({ page }) => {
    await open(page, "/research/runs/bt_0001", "not_run");
    await page.getByTestId("robustness").scrollIntoViewIfNeeded();
    await page.getByTestId("rb-tab-noise").click();
    await expect(page.getByTestId("rb-status-badge")).toContainText("NOT RUN");
    await expect(page.getByTestId("rb-queue-btn")).toBeDisabled();
    await page.getByTestId("rb-reason").fill("price-noise check before paper");
    const post = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/robustness/noise"));
    await page.getByTestId("rb-queue-btn").click();
    const sent = await post;
    expect(sent.postDataJSON()).toEqual({ reason: "price-noise check before paper" });
  });

  test("the panel fits the phone width", async ({ page }, info) => {
    test.skip(info.project.name !== "mobile", "mobile project only");
    await open(page, "/research/runs/bt_0001");
    await page.getByTestId("robustness").scrollIntoViewIfNeeded();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
});

test.describe("strategy robustness", () => {
  test("optimisation profile and ranking by a selectable objective", async ({ page }) => {
    const prof = robustnessFixture<Data<{ points: unknown[] }>>("profile").data;
    const rank = robustnessFixture<Data<{ rows: unknown[] }>>("rankings").data;
    await open(page, "/research/strategies/ichimoku_kumo_trend");
    await page.getByTestId("strategy-robustness").scrollIntoViewIfNeeded();
    await expect(page.getByTestId("spp-points").locator("tbody tr")).toHaveCount(prof.points.length);
    await expect(page.getByTestId("spp-stats")).toContainText("Median");
    await expect(page.getByTestId("ranking-table").locator("tbody tr")).toHaveCount(rank.rows.length);
    const req = page.waitForRequest((r) => r.url().includes("/api/research/rankings") && r.url().includes("objective=sortino"));
    await page.getByTestId("objective-sortino").click();
    await req;
    await expect(page.getByTestId("objective-sortino")).toHaveAttribute("aria-pressed", "true");
  });
});
