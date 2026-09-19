import { expect, test } from "@playwright/test";

/**
 * End-to-end against the REAL API, with no route mocking.
 *
 * The rest of this suite mocks the API, which proves the UI renders what it is
 * told. This file proves the UI is told the right thing — that the pydantic
 * models and lib/types.ts have not drifted apart. It is skipped unless
 * FIBOKI_LIVE_API=1, because it needs a running backend:
 *
 *   FIBOKI_STATE_DIR=var FIBOKI_ALLOWED_ORIGINS=http://127.0.0.1:3100 \
 *   python -m uvicorn --factory fiboki.api.app:asgi_factory --port 8000
 *   FIBOKI_LIVE_API=1 npx playwright test live-api
 */

test.describe("against the real API", () => {
  test.skip(process.env.FIBOKI_LIVE_API !== "1", "needs a running backend");

  test("the banner shows the backend's actual mode", async ({ page }) => {
    await page.goto("/");
    const banner = page.getByTestId("mode-banner");
    await expect(banner).toBeVisible();
    await expect(banner).not.toHaveAttribute("data-mode", "unknown");
    await expect(banner).toHaveAttribute("data-mode", /backtest|paper|shadow|demo|live/);
  });

  test("real trades render with a real provenance on every row", async ({ page }) => {
    await page.goto("/trading/execution");
    await expect(page.getByTestId("state-success")).toBeVisible({ timeout: 15_000 });

    const chips = page.locator("tbody tr").locator('[data-testid="provenance-chip"]').first();
    await expect(chips).toBeVisible();

    const rows = await page.locator("tbody tr").count();
    expect(rows).toBeGreaterThan(0);
    const chipCount = await page
      .locator("tbody tr")
      .locator('[data-testid="provenance-chip"]')
      .count();
    // One source chip per row, at minimum.
    expect(chipCount).toBeGreaterThanOrEqual(rows);
  });

  test("real caveats are rendered, and they came from the server", async ({ page }) => {
    await page.goto("/trading/execution");
    await expect(page.getByTestId("state-success")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("caveat").first()).toBeVisible();
  });

  test("the health panel reports the backend's real status", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("status-badge").first()).toBeVisible({ timeout: 15_000 });
    // Nothing is provisioned in a bare deployment, so it must NOT claim ok.
    await expect(page.getByTestId("health-advisory")).toBeVisible();
  });

  test("the real kill switch is armable in the backend's real mode", async ({ page }) => {
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-arm")).toBeEnabled({ timeout: 15_000 });
    await page.getByTestId("kill-switch-arm").click();
    await expect(page.getByTestId("confirm-choice-pause")).toBeVisible();
    await expect(page.getByTestId("confirm-choice-flatten")).toBeVisible();
    // The consequences came from the server, so they mention the real book.
    await page.getByTestId("confirm-choice-flatten").click();
    await expect(page.getByTestId("confirm-consequences")).toContainText("closing intent");
  });
});
