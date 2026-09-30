import { expect, test, type Route } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  API,
  attentionItem,
  attentionPage,
  figure,
  incident,
  incidentsPage,
  killSwitchEvent,
  killSwitchHistory,
  liveBanner,
  mockShell,
  riskState,
  source,
} from "./fixtures";

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
    // The shared confirm dialog (inventory F-3): the same one the Incidents
    // table uses, stating the execution mode.
    const dialog = page.getByTestId("confirm-dialog");
    await expect(dialog).toContainText("Worker heartbeat late");
    await expect(page.getByTestId("confirm-mode")).toContainText("PAPER");
    const reason = page.getByTestId("confirm-reason");
    const submit = page.getByTestId("confirm-submit");
    await reason.fill("short");
    await expect(submit).toBeDisabled();
    // j and k typed into the reason are text, not row navigation.
    await reason.fill("joked; kicked the worker by hand");
    await expect(reason).toHaveValue("joked; kicked the worker by hand");
    await expect(submit).toBeEnabled();
    const readsBefore = queueReads;
    await submit.click();

    await expect.poll(() => acknowledged).not.toBeNull();
    await expect(dialog).toHaveCount(0);
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
    await page.getByTestId("confirm-reason").fill("seen it, restarting");
    await page.getByTestId("confirm-submit").click();
    await expect(page.getByTestId("confirm-error")).toContainText("already acknowledged by tom");
    await expect(page.getByTestId("confirm-reason")).toHaveValue("seen it, restarting");
    await expect(page.getByTestId("confirm-dialog")).toBeVisible();
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
    // Why, beside the control itself, not only in a hover title (inventory F-4).
    const why = page.getByTestId("ack-blocked-attention-ack-incident:inc-1");
    await expect(why).toBeVisible();
    await expect(why).toHaveText("Blocked: admin role required.");
    await expect(page.getByTestId("attention-ack-incident:inc-1")).toHaveAttribute(
      "aria-describedby",
      (await why.getAttribute("id")) ?? "missing",
    );
    await expect(page.getByTestId("ack-blocked-incident-ack-inc-1")).toHaveText("Blocked: admin role required.");
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
  test("the loss limits are the Risk page's bars, with provenance; open risk and the throttle are NOT REPORTED", async ({
    page,
  }) => {
    await mockShell(page);
    await page.route(`${API}/api/trading/risk`, (route: Route) =>
      route.fulfill({
        json: { data: riskState({ daily_loss_pct: figure(-0.42, "paper", "pct") }), source: source("live"), caveats: [] },
      }),
    );
    await page.goto("/");
    // V-1: the three stat tiles are gone; the limit board's own rows are here.
    await expect(page.locator('[data-testid="tile"][data-label="Open risk"]')).toHaveCount(0);
    const limits = page.getByTestId("command-limits");
    const rows = limits.getByTestId("limit-row");
    await expect(rows).toHaveCount(2);
    const daily = limits.locator('[data-testid="limit-row"][data-key="risk:daily_loss"]');
    const drawdown = limits.locator('[data-testid="limit-row"][data-key="risk:drawdown"]');
    // 0.42 of a 2% limit = 21.0%; 1.1 of 10% = 11.0%; both derived here (†).
    await expect(daily).toHaveAttribute("data-utilisation", "21.0");
    await expect(daily).toHaveAttribute("data-derived", "true");
    await expect(daily.getByTestId("limit-value")).toHaveText("−0.42%");
    await expect(daily.getByTestId("provenance-chip")).toHaveAttribute("data-provenance", "paper");
    await expect(daily.getByTestId("limit-bar")).toHaveAttribute("data-length", "21.0");
    await expect(drawdown).toHaveAttribute("data-utilisation", "11.0");
    await expect(drawdown.getByTestId("limit-value")).toContainText("1.10%");
    await expect(drawdown.getByTestId("provenance-chip")).toHaveAttribute("data-provenance", "paper");
    await expect(limits.getByTestId("limit-derived-note")).toContainText("daily loss used = the day's loss ÷ the limit");
    // The same provenance colour rule as on Risk: an OK bar takes its figure's ink.
    await expect(daily.locator(".limit-bar__fill")).toHaveAttribute("data-ink", "paper");
    const absent = page.getByTestId("command-absent");
    await expect(absent.getByTestId("view-state-absent")).toHaveText("⊘NOT REPORTED");
    await expect(absent).toContainText("Open risk");
    await expect(absent).toContainText("drawdown throttle");
  });

  test("a breached daily loss is BREACHED on Command exactly as on Risk", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/trading/risk`, (route: Route) =>
      route.fulfill({
        json: { data: riskState({ daily_loss_pct: figure(-2.5, "paper", "pct") }), source: source("live"), caveats: [] },
      }),
    );
    await page.goto("/");
    const daily = page.locator('[data-testid="command-limits"] [data-testid="limit-row"][data-key="risk:daily_loss"]');
    await expect(daily).toHaveAttribute("data-state", "breached");
    await expect(daily.getByTestId("limit-state")).toHaveText("✕BREACHED");
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

  test("axe: zero serious with the acknowledge dialog open", async ({ page }, testInfo) => {
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
    await expect(page.getByTestId("confirm-reason")).toBeVisible();
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
    await expect(page.getByTestId("confirm-reason")).toBeInViewport();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});

// ------------------------------------------- inventory fixes (2026-09-30)

test.describe("one acknowledge, one friction (inventory F-3)", () => {
  test("in LIVE, the attention row and the Incidents table ask for the same mode statement and REAL MONEY", async ({
    page,
  }) => {
    await mockShell(page);
    await page.route(`${API}/api/system/execution-mode`, (route: Route) => route.fulfill({ json: liveBanner() }));
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([incidentItem()]) }),
    );
    await page.goto("/");
    const friction: Record<string, string | null>[] = [];
    for (const trigger of ["attention-ack-incident:inc-1", "incident-ack-inc-1"]) {
      await page.getByTestId(trigger).click();
      const dialog = page.getByTestId("confirm-dialog");
      await expect(dialog).toBeVisible();
      await expect(page.getByTestId("confirm-mode")).toContainText("LIVE");
      await page.getByTestId("confirm-reason").fill("seen; paging the on-call operator");
      // A reason alone is not enough in LIVE: the phrase is required.
      await expect(page.getByTestId("confirm-submit")).toBeDisabled();
      friction.push({
        trigger,
        mode: await dialog.getAttribute("data-mode"),
        phrase: await page.getByTestId("confirm-phrase").getAttribute("data-phrase"),
        title: await dialog.locator("h2").first().textContent(),
      });
      await page.getByTestId("confirm-phrase").fill("REAL MONEY");
      await expect(page.getByTestId("confirm-submit")).toBeEnabled();
      await page.getByTestId("confirm-cancel").click();
      await expect(dialog).toHaveCount(0);
    }
    expect(friction[0]).toEqual({ ...friction[1], trigger: "attention-ack-incident:inc-1" });
    expect(friction[0]?.phrase).toBe("REAL MONEY");
    expect(friction[0]?.mode).toBe("live");
  });

  test("with the mode unknown, both acknowledge controls are disabled and say why beside them", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/system/execution-mode`, (route: Route) =>
      route.fulfill({
        status: 503,
        json: { code: "unavailable", detail: "down", correlation_id: "cid-mode", context: {} },
      }),
    );
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([incidentItem()]) }),
    );
    await page.goto("/");
    for (const trigger of ["attention-ack-incident:inc-1", "incident-ack-inc-1"]) {
      await expect(page.getByTestId(trigger)).toBeDisabled();
      await expect(page.getByTestId(`ack-blocked-${trigger}`)).toHaveText(
        "Blocked: execution mode unknown or disconnected.",
      );
    }
  });
});

