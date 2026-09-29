import { expect, test, type Page, type Route } from "@playwright/test";
import { API, candidatesPage, killSwitchView, liveBanner, mockShell, modeBanner } from "./fixtures";

/**
 * Execution mode changes the whole shell (report E §4.4, plan §3): a viewport
 * frame, the favicon and the tab title, as well as the banner. And a
 * workstation that cannot read its mode disables everything that mutates.
 */

const BANNERS: Record<string, Record<string, unknown>> = {
  backtest: { mode: "backtest", provenance: "backtest", severity: "info" },
  paper: {},
  shadow: { mode: "shadow", provenance: "shadow", severity: "info" },
  demo: {
    mode: "demo",
    provenance: "broker_demo",
    touches_broker: true,
    severity: "caution",
    headline: "DEMO: orders are reaching a broker demo account",
  },
};

const FRAME: Record<string, { width: string; style: string; title: string; token: string }> = {
  backtest: { width: "0px", style: "none", title: "[BT] Fiboki", token: "--mode-backtest" },
  paper: { width: "1px", style: "solid", title: "[PAPER] Fiboki", token: "--mode-paper" },
  shadow: { width: "2px", style: "dashed", title: "[SHADOW] Fiboki", token: "--mode-shadow" },
  demo: { width: "3px", style: "solid", title: "[DEMO] Fiboki", token: "--mode-demo" },
  live: { width: "4px", style: "solid", title: "● LIVE Fiboki", token: "--mode-live" },
};

/** The computed colour a token resolves to, via a probe element. */
async function tokenColour(page: Page, token: string): Promise<string> {
  return page.evaluate((name) => {
    const probe = document.createElement("div");
    probe.style.color = `var(${name})`;
    document.body.append(probe);
    const colour = getComputedStyle(probe).color;
    probe.remove();
    return colour;
  }, token);
}

async function frameOf(page: Page) {
  return page.getByTestId("mode-frame").evaluate((el) => {
    const cs = getComputedStyle(el);
    return { width: cs.borderTopWidth, style: cs.borderTopStyle, colour: cs.borderTopColor };
  });
}

async function withMode(page: Page, payload: unknown) {
  await page.route(`${API}/api/system/execution-mode`, (route: Route) =>
    route.fulfill({ json: payload }),
  );
}

test.describe("mode frame, favicon and title", () => {
  for (const mode of Object.keys(FRAME)) {
    test(`${mode}: frame ${FRAME[mode]!.width} ${FRAME[mode]!.style}, title and favicon`, async ({
      page,
    }) => {
      await mockShell(page);
      await withMode(page, mode === "live" ? liveBanner() : modeBanner(BANNERS[mode]));
      await page.goto("/trading/execution");

      const expected = FRAME[mode]!;
      await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", mode);
      await expect(page.getByTestId("mode-frame")).toHaveAttribute("data-mode", mode);
      const frame = await frameOf(page);
      expect(frame.width).toBe(expected.width);
      expect(frame.style).toBe(expected.style);
      if (mode !== "backtest") {
        expect(frame.colour).toBe(await tokenColour(page, expected.token));
      }

      await expect(page).toHaveTitle(expected.title);
      const icon = page.locator('head link[rel="icon"]');
      await expect(icon).toHaveCount(1);
      await expect(icon).toHaveAttribute("data-mode", mode);
      await expect(icon).toHaveAttribute("href", /^data:image\/svg\+xml,/);

      // The status bar mirrors the mode.
      await expect(page.getByTestId("status-mode")).toHaveAttribute("data-mode", mode);
    });
  }

  test("each mode has its own favicon", async ({ page }) => {
    const hrefs = new Set<string>();
    await mockShell(page);
    for (const mode of Object.keys(FRAME)) {
      await withMode(page, mode === "live" ? liveBanner() : modeBanner(BANNERS[mode]));
      await page.goto("/");
      await expect(page.getByTestId("mode-frame")).toHaveAttribute("data-mode", mode);
      const href = await page.locator('head link[rel="icon"]').getAttribute("href");
      hrefs.add(href ?? "");
    }
    expect(hrefs.size).toBe(Object.keys(FRAME).length);
  });
});

