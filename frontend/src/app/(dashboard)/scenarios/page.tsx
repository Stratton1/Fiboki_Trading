"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { formatPnl, formatCurrency, currencySymbol } from "@/lib/format-currency";
import { PageHeader } from "@/components/PageHeader";
import { Loader2, Play, AlertTriangle, TrendingUp, TrendingDown } from "lucide-react";

const TIMEFRAMES = ["M15", "H1", "H4", "D1"];

interface BotResult {
  strategy_id: string;
  instrument: string;
  timeframe: string;
  total_trades?: number;
  net_profit?: number;
  sharpe_ratio?: number | null;
  max_drawdown_pct?: number | null;
  equity_curve?: number[];
  error?: string;
}

interface ScenarioResult {
  combos: Array<{ strategy_id: string; instrument: string; timeframe: string }>;
  per_bot: BotResult[];
  aggregate_equity: number[];
  total_trades: number;
  aggregate_pnl: number;
  aggregate_max_dd: number | null;
}

export default function ScenariosPage() {
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { data: instrumentsData } = useSWR("instruments", () => api.instruments());
  const instruments = instrumentsData as Array<{ epic: string; name: string }> | undefined;

  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [selectedInstruments, setSelectedInstruments] = useState<string[]>([]);
  const [selectedTimeframes, setSelectedTimeframes] = useState<string[]>(["H1"]);
  const [capital, setCapital] = useState(10000);

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScenarioResult | null>(null);

  function toggleItem(list: string[], setList: (v: string[]) => void, item: string) {
    setList(list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);
  }

  const comboCount = selectedStrategies.length * selectedInstruments.length * selectedTimeframes.length;

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (comboCount === 0) return;

    setRunning(true);
    setProgress(0);
    setError(null);
    setResult(null);

    try {
      const combos = selectedStrategies.flatMap((s) =>
        selectedInstruments.flatMap((i) =>
          selectedTimeframes.map((tf) => ({
            strategy_id: s,
            instrument: i,
            timeframe: tf,
          }))
        )
      );

      const res = await api.runScenario({ combos, capital });
      const jobId = res.job_id;

      const poll = setInterval(async () => {
        try {
          const job = await api.getJob(jobId);
          setProgress(job.progress ?? 0);

          if (job.state === "completed" && job.result) {
            clearInterval(poll);
            setResult(job.result as unknown as ScenarioResult);
            setRunning(false);
          } else if (job.state === "failed") {
            clearInterval(poll);
            setError(job.error || "Scenario job failed");
            setRunning(false);
          } else if (job.state === "cancelled") {
            clearInterval(poll);
            setError("Scenario job was cancelled");
            setRunning(false);
          }
        } catch {
          // poll error — keep trying
        }
      }, 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run scenario");
      setRunning(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Scenario Sandbox"
        subtitle="Simulate portfolio combinations across strategies, instruments, and timeframes"
      />

      <form onSubmit={handleRun} className="space-y-4 mb-6">
        {/* Strategies */}
        <div className="bg-background-card rounded-lg border border-gray-200 p-4">
          <div className="flex items-center gap-2 mb-2">
            <label className="text-sm font-medium">Strategies</label>
            <button
              type="button"
              onClick={() =>
                setSelectedStrategies(
                  strategies?.map((s: Record<string, unknown>) => s.id as string) ?? []
                )
              }
              className="text-xs text-primary hover:underline"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={() => setSelectedStrategies([])}
              className="text-xs text-foreground-muted hover:underline"
            >
              Clear
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {strategies?.map((s: Record<string, unknown>) => (
              <button
                key={s.id as string}
                type="button"
                onClick={() => toggleItem(selectedStrategies, setSelectedStrategies, s.id as string)}
                className={`px-2 py-1 rounded text-xs border ${
                  selectedStrategies.includes(s.id as string)
                    ? "bg-primary text-white border-primary"
                    : "bg-background border-gray-300 text-foreground"
                }`}
              >
                {(s.name as string) || (s.id as string)}
              </button>
            ))}
          </div>
        </div>

        {/* Instruments */}
        <div className="bg-background-card rounded-lg border border-gray-200 p-4">
          <div className="flex items-center gap-2 mb-2">
            <label className="text-sm font-medium">Instruments</label>
            <button
              type="button"
              onClick={() =>
                setSelectedInstruments(instruments?.map((i) => i.epic) ?? [])
              }
              className="text-xs text-primary hover:underline"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={() => setSelectedInstruments([])}
              className="text-xs text-foreground-muted hover:underline"
            >
              Clear
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {instruments?.map((i) => (
              <button
                key={i.epic}
                type="button"
                onClick={() => toggleItem(selectedInstruments, setSelectedInstruments, i.epic)}
                className={`px-2 py-1 rounded text-xs border ${
                  selectedInstruments.includes(i.epic)
                    ? "bg-primary text-white border-primary"
                    : "bg-background border-gray-300 text-foreground"
                }`}
              >
                {i.epic}
              </button>
            ))}
          </div>
        </div>

        {/* Timeframes */}
        <div className="bg-background-card rounded-lg border border-gray-200 p-4">
          <label className="text-sm font-medium mb-2 block">Timeframes</label>
          <div className="flex flex-wrap gap-2">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                type="button"
                onClick={() => toggleItem(selectedTimeframes, setSelectedTimeframes, tf)}
                className={`px-2 py-1 rounded text-xs border ${
                  selectedTimeframes.includes(tf)
                    ? "bg-primary text-white border-primary"
                    : "bg-background border-gray-300 text-foreground"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>

        {/* Capital + Run */}
        <div className="flex items-end gap-4">
          <div>
            <label className="text-xs text-foreground-muted block mb-1">Starting Capital ({currencySymbol()})</label>
            <input
              type="number"
              min={100}
              step={100}
              value={capital}
              onChange={(e) => setCapital(parseInt(e.target.value, 10) || 10000)}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm w-32 bg-background"
            />
          </div>
          <button
            type="submit"
            disabled={running || comboCount === 0}
            className="flex items-center gap-2 bg-primary text-white px-4 py-1.5 rounded text-sm font-medium disabled:opacity-50"
          >
            {running ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                Running ({progress}%)
              </>
            ) : (
              <>
                <Play size={14} />
                Run {comboCount} combo{comboCount !== 1 ? "s" : ""}
              </>
            )}
          </button>
        </div>

        {running && (
          <div className="w-full h-1.5 bg-background-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
      </form>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-danger text-sm mb-4 bg-red-50 border border-red-200 rounded-lg px-4 py-2">
          <AlertTriangle size={14} />
          {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-4">
          {/* Aggregate summary */}
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <h3 className="text-sm font-medium mb-3">Portfolio Summary</h3>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-foreground-muted">Total Trades</p>
                <p className="text-lg font-semibold">{result.total_trades}</p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Aggregate PnL</p>
                <p
                  className={`text-lg font-semibold flex items-center gap-1 ${
                    result.aggregate_pnl >= 0 ? "text-primary" : "text-danger"
                  }`}
                >
                  {result.aggregate_pnl >= 0 ? (
                    <TrendingUp size={16} />
                  ) : (
                    <TrendingDown size={16} />
                  )}
                  {formatPnl(result.aggregate_pnl)}
                </p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Max Drawdown</p>
                <p className="text-lg font-semibold text-danger">
                  {result.aggregate_max_dd != null
                    ? `${result.aggregate_max_dd.toFixed(1)}%`
                    : "—"}
                </p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Bots</p>
                <p className="text-lg font-semibold">{result.per_bot.length}</p>
              </div>
            </div>
          </div>

          {/* Equity curve (simple text-based for now — lightweight) */}
          {result.aggregate_equity.length > 0 && (
            <div className="bg-background-card rounded-lg border border-gray-200 p-4">
              <h3 className="text-sm font-medium mb-2">Aggregate Equity Curve</h3>
              <div className="h-24 flex items-end gap-px">
                {(() => {
                  const eq = result.aggregate_equity;
                  const min = Math.min(...eq);
                  const max = Math.max(...eq);
                  const range = max - min || 1;
                  // Sample to max 200 bars for display
                  const step = Math.max(1, Math.floor(eq.length / 200));
                  const sampled = eq.filter((_, i) => i % step === 0);
                  return sampled.map((val, i) => (
                    <div
                      key={i}
                      className={`flex-1 min-w-[1px] rounded-t ${
                        val >= eq[0] ? "bg-primary/60" : "bg-danger/60"
                      }`}
                      style={{
                        height: `${Math.max(2, ((val - min) / range) * 100)}%`,
                      }}
                      title={formatCurrency(val, "GBP", 0)}
                    />
                  ));
                })()}
              </div>
              <div className="flex justify-between text-[10px] text-foreground-muted mt-1">
                <span>{formatCurrency(result.aggregate_equity[0] ?? 0, "GBP", 0)}</span>
                <span>
                  {formatCurrency(result.aggregate_equity[result.aggregate_equity.length - 1] ?? 0, "GBP", 0)}
                </span>
              </div>
            </div>
          )}

          {/* Per-bot breakdown */}
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <h3 className="text-sm font-medium mb-3">Per-Bot Breakdown</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-foreground-muted border-b border-border">
                    <th className="pb-2 pr-3">Strategy</th>
                    <th className="pb-2 pr-3">Instrument</th>
                    <th className="pb-2 pr-3">TF</th>
                    <th className="pb-2 pr-3 text-right">Trades</th>
                    <th className="pb-2 pr-3 text-right">Net Profit</th>
                    <th className="pb-2 pr-3 text-right">Sharpe</th>
                    <th className="pb-2 text-right">Max DD</th>
                  </tr>
                </thead>
                <tbody>
                  {result.per_bot.map((bot, i) => (
                    <tr key={i} className="border-b border-border/50 last:border-0">
                      {bot.error ? (
                        <>
                          <td className="py-1.5 pr-3 font-mono text-xs">{bot.strategy_id}</td>
                          <td className="py-1.5 pr-3">{bot.instrument}</td>
                          <td className="py-1.5 pr-3">{bot.timeframe}</td>
                          <td colSpan={4} className="py-1.5 text-danger text-xs">
                            {bot.error}
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="py-1.5 pr-3 font-mono text-xs">{bot.strategy_id}</td>
                          <td className="py-1.5 pr-3">{bot.instrument}</td>
                          <td className="py-1.5 pr-3">{bot.timeframe}</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">
                            {bot.total_trades ?? 0}
                          </td>
                          <td
                            className={`py-1.5 pr-3 text-right tabular-nums ${
                              (bot.net_profit ?? 0) >= 0 ? "text-primary" : "text-danger"
                            }`}
                          >
                            {formatPnl(bot.net_profit ?? 0)}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">
                            {bot.sharpe_ratio != null ? bot.sharpe_ratio.toFixed(2) : "—"}
                          </td>
                          <td className="py-1.5 text-right tabular-nums text-danger">
                            {bot.max_drawdown_pct != null
                              ? `${bot.max_drawdown_pct.toFixed(1)}%`
                              : "—"}
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
