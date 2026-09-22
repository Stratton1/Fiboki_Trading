import { test, expect, Page } from "@playwright/test";

/**
 * Shortlist Workflow E2E — Deterministic
 *
 * Tests the shortlist happy path:
 *   1. Ensure a backtest exists (via dev seed endpoint if needed)
 *   2. Navigate to Backtests page
 *   3. Star (promote) a backtest row to Shortlist
 *   4. Navigate to Paper Bots page
 *   5. Click "From Shortlist" and verify the dropdown appears
 *   6. Select a shortlisted combo and verify fields are populated
 *
 * Required env vars:
 *   FIBOKI_TEST_EMAIL     — username
 *   FIBOKI_TEST_PASSWORD  — password
 *   BASE_URL              — target URL (defaults to http://localhost:3000)
 *
 * For deterministic CI, also set:
 *   FIBOKEI_API_URL       — backend URL (defaults to http://localhost:8000)
 *   FIBOKEI_DEV_SEED=1    — must be set on the backend for seed endpoint
 *
 * Run:
 *   FIBOKI_TEST_EMAIL=x FIBOKI_TEST_PASSWORD=y npx playwright test --project=shortlist
 */

const USERNAME =
  process.env.FIBOKI_TEST_EMAIL || process.env.FIBOKI_E2E_USERNAME;
const PASSWORD =
  process.env.FIBOKI_TEST_PASSWORD || process.env.FIBOKI_E2E_PASSWORD;
const API_URL =
  process.env.FIBOKEI_API_URL || "http://localhost:8000";
const IS_CI = !!process.env.CI;

// ── Helpers ─────────────────────────────────────────────────────────

async function login(page: Page): Promise<string> {
  await page.goto("/login");
  await page.fill("#username", USERNAME!);
  await page.fill("#password", PASSWORD!);
  await page.click('button[type="submit"]');
  await expect(page).not.toHaveURL(/\/login/, { timeout: 10_000 });

  // Extract JWT token from localStorage for API calls
  const token = await page.evaluate(() => localStorage.getItem("fibokei_token"));
  return token ?? "";
}

async function ensureBacktestExists(token: string): Promise<void> {
  // Call the dev seed endpoint to ensure at least one backtest exists
  try {
    const res = await fetch(`${API_URL}/api/v1/dev/seed/backtest`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
    });
    if (res.status === 404) {
      // Dev seed not enabled — fall through, test will use existing data
      return;
    }
    if (!res.ok) {
      console.warn(`Dev seed returned ${res.status}: ${await res.text()}`);
    }
  } catch {
    // Backend not reachable for seed — continue with existing data
  }
}

// ── Tests ───────────────────────────────────────────────────────────

test.describe("shortlist workflow", () => {
  if (IS_CI && (!USERNAME || !PASSWORD)) {
    // In CI, fail loudly if credentials are missing
    test("CI requires auth credentials", () => {
      throw new Error(
        "CI mode: FIBOKI_TEST_EMAIL and FIBOKI_TEST_PASSWORD must be set"
      );
    });
  }

  test.skip(!USERNAME || !PASSWORD, "Skipped: no auth credentials provided");

  let authToken = "";

  test.beforeEach(async ({ page }) => {
    authToken = await login(page);
  });

  test("promote backtest to shortlist, then load in Paper Bots form", async ({
    page,
  }) => {
    // Step 0: Seed data if dev endpoint available
    await ensureBacktestExists(authToken);

    // Step 1: Navigate to backtests
    await page.goto("/backtests");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("text=Backtests")).toBeVisible();

    // Step 2: Find star buttons in the table
    const starButtons = page.locator('button[title="Save to Shortlist"]');
    const alreadyStarred = page.locator(
      'button[title="Already in Shortlist"]'
    );
    const totalStars = await starButtons.count();
    const totalAlready = await alreadyStarred.count();

    if (totalStars === 0 && totalAlready === 0) {
      if (IS_CI) {
        throw new Error(
          "CI mode: no backtests found even after seeding. Check FIBOKEI_DEV_SEED is set on backend."
        );
      }
      test.skip(true, "No backtests available to promote");
      return;
    }

    // If there's an unstarred backtest, star it
    if (totalStars > 0) {
      await starButtons.first().click();
      await expect(
        page.locator('button[title="Already in Shortlist"]').first()
      ).toBeVisible({ timeout: 5_000 });
    }

    // Step 3: Navigate to Paper Bots
    await page.goto("/bots");
    await page.waitForLoadState("networkidle");
    await expect(page.locator("text=Paper Bots")).toBeVisible();

    // Step 4: Click "From Shortlist"
    const fromShortlist = page.locator("button", {
      hasText: "From Shortlist",
    });
    await expect(fromShortlist).toBeVisible();
    await expect(fromShortlist).toBeEnabled({ timeout: 5_000 });
    await fromShortlist.click();

    // Step 5: Verify dropdown appears
    const dropdown = page.locator(".absolute.top-full");
    await expect(dropdown).toBeVisible({ timeout: 3_000 });

    // Click first entry
    const firstEntry = dropdown.locator("button").first();
    await expect(firstEntry).toBeVisible();
    await firstEntry.click();

    // Step 6: Verify form fields populated
    const strategySelect = page.locator("select").first();
    const selectedValue = await strategySelect.inputValue();
    expect(selectedValue).not.toBe("");

    // Dropdown closed
    await expect(dropdown).not.toBeVisible();
  });

  test("From Shortlist button exists on Backtests run form", async ({
    page,
  }) => {
    await page.goto("/backtests");
    await page.waitForLoadState("networkidle");

    const fromShortlist = page.locator("button", {
      hasText: "From Shortlist",
    });
    await expect(fromShortlist).toBeVisible();
  });
});
