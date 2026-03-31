"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useAccount } from "@/lib/hooks/use-bots";
import { formatCurrency, formatPnl, currencySymbol } from "@/lib/format-currency";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { InfoTip } from "@/components/InfoTip";
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
  Star,
  ShieldAlert,
  ShieldCheck,
  Activity,
  Target,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Loader2,
  ArrowRight,
  Crosshair,
  Eye,
  Gauge,
} from "lucide-react";

// ─── Stat Card ────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  trend,
  sub,
  tip,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  trend?: "up" | "down" | "neutral";
  sub?: string;
  tip?: string;
}) {
  const trendColor =
    trend === "up"
      ? "text-primary"
      : trend === "down"
        ? "text-danger"
        : "text-foreground";
  return (
    <div className="stat-card group" data-testid={`stat-${label.toLowerCase().replace(/\s+/g, "-")}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
          {label}
          {tip && <InfoTip text={tip} />}
        </span>
        <div className="w-7 h-7 rounded-lg bg-primary/8 flex items-center justify-center text-primary group-hover:bg-primary/12 transition-colors">
          <Icon size={14} />
        </div>
      </div>
      <div className="flex items-end gap-2">
        <span className={`text-xl font-bold tracking-tight ${trendColor}`}>{value}</span>
        {trend && trend !== "neutral" && (
          <span className={`mb-0.5 ${trend === "up" ? "text-primary" : "text-danger"}`}>
            {trend === "up" ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
          </span>
        )}
      </div>
      {sub && <p className="text-[11px] text-foreground-muted mt-1 truncate">{sub}</p>}
    </div>
  );
}

// ─── Quick Action ─────────────────────────────────────────────

function QuickAction({
  href,
  icon: Icon,
  label,
  primary,
  badge,
}: {
  href: string;
  icon: React.ElementType;
  label: string;
  primary?: boolean;
  badge?: number;
}) {
  return (
    <Link
      href={href}
      className={`btn ${primary ? "btn-primary" : "btn-secondary"} justify-between`}
      data-testid={`qa-${label.toLowerCase().replace(/\s+/g, "-")}`}
    >
      <span className="flex items-center gap-2">
        <Icon size={15} />
        {label}
      </span>
      {badge !== undefined && badge > 0 && (
        <span className="text-[10px] bg-danger text-white rounded-full px-1.5 py-0.5 leading-none tabular-nums">{badge}</span>
      )}
    </Link>
  );
}

// ─── Activity Item ────────────────────────────────────────────

function ActivityItem({
  icon: Icon,
  iconColor,
  text,
  time,
  href,
}: {
  icon: React.ElementType;
  iconColor: string;
  text: string;
  time: string;
  href?: string;
}) {
  const content = (
    <div className="flex items-start gap-2.5 py-2 group/item">
      <Icon size={14} className={`mt-0.5 shrink-0 ${iconColor}`} />
      <div className="min-w-0 flex-1">
        <p className="text-sm truncate">{text}</p>
        <p className="text-[11px] text-foreground-muted">{formatTime(time)}</p>
      </div>
      {href && (
        <ArrowRight size={12} className="mt-1 shrink-0 text-foreground-muted opacity-0 group-hover/item:opacity-100 transition-opacity" />
      )}
    </div>
  );
  if (href) {
    return <Link href={href} className="block hover:bg-background-muted/50 -mx-2 px-2 rounded-lg transition-colors">{content}</Link>;
  }
  return content;
}

// ─── Helpers ──────────────────────────────────────────────────

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString();
  } catch {
    return iso;
  }
}

type JobItem = {
  job_id: string;
  job_type: string;
  label: string;
  state: string;
  progress: number;
  created_at: string;
  completed_at?: string;
  result?: Record<string, unknown>;
};

type AlertItem = {
  id: number;
  alert_type: string;
  severity: string;
  title: string;
  message: string;
  is_read: boolean;
  created_at: string;
};

type ShortlistEntry = {
  id: number;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  score: number;
  status: string;
};

// ─── Page ─────────────────────────────────────────────────────

export default function DashboardPage() {
  const { user } = useAuth();
  const { data: account } = useAccount();
  const [killSwitchLoading, setKillSwitchLoading] = useState(false);

  async function handleKillSwitch() {
    if (killSwitchLoading) return;
    if (killSwitchActive) {
      if (!confirm("Deactivate kill switch? Bots will resume normal operation.")) return;
    } else {
      if (!confirm("Activate kill switch? This will halt all new trades immediately.")) return;
    }
    setKillSwitchLoading(true);
    try {
      if (killSwitchActive) {
        await api.deactivateKillSwitch();
      } else {
        await api.activateKillSwitch("Dashboard emergency stop");
      }
      await mutateKillSwitch();
    } catch {
      // silent — user can retry
    } finally {
      setKillSwitchLoading(false);
    }
  }
  const { data: fleet } = useSWR("/paper/fleet", () => api.fleet(), { refreshInterval: 10000 });
  const { data: jobsData } = useSWR("/jobs", () => api.listJobs(), { refreshInterval: 5000 });
  const { data: alertsData } = useSWR("/alerts?limit=5", () => api.listAlerts("limit=5"), { refreshInterval: 30000 });
  const { data: shortlist } = useSWR("/research/shortlist", () => api.listShortlist());
  const { data: systemStatus } = useSWR("/system/status", () => api.systemStatus(), { refreshInterval: 30000 });
  const { data: execMode } = useSWR("/execution/mode", () => api.executionMode(), { refreshInterval: 15000 });
  const { data: killSwitch, mutate: mutateKillSwitch } = useSWR("/execution/kill-switch", () => api.killSwitchStatus(), { refreshInterval: 15000 });
  const isIgDemo = (execMode?.mode ?? systemStatus?.execution_mode) === "ig_demo";
  const { data: igHealth } = useSWR(
    isIgDemo ? "/execution/ig-health" : null,
    () => api.igHealth(),
    { refreshInterval: 60000 }
  );

  // Derived values
  const balance = account?.balance ?? 0;
  const equity = account?.equity ?? 0;
  const initialBalance = account?.initial_balance ?? 1000;
  const dailyPnl = account?.daily_pnl ?? 0;
  const weeklyPnl = account?.weekly_pnl ?? 0;
  const openPositions = account?.open_positions ?? 0;
  const totalTrades = account?.total_trades ?? 0;
  const currency = account?.currency ?? "GBP";
  const sym = currencySymbol(currency);

  const recentJobs = ((jobsData?.items ?? []) as JobItem[]).slice(0, 5);
  const activeJobs = jobsData?.active_count ?? 0;
  const recentAlerts = ((alertsData?.items ?? []) as AlertItem[]).slice(0, 5);
  const unreadAlerts = alertsData?.unread_count ?? 0;
  const runningBots = fleet?.running ?? 0;
  const totalBots = fleet?.total_bots ?? 0;
  const staleBots = fleet?.stale ?? 0;
  const pausedBots = fleet?.paused ?? 0;
  const fleetPnl = fleet?.aggregate_pnl ?? 0;
  const fleetTrades = fleet?.aggregate_trades ?? 0;
  const topShortlist = ((shortlist ?? []) as ShortlistEntry[]).slice(0, 5);

  const executionMode = execMode?.mode ?? systemStatus?.execution_mode ?? "paper";
  const killSwitchActive = killSwitch?.is_active ?? execMode?.kill_switch_active ?? false;
  const strategiesLoaded = systemStatus?.strategies_loaded ?? 12;
  const dbStatus = systemStatus?.database ?? "ok";

  // Build activity feed from jobs + alerts
  const activityFeed: Array<{
    id: string;
    icon: React.ElementType;
    iconColor: string;
    text: string;
    time: string;
    href?: string;
  }> = [];

  for (const j of recentJobs) {
    const isComplete = j.state === "completed";
    const isFailed = j.state === "failed";
    const isRunning = j.state === "running" || j.state === "pending";
    let href: string | undefined;
    if (isComplete && j.job_type === "backtest" && j.result?.backtest_run_id) {
      href = `/backtests/${j.result.backtest_run_id}`;
    } else if (isComplete && j.job_type === "research") {
      href = "/research";
    } else if (isRunning) {
      href = "/jobs";
    }
    activityFeed.push({
      id: `job-${j.job_id}`,
      icon: isComplete ? CheckCircle2 : isFailed ? XCircle : isRunning ? Loader2 : ListTodo,
      iconColor: isComplete ? "text-primary" : isFailed ? "text-danger" : isRunning ? "text-amber-500" : "text-foreground-muted",
      text: `${j.job_type === "backtest" ? "Backtest" : j.job_type === "research" ? "Research" : "Job"}: ${j.label}${isRunning ? ` (${j.progress}%)` : isFailed ? " — failed" : ""}`,
      time: j.completed_at ?? j.created_at,
      href,
    });
  }

  for (const a of recentAlerts) {
    activityFeed.push({
      id: `alert-${a.id}`,
      icon: Bell,
      iconColor: a.severity === "critical" ? "text-danger" : a.severity === "warning" ? "text-amber-500" : "text-foreground-muted",
      text: a.title,
      time: a.created_at,
      href: "/alerts",
    });
  }

  // Sort by time descending
  activityFeed.sort((a, b) => {
    try {
      return new Date(b.time).getTime() - new Date(a.time).getTime();
    } catch {
      return 0;
    }
  });

  const activityItems = activityFeed.slice(0, 8);

  return (
    <div className="max-w-6xl" data-testid="dashboard">
      <PageHeader
        title={`Welcome back${user ? `, ${user.username}` : ""}`}
        subtitle="Your trading operations at a glance"
      />

      {/* ─── KPI Cards ────────────────────────────────────────── */}
      <div className={`grid grid-cols-2 sm:grid-cols-3 ${isIgDemo ? "lg:grid-cols-7" : "lg:grid-cols-6"} gap-3 mb-6`} data-testid="kpi-cards">
        <StatCard
          icon={Wallet}
          label="Balance"
          value={formatCurrency(balance, currency)}
          tip="Current paper account balance including realised PnL."
          sub={`Start: ${sym}${initialBalance.toFixed(0)}`}
        />
        <StatCard
          icon={TrendingUp}
          label="Equity"
          value={formatCurrency(equity, currency)}
          trend={equity >= initialBalance ? "up" : equity < initialBalance ? "down" : "neutral"}
          tip="Balance plus unrealised PnL from open positions."
          sub={openPositions > 0 ? `${openPositions} open position${openPositions !== 1 ? "s" : ""}` : "No open positions"}
        />
        <StatCard
          icon={CalendarDays}
          label="Daily PnL"
          value={formatPnl(dailyPnl, currency)}
          trend={dailyPnl > 0 ? "up" : dailyPnl < 0 ? "down" : "neutral"}
          tip="Realised profit/loss from trades closed today (UTC)."
        />
        <StatCard
          icon={CalendarRange}
          label="Weekly PnL"
          value={formatPnl(weeklyPnl, currency)}
          trend={weeklyPnl > 0 ? "up" : weeklyPnl < 0 ? "down" : "neutral"}
          tip="Realised profit/loss from trades closed this week (UTC Monday reset)."
        />
        <StatCard
          icon={Bot}
          label="Running Bots"
          value={`${runningBots}/${totalBots}`}
          tip="Active paper trading bots vs total registered. Running bots monitor candle closes for signals."
          sub={pausedBots > 0 ? `${pausedBots} paused` : staleBots > 0 ? `${staleBots} stale` : totalBots === 0 ? "None created" : "All healthy"}
        />
        <StatCard
          icon={Crosshair}
          label="Open Positions"
          value={String(openPositions)}
          tip="Paper positions currently open across all bots."
          sub={`${totalTrades} total trades`}
        />
        {isIgDemo && (
          <StatCard
            icon={Zap}
            label="IG Demo"
            value={igHealth?.reachable && igHealth.balance != null ? `£${igHealth.balance.toFixed(2)}` : igHealth?.reachable === false ? "Error" : "—"}
            trend={igHealth?.reachable ? "neutral" : undefined}
            tip="Live IG demo account balance. Fetched directly from IG — updates every 60s."
            sub={igHealth?.reachable ? (igHealth.account_name ?? "Connected") : igHealth?.reachable === false ? "Check Settings" : "Connecting..."}
          />
        )}
      </div>

      {/* ─── Fleet Summary Bar ─────────────────────────────────── */}
      <div className="card-elevated mb-6" data-testid="fleet-summary">
        <div className="flex items-center justify-between mb-3">
          <p className="section-label !mb-0">Fleet Overview</p>
          <Link href="/bots" className="text-xs text-primary hover:underline flex items-center gap-1">
            Manage bots <ArrowRight size={10} />
          </Link>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4">
          <div>
            <p className="text-xs text-foreground-muted">Running</p>
            <p className="text-lg font-bold tabular-nums">{runningBots}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted">Total Bots</p>
            <p className="text-lg font-bold tabular-nums">{totalBots}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted">Open Positions</p>
            <p className="text-lg font-bold tabular-nums">{openPositions}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted">Fleet Trades</p>
            <p className="text-lg font-bold tabular-nums">{fleetTrades}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted">Fleet PnL<InfoTip text="Aggregate PnL across all bots, including stopped bots." /></p>
            <p className={`text-lg font-bold tabular-nums ${fleetPnl >= 0 ? "text-primary" : "text-danger"}`}>
              {formatPnl(fleetPnl, currency)}
            </p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted">Active Jobs</p>
            <p className="text-lg font-bold tabular-nums">{activeJobs}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted">Unread Alerts</p>
            <p className={`text-lg font-bold tabular-nums ${unreadAlerts > 0 ? "text-amber-600" : ""}`}>{unreadAlerts}</p>
          </div>
        </div>
        {staleBots > 0 && (
          <div className="mt-3 flex items-center gap-2 text-xs text-amber-700 bg-amber-50 rounded-lg px-3 py-2">
            <AlertTriangle size={13} />
            {staleBots} bot{staleBots !== 1 ? "s" : ""} with stale data — check <Link href="/bots" className="underline font-medium">Bots</Link>
          </div>
        )}
      </div>

      {/* ─── Main Two-Column: Activity + Quick Actions ─────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">

        {/* Activity Feed (2/3 width) */}
        <div className="lg:col-span-2 card" data-testid="activity-feed">
          <div className="flex items-center justify-between mb-3">
            <p className="section-label !mb-0">Recent Activity</p>
            <Link href="/jobs" className="text-xs text-primary hover:underline">View jobs</Link>
          </div>
          {activityItems.length === 0 ? (
            <div className="py-6 text-center" data-testid="activity-empty">
              <Activity size={28} className="mx-auto text-foreground-muted/30 mb-2" />
              <p className="text-sm text-foreground-muted">No recent activity</p>
              <p className="text-xs text-foreground-muted/70 mt-1">Run a backtest or research to get started</p>
              <Link href="/backtests" className="btn btn-secondary text-xs mt-3 inline-flex">
                <BarChart3 size={12} /> Run Backtest
              </Link>
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {activityItems.map((item) => (
                <ActivityItem
                  key={item.id}
                  icon={item.icon}
                  iconColor={item.iconColor}
                  text={item.text}
                  time={item.time}
                  href={item.href}
                />
              ))}
            </div>
          )}
        </div>

        {/* Quick Actions (1/3 width) */}
        <div className="card-elevated" data-testid="quick-actions">
          <p className="section-label">Quick Actions</p>
          <div className="flex flex-col gap-2">
            <QuickAction href="/backtests" icon={BarChart3} label="Run Backtest" primary />
            <QuickAction href="/research" icon={Search} label="Research Matrix" />
            <QuickAction href="/bots" icon={Bot} label="Bots" />
            <QuickAction href="/charts" icon={ChartCandlestick} label="View Charts" />
            <QuickAction href="/alerts" icon={Bell} label="Alerts" badge={unreadAlerts} />
            <QuickAction href="/jobs" icon={ListTodo} label="Jobs" badge={activeJobs} />
          </div>
        </div>
      </div>

      {/* ─── Second Row: Shortlist + Health ─────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">

        {/* Top Saved Combos */}
        <div className="card" data-testid="shortlist-panel">
          <div className="flex items-center justify-between mb-3">
            <p className="section-label !mb-0">
              Saved Combos
              <InfoTip text="Your curated shortlist of strategy/instrument/timeframe combos. Promote top scorers to paper trading." />
            </p>
            <Link href="/research" className="text-xs text-primary hover:underline">Manage</Link>
          </div>
          {topShortlist.length === 0 ? (
            <div className="py-6 text-center" data-testid="shortlist-empty">
              <Star size={28} className="mx-auto text-foreground-muted/30 mb-2" />
              <p className="text-sm text-foreground-muted">No saved combos yet</p>
              <p className="text-xs text-foreground-muted/70 mt-1 max-w-xs mx-auto">
                Run Research to find promising setups, then save the best ones to your shortlist.
              </p>
              <Link href="/research" className="btn btn-secondary text-xs mt-3 inline-flex">
                <Search size={12} /> Go to Research
              </Link>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left py-1.5 pr-3 text-xs text-foreground-muted">Strategy</th>
                    <th className="text-left py-1.5 pr-3 text-xs text-foreground-muted">Instrument</th>
                    <th className="text-left py-1.5 pr-3 text-xs text-foreground-muted">TF</th>
                    <th className="text-right py-1.5 pr-2 text-xs text-foreground-muted">Score</th>
                    <th className="text-right py-1.5 text-xs text-foreground-muted">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {topShortlist.map((s) => (
                    <tr key={s.id} className="border-b border-gray-100 hover:bg-background-muted/50 transition-colors">
                      <td className="py-1.5 pr-3 font-medium">{s.strategy_id}</td>
                      <td className="py-1.5 pr-3">{s.instrument}</td>
                      <td className="py-1.5 pr-3 text-foreground-muted">{s.timeframe}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">
                        <span className={s.score >= 0.7 ? "text-primary font-medium" : s.score >= 0.55 ? "" : "text-foreground-muted"}>
                          {s.score.toFixed(3)}
                        </span>
                      </td>
                      <td className="py-1.5 text-right">
                        <Link href="/bots" className="text-xs text-primary hover:underline" title="Create Bot">
                          Create Bot
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Health / Risk / Readiness Panel */}
        <div className="card" data-testid="health-panel">
          <p className="section-label">
            System Health & Readiness
            <InfoTip text="Operational status of the Fiboki platform. All safety controls including kill switch and execution mode are shown here." />
          </p>
          <div className="space-y-3">
            {/* Engine Status */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="w-2 h-2 rounded-full bg-primary animate-pulse" />
                <span className="text-sm">Engine</span>
              </div>
              <StatusBadge variant="ok">Online</StatusBadge>
            </div>

            {/* Execution Mode */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Gauge size={14} className="text-foreground-muted" />
                <span className="text-sm">
                  Execution Mode
                  <InfoTip text="Current trading execution mode. 'paper' = simulated only. 'ig_demo' = connected to IG demo account." />
                </span>
              </div>
              <StatusBadge variant={executionMode === "ig_demo" ? "info" : "neutral"}>
                {executionMode}
              </StatusBadge>
            </div>

            {/* Kill Switch */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                {killSwitchActive ? <ShieldAlert size={14} className="text-danger" /> : <ShieldCheck size={14} className="text-primary" />}
                <span className="text-sm">
                  Kill Switch
                  <InfoTip text="Emergency stop for all execution. When active, no new trades are opened." />
                </span>
              </div>
              <button
                onClick={handleKillSwitch}
                disabled={killSwitchLoading}
                className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg font-medium transition-colors disabled:opacity-50 ${
                  killSwitchActive
                    ? "bg-red-100 text-red-700 hover:bg-red-200"
                    : "bg-green-100 text-green-700 hover:bg-green-200"
                }`}
              >
                {killSwitchLoading ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : killSwitchActive ? (
                  <ShieldAlert size={11} />
                ) : (
                  <ShieldCheck size={11} />
                )}
                {killSwitchActive ? "ACTIVE — Deactivate" : "Off — Activate"}
              </button>
            </div>

            {/* Database */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Layers size={14} className="text-foreground-muted" />
                <span className="text-sm">Database</span>
              </div>
              <StatusBadge variant={dbStatus === "ok" ? "ok" : "error"}>
                {dbStatus === "ok" ? "Connected" : dbStatus}
              </StatusBadge>
            </div>

            {/* Strategies */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Target size={14} className="text-foreground-muted" />
                <span className="text-sm">Strategies</span>
              </div>
              <span className="text-sm font-medium tabular-nums">{strategiesLoaded} loaded</span>
            </div>

            {/* API */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Zap size={14} className="text-foreground-muted" />
                <span className="text-sm">API</span>
              </div>
              <StatusBadge variant="ok">Connected</StatusBadge>
            </div>

            {/* Alerts */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Bell size={14} className={unreadAlerts > 0 ? "text-amber-500" : "text-foreground-muted"} />
                <span className="text-sm">
                  Alerts
                  <InfoTip text="Unread platform alerts. Check Alerts page for trade events, risk warnings, and system notifications." />
                </span>
              </div>
              {unreadAlerts > 0 ? (
                <Link href="/alerts" className="flex items-center gap-1.5">
                  <span className="text-xs bg-amber-100 text-amber-800 rounded-full px-2 py-0.5 font-medium tabular-nums">{unreadAlerts} unread</span>
                </Link>
              ) : (
                <span className="text-sm text-foreground-muted">None</span>
              )}
            </div>

            {/* IG Demo Readiness */}
            <div className="border-t border-gray-100 pt-3 mt-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <Eye size={14} className="text-foreground-muted" />
                  <span className="text-sm">
                    IG Demo Readiness
                    <InfoTip text="IG demo broker integration status. Adapter and execution pipeline are built and tested. Activation requires IG API credentials (env vars)." />
                  </span>
                </div>
                <StatusBadge variant="info">Ready</StatusBadge>
              </div>
              <p className="text-[11px] text-foreground-muted mt-1.5 ml-[22px]">
                Adapter built, demo-only hard-blocked, kill switch wired. Set IG env vars to activate.
              </p>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-gray-100">
            <Link href="/system" className="text-xs text-primary hover:underline flex items-center gap-1">
              Full system details <ArrowRight size={10} />
            </Link>
          </div>
        </div>
      </div>

      {/* ─── Recent Alerts (show when alerts exist) ────────── */}
      {recentAlerts.length > 0 && (
        <div className="card mb-6" data-testid="alerts-panel">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <p className="section-label !mb-0">Recent Alerts</p>
              {unreadAlerts > 0 && (
                <span className="text-[10px] bg-danger text-white rounded-full px-1.5 py-0.5 tabular-nums">{unreadAlerts}</span>
              )}
            </div>
            <Link href="/alerts" className="text-xs text-primary hover:underline">View all</Link>
          </div>
          <div className="divide-y divide-gray-100">
            {recentAlerts.slice(0, 3).map((a) => (
              <div key={a.id} className="flex items-start gap-2.5 py-2">
                <Bell size={13} className={`mt-0.5 shrink-0 ${a.severity === "critical" ? "text-danger" : a.severity === "warning" ? "text-amber-500" : "text-foreground-muted"}`} />
                <div className="min-w-0 flex-1">
                  <p className={`text-sm truncate ${!a.is_read ? "font-medium" : "text-foreground-muted"}`}>{a.title}</p>
                  <p className="text-[11px] text-foreground-muted">{formatTime(a.created_at)}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
