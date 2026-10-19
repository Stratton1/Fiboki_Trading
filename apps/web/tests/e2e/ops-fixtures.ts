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
