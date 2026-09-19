import type { Page, Route } from "@playwright/test";

/**
 * API mocking for the workstation's Playwright suite.
 *
 * Everything the UI renders comes from the API, so a test drives the UI by
 * deciding what the API says. That is also the proof that the UI holds no
 * trading logic of its own: there is no code path that produces a number
 * without one of these responses.
 */

export const API = "http://127.0.0.1:8000";

export function figure(
  value: number | null,
  provenance: string,
  unit = "",
  extra: Record<string, unknown> = {},
) {
  return {
    value,
    provenance,
    unit,
    as_of: null,
    sample_size: null,
    caveats: [],
    estimated: false,
    ...extra,
  };
}

export function source(kind: string, detail = "Test fixture.") {
  return { kind, detail, as_of: "2026-09-19T12:00:00Z" };
}

export function modeBanner(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      mode: "paper",
      provenance: "paper",
      touches_broker: false,
      touches_real_money: false,
      severity: "info",
      headline: "PAPER — simulated fills, no venue contacted",
      detail: "Fills are simulated against recorded executable prices.",
      live_execution_compiled_in: false,
      kill_switch_active: false,
      kill_switch_mode: null,
      as_of: "2026-09-19T12:00:00Z",
      ...overrides,
    },
    source: source("live"),
    caveats: [],
  };
}

export function killSwitchView(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      active: false,
      mode: null,
      operator: null,
      reason: null,
      since: null,
      armable: true,
      blocks_new_risk: false,
      requires_flatten: false,
      open_positions: 3,
      consequences: {
        pause: [
          "No new positions will be opened and no position will be increased.",
          "Reducing, closing and protective stop amendments stay permitted.",
          "Open positions are LEFT OPEN and keep their market exposure.",
        ],
        flatten: [
          "No new positions, and every open position is queued to be closed.",
          "3 closing intent(s) will be produced, ordered deterministically.",
          "Closing at market accepts whatever spread is available now.",
        ],
      },
      ...overrides,
    },
    source: source("live"),
    caveats: [],
  };
}

export function tradesPage() {
  const make = (id: string, provenance: string, pnl: number) => ({
    trade_id: id,
    strategy_id: "ichimoku_kumo_trend",
    instrument: "EURUSD",
    direction: "long",
    provenance,
    entry_time: "2026-09-18T08:00:00Z",
    exit_time: "2026-09-18T14:00:00Z",
    exit_reason: pnl > 0 ? "take_profit" : "stop_loss",
    size: figure(1.0, provenance, "lots"),
    entry_price: figure(1.1, provenance),
    exit_price: figure(1.11, provenance),
    gross_pnl: figure(pnl + 6, provenance, "GBP"),
    costs: figure(6, provenance, "GBP"),
    net_pnl: figure(pnl, provenance, "GBP"),
    r_multiple: figure(pnl / 250, provenance, "R"),
  });
  return {
    items: [
      make("trd_0001", "backtest", 412.5),
      make("trd_0002", "paper", -233.25),
      make("trd_0003", "out_of_sample", 118.0),
      make("trd_0004", "walkforward", -95.5),
    ],
    total: 4,
    offset: 0,
    limit: 100,
    source: source("seed"),
    caveats: [
      {
        code: "mixed_provenance_result",
        severity: "info",
        message:
          "This result mixes 1 backtest, 1 out_of_sample, 1 paper, 1 walkforward trades.",
        affects: "net_pnl",
        direction: "unknown",
      },
    ],
  };
}

export function candidatesPage() {
  const candidate = (id: string, n: number, eligible: boolean) => ({
    strategy_id: id,
    name: id.replace(/_/g, " "),
    family: "trend",
    lifecycle: "candidate",
    content_hash: "abc123def456",
    trades: figure(n, "out_of_sample", "count"),
    win_rate: figure(43.5, "out_of_sample", "pct"),
    expectancy_r: figure(0.08, "out_of_sample", "R"),
    net_pnl: figure(1240.5, "out_of_sample", "GBP"),
    sharpe: figure(0.62, "out_of_sample", "ratio"),
    max_drawdown_pct: figure(11.2, "out_of_sample", "pct"),
    eligible_for_ranking: eligible,
    blocking_reasons: eligible
      ? []
      : [`${n} out-of-sample trades; primary ranking requires 80.`],
    next_action: eligible ? "promote_to_paper" : "hold",
    next_action_requires_role: "admin",
  });
  return {
    items: [candidate("ichimoku_kumo_trend", 124, true), candidate("donchian_breakout_atr", 41, false)],
    total: 2,
    offset: 0,
    limit: 100,
    source: source("seed", "Ranked on out-of-sample evidence only."),
    caveats: [],
  };
}

/** Install the default happy-path API. Individual tests override routes after. */
export async function mockApi(page: Page) {
  await page.route(`${API}/api/system/execution-mode`, (route: Route) =>
    route.fulfill({ json: modeBanner() }),
  );
  await page.route(`${API}/api/system/kill-switch`, (route: Route) =>
    route.fulfill({ json: killSwitchView() }),
  );
  await page.route(`${API}/api/trading/trades*`, (route: Route) =>
    route.fulfill({ json: tradesPage() }),
  );
  await page.route(`${API}/api/trading/candidates`, (route: Route) =>
    route.fulfill({ json: candidatesPage() }),
  );
}

export async function failAll(page: Page, path = "**/api/**") {
  await page.route(path, (route: Route) =>
    route.fulfill({
      status: 503,
      json: {
        code: "database_unreachable",
        detail: "The platform could not reach its database.",
        correlation_id: "cid-test-0001",
        context: {},
      },
    }),
  );
}
