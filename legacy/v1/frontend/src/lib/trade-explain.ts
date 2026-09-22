/**
 * Shared trade explanation helpers.
 *
 * Single source of truth for trade-level explanations that appear
 * across backtest detail, trade list, and trade detail pages.
 */

/** Returns true when a TP-hit trade has negative PnL due to spread/slippage. */
export function isTpHitNegativePnl(trade: { exit_reason: string; pnl: number }): boolean {
  return trade.exit_reason === "take_profit_hit" && trade.pnl < 0;
}

/** Explanation copy for the TP-hit negative PnL artefact. */
export const TP_HIT_NEGATIVE_PNL_EXPLANATION =
  "TP was hit, but realised PnL is negative because spread/slippage cost exceeded the TP distance. See Assumptions panel for execution model details.";
