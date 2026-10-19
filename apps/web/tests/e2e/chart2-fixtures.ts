import type { Page, Route } from "@playwright/test";
import { barTime, chartBar, chartOverlays, figure, source } from "./fixtures";

/**
 * Wave 2B chart routes, mocked in the shapes src/fiboki/api/routers/
 * chart_workstation.py serves: replay, compare, data window, calendar events,
 * templates, price alerts and the snapshot stamp. Registered by mockChart and
 * mockEverything, so every chart test answers them.
 */

const N = 120;

/** GET /api/markets/replay/{symbol}: bars closed by the cursor (H1, open + 1h <= cursor). */
export function replayView(cursor: string, run: string | null) {
  const at = Date.parse(cursor);
  const closed = Array.from({ length: N }, (_, i) => i).filter((i) => Date.parse(barTime(i)) + 3_600_000 <= at);
  const n = closed.length;
  const bars = closed.slice(-300).map((i) => chartBar(i));
  const overlays = chartOverlays();
  const lastT = n > 0 ? Date.parse(barTime(n - 1)) : null;
  const keep = <T extends { t: string }>(items: T[]) => items.filter((x) => Date.parse(x.t) <= at);
  const trades =
    run && n > 31
      ? [
          {
            trade_id: "rp_1",
            strategy_id: "donchian_breakout_atr",
            direction: "long",
            size: figure(0.5, "backtest", "lots"),
            entry_time: barTime(30),
            entry_price: figure(chartBar(30).o, "backtest"),
            stop_loss: figure(chartBar(30).o - 0.0012, "backtest"),
            take_profit_targets: [figure(chartBar(30).o + 0.0025, "backtest")],
            open_at_cursor: n <= 40,
            exit_time: n > 40 ? barTime(40) : null,
            exit_price: n > 40 ? figure(chartBar(40).c, "backtest") : null,
            exit_reason: n > 40 ? "take_profit" : null,
            net_pnl: n > 40 ? figure(25.5, "backtest", "GBP") : null,
            final_stop: n > 40 ? figure(chartBar(30).o - 0.0012, "backtest") : null,
            provenance: "backtest",
          },
        ]
      : [];
  return {
    data: {
      symbol: "EURUSD",
      timeframe: "H1",
      cursor: new Date(at).toISOString().replace(".000Z", "Z"),
      dataset_version_id: "dsv_eurusd_h1_0007abcdef",
      volume_kind: "tick_volume",
      bars,
      last_close: lastT === null ? null : new Date(lastT + 3_600_000).toISOString().replace(".000Z", "Z"),
      next_cursor: n < N ? new Date(Date.parse(barTime(n)) + 3_600_000).toISOString().replace(".000Z", "Z") : null,
      prev_cursor: n >= 2 ? new Date(Date.parse(barTime(n - 2)) + 3_600_000).toISOString().replace(".000Z", "Z") : null,
      first_bar: barTime(0),
      last_bar: barTime(N - 1),
      bars_closed: n,
      bars_total: N,
      overlays: {
        ...overlays.data,
        window_to: n > 0 ? barTime(n - 1) : null,
        signals: keep(overlays.data.signals),
        fills: keep(overlays.data.fills),
        events: keep(overlays.data.events),
        levels: [],
        regimes: [],
        series: overlays.data.series
          .filter((s) => !s.display_only)
          .map((s) => ({ ...s, points: s.points.filter((p) => Date.parse(p.t) + 3_600_000 <= at) })),
      },
      run_id: run,
      trades,
      trades_detail: run ? `Backtest ${run}: ${trades.length} trade(s) entered by the cursor.` : "No backtest run selected.",
    },
    source: source("live", "Replay fixture."),
    caveats: [],
  };
}

export function compareView(base: string, other: string) {
  return {
    data: {
      base,
      symbol: other,
      timeframe: "H1",
      unit: "percent",
      anchor_time: barTime(0),
      anchor_close: figure(1.3, "backtest"),
      base_dataset_version_id: "dsv_eurusd_h1_0007abcdef",
      dataset_version_id: "dsv_gbpusd_h1_0003",
      points: Array.from({ length: N }, (_, i) => ({ t: barTime(i), v: i === 7 ? null : Number((i * 0.01).toFixed(2)) })),
      unmatched: 1,
    },
    source: source("live", "Compare fixture."),
    caveats: [],
  };
}

export function dataWindowView(t: string) {
  const i = Math.max(0, Math.min(N - 1, Math.floor((Date.parse(t) - Date.parse(barTime(0))) / 3_600_000)));
  const overlays = chartOverlays();
  return {
    data: {
      symbol: "EURUSD",
      timeframe: "H1",
      t: barTime(i),
      t_end: barTime(i + 1),
      dataset_version_id: "dsv_eurusd_h1_0007abcdef",
      bar: chartBar(i),
      values: overlays.data.series.map((s) => ({
        indicator_id: s.indicator_id,
        name: s.name,
        pane: s.pane,
        value: s.points[i]?.v ?? null,
        display_only: s.display_only,
      })),
      regime: null,
      signals: [],
      fills: [],
      levels: [],
      events: [],
      headlines: [],
      sections: overlays.data.sections,
    },
    source: source("live", "Data window fixture."),
    caveats: [],
  };
}

export const EVENT = {
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
  dataset_version_id: "official_calendar:1.1.0 sha256:abc",
  source: { kind: "official_calendar", detail: "BLS (official calendar 1.1.0)" },
};

