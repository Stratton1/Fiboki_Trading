import type { Page, Route } from "@playwright/test";
import { API, figure, source } from "./fixtures";

/**
 * Wave 3O mocks: the ops routes (src/fiboki/api/routers/ops.py), answered with
 * happy-path payloads in the API's shapes. Registered by mockShell so every
 * screen that reads them (fleet, a bot's page, the System board, candidates,
 * risk, the journal) renders its success state; a spec overrides any one by
 * registering its own route afterwards.
 */

export const OPS_SESSION = "forward-paper_forward_v1";

const env = <T>(data: T, detail = "Ops fixture.") => ({ data, source: source("live", detail), caveats: [] });
const page1 = <T>(items: T[], detail = "Ops fixture.") => ({
  items,
  total: items.length,
  offset: 0,
  limit: Math.max(1, items.length),
  source: source("live", detail),
  caveats: [],
});

export function controlView(overrides: Record<string, unknown> = {}) {
  return {
    session_id: OPS_SESSION,
    state: "active",
    blocks_entries: false,
    meaning: "Trading as its wiring says: new entries are evaluated and may be opened.",
    since: null,
    actor: null,
    reason: null,
    allowed_actions: ["pause", "drain"],
    controllable: true,
    consequences: {
      pause: [
        "The paper runtime opens no new position for this bot from its next cycle.",
        "1 open position(s) stay open and keep their exits.",
        "Signals the pause suppresses are journalled; nothing is closed.",
      ],
      drain: [
        "No new position; open ones close only by their own stop, trail, target or time rule.",
        "1 open position(s) stay open and keep their exits.",
        "When the book is flat the runtime marks the bot STOPPED by itself.",
      ],
    },
    history: [],
    ...overrides,
  };
}

export const PAUSED_CONTROL = controlView({
  state: "paused",
  blocks_entries: true,
  meaning: "No new entries. Open positions keep their stops, trails, targets and time stops; nothing is closed by the pause.",
  since: "2026-10-19T09:00:00Z",
  actor: "joe",
  reason: "reviewing a stop placement",
  allowed_actions: ["resume", "drain"],
  consequences: {
    resume: [
      "New entries are evaluated and may be opened from the next cycle.",
      "The kill switch, the risk gateway and every limit still apply.",
    ],
    drain: ["No new position; open ones close only by their own stop, trail, target or time rule."],
  },
  history: [
    {
      action: "pause",
      state_before: "active",
      state_after: "paused",
      actor: "joe",
      actor_role: "admin",
      reason: "reviewing a stop placement",
      at: "2026-10-19T09:00:00Z",
    },
  ],
});

export async function mockOps(page: Page) {
  await page.route(`${API}/api/trading/controls`, (route: Route) =>
    route.fulfill({ json: page1([controlView()]) }),
  );
  await page.route(`${API}/api/trading/sessions/*/control`, (route: Route) =>
    route.request().method() === "GET" ? route.fulfill({ json: env(controlView()) }) : route.fallback(),
  );
  await page.route(`${API}/api/trading/audit-chain`, (route: Route) =>
    route.fulfill({ json: page1([chainIntegrity()]) }),
  );
  await page.route(`${API}/api/trading/sessions/*/audit-chain*`, (route: Route) =>
    route.fulfill({ json: env(chainTail()) }),
  );
  await page.route(`${API}/api/trading/candidates/correlation`, (route: Route) =>
    route.fulfill({ json: env(selfCorrelationView()) }),
  );
  await page.route(`${API}/api/trading/sessions/*/maturity`, (route: Route) =>
    route.fulfill({ json: env(maturityView()) }),
  );
  await page.route(`${API}/api/trading/sessions/*/reconciliation`, (route: Route) =>
    route.request().method() === "GET" ? route.fulfill({ json: env(reconciliationView()) }) : route.fallback(),
  );
}


export function chainIntegrity(overrides: Record<string, unknown> = {}) {
  return {
    session_id: OPS_SESSION,
    file: "audit_chain.jsonl",
    present: true,
    intact: true,
    status: "ok",
    rows: figure(1284, "paper", "count"),
    first_broken_seq: null,
    reason: "1284 row(s) verify from genesis",
    head_hash: "9f2c41d0a7b3e5c6d8f0123456789abcdef0123456789abcdef0123456789ab",
    first_at: "2026-10-12T00:00:05+00:00",
    last_at: "2026-10-19T08:00:05+00:00",
    kinds: { risk_decision: 900, order_intent: 120, venue_fill: 60, trade_closed: 58 },
    verified_at: "2026-10-19T09:00:00Z",
    ...overrides,
  };
}

export function chainTail(overrides: Record<string, unknown> = {}) {
  return {
    integrity: chainIntegrity(overrides),
    rows: [
      { seq: 1284, at: "2026-10-19T08:00:05+00:00", kind: "trade_closed", summary: "EURUSD trailing_stop net -12.5", hash: "a".repeat(64), prev_hash: "b".repeat(64) },
      { seq: 1283, at: "2026-10-19T08:00:05+00:00", kind: "risk_decision", summary: "EURUSD allowed", hash: "b".repeat(64), prev_hash: "c".repeat(64) },
    ],
  };
}