test.describe("incident deep links (inventory F-1)", () => {
  test("Command prefetches and opens the real incident page: no 404, no console error", async ({ page }) => {
    const errors: string[] = [];
    const notFound: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    page.on("response", (res) => {
      if (res.url().includes("/system/incidents/") && res.status() >= 400) notFound.push(`${res.status()} ${res.url()}`);
    });
    await mockShell(page);
    await page.route(`${API}/api/command/attention`, (route: Route) =>
      route.fulfill({ json: attentionPage([incidentItem()]) }),
    );
    const prefetched = page.waitForRequest((req) => new URL(req.url()).pathname === "/system/incidents/inc-1");
    await page.goto("/");
    await prefetched;
    const link = page.getByTestId("attention-link-incident:inc-1");
    await expect(link).toHaveAttribute("href", "/system/incidents/inc-1");
    await link.click();
    await expect(page).toHaveURL(`${BASE}/system/incidents/inc-1`);
    await expect(page.locator("main h1")).toHaveText("Incident");
    await expect(page.getByTestId("incident-title")).toHaveText("Worker heartbeat late");
    expect(notFound).toEqual([]);
    // The unmocked stream and risk reads log connection errors of their own;
    // what F-1 produced was a 404 for the incident route.
    expect(errors.filter((text) => /404|system\/incidents/.test(text))).toEqual([]);
  });

  test("an incident title in the Incidents table opens its page", async ({ page }) => {
    await mockShell(page);
    await page.goto("/");
    await page.getByTestId("incident-link-inc-1").click();
    await expect(page).toHaveURL(`${BASE}/system/incidents/inc-1`);
    await expect(page.getByTestId("incident-detail")).toHaveAttribute("data-incident-id", "inc-1");
  });
});

