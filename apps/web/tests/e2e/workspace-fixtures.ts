import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page, Route } from "@playwright/test";
import { API, chartBars, source } from "./fixtures";

/**
 * Wave 2A fixtures: the workspace, the market monitors and the alert
 * notifications. The JSON beside this file (workspace-fixtures/*.json) is
 * written by the REAL backend (workspace-fixtures/generate.py), so the specs
 * drive the screens with payloads the API can send. The layout store here is
 * a small in-memory model of routers/workspace.py: versions, 409 on a stale
 * base version, and a revision per accepted write.
 */
const DIR = join(__dirname, "workspace-fixtures");

export function wsFixture<T = Record<string, unknown>>(name: string): T {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8")) as T;
}

interface StoredLayout {
  version: number;
  layout: Record<string, unknown> | null;
  updated_at: string;
  history: { version: number; action: string; at: string; actor: string; panel_count: number; kinds: string[] }[];
}

export interface WorkspaceMock {
  layouts: Map<string, StoredLayout>;
  writes: { name: string; base: number; action: string; layout: Record<string, unknown> }[];
  /** Bump a layout's version as if another tab had saved it. */
  saveElsewhere: (name: string) => void;
}

const panelsOf = (layout: Record<string, unknown> | null) =>
  ((layout?.panels as { kind: string }[] | undefined) ?? []);

export async function mockWorkspace(
  page: Page,
  options: { saved?: Record<string, Record<string, unknown>>; canEdit?: boolean } = {},
): Promise<WorkspaceMock> {
  const layouts = new Map<string, StoredLayout>();
  const writes: WorkspaceMock["writes"] = [];
  const at = (n: number) => new Date(Date.UTC(2026, 9, 19, 9, 0, n)).toISOString();
  for (const [name, layout] of Object.entries(options.saved ?? {})) {
    layouts.set(name, {
      version: 1,
      layout,
      updated_at: at(0),
      history: [{ version: 1, action: "save", at: at(0), actor: "joe", panel_count: panelsOf(layout).length, kinds: [] }],
    });
  }
  const view = (name: string, s: StoredLayout) => ({
    data: {
      name,
      version: s.version,
      updated_at: s.updated_at,
      updated_by: "joe",
      layout: s.layout,
      can_edit: options.canEdit ?? true,
    },
    source: source("live", "Operator workspace documents (fixture)."),
    caveats: [],
  });

  await page.route(`${API}/api/workspace/panels`, (route) => route.fulfill({ json: wsFixture("panels") }));
  await page.route(`${API}/api/workspace/presets`, (route) => route.fulfill({ json: wsFixture("presets") }));
  await page.route(`${API}/api/workspace/layouts`, (route) => {
    const items = [...layouts.entries()]
      .filter(([, s]) => s.layout !== null)
      .map(([name, s]) => ({
        name,
        version: s.version,
        updated_at: s.updated_at,
        updated_by: "joe",
        panel_count: panelsOf(s.layout).length,
        kinds: [...new Set(panelsOf(s.layout).map((p) => p.kind))].sort(),
      }));
    return route.fulfill({
      json: { items, total: items.length, offset: 0, limit: 100, source: source("live"), caveats: [] },
    });
  });
  await page.route(
    (url) => url.origin === API && /^\/api\/workspace\/layouts\/[^/]+(\/history)?$/.test(url.pathname),
    async (route: Route) => {
      const url = new URL(route.request().url());
      const parts = url.pathname.split("/");
      const name = decodeURIComponent(parts[4] ?? "");
      const method = route.request().method();
      const stored = layouts.get(name);
      if (parts[5] === "history") {
        const items = [...(stored?.history ?? [])].reverse();
        return route.fulfill({ json: { items, total: items.length, offset: 0, limit: 50, source: source("live"), caveats: [] } });
      }
      if (method === "GET") {
        if (!stored || stored.layout === null) {
          return route.fulfill({
            status: 404,
            json: { code: "layout_not_found", detail: `No layout is saved as '${name}'.`, correlation_id: "cid-ws", context: { version: stored?.version ?? 0 } },
          });
        }
        return route.fulfill({ json: view(name, stored) });
      }
      const current = stored?.version ?? 0;
      const base = method === "DELETE" ? Number(url.searchParams.get("base_version")) : (route.request().postDataJSON() as { base_version: number }).base_version;
      if (base !== current) {
        return route.fulfill({
          status: 409,
          json: {
            code: "workspace_conflict",
            detail: `Layout '${name}' changed elsewhere (now version ${current}, you edited version ${base}). Nothing was overwritten; reload to see the newer one.`,
            correlation_id: "cid-ws-409",
            context: { current_version: current, base_version: base },
          },
        });
      }
      const body = method === "DELETE" ? null : (route.request().postDataJSON() as { layout: Record<string, unknown>; action: string });
      const version = current + 1;
      const next: StoredLayout = {
        version,
        layout: body?.layout ?? null,
        updated_at: at(version),
        history: [
          ...(stored?.history ?? []),
          { version, action: body?.action ?? "delete", at: at(version), actor: "joe", panel_count: panelsOf(body?.layout ?? null).length, kinds: [] },
        ],
      };
      layouts.set(name, next);
      if (body) writes.push({ name, base, action: body.action, layout: body.layout });
      if (method === "DELETE") return route.fulfill({ status: 204, body: "" });
      return route.fulfill({ json: view(name, next) });
    },
  );
  // The chart panel reads closed bars.
  await page.route((url) => url.origin === API && url.pathname.startsWith("/api/markets/bars/"), (route) => {
    const url = new URL(route.request().url());
    const symbol = decodeURIComponent(url.pathname.split("/").pop() ?? "EURUSD");
    return route.fulfill({ json: chartBars({ symbol, timeframe: url.searchParams.get("timeframe") ?? "H1", count: 60 }) });
  });
  return {
    layouts,
    writes,
    saveElsewhere(name: string) {
      const s = layouts.get(name);
      if (!s) return;
      s.version += 1;
      s.history.push({ version: s.version, action: "save", at: at(s.version), actor: "joe", panel_count: panelsOf(s.layout).length, kinds: [] });
    },
  };
}

