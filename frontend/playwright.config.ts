import { defineConfig } from "@playwright/test";

/**
 * Playwright config for Fiboki smoke tests and screenshot capture.
 *
 * Usage:
 *   npx playwright test --project=smoke                          # smoke tests
 *   npx playwright test --project=screenshots                    # capture screenshots
 *   BASE_URL=https://fiboki.uk npx playwright test --project=screenshots
 *
 * Authenticated production verification:
 *   FIBOKI_E2E_USERNAME=x FIBOKI_E2E_PASSWORD=y npm run verify:prod
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 20_000,
  retries: 0,
  use: {
    baseURL: process.env.BASE_URL || "http://localhost:3000",
    // Chromium only — lightweight checks don't need cross-browser
    browserName: "chromium",
    headless: true,
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "smoke",
      testMatch: /smoke\.spec\.ts$/,
    },
    {
      name: "screenshots",
      testMatch: /screenshots\.spec\.ts$/,
      use: {
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "shortlist",
      testMatch: /shortlist-workflow\.spec\.ts$/,
      use: {
        actionTimeout: 10_000,
      },
      timeout: 30_000,
    },
    {
      name: "chart-trust",
      testMatch: /chart-trust\.spec\.ts$/,
      use: {
        actionTimeout: 10_000,
      },
      timeout: 30_000,
    },
    {
      name: "dashboard",
      testMatch: /dashboard\.spec\.ts$/,
      use: {
        actionTimeout: 10_000,
      },
      timeout: 30_000,
    },
    {
      name: "charts",
      testMatch: /charts\.spec\.ts$/,
      use: {
        actionTimeout: 10_000,
      },
      timeout: 30_000,
    },
    {
      name: "auth-prod",
      testMatch: /auth-prod\.spec\.ts$/,
      use: {
        baseURL: process.env.BASE_URL || process.env.FIBOKI_BASE_URL || "https://fiboki.uk",
        viewport: { width: 1440, height: 900 },
        // Longer timeout for real network + login flow
        actionTimeout: 15_000,
      },
      timeout: 30_000,
    },
  ],
  // Don't start a dev server automatically — caller is responsible
  webServer: undefined,
});
