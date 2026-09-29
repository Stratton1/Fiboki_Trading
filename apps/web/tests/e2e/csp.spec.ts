import { expect, test, type Page } from "@playwright/test";
import { API, mockShell } from "./fixtures";
import { allRoutes } from "./routes";

/**
 * Content-Security-Policy (next.config.ts). The header is present with the
 * directives the report asks for, and no route, dialog or popover triggers a
 * single violation: no inline style attribute, no <style> element, no eval,
 * no connection outside this origin and the API origin.
 */

async function recordViolations(page: Page) {
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { __csp: string[] }).__csp = seen;
    document.addEventListener("securitypolicyviolation", (event) => {
      seen.push(
        `${event.violatedDirective} ${event.blockedURI} ${event.sourceFile}:${event.lineNumber}`,
      );
    });
  });
}

const violations = (page: Page) =>
  page.evaluate(() => (window as unknown as { __csp: string[] }).__csp);

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "headers and violations, desktop project");
});

test("the policy header is strict where it can be", async ({ request }) => {
  const response = await request.get("/");
  const csp = response.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("default-src 'self'");
  expect(csp).toContain(`connect-src 'self' ${API}`);
  expect(csp).toContain("style-src 'self'");
  expect(csp).not.toContain("style-src 'self' 'unsafe-inline'");
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("object-src 'none'");
  expect(csp).not.toContain("unsafe-eval");
  expect(response.headers()["x-frame-options"]).toBe("DENY");
});

test("no route triggers a violation", async ({ page }) => {
  await recordViolations(page);
  await mockShell(page);
  for (const route of allRoutes()) {
    await page.goto(route);
    await expect(page.getByTestId("mode-banner")).not.toHaveAttribute("data-mode", "loading");
    expect(await violations(page), `CSP violations on ${route}`).toEqual([]);
  }
});

test("dialogs, popovers, tooltips and the split pane trigger none", async ({ page }) => {
  await recordViolations(page);
  await mockShell(page);
  await page.goto("/trading/risk");
  await page.getByTestId("kill-switch-arm").click();
  await expect(page.getByTestId("confirm-dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByTestId("display-settings-trigger").click();
  await expect(page.getByTestId("pref-theme")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.goto("/system/legend");
  await page.getByTestId("legend-tab-controls").click();
  await expect(page.getByTestId("legend-controls")).toBeVisible();
  await page.getByRole("combobox").first().click();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Settings example" }).focus();
  await page.getByTestId("legend-open-inspector").click();
  await expect(page.getByTestId("inspector")).toBeVisible();
  expect(await violations(page)).toEqual([]);
});
