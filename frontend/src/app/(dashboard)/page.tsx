"use client";

import { useAuth } from "@/lib/auth";
import { useAccount } from "@/lib/hooks/use-bots";

function StatCard({ label, value, trend }: { label: string; value: string; trend?: "up" | "down" | "neutral" }) {
  const trendColor = trend === "up" ? "text-primary" : trend === "down" ? "text-danger" : "text-foreground-muted";
  return (
    <div className="bg-background-card rounded-lg border border-gray-200 p-5">
      <p className="text-sm text-foreground-muted mb-1">{label}</p>
      <p className={`text-2xl font-semibold ${trendColor}`}>{value}</p>
    </div>
  );
}

export default function DashboardPage() {
  const { user } = useAuth();
  const { data: account } = useAccount();

  const balance = account?.balance ?? 0;
  const equity = account?.equity ?? 0;
  const dailyPnl = account?.daily_pnl ?? 0;
  const weeklyPnl = account?.weekly_pnl ?? 0;

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Welcome{user ? `, ${user.username}` : ""}</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard label="Balance" value={`$${balance.toFixed(2)}`} />
        <StatCard label="Equity" value={`$${equity.toFixed(2)}`} trend={equity >= balance ? "up" : "down"} />
        <StatCard label="Daily PnL" value={`${dailyPnl >= 0 ? "+" : ""}$${dailyPnl.toFixed(2)}`} trend={dailyPnl >= 0 ? "up" : "down"} />
        <StatCard label="Weekly PnL" value={`${weeklyPnl >= 0 ? "+" : ""}$${weeklyPnl.toFixed(2)}`} trend={weeklyPnl >= 0 ? "up" : "down"} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-foreground-muted mb-3">Active Bots</h3>
          <p className="text-3xl font-semibold">{account?.open_positions ?? 0}</p>
        </div>
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-foreground-muted mb-3">Total Trades</h3>
          <p className="text-3xl font-semibold">{account?.total_trades ?? 0}</p>
        </div>
      </div>
    </div>
  );
}
