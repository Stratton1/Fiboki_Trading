import { expect, test } from "@playwright/test";
import { mockShell } from "./fixtures";
import { mockMonitors, mockWorkspace, presetLayout } from "./workspace-fixtures";

/**
 * Wave 2A · Alert notifications (NTF, /alerts/notifications): what fired (the
 * backend's evaluator wrote the fixture), the server-side read marker, rule
 * CRUD and the opt-in browser notifications.
 */

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await mockShell(page);
  await mockMonitors(page);
});

test("notifications newest first with the API's unread count and evaluator state", async ({ page }) => {
  await page.goto("/alerts/notifications");
  await expect(page.getByTestId("ntf-item")).toHaveCount(3);
  await expect(page.getByTestId("ntf-unread")).toHaveText("2 UNREAD");
  await expect(page.getByTestId("ntf-evaluator")).toHaveAttribute("data-state", "live");
  await expect(page.getByTestId("ntf-item").first()).toContainText("crossed to below");
  await expect(page.locator('[data-testid="ntf-item"][data-read="true"]')).toHaveCount(1);
  await expect(page.getByTestId("ntf-rule")).toHaveCount(3);
  await expect(page.locator('[data-testid="ntf-rule"][data-state="fired"]')).toContainText("GBPUSD");
});

test("mark all read moves the server marker to the newest", async ({ page }) => {
  const mock = await mockMonitors(page);
  await page.goto("/alerts/notifications");
  await expect(page.getByTestId("ntf-unread")).toHaveText("2 UNREAD");
  await page.getByTestId("ntf-mark-read").click();
  await expect(page.getByTestId("ntf-unread")).toHaveText("0 UNREAD");
  const write = mock.alertWrites.find((w) => w.path === "read");
  expect(write?.body).toEqual({ through_seq: 3 });
});

test("a new rule is posted with its spec; delete and disable carry the version", async ({ page }) => {
  const mock = await mockMonitors(page);
  await page.goto("/alerts/notifications");
  await expect(page.getByTestId("ntf-rule")).toHaveCount(3);
  await page.getByTestId("ntf-new-instrument").fill("GBPUSD");
  await page.getByTestId("ntf-new-kind").selectOption("cross");
  await expect(page.getByTestId("ntf-new-add")).toBeDisabled();
  await page.getByTestId("ntf-new-level").fill("1.2750");
  await page.getByTestId("ntf-new-add").click();
  await expect.poll(() => mock.alertWrites.filter((w) => w.method === "POST" && w.path === "/api/alerts/rules").length).toBe(1);
  expect(mock.alertWrites[0]?.body).toMatchObject({ instrument: "GBPUSD", kind: "cross", level: 1.275, side: "mid" });
  await page.getByTestId("ntf-rule").first().getByTestId("ntf-rule-toggle").click();
  await expect.poll(() => mock.alertWrites.filter((w) => w.method === "PUT").length).toBe(1);
  expect(mock.alertWrites.find((w) => w.method === "PUT")?.body).toMatchObject({ base_version: 1, enabled: false });
  await page.getByTestId("ntf-rule").first().getByTestId("ntf-rule-delete").click();
  await expect.poll(() => mock.alertWrites.filter((w) => w.method === "DELETE").length).toBe(1);
  expect(mock.alertWrites.find((w) => w.method === "DELETE")?.path).toMatch(/^\/api\/alerts\/rules\/pa_/);
});

test("browser notifications are off until opted in, then announce only new unread alerts", async ({ page, context }) => {
  await context.grantPermissions(["notifications"]);
  await page.addInitScript(() => {
    const shown: string[] = [];
    (window as unknown as { __shown: string[] }).__shown = shown;
    class FakeNotification {
      static permission = "granted";
      static requestPermission = async () => "granted";
      constructor(title: string, opts?: { body?: string }) {
        shown.push(`${title}: ${opts?.body ?? ""}`);
      }
    }
    (window as unknown as { Notification: unknown }).Notification = FakeNotification;
  });
  await page.goto("/alerts/notifications");
  const toggle = page.getByTestId("ntf-browser");
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  // Opting in starts from now: the backlog is not replayed.
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => (window as unknown as { __shown: string[] }).__shown.length)).toBe(0);
  await page.evaluate(() => window.localStorage.setItem("fiboki.alerts.browser.seen", "1"));
  await page.reload();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __shown: string[] }).__shown.length)).toBe(2);
});

test("NTF on the command line, and the alerts tile in the Incident preset", async ({ page }) => {
  await mockWorkspace(page, { saved: { Ops: presetLayout("incident") } });
  await page.goto("/");
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
  await page.getByTestId("command-line-input").fill("NTF");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/alerts\/notifications$/, { timeout: 20_000 });
  await page.goto("/workspace?layout=Ops");
  const tile = page.locator('[data-testid="ws-tile"][data-kind="alerts"]');
  await expect(tile.getByTestId("ntf-item")).toHaveCount(3);
  await expect(tile.getByTestId("ntf-rules")).toHaveCount(0);
});
