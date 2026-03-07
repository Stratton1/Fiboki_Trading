"use client";

import { use } from "react";
import Link from "next/link";
import { useTrade } from "@/lib/hooks/use-trades";

export default function TradeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const numId = parseInt(id, 10);
  const { data: trade, isLoading } = useTrade(isNaN(numId) ? null : numId);

  if (isLoading) {
    return <p className="text-foreground-muted">Loading trade...</p>;
  }

  if (!trade) {
    return <p className="text-foreground-muted">Trade not found.</p>;
  }

  const fields: { label: string; value: string; color?: string }[] = [
    { label: "Strategy", value: trade.strategy_id },
    { label: "Instrument", value: trade.instrument },
    {
      label: "Direction",
      value: trade.direction,
      color: trade.direction === "LONG" ? "text-primary" : "text-danger",
    },
    { label: "Entry Time", value: trade.entry_time ?? "-" },
    { label: "Entry Price", value: trade.entry_price.toFixed(5) },
    { label: "Exit Time", value: trade.exit_time ?? "-" },
    { label: "Exit Price", value: trade.exit_price.toFixed(5) },
    {
      label: "PnL",
      value: `${trade.pnl >= 0 ? "+" : ""}$${trade.pnl.toFixed(2)}`,
      color: trade.pnl >= 0 ? "text-primary" : "text-danger",
    },
    { label: "Bars in Trade", value: String(trade.bars_in_trade) },
    { label: "Exit Reason", value: trade.exit_reason },
    { label: "Backtest Run", value: String(trade.backtest_run_id) },
  ];

  return (
    <div>
      <div className="flex items-center gap-2 mb-6">
        <Link href="/trades" className="text-foreground-muted hover:text-foreground text-sm">
          Trades
        </Link>
        <span className="text-foreground-muted text-sm">/</span>
        <h2 className="text-xl font-semibold">Trade #{trade.id}</h2>
      </div>

      <div className="bg-background-card rounded-lg border border-gray-200 p-5">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          {fields.map(({ label, value, color }) => (
            <div key={label}>
              <p className="text-xs text-foreground-muted mb-1">{label}</p>
              <p className={`text-sm font-medium ${color ?? ""}`}>{value}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
