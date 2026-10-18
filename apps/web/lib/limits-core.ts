import type { LimitBand, LimitBoardView, LimitRowView } from "./types";

/**
 * The limit rows every screen draws the same way (Risk & Exposure, Command),
 * straight from GET /api/trading/limits.
 *
 * This module DECIDES nothing. Utilisation, headroom, the band (ok, warn,
 * critical, breached, absent), the breach verdict, the display thresholds and
 * the order of rows and families all arrive from the API
 * (src/fiboki/api/limit_board.py), which also feeds the attention queue from
 * the same rows. Before, this file divided two figures, invented 70% and 90%
 * bands and compared values against limits; the queue and the bars could then
 * disagree. tests/e2e/source-rules.spec.ts fails if a comparison against a
 * limit or a band reappears anywhere under lib/.
 *
 * What is left is lookup and the bar's geometry.
 */

export type LimitState = LimitBand;
export type LimitRow = LimitRowView;

/** The board's rows by key. */
export function rowsByKey(board: LimitBoardView): Map<string, LimitRowView> {
  return new Map(board.rows.map((row) => [row.key, row]));
}

/** The rows a family lists, in the API's order. */
export function familyRows(board: LimitBoardView, keys: readonly string[]): LimitRowView[] {
  const byKey = rowsByKey(board);
  return keys.map((key) => byKey.get(key)).filter((row): row is LimitRowView => row !== undefined);
}

/** The daily-loss and drawdown rows Command draws, in the API's order. */
export const LOSS_KEYS = ["risk:daily_loss", "risk:drawdown"] as const;

export function lossRows(board: LimitBoardView): LimitRowView[] {
  return board.rows.filter((row) => (LOSS_KEYS as readonly string[]).includes(row.key));
}

/**
 * The bar's filled length, 0 to 100 of the track: the API's utilisation,
 * clipped to the track; a row the API calls breached fills it.
 */
export function barLength(row: LimitRowView): number {
  if (row.breached) return 100;
  const used = row.utilisation_pct.value;
  return used === null ? 0 : Math.min(100, Math.max(0, used));
}
