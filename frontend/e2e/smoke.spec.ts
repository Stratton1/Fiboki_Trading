import { test, expect } from "@playwright/test";

/**
 * Fiboki Smoke Tests
 *
 * Lightweight checks that confirm the deployed platform is healthy.
 * These test unauthenticated routing and page rendering only.
 *
 * Intentionally excluded:
 *   - Research page (C1 is actively working on Phase 8)
 *   - Mutating operations (login with real credentials, creating backtests, etc.)
 *   - Flows requiring auth secrets
 *
 * Run against local:       npx playwright test
 * Run against production:  BASE_URL=https://fiboki.uk npx playwright test
 */

// ─── Unauthenticated routing ───────────────────────────────────────

test.describe("unauthenticated routing", () => {
  test("/ redirects to /login", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
  });

  test("/charts redirects to /login", async ({ page }) => {
    await page.goto("/charts");
    await expect(page).toHaveURL(/\/login/);
  });

  test("/backtests redirects to /login", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page).toHaveURL(/\/login/);
  });

  test("/bots redirects to /login", async ({ page }) => {
    await page.goto("/bots");
    await expect(page).toHaveURL(/\/login/);
  });

  test("/trades redirects to /login", async ({ page }) => {
    await page.goto("/trades");
    await expect(page).toHaveURL(/\/login/);
  });

  test("/settings redirects to /login", async ({ page }) => {
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/login/);
  });

  test("/system redirects to /login", async ({ page }) => {
    await page.goto("/system");
    await expect(page).toHaveURL(/\/login/);
  });
});

// ─── Login page rendering ──────────────────────────────────────────

test.describe("login page", () => {
  test("renders branding and form", async ({ page }) => {
    await page.goto("/login");

    // Branding
    await expect(page.locator("h1")).toContainText("Fiboki Trading");
    await expect(page.locator("text=Trading Research Platform")).toBeVisible();

    // Form elements
    await expect(page.locator("#username")).toBeVisible();
    await expect(page.locator("#password")).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toContainText(
      "Sign In"
    );
  });

  test("shows validation on empty submit", async ({ page }) => {
    await page.goto("/login");

    // HTML5 required attributes should prevent empty submission
    const username = page.locator("#username");
    await expect(username).toHaveAttribute("required", "");

    const password = page.locator("#password");
    await expect(password).toHaveAttribute("required", "");
  });
});

// ─── Authenticated page shells ─────────────────────────────────────
//
// These tests inject the fiboki_auth marker cookie to bypass the
// Next.js middleware redirect. The pages will render their shell/layout
// but API calls will fail (no valid JWT). This is sufficient to confirm
// the page components load without fatal JS errors.

test.describe("authenticated page shells", () => {
  test.beforeEach(async ({ context }) => {
    // Set the marker cookie that Next.js middleware checks.
    // This does NOT authenticate with the backend — API calls will 401.
    // We're only checking that pages render their shell without crashing.
    const baseURL = process.env.BASE_URL || "http://localhost:3000";
    const url = new URL(baseURL);
    await context.addCookies([
      {
        name: "fiboki_auth",
        value: "1",
        domain: url.hostname,
        path: "/",
      },
    ]);
  });

  test("dashboard loads", async ({ page }) => {
    await page.goto("/");
    // Should NOT redirect to /login
    await expect(page).not.toHaveURL(/\/login/);
    // Page should have rendered something (not a blank crash)
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("charts page loads", async ({ page }) => {
    await page.goto("/charts");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("backtests page loads", async ({ page }) => {
    await page.goto("/backtests");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("bots page loads", async ({ page }) => {
    await page.goto("/bots");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("trades page loads", async ({ page }) => {
    await page.goto("/trades");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("settings page loads", async ({ page }) => {
    await page.goto("/settings");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("system page loads", async ({ page }) => {
    await page.goto("/system");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator("body")).not.toBeEmpty();
  });
});
