import { defineConfig } from "@playwright/test";

/**
 * Playwright config for Fiboki smoke tests and screenshot capture.
 *
 * Usage:
 *   npx playwright test --project=smoke                          # smoke tests
 *   npx playwright test --project=screenshots                    # capture screenshots
 *   BASE_URL=https://fiboki.uk npx playwright test --project=screenshots
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
        viewport: { width: 1280, height: 800 },
      },
    },
  ],
  // Don't start a dev server automatically — caller is responsible
  webServer: undefined,
});
