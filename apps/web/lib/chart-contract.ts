import type { components } from "./generated/openapi";
import type {
  Bar,
  EventOverlay,
  FillOverlay,
  HeadlineOverlay,
  LevelOverlay,
  OverlayView,
  Provenance,
  RegimeOverlay,
  SectionStatus,
  SignalOverlay,
  Figure,
} from "./types";

/**
 * Typed mirrors of the chart workstation routes (Wave 2B,
 * src/fiboki/api/routers/chart_workstation.py). Hand-written where the local
 * type is deliberately the authoritative one (Figure, OverlayView); every
 * mirror's field NAMES are checked against the generated schema below, so a
 * renamed or dropped API field fails `tsc`, not an operator's screen.
 */

type S = components["schemas"];

// ------------------------------------------------------------------ replay

/** A backtest trade as it stood at the replay cursor (exit fields null while open). */
export interface ReplayTrade {
  trade_id: string;
  strategy_id: string;
  direction: "long" | "short";
  size: Figure;
  entry_time: string;
  entry_price: Figure;
  stop_loss: Figure;
  take_profit_targets: Figure[];
  open_at_cursor: boolean;
  exit_time: string | null;
  exit_price: Figure | null;
  exit_reason: string | null;
  net_pnl: Figure | null;
  final_stop: Figure | null;
  provenance: Provenance;
}

/** GET /api/markets/replay/{symbol} (Envelope): nothing timed after `cursor`. */
export interface ReplayView {
  symbol: string;
  timeframe: string;
  cursor: string;
  dataset_version_id: string;
  volume_kind: "volume" | "tick_volume" | null;
  bars: Bar[];
  last_close: string | null;
  next_cursor: string | null;
  prev_cursor: string | null;
  first_bar: string | null;
  last_bar: string | null;
  bars_closed: number;
  bars_total: number;
  overlays: OverlayView;
  run_id: string | null;
  trades: ReplayTrade[];
  trades_detail: string;
}

// ----------------------------------------------------------------- compare

/** GET /api/markets/compare/{base}?with= (Envelope): percent since the anchor bar. */
export interface CompareView {
  base: string;
  symbol: string;
  timeframe: string;
  unit: string;
  anchor_time: string | null;
  anchor_close: Figure;
  base_dataset_version_id: string;
  dataset_version_id: string;
  points: { t: string; v: number | null }[];
  unmatched: number;
}

// ------------------------------------------------------------- data window

export interface DataWindowValue {
  indicator_id: string;
  name: string;
  pane: string;
  value: number | null;
  display_only: boolean;
}

/** GET /api/markets/datawindow/{symbol}?t= (Envelope): everything at one bar. */
export interface DataWindowView {
  symbol: string;
  timeframe: string;
  t: string;
  t_end: string;
  dataset_version_id: string;
  bar: Bar;
  values: DataWindowValue[];
  regime: RegimeOverlay | null;
  signals: SignalOverlay[];
  fills: FillOverlay[];
  levels: LevelOverlay[];
  events: EventOverlay[];
  headlines: HeadlineOverlay[];
  sections: Record<string, SectionStatus>;
}

// --------------------------------------------------------------- templates

export type TradeSource = "both" | "paper" | "backtest";

/** A chart template as the API stores it: display choices only. */
export interface ChartTemplateBody {
  layers: Partial<Record<string, boolean>>;
  groups: Record<string, boolean>;
  strategy_ids: string[] | null;
  trade_source: TradeSource;
}

export interface ChartTemplateRow {
  name: string;
  version: number;
  updated_at: string;
  template: ChartTemplateBody;
}

/** GET /api/chart/templates (Envelope): the signed-in operator's templates. */
export interface ChartTemplatesView {
  owner: string;
  templates: ChartTemplateRow[];
  can_edit: boolean;
  max_templates: number;
}

// ------------------------------------------------------------ price alerts

export interface PriceAlertRow {
  alert_id: string;
  owner: string;
  symbol: string;
  price: number;
  condition: "cross_above" | "cross_below";
  reference_close: number;
  reference_time: string;
  dataset_version_id: string;
  drawing_id: string | null;
  origin: string | null;
  note: string;
  created_at: string;
  status: "armed";
}

/** GET / POST /api/alerts/price (Envelope). `evaluated` false: nothing watches them yet. */
export interface PriceAlertsView {
  alerts: PriceAlertRow[];
  evaluated: boolean;
  evaluator_detail: string;
}

// ------------------------------------------------------------------ events

/** GET /api/markets/events/{symbol} (Envelope): the newest official calendar. */
export interface CalendarEventsView {
  symbol: string;
  calendar_version: string;
  calendar_digest: string | null;
  currencies: string[];
  window_from: string | null;
  window_to: string | null;
  events: EventOverlay[];
  focus: EventOverlay | null;
  declared_start: string | null;
  declared_end: string | null;
}

// ---------------------------------------------------------------- snapshot

/** GET /api/chart/stamp/{symbol} (Envelope): the provenance stamp a snapshot carries. */
export interface SnapshotStampView {
  symbol: string;
  timeframe: string;
  dataset_version_id: string;
  dataset_current: boolean | null;
  latest_dataset_version_id: string | null;
  build_sha: string | null;
  api_version: string;
  execution_mode: string;
  generated_at: string;
  text: string;
}

// ------------------------------------------------------------------ checks

type SameKeys<A, B> = [Exclude<keyof A, keyof B>, Exclude<keyof B, keyof A>] extends [never, never]
  ? true
  : { onlyLocal: Exclude<keyof A, keyof B>; onlySchema: Exclude<keyof B, keyof A> };

export type ChartContractChecks = [
  SameKeys<ReplayTrade, S["ReplayTrade"]>,
  SameKeys<ReplayView, S["ReplayView"]>,
  SameKeys<Bar, S["ReplayBar"]>,
  SameKeys<CompareView, S["CompareView"]>,
  SameKeys<DataWindowValue, S["DataWindowValue"]>,
  SameKeys<DataWindowView, S["DataWindowView"]>,
  SameKeys<ChartTemplateBody, S["ChartTemplate"]>,
  SameKeys<ChartTemplateRow, S["ChartTemplateView"]>,
  SameKeys<ChartTemplatesView, S["ChartTemplatesView"]>,
  SameKeys<PriceAlertRow, S["PriceAlert"]>,
  SameKeys<PriceAlertsView, S["PriceAlertsView"]>,
  SameKeys<CalendarEventsView, S["CalendarEventsView"]>,
  SameKeys<SnapshotStampView, S["SnapshotStampView"]>,
];

/** Fails to compile when any check above is not `true`. */
export const CHART_CONTRACT_OK: ChartContractChecks extends true[] ? true : never = true;
