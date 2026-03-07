"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useBots, useAccount } from "@/lib/hooks/use-bots";

interface Bot {
  id: string;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  state: string;
}

const STATE_BADGE: Record<string, string> = {
  monitoring: "bg-green-100 text-green-800",
  paused: "bg-yellow-100 text-yellow-800",
  stopped: "bg-gray-100 text-gray-600",
};

export default function BotsPage() {
  const { data: bots, mutate: mutateBots } = useBots();
  const { data: account } = useAccount();
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
  const botList = (bots ?? []) as Bot[];

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">Paper Bots</h2>

      {/* Account Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <p className="text-sm text-foreground-muted mb-1">Balance</p>
          <p className="text-2xl font-semibold">${balance.toFixed(2)}</p>
        </div>
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <p className="text-sm text-foreground-muted mb-1">Equity</p>
          <p className="text-2xl font-semibold">${equity.toFixed(2)}</p>
        </div>
        <div className="bg-background-card rounded-lg border border-gray-200 p-5">
          <p className="text-sm text-foreground-muted mb-1">Daily PnL</p>
          <p className={`text-2xl font-semibold ${dailyPnl >= 0 ? "text-primary" : "text-danger"}`}>
            {dailyPnl >= 0 ? "+" : ""}${dailyPnl.toFixed(2)}
          </p>
        </div>
      </div>

      {/* Add Bot Form */}
      <form onSubmit={handleCreate} className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">Add Bot</h3>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Strategy</label>
            <input
              type="text"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              placeholder="e.g. ichimoku_tk_cross"
              className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-background"
            />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Instrument</label>
            <input
              type="text"
              value={instrument}
              onChange={(e) => setInstrument(e.target.value)}
              placeholder="e.g. EUR_USD"
              className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-background"
            />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Timeframe</label>
            <select
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-background"
            >
              <option value="M15">M15</option>
              <option value="H1">H1</option>
              <option value="H4">H4</option>
              <option value="D">D</option>
            </select>
          </div>
          <button
            type="submit"
            disabled={creating || !strategy || !instrument}
            className="bg-primary text-white px-4 py-1.5 rounded text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
          >
            {creating ? "Creating..." : "Add Bot"}
          </button>
        </div>
        {error && <p className="text-danger text-sm mt-2">{error}</p>}
      </form>

      {/* Bot List */}
      <div className="bg-background-card rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-background-muted">
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Strategy</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Instrument</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">TF</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">State</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Actions</th>
            </tr>
          </thead>
          <tbody>
            {botList.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-foreground-muted">
                  No bots yet. Add one above.
                </td>
              </tr>
            )}
            {botList.map((bot) => (
              <tr key={bot.id} className="border-b border-gray-100 hover:bg-background-muted/50">
                <td className="px-4 py-3">{bot.strategy_id}</td>
                <td className="px-4 py-3">{bot.instrument}</td>
                <td className="px-4 py-3">{bot.timeframe}</td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      STATE_BADGE[bot.state] ?? "bg-gray-100 text-gray-600"
                    }`}
                  >
                    {bot.state}
                  </span>
                </td>
                <td className="px-4 py-3 text-right space-x-2">
                  {bot.state === "monitoring" && (
                    <button
                      onClick={() => handlePause(bot.id)}
                      className="text-yellow-600 hover:text-yellow-800 text-xs font-medium"
                    >
                      Pause
                    </button>
                  )}
                  {bot.state !== "stopped" && (
                    <button
                      onClick={() => handleStop(bot.id)}
                      className="text-danger hover:text-red-800 text-xs font-medium"
                    >
                      Stop
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