/** The preset layout with this key, from the backend's own answer. */
export function presetLayout(key: string): Record<string, unknown> {
  const page = wsFixture<{ items: { key: string; layout: Record<string, unknown> }[] }>("presets");
  const found = page.items.find((p) => p.key === key);
  if (!found) throw new Error(`no preset ${key}`);
  return JSON.parse(JSON.stringify(found.layout)) as Record<string, unknown>;
}

export interface MonitorMock {
  watchlistWrites: { name: string; body: Record<string, unknown> }[];
  columnSetWrites: Record<string, unknown>[];
}

/** The market monitors (watchlists, quotes, column sets, format rules), from the backend-written JSON. */
export async function mockMonitors(page: Page): Promise<MonitorMock> {
  const mock: MonitorMock = { watchlistWrites: [], columnSetWrites: [] };
  let columnSets = wsFixture<{ data: Record<string, unknown> }>("column_sets_watchlist");
  await page.route(`${API}/api/workspace/format-rules/watchlist`, (route) => route.fulfill({ json: wsFixture("format_rules_watchlist") }));
  await page.route(`${API}/api/markets/watchlists`, (route) => route.fulfill({ json: wsFixture("watchlists") }));
  await page.route((url) => url.origin === API && /^\/api\/markets\/watchlists\/[^/]+\/quotes$/.test(url.pathname), (route) =>
    route.fulfill({ json: wsFixture("watchlist_majors_24h") }),
  );
  await page.route((url) => url.origin === API && url.pathname === "/api/markets/quotes", (route) => route.fulfill({ json: wsFixture("quotes_fx") }));
  await page.route((url) => url.origin === API && /^\/api\/markets\/watchlists\/[^/]+$/.test(url.pathname), (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "");
    const body = route.request().method() === "PUT" ? (route.request().postDataJSON() as Record<string, unknown>) : {};
    mock.watchlistWrites.push({ name, body });
    return route.fulfill({
      json: {
        items: [{ name, version: 1, updated_at: "2026-10-19T09:00:00Z", symbols: body.symbols ?? [], column_set: null, builtin: false, can_edit: true }],
        total: 1, offset: 0, limit: 1, source: source("live"), caveats: [],
      },
    });
  });
  await page.route((url) => url.origin === API && url.pathname === "/api/markets/news", (route) => {
    const url = new URL(route.request().url());
    return route.fulfill({ json: wsFixture(url.searchParams.get("instrument") ? "news_eurusd" : "news_24h") });
  });
  await page.route(`${API}/api/workspace/column-sets/watchlist`, (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { base_version: number; sets: unknown[]; default: string | null };
      mock.columnSetWrites.push(body);
      columnSets = {
        ...columnSets,
        data: { ...columnSets.data, version: body.base_version + 1, sets: body.sets, default: body.default },
      };
    }
    return route.fulfill({ json: columnSets });
  });
  return mock;
}
