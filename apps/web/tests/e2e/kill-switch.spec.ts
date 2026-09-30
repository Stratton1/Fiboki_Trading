import { expect, test } from "@playwright/test";
import { API, killSwitchView, mockApi, modeBanner } from "./fixtures";

/**
 * The kill switch: reachable in every mode, PAUSE and FLATTEN as distinct
 * explicit choices, behind the one shared confirm dialog.
 *
 * V1's was a bare icon button with no confirmation that could not be armed in
 * paper mode at all.
 */

test.describe("kill switch", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("is reachable and armable in paper mode", async ({ page }) => {
    await page.goto("/trading/risk");
    const panel = page.getByTestId("kill-switch-panel");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "false");
    await expect(page.getByTestId("kill-switch-arm")).toBeEnabled();
    await expect(panel).toContainText("armable in every mode, including this one");
  });

  test("is armable in backtest mode too", async ({ page }) => {
    await page.route(`${API}/api/system/execution-mode`, (route) =>
      route.fulfill({ json: modeBanner({ mode: "backtest", provenance: "backtest" }) }),
    );
    await page.goto("/trading/risk");
    await expect(page.getByTestId("kill-switch-arm")).toBeEnabled();
  });

  test("arming opens the shared confirm dialog, never a native confirm", async ({ page }) => {
    let nativeDialogs = 0;
    page.on("dialog", (dialog) => {
      nativeDialogs += 1;
      void dialog.dismiss();
    });
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await expect(page.getByTestId("confirm-dialog")).toBeVisible();
    expect(nativeDialogs).toBe(0);
  });

  test("PAUSE and FLATTEN are distinct explicit choices with no default", async ({ page }) => {
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();

    const pause = page.getByTestId("confirm-choice-pause");
    const flatten = page.getByTestId("confirm-choice-flatten");
    await expect(pause).toBeVisible();
    await expect(flatten).toBeVisible();

    // Nothing is pre-selected, and the confirm button is dead until it is.
    await expect(pause).toHaveAttribute("data-selected", "false");
    await expect(flatten).toHaveAttribute("data-selected", "false");
    await expect(page.getByTestId("confirm-no-choice")).toBeVisible();
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
  });

  test("consequences come from the API and differ between the two choices", async ({ page }) => {
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();

    await page.getByTestId("confirm-choice-pause").click();
    const pauseText = await page.getByTestId("confirm-consequences").innerText();
    expect(pauseText).toContain("LEFT OPEN");

    await page.getByTestId("confirm-choice-flatten").click();
    const flattenText = await page.getByTestId("confirm-consequences").innerText();
    expect(flattenText).toContain("queued to be closed");
    expect(flattenText).not.toBe(pauseText);
  });

  test("the dialog states the current execution mode", async ({ page }) => {
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await expect(page.getByTestId("confirm-mode")).toContainText("PAPER");
  });

  test("a reason is mandatory before the switch can be armed", async ({ page }) => {
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-pause").click();

    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-reason").fill("short");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-reason").fill("spread blowout on the london open");
    await expect(page.getByTestId("confirm-submit")).toBeEnabled();
  });

  test("confirming posts the chosen mode and the reason", async ({ page }) => {
    let posted: Record<string, unknown> | null = null;
    const armed = killSwitchView({
      active: true,
      mode: "flatten",
      operator: "joe",
      reason: "data feed went stale mid-session",
      requires_flatten: true,
      blocks_new_risk: true,
    });
    // The platform's state, as a real backend would hold it: the GET shows
    // the arm once the POST has been accepted. The dialog closes only when
    // that state is read back (no optimistic UI; stream.spec covers the
    // stream echo, this covers the REST echo when no stream is connected).
    await page.route(`${API}/api/system/kill-switch`, (route) =>
      route.fulfill({ json: posted ? armed : killSwitchView() }),
    );
    await page.route(`${API}/api/system/kill-switch/arm`, async (route) => {
      posted = JSON.parse(route.request().postData() ?? "{}");
      await route.fulfill({ json: armed });
    });

    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-flatten").click();
    await page.getByTestId("confirm-reason").fill("data feed went stale mid-session");
    // FLATTEN closes every position: the typed phrase is required in every mode.
    await page.getByTestId("confirm-phrase").fill("FLATTEN");
    await page.getByTestId("confirm-submit").click();

    await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
    expect(posted).toEqual({
      mode: "flatten",
      reason: "data feed went stale mid-session",
    });
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "true");
    await expect(page.getByTestId("kill-switch-confirming")).toHaveCount(0);
  });

  test("friction is asymmetric: PAUSE needs a reason only, FLATTEN also the typed word", async ({
    page,
  }) => {
    // Report G W-08/W-09, plan §3: the emergency brake is never behind a
    // typing test; closing every position at market always is.
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-pause").click();
    await page.getByTestId("confirm-reason").fill("spread blowout on the open");
    await expect(page.getByTestId("confirm-phrase")).toHaveCount(0);
    await expect(page.getByTestId("confirm-submit")).toBeEnabled();

    await page.getByTestId("confirm-choice-flatten").click();
    await expect(page.getByTestId("confirm-phrase")).toHaveAttribute("data-phrase", "FLATTEN");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-phrase").fill("flatten");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-phrase").fill("FLATTEN");
    await expect(page.getByTestId("confirm-submit")).toBeEnabled();
  });

  for (const mode of ["backtest", "demo"]) {
    test(`FLATTEN requires the typed word in ${mode} mode too`, async ({ page }) => {
      await page.route(`${API}/api/system/execution-mode`, (route) =>
        route.fulfill({
          json: modeBanner({ mode, provenance: mode === "demo" ? "broker_demo" : "backtest" }),
        }),
      );
      await page.goto("/trading/risk");
      await page.getByTestId("kill-switch-arm").click();
      await page.getByTestId("confirm-choice-flatten").click();
      await page.getByTestId("confirm-reason").fill("closing everything ahead of the release");
      await expect(page.getByTestId("confirm-submit")).toBeDisabled();
      await page.getByTestId("confirm-phrase").fill("FLATTEN");
      await expect(page.getByTestId("confirm-submit")).toBeEnabled();
    });
  }

  test("cancelling posts nothing", async ({ page }) => {
    let calls = 0;
    await page.route(`${API}/api/system/kill-switch/arm`, async (route) => {
      calls += 1;
      await route.fulfill({ json: killSwitchView() });
    });
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-pause").click();
    await page.getByTestId("confirm-reason").fill("thinking better of it");
    await page.getByTestId("confirm-cancel").click();
    await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
    expect(calls).toBe(0);
  });

  test("a refused arm shows the reason instead of silently succeeding", async ({ page }) => {
    await page.route(`${API}/api/system/kill-switch/arm`, (route) =>
      route.fulfill({
        status: 403,
        json: {
          code: "insufficient_role",
          detail: "This action requires the admin role.",
          correlation_id: "cid-xyz",
          context: {},
        },
      }),
    );
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-pause").click();
    await page.getByTestId("confirm-reason").fill("attempting without permission");
    await page.getByTestId("confirm-submit").click();

    await expect(page.getByTestId("confirm-error")).toContainText("requires the admin role");
    await expect(page.getByTestId("confirm-dialog")).toBeVisible();
  });

  test("disarming requires typing the confirm phrase", async ({ page }) => {
    await page.route(`${API}/api/system/kill-switch`, (route) =>
      route.fulfill({
        json: killSwitchView({
          active: true,
          mode: "pause",
          operator: "joe",
          reason: "spread blowout",
          blocks_new_risk: true,
        }),
      }),
    );
    await page.goto("/trading/risk");
    // Worded as what it does (inventory F-13): "arm" means HALT on this card,
    // so "re-arm trading" read as its opposite.
    await expect(page.getByTestId("kill-switch-disarm")).toHaveText("Lift the halt (resume trading)");
    await page.getByTestId("kill-switch-disarm").click();
    await expect(page.getByTestId("confirm-dialog").locator("h2").first()).toHaveText("Lift the halt and resume trading");
    await expect(page.getByTestId("confirm-phrase")).toHaveAttribute("data-phrase", "LIFT HALT");
    await page.getByTestId("confirm-reason").fill("spreads have normalised again");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-phrase").fill("RE-ARM");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-phrase").fill("LIFT HALT");
    await expect(page.getByTestId("confirm-submit")).toBeEnabled();
    await expect(page.getByTestId("confirm-submit")).toHaveText("Lift the halt");
  });

  test("the same dialog component is used to promote a candidate", async ({ page }) => {
    await page.goto("/trading/candidates");
    await page.getByTestId("promote-ichimoku_kumo_trend").click();
    await expect(page.getByTestId("confirm-dialog")).toBeVisible();
    await expect(page.getByTestId("confirm-mode")).toContainText("PAPER");
    await expect(page.getByTestId("confirm-choice-paper")).toBeVisible();
    await expect(page.getByTestId("confirm-choice-shadow")).toBeVisible();
    // Neither demo nor live is offered anywhere in this dialog.
    await expect(page.getByTestId("confirm-choice-demo")).toHaveCount(0);
    await expect(page.getByTestId("confirm-choice-live")).toHaveCount(0);
  });

  test("an ineligible candidate cannot be promoted at all", async ({ page }) => {
    await page.goto("/trading/candidates");
    await expect(page.getByTestId("promote-donchian_breakout_atr")).toBeDisabled();
  });
});
