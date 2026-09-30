import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import { API, attentionItem, attentionPage, figure, mockShell, source } from "./fixtures";

/**
 * /lifecycle/<hash> (inventory F-2): the strategy-review deep link the
 * backend's attention queue emits (routers/command.py). The research API
 * addresses a document by id, not hash; the lifecycle API addresses it by
 * hash and names the id. The page shows the document, its validation summary
 * as a rung meter, and the lifecycle state, and says plainly what it cannot
 * resolve.
 */

const HASH = "abc123def456";
const BASE = `http://127.0.0.1:${process.env.PORT ?? 3100}`;

function strategy(id: string, hash: string) {
  return {
    strategy_id: id,
    name: id.replace(/_/g, " "),
    family: "trend",
    author: "joe",
    hypothesis: "Price closing outside the cloud continues; Deng, Sakurai and Ueda (2021) found no significant profitability.",
    timeframes: ["H1", "H4"],
    universe: ["EURUSD", "GBPUSD"],
    content_hash: hash,
    schema_version: "1",
    complexity: figure(7, "backtest", "score"),
    parameter_count: figure(4, "backtest", "count"),
    rule_count: figure(3, "backtest", "count"),
    parent_strategy_ids: [],
    notes: "",
  };
}

const page1 = (items: unknown[], detail = "Test fixture.") => ({
  items,
  total: items.length,
  offset: 0,
  limit: 100,
  source: source("live", detail),
  caveats: [],
});

function lifecycleStatus(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      strategy_id: "ichimoku_kumo_trend",
      strategy_content_hash: `${HASH}7890abcdef`,
      lifecycle: "paper",
      band: "healthy",
      degraded: false,
      ever_evaluated: true,
      entered_state_at: "2026-09-20T09:00:00Z",
      last_evaluated_at: "2026-09-29T09:00:00Z",
      latched_halts: [],
      missing_rule_registrations: [],
      health: figure(0.91, "paper", "ratio"),
      last_score: figure(0.09, "paper", "ratio"),
      last_confidence: figure(0.7, "paper", "ratio"),
      n_transitions: figure(2, "paper", "count"),
      ...overrides,
    },
    source: source("live", "Lifecycle store."),
    caveats: [],
  };
}

const evaluation = {
  data: {
    strategy_content_hash: `${HASH}7890abcdef`,
    at: "2026-09-29T09:00:00Z",
    state_before: "paper",
    state_after: "paper",
    demoted: false,
    summary: "No rule fired.",
    score: figure(0.09, "paper", "ratio"),
    confidence: figure(0.7, "paper", "ratio"),
    rule_evaluations: [
      {
        kind: "cusum_drift",
        fired: false,
        registration_id: "reg_1",
        statistic: figure(0.4, "paper", "ratio"),
        threshold: figure(1.5, "paper", "ratio"),
        detail: "clear",
      },
    ],
    divergence: [],
    latched_halts: [],
    notes: [],
  },
  source: source("live", "Lifecycle store."),
  caveats: [],
};

const validationRow = {
  strategy_id: "ichimoku_kumo_trend",
  verdict: "reject",
  report_version: "1",
  dataset_version_id: "dsv_eurusd_h1_0007abcdef",
  gate_set_version: "gates_v3",
  rungs_passed: figure(5, "holdout", "count"),
  rungs_total: figure(7, "holdout", "count"),
  binding_constraint: "deflated_sharpe: observed 0.41, required > 0.95, short by 0.54",
  provenance_labels: {},
  available: true,
  detail: "Died at deflation.",
};

async function mockLifecycle(
  page: Page,
  options: { status?: unknown; statusCode?: number; docs?: unknown[]; validation?: unknown[] } = {},
) {
  await mockShell(page);
  await page.route(`${API}/api/research/strategies`, (route: Route) =>
    route.fulfill({
      json: page1(
        options.docs ?? [strategy("ichimoku_kumo_trend", HASH), strategy("donchian_breakout_atr", "fedcba987654")],
        "Strategy registry.",
      ),
    }),
  );
  await page.route(`${API}/api/research/validation`, (route: Route) =>
    route.fulfill({ json: page1(options.validation ?? [validationRow], "research/reports.") }),
  );
  await page.route(`${API}/api/trading/lifecycle/strategies/*`, (route: Route) =>
    route.fulfill(
      options.statusCode
        ? {
            status: options.statusCode,
            json: {
              code: "lifecycle_strategy_not_found",
              detail: "No strategy in the lifecycle store.",
              correlation_id: "cid-lc",
              context: {},
            },
          }
        : { json: options.status ?? lifecycleStatus() },
    ),
  );
  await page.route(`${API}/api/trading/lifecycle/strategies/*/evaluation`, (route: Route) =>
    route.fulfill({ json: evaluation }),
  );
}

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "entity page, desktop project");
});

test("the backend's /lifecycle/<hash> link from Command resolves to this page", async ({ page }) => {
  await mockLifecycle(page);
  await page.route(`${API}/api/command/attention`, (route: Route) =>
    route.fulfill({
      json: attentionPage([
        attentionItem("strategy_review:ichimoku_kumo_trend:paper", "info", `/lifecycle/${HASH}`, {
          category: "strategy_review",
          title: "ichimoku kumo trend needs review",
        }),
      ]),
    }),
  );
  await page.goto("/");
  await page.getByTestId("attention-link-strategy_review:ichimoku_kumo_trend:paper").click();
  await expect(page).toHaveURL(`${BASE}/lifecycle/${HASH}`);
  await expect(page.locator("main h1")).toHaveText("Strategy");
  // The page belongs to Strategy Lifecycle in the header.
  await expect(page.getByTestId("page-header")).toHaveAttribute("data-section", "lifecycle");
});

