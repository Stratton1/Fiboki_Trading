import { expect, test, type Page } from "@playwright/test";
import { mockShell } from "./fixtures";

/**
 * The terminal chrome: the command line (codes, symbols, `/` to focus), the
 * F1-F9 section keys (in the rail), the session clocks and the inverted mode
 * cell. All of it navigates or
 * opens a dialog; none of it changes anything on the platform.
 */

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await mockShell(page);
});

/** The command line's engine loads right after hydration; wait for it. */
async function goReady(page: Page, path = "/") {
  await page.goto(path);
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
}

test.describe("function keys (merged into the rail)", () => {
  test("one top line: no second row of keys, and the rail carries every section's key", async ({ page }) => {
    await goReady(page);
    await expect(page.getByTestId("function-keys")).toHaveCount(0);
    const top = await page.getByTestId("topbar").boundingBox();
    expect(top?.height ?? 99).toBeLessThanOrEqual(30);
    await expect(page.getByTestId("fkey-command")).toHaveText("F1");
    await expect(page.getByTestId("fkey-fleet")).toHaveText("F2");
    await expect(page.getByTestId("fkey-risk")).toHaveText("F6");
    // F5 is the browser's reload: Market Intelligence shows its mnemonic.
    await expect(page.getByTestId("fkey-markets")).toHaveText("MKT");
    await expect(page.getByTestId("rail-command")).toHaveAttribute("data-active", "true");
  });

  test("F2 opens the fleet, F6 Risk; F5 is left to the browser", async ({ page }) => {
    await goReady(page);
    await page.keyboard.press("F2");
    await expect(page).toHaveURL(/\/trading\/fleet$/);
    await page.keyboard.press("F6");
    await expect(page).toHaveURL(/\/trading\/risk$/);
    await page.keyboard.press("F4");
    await expect(page).toHaveURL(/\/research$/);
    const prevented = await page.evaluate(
      () =>
        new Promise<boolean>((resolve) => {
          const event = new KeyboardEvent("keydown", { key: "F5", cancelable: true, bubbles: true });
          window.dispatchEvent(event);
          resolve(event.defaultPrevented);
        }),
    );
    expect(prevented).toBe(false);
  });

  test("the market session clocks come from the API", async ({ page }) => {
    await goReady(page);
    await expect(page.getByTestId("session-london")).toHaveAttribute("data-open", "true");
    await expect(page.getByTestId("session-tokyo")).toHaveAttribute("data-open", "false");
    await expect(page.getByTestId("session-london")).toContainText("LDN");
  });
});

test.describe("command line", () => {
  test("a mnemonic and Enter goes to that view", async ({ page }) => {
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.fill("expo");
    await expect(page.getByTestId("command-line-list")).toContainText("Exposure");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/trading\/exposure$/);
  });

  test("a symbol opens that instrument's chart", async ({ page }) => {
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.fill("eurusd");
    await input.press("Enter");
    await expect(page).toHaveURL(/\/markets\/EURUSD$/);
  });

  test("arrow keys choose among matches", async ({ page }) => {
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.fill("r");
    const options = page.getByTestId("command-line-list").getByRole("option");
    await expect(options.first()).toBeVisible();
    // Nothing is chosen until the operator chooses.
    await expect(options.first()).toHaveAttribute("aria-selected", "false");
    await input.press("ArrowDown");
    await expect(options.first()).toHaveAttribute("aria-selected", "true");
    await input.press("ArrowDown");
    await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
    await input.press("Escape");
    await expect(input).toHaveValue("");
  });

  test("/ focuses the line from anywhere that is not a field", async ({ page }) => {
    await goReady(page);
    await page.keyboard.press("/");
    await expect(page.getByTestId("command-line-input")).toBeFocused();
    // Typing in the field is typing: g does not start a chord there.
    await page.keyboard.type("gc");
    await expect(page.getByTestId("command-line-input")).toHaveValue("gc");
  });

  test("an empty Enter opens the palette; KILL only opens the kill-switch dialog", async ({ page }) => {
    await goReady(page);
    const input = page.getByTestId("command-line-input");
    await input.focus();
    await input.press("Enter");
    await expect(page.getByTestId("command-palette")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("command-palette")).toHaveCount(0);
    await input.fill("kill");
    await input.press("Enter");
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByTestId("mode-banner-killswitch-off")).toBeVisible();
  });
});

test.describe("mode is unmistakable", () => {
  test("the status bar's mode cell is a solid block in the mode's colour", async ({ page }) => {
    await goReady(page);
    const cell = page.getByTestId("status-mode");
    await expect(cell).toHaveAttribute("data-mode", "paper");
    const [bg, banner] = await Promise.all([
      cell.evaluate((el) => getComputedStyle(el).backgroundColor),
      page
        .getByTestId("mode-banner-mode")
        .evaluate((el) => getComputedStyle(el).backgroundColor),
    ]);
    expect(bg).not.toBe("rgba(0, 0, 0, 0)");
    expect(banner).not.toBe("rgba(0, 0, 0, 0)");
  });

  test("the command line is not offered on the sign-in page (no session)", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByTestId("command-line")).toHaveCount(0);
    await expect(page.getByTestId("mode-banner")).toBeVisible();
  });
});
