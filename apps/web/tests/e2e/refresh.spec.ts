import { expect, test, type Page, type Route } from "@playwright/test";
import { API, boardRow, boardView, figure, healthReport, mockApi, modeBanner } from "./fixtures";

/**
 * Polling must not blank the view.
 *
 * `useApi` used to reset to "loading" on every poll tick, so every 30 seconds
 * the execution-mode banner turned grey and read "MODE …", and every 20 seconds
 * the overview's health panel collapsed to skeletons. An outage and a routine
 * refresh looked the same. After the first success a view now keeps its last
 * good data; a failed refresh adds a STALE marker beside it.
 *
 * The page clock is faked so a 30-second poll interval runs in milliseconds.
 */

const unavailable = {
  status: 503,
  json: {
    code: "unavailable",
    detail: "The platform is restarting.",
    correlation_id: "cid-refresh-0001",
    context: {},
  },
};

/** Record every data-mode the banner takes, from the first paint onwards. */
async function recordBannerModes(page: Page) {
  await page.addInitScript(() => {
    const history: string[] = [];
    (window as unknown as { __bannerModes: string[] }).__bannerModes = history;
    const sample = () => {
      const el = document.querySelector('[data-testid="mode-banner"]');
      if (!el) return;
      const mode = el.getAttribute("data-mode") ?? "none";
      const text = el.textContent ?? "";
      const entry = text.includes("MODE …") ? `${mode}|loading-text` : mode;
      if (history[history.length - 1] !== entry) history.push(entry);
    };
    new MutationObserver(sample).observe(document, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true,
    });
  });
}

async function bannerModes(page: Page): Promise<string[]> {
  return page.evaluate(
    () => (window as unknown as { __bannerModes: string[] }).__bannerModes,
  );
}