test.describe("kill-switch timeline on Command (V-2)", () => {
  test("arms, lifts and incidents on one UTC axis, with the operator and reason on hover and in View data", async ({
    page,
  }) => {
    await mockShell(page);
    await page.route(`${API}/api/system/kill-switch/history*`, (route: Route) =>
      route.fulfill({
        json: killSwitchHistory([
          killSwitchEvent(2, "activate", "flatten", "tom", "gap through the stops on USDJPY"),
          killSwitchEvent(30, "deactivate", null, "tom", "spreads normal again after the release"),
          killSwitchEvent(52, "activate", "pause", "joe", "NFP in ten minutes; pausing new risk"),
          // Older than the window: counted, not drawn.
          killSwitchEvent(24 * 45, "activate", "pause", "joe", "an old halt"),
        ]),
      }),
    );
    await page.route(`${API}/api/system/incidents`, (route: Route) =>
      route.fulfill({
        json: incidentsPage([
          incident({ first_seen: new Date(Date.now() - 5 * 3_600_000).toISOString(), severity: "error" }),
        ]),
      }),
    );
    await page.goto("/");
    const timeline = page.getByTestId("ks-timeline");
    await expect(timeline).toHaveAttribute("data-events", "3");
    await expect(timeline).toHaveAttribute("data-incidents", "1");
    const events = timeline.getByTestId("ks-event");
    expect(await events.evaluateAll((els) => els.map((el) => el.getAttribute("data-kind")))).toEqual([
      "pause",
      "lift",
      "flatten",
    ]);
    // Hover text: who, when and why (SVG <title>).
    await expect(events.first().locator("title")).toContainText("PAUSE armed by joe");
    await expect(events.first().locator("title")).toContainText("NFP in ten minutes; pausing new risk");
    // PAUSE → lift is a closed halted span; the FLATTEN 2 h ago is still open.
    const spans = timeline.getByTestId("ks-armed-span");
    await expect(spans).toHaveCount(2);
    await expect(spans.nth(1)).toHaveAttribute("data-open", "true");
    await expect(timeline.getByTestId("ks-incident-tick")).toHaveAttribute("data-severity", "error");
    await expect(timeline.getByTestId("ks-timeline-older")).toContainText("1 older event");
    await timeline.getByTestId("ks-timeline-data").locator("summary").click();
    const rows = timeline.getByTestId("ks-timeline-row");
    await expect(rows).toHaveCount(4);
    await expect(rows.first()).toContainText("FLATTEN armed");
    await expect(rows.first()).toContainText("tom");
    await expect(rows.first()).toContainText("gap through the stops on USDJPY");
    // Both streams carry their source.
    await expect(page.getByTestId("ks-timeline-card").getByTestId("source-note")).toHaveCount(2);
  });

  test("a quiet month is said; a failed journal read is an error, never a quiet month", async ({ page }) => {
    await mockShell(page);
    await page.route(`${API}/api/system/kill-switch/history*`, (route: Route) =>
      route.fulfill({ json: killSwitchHistory([]) }),
    );
    await page.goto("/");
    await expect(page.getByTestId("ks-timeline-quiet")).toBeVisible();
    await page.route(`${API}/api/system/kill-switch/history*`, (route: Route) =>
      route.fulfill({
        status: 503,
        json: { code: "journal_unreadable", detail: "The journal could not be read.", correlation_id: "cid-ks", context: {} },
      }),
    );
    await page.reload();
    const card = page.getByTestId("ks-timeline-card");
    await expect(card.getByTestId("state-error")).toContainText("journal_unreadable");
    await expect(card.getByTestId("ks-timeline-quiet")).toHaveCount(0);
  });
});
