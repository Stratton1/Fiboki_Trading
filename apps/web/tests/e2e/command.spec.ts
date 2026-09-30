import { expect, test, type Route } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { API, attentionItem, attentionPage, figure, incident, incidentsPage, mockShell, riskState, source } from "./fixtures";

/**
 * Command screen v1 (Wave 2): the server-ranked attention queue as the top
 * panel of the Overview, with keyboard access, and incidents with an audited
 * acknowledge through the one ConfirmDialog.
 */

const BASE = `http://127.0.0.1:${process.env.PORT ?? 3100}`;

/** Scores deliberately out of order: the UI must not "fix" the server's ranking. */
const item = (id: string, score: number, severity: string, link: string) =>
  attentionItem(id, severity, link, { score: { ...attentionItem(id, severity, link).score, value: score } });

test.describe("attention queue", () => {
  test("is the top panel and keeps the server's order exactly", async ({ page }) => {
    await mockShell(page);
    // Deliberately in neither score nor severity order: the UI must not "fix" it.
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({
        json: attentionPage([
          item("c", 200, "info", "/research"),
          item("a", 1090, "critical", "/trading/risk"),
          item("b", 470, "warning", "/system/services"),
        ]),
      }),
    );
    await page.goto("/");
    const cards = page.locator("main .card__title");
    await expect(cards.first()).toHaveText("Needs attention");
    const items = page.getByTestId("attention-item");
    await expect(items).toHaveCount(3);
    expect(await items.evaluateAll((els) => els.map((el) => el.getAttribute("data-item-id")))).toEqual([
      "c",
      "a",
      "b",
    ]);
    // The position shown is the place in the server's order; the score is
    // the server's, with its provenance chip.
    await expect(items.nth(0)).toContainText("#1");
    await expect(items.nth(0)).toContainText("INFO");
    await expect(items.nth(1)).toContainText("CRITICAL");
    await expect(items.nth(1).getByTestId("figure")).toBeVisible();
  });

  test("Enter opens the focused item's deep link; arrows and j/k move between items", async ({
    page,
  }) => {
    await mockShell(page);
    await page.goto("/");
    const first = page.getByTestId("attention-link-kill_switch:armed");
    const second = page.getByTestId("attention-link-strategy_review:abc:candidate");
    await first.focus();
    await page.keyboard.press("ArrowDown");
    await expect(second).toBeFocused();
    await page.keyboard.press("k");
    await expect(first).toBeFocused();
    await page.keyboard.press("j");
    await expect(second).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`${BASE}/trading/candidates`);
  });

  test("the plan's section links the backend emits reach today's screens", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([item("k", 1090, "critical", "/risk")]) }),
    );
    await page.goto("/");
    await page.getByTestId("attention-link-k").focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(`${BASE}/trading/risk`);
  });

  test("a deep link that is not an in-app path is shown but never followed", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({
        json: attentionPage([
          item("x", 400, "warning", "https://evil.example/x"),
          item("y", 200, "info", "//evil.example/y"),
        ]),
      }),
    );
    await page.goto("/");
    await expect(page.getByTestId("attention-nolink-x")).toBeVisible();
    await expect(page.getByTestId("attention-nolink-y")).toBeVisible();
    await expect(page.locator('main a[href*="evil.example"]')).toHaveCount(0);
  });

  test("an empty queue is an answer; a failed one is an error", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([]) }),
    );
    await page.goto("/");
    await expect(page.getByText("Nothing needs you")).toBeVisible();

    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({
        status: 404,
        json: { code: "not_found", detail: "No attention queue here.", correlation_id: "cid-att", context: {} },
      }),
    );
    await page.reload();
    await expect(page.getByText("Could not load the attention queue")).toBeVisible();
    await expect(page.getByText("Nothing needs you")).toHaveCount(0);
  });
});

