import { expect, test } from "@playwright/test";
import { API, failAll, mockApi } from "./fixtures";

/**
 * A failed fetch renders an explicit error, never zeros.
 *
 * V1's dashboard rendered a total backend outage as "£0.00 balance, 0/0 bots
 * running" beside hardcoded "Online" and "Connected" badges — a dead backend
 * and a flat, idle, healthy fleet looked identical.
 */

test.describe("failure states", () => {
  test("an API outage renders an error, not zeros", async ({ page }) => {
    await failAll(page);
    await page.goto("/trading/execution");

    await expect(page.getByTestId("state-error").first()).toBeVisible();
    await expect(page.getByTestId("state-success")).toHaveCount(0);

    const body = await page.locator("main").innerText();
    expect(body).not.toMatch(/£0\.00/);
    expect(body).not.toMatch(/\b0\.00%/);
    expect(body).toContain("Nothing on this panel is current");
  });

  test("the error names the failure and the correlation id", async ({ page }) => {
    await failAll(page);
    await page.goto("/trading/candidates");
    const error = page.getByTestId("state-error").first();
    await expect(error).toBeVisible();
    await expect(error).toContainText("database_unreachable");
    await expect(error).toContainText("cid-test-0001");
  });

  test("the overview does not draw a zero balance when the API is down", async ({ page }) => {
    await failAll(page);
    await page.goto("/");
    await expect(page.getByTestId("state-error").first()).toBeVisible();
    await expect(page.getByTestId("tile")).toHaveCount(0);
    const body = await page.locator("main").innerText();
    expect(body).not.toMatch(/£0\.00/);
  });

  test("a network failure is distinguished from an empty result", async ({ page }) => {
    await page.route(`${API}/api/**`, (route) => route.abort("failed"));
    await page.goto("/trading/execution");
    const error = page.getByTestId("state-error").first();
    await expect(error).toBeVisible();
    await expect(error).toContainText("Could not reach the Fiboki API");
    await expect(error).toContainText("not an empty result");
  });

  test("loading, empty, error and success are four different states", async ({ page }) => {
    // EMPTY: a successful response with no rows.
    await mockApi(page);
    await page.route(`${API}/api/trading/trades*`, (route) =>
      route.fulfill({
        json: {
          items: [],
          total: 0,
          offset: 0,
          limit: 100,
          source: { kind: "live", detail: "No trades yet.", as_of: null },
          caveats: [],
        },
      }),
    );
    await page.goto("/trading/execution");
    await expect(page.getByTestId("state-empty")).toBeVisible();
    await expect(page.getByTestId("state-empty")).toContainText("not a failure to load");
    await expect(page.getByTestId("state-error")).toHaveCount(0);

    // ERROR: visually and structurally distinct from the empty state above.
    await failAll(page);
    await page.reload();
    await expect(page.getByTestId("state-error").first()).toBeVisible();
    await expect(page.getByTestId("state-empty")).toHaveCount(0);
  });

  test("LOADING is a distinct state while the request is in flight", async ({ page }) => {
    await mockApi(page);
    let release: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`${API}/api/trading/trades*`, async (route) => {
      await gate;
      await route.fulfill({
        json: {
          items: [],
          total: 0,
          offset: 0,
          limit: 100,
          source: { kind: "live", detail: "ok", as_of: null },
          caveats: [],
        },
      });
    });
    await page.goto("/trading/execution");
    await expect(page.getByTestId("state-loading")).toBeVisible();
    await expect(page.getByTestId("state-empty")).toHaveCount(0);
    await expect(page.getByTestId("state-error")).toHaveCount(0);
    release?.();
    await expect(page.getByTestId("state-empty")).toBeVisible();
  });

  test("a figure the platform could not supply reads 'no data', never 0", async ({ page }) => {
    await mockApi(page);
    await page.route(`${API}/api/trading/risk`, (route) =>
      route.fulfill({
        json: {
          data: {
            limits_version: "limits_v1_paper",
            kill_switch_active: false,
            kill_switch_mode: null,
            new_risk_permitted: true,
            new_risk_reason: "kill_switch_inactive",
            closing_permitted: true,
            daily_loss_pct: {
              value: -0.4, provenance: "paper", unit: "pct", as_of: null,
              sample_size: null, caveats: [], estimated: false,
            },
            max_daily_loss_pct: {
              value: 3, provenance: "paper", unit: "pct", as_of: null,
              sample_size: null, caveats: [], estimated: false,
            },
            drawdown_pct: {
              value: 2.1, provenance: "paper", unit: "pct", as_of: null,
              sample_size: null, caveats: [], estimated: false,
            },
            max_drawdown_limit_pct: {
              value: 20, provenance: "paper", unit: "pct", as_of: null,
              sample_size: null, caveats: [], estimated: false,
            },
            margin_utilisation_pct: {
              value: null, provenance: "paper", unit: "pct", as_of: null,
              sample_size: null,
              caveats: [
                {
                  code: "value_unavailable", severity: "warning",
                  message: "No broker session exists in this mode.",
                  affects: "", direction: "unknown",
                },
              ],
              estimated: false,
            },
            breaches: [],
          },
          source: { kind: "live", detail: "Limit set.", as_of: null },
          caveats: [],
        },
      }),
    );
    await page.goto("/trading/risk");
    const tile = page.locator('[data-testid="tile"][data-label="Margin utilisation"]');
    await expect(tile).toBeVisible();
    await expect(tile.getByTestId("figure-missing")).toHaveText("no data");
    await expect(tile).not.toContainText("0.00%");
  });
});
