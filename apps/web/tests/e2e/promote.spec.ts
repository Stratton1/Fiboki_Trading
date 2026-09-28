import { expect, test, type Route } from "@playwright/test";
import {
  API,
  PREFLIGHT_CAVEATS,
  disarmPreflight,
  killSwitchView,
  mockApi,
  promotePreflight,
} from "./fixtures";

/**
 * The promote dialog no longer signs the caveat acknowledgement for the
 * operator, and no longer writes its own consequences.
 *
 * It used to send `acknowledge_caveats: true` unconditionally and render a
 * consequence list hard-coded in page code, even though ConfirmDialog documents
 * consequences as server-computed. Now each caveat returned by the preflight is
 * a required checkbox, and the consequence text is the API's, verbatim.
 */

const CODES = PREFLIGHT_CAVEATS.map((c) => c.code);

async function openPromote(page: import("@playwright/test").Page) {
  await page.goto("/trading/candidates");
  await page.getByTestId("promote-ichimoku_kumo_trend").click();
  await expect(page.getByTestId("confirm-dialog")).toBeVisible();
}

test.describe("promote dialog", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("renders every caveat as an unticked required checkbox", async ({ page }) => {
    await openPromote(page);
    const list = page.getByTestId("confirm-acknowledgements");
    await expect(list).toBeVisible();
    for (const caveat of PREFLIGHT_CAVEATS) {
      const row = page.getByTestId(`confirm-ack-${caveat.code}`);
      await expect(row).toContainText(caveat.code);
      await expect(row).toContainText(caveat.message);
      await expect(page.getByTestId(`confirm-ack-input-${caveat.code}`)).not.toBeChecked();
    }
  });

  test("Promote stays disabled until every caveat is ticked", async ({ page }) => {
    await openPromote(page);
    await page.getByTestId("confirm-choice-paper").click();
    await page.getByTestId("confirm-reason").fill("out-of-sample evidence reviewed");
    const submit = page.getByTestId("confirm-submit");

    await expect(submit).toBeDisabled();
    for (const [index, code] of CODES.entries()) {
      await page.getByTestId(`confirm-ack-input-${code}`).check();
      if (index < CODES.length - 1) await expect(submit).toBeDisabled();
    }
    await expect(submit).toBeEnabled();

    // Unticking any one disables it again.
    await page.getByTestId(`confirm-ack-input-${CODES[1]}`).uncheck();
    await expect(submit).toBeDisabled();
  });

  test("the request acknowledges caveats only once all are ticked, naming them", async ({
    page,
  }) => {
    const posted: Record<string, unknown>[] = [];
    await page.route(
      `${API}/api/trading/candidates/ichimoku_kumo_trend/promote`,
      async (route: Route) => {
        posted.push(JSON.parse(route.request().postData() ?? "{}"));
        await route.fulfill({
          json: {
            data: { strategy_id: "ichimoku_kumo_trend", target_lifecycle: "paper", recorded: true },
            source: { kind: "live", detail: "Recorded.", as_of: null },
            caveats: [],
          },
        });
      },
    );
    await openPromote(page);
    await page.getByTestId("confirm-choice-paper").click();
    await page.getByTestId("confirm-reason").fill("out-of-sample evidence reviewed");

    // Partially ticked: the button cannot be pressed, so nothing is sent.
    await page.getByTestId(`confirm-ack-input-${CODES[0]}`).check();
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-submit").click({ force: true });
    expect(posted).toEqual([]);

    for (const code of CODES) await page.getByTestId(`confirm-ack-input-${code}`).check();
    await page.getByTestId("confirm-submit").click();
    await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);

    expect(posted).toHaveLength(1);
    expect(posted[0]).toEqual({
      target_lifecycle: "paper",
      reason: "out-of-sample evidence reviewed",
      acknowledge_caveats: true,
      acknowledged_caveats: CODES,
    });
  });

  test("ticks do not survive into the next promotion", async ({ page }) => {
    await openPromote(page);
    for (const code of CODES) await page.getByTestId(`confirm-ack-input-${code}`).check();
    await page.getByTestId("confirm-cancel").click();
    await page.getByTestId("promote-ichimoku_kumo_trend").click();
    for (const code of CODES) {
      await expect(page.getByTestId(`confirm-ack-input-${code}`)).not.toBeChecked();
    }
  });

  test("consequences are the API's text, verbatim, per target", async ({ page }) => {
    const expected = promotePreflight().data.consequences;
    await openPromote(page);

    await page.getByTestId("confirm-choice-paper").click();
    await expect(page.getByTestId("confirm-consequences").locator("li")).toHaveText(
      expected.paper,
    );
    await page.getByTestId("confirm-choice-shadow").click();
    await expect(page.getByTestId("confirm-consequences").locator("li")).toHaveText(
      expected.shadow,
    );
  });

  test("choices follow the API's consequence keys", async ({ page }) => {
    await page.route(`${API}/api/trading/candidates/*/promote/preflight`, (route: Route) =>
      route.fulfill({
        json: promotePreflight({
          consequences: { paper: ["SERVER-ONLY-PAPER: the only target offered."] },
        }),
      }),
    );
    await openPromote(page);
    await expect(page.getByTestId("confirm-choice-shadow")).toHaveCount(0);
    await expect(page.getByTestId("confirm-consequences").locator("li")).toHaveText([
      "SERVER-ONLY-PAPER: the only target offered.",
    ]);
  });

  test("an ineligible preflight offers no choice and says why", async ({ page }) => {
    await page.route(`${API}/api/trading/candidates/*/promote/preflight`, (route: Route) =>
      route.fulfill({
        json: promotePreflight({
          eligible: false,
          blocking_reasons: ["The kill switch is armed. Promotion is blocked while trading is halted."],
        }),
      }),
    );
    await openPromote(page);
    await expect(page.getByTestId("confirm-notice")).toContainText("kill switch is armed");
    await expect(page.getByTestId("confirm-choice-paper")).toHaveCount(0);
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
  });

  test("a failed preflight blocks confirmation rather than guessing", async ({ page }) => {
    await page.route(`${API}/api/trading/candidates/*/promote/preflight`, (route: Route) =>
      route.fulfill({
        status: 503,
        json: {
          code: "database_unreachable",
          detail: "The platform could not reach its database.",
          correlation_id: "cid-pre-1",
          context: {},
        },
      }),
    );
    await openPromote(page);
    await expect(page.getByTestId("confirm-error")).toContainText("database_unreachable");
    await expect(page.getByTestId("confirm-consequences")).toHaveCount(0);
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
  });
});

test.describe("kill-switch disarm dialog", () => {
  test("renders the server-computed disarm consequences verbatim", async ({ page }) => {
    await mockApi(page);
    const lines = [
      "SERVER-DISARM-A: lifts the PAUSE halt armed by joe at 2026-09-19 12:00 UTC.",
      "SERVER-DISARM-B: 3 position(s) are currently open in paper mode.",
    ];
    await page.route(`${API}/api/system/kill-switch`, (route: Route) =>
      route.fulfill({
        json: killSwitchView({ active: true, mode: "pause", operator: "joe", reason: "spread blowout" }),
      }),
    );
    await page.route(`${API}/api/trading/preflight/kill-switch-disarm`, (route: Route) =>
      route.fulfill({ json: disarmPreflight(lines) }),
    );
    await page.goto("/trading/risk");
    await page.getByTestId("kill-switch-disarm").click();
    await expect(page.getByTestId("confirm-consequences").locator("li")).toHaveText(lines);
  });
});