test.describe("live mode", () => {
  test("shows REAL MONEY, the operator and an always-visible kill switch", async ({ page }) => {
    await mockShell(page);
    await withMode(page, liveBanner());
    await page.goto("/research/strategies");
    const banner = page.getByTestId("mode-banner");
    await expect(banner).toHaveAttribute("data-mode", "live");
    await expect(page.getByTestId("mode-banner-real-money")).toHaveText("REAL MONEY");
    await expect(page.getByTestId("mode-banner-operator")).toHaveText("Joe");
    await expect(page.getByTestId("mode-banner-killswitch-action")).toBeVisible();
    await expect(page.getByTestId("status-mode")).toContainText("REAL MONEY");
    // Live is magenta, never the loss colour.
    expect(await tokenColour(page, "--mode-live")).not.toBe(await tokenColour(page, "--pnl-down"));
  });

  test("omits the operator's name when the platform does not supply one", async ({ page }) => {
    await mockShell(page, { operator: false });
    await withMode(page, liveBanner());
    await page.goto("/");
    await expect(page.getByTestId("mode-banner-real-money")).toBeVisible();
    await expect(page.getByTestId("mode-banner-operator")).toHaveCount(0);
    await expect(page.getByTestId("status-operator")).toHaveCount(0);
  });

  test("friction follows the act in LIVE: PAUSE a reason, FLATTEN the word, promote REAL MONEY", async ({
    page,
  }) => {
    // Report G W-08: LIVE PAUSE used to demand "REAL MONEY" typed as well,
    // putting the emergency brake behind a typing test. PAUSE only reduces
    // risk, so a reason is enough in every mode; FLATTEN needs "FLATTEN" in
    // every mode; any act without its own phrase keeps "REAL MONEY" in LIVE.
    await mockShell(page);
    await withMode(page, liveBanner());
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-pause").click();
    await page.getByTestId("confirm-reason").fill("halting ahead of the release");
    await expect(page.getByTestId("confirm-phrase")).toHaveCount(0);
    await expect(page.getByTestId("confirm-submit")).toBeEnabled();

    await page.getByTestId("confirm-choice-flatten").click();
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-phrase").fill("REAL MONEY");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-phrase").fill("FLATTEN");
    await expect(page.getByTestId("confirm-submit")).toBeEnabled();
    await page.getByTestId("confirm-cancel").click();

    await page.goto("/trading/candidates");
    await page.getByTestId("promote-ichimoku_kumo_trend").click();
    await expect(page.getByTestId("confirm-phrase")).toHaveAttribute("data-phrase", "REAL MONEY");
  });
});

test.describe("demo mode", () => {
  test("the confirm dialog carries a DEMO stamp", async ({ page }) => {
    await mockShell(page);
    await withMode(page, modeBanner(BANNERS.demo));
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await expect(page.getByTestId("confirm-mode")).toContainText("DEMO");
    await expect(page.getByTestId("confirm-dialog")).toHaveAttribute("data-mode", "demo");
  });
});

test.describe("mode unknown", () => {
  test.beforeEach(async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/system/execution-mode`, (route: Route) =>
      route.fulfill({
        status: 503,
        json: { code: "unavailable", detail: "down", correlation_id: "cid-mode", context: {} },
      }),
    );
  });

  test("frame, favicon and title say unknown", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-mode", "unknown");
    await expect(page.getByTestId("mode-frame")).toHaveAttribute("data-mode", "unknown");
    await expect(page).toHaveTitle("[MODE UNKNOWN] Fiboki");
    await expect(page.locator('head link[rel="icon"]')).toHaveAttribute("data-mode", "unknown");
    const frame = await frameOf(page);
    expect(frame.width).toBe("2px");
  });

  test("disables the kill switch", async ({ page }) => {
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-status")).toBeVisible();
    await expect(page.getByTestId("kill-switch-arm")).toBeDisabled();
    await expect(page.getByTestId("kill-switch-blocked")).toContainText("unknown");
  });

  test("disables disarm", async ({ page }) => {
    await page.route(`${API}/api/system/kill-switch`, (route: Route) =>
      route.fulfill({ json: killSwitchView({ active: true, mode: "pause", operator: "joe" }) }),
    );
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-disarm")).toBeDisabled();
  });

  test("disables promotion, even for an eligible candidate", async ({ page }) => {
    await page.goto("/trading/candidates");
    await expect(page.getByTestId("candidate-row")).toHaveCount(candidatesPage().items.length);
    await expect(page.getByTestId("promote-ichimoku_kumo_trend")).toBeDisabled();
  });
});

test.describe("a failed refresh is stale, never unknown", () => {
  test("the frame keeps the last mode through a poll failure", async ({ page }) => {
    await page.clock.install();
    await mockShell(page);
    let calls = 0;
    await page.route(`${API}/api/system/execution-mode`, (route: Route) => {
      calls += 1;
      if (calls === 1) return route.fulfill({ json: modeBanner() });
      return route.fulfill({
        status: 503,
        json: { code: "unavailable", detail: "restarting", correlation_id: "x", context: {} },
      });
    });
    await page.goto("/trading/risk");
    await expect(page.getByTestId("mode-frame")).toHaveAttribute("data-mode", "paper");
    await page.clock.fastForward(30_500);
    await expect(page.getByTestId("mode-banner-stale")).toBeVisible();
    await expect(page.getByTestId("mode-frame")).toHaveAttribute("data-mode", "paper");
    await expect(page).toHaveTitle("[PAPER] Fiboki");
    await expect(page.getByTestId("status-mode")).toContainText("stale");
    // Controls stay usable on the last known mode; the dialog states it.
    await expect(page.getByTestId("kill-switch-arm")).toBeEnabled();
  });
});