test("document header, rung meter with the binding gate, lifecycle state and the promote link", async ({ page }) => {
  await mockLifecycle(page);
  await page.goto(`/lifecycle/${HASH}`);
  const doc = page.getByTestId("lifecycle-doc");
  await expect(doc).toHaveAttribute("data-strategy-id", "ichimoku_kumo_trend");
  await expect(doc).toHaveAttribute("data-hash-agrees", "true");
  await expect(page.getByTestId("lifecycle-doc-name")).toHaveText("ichimoku kumo trend");
  await expect(page.getByTestId("lifecycle-hypothesis")).toContainText("no significant profitability");
  await expect(doc.getByTestId("provenance-chip").first()).toHaveAttribute("data-provenance", "backtest");

  const validation = page.getByTestId("lifecycle-validation");
  await expect(validation.getByTestId("verdict-badge")).toHaveText("REJECT");
  const meter = validation.getByTestId("rung-meter");
  await expect(meter).toHaveAttribute("data-passed", "5");
  await expect(meter).toHaveAttribute("data-total", "7");
  await expect(meter).toHaveAttribute("data-stopped-at", "5");
  expect(await meter.getByTestId("rung-segment").evaluateAll((els) => els.map((el) => el.getAttribute("data-state")))).toEqual([
    "pass",
    "pass",
    "pass",
    "pass",
    "pass",
    "stopped",
    "not_reached",
  ]);
  await expect(meter.getByTestId("rung-meter-binding")).toContainText("deflated_sharpe");
  await expect(page.getByTestId("lifecycle-gates-note")).toContainText("not each gate's status");

  const status = page.getByTestId("lifecycle-status");
  await expect(status).toHaveAttribute("data-lifecycle", "paper");
  await expect(page.getByTestId("lifecycle-health").getByTestId("provenance-chip")).toHaveAttribute(
    "data-provenance",
    "paper",
  );
  await expect(page.getByTestId("lifecycle-evaluation")).toContainText("No rule fired.");
  await expect(page.getByTestId("lifecycle-promote-link")).toHaveAttribute(
    "href",
    "/trading/candidates?row=ichimoku_kumo_trend",
  );
  await expect(page.getByTestId("lifecycle-actions")).toContainText("no screen and no route demotes");
});

test("a document edited since the lifecycle record is flagged, not passed off as the same", async ({ page }) => {
  await mockLifecycle(page, { docs: [strategy("ichimoku_kumo_trend", "999999999999")] });
  await page.goto(`/lifecycle/${HASH}`);
  await expect(page.getByTestId("lifecycle-doc")).toHaveAttribute("data-hash-agrees", "false");
  await expect(page.getByTestId("lifecycle-hash-mismatch")).toContainText("has been edited");
});

test("a never-evaluated strategy says nothing has looked, and does not ask for an evaluation", async ({ page }) => {
  await mockLifecycle(page, { status: lifecycleStatus({ ever_evaluated: false, last_evaluated_at: null }) });
  let evaluationReads = 0;
  page.on("request", (req) => {
    if (req.url().endsWith("/evaluation")) evaluationReads += 1;
  });
  await page.goto(`/lifecycle/${HASH}`);
  await expect(page.getByTestId("lifecycle-never-evaluated")).toContainText("not the same as healthy");
  await expect(page.getByTestId("lifecycle-health").getByTestId("view-state-absent")).toContainText("NOT EVALUATED");
  expect(evaluationReads).toBe(0);
});

test("with no lifecycle record, the document is found by hash alone", async ({ page }) => {
  await mockLifecycle(page, { statusCode: 404 });
  await page.goto(`/lifecycle/${HASH}`);
  await expect(page.getByTestId("lifecycle-doc")).toHaveAttribute("data-strategy-id", "ichimoku_kumo_trend");
  await expect(page.locator('section[aria-labelledby="lifecycle-state"]').getByTestId("state-error")).toContainText(
    "lifecycle_strategy_not_found",
  );
  // The validation report is still matched, through the id the document gave.
  await expect(page.getByTestId("lifecycle-validation").getByTestId("rung-meter")).toHaveAttribute("data-passed", "5");
});

test("a hash nothing matches says so plainly and links to the Strategies list", async ({ page }) => {
  await mockLifecycle(page, { statusCode: 404 });
  const response = await page.goto("/lifecycle/0000deadbeef");
  expect(response?.status()).toBe(200);
  const none = page.getByTestId("lifecycle-doc-none");
  await expect(none).toContainText("addresses a document by strategy id, not by hash");
  await expect(page.getByTestId("lifecycle-strategies-link")).toHaveAttribute("href", "/research/strategies");
  await expect(page.getByTestId("lifecycle-validation-unknown")).toBeVisible();
});

test("two documents matching one short hash are not guessed between", async ({ page }) => {
  await mockLifecycle(page, {
    statusCode: 404,
    docs: [strategy("a_strategy", `${HASH}`), strategy("b_strategy", `${HASH}`)],
  });
  await page.goto(`/lifecycle/${HASH}`);
  await expect(page.getByTestId("lifecycle-doc-ambiguous")).toContainText("a_strategy, b_strategy");
});

test("axe: zero serious on the lifecycle page", async ({ page }) => {
  await mockLifecycle(page);
  await page.goto(`/lifecycle/${HASH}`);
  await expect(page.getByTestId("lifecycle-evaluation")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const blocking = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => ({ id: v.id, targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")) }));
  expect(blocking).toEqual([]);
});
