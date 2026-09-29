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

export const PREFLIGHT_CAVEATS = [
  {
    code: "slippage_not_modelled",
    severity: "warning",
    message: "Slippage model is 'zero': every fill is assumed at the requested price.",
    affects: "net_pnl",
    direction: "optimistic",
  },
  {
    code: "static_spread",
    severity: "warning",
    message: "Spreads are the instrument's typical value, held constant.",
    affects: "net_pnl",
    direction: "optimistic",
  },
  {
    code: "simulated_execution",
    severity: "warning",
    message: "Produced by simulation (out_of_sample); no order reached a venue.",
    affects: "expectancy",
    direction: "optimistic",
  },
];

/** GET /api/trading/candidates/{id}/promote/preflight. */
export function promotePreflight(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      strategy_id: "ichimoku_kumo_trend",
      execution_mode: "paper",
      eligible: true,
      blocking_reasons: [],
      caveats: PREFLIGHT_CAVEATS,
      consequences: {
        paper: [
          "SERVER-PAPER-1: records the promotion to PAPER on 124 out_of_sample trade(s).",
          "SERVER-PAPER-2: this route does not itself start a run.",
        ],
        shadow: [
          "SERVER-SHADOW-1: records the promotion to SHADOW.",
          "SERVER-SHADOW-2: no order is submitted and no position is opened.",
        ],
      },
      ...overrides,
    },
    source: source("live"),
    caveats: [],
  };
}

/** GET /api/trading/preflight/kill-switch-disarm. */
export function disarmPreflight(lines?: string[]) {
  return {
    data: {
      active: true,
      mode: "pause",
      execution_mode: "paper",
      consequences: {
        disarm: lines ?? [
          "SERVER-DISARM-1: lifts the PAUSE halt armed by joe.",
          "SERVER-DISARM-2: the deployment stays in PAPER mode.",
        ],
      },
    },
    source: source("live"),
    caveats: [],
  };
}

export function healthReport(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    checked_at: "2026-09-19T12:00:00Z",
    build_sha: "deadbeef",
    build_time: null,
    migration_revision: "0007",
    execution_mode: "paper",
    worker_heartbeat_age_seconds: 12,
    uptime_seconds: 3600,
    python_version: "3.11.9",
    checks: [
      {
        name: "database",
        status: "ok",
        detail: "reachable in 3 ms",
        critical: true,
        latency_ms: 3,
      },
    ],
    advisory: "",
    ...overrides,
  };
}

/**
 * GET /api/stream, held open and never answered: the stream stays
 * "connecting", deterministically, and every view reads REST exactly as it
 * did before the stream existed. Tests of the stream install tests/e2e/sse.ts
 * instead (a later route wins).
 */
export async function holdStream(page: Page) {
  await page.route(`${API}/api/stream*`, () => new Promise<void>(() => undefined));
}

