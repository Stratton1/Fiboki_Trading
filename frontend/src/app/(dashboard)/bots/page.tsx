"use client";

import { Fragment, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useBots, useAccount } from "@/lib/hooks/use-bots";
import { currencySymbol as getCurrencySymbol, formatCurrency, formatPnl } from "@/lib/format-currency";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";
import WatchlistPicker from "@/components/WatchlistPicker";
import { useWatchlists } from "@/lib/hooks/use-watchlists";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { Bot, Loader2, Wallet, TrendingUp, CalendarDays, Activity, AlertTriangle, BarChart3, Search, Star, ChevronDown } from "lucide-react";
import { InfoTip } from "@/components/InfoTip";
import { strategyShortName } from "@/lib/strategy-names";
import { useShortlist } from "@/lib/hooks/use-shortlist";
import Link from "next/link";

interface BotItem {
  id: string;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  state: string;
}

const STATE_VARIANT: Record<string, "ok" | "warn" | "neutral"> = {
  monitoring: "ok",
  paused: "warn",
  stopped: "neutral",
};

export default function BotsPage() {
  const { data: bots, mutate: mutateBots } = useBots();
  const { data: account } = useAccount();
  const { data: fleet } = useSWR("/paper/fleet", () => api.fleet(), { refreshInterval: 5000 });
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { data: instruments } = useSWR("instruments", () => api.instruments());
  const { filterSet } = useWatchlists();
  const [strategy, setStrategy] = useState("");
  const [instrument, setInstrument] = useState("");
  const [timeframe, setTimeframe] = useState("H1");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actingBotId, setActingBotId] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<"none" | "strategy">("none");
  const { shortlist } = useShortlist();
  const [showShortlistPicker, setShowShortlistPicker] = useState(false);

  function loadFromShortlist(entry: { strategy_id: string; instrument: string; timeframe: string }) {
    setStrategy(entry.strategy_id);
    setInstrument(entry.instrument);
    setTimeframe(entry.timeframe);
    setShowShortlistPicker(false);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!strategy || !instrument) return;
    setCreating(true);
    setError(null);
    try {
      await api.createBot({ strategy_id: strategy, instrument, timeframe });
      setStrategy("");
      setInstrument("");
      await mutateBots();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create bot");
    } finally {
      setCreating(false);
    }
  }

  async function handlePause(id: string) {
    if (actingBotId) return;
    setActingBotId(id);
    setActionError(null);
    try {
      await api.pauseBot(id);
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to pause bot");
    } finally {
      setActingBotId(null);
    }
  }

  async function handleStop(id: string) {
    if (actingBotId) return;
    setActingBotId(id);
    setActionError(null);
    try {
      await api.stopBot(id);
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to stop bot");
    } finally {
      setActingBotId(null);
    }
  }

  const balance = account?.balance ?? 0;
  const equity = account?.equity ?? 0;
  const dailyPnl = account?.daily_pnl ?? 0;
  const currency = account?.currency ?? "GBP";
  const sym = getCurrencySymbol(currency);
  const botList = (bots ?? []) as BotItem[];
  const fleetBots = fleet?.bots ?? [];

  // Strategy groups for grouped view
  const strategyGroups = fleet?.strategy_groups ?? {};
  const groupedBots: Record<string, typeof fleetBots> = {};
  if (groupBy === "strategy") {
    for (const b of fleetBots) {
      if (!groupedBots[b.strategy_id]) groupedBots[b.strategy_id] = [];
      groupedBots[b.strategy_id].push(b);
    }
  }

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Paper Bots"
        subtitle="Manage paper trading bots and monitor fleet performance"
      />

      {/* Fleet Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Balance</span>
            <Wallet size={14} className="text-foreground-muted" />
          </div>
          <p className="text-xl font-bold tracking-tight">{sym}{balance.toFixed(2)}</p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Equity</span>
            <TrendingUp size={14} className="text-foreground-muted" />
          </div>
          <p className="text-xl font-bold tracking-tight">{sym}{equity.toFixed(2)}</p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Daily PnL</span>
            <CalendarDays size={14} className="text-foreground-muted" />
          </div>
          <p className={`text-xl font-bold tracking-tight ${dailyPnl >= 0 ? "text-primary" : "text-danger"}`}>
            {dailyPnl >= 0 ? "+" : ""}{sym}{dailyPnl.toFixed(2)}
          </p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Running</span>
            <Activity size={14} className="text-foreground-muted" />
          </div>
          <p className="text-xl font-bold tracking-tight">
            {fleet?.running ?? 0}
            <span className="text-sm font-normal text-foreground-muted"> / {fleet?.total_bots ?? 0}</span>
          </p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Fleet PnL<InfoTip text="Aggregate profit/loss across all paper bots. Combines realised and unrealised PnL from every active and stopped bot." /></span>
            <BarChart3 size={14} className="text-foreground-muted" />
          </div>
          <p className={`text-xl font-bold tracking-tight ${(fleet?.aggregate_pnl ?? 0) >= 0 ? "text-primary" : "text-danger"}`}>
            {(fleet?.aggregate_pnl ?? 0) >= 0 ? "+" : ""}{sym}{(fleet?.aggregate_pnl ?? 0).toFixed(2)}
          </p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Fleet Trades</span>
            {(fleet?.stale ?? 0) > 0 && <AlertTriangle size={14} className="text-amber-500" />}
          </div>
          <p className="text-xl font-bold tracking-tight">
            {fleet?.aggregate_trades ?? 0}
            {(fleet?.stale ?? 0) > 0 && (
              <span className="text-xs font-normal text-amber-600 ml-1">({fleet?.stale} stale)</span>
            )}
          </p>
        </div>
      </div>

      {/* Strategy Family Summary */}
      {Object.keys(strategyGroups).length > 1 && (
        <div className="card mb-6">
          <p className="section-label">By Strategy</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {Object.entries(strategyGroups).map(([sid, g]) => (
              <div key={sid} className="border border-gray-200 rounded-lg p-3">
                <p className="text-sm font-medium truncate">{sid}</p>
                <div className="flex items-baseline gap-3 mt-1">
                  <span className="text-xs text-foreground-muted">{g.count} bots</span>
                  <span className="text-xs text-foreground-muted">{g.running} active</span>
                  <span className={`text-xs font-medium ${g.pnl >= 0 ? "text-primary" : "text-danger"}`}>
                    {g.pnl >= 0 ? "+" : ""}{sym}{g.pnl.toFixed(2)}
                  </span>
                </div>
                <p className="text-xs text-foreground-muted mt-0.5">{g.trades} trades</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Workflow explainer (show when no bots exist) */}
      {botList.length === 0 && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-xs text-blue-800 mb-6">
          <strong>How Paper Bots work:</strong> A paper bot monitors a strategy/instrument/timeframe combo in real time using simulated capital.
          No real money is at risk. The recommended path: <strong>Research</strong> (find promising combos) → <strong>Save to Shortlist</strong> → <strong>Promote to Paper Bot</strong> → observe performance before going live.
          You can also add bots manually below.
        </div>
      )}

      {/* Add Bot Form */}
      <form onSubmit={handleCreate} className="card-elevated mb-6">
        <p className="section-label">Add Bot<InfoTip text="Creates a paper bot that monitors this strategy/instrument/timeframe combo. The bot starts in 'monitoring' state and evaluates signals on each new candle close." /></p>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Strategy</label>
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className="input">
              <option value="">Select strategy</option>
              {strategies?.map((s: any) => (
                <option key={s.id} value={s.id}>{s.name || s.id}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="flex items-center gap-2 text-xs text-foreground-muted mb-1.5">
              Instrument
              <WatchlistPicker />
            </label>
            <GroupedInstrumentSelect instruments={instruments ?? []} value={instrument} onChange={setInstrument} className="input" watchlistFilter={filterSet} />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Timeframe</label>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} className="input">
              <option value="M1">M1</option>
              <option value="M5">M5</option>
              <option value="M15">M15</option>
              <option value="M30">M30</option>
              <option value="H1">H1</option>
              <option value="H4">H4</option>
            </select>
          </div>
          <button type="submit" disabled={creating || !strategy || !instrument} className="btn btn-primary">
            {creating && <Loader2 size={14} className="animate-spin" />}
            {creating ? "Creating..." : "Add Bot"}
          </button>
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowShortlistPicker(!showShortlistPicker)}
              className="btn btn-secondary text-xs"
              disabled={shortlist.length === 0}
              title={shortlist.length === 0 ? "No shortlisted combos yet" : "Load combo from Shortlist"}
            >
              <Star size={12} />
              From Shortlist
              <ChevronDown size={12} />
            </button>
            {showShortlistPicker && shortlist.length > 0 && (
              <div className="absolute top-full left-0 mt-1 bg-white border border-border rounded-lg shadow-lg z-20 w-72 max-h-60 overflow-y-auto">
                {shortlist.filter((e) => e.status === "active").map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() => loadFromShortlist(e)}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-background-muted transition-colors flex items-center gap-2"
                  >
                    <span className="font-medium">{e.strategy_id}</span>
                    <span className="text-foreground-muted">{e.instrument}</span>
                    <span className="text-foreground-muted">{e.timeframe}</span>
                    <span className="ml-auto text-xs text-foreground-muted">{e.score.toFixed(2)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        {error && <p className="text-danger text-sm mt-3">{error}</p>}
      </form>

      {actionError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 mb-4 flex items-center justify-between">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="text-red-500 hover:text-red-700 transition text-xs ml-4">Dismiss</button>
        </div>
      )}

      {/* View controls */}
      <div className="flex items-center gap-3 mb-3">
        <button
          onClick={() => setGroupBy(groupBy === "none" ? "strategy" : "none")}
          className={`text-xs px-3 py-1 rounded border ${groupBy === "strategy" ? "bg-primary/10 border-primary text-primary" : "border-gray-200"}`}
        >
          {groupBy === "strategy" ? "Grouped by Strategy" : "Group by Strategy"}
        </button>
      </div>

      {/* Bot List */}
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
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {botList.length === 0 && (
              <tr>
                <td colSpan={7}>
                  <EmptyState
                    icon={<Bot size={36} strokeWidth={1.5} />}
                    title="No paper bots yet"
                    description="Paper bots simulate live trading with no real money. Add one manually above, or promote a top-scoring combo from your Research shortlist."
                  />
                  <div className="flex justify-center gap-3 pb-4">
                    <Link href="/research" className="btn btn-secondary text-sm">
                      <Search size={14} />
                      Go to Research
                    </Link>
                  </div>
                </td>
              </tr>
            )}
            {groupBy === "none" ? (
              botList.map((bot) => {
                const fleetBot = fleetBots.find((fb) => fb.bot_id === bot.id);
                return (
                  <tr key={bot.id} className={fleetBot?.is_stale ? "bg-amber-50/50" : ""}>
                    <td>
                      <span className="font-medium">{bot.strategy_id}</span>
                      <span className="block text-[10px] text-foreground-muted truncate max-w-[160px]">{strategyShortName(bot.strategy_id)}</span>
                    </td>
                    <td>{bot.instrument}</td>
                    <td className="text-foreground-muted">{bot.timeframe}</td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <StatusBadge variant={STATE_VARIANT[bot.state] ?? "neutral"}>
                          {bot.state}
                        </StatusBadge>
                        {fleetBot?.is_stale && (
                          <AlertTriangle size={12} className="text-amber-500" />
                        )}
                      </div>
                    </td>
                    <td className="text-right tabular-nums">{fleetBot?.total_trades ?? 0}</td>
                    <td className={`text-right tabular-nums font-medium ${(fleetBot?.total_pnl ?? 0) >= 0 ? "text-primary" : "text-danger"}`}>
                      {(fleetBot?.total_pnl ?? 0) >= 0 ? "+" : ""}{sym}{(fleetBot?.total_pnl ?? 0).toFixed(2)}
                    </td>
                    <td className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        {bot.state === "monitoring" && (
                          <button
                            onClick={() => handlePause(bot.id)}
                            disabled={!!actingBotId}
                            className="btn-ghost text-xs px-2 py-1 rounded disabled:opacity-40"
                          >
                            {actingBotId === bot.id ? <Loader2 size={12} className="animate-spin inline" /> : "Pause"}
                          </button>
                        )}
                        {bot.state !== "stopped" && (
                          <button
                            onClick={() => handleStop(bot.id)}
                            disabled={!!actingBotId}
                            className="text-xs px-2 py-1 rounded text-danger hover:bg-red-50 transition-colors disabled:opacity-40"
                          >
                            {actingBotId === bot.id ? <Loader2 size={12} className="animate-spin inline" /> : "Stop"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })
            ) : (
              Object.entries(groupedBots).map(([sid, bots]) => (
                <Fragment key={sid}>
                  <tr className="bg-background-muted">
                    <td colSpan={7} className="text-sm font-semibold py-2">
                      {sid}
                      <span className="text-foreground-muted font-normal ml-2">
                        ({bots.length} bots &middot; {strategyGroups[sid]?.running ?? 0} active &middot;
                        <span className={`ml-1 ${(strategyGroups[sid]?.pnl ?? 0) >= 0 ? "text-primary" : "text-danger"}`}>
                          {(strategyGroups[sid]?.pnl ?? 0) >= 0 ? "+" : ""}{sym}{(strategyGroups[sid]?.pnl ?? 0).toFixed(2)}
                        </span>
                        )
                      </span>
                    </td>
                  </tr>
                  {bots.map((b) => (
                    <tr key={b.bot_id} className={b.is_stale ? "bg-amber-50/50" : ""}>
                      <td className="pl-6" title={strategyShortName(b.strategy_id)}>
                        <span className="font-medium">{b.strategy_id}</span>
                      </td>
                      <td>{b.instrument}</td>
                      <td className="text-foreground-muted">{b.timeframe}</td>
                      <td>
                        <div className="flex items-center gap-1.5">
                          <StatusBadge variant={STATE_VARIANT[b.state] ?? "neutral"}>
                            {b.state}
                          </StatusBadge>
                          {b.is_stale && <AlertTriangle size={12} className="text-amber-500" />}
                        </div>
                      </td>
                      <td className="text-right tabular-nums">{b.total_trades}</td>
                      <td className={`text-right tabular-nums font-medium ${b.total_pnl >= 0 ? "text-primary" : "text-danger"}`}>
                        {b.total_pnl >= 0 ? "+" : ""}{sym}{b.total_pnl.toFixed(2)}
                      </td>
                      <td className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          {b.state === "monitoring" && (
                            <button
                              onClick={() => handlePause(b.bot_id)}
                              disabled={!!actingBotId}
                              className="btn-ghost text-xs px-2 py-1 rounded disabled:opacity-40"
                            >
                              {actingBotId === b.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Pause"}
                            </button>
                          )}
                          {b.state !== "stopped" && (
                            <button
                              onClick={() => handleStop(b.bot_id)}
                              disabled={!!actingBotId}
                              className="text-xs px-2 py-1 rounded text-danger hover:bg-red-50 transition-colors disabled:opacity-40"
                            >
                              {actingBotId === b.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Stop"}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
