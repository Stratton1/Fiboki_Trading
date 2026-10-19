import type { components } from "./generated/openapi";
import type { Bar, Figure, OverlayView, Provenance } from "./types";

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

// ------------------------------------------------------------------ checks

type SameKeys<A, B> = [Exclude<keyof A, keyof B>, Exclude<keyof B, keyof A>] extends [never, never]
  ? true
  : { onlyLocal: Exclude<keyof A, keyof B>; onlySchema: Exclude<keyof B, keyof A> };

export type ChartContractChecks = [
  SameKeys<ReplayTrade, S["ReplayTrade"]>,
  SameKeys<ReplayView, S["ReplayView"]>,
  SameKeys<Bar, S["ReplayBar"]>,
  SameKeys<CompareView, S["CompareView"]>,
];

/** Fails to compile when any check above is not `true`. */
export const CHART_CONTRACT_OK: ChartContractChecks extends true[] ? true : never = true;