export interface Wave2BMock {
  templates: { name: string; version: number; updated_at: string; template: unknown }[];
  alerts: unknown[];
  requests: URL[];
}

export async function mockChartWave2B(page: Page, options: { canEdit?: boolean } = {}): Promise<Wave2BMock> {
  const state: Wave2BMock = { templates: [], alerts: [], requests: [] };
  const env = (data: unknown, caveats: unknown[] = []) => ({ data, source: source("live", "Wave 2B fixture."), caveats });
  const templatesView = () =>
    env({ owner: "joe", templates: state.templates, can_edit: options.canEdit ?? true, max_templates: 50 });
  await page.route(
    (url) =>
      url.pathname.startsWith("/api/markets/replay/") ||
      url.pathname.startsWith("/api/markets/compare/") ||
      url.pathname.startsWith("/api/markets/datawindow/") ||
      url.pathname.startsWith("/api/markets/events/") ||
      url.pathname.startsWith("/api/chart/") ||
      url.pathname === "/api/alerts/price",
    async (route: Route) => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      state.requests.push(url);
      const p = url.pathname;
      if (p.startsWith("/api/markets/replay/")) {
        return route.fulfill({ json: replayView(url.searchParams.get("cursor") ?? barTime(100), url.searchParams.get("run")) });
      }
      if (p.startsWith("/api/markets/compare/")) {
        return route.fulfill({ json: compareView(decodeURIComponent(p.split("/")[4] ?? ""), url.searchParams.get("with") ?? "") });
      }
      if (p.startsWith("/api/markets/datawindow/")) {
        return route.fulfill({ json: dataWindowView(url.searchParams.get("t") ?? barTime(0)) });
      }
      if (p.startsWith("/api/markets/events/")) {
        const id = url.searchParams.get("event_id");
        if (id && id !== EVENT.event_id) {
          return route.fulfill({
            status: 404,
            json: { code: "unknown_event", detail: `Official calendar 1.1.0 has no event '${id}'.`, correlation_id: "cid-ev", context: {} },
          });
        }
        return route.fulfill({
          json: env({
            symbol: "EURUSD",
            calendar_version: "1.1.0",
            calendar_digest: "sha256:abc",
            currencies: ["EUR", "USD"],
            window_from: null,
            window_to: null,
            events: [],
            focus: id ? EVENT : null,
            declared_start: "2024-01-01T00:00:00Z",
            declared_end: "2027-01-01T00:00:00Z",
          }),
        });
      }
      if (p.startsWith("/api/chart/stamp/")) {
        const dataset = url.searchParams.get("dataset_version_id") ?? "";
        return route.fulfill({
          json: env({
            symbol: "EURUSD",
            timeframe: url.searchParams.get("timeframe") ?? "H1",
            dataset_version_id: dataset,
            dataset_current: true,
            latest_dataset_version_id: dataset,
            build_sha: "deadbeef",
            api_version: "2.0.0",
            execution_mode: "paper",
            generated_at: "2026-10-19T02:30:00Z",
            text: `EURUSD H1 · dataset ${dataset} · build deadbeef · 2026-10-19 02:30:00 UTC · PAPER`,
          }),
        });
      }
      if (p === "/api/chart/templates") return route.fulfill({ json: templatesView() });
      if (p.startsWith("/api/chart/templates/")) {
        const name = decodeURIComponent(p.slice("/api/chart/templates/".length));
        if (method === "PUT") {
          const body = route.request().postDataJSON() as { base_version: number; template: unknown };
          const existing = state.templates.find((t) => t.name === name);
          if ((existing?.version ?? 0) !== body.base_version) {
            return route.fulfill({
              status: 409,
              json: { code: "template_conflict", detail: "Stale.", correlation_id: "cid-t", context: {} },
            });
          }
          const row = { name, version: body.base_version + 1, updated_at: "2026-10-19T02:30:00Z", template: body.template };
          state.templates = [...state.templates.filter((t) => t.name !== name), row].sort((a, b) => a.name.localeCompare(b.name));
          return route.fulfill({ json: templatesView() });
        }
        if (method === "DELETE") {
          state.templates = state.templates.filter((t) => t.name !== name);
          return route.fulfill({ json: templatesView() });
        }
      }
      if (p === "/api/alerts/price") {
        const caveats = [
          {
            code: "price_alerts_not_evaluated",
            severity: "warning",
            message: "Stored, not watched.",
            affects: "alerts",
            direction: "optimistic",
          },
        ];
        if (method === "POST") {
          const body = route.request().postDataJSON() as { symbol: string; price: number; drawing_id: string; origin: string };
          const ref = chartBar(N - 1).c;
          state.alerts.push({
            alert_id: `pa_${state.alerts.length + 1}`,
            owner: "joe",
            symbol: body.symbol,
            price: body.price,
            condition: body.price > ref ? "cross_above" : "cross_below",
            reference_close: ref,
            reference_time: barTime(N - 1),
            dataset_version_id: "dsv_eurusd_h1_0007abcdef",
            drawing_id: body.drawing_id,
            origin: body.origin,
            note: "",
            created_at: "2026-10-19T02:30:00Z",
            status: "armed",
          });
          return route.fulfill({
            status: 201,
            json: env({ alerts: state.alerts, evaluated: false, evaluator_detail: "Stored, not watched." }, caveats),
          });
        }
        return route.fulfill({ json: env({ alerts: state.alerts, evaluated: false, evaluator_detail: "Stored, not watched." }, caveats) });
      }
      return route.fallback();
    },
  );
  return state;
}
