"use client";

import Link from "next/link";
import useSWR from "swr";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useAccount } from "@/lib/hooks/use-bots";
import { formatCurrency, formatPnl } from "@/lib/format-currency";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
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
  ListTodo,
  Bell,
  ExternalLink,
  Star,
} from "lucide-react";

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
  const { data: fleet } = useSWR("/paper/fleet", () => api.fleet(), { refreshInterval: 10000 });
  const { data: jobsData } = useSWR("/jobs", () => api.listJobs(), { refreshInterval: 5000 });
  const { data: alertsData } = useSWR("/alerts?limit=5", () => api.listAlerts("limit=5"));
  const { data: shortlist } = useSWR("/research/shortlist", () => api.listShortlist());

  const balance = account?.balance ?? 0;
  const equity = account?.equity ?? 0;
  const dailyPnl = account?.daily_pnl ?? 0;
  const weeklyPnl = account?.weekly_pnl ?? 0;
  const currency = account?.currency ?? "GBP";

  const recentJobs = (jobsData?.items ?? []).slice(0, 5);
  const activeJobs = jobsData?.active_count ?? 0;
  const recentAlerts = (alertsData?.items ?? []).slice(0, 3);
  const unreadAlerts = alertsData?.unread_count ?? 0;
  const runningBots = fleet?.running ?? 0;
  const totalBots = fleet?.total_bots ?? 0;
  const fleetPnl = fleet?.aggregate_pnl ?? 0;
  const topShortlist = (shortlist ?? []).slice(0, 5);

  return (
    <div className="max-w-6xl">
      <PageHeader
        title={`Welcome back${user ? `, ${user.username}` : ""}`}
        subtitle="Your trading overview at a glance"
      />

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard icon={Wallet} label="Balance" value={formatCurrency(balance, currency)} />
        <StatCard icon={TrendingUp} label="Equity" value={formatCurrency(equity, currency)} trend={equity >= balance ? "up" : "down"} />
        <StatCard icon={CalendarDays} label="Daily PnL" value={formatPnl(dailyPnl, currency)} trend={dailyPnl >= 0 ? "up" : "down"} />
        <StatCard icon={CalendarRange} label="Weekly PnL" value={formatPnl(weeklyPnl, currency)} trend={weeklyPnl >= 0 ? "up" : "down"} />
      </div>

      {/* Main grid: Activity + Quick Actions + Fleet */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        {/* Activity Summary */}
        <div className="card-elevated lg:col-span-2">
          <p className="section-label">Activity</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-primary/8 flex items-center justify-center text-primary">
                <Bot size={18} />
              </div>
              <div>
                <p className="text-2xl font-bold tracking-tight">{runningBots}<span className="text-sm font-normal text-foreground-muted">/{totalBots}</span></p>
                <p className="text-xs text-foreground-muted">Bots Running</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-primary/8 flex items-center justify-center text-primary">
                <BarChart3 size={18} />
              </div>
              <div>
                <p className={`text-2xl font-bold tracking-tight ${fleetPnl >= 0 ? "text-primary" : "text-danger"}`}>
                  {formatPnl(fleetPnl, currency)}
                </p>
                <p className="text-xs text-foreground-muted">Fleet PnL</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-primary/8 flex items-center justify-center text-primary">
                <ListTodo size={18} />
              </div>
              <div>
                <p className="text-2xl font-bold tracking-tight">{activeJobs}</p>
                <p className="text-xs text-foreground-muted">Active Jobs</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-primary/8 flex items-center justify-center text-primary">
                <Star size={18} />
              </div>
              <div>
                <p className="text-2xl font-bold tracking-tight">{topShortlist.length}</p>
                <p className="text-xs text-foreground-muted">Shortlisted</p>
              </div>
            </div>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="card-elevated">
          <p className="section-label">Quick Actions</p>
          <div className="flex flex-col gap-2">
            <QuickAction href="/backtests" icon={BarChart3} label="Run Backtest" primary />
            <QuickAction href="/bots" icon={Bot} label="Paper Bots" />
            <QuickAction href="/research" icon={Search} label="Research Matrix" />
            <QuickAction href="/charts" icon={ChartCandlestick} label="View Charts" />
          </div>
        </div>
      </div>

      {/* Second row: Recent Jobs + Alerts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        {/* Recent Jobs */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <p className="section-label !mb-0">Recent Jobs</p>
            <Link href="/jobs" className="text-xs text-primary hover:underline">View all</Link>
          </div>
          {recentJobs.length === 0 ? (
            <p className="text-sm text-foreground-muted py-2">No recent jobs.</p>
          ) : (
            <div className="space-y-2">
              {recentJobs.map((j) => (
                <div key={j.job_id} className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2 min-w-0">
                    <StatusBadge variant={
                      j.state === "completed" ? "ok" :
                      j.state === "failed" ? "error" :
                      (j.state === "running" || j.state === "pending") ? "warn" : "info"
                    }>
                      {j.state}
                    </StatusBadge>
                    <span className="truncate">{j.label}</span>
                  </div>
                  {j.state === "completed" && j.result && j.job_type === "backtest" && (
                    <Link
                      href={`/backtests/${(j.result as Record<string, unknown>).backtest_run_id}`}
                      className="text-xs text-primary hover:underline flex items-center gap-1 shrink-0"
                    >
                      View <ExternalLink size={10} />
                    </Link>
                  )}
                  {j.state === "completed" && j.job_type === "research" && (
                    <Link href="/research" className="text-xs text-primary hover:underline flex items-center gap-1 shrink-0">
                      View <ExternalLink size={10} />
                    </Link>
                  )}
                  {(j.state === "running" || j.state === "pending") && (
                    <span className="text-xs text-foreground-muted tabular-nums shrink-0">{j.progress}%</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Recent Alerts */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <p className="section-label !mb-0">Alerts</p>
              {unreadAlerts > 0 && (
                <span className="text-xs bg-danger text-white rounded-full px-1.5 py-0.5 tabular-nums">{unreadAlerts}</span>
              )}
            </div>
            <Link href="/alerts" className="text-xs text-primary hover:underline">View all</Link>
          </div>
          {recentAlerts.length === 0 ? (
            <p className="text-sm text-foreground-muted py-2">No recent alerts.</p>
          ) : (
            <div className="space-y-2">
              {recentAlerts.map((a) => (
                <div key={a.id} className="flex items-start gap-2 text-sm">
                  <Bell size={12} className={`mt-1 shrink-0 ${a.severity === "critical" ? "text-danger" : a.severity === "warning" ? "text-amber-500" : "text-foreground-muted"}`} />
                  <div className="min-w-0">
                    <p className={`text-sm truncate ${!a.is_read ? "font-medium" : "text-foreground-muted"}`}>{a.title}</p>
                    <p className="text-xs text-foreground-muted">{new Date(a.created_at).toLocaleString()}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Shortlist summary */}
      {topShortlist.length > 0 && (
        <div className="card mb-6">
          <div className="flex items-center justify-between mb-3">
            <p className="section-label !mb-0">Top Saved Combos</p>
            <Link href="/research" className="text-xs text-primary hover:underline">Manage shortlist</Link>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-1.5 pr-3 text-xs text-foreground-muted">Strategy</th>
                  <th className="text-left py-1.5 pr-3 text-xs text-foreground-muted">Instrument</th>
                  <th className="text-left py-1.5 pr-3 text-xs text-foreground-muted">TF</th>
                  <th className="text-right py-1.5 text-xs text-foreground-muted">Score</th>
                </tr>
              </thead>
              <tbody>
                {topShortlist.map((s) => (
                  <tr key={s.id} className="border-b border-gray-100">
                    <td className="py-1.5 pr-3 font-medium">{s.strategy_id}</td>
                    <td className="py-1.5 pr-3">{s.instrument}</td>
                    <td className="py-1.5 pr-3 text-foreground-muted">{s.timeframe}</td>
                    <td className="py-1.5 text-right tabular-nums">{s.score.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

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