test.describe("incidents", () => {
  test("acknowledging needs a reason, posts it, and shows the platform's answer", async ({ page }) => {
    await mockShell(page);
    let acknowledged: Record<string, unknown> | null = null;
    await page.route(`${API}/api/system/incidents`, (route: Route) =>
      route.fulfill({
        json: incidentsPage([
          acknowledged
            ? incident({
                status: "acknowledged",
                acknowledged_by: "joe",
                acknowledged_at: "2026-09-19T12:05:00Z",
                timeline: [
                  {
                    at: "2026-09-19T12:05:00Z",
                    kind: "ack",
                    severity: null,
                    actor: "joe",
                    text: String(acknowledged.reason),
                    correlation_id: "cid-ack",
                  },
                ],
              })
            : incident(),
        ]),
      }),
    );
    await page.route(`${API}/api/system/incidents/inc-1/ack`, async (route: Route) => {
      acknowledged = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({ json: { data: incident({ status: "acknowledged" }), source: {}, caveats: [] } });
    });

    await page.goto("/");
    const row = page.locator('[data-testid="incident-row"][data-incident-id="inc-1"]');
    await expect(row).toHaveAttribute("data-status", "open");
    await page.getByTestId("incident-ack-inc-1").click();
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toContainText("Worker heartbeat late");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-reason").fill("short");
    await expect(page.getByTestId("confirm-submit")).toBeDisabled();
    await page.getByTestId("confirm-reason").fill("seen; worker restarted by hand");
    await page.getByTestId("confirm-submit").click();

    await expect(dialog).toHaveCount(0);
    expect(acknowledged).toEqual({ reason: "seen; worker restarted by hand" });
    await expect(row).toHaveAttribute("data-status", "acknowledged");
    await expect(row).toContainText("by joe");
    await expect(row).toContainText("seen; worker restarted by hand");
  });

  test("a refused acknowledgement shows why and changes nothing", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/system/incidents/inc-1/ack`, (route: Route) =>
      route.fulfill({
        status: 403,
        json: {
          code: "insufficient_role",
          detail: "This action requires the admin role.",
          correlation_id: "cid-ack",
          context: {},
        },
      }),
    );
    await page.goto("/");
    await page.getByTestId("incident-ack-inc-1").click();
    await page.getByTestId("confirm-reason").fill("trying without the role");
    await page.getByTestId("confirm-submit").click();
    await expect(page.getByTestId("confirm-error")).toContainText("requires the admin role");
    await expect(
      page.locator('[data-testid="incident-row"][data-incident-id="inc-1"]'),
    ).toHaveAttribute("data-status", "open");
  });
});

// ------------------------------------------------------------ Command v2 (Wave 4a)

const incidentItem = () =>
  attentionItem("incident:inc-1", "warning", "/system/incidents/inc-1", {
    category: "incident",
    title: "Worker heartbeat late",
    reason: "worker.heartbeat_late: 3 occurrence(s); not acknowledged.",
  });

test.describe("triage rows", () => {
  test("each row has a severity glyph, its age, the provenance it came from and its deep link", async ({ page }) => {
    await mockShell(page);
    await page.goto("/");
    const first = page.getByTestId("attention-item").first();
    await expect(first.locator(".triage__glyph")).toHaveText("◆");
    await expect(first.locator(".triage__glyph")).toHaveAttribute("data-severity", "critical");
    await expect(first.getByTestId("attention-age")).toHaveText(/^\d+[smhd] old$/);
    await expect(first.getByTestId("attention-mode").getByTestId("provenance-chip")).toHaveAttribute(
      "data-provenance",
      "paper",
    );
    await expect(first.getByTestId("attention-link-kill_switch:armed")).toHaveAttribute("href", "/trading/risk");
    // Only an incident can be acknowledged; nothing else on a row mutates.
    await expect(page.locator('[data-testid^="attention-ack-"]')).toHaveCount(0);
  });

  test("an item with no as-of says its age is unknown rather than inventing one", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([attentionItem("h", "warning", "/system", { as_of: null })]) }),
    );
    await page.goto("/");
    await expect(page.getByTestId("attention-age")).toHaveText("age unknown: no as-of");
  });

  test("acknowledge posts the reason with the CSRF header, then shows the platform's queue", async ({
    page,
    context,
  }) => {
    await context.addCookies([{ name: "fiboki_csrf", value: "csrf-token-42", url: "http://127.0.0.1:3100" }]);
    await mockShell(page);
    let acknowledged: { body: unknown; csrf: string | undefined } | null = null;
    let queueReads = 0;
    await page.route(`${API}/api/command/attention`, (route: Route) => {
      queueReads += 1;
      return route.fulfill({
        json: attentionPage(
          acknowledged
            ? [attentionItem("strategy_review:abc:candidate", "info", "/trading/candidates")]
            : [incidentItem(), attentionItem("strategy_review:abc:candidate", "info", "/trading/candidates")],
        ),
      });
    });
    await page.route(`${API}/api/system/incidents`, (route: Route) =>
      route.fulfill({
        json: incidentsPage([
          acknowledged ? incident({ status: "acknowledged", acknowledged_by: "joe" }) : incident(),
        ]),
      }),
    );
    await page.route(`${API}/api/system/incidents/inc-1/ack`, async (route: Route) => {
      const request = route.request();
      acknowledged = { body: request.postDataJSON(), csrf: await request.headerValue("x-fiboki-csrf") ?? undefined };
      await route.fulfill({
        json: { data: incident({ status: "acknowledged", acknowledged_by: "joe" }), source: source("live"), caveats: [] },
      });
    });

    await page.goto("/");
    const row = page.locator('[data-testid="attention-item"][data-item-id="incident:inc-1"]');
    await expect(row).toBeVisible();
    await page.getByTestId("attention-ack-incident:inc-1").click();
    const reason = page.getByTestId("attention-ack-reason-incident:inc-1");
    const submit = page.getByTestId("attention-ack-submit-incident:inc-1");
    await reason.fill("short");
    await expect(submit).toBeDisabled();
    // j and k typed into the reason are text, not row navigation.
    await reason.fill("joked; kicked the worker by hand");
    await expect(reason).toHaveValue("joked; kicked the worker by hand");
    await expect(submit).toBeEnabled();
    const readsBefore = queueReads;
    await submit.click();

    await expect.poll(() => acknowledged).not.toBeNull();
    expect(acknowledged!.body).toEqual({ reason: "joked; kicked the worker by hand" });
    expect(acknowledged!.csrf).toBe("csrf-token-42");
    // The row re-renders from the platform: the queue is re-read and no
    // longer lists the incident; nothing was removed on the client's say-so.
    await expect(row).toHaveCount(0);
    expect(queueReads).toBeGreaterThan(readsBefore);
    await expect(page.getByTestId("attention-item")).toHaveCount(1);
    await expect(
      page.locator('[data-testid="incident-row"][data-incident-id="inc-1"]'),
    ).toHaveAttribute("data-status", "acknowledged");
  });

  test("a refused acknowledgement keeps the row, the reason and says why", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([incidentItem()]) }),
    );
    await page.route(`${API}/api/system/incidents/inc-1/ack`, (route: Route) =>
      route.fulfill({
        status: 409,
        json: {
          code: "incident_already_acknowledged",
          detail: "Incident inc-1 was already acknowledged by tom.",
          correlation_id: "cid-ack",
          context: {},
        },
      }),
    );
    await page.goto("/");
    await page.getByTestId("attention-ack-incident:inc-1").click();
    await page.getByTestId("attention-ack-reason-incident:inc-1").fill("seen it, restarting");
    await page.getByTestId("attention-ack-submit-incident:inc-1").click();
    await expect(page.getByTestId("attention-ack-error-incident:inc-1")).toContainText("already acknowledged by tom");
    await expect(page.getByTestId("attention-ack-reason-incident:inc-1")).toHaveValue("seen it, restarting");
    await expect(page.locator('[data-testid="attention-item"][data-item-id="incident:inc-1"]')).toBeVisible();
  });

  test("a role that cannot acknowledge sees the action disabled with the reason", async ({ page }) => {
    await mockShell(page, {
      operator: { role: "operator", display_name: "Tom", can_arm_kill_switch: true, can_promote: false },
    });
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([incidentItem()]) }),
    );
    await page.goto("/");
    await expect(page.getByTestId("attention-ack-incident:inc-1")).toBeDisabled();
    await expect(page.getByTestId("attention-role-blocked")).toContainText("Tom (operator)");
  });

  test("the empty queue says so plainly", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) => route.fulfill({ json: attentionPage([]) }));
    await page.goto("/");
    const empty = page.getByTestId("state-empty").filter({ hasText: "Nothing needs you" });
    await expect(empty).toBeVisible();
    await expect(empty).toContainText("no open incident");
  });
});

test.describe("first numbers and fleet", () => {
  test("open risk and the throttle are NOT REPORTED; today's P&L carries its provenance", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/trading/risk`, (route: Route) =>
      route.fulfill({
        json: { data: riskState({ daily_loss_pct: figure(-0.42, "paper", "pct") }), source: source("live"), caveats: [] },
      }),
    );
    await page.goto("/");
    const tile = (label: string) => page.locator(`[data-testid="tile"][data-label="${label}"]`);
    await expect(tile("Open risk")).toHaveAttribute("data-state", "absent");
    await expect(tile("Open risk").getByTestId("view-state-absent")).toHaveText("⊘NOT REPORTED");
    await expect(tile("Today's P&L").getByTestId("figure-value")).toHaveText("▼−0.42%");
    await expect(tile("Today's P&L").getByTestId("provenance-chip")).toHaveAttribute("data-provenance", "paper");
    await expect(tile("Drawdown throttle")).toHaveAttribute("data-state", "absent");
    await expect(tile("Drawdown throttle")).toContainText("1.10%");
  });

  test("the fleet strip shows the worker, the paper session, and what is not reported", async ({ page }) => {
    await mockShell(page);
    await page.goto("/");
    await expect(page.getByTestId("fleet-worker")).toHaveAttribute("data-tone", /ok|warn|critical|unknown/);
    await expect(page.getByTestId("health-heartbeat")).toHaveText("12s ago");
    // The mocked report has no paper_journal check: no session, said as such.
    await expect(page.getByTestId("fleet-paper")).toContainText("NONE");
    await expect(page.getByTestId("fleet-news")).toHaveAttribute("data-tone", "absent");
    await expect(page.getByTestId("fleet-news")).toContainText("NOT REPORTED");
    await expect(page.getByTestId("fleet-model")).toContainText("NOT REPORTED");
  });

  test("axe: zero serious with the acknowledge form open", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "one audit, on the desktop project");
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([incidentItem(), attentionItem("x", "critical", "/risk")]) }),
    );
    await page.route(`${API}/api/trading/risk`, (route: Route) =>
      route.fulfill({ json: { data: riskState(), source: source("live"), caveats: [] } }),
    );
    await page.goto("/");
    await page.getByTestId("attention-ack-incident:inc-1").click();
    await expect(page.getByTestId("attention-ack-reason-incident:inc-1")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const blocking = results.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => ({ id: v.id, targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")) }));
    expect(blocking).toEqual([]);
  });

  test("at 390 px the triage rows and the strip fit without sideways scroll", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([incidentItem()]) }),
    );
    await page.goto("/");
    await expect(page.getByTestId("attention-ack-incident:inc-1")).toBeVisible();
    await page.getByTestId("attention-ack-incident:inc-1").click();
    await expect(page.getByTestId("attention-ack-reason-incident:inc-1")).toBeInViewport();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
