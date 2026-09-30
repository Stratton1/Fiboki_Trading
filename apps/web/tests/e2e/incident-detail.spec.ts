import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Route } from "@playwright/test";
import { API, incident, incidentEnvelope, liveBanner, mockShell, source } from "./fixtures";

/**
 * /system/incidents/<id> (inventory F-1): the incident the backend's deep
 * links point at. The page reads GET /api/system/incidents/{id}, offers the
 * shared Acknowledge (same dialog and friction as Command) and the Note
 * action (POST /api/system/incidents/{id}/note, unused before this page),
 * lists the timeline oldest first, and links back to Command and the source.
 */

const PAGE = "/system/incidents/inc-1";

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "entity page, desktop project");
});

test("the header, the occurrences with provenance, and the timeline in the platform's order", async ({ page }) => {
  await mockShell(page);
  await page.goto(PAGE);
  const detail = page.getByTestId("incident-detail");
  await expect(detail).toHaveAttribute("data-incident-id", "inc-1");
  await expect(page.locator("main h1")).toHaveText("Incident");
  await expect(page.getByTestId("incident-title")).toHaveText("Worker heartbeat late");
  await expect(page.getByTestId("incident-status")).toHaveText("OPEN");
  await expect(page.getByTestId("incident-occurrences").getByTestId("provenance-chip")).toHaveAttribute(
    "data-provenance",
    "paper",
  );
  await expect(detail.getByTestId("source-note")).toContainText("Incident read model.");
  const entries = page.getByTestId("incident-timeline-entry");
  await expect(entries).toHaveCount(2);
  await expect(entries.first()).toContainText("no heartbeat for 95 s");
  await expect(entries.first()).toContainText("correlation cid-occ-1");
  await expect(entries.nth(1)).toContainText("no heartbeat for 180 s");
  expect(await entries.evaluateAll((els) => els.map((el) => el.getAttribute("data-kind")))).toEqual([
    "occurrence",
    "occurrence",
  ]);
});

test("links back to Command, and to the source's screen where one exists", async ({ page }) => {
  await mockShell(page);
  await page.route(`${API}/api/system/incidents/inc-1`, (route: Route) =>
    route.fulfill({ json: incidentEnvelope({ event: "heartbeat_stale" }) }),
  );
  await page.goto(PAGE);
  await expect(page.getByTestId("incident-source-link")).toHaveAttribute("href", "/system/workers");
  await page.getByTestId("incident-back-command").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator("main h1")).toHaveText("Command");
});

test("an event no screen shows says so rather than linking somewhere wrong", async ({ page }) => {
  await mockShell(page);
  await page.goto(PAGE);
  await expect(page.getByTestId("incident-source-link")).toHaveCount(0);
  await expect(page.getByTestId("incident-source-none")).toContainText("worker.heartbeat_late");
});

test("an unknown incident is the API's 404, named, never a blank page", async ({ page }) => {
  await mockShell(page);
  await page.route(`${API}/api/system/incidents/inc-gone`, (route: Route) =>
    route.fulfill({
      status: 404,
      json: {
        code: "incident_not_found",
        detail: "No incident 'inc-gone' is derivable from the alert log or the kill-switch journal.",
        correlation_id: "cid-404",
        context: {},
      },
    }),
  );
  const response = await page.goto("/system/incidents/inc-gone");
  expect(response?.status()).toBe(200);
  await expect(page.getByTestId("state-error")).toContainText("incident_not_found");
  await expect(page.getByTestId("state-error")).toContainText("cid-404");
});

test("acknowledge: the shared dialog, the reason posted with CSRF, and the platform's echo", async ({
  page,
  context,
}) => {
  await context.addCookies([{ name: "fiboki_csrf", value: "csrf-token-7", url: "http://127.0.0.1:3100" }]);
  await mockShell(page);
  let posted: { body: unknown; csrf: string | null } | null = null;
  await page.route(`${API}/api/system/incidents/inc-1`, (route: Route) =>
    route.fulfill({
      json: posted
        ? incidentEnvelope({
            status: "acknowledged",
            acknowledged_by: "joe",
            acknowledged_at: "2026-09-19T12:05:00Z",
          })
        : incidentEnvelope(),
    }),
  );
  await page.route(`${API}/api/system/incidents/inc-1/ack`, async (route: Route) => {
    posted = { body: route.request().postDataJSON(), csrf: await route.request().headerValue("x-fiboki-csrf") };
    await route.fulfill({ json: { data: incident({ status: "acknowledged" }), source: source("live"), caveats: [] } });
  });
  await page.goto(PAGE);
  await page.getByTestId("incident-detail-ack").click();
  await expect(page.getByTestId("confirm-mode")).toContainText("PAPER");
  await page.getByTestId("confirm-reason").fill("restarted the worker by hand");
  await page.getByTestId("confirm-submit").click();
  await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
  expect(posted).toEqual({ body: { reason: "restarted the worker by hand" }, csrf: "csrf-token-7" });
  await expect(page.getByTestId("incident-status")).toHaveText("ACKNOWLEDGED");
  await expect(page.getByTestId("incident-acknowledged")).toContainText("by joe");
  await expect(page.getByTestId("incident-detail-ack")).toHaveCount(0);
});