/** Install the default happy-path API. Individual tests override routes after. */
export async function mockApi(page: Page) {
  await holdStream(page);
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
  await page.route(`${API}/api/trading/candidates/*/promote/preflight`, (route: Route) =>
    route.fulfill({ json: promotePreflight() }),
  );
  await page.route(`${API}/api/trading/preflight/kill-switch-disarm`, (route: Route) =>
    route.fulfill({ json: disarmPreflight() }),
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

/** GET /api/auth/me: the signed-in operator (PrincipalView, not an envelope). */
export function principal(overrides: Record<string, unknown> = {}) {
  return {
    user_id: "joe",
    display_name: "Joe",
    role: "admin",
    expires_at: "2026-09-29T12:00:00Z",
    can_arm_kill_switch: true,
    can_promote: true,
    ...overrides,
  };
}

/** The live-mode banner payload, exactly as the API describes real money. */
export function liveBanner(overrides: Record<string, unknown> = {}) {
  return modeBanner({
    mode: "live",
    provenance: "broker_live",
    touches_broker: true,
    touches_real_money: true,
    severity: "danger",
    headline: "LIVE: orders are reaching a real-money account",
    detail: "Every control on this page moves real capital.",
    live_execution_compiled_in: true,
    ...overrides,
  });
}

/** One attention item, shaped as routers/command.py's AttentionItem. */
export function attentionItem(
  id: string,
  severity: string,
  deepLink: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    category: "test",
    severity,
    title: `Item ${id}`,
    reason: `Reason ${id}`,
    deep_link: deepLink,
    as_of: "2026-09-19T12:00:00Z",
    score: figure(400, "paper", "score"),
    ...overrides,
  };
}

/** GET /api/command/attention: the server-ranked queue, in the server's order. */
export function attentionPage(items?: Record<string, unknown>[]) {
  const list = items ?? [
    attentionItem("kill_switch:armed", "critical", "/trading/risk", {
      title: "Daily loss at 82% of its limit",
    }),
    attentionItem("strategy_review:abc:candidate", "info", "/trading/candidates", {
      title: "Two candidates await review",
    }),
  ];
  return {
    items: list,
    total: list.length,
    offset: 0,
    limit: 50,
    source: source("live", "Ranked by the platform."),
    caveats: [],
  };
}

/** One incident, shaped as routers/incidents.py's IncidentView. */
export function incident(overrides: Record<string, unknown> = {}) {
  return {
    id: "inc-1",
    key: "worker_heartbeat_late",
    event: "worker.heartbeat_late",
    source: "alert_log",
    title: "Worker heartbeat late",
    severity: "warning",
    status: "open",
    first_seen: "2026-09-19T11:55:00Z",
    last_seen: "2026-09-19T11:58:00Z",
    occurrences: figure(3, "paper", "count"),
    acknowledged_by: null,
    acknowledged_at: null,
    resolved_at: null,
    deep_link: "/system/incidents/inc-1",
    as_of: "2026-09-19T12:00:00Z",
    timeline: [],
    ...overrides,
  };
}

/** GET /api/system/incidents. */
export function incidentsPage(items?: Record<string, unknown>[]) {
  const list = items ?? [incident()];
  return {
    items: list,
    total: list.length,
    offset: 0,
    limit: 100,
    source: source("live", "Incident read model."),
    caveats: [],
  };
}

/**
 * The shell's own reads (health for the status bar, the operator, the
 * command screen's queue and incidents) on top of mockApi, so a test sees a
 * fully answered shell rather than "unreachable".
 *
 * `operator: false` makes /api/auth/me fail without saying "no session"
 * (a 503): the platform did not supply an operator. `"unauthenticated"` is
 * the 401 that sends the workstation to the sign-in page.
 */
export async function mockShell(
  page: Page,
  options: { operator?: boolean | "unauthenticated" | Record<string, unknown> } = {},
) {
  await mockApi(page);
  await page.route(`${API}/api/health`, (route: Route) => route.fulfill({ json: healthReport() }));
  await page.route(`${API}/api/command/attention`, (route: Route) =>
    route.fulfill({ json: attentionPage() }),
  );
  await page.route(`${API}/api/system/incidents`, (route: Route) =>
    route.fulfill({ json: incidentsPage() }),
  );
  const op = options.operator;
  await page.route(`${API}/api/auth/me`, (route: Route) => {
    if (op === false) {
      return route.fulfill({
        status: 503,
        json: { code: "unavailable", detail: "Sessions unavailable.", correlation_id: "cid-me", context: {} },
      });
    }
    if (op === "unauthenticated") {
      return route.fulfill({
        status: 401,
        json: { code: "not_authenticated", detail: "Sign in to continue.", correlation_id: "cid-me", context: {} },
      });
    }
    return route.fulfill({ json: principal(typeof op === "object" ? op : {}) });
  });
}

/** GET /api/trading/risk's `data` (RiskStateView). */
export function riskState(overrides: Record<string, unknown> = {}) {
  return {
    limits_version: "limits_v1_paper",
    kill_switch_active: false,
    kill_switch_mode: null,
    new_risk_permitted: true,
    new_risk_reason: "within limits",
    closing_permitted: true,
    daily_loss_pct: figure(-0.42, "paper", "pct"),
    max_daily_loss_pct: figure(2, "paper", "pct"),
    drawdown_pct: figure(1.1, "paper", "pct"),
    max_drawdown_limit_pct: figure(10, "paper", "pct"),
    margin_utilisation_pct: figure(null, "paper", "pct"),
    breaches: [],
    ...overrides,
  };
}
