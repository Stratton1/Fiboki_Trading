"use client";

import dynamic from "next/dynamic";
import useSWR from "swr";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { EquityCurve } from "@/components/analytics/EquityCurve";
import { DrawdownChart } from "@/components/analytics/DrawdownChart";
import { Distribution } from "@/components/analytics/Distribution";
import {
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Activity,
  Target,
  BarChart2,
  Info,
  Download,
  Loader2,
} from "lucide-react";
import { formatCurrency, currencySymbol as getCurrencySymbol } from "@/lib/format-currency";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

// ── KPI Card ──────────────────────────────────────────────────────────────────
function KpiCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: "positive" | "negative" | "neutral" | "warn";
}) {
  const accentClass =
    accent === "positive"
      ? "text-primary"
      : accent === "negative"
      ? "text-danger"
      : accent === "warn"
      ? "text-amber-600"
      : "text-foreground";
  return (
    <div className="stat-card">
      <p className="text-xs font-medium uppercase tracking-wide text-foreground-muted mb-2">
        {label}
      </p>
      <p className={`text-2xl font-bold tabular-nums ${accentClass}`}>{value}</p>
      {sub && <p className="text-xs text-foreground-muted mt-1">{sub}</p>}
    </div>
  );
}

// ── Section Header ────────────────────────────────────────────────────────────
function SectionHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-base font-semibold">{title}</h2>
      {sub && <p className="text-xs text-foreground-muted mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Loading skeleton ──────────────────────────────────────────────────────────
function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`animate-pulse bg-background-muted rounded-lg ${className}`} />
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function AnalyticsPage() {
  const { data: analytics, isLoading: aLoading, error: aError } =
    useSWR("/paper/analytics", () => api.paperAnalytics(), { refreshInterval: 60000 });
  const { data: account, isLoading: accLoading } =
    useSWR("/paper/account", () => api.account(), { refreshInterval: 60000 });
  const { data: fleet, isLoading: fleetLoading } =
    useSWR("/paper/fleet", () => api.fleet(), { refreshInterval: 30000 });

  const isLoading = aLoading || accLoading || fleetLoading;

  const sym = getCurrencySymbol(account?.currency ?? "GBP");
  const initialBalance = account?.initial_balance ?? 1000;
  const currentBalance = account?.balance ?? initialBalance;
  const totalReturn = ((currentBalance - initialBalance) / initialBalance) * 100;
  const totalReturnPnl = currentBalance - initialBalance;

  // Absolute equity curve (starting from initial balance)
  const equityCurveAbsolute =
    analytics?.equity_curve.map((pnl) => initialBalance + pnl) ?? [];

  // Sort bots for the performance table
  const sortedBots = [...(fleet?.bots ?? [])].sort(
    (a, b) => b.total_pnl - a.total_pnl
  );

  // Strategy horizontal bar data
  const strategyEntries = Object.entries(analytics?.pnl_by_strategy ?? {}).sort(
    (a, b) => b[1].pnl - a[1].pnl
  );

  // Instrument bar data (top 15)
  const instrumentEntries = Object.entries(analytics?.pnl_by_instrument ?? {})
    .sort((a, b) => b[1].pnl - a[1].pnl)
    .slice(0, 15);

  // Exit reason data
  const exitReasonEntries = Object.entries(analytics?.pnl_by_exit_reason ?? {}).sort(
    (a, b) => b[1].trades - a[1].trades
  );

  // Direction data
  const directionEntries = Object.entries(analytics?.pnl_by_direction ?? {});

  if (aError) {
    return (
      <div className="max-w-6xl">
        <PageHeader title="Analytics" subtitle="Paper trading performance analysis" />
        <div className="card border-red-200 bg-red-50 text-red-800 text-sm p-4 flex items-center gap-2">
          <AlertTriangle size={16} />
          Failed to load analytics data. Check that the backend is reachable.
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl space-y-8">
      <div className="flex items-start justify-between gap-4">
        <PageHeader
          title="Analytics"
          subtitle={
            analytics?.first_trade_date
              ? `Paper trading performance · ${new Date(analytics.first_trade_date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })} – ${analytics.last_trade_date ? new Date(analytics.last_trade_date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "present"} · ${analytics.days_active} days`
              : "Paper trading performance"
          }
        />
        <a
          href={`${api.exportAllTrades()}${typeof window !== "undefined" && localStorage.getItem("fibokei_token") ? `?token=${localStorage.getItem("fibokei_token")}` : ""}`}
          className="btn btn-secondary text-xs flex items-center gap-1.5 shrink-0 mt-1"
          title="Export all paper trades to Excel"
        >
          <Download size={12} /> Export Trades
        </a>
      </div>

      {/* ── Cost Audit Warning ─────────────────────────────────────── */}
      <div className="rounded-xl border border-amber-300 bg-amber-50 px-5 py-4">
        <p className="text-sm font-semibold text-amber-900 flex items-center gap-2 mb-1.5">
          <AlertTriangle size={15} className="text-amber-600" />
          Paper Trading Realism Caveats
        </p>
        <ul className="text-xs text-amber-800 space-y-0.5 list-disc list-inside">
          <li>
            <strong>No spread applied</strong> — entry/exit prices are raw mid prices. Realistic spreads
            (e.g. 0.12 pip on EURUSD, £0.35 on Gold) would reduce PnL by an estimated 15–25%.
          </li>
          <li>
            <strong>No slippage or overnight financing</strong> — all fills are instant at the signal price.
          </li>
          <li>
            <strong>USD→GBP FX approximated as 1:1</strong> — USD-quoted instruments (XAU, US500, oil)
            overstate GBP PnL by ~25–32%. Apply a ≈0.79 multiplier for realistic estimates.
          </li>
          <li>
            <strong>Backtests apply costs; paper bots do not</strong> — backtest/paper parity is incomplete.
          </li>
        </ul>
        <p className="text-xs text-amber-700 mt-2 font-medium">
          Estimated realistic return after cost adjustments: 190–230% vs displayed {isLoading ? "…" : `${totalReturn.toFixed(1)}%`}
        </p>
      </div>

      {/* ── Section 1: Account KPIs ────────────────────────────────── */}
      <div>
        <SectionHeader title="Account Overview" sub="Live paper account position" />
        {isLoading ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
            {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-[82px]" />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <KpiCard
              label="Balance"
              value={`${sym}${currentBalance.toFixed(2)}`}
              sub={`Started: ${sym}${initialBalance.toFixed(0)}`}
              accent="neutral"
            />
            <KpiCard
              label="Total Return"
              value={`${totalReturn >= 0 ? "+" : ""}${totalReturn.toFixed(1)}%`}
              sub={`${sym}${totalReturnPnl >= 0 ? "+" : ""}${totalReturnPnl.toFixed(2)} net PnL`}
              accent={totalReturn >= 0 ? "positive" : "negative"}
            />
            <KpiCard
              label="Win Rate"
              value={`${analytics?.win_rate.toFixed(1) ?? "—"}%`}
              sub={`${analytics?.win_trades ?? 0}W / ${analytics?.loss_trades ?? 0}L`}
              accent={
                (analytics?.win_rate ?? 0) >= 55
                  ? "positive"
                  : (analytics?.win_rate ?? 0) >= 45
                  ? "neutral"
                  : "negative"
              }
            />
            <KpiCard
              label="Profit Factor"
              value={analytics?.profit_factor.toFixed(2) ?? "—"}
              sub="Gross profit ÷ gross loss"
              accent={
                (analytics?.profit_factor ?? 0) >= 1.5
                  ? "positive"
                  : (analytics?.profit_factor ?? 0) >= 1
                  ? "warn"
                  : "negative"
              }
            />
            <KpiCard
              label="Expectancy"
              value={`${sym}${analytics?.expectancy.toFixed(2) ?? "—"}`}
              sub="Avg PnL per trade"
              accent={(analytics?.expectancy ?? 0) >= 0 ? "positive" : "negative"}
            />
            <KpiCard
              label="Total Trades"
              value={String(analytics?.total_trades ?? "—")}
              sub={`${analytics?.days_active ?? 0} days active`}
              accent="neutral"
            />
          </div>
        )}
      </div>

      {/* ── Section 2: Equity Curve + Drawdown ───────────────────── */}
      <div>
        <SectionHeader
          title="Equity Curve"
          sub="Cumulative account balance over all closed paper trades"
        />
        {isLoading ? (
          <Skeleton className="h-[300px]" />
        ) : equityCurveAbsolute.length > 0 ? (
          <div className="card p-0 overflow-hidden">
            <div className="grid grid-cols-1 lg:grid-cols-2 divide-y lg:divide-y-0 lg:divide-x divide-border">
              <EquityCurve
                data={equityCurveAbsolute}
                height={280}
                title={`Account Equity (${sym})`}
              />
              <DrawdownChart data={equityCurveAbsolute} height={280} />
            </div>
          </div>
        ) : (
          <div className="card text-sm text-foreground-muted text-center py-8">
            No closed trades yet.
          </div>
        )}
      </div>

      {/* ── Section 3: Trade Statistics ────────────────────────────── */}
      <div>
        <SectionHeader
          title="Trade Statistics"
          sub="Distribution and win/loss profile"
        />
        {isLoading ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Skeleton className="h-[260px]" />
            <Skeleton className="h-[260px]" />
          </div>
        ) : (analytics?.trade_pnl_list.length ?? 0) > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="card p-0 overflow-hidden">
              <Distribution
                data={analytics!.trade_pnl_list}
                title="PnL Distribution per Trade"
                height={260}
              />
            </div>
            <div className="card p-4">
              <p className="section-label mb-3">Win / Loss Profile</p>
              <div className="space-y-2.5">
                {[
                  {
                    label: "Avg Win",
                    value: `${sym}${analytics!.avg_win.toFixed(2)}`,
                    bar: Math.min((analytics!.avg_win / (analytics!.max_trade_pnl || 1)) * 100, 100),
                    color: "bg-primary",
                  },
                  {
                    label: "Avg Loss",
                    value: `${sym}${analytics!.avg_loss.toFixed(2)}`,
                    bar: Math.min(
                      (Math.abs(analytics!.avg_loss) / (Math.abs(analytics!.min_trade_pnl) || 1)) * 100,
                      100
                    ),
                    color: "bg-danger",
                  },
                  {
                    label: "Best Trade",
                    value: `+${sym}${analytics!.max_trade_pnl.toFixed(2)}`,
                    bar: 100,
                    color: "bg-primary",
                  },
                  {
                    label: "Worst Trade",
                    value: `${sym}${analytics!.min_trade_pnl.toFixed(2)}`,
                    bar: 100,
                    color: "bg-danger",
                  },
                ].map(({ label, value, bar, color }) => (
                  <div key={label}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-foreground-muted">{label}</span>
                      <span className="font-medium tabular-nums">{value}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-background-muted overflow-hidden">
                      <div
                        className={`h-full rounded-full ${color} transition-all`}
                        style={{ width: `${bar}%` }}
                      />
                    </div>
                  </div>
                ))}
                <div className="pt-2 border-t border-border grid grid-cols-2 gap-y-1.5 text-xs">
                  <span className="text-foreground-muted">Payoff Ratio</span>
                  <span className="font-medium text-right tabular-nums">
                    {analytics!.avg_loss !== 0
                      ? (analytics!.avg_win / Math.abs(analytics!.avg_loss)).toFixed(2)
                      : "—"}
                  </span>
                  <span className="text-foreground-muted">Total Gross Profit</span>
                  <span className="font-medium text-primary text-right tabular-nums">
                    +{sym}
                    {analytics!.trade_pnl_list
                      .filter((p) => p > 0)
                      .reduce((s, p) => s + p, 0)
                      .toFixed(2)}
                  </span>
                  <span className="text-foreground-muted">Total Gross Loss</span>
                  <span className="font-medium text-danger text-right tabular-nums">
                    {sym}
                    {analytics!.trade_pnl_list
                      .filter((p) => p <= 0)
                      .reduce((s, p) => s + p, 0)
                      .toFixed(2)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="card text-sm text-foreground-muted text-center py-8">
            No closed trades yet.
          </div>
        )}
      </div>

      {/* ── Section 4: Strategy Breakdown ─────────────────────────── */}
      <div>
        <SectionHeader
          title="Performance by Strategy"
          sub="Aggregate PnL and trade count per strategy family"
        />
        {isLoading ? (
          <Skeleton className="h-[200px]" />
        ) : strategyEntries.length > 0 ? (
          <div className="card p-0 overflow-hidden">
            <Plot
              data={[
                {
                  type: "bar",
                  orientation: "h",
                  y: strategyEntries.map(([s]) => s),
                  x: strategyEntries.map(([, v]) => v.pnl),
                  text: strategyEntries.map(
                    ([, v]) =>
                      `${sym}${v.pnl >= 0 ? "+" : ""}${v.pnl.toFixed(2)} · ${v.trades} trades · ${v.trades > 0 ? Math.round((v.wins / v.trades) * 100) : 0}% WR`
                  ),
                  textposition: "outside",
                  marker: {
                    color: strategyEntries.map(([, v]) =>
                      v.pnl >= 0 ? "#16A34A" : "#EF4444"
                    ),
                  },
                } as unknown as Plotly.Data,
              ]}
              layout={{
                height: Math.max(120, strategyEntries.length * 52 + 60),
                margin: { t: 20, r: 180, b: 40, l: 220 },
                paper_bgcolor: "transparent",
                plot_bgcolor: "transparent",
                xaxis: {
                  gridcolor: "#E5E5E5",
                  tickprefix: sym,
                  zeroline: true,
                  zerolinecolor: "#D4D4D4",
                },
                yaxis: { automargin: true },
                font: { family: "Inter, system-ui, sans-serif", size: 12 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        ) : (
          <div className="card text-sm text-foreground-muted text-center py-8">
            No trades to show.
          </div>
        )}
      </div>

      {/* ── Section 5: Instrument + Direction + Exit Reason ─────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Instrument */}
        <div className="lg:col-span-2">
          <SectionHeader title="Performance by Instrument" sub="Top 15 by trade count" />
          {isLoading ? (
            <Skeleton className="h-[320px]" />
          ) : instrumentEntries.length > 0 ? (
            <div className="card p-0 overflow-hidden">
              <Plot
                data={[
                  {
                    type: "bar",
                    orientation: "h",
                    y: instrumentEntries.map(([i]) => i),
                    x: instrumentEntries.map(([, v]) => v.pnl),
                    text: instrumentEntries.map(
                      ([, v]) => `${v.trades}t · ${v.trades > 0 ? Math.round((v.wins / v.trades) * 100) : 0}%WR`
                    ),
                    textposition: "outside",
                    marker: {
                      color: instrumentEntries.map(([, v]) =>
                        v.pnl >= 0 ? "#16A34A" : "#EF4444"
                      ),
                    },
                  } as unknown as Plotly.Data,
                ]}
                layout={{
                  height: Math.max(160, instrumentEntries.length * 28 + 60),
                  margin: { t: 20, r: 120, b: 40, l: 90 },
                  paper_bgcolor: "transparent",
                  plot_bgcolor: "transparent",
                  xaxis: {
                    gridcolor: "#E5E5E5",
                    tickprefix: sym,
                    zeroline: true,
                    zerolinecolor: "#D4D4D4",
                  },
                  yaxis: { automargin: true },
                  font: { family: "Inter, system-ui, sans-serif", size: 11 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          ) : (
            <div className="card text-sm text-foreground-muted text-center py-8">
              No trades to show.
            </div>
          )}
        </div>

        {/* Direction + Exit Reason */}
        <div className="space-y-6">
          <div>
            <SectionHeader title="By Direction" />
            {isLoading ? (
              <Skeleton className="h-[180px]" />
            ) : directionEntries.length > 0 ? (
              <div className="card p-0 overflow-hidden">
                <Plot
                  data={[
                    {
                      type: "pie",
                      labels: directionEntries.map(([d]) => d),
                      values: directionEntries.map(([, v]) => v.trades),
                      text: directionEntries.map(
                        ([, v]) => `${sym}${v.pnl >= 0 ? "+" : ""}${v.pnl.toFixed(0)}`
                      ),
                      hovertext: directionEntries.map(
                        ([d, v]) =>
                          `${d}: ${v.trades} trades · ${sym}${v.pnl >= 0 ? "+" : ""}${v.pnl.toFixed(2)}`
                      ),
                      hoverinfo: "text",
                      textinfo: "label+percent",
                      hole: 0.45,
                      marker: { colors: ["#16A34A", "#3B82F6", "#F59E0B"] },
                    } as unknown as Plotly.Data,
                  ]}
                  layout={{
                    height: 200,
                    margin: { t: 10, r: 10, b: 10, l: 10 },
                    paper_bgcolor: "transparent",
                    showlegend: false,
                    font: { family: "Inter, system-ui, sans-serif", size: 11 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            ) : null}
          </div>

          <div>
            <SectionHeader title="By Exit Reason" />
            {isLoading ? (
              <Skeleton className="h-[180px]" />
            ) : exitReasonEntries.length > 0 ? (
              <div className="card p-0 overflow-hidden">
                <Plot
                  data={[
                    {
                      type: "pie",
                      labels: exitReasonEntries.map(([r]) =>
                        r.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
                      ),
                      values: exitReasonEntries.map(([, v]) => v.trades),
                      hovertext: exitReasonEntries.map(
                        ([r, v]) =>
                          `${r}: ${v.trades} trades · ${sym}${v.pnl >= 0 ? "+" : ""}${v.pnl.toFixed(2)}`
                      ),
                      hoverinfo: "text",
                      textinfo: "label+percent",
                      hole: 0.45,
                      marker: {
                        colors: ["#16A34A", "#3B82F6", "#EF4444", "#F59E0B", "#8B5CF6", "#EC4899"],
                      },
                    } as unknown as Plotly.Data,
                  ]}
                  layout={{
                    height: 200,
                    margin: { t: 10, r: 10, b: 10, l: 10 },
                    paper_bgcolor: "transparent",
                    showlegend: false,
                    font: { family: "Inter, system-ui, sans-serif", size: 11 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* ── Section 6: Bot Performance Table ──────────────────────── */}
      <div>
        <SectionHeader
          title="Bot Performance"
          sub="All bots sorted by realised PnL — stale bots highlighted"
        />
        {isLoading ? (
          <Skeleton className="h-[240px]" />
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th className="text-left">Strategy</th>
                  <th className="text-left">Instrument</th>
                  <th className="text-left">TF</th>
                  <th className="text-left">State</th>
                  <th className="text-right">Trades</th>
                  <th className="text-right">PnL</th>
                  <th className="text-right">WR</th>
                </tr>
              </thead>
              <tbody>
                {sortedBots.length === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center text-foreground-muted py-6 text-sm">
                      No bots found.
                    </td>
                  </tr>
                )}
                {sortedBots.map((bot) => {
                  // Get per-bot win rate from analytics
                  const botSid = bot.strategy_id;
                  const stateColor =
                    bot.state === "monitoring" || bot.state === "position_open"
                      ? "text-primary"
                      : bot.state === "paused"
                      ? "text-amber-600"
                      : "text-foreground-muted";
                  return (
                    <tr key={bot.bot_id} className={bot.is_stale ? "bg-amber-50/50" : ""}>
                      <td className="font-medium text-xs">{bot.strategy_id}</td>
                      <td>{bot.instrument}</td>
                      <td className="text-foreground-muted">{bot.timeframe}</td>
                      <td>
                        <span className={`text-xs font-medium ${stateColor}`}>
                          {bot.state}
                          {bot.is_stale && (
                            <span className="ml-1 text-amber-500" title="Data may be stale">
                              ⚠
                            </span>
                          )}
                        </span>
                      </td>
                      <td className="text-right tabular-nums text-sm">{bot.total_trades}</td>
                      <td
                        className={`text-right tabular-nums text-sm font-medium ${
                          bot.total_pnl >= 0 ? "text-primary" : "text-danger"
                        }`}
                      >
                        {bot.total_pnl >= 0 ? "+" : ""}
                        {sym}
                        {bot.total_pnl.toFixed(2)}
                      </td>
                      <td className="text-right tabular-nums text-xs text-foreground-muted">—</td>
                    </tr>
                  );
                })}
              </tbody>
              {sortedBots.length > 0 && (
                <tfoot>
                  <tr className="border-t-2 border-border">
                    <td colSpan={4} className="text-xs font-semibold text-foreground-muted pt-2">
                      Fleet Total ({fleet?.total_bots ?? 0} bots)
                    </td>
                    <td className="text-right tabular-nums text-sm font-semibold pt-2">
                      {fleet?.aggregate_trades ?? 0}
                    </td>
                    <td
                      className={`text-right tabular-nums text-sm font-bold pt-2 ${
                        (fleet?.aggregate_pnl ?? 0) >= 0 ? "text-primary" : "text-danger"
                      }`}
                    >
                      {(fleet?.aggregate_pnl ?? 0) >= 0 ? "+" : ""}
                      {sym}
                      {(fleet?.aggregate_pnl ?? 0).toFixed(2)}
                    </td>
                    <td className="pt-2 text-right text-xs text-foreground-muted">
                      {analytics?.win_rate.toFixed(1) ?? "—"}%
                    </td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </div>

      {/* ── Section 7: Report Callout ──────────────────────────────── */}
      <div className="card border-blue-200 bg-blue-50/60">
        <div className="flex items-center gap-2 mb-3">
          <Info size={15} className="text-blue-600 shrink-0" />
          <h3 className="text-sm font-semibold text-blue-900">Platform Report — Phase A Initial Testing</h3>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-1.5 text-xs text-blue-800">
          {[
            ["Period", "1 Apr – 8 May 2026 (37 days)"],
            ["Starting Balance", "£1,000"],
            ["Closing Balance", `${sym}${currentBalance.toFixed(2)}`],
            ["Gross Return (paper)", `+${totalReturn.toFixed(1)}%`],
            ["Estimated Realistic Return", "190–230% (after spread & FX correction)"],
            ["Total Closed Trades", String(analytics?.total_trades ?? "—")],
            ["Win Rate", `${analytics?.win_rate.toFixed(1) ?? "—"}%`],
            ["Profit Factor", analytics?.profit_factor.toFixed(2) ?? "—"],
            ["Expectancy / Trade", `${sym}${analytics?.expectancy.toFixed(2) ?? "—"}`],
            ["Bot04 (chikou_momentum)", "Drives 99.8% of profit — 66% WR, dominant"],
            ["Bot06 (nwave)", "0.2% of profit — 28% WR, underperforming"],
            ["Active Bots", `${fleet?.running ?? "—"} / ${fleet?.total_bots ?? "—"} (${fleet?.stale ?? 0} stale)`],
            ["Cost Model", "No spread · No slippage · No financing · 1:1 FX"],
            ["Signal Basis", "Closed candles only — correct implementation"],
            ["Readiness", "Strong alpha signal; not yet show-ready for Tom without cost caveats"],
          ].map(([label, val]) => (
            <div key={label} className="flex gap-2">
              <span className="font-medium shrink-0 min-w-[160px]">{label}:</span>
              <span>{val}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
