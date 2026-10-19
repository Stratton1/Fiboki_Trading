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
    worker_state: "ok",
    worker_stale_after_seconds: 120,
    worker_stale_at: "2026-09-19T12:01:48Z",
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

/** GET /api/markets/instruments (InstrumentView rows). */
export function instrumentsPage() {
  const make = (symbol: string, pip: number, spread: number) => ({
    symbol,
    asset_class: "fx",
    base: symbol.slice(0, 3),
    quote: symbol.slice(3),
    trading_hours: "Sun 22:00 to Fri 22:00 UTC",
    pip_size: figure(pip, "backtest"),
    contract_size: figure(100000, "backtest", "count"),
    min_size: figure(0.01, "backtest", "lots"),
    size_step: figure(0.01, "backtest", "lots"),
    typical_spread_pips: figure(spread, "backtest", "pips"),
    retail_leverage: figure(30, "backtest", "x"),
    annual_financing_bps: figure(-250, "backtest", "bps"),
    venue: "oanda",
    shortable: true,
    typical_spread_bps: null,
  });
  const items = [make("EURUSD", 0.0001, 0.6), make("USDJPY", 0.01, 0.9), make("GBPUSD", 0.0001, 0.9)];
  return { items, total: items.length, offset: 0, limit: 100, source: source("seed"), caveats: [] };
}

/** GET /api/intelligence/audit (AuditEntryView rows). */
export function auditPage(count = 3) {
  const items = Array.from({ length: count }, (_, i) => ({
    sequence: i + 1,
    at: `2026-09-19T1${i % 10}:00:00Z`,
    action: i % 2 === 0 ? "kill_switch.arm" : "kill_switch.disarm",
    actor: "joe",
    actor_role: "admin",
    outcome: i === 1 ? "refused" : "allowed",
    execution_mode: "paper",
    target: "kill_switch",
    reason: `audited reason ${i + 1}`,
    correlation_id: `cid-${i + 1}`,
    entry_hash: `hash${i + 1}`.padEnd(16, "0"),
  }));
  return { items, total: items.length, offset: 0, limit: 200, source: source("live"), caveats: [] };
}

export function auditIntegrity() {
  return {
    data: { intact: true, entries: 3, first_broken_sequence: null, detail: "3 entries, hash chain verified." },
    source: source("live"),
    caveats: [],
  };
}

