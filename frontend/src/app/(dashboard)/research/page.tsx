"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useRankings } from "@/lib/hooks/use-research";
import { Heatmap } from "@/components/analytics/Heatmap";

export default function ResearchPage() {
  const { data: rankings, mutate, isLoading } = useRankings();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRunResearch() {
    setRunning(true);
    setError(null);
    try {
      await api.runResearch({});
      await mutate();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run research");
    } finally {
      setRunning(false);
    }
  }

  // Build heatmap data from rankings
  const strategies = [...new Set(rankings?.map((r) => r.strategy_id) ?? [])];
  const instruments = [...new Set(rankings?.map((r) => r.instrument) ?? [])];

  const z: number[][] = strategies.map((strat) =>
    instruments.map((inst) => {
      const match = rankings?.find((r) => r.strategy_id === strat && r.instrument === inst);
      return match?.composite_score ?? 0;
    })
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold">Research Matrix</h2>
        <button
          onClick={handleRunResearch}
          disabled={running}
          className="bg-primary text-white px-4 py-1.5 rounded text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
        >
          {running ? "Running..." : "Run Research"}
        </button>
      </div>
      {error && <p className="text-danger text-sm mb-4">{error}</p>}

      {/* Heatmap */}
      {strategies.length > 0 && instruments.length > 0 && (
        <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
          <Heatmap
            z={z}
            x={instruments}
            y={strategies}
            title="Composite Score by Strategy x Instrument"
          />
        </div>
      )}

      {/* Rankings Table */}
      <div className="bg-background-card rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-background-muted">
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Rank</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Strategy</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Instrument</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Timeframe</th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Score</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-foreground-muted">Loading...</td>
              </tr>
            )}
            {!isLoading && (!rankings || rankings.length === 0) && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-foreground-muted">
                  No research results yet. Click &quot;Run Research&quot; to start.
                </td>
              </tr>
            )}
            {rankings?.map((r) => (
              <tr key={r.id} className="border-b border-gray-100 hover:bg-background-muted/50">
                <td className="px-4 py-3 font-medium">{r.rank}</td>
                <td className="px-4 py-3">{r.strategy_id}</td>
                <td className="px-4 py-3">{r.instrument}</td>
                <td className="px-4 py-3">{r.timeframe}</td>
                <td className="px-4 py-3 text-right font-medium">{r.composite_score.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
