"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useBots, useAccount } from "@/lib/hooks/use-bots";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { Bot, Loader2, Wallet, TrendingUp, CalendarDays } from "lucide-react";

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
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { data: instruments } = useSWR("instruments", () => api.instruments());
  const [strategy, setStrategy] = useState("");
  const [instrument, setInstrument] = useState("");
  const [timeframe, setTimeframe] = useState("H1");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    try {
      await api.pauseBot(id);
      await mutateBots();
    } catch {
      /* ignore */
    }
  }

  async function handleStop(id: string) {
    try {
      await api.stopBot(id);
      await mutateBots();
    } catch {
      /* ignore */
    }
  }

  const balance = account?.balance ?? 0;
  const equity = account?.equity ?? 0;
  const dailyPnl = account?.daily_pnl ?? 0;
  const botList = (bots ?? []) as BotItem[];

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Paper Bots"
        subtitle="Manage paper trading bots and monitor account performance"
      />

      {/* Account Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Balance</span>
            <Wallet size={16} className="text-foreground-muted" />
          </div>
          <p className="text-2xl font-bold tracking-tight">${balance.toFixed(2)}</p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Equity</span>
            <TrendingUp size={16} className="text-foreground-muted" />
          </div>
          <p className="text-2xl font-bold tracking-tight">${equity.toFixed(2)}</p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Daily PnL</span>
            <CalendarDays size={16} className="text-foreground-muted" />
          </div>
          <p className={`text-2xl font-bold tracking-tight ${dailyPnl >= 0 ? "text-primary" : "text-danger"}`}>
            {dailyPnl >= 0 ? "+" : ""}${dailyPnl.toFixed(2)}
          </p>
        </div>
      </div>

      {/* Add Bot Form */}
      <form onSubmit={handleCreate} className="card-elevated mb-6">
        <p className="section-label">Add Bot</p>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Strategy</label>
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className="input">
              <option value="">Select strategy</option>
              {strategies?.map((s: any) => (
                <option key={s.strategy_id} value={s.strategy_id}>{s.strategy_name || s.strategy_id}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Instrument</label>
            <GroupedInstrumentSelect instruments={instruments ?? []} value={instrument} onChange={setInstrument} className="input" />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Timeframe</label>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} className="input">
              <option value="M15">M15</option>
              <option value="H1">H1</option>
              <option value="H4">H4</option>
              <option value="D">D</option>
            </select>
          </div>
          <button type="submit" disabled={creating || !strategy || !instrument} className="btn btn-primary">
            {creating && <Loader2 size={14} className="animate-spin" />}
            {creating ? "Creating..." : "Add Bot"}
          </button>
        </div>
        {error && <p className="text-danger text-sm mt-3">{error}</p>}
      </form>

      {/* Bot List */}
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th className="text-left">Strategy</th>
              <th className="text-left">Instrument</th>
              <th className="text-left">TF</th>
              <th className="text-left">State</th>
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {botList.length === 0 && (
              <tr>
                <td colSpan={5}>
                  <EmptyState
                    icon={<Bot size={36} strokeWidth={1.5} />}
                    title="No bots running"
                    description="Add a paper bot above to start monitoring."
                  />
                </td>
              </tr>
            )}
            {botList.map((bot) => (
              <tr key={bot.id}>
                <td className="font-medium">{bot.strategy_id}</td>
                <td>{bot.instrument}</td>
                <td className="text-foreground-muted">{bot.timeframe}</td>
                <td>
                  <StatusBadge variant={STATE_VARIANT[bot.state] ?? "neutral"}>
                    {bot.state}
                  </StatusBadge>
                </td>
                <td className="text-right">
                  <div className="flex items-center justify-end gap-2">
                    {bot.state === "monitoring" && (
                      <button onClick={() => handlePause(bot.id)} className="btn-ghost text-xs px-2 py-1 rounded">
                        Pause
                      </button>
                    )}
                    {bot.state !== "stopped" && (
                      <button onClick={() => handleStop(bot.id)} className="text-xs px-2 py-1 rounded text-danger hover:bg-red-50 transition-colors">
                        Stop
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
