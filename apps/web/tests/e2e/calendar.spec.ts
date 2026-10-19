import { expect, test } from "@playwright/test";
import { mockChart } from "./fixtures";
import { mockMonitors, mockWorkspace, presetLayout, wsFixture } from "./workspace-fixtures";

/**
 * Wave 2A · Economic calendar (ECO, /markets/calendar): the newest official
 * version (the backend-written fixture says which), impact filter on the
 * server, countdowns, and chart links that open the chart at the event.
 */

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await mockChart(page);
  await mockMonitors(page);
});

test("events from the newest official version with the API's status and a countdown", async ({ page }) => {
  await page.goto("/markets/calendar");
  const rows = page.getByTestId("eco-row");
  const fixture = wsFixture<{ data: { events: unknown[]; calendar_version: string; research_pinned_version: string } }>("calendar_7d");
  await expect(rows).toHaveCount(fixture.data.events.length);
  await expect(page.getByTestId("panel-stamp")).toContainText(`official ${fixture.data.calendar_version}`);
  await expect(page.getByTestId("panel-stamp")).toContainText(`research pins ${fixture.data.research_pinned_version}`);
  await expect(rows.first()).toHaveAttribute("data-status", "upcoming");
  await expect(rows.first()).toHaveAttribute("data-next", "true");
  await expect(rows.first().getByTestId("eco-until")).toHaveText(/^(in .+|.+ ago|due)$/);
});

test("the impact filter is a server filter kept in the URL", async ({ page }) => {
  await page.goto("/markets/calendar");
  await expect(page.getByTestId("eco-row").first()).toBeVisible();
  const request = page.waitForRequest((r) => r.url().includes("/api/markets/calendar?") && r.url().includes("impact=high"));
  await page.getByTestId("eco-impact-high").click();
  await request;
  await expect(page).toHaveURL(/impact=high/);
});

test("EURUSD CAL lists the instrument's events; a chart link opens the chart at the event", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
  await page.getByTestId("command-line-input").fill("EURUSD CAL");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/markets\/calendar\?instrument=EURUSD/);
  const link = page.getByTestId("eco-chart").first();
  await expect(link).toHaveAttribute("href", /^\/markets\/EURUSD\?event=/);
  await link.click();
  await expect(page).toHaveURL(/\/markets\/EURUSD\?event=/, { timeout: 20_000 });
  await expect(page.getByTestId("event-focus")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("event-focus")).toHaveAttribute("data-status", "upcoming");
});

test("ECO and the calendar tile follow the amber group", async ({ page }) => {
  await mockWorkspace(page, { saved: { Desk: presetLayout("fx-monitoring") } });
  await page.goto("/workspace?layout=Desk");
  const tile = page.locator('[data-testid="ws-tile"][data-kind="calendar"]');
  await expect(tile.getByTestId("eco-instrument")).toContainText("EURUSD");
  await expect(tile.getByTestId("eco-row").first()).toBeVisible();
});
