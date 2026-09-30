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
  return requests;
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
}
