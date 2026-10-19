import { expect, test, type Route } from "@playwright/test";
import { API, mockShell } from "./fixtures";
import { OPS_SESSION, PAUSED_CONTROL } from "./ops-fixtures";

/**
 * Wave 3O screens: per-bot control (fleet row, bot page, command line), the
 * paper audit chain on the System board, reconciliation and the advisory
 * maturity report on the bot page, self-correlation on Candidates, the risk
 * calculator, allocation, and the structured journal. Every state, verdict
 * and number comes from a mocked API answer (ops-fixtures.ts).
 */

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await mockShell(page);
});

const env = <T>(data: T) => ({ data, source: { kind: "live", detail: "spec", as_of: "2026-10-19T09:00:00Z" }, caveats: [] });

test.describe("bot control", () => {
  test("the fleet row shows the API's control state and opens the control dialog", async ({ page }) => {
    await page.goto("/trading/fleet");
    const row = page.locator(`[data-testid="fleet-row"][data-session="${OPS_SESSION}"]`);
    await expect(row.getByTestId("bot-control-state")).toHaveAttribute("data-state", "active");
    await row.getByTestId("fleet-control-open").click();
    await expect(page.getByRole("dialog")).toContainText("Bot control");
    // Several acts: no default, the operator chooses.
    await expect(page.getByRole("dialog")).toContainText("There is no default");
    await expect(page.getByRole("dialog")).toContainText("DRAIN");
  });

  test("PAUSE <bot> on the command line opens the dialog with PAUSE chosen; a reason is required and the POST carries it", async ({ page }) => {
    let posted: Record<string, unknown> | null = null;
    await page.route(`${API}/api/trading/sessions/*/control`, async (route: Route) => {
      if (route.request().method() === "POST") {
        posted = route.request().postDataJSON() as Record<string, unknown>;
        return route.fulfill({ json: env(PAUSED_CONTROL) });
      }
      return route.fallback();
    });
    await page.goto("/");
    await expect(page.getByTestId("command-line")).toHaveAttribute("data-ready", "true");
    await page.getByTestId("command-line-input").fill(`PAUSE ${OPS_SESSION}`);
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/trading/sessions/${OPS_SESSION}\\?act=pause`));
    const dialog = page.getByRole("dialog");
    // The named act is the only choice, so its server-computed consequences show at once.
    await expect(dialog).toContainText("opens no new position for this bot");
    const confirm = dialog.getByRole("button", { name: "Record" });
    await expect(confirm).toBeDisabled();
    await dialog.getByRole("textbox").first().fill("reviewing a stop placement");
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect.poll(() => posted).toEqual({ action: "pause", reason: "reviewing a stop placement" });
  });

  test("the bot page renders control history, reconciliation, maturity and the audit chain", async ({ page }) => {
    await page.route(`${API}/api/trading/sessions/*/control`, (route: Route) =>
      route.request().method() === "GET" ? route.fulfill({ json: env(PAUSED_CONTROL) }) : route.fallback(),
    );
    await page.goto(`/trading/sessions/${OPS_SESSION}`);
    await expect(page.getByTestId("session-control")).toHaveAttribute("data-state", "paused");
    await expect(page.getByTestId("control-history-row")).toContainText("reviewing a stop placement");
    const recon = page.getByTestId("reconciliation");
    await expect(recon).toHaveAttribute("data-status", "diverged");
    await expect(page.locator('[data-testid="recon-divergence"][data-reason="operator_control"]')).toHaveCount(1);
    await expect(page.getByTestId("maturity-advisory")).toContainText("enables nothing");
    await expect(page.getByTestId("maturity-verdict")).toHaveText("NOT READY");
    await expect(page.locator('[data-testid="maturity-item"][data-key="min_trades"]')).toHaveAttribute("data-status", "fail");
    await expect(page.getByTestId("session-audit-chain")).toHaveAttribute("data-status", "ok");
  });
});

test("the System board shows each bot's audit-chain verdict, including a broken chain", async ({ page }) => {
  await page.route(`${API}/api/trading/audit-chain`, (route: Route) =>
    route.fulfill({
      json: {
        items: [
          {
            session_id: OPS_SESSION, file: "audit_chain.jsonl", present: true, intact: false, status: "broken",
            rows: { value: 40, provenance: "paper", unit: "count", as_of: null, sample_size: null, caveats: [], estimated: false },
            first_broken_seq: 12, reason: "row 12's content does not match its hash: it was edited",
            head_hash: "f".repeat(64), first_at: null, last_at: "2026-10-19T08:00:00Z", kinds: {}, verified_at: "2026-10-19T09:00:00Z",
          },
        ],
        total: 1, offset: 0, limit: 1, source: { kind: "live", detail: "BROKEN", as_of: "2026-10-19T09:00:00Z" }, caveats: [],
      },
    }),
  );
  await page.goto("/system");
  const row = page.getByTestId("audit-chain-row");
  await expect(row).toHaveAttribute("data-status", "broken");
  await expect(row.getByTestId("audit-chain-reason")).toContainText("row 12");
});

test("candidates show the API's self-correlation flag", async ({ page }) => {
  await page.goto("/trading/candidates");
  const flagged = page.locator('[data-testid="self-correlation-row"][data-strategy="ichimoku_kumo_trend"]');
  await expect(flagged).toHaveAttribute("data-flagged", "true");
  await expect(flagged.getByTestId("self-correlation-flag")).toHaveText("ABOVE 0.7");
});

test("the risk calculator sends inputs only and renders the API's sizes and ruin", async ({ page }) => {
  let asked = "";
  await page.route(`${API}/api/trading/risk/calculator*`, async (route: Route) => {
    asked = route.request().url();
    return route.fallback();
  });
  await page.goto("/trading/risk");
  await page.getByTestId("risk-calc-risk").fill("1");
  await page.getByTestId("risk-calc-run").click();
  await expect(page.getByTestId("risk-calc-ruin")).toContainText("2.35");
  expect(asked).toContain("risk_pct=1");
  expect(asked).toContain("strategy_id=donchian_breakout_atr");
  await expect(page.locator('[data-testid="risk-calc-size-row"][data-instrument="USDJPY"]')).toContainText("fx_rate_required");
});

test("allocation renders the API's weights as display only", async ({ page }) => {
  await page.goto("/trading/allocation");
  await expect(page.getByTestId("allocation-advisory")).toContainText("Display only");
  await expect(page.getByTestId("allocation-member")).toHaveCount(2);
  await expect(page.locator('[data-testid="allocation-stat"][data-stat="sharpe"]')).toContainText("0.66");
});

test.describe("structured journal", () => {
  test("notes show on the grid; Enter on a row opens the editor; Ctrl+Enter saves with the version", async ({ page }) => {
    let put: Record<string, unknown> | null = null;
    await page.route(`${API}/api/trading/journal/notes/*`, async (route: Route) => {
      if (route.request().method() === "PUT") {
        put = route.request().postDataJSON() as Record<string, unknown>;
        return route.fulfill({ json: env({ note: { ...(put as object), trade_id: "trd_0001", version: 1, updated_by: "joe", updated_at: "2026-10-19T09:00:00Z" }, history: [] }) });
      }
      return route.fallback();
    });
    await page.goto("/trading/execution");
    await expect(page.locator('[data-row-id="trd_0002"]').getByTestId("journal-grade")).toHaveText("A");
    await page.locator('[data-row-id="trd_0001"]').dblclick();
    const form = page.getByTestId("note-form");
    await expect(form).toBeVisible();
    await page.getByTestId("note-text").fill("chased the breakout");
    await page.getByTestId("note-grade").getByTestId("note-grade-C").focus();
    await page.keyboard.press("2");
    await expect(page.getByTestId("note-grade-B")).toBeChecked();
    await page.getByTestId("note-tags").fill("breakout, news");
    await page.getByTestId("note-text").focus();
    await page.keyboard.press("Control+Enter");
    await expect.poll(() => put).not.toBeNull();
    expect(put).toMatchObject({ note: "chased the breakout", grade: "B", tags: ["breakout", "news"], expected_version: 0 });
  });

  test("a journal filter switches the grid to the server-side filtered record", async ({ page }) => {
    let asked = "";
    await page.route(`${API}/api/trading/journal/trades*`, (route: Route) => {
      asked = route.request().url();
      return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 200, source: { kind: "live", detail: "spec", as_of: null }, caveats: [] } });
    });
    await page.goto("/trading/execution?grade=A");
    await expect.poll(() => asked).toContain("grade=A");
  });
});
