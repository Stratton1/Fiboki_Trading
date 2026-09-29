import { expect, test, type Page } from "@playwright/test";
import { mockShell, riskState } from "./fixtures";
import { MockStream } from "./sse";

/**
 * freshness.spec (gate for Waves 0 and 2, report E §6.4, §8.1).
 *
 *  - No view ever returns to loading after its first data, through stream
 *    snapshots, deltas, gap resyncs, drops, reconnects and failed refreshes.
 *  - Stale, then disconnected, keep the last-known-good numbers on screen.
 *  - A connected stream with a dead worker looks stale (the V1 3 am lesson).
 *  - The worker heartbeat age counts up between heartbeats.
 *  - The platform's as-of is visible.
 */

/**
 * Record, from first paint, any panel that shows its loading state after it
 * has shown data (success or empty). Panels are told apart by their label.
 */
async function recordLoadingAfterData(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { __loadingAfterData: string[]; __hadData: Set<string> };
    w.__loadingAfterData = [];
    w.__hadData = new Set();
    const sample = () => {
      for (const el of document.querySelectorAll(
        '[data-testid="state-success"], [data-testid="state-empty"]',
      )) {
        const label = el.getAttribute("data-label");
        if (label) w.__hadData.add(label);
      }
      for (const el of document.querySelectorAll('[data-testid="state-loading"]')) {
        const label = el.getAttribute("data-label");
        if (label && w.__hadData.has(label)) w.__loadingAfterData.push(label);
      }
    };
    new MutationObserver(sample).observe(document, {
      subtree: true,
      childList: true,
      attributes: true,
    });
  });
}

async function loadingAfterData(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __loadingAfterData: string[] }).__loadingAfterData);
}

async function openRisk(page: Page, stream: MockStream) {
  await mockShell(page);
  await stream.mirrorRest(page);
  await stream.install(page);
  await page.goto("/trading/risk");
  await expect(page.getByTestId("status-stream")).toHaveAttribute("data-state", "live");
  await expect(page.getByTestId("kill-switch-status")).toBeVisible();
}

const killSwitchPanel = (page: Page) =>
  page.locator('[data-testid="state-success"]').filter({ has: page.getByTestId("kill-switch-panel") });
const riskPanel = (page: Page) =>
  page.locator('[data-testid="state-success"]').filter({ hasText: "Daily loss limit" });

