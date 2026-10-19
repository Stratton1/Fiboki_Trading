import { parseUtc, roundedSign } from "./format";
import { SERIES_ROLES, type SeriesRole } from "./chart-theme";
import type { ReplayTrade } from "./chart-contract";
import type {
  BacktestTradeRow,
  BacktestTradesView,
  Bar,
  Figure,
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

// ------------------------------------------------------------ trades

/**
 * One trade's protection, drawn as a short horizontal segment that spans the
 * trade's life on the bar axis: the initial stop and every planned target
 * from the entry bar to the exit bar, as the platform recorded them. A stop
 * the engine moved (breakeven or a trail) is NOT drawn as a path: the engine
 * records which rule moved it and where it ended, not when, so the final stop
 * is a one-bar tick at the exit (`final_stop`), never a line across time the
 * platform cannot vouch for.
 */
export interface TradeSegment {
  role: "stop" | "final_stop" | "target";
  price: number;
  /** First and last bar index the segment covers, inclusive. */
  from: number;
  to: number;
  /** Leg number for a target (1-based), when there are several. */
  leg: number | null;
}

/** Where a trade sits on this chart's bars, with what the API said about it. */
export interface PlacedTrade {
  /** Unique across sources: `bt|<run>|<trade>` or `paper|<session>|<trade>`. */
  key: string;
  source: "backtest" | "paper";
  tradeId: string;
  direction: "long" | "short" | null;
  provenance: Provenance;
  entryIndex: number;
  exitIndex: number;
  /** False when the entry (or exit) is outside the bar window and was clamped to its edge. */
  entryInWindow: boolean;
  exitInWindow: boolean;
  entryPrice: number | null;
  exitPrice: number | null;
  pnlSign: -1 | 0 | 1 | null;
  segments: TradeSegment[];
  /** The backtest row as the API sent it (tooltip, table). */
  row: BacktestTradeRow | null;
  /** A paper trade's two fills as the journal sent them (tooltip, table). */
  fills: { entry: FillOverlay; exit: FillOverlay } | null;
  /** Paper: the session label; backtest: the run id. */
  origin: string;
}

export interface PlacedTrades {
  trades: PlacedTrade[];
  /** Trades the API sent that lie entirely outside this chart's bars. */
  unplaced: number;
}

/**
 * The bar span of a trade on this chart: the bar open at entry to the bar
 * open at exit, clamped to the window. Null when the trade is entirely
 * outside it (exited before the first bar, or entered after the last).
 */
export function tradeSpan(
  times: readonly number[],
  entryIso: string,
  exitIso: string,
): { from: number; to: number; entryIn: boolean; exitIn: boolean } | null {
  const n = times.length;
  if (n === 0) return null;
  const first = times[0] as number;
  const last = times[n - 1] as number;
  const entry = toSeconds(entryIso);
  const exit = toSeconds(exitIso);
  if (!Number.isFinite(entry) || !Number.isFinite(exit) || exit < first || entry > last) return null;
  const entryIn = entry >= first;
  const exitIn = exit <= last;
  const from = entryIn ? Math.max(0, barIndexAtOrBefore(times, entry)) : 0;
  const to = exitIn ? Math.max(from, barIndexAtOrBefore(times, exit)) : n - 1;
  return { from, to, entryIn, exitIn };
}

/** SL/TP segments for one backtest trade, from the figures the API sent. */
export function backtestSegments(row: BacktestTradeRow, from: number, to: number): TradeSegment[] {
  const out: TradeSegment[] = [];
  const stop = row.stop_loss.value;
  if (stop !== null) out.push({ role: "stop", price: stop, from, to, leg: null });
  const finalStop = row.final_stop.value;
  if (finalStop !== null && stop !== null && finalStop !== stop) {
    out.push({ role: "final_stop", price: finalStop, from: to, to, leg: null });
  }
  const targets = row.take_profit_targets
    .map((t) => t.value)
    .filter((v): v is number => v !== null);
  targets.forEach((price, i) =>
    out.push({ role: "target", price, from, to, leg: targets.length > 1 ? i + 1 : null }),
  );
  return out;
}

/** Place a backtest run's trades on the bars. Every number is the API's. */
export function placeBacktestTrades(
  times: readonly number[],
  view: BacktestTradesView | null,
): PlacedTrades {
  if (!view) return { trades: [], unplaced: 0 };
  return placeBacktestRows(times, view.run.run_id, view.trades);
}

/** A figure the platform did not supply (yet), with the trade's own provenance. */
function absent(provenance: Provenance, unit = ""): Figure {
  return { value: null, provenance, unit, as_of: null, sample_size: null, caveats: [], estimated: false };
}

/**
 * A replayed trade as a table and tooltip row. A trade still open at the
 * cursor has no exit yet: its exit is placed at the cursor's last bar with no
 * price, no P&L and no final stop (the API withheld them), and its reason
 * says so. Nothing is filled in from the trade's stored future.
 */
export function replayRow(t: ReplayTrade, cursor: string): BacktestTradeRow {
  const p = t.provenance;
  return {
    trade_id: t.trade_id,
    strategy_id: t.strategy_id,
    direction: t.direction,
    size: t.size,
    entry_time: t.entry_time,
    exit_time: t.exit_time ?? cursor,
    entry_price: t.entry_price,
    exit_price: t.exit_price ?? absent(p),
    exit_reason: t.open_at_cursor ? "open at cursor" : (t.exit_reason ?? ""),
    net_pnl: t.net_pnl ?? absent(p),
    gross_pnl: absent(p),
    costs: absent(p),
    bars_held: absent(p, "bars"),
    stop_loss: t.stop_loss,
    final_stop: t.final_stop ?? absent(p),
    take_profit: t.take_profit_targets[0] ?? absent(p),
    take_profit_targets: t.take_profit_targets,
    stop_moved_by: null,
    provenance: p,
  };
}

/** Place backtest rows (one run's) on the bars. Every number is the API's. */
export function placeBacktestRows(
  times: readonly number[],
  runId: string,
  rows: readonly BacktestTradeRow[],
): PlacedTrades {
  const trades: PlacedTrade[] = [];
  let unplaced = 0;
  for (const row of rows) {
    const span = tradeSpan(times, row.entry_time, row.exit_time);
    if (span === null) {
      unplaced += 1;
      continue;
    }
    const pnl = row.net_pnl;
    trades.push({
      key: `bt|${runId}|${row.trade_id}`,
      source: "backtest",
      tradeId: row.trade_id,
      direction: row.direction,
      provenance: row.provenance,
      entryIndex: span.from,
      exitIndex: span.to,
      entryInWindow: span.entryIn,
      exitInWindow: span.exitIn,
      entryPrice: row.entry_price.value,
      exitPrice: row.exit_price.value,
      pnlSign: pnl.value === null ? null : roundedSign(pnl.value, pnl.unit),
      segments: backtestSegments(row, span.from, span.to),
      row,
      fills: null,
      origin: runId,
    });
  }
  return { trades, unplaced };
}

/**
 * The paper journal's closed trades, from the connectors the model already
 * joined, with each trade's recorded stop and target (the API's levels with a
 * `to`, keyed by trade id) as segments over its life.
 */
export function placePaperTrades(model: ChartModel): PlacedTrade[] {
  const closedLevels = new Map<string, LevelOverlay[]>();
  for (const level of model.levels) {
    if (level.to === null) continue;
    const key = `${level.session_id}|${level.position_id}`;
    const list = closedLevels.get(key);
    if (list) list.push(level);
    else closedLevels.set(key, [level]);
  }
  return model.connectors.map((c) => {
    const from = c.entry.index;
    const to = Math.max(from, c.exit.index);
    const segments: TradeSegment[] = [];
    for (const level of closedLevels.get(`${c.exit.item.session_id}|${c.tradeId}`) ?? []) {
      if (level.price.value === null) continue;
      segments.push({
        role: level.role === "target" ? "target" : "stop",
        price: level.price.value,
        from,
        to,
        leg: null,
      });
    }
    return {
      key: `paper|${c.exit.item.session_id}|${c.tradeId}`,
      source: "paper" as const,
      tradeId: c.tradeId,
      direction: c.entry.item.side === "buy" ? "long" : "short",
      provenance: c.provenance,
      entryIndex: from,
      exitIndex: to,
      entryInWindow: true,
      exitInWindow: true,
      entryPrice: c.entry.item.price.value,
      exitPrice: c.exit.item.price.value,
      pnlSign: c.pnlSign,
      segments,
      row: null,
      fills: { entry: c.entry.item, exit: c.exit.item },
      origin: c.exit.item.session_label || c.exit.item.session_id,
    };
  });
}
