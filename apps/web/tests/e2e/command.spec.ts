import { expect, test, type Route } from "@playwright/test";
import { API, attentionItem, attentionPage, incident, incidentsPage, mockShell } from "./fixtures";

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