test.describe("refresh keeps the last good data", () => {
  test("the mode banner keeps the mode, marked stale, when a refresh fails", async ({
    page,
  }) => {
    await page.clock.install();
    await recordBannerModes(page);
    await mockApi(page);

    let calls = 0;
    let releaseSecond: (() => void) | null = null;
    const secondGate = new Promise<void>((resolve) => {
      releaseSecond = resolve;
    });
    await page.route(`${API}/api/system/execution-mode`, async (route: Route) => {
      calls += 1;
      if (calls === 1) return route.fulfill({ json: modeBanner() });
      if (calls === 2) await secondGate;
      return route.fulfill(unavailable);
    });

    await page.goto("/trading/execution");
    const banner = page.getByTestId("mode-banner");
    await expect(banner).toHaveAttribute("data-mode", "paper");
    await expect(banner).toHaveAttribute("data-stale", "false");

    // First poll tick: the refresh is IN FLIGHT. The banner still reads PAPER.
    await page.clock.fastForward(30_500);
    await expect.poll(() => calls).toBe(2);
    await expect(banner).toHaveAttribute("data-mode", "paper");
    await expect(page.getByTestId("mode-banner-mode")).toHaveText("PAPER");
    await expect(banner).not.toContainText("MODE …");

    // The refresh fails: last mode kept, stale marker shown, never UNKNOWN.
    releaseSecond?.();
    const stale = page.getByTestId("mode-banner-stale");
    await expect(stale).toBeVisible();
    await expect(stale).toContainText("STALE");
    await expect(stale).toContainText("last good");
    await expect(stale).toContainText("retrying");
    await expect(banner).toHaveAttribute("data-mode", "paper");
    await expect(banner).toHaveAttribute("data-stale", "true");
    await expect(page.getByTestId("mode-banner-mode")).toHaveText("PAPER");
    await expect(banner).not.toContainText("MODE …");
    await expect(banner).not.toContainText("MODE UNKNOWN");

    // It keeps retrying on the interval and keeps the mode through each failure.
    await page.clock.fastForward(30_500);
    await expect.poll(() => calls).toBeGreaterThanOrEqual(3);
    await expect(banner).toHaveAttribute("data-mode", "paper");

    // Across the whole session, once a mode was shown, the banner never went
    // back to the loading state or its "MODE …" text.
    const modes = await bannerModes(page);
    const firstPaper = modes.indexOf("paper");
    expect(firstPaper, `banner history: ${modes.join(" > ")}`).toBeGreaterThanOrEqual(0);
    const after = modes.slice(firstPaper);
    expect(after.filter((m) => m !== "paper"), `banner history: ${modes.join(" > ")}`).toEqual(
      [],
    );
  });

  test("the stale marker clears when a later refresh succeeds", async ({ page }) => {
    await page.clock.install();
    await mockApi(page);
    let calls = 0;
    await page.route(`${API}/api/system/execution-mode`, (route: Route) => {
      calls += 1;
      if (calls === 2) return route.fulfill(unavailable);
      return route.fulfill({
        json: modeBanner(calls >= 3 ? { kill_switch_active: true, kill_switch_mode: "pause" } : {}),
      });
    });
    await page.goto("/trading/execution");
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "paper");
    await page.clock.fastForward(30_500);
    await expect(page.getByTestId("mode-banner-stale")).toBeVisible();
    await page.clock.fastForward(30_500);
    await expect(page.getByTestId("mode-banner-stale")).toHaveCount(0);
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-stale", "false");
    // The newer payload replaced the old one.
    await expect(page.getByTestId("mode-banner-killswitch")).toBeVisible();
  });

  test("the System board keeps its numbers across a poll tick", async ({ page }) => {
    await page.clock.install();
    await mockApi(page);
    await page.route(`${API}/api/health`, (route: Route) => route.fulfill({ json: healthReport() }));

    let calls = 0;
    let releaseSecond: (() => void) | null = null;
    const secondGate = new Promise<void>((resolve) => {
      releaseSecond = resolve;
    });
    const second = boardView([
      boardRow({ id: "worker:w1", group: "worker", name: "w1", age: figure(4, "paper", "s") }),
    ]);
    await page.route(`${API}/api/system/board`, async (route: Route) => {
      calls += 1;
      if (calls === 1) {
        return route.fulfill({
          json: boardView([boardRow({ id: "worker:w1", group: "worker", name: "w1", age: figure(12, "paper", "s") })]),
        });
      }
      if (calls === 2) {
        await secondGate;
        return route.fulfill({ json: second });
      }
      return route.fulfill(unavailable);
    });

    await page.goto("/system");
    const age = page.locator('[data-testid="board-row"][data-id="worker:w1"] [data-testid="board-age"]');
    await expect(age).toHaveText("12s");

    // Poll tick with the refresh held in flight: numbers stay, no skeleton.
    await page.clock.fastForward(10_500);
    await expect.poll(() => calls).toBe(2);
    await expect(age).toHaveText("12s");
    await expect(page.getByText("Loading the system board")).toHaveCount(0);

    // The refresh lands: the new numbers replace the old, still no skeleton.
    releaseSecond?.();
    await expect(age).toHaveText("4s");

    // The next refresh fails: numbers kept, STALE beside them, the page says so.
    await page.clock.fastForward(10_500);
    await expect.poll(() => calls).toBeGreaterThanOrEqual(3);
    await expect(page.getByTestId("state-stale").first()).toContainText("STALE");
    await expect(age).toHaveText("4s");
    await expect(page.getByTestId("page-stale-banner")).toBeVisible();
    await expect(page.getByText("Could not load the system board")).toHaveCount(0);
  });

  test("a first-load failure is still an error, not a stale success", async ({ page }) => {
    await mockApi(page);
    await page.route(`${API}/api/system/board`, (route: Route) => route.fulfill(unavailable));
    await page.goto("/system");
    await expect(page.getByText("Could not load the system board")).toBeVisible();
    await expect(page.getByTestId("board-summary")).toHaveCount(0);
  });
});