/** `count` trades with distinct ids, for the grid's virtualisation tests. */
export function manyTrades(count: number) {
  const base = tradesPage().items;
  const items = Array.from({ length: count }, (_, i) => {
    const row = base[i % base.length]!;
    const pnl = ((i * 37) % 1000) - 500;
    return {
      ...row,
      trade_id: `trd_${String(i).padStart(5, "0")}`,
      net_pnl: figure(pnl, row.provenance, "GBP"),
      r_multiple: figure(pnl / 250, row.provenance, "R"),
    };
  });
  return { ...tradesPage(), items, total: count, limit: count };
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
  await page.route(`${API}/api/markets/instruments`, (route: Route) =>
    route.fulfill({ json: instrumentsPage() }),
  );
  await page.route((url) => url.pathname === "/api/intelligence/audit", (route: Route) =>
    route.fulfill({ json: auditPage() }),
  );
  await page.route(`${API}/api/intelligence/audit/integrity`, (route: Route) =>
    route.fulfill({ json: auditIntegrity() }),
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
    can_draw: true,
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

/** GET /api/system/incidents/{id}: one incident in an Envelope, with a timeline. */
export function incidentEnvelope(overrides: Record<string, unknown> = {}) {
  return {
    data: incident({
      timeline: [
        {
          at: "2026-09-19T11:55:00Z",
          kind: "occurrence",
          severity: "warning",
          actor: "alert_log",
          text: "worker.heartbeat_late: no heartbeat for 95 s.",
          correlation_id: "cid-occ-1",
        },
        {
          at: "2026-09-19T11:58:00Z",
          kind: "occurrence",
          severity: "warning",
          actor: "alert_log",
          text: "worker.heartbeat_late: no heartbeat for 180 s.",
          correlation_id: "cid-occ-2",
        },
      ],
      ...overrides,
    }),
    source: source("live", "Incident read model."),
    caveats: [],
  };
}

/**
 * One kill-switch journal event (GET /api/system/kill-switch/history rows),
 * `hoursAgo` before the test's own clock, so it always lands in the 30-day
 * window the timeline draws.
 */
export function killSwitchEvent(
  hoursAgo: number,
  action: "activate" | "deactivate",
  mode: "pause" | "flatten" | null,
  operator: string,
  reason: string,
) {
  return {
    action,
    mode,
    operator,
    reason,
    at: new Date(Date.now() - hoursAgo * 3_600_000).toISOString(),
    positions_open: 2,
  };
}

/** GET /api/system/kill-switch/history: the journal, newest first (as the route returns it). */
export function killSwitchHistory(items?: Record<string, unknown>[]) {
  const list = items ?? [
    killSwitchEvent(30, "deactivate", null, "tom", "spreads normal again after the release"),
    killSwitchEvent(52, "activate", "pause", "joe", "NFP in ten minutes; pausing new risk"),
  ];
  return {
    items: list,
    total: list.length,
    offset: 0,
    limit: 500,
    source: source("live", "Append-only kill-switch journal."),
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
  await mockOperatorCore(page);
  await page.route(`${API}/api/health`, (route: Route) => route.fulfill({ json: healthReport() }));
  await page.route(`${API}/api/command/attention`, (route: Route) =>
    route.fulfill({ json: attentionPage() }),
  );
  await page.route(`${API}/api/system/incidents`, (route: Route) =>
    route.fulfill({ json: incidentsPage() }),
  );
  // One incident (its page) and the kill-switch journal (the timeline on
  // Command and Risk). `*` stops at a slash, so .../inc-1/ack is not caught.
  await page.route(`${API}/api/system/incidents/*`, (route: Route) =>
    route.request().method() === "GET" ? route.fulfill({ json: incidentEnvelope() }) : route.fallback(),
  );
  await page.route(`${API}/api/system/kill-switch/history*`, (route: Route) =>
    route.fulfill({ json: killSwitchHistory() }),
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

// ------------------------------------------------------ chart workstation

/** Round to the 5 decimals an EURUSD price is quoted in, so fixtures are exact. */
const px = (value: number) => Number(value.toFixed(5));

/** The open time (ISO, UTC) of H1 bar `i` in the chart fixtures. */
export function barTime(i: number, start = "2026-09-21T00:00:00Z") {
  return new Date(Date.parse(start) + i * 3_600_000).toISOString().replace(".000Z", "Z");
}

/** One synthetic bar, deterministic, as routers/markets.py `bars` serialises it. */
export function chartBar(i: number) {
  const o = px(1.1 + i * 0.0002);
  const c = px(o + (i % 2 === 0 ? 0.0003 : -0.0002));
  return {
    t: barTime(i),
    o,
    h: px(Math.max(o, c) + 0.0004),
    l: px(Math.min(o, c) - 0.0003),
    c,
    // Bar 5 has no recorded volume: null, never 0.
    v: i === 5 ? null : 100 + i,
  };
}

/** GET /api/markets/bars/{symbol}: `count` H1 bars with tick volume. */
export function chartBars(
  options: { count?: number; symbol?: string; timeframe?: string; version?: string; volume?: boolean } = {},
) {
  const count = options.count ?? 120;
  const volume = options.volume ?? true;
  const bars = Array.from({ length: count }, (_, i) => {
    const bar = chartBar(i);
    if (!volume) {
      const { v: _v, ...rest } = bar;
      return rest;
    }
    return bar;
  });
  return {
    data: {
      symbol: options.symbol ?? "EURUSD",
      timeframe: options.timeframe ?? "H1",
      dataset_version_id: options.version ?? "dsv_eurusd_h1_0007abcdef",
      volume_kind: volume ? "tick_volume" : null,
      bars,
    },
    source: source("live", "Canonical bar store."),
    caveats: [],
  };
}

const overlaySource = (kind: string, detail: string) => ({ kind, detail });

function seriesItem(
  name: string,
  indicator: string,
  key: string,
  pane: string,
  value: (i: number) => number | null,
  count: number,
  displayOnly = false,
) {
  return {
    kind: "series",
    pane,
    name,
    indicator_id: indicator,
    indicator_key: key,
    params: {},
    display_only: displayOnly,
    points: Array.from({ length: count }, (_, i) => ({ t: barTime(i), v: value(i) })),
    provenance: null,
    dataset_version_id: "dsv_eurusd_h1_0007abcdef",
    source: overlaySource("bar_store", "Canonical bar store, dataset dsv_eurusd_h1_0007abcdef; computed by fiboki.indicators."),
  };
}

/**
 * GET /api/markets/overlays/{symbol}, shaped as routers/markets.py OverlayView:
 * four signals (one before the first bar, so outside the window), one closed
 * trade (entry and exit) and one open position with its three levels, three
 * regime runs (trend, range, unknown), four indicator series across the price
 * pane, a state pane and an own pane, and the sections the API reports.
 */
export function chartOverlays(options: { count?: number; version?: string | null } = {}) {
  const count = options.count ?? 120;
  const ichimoku = "ichimoku_9_26_52_26_26";
  const journal = overlaySource("paper_journal", "session ps_001 (H1), replayed dataset dsv_eurusd_h1_0007abcdef");
  const common = {
    session_id: "ps_001",
    provenance: "paper",
    dataset_version_id: "dsv_eurusd_h1_0007abcdef",
    source: journal,
  };
  const signal = (t: string, side: string, outcome: string, reason: string, id: string, price: number | null) => ({
    kind: "signal",
    t,
    side,
    strategy_id: "ichimoku_kumo_trend",
    outcome,
    reason,
    signal_id: id,
    timeframe: "H1",
    requested_price: figure(price, "paper"),
    ...common,
  });
  const fill = (t: string, role: string, side: string, price: number, trade: string, pnl: number | null) => ({
    kind: "fill",
    t,
    role,
    side,
    price: figure(price, "paper"),
    trade_id: trade,
    strategy_id: "ichimoku_kumo_trend",
    exit_reason: role === "exit" ? "take_profit" : null,
    net_pnl: pnl === null ? null : figure(pnl, "paper", "GBP"),
    ...common,
  });
  const level = (role: string, price: number) => ({
    kind: "level",
    role,
    price: figure(price, "paper"),
    from: barTime(100),
    to: null,
    position_id: "pos_0001",
    strategy_id: "ichimoku_kumo_trend",
    ...common,
  });
  const regime = (from: number, to: number, label: string) => ({
    kind: "regime",
    from: barTime(from),
    to: barTime(to),
    label,
    regime_key: `${label}|normal|liquid|calm|neutral`,
    axes: { direction: label === "trend" ? "up" : "flat", volatility: "normal" },
    provenance: null,
    dataset_version_id: "dsv_eurusd_h1_0007abcdef",
    classifier_fingerprint: "rc_v1_3f2a",
    source: overlaySource("marketstate", "RegimeClassifier"),
  });
  const last = count - 1;
  return {
    data: {
      symbol: "EURUSD",
      timeframe: "H1",
      window_from: barTime(0),
      window_to: barTime(last),
      bars_dataset_version_id:
        options.version === undefined ? "dsv_eurusd_h1_0007abcdef" : options.version,
      signals: [
        signal("2026-09-20T12:00:00Z", "long", "accepted", "open accepted by the gateway", "sig_0", 1.0999),
        signal(`${barTime(10).slice(0, -1)}.500Z`, "long", "accepted", "open accepted by the gateway", "sig_1", 1.102),
        signal(barTime(30), "short", "blocked", "max_open_positions", "sig_2", 1.106),
        signal(barTime(50), "unknown", "accepted", "open accepted by the gateway", "sig_3", null),
      ],
      fills: [
        fill(barTime(11), "entry", "buy", 1.1025, "trd_0001", null),
        fill(barTime(20), "exit", "sell", 1.1045, "trd_0001", 12.5),
        fill(barTime(100), "entry", "buy", 1.12, "pos_0001", null),
      ],
      levels: [level("entry", 1.12), level("stop", 1.118), level("target", 1.125)],
      regimes: [regime(0, 40, "trend"), regime(40, 80, "range"), regime(80, last, "unknown")],
      series: [
        seriesItem(`${ichimoku}_tenkan`, ichimoku, "ichimoku", "price", (i) => px(chartBar(i).c - 0.0001), count),
        seriesItem(`${ichimoku}_kijun`, ichimoku, "ichimoku", "price", (i) => (i < 25 ? null : px(chartBar(i).c - 0.0005)), count),
        seriesItem(`${ichimoku}_chikou_span_display`, ichimoku, "ichimoku", "price", (i) => (i > last - 26 ? null : chartBar(i + 26).c), count, true),
        seriesItem(`${ichimoku}_price_vs_cloud`, ichimoku, "ichimoku", `state:${ichimoku}`, (i) => (i % 3 === 0 ? -1 : 1), count),
        seriesItem("atr_14", "atr_14", "atr", "atr_14", (i) => (i < 13 ? null : px(0.0012 + i * 0.00001)), count),
      ],
      events: [
        {
          kind: "event",
          t: barTime(60),
          window_end: null,
          time_known: true,
          currency: "USD",
          name: "Non-farm payrolls",
          impact: "high",
          event_id: "ev_1",
          source_url: "https://www.bls.gov/",
          provenance: null,
          dataset_version_id: "sha256:abc",
          source: overlaySource("official_calendar", "BLS"),
        },
      ],
      headlines: [],
      sections: {
        series: { available: true, detail: "5 series from 2 indicator(s)." },
        regimes: { available: true, detail: "3 segment(s); classifier warm-up 200 bars." },
        fills: { available: true, detail: "3 fill(s) from 1 paper session(s) on EURUSD." },
        levels: { available: true, detail: "Entry, stop and target of positions open at the end of each session." },
        signals: { available: true, detail: "Gateway attempts from each session's telemetry.jsonl." },
        backtest_fills: {
          available: false,
          detail:
            "The research ledger records experiment metrics, not per-trade fills, so no backtest trade can be drawn.",
        },
        stop_moves: {
          available: false,
          detail:
            "The paper journal persists each open position's current stop but not when it moved; a stop-move history would have to invent times.",
        },
        events: { available: true, detail: "1 scheduled event(s) for EUR, USD." },
        headlines: { available: false, detail: "No headline store has been recorded on this deployment." },
      },
    },
    source: source("mixed", "Bar store (series, regimes), paper journal (signals, fills, levels), official calendar and headline store; computed now."),
    caveats: [],
  };
}

/**
 * Mock the shell plus the chart workstation's two reads for any symbol and
 * timeframe. Each request's URL is recorded so a test can assert the query.
 */
export async function mockChart(
  page: Page,
  options: {
    bars?: (url: URL) => unknown;
    overlays?: (url: URL) => unknown;
    barsStatus?: number;
    overlaysStatus?: number;
  } = {},
) {
  await mockShell(page);
  const requests: URL[] = [];
  await page.route((url) => url.pathname.startsWith("/api/markets/bars/"), (route: Route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const timeframe = url.searchParams.get("timeframe") ?? "H1";
    const body = options.bars ? options.bars(url) : chartBars({ timeframe });
    return route.fulfill({ status: options.barsStatus ?? 200, json: body });
  });
  await page.route((url) => url.pathname.startsWith("/api/markets/overlays/"), (route: Route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const body = options.overlays ? options.overlays(url) : chartOverlays();
    return route.fulfill({ status: options.overlaysStatus ?? 200, json: body });
  });
  await page.route(
    (url) => url.pathname.startsWith("/api/markets/backtests/"),
    (route: Route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      const parts = url.pathname.split("/");
      // /api/markets/backtests/{symbol}[/{run}/trades]
      if (parts.length >= 7 && parts[6] === "trades") {
        const run = decodeURIComponent(parts[5] ?? "");
        return route.fulfill({ json: backtestTrades(run) });
      }
      return route.fulfill({ json: backtestRuns() });
    },
  );
  await mockDrawings(page);
  return requests;
}

// ------------------------------------------------- backtest trades overlay

/** One BacktestRunView (routers/chart.py). */
export function backtestRun(overrides: Record<string, unknown> = {}) {
  const count = (v: number) => figure(v, "backtest", "count");
  return {
    run_id: "bt_0001",
    strategy_id: "donchian_breakout_atr",
    kind: "backtest",
    label: "",
    created_at: "2026-10-12T09:30:00Z",
    timeframe: "H1",
    instruments: ["EURUSD"],
    dataset_version_ids: ["dsv_eurusd_h1_0007abcdef"],
    engine_version: "engine_v3_realism",
    engine_current: true,
    superseded: false,
    gate_verdict: "fail",
    gate_report_id: "val_01",
    trade_count: count(3),
    trades_on_symbol: count(3),
    trades_with_stops: count(3),
    provenance: "backtest",
    ...overrides,
  };
}

/** GET /api/markets/backtests/{symbol}: a current run and a stale one with no stops. */
export function backtestRuns() {
  return {
    items: [
      backtestRun(),
      backtestRun({
        run_id: "bt_old",
        strategy_id: "ichimoku_kumo_trend",
        created_at: "2026-09-01T09:30:00Z",
        engine_version: "engine_v1",
        engine_current: false,
        superseded: true,
        gate_verdict: "not_evaluated",
        gate_report_id: null,
        trades_with_stops: figure(0, "backtest", "count"),
        trade_count: figure(1, "backtest", "count"),
        trades_on_symbol: figure(1, "backtest", "count"),
      }),
    ],
    total: 2,
    offset: 0,
    limit: 200,
    source: source("live", "Research ledger (append-only), read-only."),
    caveats: [],
  };
}

function btTrade(
  id: string,
  direction: "long" | "short",
  entry: number,
  exit: number,
  prices: { entry: number; exit: number; stop: number | null; final?: number; targets: number[] },
  pnl: number,
  moved: string | null = null,
) {
  const p = (v: number | null, why = "not recorded") =>
    v === null
      ? figure(null, "backtest", "", {
          caveats: [{ code: "value_unavailable", severity: "warning", message: why, affects: "", direction: "unknown" }],
        })
      : figure(v, "backtest");
  return {
    trade_id: id,
    strategy_id: "donchian_breakout_atr",
    direction,
    size: figure(0.5, "backtest", "lots"),
    entry_time: barTime(entry),
    exit_time: barTime(exit),
    entry_price: p(prices.entry),
    exit_price: p(prices.exit),
    exit_reason: pnl > 0 ? "take_profit" : "stop_loss",
    net_pnl: figure(pnl, "backtest", "GBP"),
    gross_pnl: figure(pnl + 1.5, "backtest", "GBP"),
    costs: figure(1.5, "backtest", "GBP"),
    bars_held: figure(exit - entry, "backtest", "bars"),
    stop_loss: p(prices.stop, "Run bt_old was filed before the engine recorded per-trade stops."),
    final_stop: p(prices.final ?? prices.stop, "not recorded"),
    take_profit: p(prices.targets[0] ?? null, "The engine planned no take-profit."),
    take_profit_targets: prices.targets.map((t) => figure(t, "backtest")),
    stop_moved_by: moved,
    provenance: "backtest",
    // Trade detail (Wave 2B, routers/chart.py): stored excursions, the tear
    // sheet's R (missing without a stop), charged cost components.
    mae: figure(-6.5, "backtest", "GBP"),
    mfe: figure(pnl > 0 ? pnl + 4 : 3.25, "backtest", "GBP"),
    r_multiple: prices.stop === null ? p(null, "No R: the trade has no recorded initial stop, so its planned risk is unknown.") : figure(pnl / 10, "backtest", "R"),
    mae_r: prices.stop === null ? p(null, "No R.") : figure(-0.65, "backtest", "R"),
    mfe_r: prices.stop === null ? p(null, "No R.") : figure((pnl > 0 ? pnl + 4 : 3.25) / 10, "backtest", "R"),
    spread_cost: figure(1.0, "backtest", "GBP"),
    commission: figure(0, "backtest", "GBP"),
    slippage_cost: figure(0.5, "backtest", "GBP"),
    financing_cost: figure(0, "backtest", "GBP"),
    entry_reason: null,
    entry_reason_detail:
      "The backtest engine does not record an entry reason per trade; the entry follows donchian_breakout_atr's signal on a closed bar.",
  };
}

/** GET /api/markets/backtests/{symbol}/{run}/trades for the 120-bar fixture window. */
export function backtestTrades(run: string) {
  const bar = (i: number) => chartBar(i);
  const trades =
    run === "bt_old"
      ? [btTrade("old_1", "long", 70, 75, { entry: bar(70).o, exit: bar(75).c, stop: null, targets: [] }, 4.0)]
      : [
          btTrade(
            "bt_t1",
            "long",
            30,
            40,
            { entry: bar(30).o, exit: bar(40).c, stop: px(bar(30).o - 0.0012), targets: [px(bar(30).o + 0.0025)] },
            25.5,
          ),
          btTrade(
            "bt_t2",
            "short",
            60,
            66,
            { entry: bar(60).o, exit: bar(66).c, stop: px(bar(60).o + 0.0009), targets: [px(bar(60).o - 0.002)] },
            -12.0,
          ),
          btTrade(
            "bt_t3",
            "long",
            90,
            110,
            {
              entry: bar(90).o,
              exit: bar(110).c,
              stop: px(bar(90).o - 0.0015),
              final: bar(90).o,
              targets: [px(bar(90).o + 0.002), px(bar(90).o + 0.004)],
            },
            41.25,
            "breakeven",
          ),
        ];
  const runRow = run === "bt_old" ? backtestRuns().items[1] : backtestRun();
  return {
    data: {
      symbol: "EURUSD",
      timeframe: "H1",
      window_from: barTime(0),
      window_to: barTime(119),
      run: runRow,
      trades,
      total_on_symbol: figure(trades.length, "backtest", "count"),
      outside_window: figure(0, "backtest", "count"),
      stops_detail:
        run === "bt_old"
          ? "Run bt_old was filed before the engine recorded per-trade stops and targets (2026-10-18); re-run it to see them. Nothing is estimated."
          : "Stops and targets as the engine placed them (initial) and held them at the close (final).",
      account_ccy: "GBP",
    },
    source: source(
      "live",
      `Research ledger backtest ${run} (donchian_breakout_atr, engine engine_v3_realism), dataset dsv_eurusd_h1_0007abcdef; read-only.`,
    ),
    caveats: [],
  };
}

// --------------------------------------------------------------- drawings

export interface DrawingStoreMock {
  version: number;
  drawings: unknown[];
  others: unknown[];
  writes: { method: string; body: unknown }[];
  canEdit: boolean;
}

/**
 * A stateful GET/PUT/DELETE /api/markets/drawings/{symbol}, one set per
 * symbol, held in the test process so it survives a page reload. Versioned
 * like the API: a write on a stale base version is a 409.
 */
export async function mockDrawings(
  page: Page,
  options: { canEdit?: boolean; initial?: unknown[]; others?: unknown[] } = {},
): Promise<Map<string, DrawingStoreMock>> {
  const sets = new Map<string, DrawingStoreMock>();
  const get = (symbol: string) => {
    let set = sets.get(symbol);
    if (!set) {
      set = {
        version: options.initial ? 1 : 0,
        drawings: options.initial ?? [],
        others: options.others ?? [],
        writes: [],
        canEdit: options.canEdit ?? true,
      };
      sets.set(symbol, set);
    }
    return set;
  };
  const view = (symbol: string, set: DrawingStoreMock) => ({
    data: {
      symbol,
      owner: "joe",
      version: set.version,
      updated_at: set.version ? "2026-10-18T10:00:00Z" : null,
      updated_by: set.version ? "joe" : "",
      drawings: set.drawings,
      can_edit: set.canEdit,
      others: set.others,
      max_drawings: 500,
    },
    source: source("live", "Operator drawings: annotations placed by hand, never read by a strategy."),
    caveats: [],
  });
  await page.route(
    (url) => url.pathname.startsWith("/api/markets/drawings/"),
    async (route: Route) => {
      const request = route.request();
      const url = new URL(request.url());
      const symbol = decodeURIComponent(url.pathname.split("/").pop() ?? "");
      const set = get(symbol);
      const method = request.method();
      if (method === "GET") return route.fulfill({ json: view(symbol, set) });
      if (!set.canEdit) {
        return route.fulfill({
          status: 403,
          json: { code: "insufficient_role", detail: "This action requires the operator role.", correlation_id: "cid-d", context: {} },
        });
      }
      const body = method === "PUT" ? (request.postDataJSON() as { base_version: number; drawings: unknown[] }) : null;
      const base = body ? body.base_version : Number(url.searchParams.get("base_version"));
      set.writes.push({ method, body });
      if (base !== set.version) {
        return route.fulfill({
          status: 409,
          json: {
            code: "drawings_conflict",
            detail: "Your drawings changed elsewhere.",
            correlation_id: "cid-c",
            context: { current_version: set.version, base_version: base },
          },
        });
      }
      set.version += 1;
      set.drawings = body ? body.drawings : [];
      return route.fulfill({ json: view(symbol, set) });
    },
  );
  return sets;
}

// --------------------------------------------------- risk & exposure v2

/** One ExposureRow (routers/trading.py), with the API's own utilisation. */
export function exposureRow(
  key: string,
  exposure: number | null,
  limit: number,
  utilisation: number | null,
  options: { provenance?: string; breached?: boolean } = {},
) {
  const provenance = options.provenance ?? "paper";
  return {
    key,
    label: key.slice(key.indexOf(":") + 1),
    exposure_pct: figure(exposure, provenance, "pct"),
    limit_pct: figure(limit, provenance, "pct"),
    utilisation_pct: figure(utilisation, provenance, "pct"),
    breached: options.breached ?? false,
  };
}

/** GET /api/trading/exposure: instrument, strategy and currency buckets, in the API's order. */
export function exposurePage(items?: Record<string, unknown>[]) {
  const list = items ?? [
    exposureRow("instrument:EURUSD", 7.5, 10, 75),
    exposureRow("instrument:USDJPY", 2.4, 10, 24),
    exposureRow("strategy:ichimoku_kumo_trend", 9.9, 15, 66),
    exposureRow("currency:EUR", 7.5, 15, 50),
    exposureRow("currency:JPY", 2.4, 15, 16),
    exposureRow("currency:USD", 14.1, 15, 94),
  ];
  return {
    items: list,
    total: list.length,
    offset: 0,
    limit: list.length,
    source: source("live", "Notional exposure against limit set limits_v1_paper."),
    caveats: [],
  };
}

/** GET /api/trading/positions (PositionRowView rows). */
export function positionsPage() {
  const make = (id: string, instrument: string, pnl: number) => ({
    position_id: id,
    strategy_id: "ichimoku_kumo_trend",
    instrument,
    direction: "long",
    provenance: "paper",
    entry_time: "2026-09-19T08:00:00Z",
    size: figure(0.5, "paper", "lots"),
    entry_price: figure(1.1, "paper"),
    mark_price: figure(1.102, "paper", "", { estimated: true }),
    stop_loss: figure(1.095, "paper"),
    take_profit: figure(1.12, "paper"),
    unrealised_pnl: figure(pnl, "paper", "GBP"),
    distance_to_stop_pct: figure(0.64, "paper", "pct"),
  });
  const items = [make("pos_0001", "EURUSD", 42.5), make("pos_0002", "USDJPY", -12.25)];
  return { items, total: items.length, offset: 0, limit: 100, source: source("live"), caveats: [] };
}

/** Mock the shell plus the Risk & Exposure reads. */
export async function mockRisk(
  page: Page,
  options: { risk?: Record<string, unknown>; exposure?: Record<string, unknown>[] } = {},
) {
  await mockShell(page);
  await page.route(`${API}/api/trading/risk`, (route: Route) =>
    route.fulfill({
      json: { data: riskState(options.risk), source: source("live", "Limit set limits_v1_paper."), caveats: [] },
    }),
  );
  await page.route(`${API}/api/trading/exposure`, (route: Route) =>
    route.fulfill({ json: exposurePage(options.exposure) }),
  );
  await page.route(`${API}/api/trading/positions`, (route: Route) =>
    route.fulfill({ json: positionsPage() }),
  );
  await page.route(`${API}/api/trading/limits`, (route: Route) =>
    route.fulfill({ json: limitBoard({ risk: options.risk, exposure: options.exposure }) }),
  );
}

// ------------------------------------------- operator core (Wave 1A)

/** One LimitRowView (api/limit_board.py): every decision already made. */
export function limitRow(
  key: string,
  family: string,
  label: string,
  options: {
    value?: number | null;
    limit?: number | null;
    utilisation?: number | null;
    headroom?: number | null;
    band?: string;
    breached?: boolean;
    kind?: string | null;
    absent_reason?: string | null;
    provenance?: string;
    heat?: number | null;
  } = {},
) {
  const p = options.provenance ?? "paper";
  const util = options.utilisation ?? null;
  return {
    key,
    family,
    label,
    kind: options.kind ?? null,
    value: options.value === undefined ? null : figure(options.value, p, "pct"),
    limit: options.limit === undefined || options.limit === null ? null : figure(options.limit, p, "pct"),
    utilisation_pct: figure(util, p, "pct"),
    headroom: figure(options.headroom ?? null, p, "pct"),
    band: options.band ?? (util === null ? "absent" : "ok"),
    breached: options.breached ?? false,
    formula: util === null ? null : "used = value ÷ limit",
    absent_reason: options.absent_reason ?? (util === null ? "Not reported by the API." : null),
    see_also: null,
    deep_link: family === "exposure" ? "/trading/exposure" : "/trading/risk",
    heat: options.heat === undefined ? (util === null ? null : Math.min(5, Math.ceil(util / 20))) : options.heat,
  };
}

/** The test-side band rule, for building fixtures (the API's rule, pinned in tests/api). */
export function bandOf(util: number | null, breached: boolean) {
  if (breached) return "breached";
  if (util === null) return "absent";
  if (util >= 90) return "critical";
  if (util >= 70) return "warn";
  return "ok";
}

/** GET /api/trading/limits for given risk and exposure answers, ordered as the API orders. */
export function limitBoard(
  options: { risk?: Record<string, unknown>; exposure?: Record<string, unknown>[] } = {},
) {
  type Fig = { value: number | null };
  const risk = riskState(options.risk) as unknown as {
    limits_version: string;
    daily_loss_pct: Fig;
    max_daily_loss_pct: Fig;
    drawdown_pct: Fig;
    max_drawdown_limit_pct: Fig;
  };
  const rows: ReturnType<typeof limitRow>[] = [
    limitRow("none:account_risk", "account", "Open risk against the account limit"),
    limitRow("none:correlated_exposure", "correlation", "Correlated exposure"),
    limitRow("none:data_quality", "data_quality", "Data-quality tolerances"),
  ];
  const daily = risk.daily_loss_pct.value;
  const dailyLimit = risk.max_daily_loss_pct.value;
  const reasonOf = (fig: Fig & { caveats?: { code: string; message: string }[] }) =>
    fig.caveats?.find((c) => c.code === "value_unavailable")?.message ?? "The API returned no value for this figure.";
  if (daily === null || dailyLimit === null) {
    rows.push(
      limitRow("risk:daily_loss", "loss", "Daily loss", { value: daily, absent_reason: reasonOf(risk.daily_loss_pct) }),
    );
  } else {
    const util = (Math.max(0, -daily) / dailyLimit) * 100;
    const breached = daily < -dailyLimit;
    rows.push(
      limitRow("risk:daily_loss", "loss", "Daily loss", {
        value: daily,
        limit: dailyLimit,
        utilisation: Number(util.toFixed(2)),
        headroom: daily + dailyLimit,
        band: bandOf(util, breached),
        breached,
      }),
    );
  }
  rows.push(limitRow("risk:weekly_loss", "loss", "Weekly loss"));
  const dd = risk.drawdown_pct.value;
  const ddLimit = risk.max_drawdown_limit_pct.value;
  if (dd === null || ddLimit === null) {
    rows.push(limitRow("risk:drawdown", "drawdown", "Total drawdown", { value: dd, absent_reason: reasonOf(risk.drawdown_pct) }));
  } else {
    const util = (dd / ddLimit) * 100;
    const breached = dd > ddLimit;
    rows.push(
      limitRow("risk:drawdown", "drawdown", "Total drawdown", {
        value: dd,
        limit: ddLimit,
        utilisation: Number(util.toFixed(2)),
        headroom: ddLimit - dd,
        band: bandOf(util, breached),
        breached,
      }),
    );
  }
  rows.push(limitRow("risk:margin", "margin", "Margin utilisation", { value: null }));
  const exposure = (exposurePage(options.exposure).items as Record<string, any>[]).map((row) => {
    const util = row.utilisation_pct.value as number | null;
    const kind = String(row.key).split(":")[0];
    return limitRow(`exposure:${row.key}`, "exposure", row.label, {
      kind,
      value: row.exposure_pct.value,
      limit: row.limit_pct.value,
      utilisation: util,
      headroom: util === null || row.exposure_pct.value === null ? null : row.limit_pct.value - row.exposure_pct.value,
      band: bandOf(util, Boolean(row.breached)),
      breached: Boolean(row.breached),
      provenance: row.exposure_pct.provenance,
    });
  });
  rows.push(...exposure);
  const urgency = (r: ReturnType<typeof limitRow>) =>
    r.utilisation_pct.value === null
      ? r.breached
        ? 1e9
        : -Infinity
      : r.breached
        ? 1e9 + r.utilisation_pct.value
        : r.utilisation_pct.value;
  const ordered = [...rows].sort((a, b) => {
    const ua = urgency(a);
    const ub = urgency(b);
    if (ua === ub) return 0;
    if (ua === -Infinity) return 1;
    if (ub === -Infinity) return -1;
    return ub - ua;
  });
  const order = ["account", "loss", "drawdown", "margin", "correlation", "exposure", "data_quality"];
  const titles: Record<string, string> = {
    account: "Account",
    loss: "Daily / weekly loss",
    drawdown: "Drawdown",
    margin: "Margin",
    correlation: "Correlation",
    exposure: "Exposure",
    data_quality: "Data quality",
  };
  const families = order
    .map((id, index) => {
      const members = ordered.filter((r) => r.family === id);
      const worst = members[0] && members[0].band !== "absent" ? members[0] : null;
      return { index, view: { id, title: titles[id], rows: members.map((r) => r.key), worst_key: worst?.key ?? null, source: "none" }, u: worst ? urgency(worst) : -Infinity };
    })
    .filter((f) => f.view.rows.length > 0 || f.view.id === "exposure")
    .sort((a, b) => (a.u === b.u ? a.index - b.index : a.u === -Infinity ? 1 : b.u === -Infinity ? -1 : b.u - a.u))
    .map((f) => f.view);
  const measured = ordered.filter((r) => r.band !== "absent");
  return {
    data: {
      limits_version: risk.limits_version,
      rows: ordered,
      families,
      closest_key: measured[0]?.key ?? null,
      bands: {
        warn_at: figure(70, "paper", "pct"),
        critical_at: figure(90, "paper", "pct"),
        detail: "Platform display bands.",
      },
      sources: { risk: source("live"), exposure: source("live") },
    },
    source: source("live", "Limit set limits_v1_paper."),
    caveats: [],
  };
}

export function marketSessions(open: string[] = ["london", "new_york"]) {
  return [
    ["sydney", "Sydney", "Australia/Sydney", "01:02"],
    ["tokyo", "Tokyo", "Asia/Tokyo", "23:02"],
    ["london", "London", "Europe/London", "15:02"],
    ["new_york", "New York", "America/New_York", "10:02"],
  ].map(([key, label, timezone, local]) => ({
    key,
    label,
    timezone,
    local_time: local,
    open: open.includes(key as string),
    next_change_at: "2026-10-19T16:00:00Z",
    detail: "Conventional hours; public holidays are not modelled.",
  }));
}

export function clockView() {
  return { data: { server_time: "2026-10-18T14:02:31Z", sessions: marketSessions() }, source: source("live"), caveats: [] };
}

/** GET /api/command/summary. */
export function commandSummary(overrides: Record<string, unknown> = {}) {
  const n = (v: number | null) => figure(v, "paper", "count");
  return {
    data: {
      provenance: "paper",
      currency: "GBP",
      equity: figure(25_412.5, "paper", "GBP"),
      balance: figure(25_370, "paper", "GBP"),
      unrealised_pnl: figure(42.5, "paper", "GBP", { estimated: true }),
      day_pnl: figure(-105.25, "paper", "GBP"),
      day_pnl_pct: figure(-0.42, "paper", "pct"),
      week_pnl: figure(310.75, "paper", "GBP"),
      open_positions: n(2),
      bots: { total: n(3), running: n(2), idle: n(0), stale: n(1), halted: n(0), finished: n(0), failed: n(0) },
      research_in_flight: figure(null, "paper", "count", {
        caveats: [{ code: "value_unavailable", severity: "warning", message: "No research orchestrator is attached.", affects: "", direction: "unknown" }],
      }),
      kill_switch_active: false,
      kill_switch_mode: null,
      new_risk_permitted: true,
      pnl_day: "2026-10-16",
      pnl_week_start: "2026-10-12",
      server_time: "2026-10-18T14:02:31Z",
      sessions: marketSessions(),
      links: {
        equity: "/trading/portfolio",
        positions: "/trading/fleet",
        bots: "/trading/fleet",
        day_pnl: "/trading/execution",
        research: "/research",
        risk: "/trading/risk",
      },
      ...overrides,
    },
    source: source("live", "Portfolio, risk state, paper sessions and the kill-switch journal."),
    caveats: [{ code: "day_is_last_bar", severity: "info", message: "Day P&L counts by the account's last bar.", affects: "day_pnl", direction: "unknown" }],
  };
}

/** One FleetRowView (routers/fleet.py). */
export function fleetRow(overrides: Record<string, unknown> = {}) {
  const id = (overrides.session_id as string) ?? "forward-paper_forward_v1";
  const strategy = (overrides.strategy_id as string) ?? "donchian_breakout_atr";
  return {
    session_id: id,
    label: id.replace(/^forward-/, ""),
    strategy_id: strategy,
    instruments: ["EURUSD"],
    timeframe: "H1",
    asset_class: "fx",
    kind: "forward",
    provenance: "paper",
    state: "running",
    state_reason: "Summary written 120s ago; expected at least every 3600s bar.",
    health: "ok",
    health_reason: "No warnings on this session.",
    last_signal: {
      at: "2026-10-18T13:00:20Z",
      outcome: "blocked",
      kind: "open",
      reason: "total_drawdown",
      instrument: "EURUSD",
    },
    last_signal_absent: null,
    open_positions: figure(1, "paper", "count"),
    positions: [],
    unrealised_pnl: figure(-6.87, "paper", "USD", { estimated: true }),
    day_pnl: figure(0, "paper", "USD"),
    realised_pnl: figure(-120.4, "paper", "USD"),
    closed_trades: figure(4, "paper", "count"),
    equity: figure(99_993.13, "paper", "USD", { estimated: true }),
    summary_age: figure(120, "paper", "s"),
    as_of: "2026-10-18T13:00:00Z",
    written_at: "2026-10-18T14:00:31Z",
    lifecycle: "paper",
    content_hash: "abc123def456",
    links: {
      strategy: `/research/strategies/${strategy}`,
      chart: "/markets/EURUSD?tf=H1",
      journal: `/trading/execution?strategy=${strategy}`,
      lifecycle: "/lifecycle/abc123def456",
      session: `/trading/fleet?session=${id}`,
    },
    ...overrides,
  };
}

export function fleetPage(items?: Record<string, unknown>[]) {
  const list = items ?? [
    fleetRow({
      session_id: "forward-paper_forward_v1_eurgbp",
      label: "paper_forward_v1_eurgbp",
      instruments: ["EURGBP"],
      state: "stale",
      state_reason: "Summary last written 9000s ago, past 7500s while its market is open.",
      health: "critical",
      health_reason: "Summary last written 9000s ago.",
      last_signal: null,
      last_signal_absent: "No gateway decision was recorded.",
      summary_age: figure(9000, "paper", "s"),
    }),
    fleetRow(),
    fleetRow({
      session_id: "forward-paper_forward_crypto_v1",
      label: "paper_forward_crypto_v1",
      strategy_id: "donchian_breakout_atr__ccore1",
      instruments: ["BTCGBP", "ETHGBP"],
      timeframe: "H4",
      asset_class: "crypto",
      unrealised_pnl: figure(12.4, "paper", "GBP", { estimated: true }),
      day_pnl: figure(-3.2, "paper", "GBP"),
      realised_pnl: figure(88.1, "paper", "GBP"),
      content_hash: null,
      links: {
        strategy: "/research/strategies/donchian_breakout_atr__ccore1",
        chart: "/markets/BTCGBP?tf=H4",
        journal: "/trading/execution?strategy=donchian_breakout_atr__ccore1",
        lifecycle: null,
        session: "/trading/fleet?session=forward-paper_forward_crypto_v1",
      },
    }),
  ];
  return {
    items: list,
    total: list.length,
    offset: 0,
    limit: list.length,
    source: source("live", "3 paper session(s)."),
    caveats: [],
  };
}

export function attributionView() {
  const row = (key: string, net: number, share: number, link: string, chart: string | null) => ({
    key,
    label: key,
    trades: figure(4, "paper", "count"),
    wins: figure(2, "paper", "count"),
    win_rate: figure(50, "paper", "pct"),
    gross_pnl: figure(net + 10, "paper", "GBP"),
    costs: figure(10, "paper", "GBP"),
    net_pnl: figure(net, "paper", "GBP"),
    day_pnl: figure(0, "paper", "GBP"),
    unrealised_pnl: figure(0, "paper", "GBP"),
    share_of_abs_pct: figure(share, "paper", "pct"),
    link,
    chart,
  });
  return {
    data: {
      provenance: "paper",
      currency: "GBP",
      by_strategy: [
        row("donchian_breakout_atr", -120.4, 57.7, "/research/strategies/donchian_breakout_atr", null),
        row("donchian_breakout_atr__ccore1", 88.1, 42.3, "/research/strategies/donchian_breakout_atr__ccore1", null),
      ],
      by_instrument: [
        row("EURUSD", -120.4, 57.7, "/trading/execution?instrument=EURUSD", "/markets/EURUSD"),
        row("BTCGBP", 88.1, 42.3, "/trading/execution?instrument=BTCGBP", "/markets/BTCGBP"),
      ],
      total_net_pnl: figure(-32.3, "paper", "GBP"),
    },
    source: source("live", "Closed paper trades and open paper positions only."),
    caveats: [],
  };
}

export function boardRow(overrides: Record<string, unknown> = {}) {
  return {
    id: "service:database",
    group: "service",
    name: "database",
    status: "ok",
    detail: "[live] reachable",
    age: figure(0, "paper", "s"),
    stale: false,
    stale_after: figure(null, "paper", "s"),
    deep_link: "/system",
    ...overrides,
  };
}

export function boardView(rows?: Record<string, unknown>[]) {
  const list = rows ?? [
    boardRow({ id: "worker:live@mac:1", group: "worker", name: "live@mac:1", status: "ok", age: figure(12, "paper", "s"), stale_after: figure(120, "paper", "s"), detail: "live: status idle" }),
    boardRow({ id: "bot:forward-paper_forward_v1_eurgbp", group: "bot", name: "paper_forward_v1_eurgbp", status: "down", stale: true, age: figure(9000, "paper", "s"), detail: "STALE: summary last written 9000s ago.", deep_link: "/trading/fleet?session=forward-paper_forward_v1_eurgbp" }),
    boardRow({ id: "bot:forward-paper_forward_v1", group: "bot", name: "paper_forward_v1", status: "ok", age: figure(120, "paper", "s"), detail: "RUNNING", deep_link: "/trading/fleet?session=forward-paper_forward_v1" }),
    boardRow(),
    boardRow({ id: "service:market_data_store", group: "data", name: "market_data_store", detail: "[live] 60 instruments", deep_link: "/markets/data-quality" }),
    boardRow({ id: "broker:venue", group: "broker", name: "broker venue", detail: "No venue configured: correct in paper." }),
    boardRow({ id: "alerts:incidents", group: "alerts", name: "open incidents", status: "degraded", detail: "1 open incident(s) not acknowledged.", deep_link: "/alerts" }),
    boardRow({ id: "check:audit_chain", group: "check", name: "audit_chain", detail: "12 entries, hash chain verified" }),
  ];
  const count = (s: string) => figure(list.filter((r) => r.status === s).length, "paper", "count");
  return {
    data: {
      server_time: "2026-10-18T14:02:31Z",
      status: "down",
      rows: list,
      counts: { ok: count("ok"), degraded: count("degraded"), down: count("down"), absent: count("absent") },
      stale: figure(list.filter((r) => r.stale).length, "paper", "count"),
      advisory: "Not every component is healthy and current.",
    },
    source: source("live", "One API clock."),
    caveats: [],
  };
}

export function alertsView() {
  const rule = (event: string, severity: string, state: string, fired: number, last: string | null, extra: Record<string, unknown> = {}) => ({
    event,
    severity,
    category: "operational",
    condition: `Raised when ${event.replace(/_/g, " ")}.`,
    state,
    last_fired_at: last,
    fired: figure(fired, "paper", "count"),
    open_incident: null,
    ...extra,
  });
  return {
    data: {
      log_configured: true,
      channels: ["console", "file"],
      rules: [
        rule("heartbeat_stale", "error", "firing", 3, "2026-10-18T13:55:00Z", { open_incident: "/system/incidents/inc-1" }),
        rule("data_stale", "warning", "quiet", 1, "2026-10-17T09:00:00Z"),
        rule("worker_down", "critical", "never_fired", 0, null),
        rule("position_opened", "info", "never_fired", 0, null, { category: "trade" }),
      ],
      history: [
        { at: "2026-10-18T13:55:00Z", event: "heartbeat_stale", severity: "error", message: "worker live@mac:1 heartbeat is 130s old", source: "live@mac:1", link: "/system/incidents/inc-1" },
        { at: "2026-10-17T09:00:00Z", event: "data_stale", severity: "warning", message: "EURUSD H1 late", source: "feed", link: null },
      ],
      history_total: figure(4, "paper", "count"),
      thresholds: { worker_stale_after: figure(120, "paper", "s") },
    },
    source: source("live", "Alert taxonomy and the alert log."),
    caveats: [],
  };
}

/** GET /api/search: a tiny entity index answering like routers/search.py. */
export function searchAnswer(q: string) {
  const index = [
    { kind: "instrument", id: "EURUSD", label: "EURUSD", detail: "fx_major · EUR/USD · oanda", href: "/markets/EURUSD" },
    { kind: "instrument", id: "EURGBP", label: "EURGBP", detail: "fx_cross · EUR/GBP · oanda", href: "/markets/EURGBP" },
    { kind: "strategy", id: "ichimoku_kumo_trend", label: "Ichimoku kumo trend", detail: "strategy", href: "/research/strategies/ichimoku_kumo_trend" },
    { kind: "strategy", id: "donchian_breakout_atr", label: "Donchian breakout", detail: "strategy", href: "/research/strategies/donchian_breakout_atr" },
    { kind: "session", id: "forward-paper_forward_v1", label: "paper_forward_v1", detail: "paper bot", href: "/trading/fleet?session=forward-paper_forward_v1" },
    { kind: "run", id: "bt_0001", label: "bt_0001", detail: "backtest · ichimoku_kumo_trend H1", href: "/research/runs/bt_0001" },
    ...["trd_0001", "trd_0002", "trd_0003", "trd_0004"].map((id) => ({
      kind: "trade",
      id,
      label: id,
      detail: "EURUSD",
      href: `/trading/execution?row=${id}`,
    })),
  ];
  const query = q.trim().toLowerCase();
  const tier = (h: { id: string; label: string }) => {
    const i = h.id.toLowerCase();
    const l = h.label.toLowerCase();
    if (query === i || query === l) return 0;
    if (i.startsWith(query) || l.startsWith(query)) return 1;
    if ([...l.split(/[\s_\-/.:]+/), ...i.split(/[\s_\-/.:]+/)].some((w) => w && w.startsWith(query))) return 2;
    if (i.includes(query) || l.includes(query)) return 3;
    return null;
  };
  const kinds = ["instrument", "strategy", "session", "candidate", "run", "trade", "incident"];
  const items = index
    .map((h) => ({ ...h, tier: tier(h) }))
    .filter((h): h is typeof h & { tier: number } => h.tier !== null)
    .sort((a, b) => a.tier - b.tier || kinds.indexOf(a.kind) - kinds.indexOf(b.kind) || a.id.localeCompare(b.id));
  return { items, total: items.length, offset: 0, limit: 20, source: source("live"), caveats: [] };
}

/** Every operator-core read, answered. Called from mockShell. */
export async function mockOperatorCore(page: Page) {
  await page.route(`${API}/api/system/clock`, (route: Route) => route.fulfill({ json: clockView() }));
  await page.route(`${API}/api/command/summary`, (route: Route) => route.fulfill({ json: commandSummary() }));
  await page.route(`${API}/api/trading/fleet`, (route: Route) => route.fulfill({ json: fleetPage() }));
  await page.route(`${API}/api/trading/attribution`, (route: Route) => route.fulfill({ json: attributionView() }));
  await page.route(`${API}/api/trading/limits`, (route: Route) => route.fulfill({ json: limitBoard() }));
  await page.route(`${API}/api/system/board`, (route: Route) => route.fulfill({ json: boardView() }));
  await page.route(`${API}/api/system/alerts*`, (route: Route) => route.fulfill({ json: alertsView() }));
  await page.route(
    (url) => url.pathname === "/api/search",
    (route: Route) => route.fulfill({ json: searchAnswer(new URL(route.request().url()).searchParams.get("q") ?? "") }),
  );
}
