"use client";

import { use, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { EquityCurve } from "@/components/analytics/EquityCurve";
import { BotExecutionTargetsCard } from "@/components/BotExecutionTargetsCard";
import { strategyShortName } from "@/lib/strategy-names";
import {
  ArrowLeft,
  Loader2,
  Activity,
  TrendingUp,
  BarChart3,
  Clock,
  AlertTriangle,
} from "lucide-react";
import { formatCurrency, formatPnl } from "@/lib/format-currency";
import dynamic from "next/dynamic";
import { useMarketData } from "@/lib/hooks/use-market-data";

const TradeMarkerChart = dynamic(
  () => import("@/components/charts/core/TradeMarkerChart"),
  { ssr: false },
);

const STATE_VARIANT: Record<string, "ok" | "warn" | "neutral"> = {
  monitoring: "ok",
  position_open: "ok",
  paused: "warn",
  stopped: "neutral",
  idle: "neutral",
};

export default function BotDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [liveOnly, setLiveOnly] = useState(true);
  const [actingBotId, setActingBotId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data: bot, mutate: mutateBot } = useSWR(
    id ? `/paper/bots/${id}` : null,
    () => api.getBot(id),
    { refreshInterval: 5000 }
  );

  const { data: tradeData, isLoading: tradesLoading } = useSWR(
    id ? `/paper/bots/${id}/trades?live_only=${liveOnly}` : null,
    () => api.botTrades(id, liveOnly),
    { refreshInterval: 10000 }
  );

  // Price chart for THIS bot's instrument + timeframe (markers from its trades).
  const bInstrument = (bot as Record<string, unknown> | undefined)?.instrument as
    | string
    | undefined;
  const bTimeframe = (bot as Record<string, unknown> | undefined)?.timeframe as
    | string
    | undefined;
  const { data: marketData } = useMarketData(bInstrument ?? null, bTimeframe ?? null);

  async function handleAction(fn: () => Promise<unknown>) {
    setActingBotId(id);
    setActionError(null);
    try {
      await fn();
      await mutateBot();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActingBotId(null);
    }
  }

  if (!bot) {
    return (
      <div className="max-w-4xl">
        <Link href="/bots" className="inline-flex items-center gap-1.5 text-sm text-foreground-muted hover:text-foreground mb-4">
          <ArrowLeft size={14} /> Back to Bots
        </Link>
        <p className="text-foreground-muted">Loading bot...</p>
      </div>
    );
  }

  const botAny = bot as Record<string, unknown>;
  const position = botAny.position as Record<string, unknown> | null;
  const trades = tradeData?.trades ?? [];
  const equityCurve = tradeData?.equity_curve ?? [];
  const totalPnl = trades.reduce((sum: number, t: Record<string, unknown>) => sum + (t.pnl as number ?? 0), 0);
  const winCount = trades.filter((t: Record<string, unknown>) => (t.pnl as number) > 0).length;
  const winRate = trades.length > 0 ? (winCount / trades.length) * 100 : 0;

  const sourceLabel = botAny.source_type === "backtest"
    ? `Backtest #${botAny.source_id}`
    : botAny.source_type === "research"
    ? `Research run ${botAny.source_id}`
    : "Manual";

  return (
    <div className="max-w-5xl">
      <Link href="/bots" className="inline-flex items-center gap-1.5 text-sm text-foreground-muted hover:text-foreground mb-4">
        <ArrowLeft size={14} /> Back to Bots
      </Link>

      <PageHeader
        title={`Bot ${botAny.bot_id as string}`}
        subtitle={`${strategyShortName(botAny.strategy_id as string)} · ${botAny.instrument} · ${botAny.timeframe}`}
      />

      {/* Status + actions */}
      <div className="card mb-6">
        {/* Status row */}
        <div className="flex flex-wrap items-center gap-3 mb-4 pb-4 border-b border-border">
          <StatusBadge variant={STATE_VARIANT[botAny.state as string] ?? "neutral"}>
            {botAny.state as string}
          </StatusBadge>
          <span className="text-xs text-foreground-muted">{sourceLabel}</span>
          {(botAny.state as string) === "monitoring" && (
            <span className="flex items-center gap-1 text-xs text-foreground-muted">
              <Activity size={11} className="text-green-500" /> Watching for signals
            </span>
          )}
          {((botAny.state as string) === "stopped" || (botAny.state as string) === "idle") && (
            <span className="flex items-center gap-1 text-xs text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200">
              <AlertTriangle size={10} /> Not monitoring — use Restart to resume
            </span>
          )}
        </div>

        {/* Controls row */}
        <div>
          <p className="text-xs font-medium text-foreground-muted uppercase tracking-wide mb-3">Bot Controls</p>
          <div className="flex flex-wrap items-center gap-2">
            {((botAny.state as string) === "stopped" || (botAny.state as string) === "idle") && (
              <button
                onClick={() => handleAction(() => api.restartBot(id))}
                disabled={!!actingBotId}
                className="btn btn-primary text-sm px-4 py-2 flex items-center gap-2"
                title="Put bot back into monitoring mode to resume signal detection"
              >
                {actingBotId ? <Loader2 size={14} className="animate-spin" /> : <Activity size={14} />}
                {actingBotId ? "Restarting…" : "Restart Bot"}
              </button>
            )}
            {(botAny.state as string) === "paused" && (
              <button
                onClick={() => handleAction(() => api.resumeBot(id))}
                disabled={!!actingBotId}
                className="btn btn-primary text-sm px-4 py-2 flex items-center gap-2"
              >
                {actingBotId ? <Loader2 size={14} className="animate-spin" /> : <Activity size={14} />}
                {actingBotId ? "Resuming…" : "Resume Bot"}
              </button>
            )}
            {(botAny.state as string) === "monitoring" && (
              <button
                onClick={() => handleAction(() => api.pauseBot(id))}
                disabled={!!actingBotId}
                className="btn btn-secondary text-sm px-4 py-2"
              >
                {actingBotId ? <Loader2 size={14} className="animate-spin" /> : "Pause"}
              </button>
            )}
            {(botAny.state as string) !== "stopped" && (botAny.state as string) !== "idle" && (
              <button
                onClick={() => handleAction(() => api.stopBot(id))}
                disabled={!!actingBotId}
                className="btn text-sm px-4 py-2 border border-red-200 text-danger hover:bg-red-50"
              >
                {actingBotId ? <Loader2 size={14} className="animate-spin" /> : "Stop Bot"}
              </button>
            )}
          </div>
        </div>

        {actionError && (
          <p className="text-danger text-xs mt-3 pt-3 border-t border-border">{actionError}</p>
        )}
      </div>

      {/* Phase 2: per-bot execution targets */}
      <BotExecutionTargetsCard botId={id} />

      {/* Metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <div className="stat-card">
          <p className="text-xs text-foreground-muted mb-1">Total PnL</p>
          <p className={`text-xl font-bold ${totalPnl >= 0 ? "text-primary" : "text-danger"}`}>
            {totalPnl >= 0 ? "+" : ""}£{totalPnl.toFixed(2)}
          </p>
        </div>
        <div className="stat-card">
          <p className="text-xs text-foreground-muted mb-1">Trades</p>
          <p className="text-xl font-bold">{trades.length}</p>
        </div>
        <div className="stat-card">
          <p className="text-xs text-foreground-muted mb-1">Win Rate</p>
          <p className="text-xl font-bold">{winRate.toFixed(0)}%</p>
        </div>
        <div className="stat-card">
          <p className="text-xs text-foreground-muted mb-1">Bars Seen</p>
          <p className="text-xl font-bold">{botAny.bars_seen as number ?? 0}</p>
        </div>
      </div>

      {/* Open position */}
      {position && (
        <div className="card mb-6 border-l-4 border-l-primary">
          <p className="section-label mb-3">Open Position</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div>
              <p className="text-xs text-foreground-muted">Direction</p>
              <p className="font-medium">{position.direction as string}</p>
            </div>
            <div>
              <p className="text-xs text-foreground-muted">Entry Price</p>
              <p className="font-medium tabular-nums">{(position.entry_price as number)?.toFixed(5)}</p>
            </div>
            <div>
              <p className="text-xs text-foreground-muted">Stop Loss</p>
              <p className="font-medium tabular-nums text-danger">{(position.stop_loss as number)?.toFixed(5)}</p>
            </div>
            {(position.take_profit_targets as number[] | undefined)?.[0] && (
              <div>
                <p className="text-xs text-foreground-muted">Take Profit</p>
                <p className="font-medium tabular-nums text-primary">
                  {(position.take_profit_targets as number[])[0].toFixed(5)}
                </p>
              </div>
            )}
            <div>
              <p className="text-xs text-foreground-muted">Size</p>
              <p className="font-medium tabular-nums">{(position.position_size as number)?.toFixed(4)}</p>
            </div>
            <div>
              <p className="text-xs text-foreground-muted">Entry Time</p>
              <p className="font-medium text-xs">{position.entry_time as string}</p>
            </div>
            {position.bars_in_trade !== undefined && (
              <div>
                <p className="text-xs text-foreground-muted">Bars in Trade</p>
                <p className="font-medium">{position.bars_in_trade as number}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Price chart for this bot's instrument/timeframe with its trade markers */}
      {marketData && (
        <div className="card mb-6">
          <div className="flex items-center justify-between mb-3">
            <p className="section-label !mb-0">
              Price Chart — {bInstrument} · {bTimeframe}
            </p>
            <span className="text-xs text-foreground-muted">
              {trades.length} trade{trades.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="h-[420px]">
            <TradeMarkerChart
              data={marketData}
              trades={trades as unknown as import("@/types/contracts/trades").Trade[]}
              showEntries
              showExits
            />
          </div>
        </div>
      )}

      {/* Equity curve */}
      {equityCurve.length > 1 && (
        <div className="card mb-6">
          <p className="section-label mb-3">Equity Curve</p>
          <EquityCurve data={equityCurve} />
        </div>
      )}

      {/* Recent trades */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <p className="section-label">
            Trade History
            {tradesLoading && <Loader2 size={12} className="animate-spin inline ml-2" />}
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setLiveOnly(!liveOnly)}
              className={`text-xs px-3 py-1 rounded border transition-colors ${
                liveOnly
                  ? "bg-primary/10 border-primary text-primary"
                  : "border-gray-200 text-foreground-muted"
              }`}
              title={liveOnly ? "Showing live trades only (since bot was created). Click to show all trades including historical replay." : "Showing all trades including historical replay. Click to show only live trades."}
            >
              {liveOnly ? "Live Only" : "All Trades"}
            </button>
            <span className="text-xs text-foreground-muted">
              {tradeData?.total ?? 0} trades
            </span>
          </div>
        </div>
        {trades.length === 0 ? (
          <p className="text-sm text-foreground-muted">
            {liveOnly ? "No live trades yet (trades generated after bot creation). Toggle to 'All Trades' to see historical replay trades." : "No trades yet."}
          </p>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th className="text-left">Entry Time</th>
                  <th className="text-left">Dir</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Exit</th>
                  <th className="text-left">Reason</th>
                  <th className="text-right">Bars</th>
                  <th className="text-right">PnL</th>
                </tr>
              </thead>
              <tbody>
                {[...trades].reverse().map((t: Record<string, unknown>, i: number) => (
                  <tr key={i}>
                    <td className="text-xs">{(t.entry_time as string)?.slice(0, 16) ?? "—"}</td>
                    <td>
                      <span className={`text-xs font-medium ${t.direction === "LONG" ? "text-primary" : "text-danger"}`}>
                        {t.direction as string}
                      </span>
                    </td>
                    <td className="text-right tabular-nums text-xs">{(t.entry_price as number)?.toFixed(5)}</td>
                    <td className="text-right tabular-nums text-xs">{(t.exit_price as number)?.toFixed(5)}</td>
                    <td className="text-xs text-foreground-muted">{t.exit_reason as string}</td>
                    <td className="text-right text-xs tabular-nums">{t.bars_in_trade as number}</td>
                    <td className={`text-right tabular-nums font-medium text-sm ${(t.pnl as number) >= 0 ? "text-primary" : "text-danger"}`}>
                      {(t.pnl as number) >= 0 ? "+" : ""}£{(t.pnl as number)?.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