test("in LIVE the incident page's acknowledge asks for REAL MONEY, as Command's does", async ({ page }) => {
  await mockShell(page);
  await page.route(`${API}/api/system/execution-mode`, (route: Route) => route.fulfill({ json: liveBanner() }));
  await page.goto(PAGE);
  await page.getByTestId("incident-detail-ack").click();
  await expect(page.getByTestId("confirm-mode")).toContainText("LIVE");
  await expect(page.getByTestId("confirm-phrase")).toHaveAttribute("data-phrase", "REAL MONEY");
});

test("a note is posted as {text}, and shown once the platform's timeline carries it", async ({ page }) => {
  await mockShell(page);
  const notes: unknown[] = [];
  await page.route(`${API}/api/system/incidents/inc-1`, (route: Route) =>
    route.fulfill({
      json: incidentEnvelope(
        notes.length === 0
          ? {}
          : {
              timeline: [
                ...incidentEnvelope().data.timeline,
                {
                  at: "2026-09-19T12:10:00Z",
                  kind: "note",
                  severity: null,
                  actor: "joe",
                  text: "Paged the host; disk full on /var.",
                  correlation_id: "cid-note",
                },
              ],
            },
      ),
    }),
  );
  await page.route(`${API}/api/system/incidents/inc-1/note`, async (route: Route) => {
    notes.push(route.request().postDataJSON());
    await route.fulfill({ json: incidentEnvelope() });
  });
  await page.goto(PAGE);
  await expect(page.getByTestId("incident-note-mode")).toHaveText("PAPER");
  await expect(page.getByTestId("incident-note-submit")).toBeDisabled();
  await page.getByTestId("incident-note-text").fill("  Paged the host; disk full on /var.  ");
  await page.getByTestId("incident-note-submit").click();
  const note = page.locator('[data-testid="incident-timeline-entry"][data-kind="note"]');
  await expect(note).toContainText("Paged the host; disk full on /var.");
  await expect(note).toContainText("by joe");
  expect(notes).toEqual([{ text: "Paged the host; disk full on /var." }]);
  await expect(page.getByTestId("incident-note-text")).toHaveValue("");
  // A note never changes the status.
  await expect(page.getByTestId("incident-status")).toHaveText("OPEN");
});

test("a refused note keeps the text and shows the platform's reason", async ({ page }) => {
  await mockShell(page);
  await page.route(`${API}/api/system/incidents/inc-1/note`, (route: Route) =>
    route.fulfill({
      status: 403,
      json: { code: "insufficient_role", detail: "This action requires the admin role.", correlation_id: "c", context: {} },
    }),
  );
  await page.goto(PAGE);
  await page.getByTestId("incident-note-text").fill("trying a note");
  await page.getByTestId("incident-note-submit").click();
  await expect(page.getByTestId("incident-note-error")).toContainText("requires the admin role");
  await expect(page.getByTestId("incident-note-text")).toHaveValue("trying a note");
});

test("a role that cannot acknowledge or annotate sees both disabled, with the reason in words", async ({ page }) => {
  await mockShell(page, {
    operator: { role: "operator", display_name: "Tom", can_arm_kill_switch: true, can_promote: false },
  });
  await page.goto(PAGE);
  await expect(page.getByTestId("incident-detail-ack")).toBeDisabled();
  await expect(page.getByTestId("ack-blocked-incident-detail-ack")).toHaveText("Blocked: admin role required.");
  await expect(page.getByTestId("incident-note-submit")).toBeDisabled();
  await expect(page.getByTestId("incident-note-status")).toContainText("Tom (operator)");
});

test("axe: zero serious on the incident page, dark and light", async ({ page }) => {
  await mockShell(page);
  for (const theme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await page.goto(PAGE);
    await expect(page.getByTestId("incident-detail")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const blocking = results.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => ({ id: v.id, targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")) }));
    expect(blocking, theme).toEqual([]);
  }
});
