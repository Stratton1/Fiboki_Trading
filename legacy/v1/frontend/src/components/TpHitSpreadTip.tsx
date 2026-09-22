"use client";

import { InfoTip } from "@/components/InfoTip";
import { isTpHitNegativePnl, TP_HIT_NEGATIVE_PNL_EXPLANATION } from "@/lib/trade-explain";

/**
 * Renders an InfoTip when a trade hit TP but has negative PnL due to spread.
 * Pass any object with exit_reason + pnl. Renders nothing if condition not met.
 */
export function TpHitSpreadTip({ trade }: { trade: { exit_reason: string; pnl: number } }) {
  if (!isTpHitNegativePnl(trade)) return null;
  return <InfoTip text={TP_HIT_NEGATIVE_PNL_EXPLANATION} />;
}
