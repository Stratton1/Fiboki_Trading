"use client";

import { useAuth } from "@/lib/auth";
import { useAccount } from "@/lib/hooks/use-bots";
import { PageHeader } from "@/components/PageHeader";
import {
  ArrowUpRight,
  ArrowDownRight,
  Wallet,
  TrendingUp,
  CalendarDays,
  CalendarRange,
  Bot,
  BarChart3,
  ChartCandlestick,
  Search,
  Layers,
  Zap,
} from "lucide-react";
import Link from "next/link";

function StatCard({
  icon: Icon,
  label,
  value,
  trend,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  trend?: "up" | "down" | "neutral";
}) {
  const trendColor =
    trend === "up"
      ? "text-primary"
      : trend === "down"
        ? "text-danger"
        : "text-foreground";
  return (
    <div className="stat-card group">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
          {label}
        </span>
        <div className="w-8 h-8 rounded-lg bg-primary/8 flex items-center justify-center text-primary group-hover:bg-primary/12 transition-colors">
          <Icon size={16} />
        </div>
      </div>
      <div className="flex items-end gap-2">
        <span className={`text-2xl font-bold tracking-tight ${trendColor}`}>{value}</span>
        {trend && trend !== "neutral" && (
          <span className={`mb-0.5 ${trend === "up" ? "text-primary" : "text-danger"}`}>
            {trend === "up" ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}
          </span>
        )}
      </div>
    </div>
  );
}

function QuickAction({
  href,
  icon: Icon,
  label,
  primary,
}: {
  href: string;
  icon: React.ElementType;
  label: string;
  primary?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`btn ${primary ? "btn-primary" : "btn-secondary"}`}
    >
      <Icon size={16} />
      {label}
    </Link>
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
    <div className="max-w-6xl">
      <PageHeader
        title={`Welcome back${user ? `, ${user.username}` : ""}`}
        subtitle="Your trading overview at a glance"
      />

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard icon={Wallet} label="Balance" value={`$${balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
        <StatCard icon={TrendingUp} label="Equity" value={`$${equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} trend={equity >= balance ? "up" : "down"} />
        <StatCard icon={CalendarDays} label="Daily PnL" value={`${dailyPnl >= 0 ? "+" : ""}$${dailyPnl.toFixed(2)}`} trend={dailyPnl >= 0 ? "up" : "down"} />
        <StatCard icon={CalendarRange} label="Weekly PnL" value={`${weeklyPnl >= 0 ? "+" : ""}$${weeklyPnl.toFixed(2)}`} trend={weeklyPnl >= 0 ? "up" : "down"} />
      </div>

      {/* Activity + Quick Actions row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-8">
        {/* Activity Summary */}
        <div className="card-elevated lg:col-span-2">
          <p className="section-label">Activity</p>
          <div className="grid grid-cols-2 gap-6">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-primary/8 flex items-center justify-center text-primary">
                <Bot size={22} />
              </div>
              <div>
                <p className="text-3xl font-bold tracking-tight">{account?.open_positions ?? 0}</p>
                <p className="text-xs text-foreground-muted mt-0.5">Active Bots</p>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-primary/8 flex items-center justify-center text-primary">
                <BarChart3 size={22} />
              </div>
              <div>
                <p className="text-3xl font-bold tracking-tight">{account?.total_trades ?? 0}</p>
                <p className="text-xs text-foreground-muted mt-0.5">Total Trades</p>
              </div>
            </div>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="card-elevated">
          <p className="section-label">Quick Actions</p>
          <div className="flex flex-col gap-2">
            <QuickAction href="/backtests" icon={BarChart3} label="Run Backtest" primary />
            <QuickAction href="/bots" icon={Bot} label="Add Paper Bot" />
            <QuickAction href="/research" icon={Search} label="Research Matrix" />
            <QuickAction href="/charts" icon={ChartCandlestick} label="View Charts" />
          </div>
        </div>
      </div>

      {/* System Overview */}
      <div className="card">
        <p className="section-label">System Status</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-primary animate-pulse" />
            <div>
              <p className="text-sm font-medium">Engine Online</p>
              <p className="text-xs text-foreground-muted">Paper trading mode</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Layers size={16} className="text-foreground-muted" />
            <div>
              <p className="text-sm font-medium">12 Strategies</p>
              <p className="text-xs text-foreground-muted">All loaded</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Zap size={16} className="text-foreground-muted" />
            <div>
              <p className="text-sm font-medium">API Connected</p>
              <p className="text-xs text-foreground-muted">Healthy</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
