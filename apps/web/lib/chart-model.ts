import { parseUtc, roundedSign } from "./format";
import { SERIES_ROLES, type SeriesRole } from "./chart-theme";
import type {
  Bar,
  FillOverlay,
  LevelOverlay,
  OverlayView,
  Provenance,
  RegimeOverlay,
  SeriesOverlay,
  SignalOverlay,
} from "./types";

/**
 * The chart workstation's drawing model: where each server-computed item goes
 * on the bar axis, and nothing else.
 *
 * Every value drawn is one the API sent. This module never derives a price,
 * an indicator value, a signal or a regime; it only answers "which bar does
 * this timestamp belong to" and "which pane does the API say this series is
 * on", and it counts what it could NOT place, so an item outside the bar
 * window is reported rather than silently dropped. The only arithmetic is on
 * indices and on the SIGN of a P&L figure the platform already computed (for
 * the connector colour, rounded at display precision as everywhere else).
 */

/** UTC seconds, the unit the chart's time axis uses. */
export function toSeconds(iso: string): number {
  return Math.floor(parseUtc(iso).getTime() / 1_000);
}

/** The index of the last bar that opened at or before `t`, or -1. `times` ascending. */
export function barIndexAtOrBefore(times: readonly number[], t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const at = times[mid] as number;
    if (at <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/**
 * Where a point-in-time item belongs: the bar that was open at `t`. Items
 * before the first bar or after the last bar's open are outside the window
 * this chart has bars for, and are not placed (the API filters its journal
 * items to the same window, so this is rare and always counted).
 */
export function placeAt(times: readonly number[], iso: string): number | null {
  if (times.length === 0) return null;
  const t = toSeconds(iso);
  const first = times[0] as number;
  const last = times[times.length - 1] as number;
  if (!Number.isFinite(t) || t < first || t > last) return null;
  const index = barIndexAtOrBefore(times, t);
  return index < 0 ? null : index;
}

export type PaneKind = "price" | "volume" | "indicator" | "state";

export interface ModelPane {
  /** The API's pane string ("price", "state:<indicator>", "<indicator>"), or "volume". */
  id: string;
  kind: PaneKind;
  title: string;
}

/** Line styles cycled within one indicator; the chart maps them to the library's enum. */
export const LINE_STYLES = ["solid", "dashed", "dotted", "large-dashed"] as const;
export type LineStyleName = (typeof LINE_STYLES)[number] | "sparse-dotted";

export interface ModelSeries {
  /** The output column name; unique per indicator. */
  id: string;
  name: string;
  indicatorId: string;
  indicatorKey: string;
  pane: string;
  paneKind: PaneKind;
  /** The legend toggle this series belongs to: one per indicator per pane. */
  group: string;
  role: SeriesRole;
  lineStyle: LineStyleName;
  displayOnly: boolean;
  /** One value per bar, aligned to `ChartModel.times`; null where the API sent none. */
  values: (number | null)[];
  source: SeriesOverlay["source"];
  datasetVersionId: string | null;
  params: Record<string, unknown>;
}

export interface SeriesGroup {
  id: string;
  indicatorId: string;
  pane: string;
  paneKind: PaneKind;
  role: SeriesRole;
  seriesIds: string[];
  /** State and ratio columns start hidden (they open extra panes); everything else shows. */
  defaultVisible: boolean;
}

export interface PlacedSignal {
  index: number;
  item: SignalOverlay;
}

export interface PlacedFill {
  index: number;
  item: FillOverlay;
}

export interface PlacedRegime {
  /** First bar in the run. */
  start: number;
  /** Last bar in the run, inclusive. */
  end: number;
  item: RegimeOverlay;
}

/** An entry and its exit, joined by trade id, as the API reported both. */
export interface TradeConnector {
  tradeId: string;
  entry: PlacedFill;
  exit: PlacedFill;
  provenance: Provenance;
  /** The sign of the platform's net P&L at display precision; null when it sent none. */
  pnlSign: -1 | 0 | 1 | null;
}

export interface Unplaced {
  signals: number;
  fills: number;
  regimes: number;
  /** Series points whose time is not a bar time in this window. */
  seriesPoints: number;
}

export interface ChartModel {
  times: number[];
  bars: Bar[];
  panes: ModelPane[];
  series: ModelSeries[];
  groups: SeriesGroup[];
  signals: PlacedSignal[];
  fills: PlacedFill[];
  levels: LevelOverlay[];
  regimes: PlacedRegime[];
  connectors: TradeConnector[];
  unplaced: Unplaced;
  hasVolume: boolean;
}

function paneKindOf(pane: string): PaneKind {
  if (pane === "price") return "price";
  if (pane.startsWith("state:")) return "state";
  return "indicator";
}

function paneTitle(pane: string): string {
  if (pane === "price") return "Price";
  if (pane === "volume") return "Volume";
  if (pane.startsWith("state:")) return `${pane.slice("state:".length)} (states and ratios)`;
  return pane;
}

function placeRegimes(times: readonly number[], regimes: RegimeOverlay[]) {
  const placed: PlacedRegime[] = [];
  let unplaced = 0;
  const ordered = [...regimes].sort((a, b) => toSeconds(a.from) - toSeconds(b.from));
  const starts = new Set(ordered.map((r) => toSeconds(r.from)));
  for (const item of ordered) {
    const start = placeAt(times, item.from);
    const toIndex = placeAt(times, item.to);
    if (start === null || toIndex === null) {
      unplaced += 1;
      continue;
    }
    // The API's `to` is the first bar of the NEXT run when one follows, and
    // the last bar of the window otherwise (routers/markets.py `overlays`),
    // so a run that another starts at ends one bar earlier.
    const exclusive = starts.has(toSeconds(item.to)) && toSeconds(item.to) !== toSeconds(item.from);
    const end = exclusive ? Math.max(start, toIndex - 1) : toIndex;
    placed.push({ start, end, item });
  }
  return { placed, unplaced };
}

/**
 * Build the model. `bars` must be in ascending time (the API serves them so);
 * `overlays` may be null while it loads or when it failed, and the candles
 * still draw.
 */
export function buildChartModel(
  bars: Bar[],
  overlays: OverlayView | null,
  volumeKind: string | null,
): ChartModel {
  const times = bars.map((b) => toSeconds(b.t));
  const timeIndex = new Map<number, number>();
  times.forEach((t, i) => timeIndex.set(t, i));
  const hasVolume = volumeKind !== null && bars.some((b) => typeof b.v === "number");

  const unplaced: Unplaced = { signals: 0, fills: 0, regimes: 0, seriesPoints: 0 };

  // ---- series: panes as the API names them, roles by indicator order.
  const series: ModelSeries[] = [];
  const groups = new Map<string, SeriesGroup>();
  const roleByIndicator = new Map<string, SeriesRole>();
  const indicatorIds = [...new Set((overlays?.series ?? []).map((s) => s.indicator_id))].sort();
  indicatorIds.forEach((id, i) => {
    roleByIndicator.set(id, SERIES_ROLES[i % SERIES_ROLES.length] as SeriesRole);
  });
  const styleCursor = new Map<string, number>();
  for (const s of overlays?.series ?? []) {
    const paneKind = paneKindOf(s.pane);
    const groupId = `${s.indicator_id}|${s.pane}`;
    const role = roleByIndicator.get(s.indicator_id) as SeriesRole;
    let group = groups.get(groupId);
    if (!group) {
      group = {
        id: groupId,
        indicatorId: s.indicator_id,
        pane: s.pane,
        paneKind,
        role,
        seriesIds: [],
        defaultVisible: paneKind !== "state",
      };
      groups.set(groupId, group);
    }
    let lineStyle: LineStyleName;
    if (s.display_only) {
      lineStyle = "sparse-dotted";
    } else {
      const n = styleCursor.get(groupId);
      const k = n === undefined ? 0 : n;
      styleCursor.set(groupId, k + 1);
      lineStyle = LINE_STYLES[k % LINE_STYLES.length] as LineStyleName;
    }
    const values: (number | null)[] = new Array<number | null>(times.length).fill(null);
    for (const point of s.points) {
      const index = timeIndex.get(toSeconds(point.t));
      if (index === undefined) {
        unplaced.seriesPoints += 1;
        continue;
      }
      values[index] = point.v;
    }
    const id = `${s.indicator_id}:${s.name}`;
    group.seriesIds.push(id);
    series.push({
      id,
      name: s.name,
      indicatorId: s.indicator_id,
      indicatorKey: s.indicator_key,
      pane: s.pane,
      paneKind,
      group: groupId,
      role,
      lineStyle,
      displayOnly: s.display_only,
      values,
      source: s.source,
      datasetVersionId: s.dataset_version_id,
      params: s.params,
    });
  }

  // ---- panes: price, volume (when the bars carry it), indicator panes, state panes.
  const panes: ModelPane[] = [{ id: "price", kind: "price", title: paneTitle("price") }];
  if (hasVolume) panes.push({ id: "volume", kind: "volume", title: paneTitle("volume") });
  const extra = [...new Set(series.map((s) => s.pane))].filter((p) => p !== "price");
  const byKind = (kind: PaneKind) => extra.filter((p) => paneKindOf(p) === kind).sort();
  for (const pane of [...byKind("indicator"), ...byKind("state")]) {
    panes.push({ id: pane, kind: paneKindOf(pane), title: paneTitle(pane) });
  }

  // ---- markers.
  const signals: PlacedSignal[] = [];
  for (const item of overlays?.signals ?? []) {
    const index = placeAt(times, item.t);
    if (index === null) unplaced.signals += 1;
    else signals.push({ index, item });
  }
  const fills: PlacedFill[] = [];
  for (const item of overlays?.fills ?? []) {
    const index = placeAt(times, item.t);
    if (index === null) unplaced.fills += 1;
    else fills.push({ index, item });
  }

  // ---- entry-to-exit connectors, only where the API sent both ends.
  const entries = new Map<string, PlacedFill>();
  for (const fill of fills) {
    if (fill.item.role === "entry") entries.set(`${fill.item.session_id}|${fill.item.trade_id}`, fill);
  }
  const connectors: TradeConnector[] = [];
  for (const exit of fills) {
    if (exit.item.role !== "exit") continue;
    const entry = entries.get(`${exit.item.session_id}|${exit.item.trade_id}`);
    if (!entry) continue;
    const pnl = exit.item.net_pnl;
    connectors.push({
      tradeId: exit.item.trade_id,
      entry,
      exit,
      provenance: exit.item.provenance,
      pnlSign: pnl === null || pnl.value === null ? null : roundedSign(pnl.value, pnl.unit),
    });
  }

  const regimes = placeRegimes(times, overlays?.regimes ?? []);
  unplaced.regimes = regimes.unplaced;

  return {
    times,
    bars,
    panes,
    series,
    groups: [...groups.values()],
    signals,
    fills,
    levels: overlays?.levels ?? [],
    regimes: regimes.placed,
    connectors,
    unplaced,
    hasVolume,
  };
}

/** The pane index a series is drawn in, given which panes are showing. */
export function visiblePanes(model: ChartModel, hidden: ReadonlySet<string>): ModelPane[] {
  const shown = new Set(
    model.series.filter((s) => !hidden.has(s.group)).map((s) => s.pane),
  );
  return model.panes.filter((p) => p.kind === "price" || p.kind === "volume" || shown.has(p.id));
}

/** Which groups start hidden: state and ratio columns (they open extra panes). */
export function defaultHidden(model: ChartModel): Set<string> {
  return new Set(model.groups.filter((g) => !g.defaultVisible).map((g) => g.id));
}