test.describe("freshness", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "freshness is viewport-independent");
  });

  test("no view ever shows loading after its first data", async ({ page }) => {
    await recordLoadingAfterData(page);
    const stream = new MockStream({ risk: { state: riskState() } });
    await openRisk(page, stream);
    await expect(riskPanel(page)).toBeVisible();

    // Deltas.
    stream.armKillSwitch("tom", "pause", "spread blowout on the open");
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "true");
    // A gap, re-read by REST while REST is failing: data kept, marked stale.
    stream.restDown = true;
    stream.skip("killswitch", 2);
    stream.patch("killswitch", "kill_switch", { reason: "after the gap" });
    await expect(killSwitchPanel(page).getByTestId("state-stale")).toBeVisible();
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "true");
    // A heartbeat gap on the risk topic.
    stream.skip("risk", 1);
    stream.pushHeartbeat();
    // The stream drops, then comes back (a new subscription: fresh snapshots).
    stream.down = 503;
    await expect(page.getByTestId("status-stream")).toHaveAttribute("data-state", "reconnecting");
    stream.down = false;
    stream.restDown = false;
    await expect(page.getByTestId("status-stream")).toHaveAttribute("data-state", "live", {
      timeout: 5_000,
    });
    await expect(killSwitchPanel(page)).toHaveAttribute("data-freshness", "live");

    expect(await loadingAfterData(page)).toEqual([]);
  });

  test("a dropped stream shows STALE, then DISCONNECTED, and the numbers remain", async ({ page }) => {
    await page.clock.install();
    const stream = new MockStream();
    await openRisk(page, stream);
    const state = page.getByTestId("status-stream");

    // The platform goes away: the stream and REST both fail.
    stream.down = 503;
    stream.restDown = true;
    await expect(state).toHaveAttribute("data-failures", "1");
    // No longer fed by the stream, the kill switch is re-read every 10 s; the
    // re-read fails, so the last good state is shown as STALE.
    await page.clock.fastForward(10_500);
    const badge = killSwitchPanel(page).getByTestId("state-stale");
    await expect(badge).toBeVisible();
    await expect(badge).toContainText("STALE");
    await expect(badge).toContainText("last good");
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "false");

    // Time flows; each jump fires the pending retry. Stop at five failures.
    await expect
      .poll(
        async () => {
          await page.clock.fastForward(16_000);
          return state.getAttribute("data-state");
        },
        { timeout: 20_000, intervals: [300] },
      )
      .toBe("disconnected");
    await page.clock.fastForward(10_500);
    await expect(badge).toContainText("DISCONNECTED");
    await expect(killSwitchPanel(page)).toHaveAttribute("data-freshness", "disconnected");
    await expect(page.getByTestId("mode-banner-stale")).toContainText("DISCONNECTED");
    // Never zero, never blank: the last good state is still on screen...
    await expect(page.getByTestId("kill-switch-status")).toHaveText("DISARMED");
    await expect(page.getByTestId("mode-banner-mode")).toHaveText("PAPER");
    // ...and nothing can be confirmed while disconnected.
    await expect(page.getByTestId("kill-switch-arm")).toBeDisabled();
    await expect(page.getByTestId("kill-switch-blocked")).toContainText("disconnected");
  });

  test("a connected stream with a dead worker looks stale", async ({ page }) => {
    const stream = new MockStream({ risk: { state: riskState() } });
    stream.workerAgeS = 130;
    await openRisk(page, stream);

    await expect(page.getByTestId("status-stream")).toHaveAttribute("data-state", "live");
    const worker = page.getByTestId("status-worker");
    await expect(worker).toHaveAttribute("data-tone", "critical");
    await expect(worker).toContainText("stale");
    // Worker-produced data is stale however healthy the stream is...
    const stale = riskPanel(page).getByTestId("state-stale");
    await expect(stale).toBeVisible();
    await expect(stale).toContainText("as of");
    await expect(riskPanel(page)).toHaveAttribute("data-freshness", "stale");
    // ...while what the API itself produces (the kill switch) stays live.
    await expect(killSwitchPanel(page)).toHaveAttribute("data-freshness", "live");
  });

  test("a worker that never beat is stale, not zero", async ({ page }) => {
    const stream = new MockStream({ risk: { state: riskState() } });
    stream.workerAgeS = null;
    await openRisk(page, stream);
    await expect(page.getByTestId("status-worker")).toContainText("never");
    await expect(riskPanel(page)).toHaveAttribute("data-freshness", "stale");
  });

  test("the worker heartbeat age counts up between heartbeats", async ({ page }) => {
    await page.clock.install();
    const stream = new MockStream();
    stream.workerAgeS = 10;
    await openRisk(page, stream);
    const worker = page.getByTestId("status-worker");
    await expect(worker).toHaveAttribute("data-age", /^1[0-2]$/);
    stream.autoHeartbeat = false;
    await page.waitForTimeout(600);
    await page.clock.fastForward(20_000);
    await expect(worker).toHaveAttribute("data-age", /^3\d$/);
    await expect(worker).toHaveAttribute("data-tone", "ok");
  });

  test("a topic the stream stops confirming goes lagging, then stale", async ({ page }) => {
    await page.clock.install();
    const stream = new MockStream();
    await openRisk(page, stream);
    await expect(killSwitchPanel(page)).toHaveAttribute("data-freshness", "live");
    // Heartbeats keep coming, but no longer vouch for the kill switch.
    stream.heartbeatOmits = ["killswitch"];
    await page.waitForTimeout(600);
    await page.clock.fastForward(8_000);
    await expect(killSwitchPanel(page)).toHaveAttribute("data-freshness", "lagging");
    await expect(killSwitchPanel(page).getByTestId("state-lagging")).toContainText("LAG");
    await page.clock.fastForward(8_000);
    await expect(killSwitchPanel(page)).toHaveAttribute("data-freshness", "stale");
    await expect(page.getByTestId("kill-switch-status")).toHaveText("DISARMED");
    // The mode, still confirmed by every heartbeat, stays live.
    await expect(page.getByTestId("mode-banner")).toHaveAttribute("data-stale", "false");
  });

  test("the newest platform as-of is visible in the status bar", async ({ page }) => {
    const stream = new MockStream();
    await openRisk(page, stream);
    const asOf = page.getByTestId("status-as-of");
    await expect(asOf).toHaveAttribute("data-as-of", /\dT\d/);
    await expect(asOf).toContainText("data as of");
  });
});
