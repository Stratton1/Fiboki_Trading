import { expect, test, type Page } from "@playwright/test";
import { mockShell } from "./fixtures";

/**
 * The terminal chrome: the command line (mnemonics, symbols, `/` to focus),
 * the F1-F9 section keys, and the inverted mode cell. All of it navigates or
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

test.describe("function keys", () => {
  test("nine keys, one per section, the current section marked", async ({ page }) => {
    await goReady(page);
    const keys = page.getByTestId("function-keys").getByRole("link");
    await expect(keys).toHaveCount(9);
    await expect(page.getByTestId("fkey-command")).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("fkey-risk")).not.toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("fkey-risk")).toContainText("F6");
    await expect(page.getByTestId("fkey-risk")).toContainText("RSK");
  });

  test("clicking a key goes to that section's first view", async ({ page }) => {
    await goReady(page);
    await page.getByTestId("fkey-research").click();
    await expect(page).toHaveURL(/\/research$/);
  });

  test("the row is one tab stop; arrows, Home and End move between keys", async ({ page }) => {
    await goReady(page);
    const keys = page.getByTestId("function-keys").getByRole("link");
    await expect(page.getByTestId("fkey-command")).toHaveAttribute("tabindex", "0");
    await expect(page.getByTestId("fkey-fleet")).toHaveAttribute("tabindex", "-1");
    await page.getByTestId("fkey-command").focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("fkey-fleet")).toBeFocused();
    await page.keyboard.press("End");
    await expect(page.getByTestId("fkey-journal")).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("fkey-command")).toBeFocused();
    await expect(keys).toHaveCount(9);
  });

  test("F6 goes to Risk; F5 is left to the browser", async ({ page }) => {
    await goReady(page);
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