export function reconciliationView(overrides: Record<string, unknown> = {}) {
  const count = (v: number, p = "paper") => figure(v, p, "count");
  return {
    session_id: OPS_SESSION,
    job: {
      job_id: "ops_0123456789ab",
      kind: "reconcile",
      status: "succeeded",
      requested_by: "tom",
      reason: "weekly reconciliation",
      requested_at: "2026-10-19T08:00:00Z",
      updated_at: "2026-10-19T08:01:00Z",
      error: "",
      runner_note: "Run by `fiboki ops jobs` (a separate process; the API never runs a backtest). Until it runs, the job stays queued.",
    },
    report: {
      status: "diverged",
      status_reason: "3 divergence(s), 3 attributed, 0 unexplained",
      window_start: "2026-10-12T00:00:00Z",
      window_end: "2026-10-19T04:00:00Z",
      paper_trades: count(12),
      backtest_trades: count(13, "backtest"),
      matched: count(12),
      matched_identical: count(11),
      paper_only: count(0),
      backtest_only: count(1, "backtest"),
      outside_window: count(0),
      explained: count(3),
      unexplained: count(0),
      first_divergence_at: "2026-10-14T08:00:00Z",
      divergences: [
        {
          kind: "backtest_only", instrument: "EURUSD", direction: "long", entry_time: "2026-10-14T08:00:00Z", field: "trade",
          paper: null, backtest: figure(84.2, "backtest", "USD"), paper_text: null, backtest_text: null, delta: null,
          reason: "operator_control", explained: true, detail: "the bot was paused on that bar; the entry was suppressed and journalled",
        },
        {
          kind: "field", instrument: "GBPUSD", direction: "short", entry_time: "2026-10-16T12:00:00Z", field: "slippage_cost",
          paper: figure(0.91, "paper", "USD"), backtest: figure(1.37, "backtest", "USD"), paper_text: null, backtest_text: null,
          delta: figure(-0.46, "paper", "USD", { estimated: true }),
          reason: "slippage_draw", explained: true, detail: "slippage is drawn per bar by rng_for(seed, bar_index, sequence)",
        },
      ],
      reasons: { operator_control: 1, slippage_draw: 2 },
      approximations: ["Paper indicators are computed over a sliding window of 1500 bars."],
      provenance: { wiring_version: "paper_forward_v1", account_ccy: "USD" },
      digest: "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0",
      generated_at: "2026-10-19T08:01:00Z",
      job_id: "ops_0123456789ab",
    },
    ...overrides,
  };
}

export function maturityView(overrides: Record<string, unknown> = {}) {
  const item = (key: string, label: string, status: string, value: number | null, threshold: number | null, unit: string, detail: string) => ({
    key, label, status,
    value: value === null ? { ...figure(null, "paper", unit), caveats: [] } : figure(value, "paper", unit),
    threshold: threshold === null ? figure(null, "paper", unit) : figure(threshold, key === "drawdown_in_band" ? "backtest" : "paper", unit),
    detail,
  });
  return {
    session_id: OPS_SESSION,
    criteria_version: "maturity_advisory_v1",
    ready: false,
    verdict: "not_ready",
    items: [
      item("min_trades", "Paper closed trades", "fail", 12, 30, "count", "12 closed paper trade(s); the advisory floor is 30."),
      item("min_days", "Days live", "fail", 7.2, 28, "days", "7.2 day(s) from the session's first bar to its last; the advisory floor is 28."),
      item("drawdown_in_band", "Drawdown vs backtest band", "pass", 1.8, 4.1, "pct", "Paper max drawdown 1.80% over 12 trade(s)."),
      item("costs_as_expected", "Realised vs expected costs", "pass", 1.04, 1.25, "ratio", "Paper paid 1.04x the backtest's cost per unit traded."),
      item("reconciliation_clean", "Reconciliation", "pass", 0, 0, "count", "diverged: 3 divergence(s), 3 attributed, 0 unexplained."),
      item("audit_chain_intact", "Audit chain", "pass", 1284, null, "count", "1284 row(s) verify from genesis"),
    ],
    band: {
      horizon_trades: 12, source_trades: 140, n_paths: 2000, seed: 20261019, risk_fraction: 0.005,
      p5: figure(0.9, "backtest", "pct", { estimated: true }),
      p50: figure(2.1, "backtest", "pct", { estimated: true }),
      p95: figure(4.1, "backtest", "pct", { estimated: true }),
      source: "backtest bt_0001",
    },
    paper_max_drawdown_pct: figure(1.8, "paper", "pct"),
    note: "Advisory only. This report enables nothing: no lifecycle state, execution mode, limit or flag reads it.",
    notes: [],
    ...overrides,
  };
}

export function selfCorrelationView() {
  const cell = (basis: string, rho: number | null, n: number, flagged = false, same = false) => ({
    session_id: OPS_SESSION, bot_strategy_id: "donchian_breakout_atr", basis,
    rho: rho === null ? figure(null, basis === "paper" ? "paper" : "backtest", "ratio") : figure(rho, basis === "paper" ? "paper" : "backtest", "ratio", { sample_size: n }),
    n_common: n, flagged, same_strategy: same,
  });
  return {
    threshold: 0.7,
    min_common_days: 20,
    bots: [OPS_SESSION],
    rows: [
      { strategy_id: "ichimoku_kumo_trend", source: "backtest bt_0002", cells: [cell("backtest", 0.82, 400, true), cell("paper", null, 6)],
        max_abs_rho: figure(0.82, "backtest", "ratio"), flagged: true,
        flag_reason: "|rho| 0.82 with forward-paper_forward_v1 (backtest basis) is above 0.7: it would largely re-buy risk the fleet already holds." },
      { strategy_id: "donchian_breakout_atr", source: "backtest bt_0001", cells: [cell("backtest", 1, 400, false, true), cell("paper", 0.9, 30, false, true)],
        max_abs_rho: figure(null, "backtest", "ratio"), flagged: false, flag_reason: "Not measurable: no overlap with the fleet." },
    ],
    method: "Pearson correlation of daily returns of the closed-trade balance curve, on common days only.",
  };
}
