import { expect, test, type Page } from "@playwright/test";
import { API, killSwitchView, mockShell } from "./fixtures";
import { enableTestHooks, killSwitchEntity, MockStream, testHook } from "./sse";

/**
 * stream.spec (gate for Wave 2, report E §8.1): the live stream against the
 * mock SSE harness in tests/e2e/sse.ts.
 *
 * Snapshot then delta; sequence-gap resync; dedupe; backoff to DISCONNECTED
 * with manual reconnect and the REST fallback; two operators in two browser
 * contexts; one stream connection for two tabs; no optimistic UI.
 */

async function open(page: Page, stream: MockStream, path = "/trading/risk") {
  await mockShell(page);
  await stream.mirrorRest(page);
  await stream.install(page);
  await page.goto(path);
  await expect(page.getByTestId("status-stream")).toHaveAttribute("data-state", "live");
}

test.describe("stream", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "stream behaviour is viewport-independent");
  });

  test("snapshot then delta renders from the stream itself", async ({ page }) => {
    const stream = new MockStream();
    await open(page, stream);
    const status = page.getByTestId("kill-switch-status");
    await expect(status).toHaveAttribute("data-active", "false");
    await expect(page.getByTestId("status-stream")).toContainText("stream live");
    const modeReads = stream.reads("/api/system/execution-mode");

    // REST is unreachable from here on: what changes on screen came by stream.
    stream.restDown = true;
    stream.armKillSwitch("tom", "pause", "spread blowout on the open");
    await expect(status).toHaveAttribute("data-active", "true");
    await expect(page.getByTestId("kill-switch-panel")).toContainText("Armed by tom");
    await expect(page.getByTestId("mode-banner-killswitch")).toBeVisible();
    // The mode entity matches its REST shape, so it needed no re-read at all.
    expect(stream.reads("/api/system/execution-mode")).toBe(modeReads);
  });

  test("a sequence gap is not applied: the topic is re-read by REST", async ({ page }) => {
    await enableTestHooks(page);
    const stream = new MockStream();
    await open(page, stream);
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "false");
    const reads = stream.reads("/api/system/kill-switch");

    // The platform arms; that event is lost in transit, and the next one
    // arrives with a gap in the sequence.
    stream.entities.killswitch = {
      kill_switch: killSwitchEntity({
        active: true,
        mode: "flatten",
        operator: "tom",
        reason: "read back over REST",
      }),
    };
    stream.skip("killswitch", 1);
    stream.patch("killswitch", "kill_switch", { operator: "tom" });

    await expect.poll(() => testHook(page, "streamGaps")).toBeGreaterThanOrEqual(1);
    await expect.poll(() => stream.reads("/api/system/kill-switch")).toBeGreaterThan(reads);
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "true");
    await expect(page.getByTestId("kill-switch-panel")).toContainText("read back over REST");
  });

  test("a heartbeat ahead of the applied sequence triggers a resync", async ({ page }) => {
    await enableTestHooks(page);
    const stream = new MockStream();
    await open(page, stream);
    const reads = stream.reads("/api/system/kill-switch");
    stream.skip("killswitch", 3);
    stream.pushHeartbeat();
    await expect.poll(() => stream.reads("/api/system/kill-switch")).toBeGreaterThan(reads);
  });

  test("a replayed event id is applied once", async ({ page }) => {
    await enableTestHooks(page);
    const stream = new MockStream();
    await open(page, stream);
    const seq = stream.patch("killswitch", "kill_switch", { reason: "first" });
    const entity = stream.entities.killswitch?.kill_switch;
    stream.push("killswitch", "delta", { id: "kill_switch", entity }, { seq, id: `killswitch:${seq}` });
    await expect.poll(() => testHook(page, "streamDuplicates")).toBeGreaterThanOrEqual(1);
    expect(await testHook(page, "streamGaps")).toBe(0);
  });

  test("backoff 1, 2 s with jitter, DISCONNECTED after five failures, REST fallback, Reconnect", async ({
    page,
  }) => {
    await page.clock.install();
    const stream = new MockStream();
    await open(page, stream);
    const state = page.getByTestId("status-stream");
    const status = page.getByTestId("kill-switch-status");
    await expect(status).toHaveAttribute("data-active", "false");

    // Time is now moved only by the test.
    await page.clock.pauseAt(await page.evaluate(() => Date.now() + 100));
    stream.down = 503;
    await expect(state).toHaveAttribute("data-failures", "1");

    // First retry: 1 s +/- 20%, so not before 0.8 s and by 1.2 s.
    let before = stream.requests;
    await page.clock.fastForward(750);
    await page.waitForTimeout(250);
    expect(stream.requests).toBe(before);
    await page.clock.fastForward(500);
    await expect(state).toHaveAttribute("data-failures", "2");

    // Second retry: 2 s +/- 20%.
    before = stream.requests;
    await page.clock.fastForward(1_550);
    await page.waitForTimeout(250);
    expect(stream.requests).toBe(before);
    await page.clock.fastForward(900);
    await expect(state).toHaveAttribute("data-failures", "3");

    for (const failures of ["4", "5"]) {
      await page.clock.fastForward(16_000);
      await expect(state).toHaveAttribute("data-failures", failures);
    }
    await expect(state).toHaveAttribute("data-state", "disconnected");
    await expect(page.getByTestId("stream-disconnected")).toBeVisible();
    // The numbers remain.
    await expect(status).toHaveAttribute("data-active", "false");

    // REST fallback: stream-backed reads are polled every 10 s.
    const reads = stream.reads("/api/system/kill-switch");
    await page.clock.fastForward(10_500);
    await expect.poll(() => stream.reads("/api/system/kill-switch")).toBeGreaterThan(reads);

    // Manual reconnect, once the platform is back.
    stream.down = false;
    await page.getByTestId("stream-reconnect").click();
    await expect(state).toHaveAttribute("data-state", "live");
    await expect(page.getByTestId("stream-disconnected")).toHaveCount(0);
  });

  test("a stream that drops and comes back resumes with Last-Event-ID", async ({ page }) => {
    const stream = new MockStream();
    await mockShell(page);
    await stream.mirrorRest(page);
    const sub = await stream.install(page);
    await page.goto("/trading/risk");
    await expect(page.getByTestId("status-stream")).toHaveAttribute("data-state", "live");
    // Every body closes; the browser's own reconnect carries the last id.
    await expect.poll(() => sub.lastEventIds.length).toBeGreaterThan(0);
    expect(sub.lastEventIds.at(-1)).toMatch(/^[a-z]+:\d+$/);
  });

  test("Tom arms in his browser; Joe's banner and panel show it within 1 s", async ({ browser }) => {
    const stream = new MockStream();
    let postedAt = 0;
    const joeContext = await browser.newContext();
    const tomContext = await browser.newContext();
    const joe = await joeContext.newPage();
    const tom = await tomContext.newPage();
    for (const [page, who] of [
      [joe, "joe"],
      [tom, "tom"],
    ] as const) {
      await mockShell(page, { operator: { user_id: who, display_name: who } });
      await stream.mirrorRest(page);
      await stream.install(page);
    }
    await tom.route(`${API}/api/system/kill-switch/arm`, async (route) => {
      postedAt = Date.now();
      stream.armKillSwitch("tom", "pause", "spread blowout on the open");
      await route.fulfill({
        json: killSwitchView({ active: true, mode: "pause", operator: "tom", reason: "spread blowout on the open" }),
      });
    });

    await joe.goto("/trading/risk");
    await tom.goto("/trading/risk");
    for (const page of [joe, tom]) {
      await expect(page.getByTestId("status-stream")).toHaveAttribute("data-state", "live");
    }
    await expect(joe.getByTestId("mode-banner-killswitch-off")).toBeVisible();

    await tom.getByTestId("kill-switch-arm").click();
    await tom.getByTestId("confirm-choice-pause").click();
    await tom.getByTestId("confirm-reason").fill("spread blowout on the open");
    await tom.getByTestId("confirm-submit").click();

    await expect(joe.getByTestId("mode-banner-killswitch")).toBeVisible({ timeout: 1_000 });
    await expect(joe.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "true", {
      timeout: 1_000,
    });
    expect(Date.now() - postedAt).toBeLessThan(1_000);
    await expect(joe.getByTestId("kill-switch-panel")).toContainText("Armed by tom");

    // Tom's own dialog closed on the stream echo, well inside the 5 s limit.
    await expect(tom.getByTestId("confirm-dialog")).toHaveCount(0, { timeout: 3_000 });
    await expect(tom.getByTestId("kill-switch-confirming")).toHaveCount(0);
    await joeContext.close();
    await tomContext.close();
  });

  test("two tabs share one stream connection; the follower takes over", async ({ browser }) => {
    const stream = new MockStream();
    const context = await browser.newContext();
    await enableTestHooks(context);
    const first = await context.newPage();
    const second = await context.newPage();
    const subs = [];
    for (const page of [first, second]) {
      await mockShell(page);
      await stream.mirrorRest(page);
      subs.push(await stream.install(page));
    }

    await first.goto("/");
    await expect(first.getByTestId("status-stream")).toHaveAttribute("data-role", "leader");
    await expect(first.getByTestId("status-stream")).toHaveAttribute("data-state", "live");
    await second.goto("/trading/risk");
    const followerState = second.getByTestId("status-stream");
    await expect(followerState).toHaveAttribute("data-role", "follower");
    await expect(followerState).toHaveAttribute("data-state", "live");

    expect(await testHook(first, "streamConnections")).toBe(1);
    expect(await testHook(second, "streamConnections")).toBe(0);
    expect(subs[1]?.connections).toBe(0);

    // The follower receives events through the leader.
    stream.armKillSwitch("tom", "pause", "spread blowout on the open");
    await expect(second.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "true");

    // The leader closes: the follower takes the lock and connects.
    await first.close();
    await expect(followerState).toHaveAttribute("data-role", "leader");
    await expect(followerState).toHaveAttribute("data-state", "live");
    expect(await testHook(second, "streamConnections")).toBe(1);
    await context.close();
  });

  test("no optimistic UI: the dialog waits for the echo, then says confirming…", async ({ page }) => {
    const stream = new MockStream();
    await open(page, stream);
    // The platform accepts the arm, but its stream has not echoed it yet.
    await page.route(`${API}/api/system/kill-switch/arm`, (route) =>
      route.fulfill({ json: killSwitchView({ active: true, mode: "pause" }) }),
    );
    await page.getByTestId("kill-switch-arm").click();
    await page.getByTestId("confirm-choice-pause").click();
    await page.getByTestId("confirm-reason").fill("pausing ahead of the release");
    await page.getByTestId("confirm-submit").click();

    await expect(page.getByTestId("confirm-notice")).toContainText("Waiting for the platform");
    await expect(page.getByTestId("confirm-submit")).toHaveText("Working…");
    await page.waitForTimeout(2_000);
    await expect(page.getByTestId("confirm-dialog")).toBeVisible();
    // The panel still shows what the platform says, not what was requested.
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "false");

    // After 5 s the dialog closes and the panel says it is confirming.
    await expect(page.getByTestId("confirm-dialog")).toHaveCount(0, { timeout: 5_000 });
    await expect(page.getByTestId("kill-switch-confirming")).toBeVisible();
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "false");

    stream.armKillSwitch("joe", "pause", "pausing ahead of the release");
    await expect(page.getByTestId("kill-switch-status")).toHaveAttribute("data-active", "true");
    await expect(page.getByTestId("kill-switch-confirming")).toHaveCount(0);
  });
});
