"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useAccount } from "@/lib/hooks/use-bots";
import { formatCurrency, formatPnl, currencySymbol } from "@/lib/format-currency";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { InfoTip } from "@/components/InfoTip";
import { KillSwitchModal } from "@/components/KillSwitchModal";
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
  Flag,
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
  Clock,
} from "lucide-react";

// ─── Helpers ──────────────────────────────────────────────────

/** Title-case a username for the welcome heading. */
function titleCase(s?: string | null): string {
  if (!s) return "";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Format an ISO timestamp as a human-relative "Xs ago" / "Xm ago" string. */
function formatTime(iso: string | number): string {
  try {
    const d = typeof iso === "number" ? new Date(iso) : new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    if (diffSec < 5) return "just now";
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMins = Math.floor(diffSec / 60);
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString();
  } catch {
    return String(iso);
  }
}

// ─── Stat Card ────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  trend,
  sub,
  tip,
  testId,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  trend?: "up" | "down" | "neutral";
  sub?: string;
  tip?: string;
  /** Override the auto-generated test id (else derived from label). */
  testId?: string;
}) {
  const trendColor =
    trend === "up"
      ? "text-primary"
      : trend === "down"
        ? "text-danger"
        : "text-foreground";
  return (
    <div
      className="stat-card group"
      data-testid={testId ?? `stat-${label.toLowerCase().replace(/\s+/g, "-")}`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-semibold tracking-wide text-foreground-muted inline-flex items-center">
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
  testId,
}: {
  href: string;
  icon: React.ElementType;
  label: string;
  primary?: boolean;
  badge?: number;
  testId?: string;
}) {
  return (
    <Link
      href={href}
      className={`btn ${primary ? "btn-primary" : "btn-secondary"} justify-between`}
      data-testid={testId ?? `qa-${label.toLowerCase().replace(/\s+/g, "-")}`}
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

// ─── Skeleton helper ──────────────────────────────────────────

function SkeletonRow({ width = "w-full" }: { width?: string }) {
  return <div className={`h-3 bg-background-muted/70 rounded ${width} animate-pulse`} />;
}

// ─── Types ────────────────────────────────────────────────────

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
  const [killSwitchModalOpen, setKillSwitchModalOpen] = useState(false);

  // ─── Data fetching ──────────────────────────────────────────
  const { data: fleet } = useSWR("/paper/fleet", () => api.fleet(), { refreshInterval: 10000 });
  const { data: jobsData } = useSWR("/jobs", () => api.listJobs(), { refreshInterval: 5000 });
  const { data: alertsData } = useSWR("/alerts?limit=5", () => api.listAlerts("limit=5"), { refreshInterval: 30000 });
  const { data: shortlist } = useSWR("/research/shortlist", () => api.listShortlist());
  const { data: systemStatus } = useSWR("/system/status", () => api.systemStatus(), { refreshInterval: 30000 });
  const { data: execMode } = useSWR("/execution/mode", () => api.executionMode(), { refreshInterval: 15000 });
  const { data: killSwitch, mutate: mutateKillSwitch } = useSWR("/execution/kill-switch", () => api.killSwitchStatus(), { refreshInterval: 15000 });
  const { data: activePhase } = useSWR("/paper/phases/active", () => api.getActivePhase(), { refreshInterval: 60000 });
  const isIgDemo = (execMode?.mode ?? systemStatus?.execution_mode) === "ig_demo";
  const { data: igHealth } = useSWR(
    isIgDemo ? "/execution/ig-health" : null,
    () => api.igHealth(),
    { refreshInterval: 60000 }
  );

  // ─── Derived values ────────────────────────────────────────
  const balance = account?.balance ?? 0;
  const equity = account?.equity ?? 0;
  const initialBalance = account?.initial_balance ?? 1000;
  const dailyPnl = account?.daily_pnl ?? 0;
  const weeklyPnl = account?.weekly_pnl ?? 0;
  const accountOpenPositions = account?.open_positions ?? 0;
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
  const fleetOpenPositions = fleet?.open_positions ?? 0;
  // Active = running and *not* stale. Operators care about bots that are
  // both alive and producing fresh data.
  const activeBots = Math.max(0, runningBots - staleBots);
  const topShortlist = ((shortlist ?? []) as ShortlistEntry[]).slice(0, 5);

  const executionMode = execMode?.mode ?? systemStatus?.execution_mode ?? "paper";
  const killSwitchActive = killSwitch?.is_active ?? execMode?.kill_switch_active ?? false;
  // Show the raw count from the API. If it disagrees with the expected 12,
  // that surfaces a backend strategy-registry issue rather than masking it.
  const strategiesLoaded = systemStatus?.strategies_loaded;
  const dbStatus = systemStatus?.database;
  // Treat both "ok" and "connected" as healthy — the API has historically
  // returned either, and the dashboard should not light red on either.
  const dbHealthy = dbStatus === "ok" || dbStatus === "connected";

  // ─── Kill switch handler ───────────────────────────────────
  // Defined *after* killSwitchActive so we don't depend on its TDZ-evading
  // closure capture and so ESLint stays quiet.
  async function handleKillSwitchConfirm() {
    if (killSwitchLoading) return;
    setKillSwitchLoading(true);
    try {
      if (killSwitchActive) {
        await api.deactivateKillSwitch();
      } else {
        await api.activateKillSwitch("Dashboard emergency stop");
      }
      await mutateKillSwitch();
      setKillSwitchModalOpen(false);
    } catch {
      // Modal stays open; operator can retry. A future improvement is to
      // surface the error inline within the modal.
    } finally {
      setKillSwitchLoading(false);
    }
  }

  // ─── Last-updated indicator ────────────────────────────────
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  // Bump the timestamp whenever any underlying SWR result changes.
  useEffect(() => {
    setLastUpdated(Date.now());
  }, [account, fleet, jobsData, alertsData, shortlist, systemStatus, execMode, killSwitch, activePhase, igHealth]);
  // Tick once a second so the relative timestamp re-renders.
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  // ─── Activity feed ─────────────────────────────────────────
  const activityLoading = jobsData === undefined && alertsData === undefined;
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
    // De-duplicate the job-type prefix when the label already starts with it
    // (avoids "Research: Research 60 combos").
    const jobTypeLabel =
      j.job_type === "backtest" ? "Backtest" :
      j.job_type === "research" ? "Research" :
      "Job";
    const labelLower = (j.label ?? "").toLowerCase();
    const prefix = jobTypeLabel.toLowerCase();
    const cleanedLabel =
      labelLower.startsWith(prefix) ? j.label : `${jobTypeLabel}: ${j.label}`;
    const suffix = isRunning ? ` (${j.progress}%)` : isFailed ? " — failed" : "";
    activityFeed.push({
      id: `job-${j.job_id}`,
      icon: isComplete ? CheckCircle2 : isFailed ? XCircle : isRunning ? Loader2 : ListTodo,
      iconColor: isComplete ? "text-primary" : isFailed ? "text-danger" : isRunning ? "text-amber-500" : "text-foreground-muted",
      text: `${cleanedLabel}${suffix}`,
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

  activityFeed.sort((a, b) => {
    try {
      return new Date(b.time).getTime() - new Date(a.time).getTime();
    } catch {
      return 0;
    }
  });

  const activityItems = activityFeed.slice(0, 8);

  // ─── Saved combos: row-grouping helper ─────────────────────
  // Dim the strategy name when it repeats in consecutive rows so the table
  // groups visually without restructuring it.
  const groupedShortlist = useMemo(
    () =>
      topShortlist.map((s, i) => ({
        ...s,
        repeatStrategy: i > 0 && topShortlist[i - 1].strategy_id === s.strategy_id,
      })),
    [topShortlist]
  );
  const shortlistLoading = shortlist === undefined;

  return (
    <div className="max-w-7xl" data-testid="dashboard">
      <PageHeader
        title={`Welcome back${user ? `, ${titleCase(user.username)}` : ""}`}
        subtitle="Your trading operations at a glance"
      />

      {/* ─── KPI Cards ────────────────────────────────────────── */}
      <div
        className={`grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 ${isIgDemo ? "xl:grid-cols-7" : "xl:grid-cols-6"} gap-3 mb-6`}
        data-testid="kpi-cards"
      >
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
          trend={
            account === undefined
              ? "neutral"
              : equity > initialBalance
                ? "up"
                : equity < initialBalance
                  ? "down"
                  : "neutral"
          }
          tip="Balance plus unrealised PnL from open positions."
          sub={accountOpenPositions > 0 ? `${accountOpenPositions} open position${accountOpenPositions !== 1 ? "s" : ""}` : "No open positions"}
        />
        <StatCard
          icon={CalendarDays}
          label="Daily PnL"
          value={formatPnl(dailyPnl, currency)}
          trend={dailyPnl > 0 ? "up" : dailyPnl < 0 ? "down" : "neutral"}
          tip="Realised profit/loss from trades closed today (UTC)."
          sub="Today, UTC"
          testId="stat-daily-pnl"
        />
        <StatCard
          icon={CalendarRange}
          label="Weekly PnL"
          value={formatPnl(weeklyPnl, currency)}
          trend={weeklyPnl > 0 ? "up" : weeklyPnl < 0 ? "down" : "neutral"}
          tip="Realised profit/loss from trades closed this week (UTC, Monday reset)."
          sub="This week, UTC"
          testId="stat-weekly-pnl"
        />
        <StatCard
          icon={Bot}
          label="Active Bots"
          value={`${activeBots}/${totalBots}`}
          tip="Bots running and producing fresh data. Excludes stale bots; running but stale bots are flagged below."
          sub={
            staleBots > 0
              ? `${staleBots} stale`
              : pausedBots > 0
                ? `${pausedBots} paused`
                : totalBots === 0
                  ? "None created"
                  : "All healthy"
          }
          testId="stat-running-bots"
        />
        <StatCard
          icon={Crosshair}
          label="Account Open Positions"
          value={String(accountOpenPositions)}
          tip="Open paper-account positions reported by the account ledger. Compare to fleet open positions in the Fleet Overview."
          sub={`${totalTrades} lifetime trades`}
          testId="stat-open-positions"
        />
        {isIgDemo && (
          <StatCard
            icon={Zap}
            label="IG Demo"
            value={igHealth?.reachable && igHealth.balance != null ? `£${igHealth.balance.toFixed(2)}` : igHealth?.reachable === false ? "Error" : "—"}
            trend={igHealth?.reachable ? "neutral" : undefined}
            tip="Live IG demo account balance — independent from the Fiboki paper account. Fetched directly from IG, refreshes every 60s."
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
            <p className="text-xs text-foreground-muted inline-flex items-center">
              Fleet Open Positions
              <InfoTip text="Aggregate open positions across the bot fleet. May differ from the account-level open positions when reconciliation is mid-cycle." />
            </p>
            <p className="text-lg font-bold tabular-nums">{fleetOpenPositions}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted inline-flex items-center">
              Fleet Trades
              <InfoTip text="Aggregate closed trades across the bot fleet for the current execution session. Resets when bots are restarted or a new evaluation phase is started." />
            </p>
            <p className="text-lg font-bold tabular-nums">{fleetTrades}</p>
          </div>
          <div>
            <p className="text-xs text-foreground-muted inline-flex items-center">
              Fleet PnL
              <InfoTip text="Aggregate PnL across all bots, including stopped bots." />
            </p>
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
          {activityLoading ? (
            <div className="space-y-3 py-2" data-testid="activity-loading">
              <SkeletonRow width="w-3/4" />
              <SkeletonRow width="w-1/2" />
              <SkeletonRow width="w-2/3" />
            </div>
          ) : activityItems.length === 0 ? (
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
            <p className="section-label !mb-0 inline-flex items-center">
              Saved Combos
              <InfoTip text="Your curated shortlist of strategy/instrument/timeframe combos. Promote top scorers to paper trading." />
            </p>
            <Link href="/research" className="text-xs text-primary hover:underline">Manage</Link>
          </div>
          {shortlistLoading ? (
            <div className="space-y-2 py-2" data-testid="shortlist-loading">
              <SkeletonRow width="w-full" />
              <SkeletonRow width="w-5/6" />
              <SkeletonRow width="w-2/3" />
            </div>
          ) : topShortlist.length === 0 ? (
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
                  {groupedShortlist.map((s) => {
                    const params = new URLSearchParams({
                      new: "1",
                      strategy: s.strategy_id,
                      instrument: s.instrument,
                      tf: s.timeframe,
                      shortlistId: String(s.id),
                    }).toString();
                    return (
                      <tr key={s.id} className="border-b border-gray-100 hover:bg-background-muted/50 transition-colors">
                        <td className={`py-1.5 pr-3 font-medium ${s.repeatStrategy ? "text-foreground-muted/60" : ""}`}>
                          {s.repeatStrategy ? <span aria-hidden="true">↳ </span> : null}
                          {s.strategy_id}
                        </td>
                        <td className="py-1.5 pr-3">{s.instrument}</td>
                        <td className="py-1.5 pr-3 text-foreground-muted">{s.timeframe}</td>
                        <td className="py-1.5 pr-2 text-right tabular-nums">
                          <span className={s.score >= 0.7 ? "text-primary font-medium" : s.score >= 0.55 ? "" : "text-foreground-muted"}>
                            {s.score.toFixed(3)}
                          </span>
                        </td>
                        <td className="py-1.5 text-right">
                          <Link
                            href={`/bots?${params}`}
                            className="text-xs text-primary hover:underline"
                            title={`Create a bot from ${s.strategy_id} on ${s.instrument} ${s.timeframe}`}
                          >
                            Create Bot
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Health / Risk / Readiness Panel */}
        <div className="card" data-testid="health-panel">
          <p className="section-label inline-flex items-center">
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
                <span className="text-sm inline-flex items-center">
                  Execution Mode
                  <InfoTip text="Current trading execution mode. 'paper' = simulated only. 'ig_demo' = connected to IG demo account (no real money)." />
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
                <span className="text-sm inline-flex items-center">
                  Kill Switch
                  <InfoTip text="Emergency stop for all execution. When active, no new trades are opened. Existing positions continue under their own stops/targets." />
                </span>
              </div>
              <button
                onClick={() => setKillSwitchModalOpen(true)}
                disabled={killSwitchLoading}
                className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg font-medium transition-colors disabled:opacity-50 border ${
                  killSwitchActive
                    ? "bg-red-100 text-red-800 border-red-300 hover:bg-red-200"
                    : "bg-background-muted text-foreground border-gray-300 hover:bg-gray-200"
                }`}
                aria-label={killSwitchActive ? "Deactivate kill switch" : "Activate kill switch"}
              >
                {killSwitchLoading ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : killSwitchActive ? (
                  <ShieldAlert size={11} />
                ) : (
                  <ShieldCheck size={11} />
                )}
                {killSwitchActive ? "Active — Deactivate" : "Armed — Activate"}
              </button>
            </div>

            {/* Database */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Layers size={14} className="text-foreground-muted" />
                <span className="text-sm">Database</span>
              </div>
              <StatusBadge variant={dbHealthy ? "ok" : dbStatus === undefined ? "neutral" : "error"}>
                {dbHealthy ? "Connected" : (dbStatus ?? "—")}
              </StatusBadge>
            </div>

            {/* Strategies */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Target size={14} className="text-foreground-muted" />
                <span className="text-sm inline-flex items-center">
                  Strategies
                  <InfoTip text="Strategies registered in the running backend (per /system/status). Fiboki ships with 12 strategy bots — a count below 12 indicates a registry / discovery issue worth checking on the System page." />
                </span>
              </div>
              {strategiesLoaded === undefined ? (
                <span className="text-sm text-foreground-muted">—</span>
              ) : strategiesLoaded < 12 ? (
                <Link href="/system" className="text-sm font-medium tabular-nums text-amber-700 hover:underline">
                  {strategiesLoaded} loaded ⚠
                </Link>
              ) : (
                <span className="text-sm font-medium tabular-nums">{strategiesLoaded} loaded</span>
              )}
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
                <span className="text-sm inline-flex items-center">
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

            {/* Evaluation Phase */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Flag size={14} className="text-foreground-muted" />
                <span className="text-sm inline-flex items-center">
                  Eval Phase
                  <InfoTip text="Current evaluation phase. Each phase tracks performance from a clean £1,000 baseline. Archived phases preserve the full trade history." />
                </span>
              </div>
              {activePhase === undefined ? (
                <span className="text-xs text-foreground-muted">—</span>
              ) : activePhase === null ? (
                <Link href="/bots" className="text-xs text-amber-700 bg-amber-50 px-2 py-0.5 rounded-lg hover:bg-amber-100 transition-colors">
                  No active phase
                </Link>
              ) : (
                <Link href="/bots" className="text-xs font-medium text-primary bg-primary/8 px-2 py-0.5 rounded-lg hover:bg-primary/12 transition-colors">
                  {activePhase.name}
                </Link>
              )}
            </div>

            {/* IG Demo Readiness */}
            <div className="border-t border-gray-100 pt-3 mt-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <Eye size={14} className="text-foreground-muted" />
                  <span className="text-sm inline-flex items-center">
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

      {/* ─── Footer: data freshness indicator ─────────────────── */}
      <div className="flex items-center justify-end gap-2 pt-2 mt-2 border-t border-gray-100 text-[11px] text-foreground-muted" data-testid="dashboard-freshness">
        <Clock size={11} />
        <span>
          Auto-refresh enabled.{" "}
          {lastUpdated
            ? <>Last updated <span className="tabular-nums" title={new Date(lastUpdated).toISOString()}>{formatTime(lastUpdated)}</span> · {new Date(now).toUTCString().slice(17, 25)} UTC</>
            : "Loading..."}
        </span>
      </div>

      {/* ─── Kill switch confirmation modal ───────────────────── */}
      <KillSwitchModal
        open={killSwitchModalOpen}
        isActive={killSwitchActive}
        loading={killSwitchLoading}
        onCancel={() => (killSwitchLoading ? null : setKillSwitchModalOpen(false))}
        onConfirm={handleKillSwitchConfirm}
        openPositions={Math.max(accountOpenPositions, fleetOpenPositions)}
        runningBots={runningBots}
      />
    </div>
  );
}
