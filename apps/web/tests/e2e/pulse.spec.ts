import { expect, test } from "@playwright/test";
import { mockShell } from "./fixtures";
import { mockMonitors, mockWorkspace, presetLayout, wsFixture } from "./workspace-fixtures";

/** Wave 2A · Market Pulse rebuilt (/market-pulse, PULSE): the backend's movers, strength, volatility, sessions. */

test.use({ viewport: { width: 1440, height: 900 } });

type Pulse = { data: { movers_up: { symbol: string }[]; movers_down: { symbol: string }[]; strength: { currency: string; score_pct: { value: number } }[]; rows: unknown[]; instruments_recorded: number } };

test.beforeEach(async ({ page }) => {
  await mockShell(page);
  await mockMonitors(page);
});

test("movers, strength, volatility and sessions are the API's, over every recorded instrument", async ({ page }) => {
  const fx = wsFixture<Pulse>("pulse_24h").data;
  await page.goto("/market-pulse");
  await expect(page.getByTestId("pulse-up").getByTestId("pulse-symbol")).toHaveText(fx.movers_up.map((m) => m.symbol));
  await expect(page.getByTestId("pulse-down").getByTestId("pulse-symbol")).toHaveText(fx.movers_down.map((m) => m.symbol));
  const strength = page.getByTestId("pulse-strength").locator("li");
  await expect(strength).toHaveCount(fx.strength.length);
  await expect(strength.first()).toHaveAttribute("data-currency", fx.strength[0]?.currency ?? "");
  await expect(page.getByTestId("pulse-sessions").locator("li")).toHaveCount(4);
  await expect(page.getByTestId("pulse-vol").locator("tbody tr")).not.toHaveCount(0);
  await expect(page.getByTestId("pulse-row")).toHaveCount(fx.rows.length);
  await expect(page.getByTestId("panel-stamp")).toContainText(`${fx.instruments_recorded} of`);
});

test("PULSE opens it from the command line", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
  await page.getByTestId("command-line-input").fill("PULSE");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/market-pulse$/);
  await expect(page.getByTestId("pulse-strength")).toBeVisible();
});

test("a mover in the pulse tile retargets the amber group", async ({ page }) => {
  const fx = wsFixture<Pulse>("pulse_24h").data;
  await mockWorkspace(page, { saved: { Desk: presetLayout("fx-monitoring") } });
  await page.goto("/workspace?layout=Desk");
  const tile = page.locator('[data-testid="ws-tile"][data-kind="market_pulse"]');
  const target = fx.movers_down[0]?.symbol ?? fx.movers_up[0]?.symbol ?? "";
  await tile.getByTestId("pulse-symbol").filter({ hasText: target }).first().click();
  await expect(page.locator('[data-kind="chart"] [data-testid="ws-tile-symbol"]')).toHaveText(target);
});
