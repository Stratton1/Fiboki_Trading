"use client";

import { useState } from "react";
import useSWR from "swr";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { formatCurrency } from "@/lib/format-currency";
import { useRankings, useResearchRuns } from "@/lib/hooks/use-research";
import { useShortlist } from "@/lib/hooks/use-shortlist";
import { Heatmap } from "@/components/analytics/Heatmap";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";
import WatchlistPicker from "@/components/WatchlistPicker";
import { useWatchlists } from "@/lib/hooks/use-watchlists";
import { useManifest } from "@/lib/hooks/use-manifest";
import { PageHeader } from "@/components/PageHeader";
import { InfoTip } from "@/components/InfoTip";
import { useBookmarks } from "@/lib/hooks/use-bookmarks";
import { BookmarkButton } from "@/components/BookmarkButton";
import { strategyShortName } from "@/lib/strategy-names";
import type {
  AdvancedResearchResponse,
  ResearchPreset,
  ScoringWeights,
  ValidationBatchResponse,
} from "@/types/contracts/research";

const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"];

const DEFAULT_WEIGHTS: ScoringWeights = {
  weight_risk_adjusted: 0.25,
  weight_profit_factor: 0.2,
  weight_return: 0.2,
  weight_drawdown: 0.15,
  weight_sample: 0.1,
  weight_stability: 0.1,
};

export default function ResearchPage() {
  // Run state — declared first because useRankings depends on currentRunId
  const [running, setRunning] = useState(false);
  const [runProgress, setRunProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  // Run scope: "current" = latest run, "all" = all runs (deduped best-per-combo), or a specific run_id
  const [runScope, setRunScope] = useState<"current" | "all" | string>("current");
  const [lastSummary, setLastSummary] = useState<{
    run_id: string;
    completed: number;
    qualified: number;
    total_combinations: number;
    min_trades: number;
  } | null>(null);
  const [confirmAction, setConfirmAction] = useState<{
    label: string;
    description: string;
    onConfirm: () => Promise<void>;
  } | null>(null);

  // Derive the effective run_id for the rankings query
  const effectiveRunId = runScope === "current" ? currentRunId : runScope === "all" ? null : runScope;
  const isAllRunsMode = runScope === "all";
  const { data: rankings, mutate, isLoading } = useRankings(effectiveRunId, isAllRunsMode);
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { data: instruments } = useSWR("instruments", () => api.instruments());
  const { filterSet } = useWatchlists();

  const { hasData, availableTimeframes: manifestTimeframes } = useManifest();
  const { isBookmarked, toggle: toggleBookmark } = useBookmarks("research_result");
  const [showBookmarked, setShowBookmarked] = useState(false);
  const { shortlist, save: saveToShortlist, update: updateShortlistEntry, remove: removeFromShortlist, isShortlisted } = useShortlist();
  const { runs: researchRuns, mutate: mutateRuns } = useResearchRuns();
  const [shortlistNote, setShortlistNote] = useState<Record<number, string>>({});

  // Presets
  const { data: presets, mutate: mutatePresets } = useSWR("research-presets", () => api.listPresets());
  const [presetName, setPresetName] = useState("");
  const [savingPreset, setSavingPreset] = useState(false);

  // Batch selection state
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [selectedInstrument, setSelectedInstrument] = useState("");
  const [selectedTimeframes, setSelectedTimeframes] = useState<string[]>(["H1"]);
  const [minTrades, setMinTrades] = useState(80);
  const [showWeights, setShowWeights] = useState(false);
  const [weights, setWeights] = useState<ScoringWeights>(DEFAULT_WEIGHTS);

  // Advanced research state
  const [advancedResult, setAdvancedResult] = useState<AdvancedResearchResponse | null>(null);
  const [advancedLoading, setAdvancedLoading] = useState(false);

  // Validation state
  const [validationResult, setValidationResult] = useState<ValidationBatchResponse | null>(null);
  const [validatingLoading, setValidatingLoading] = useState(false);

  // Promotion state
  const [promoteTarget, setPromoteTarget] = useState<{
    strategy_id: string;
    instrument: string;
    timeframe: string;
    composite_score: number;
  } | null>(null);
  const [promoteLoading, setPromoteLoading] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);
  const [promoteSuccess, setPromoteSuccess] = useState<string | null>(null);

  // Auto Scout state
  const [scoutLoading, setScoutLoading] = useState(false);
  const [scoutResult, setScoutResult] = useState<string | null>(null);

  // Bulk bot creation
  const [selectedResults, setSelectedResults] = useState<Set<number>>(new Set());
  const [bulkDeploying, setBulkDeploying] = useState(false);
  const [bulkResult, setBulkResult] = useState<string | null>(null);

  function toggleStrategy(id: string) {
    setSelectedStrategies((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    );
  }

  function selectAllStrategies() {
    if (!strategies) return;
    setSelectedStrategies(strategies.map((s: any) => s.id));
  }

  function toggleTimeframe(tf: string) {
    setSelectedTimeframes((prev) =>
      prev.includes(tf) ? prev.filter((t) => t !== tf) : [...prev, tf]
    );
  }

  function updateWeight(key: keyof ScoringWeights, value: number) {
    setWeights((prev) => ({ ...prev, [key]: value }));
  }

  function loadPreset(preset: ResearchPreset) {
    const c = preset.config as Record<string, unknown>;
    if (Array.isArray(c.strategy_ids)) setSelectedStrategies(c.strategy_ids as string[]);
    if (Array.isArray(c.instruments) && (c.instruments as string[]).length > 0) setSelectedInstrument((c.instruments as string[])[0]);
    else if (typeof c.instrument === "string") setSelectedInstrument(c.instrument);
    if (Array.isArray(c.timeframes)) setSelectedTimeframes(c.timeframes as string[]);
    if (typeof c.min_trades === "number") setMinTrades(c.min_trades);
    if (c.scoring_weights) {
      setWeights(c.scoring_weights as ScoringWeights);
      setShowWeights(true);
    }
  }

  async function handleSavePreset() {
    if (!presetName.trim()) return;
    setSavingPreset(true);
    try {
      await api.createPreset({
        name: presetName.trim(),
        config: {
          strategy_ids: selectedStrategies,
          instruments: [selectedInstrument],
          timeframes: selectedTimeframes,
          min_trades: minTrades,
          ...(showWeights ? { scoring_weights: weights } : {}),
        },
      });
      setPresetName("");
      await mutatePresets();
    } finally {
      setSavingPreset(false);
    }
  }

  async function handleDeletePreset(id: number) {
    await api.deletePreset(id);
    await mutatePresets();
  }

  async function handleRunResearch(e: React.FormEvent) {
    e.preventDefault();
    if (selectedStrategies.length === 0 || !selectedInstrument || selectedTimeframes.length === 0) return;
    setRunning(true);
    setRunProgress(0);
    setError(null);
    setLastSummary(null);
    setCurrentRunId(null);
    setRunScope("current");
    try {
      const body: Record<string, unknown> = {
        strategy_ids: selectedStrategies,
        instruments: [selectedInstrument],
        timeframes: selectedTimeframes,
        min_trades: minTrades,
      };
      if (showWeights) {
        body.scoring_weights = weights;
      }
      const result = await api.runResearch(body);
      const jobId = result.job_id;

      // Poll for completion — track progress
      const pollInterval = setInterval(async () => {
        try {
          const job = await api.getJob(jobId);
          setRunProgress(job.progress ?? 0);

          if (job.state === "completed" && job.result) {
            clearInterval(pollInterval);
            const r = job.result as Record<string, unknown>;
            const runId = r.run_id as string;
            setCurrentRunId(runId);
            setLastSummary({
              run_id: runId,
              completed: (r.completed as number) ?? 0,
              qualified: (r.qualified as number) ?? 0,
              total_combinations: (r.total_combinations as number) ?? 0,
              min_trades: (r.min_trades as number) ?? minTrades,
            });
            await mutate();
            setRunning(false);
          } else if (job.state === "failed") {
            clearInterval(pollInterval);
            setError(job.error || "Research job failed");
            setRunning(false);
          } else if (job.state === "cancelled") {
            clearInterval(pollInterval);
            setError("Research job was cancelled");
            setRunning(false);
          }
        } catch {
          // Poll error — keep trying
        }
      }, 2000);
      return; // Don't setRunning(false) here — the poll handles it
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run research");
      setRunning(false);
    }
  }

  async function handleAdvancedResearch(strategyId: string, instrument: string, timeframe: string) {
    setAdvancedLoading(true);
    setAdvancedResult(null);
    setError(null);
    try {
      const result = await api.advancedResearch({
        strategy_id: strategyId,
        instrument,
        timeframe,
      });
      setAdvancedResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run advanced research");
    } finally {
      setAdvancedLoading(false);
    }
  }

  async function handleValidateTop() {
    if (!rankings || rankings.length === 0) return;
    setValidatingLoading(true);
    setValidationResult(null);
    setError(null);
    try {
      const shortlist = rankings.slice(0, 10).map((r) => ({
        strategy_id: r.strategy_id,
        instrument: r.instrument,
        timeframe: r.timeframe,
        original_score: r.composite_score,
      }));
      const result = await api.validateResearch({ shortlist });
      setValidationResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed");
    } finally {
      setValidatingLoading(false);
    }
  }

  async function handlePromote() {
    if (!promoteTarget) return;
    setPromoteLoading(true);
    setPromoteError(null);
    setPromoteSuccess(null);
    try {
      const res = await api.createBot({
        strategy_id: promoteTarget.strategy_id,
        instrument: promoteTarget.instrument,
        timeframe: promoteTarget.timeframe,
        source_type: "research",
        source_id: currentRunId || undefined,
      });
      const botId = (res as Record<string, unknown>).bot_id as string;
      setPromoteSuccess(`Bot ${botId} created for ${promoteTarget.strategy_id} / ${promoteTarget.instrument} / ${promoteTarget.timeframe}`);
      setPromoteTarget(null);
    } catch (err) {
      if (err instanceof Error) {
        if (err.message === "Failed to fetch") {
          setPromoteError(
            "Network error: could not reach API. Check connection and CORS configuration."
          );
        } else if (err.message.includes("AbortError") || err.message.includes("aborted")) {
          setPromoteError("Request timed out. The server may be slow — try again.");
        } else {
          setPromoteError(err.message);
        }
      } else {
        setPromoteError("Promotion failed (unknown error)");
      }
    } finally {
      setPromoteLoading(false);
    }
  }

  async function handleAutoScout() {
    setScoutLoading(true);
    setScoutResult(null);
    setError(null);
    try {
      const res = await api.autoScout({ timeframes: ["H1", "H4"], min_trades: 80 });
      setScoutResult(`Auto Scout started (job ${res.job_id}). Check Jobs page for progress.`);
      setCurrentRunId(null);
      setRunScope("all");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Auto Scout failed");
    } finally {
      setScoutLoading(false);
    }
  }

  async function handleBulkCreateBots() {
    if (selectedResults.size === 0 || !rankings) return;
    setBulkDeploying(true);
    setBulkResult(null);
    const selected = rankings.filter((r) => selectedResults.has(r.id));
    let created = 0;
    let failed = 0;
    for (const r of selected) {
      try {
        await api.createBot({
          strategy_id: r.strategy_id,
          instrument: r.instrument,
          timeframe: r.timeframe,
          source_type: "research",
          source_id: r.run_id,
        });
        created++;
      } catch {
        failed++;
      }
    }
    setBulkResult(`Created ${created} bot${created !== 1 ? "s" : ""}${failed > 0 ? `, ${failed} failed` : ""}`);
    setSelectedResults(new Set());
    setBulkDeploying(false);
  }

  function toggleResultSelection(id: number) {
    setSelectedResults((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllResults() {
    if (!rankings) return;
    setSelectedResults(new Set(rankings.map((r) => r.id)));
  }

  function deselectAllResults() {
    setSelectedResults(new Set());
  }

  const PROMOTION_THRESHOLD = 0.55;

  // Heatmap data
  const strategyIds = [...new Set(rankings?.map((r) => r.strategy_id) ?? [])];
  const instrumentIds = [...new Set(rankings?.map((r) => r.instrument) ?? [])];
  const z: number[][] = strategyIds.map((strat) =>
    instrumentIds.map((inst) => {
      const match = rankings?.find((r) => r.strategy_id === strat && r.instrument === inst);
      return match?.composite_score ?? 0;
    })
  );

  return (
    <div className="max-w-6xl space-y-6">
      <PageHeader
        title="Research Matrix"
        subtitle="Run batch research, rank strategies, and validate performance"
      />

      {/* Auto Scout — one-click full sweep */}
      <div className="card-elevated flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-sm font-medium">Auto Scout</p>
          <p className="text-xs text-foreground-muted">
            Sweep all 12 strategies × all instruments × H1+H4. Finds the best combos automatically.
          </p>
        </div>
        <button
          onClick={handleAutoScout}
          disabled={scoutLoading}
          className="btn btn-primary"
        >
          {scoutLoading ? <><Loader2 size={14} className="animate-spin" /> Launching...</> : "Run Full Sweep"}
        </button>
      </div>
      {scoutResult && (
        <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-2 text-xs text-green-800 flex items-center justify-between">
          <span>{scoutResult}</span>
          <button onClick={() => setScoutResult(null)} className="text-green-500 hover:text-green-700 text-xs ml-4">Dismiss</button>
        </div>
      )}

      {error && <p className="text-danger text-sm">{error}</p>}

      {/* Batch Run Form */}
      <form onSubmit={handleRunResearch} className="card">
        <h3 className="text-sm font-medium text-foreground-muted mb-3">Run Research</h3>

        {/* Strategy multi-select */}
        <div className="mb-3">
          <div className="flex items-center gap-2 mb-1">
            <label className="block text-xs text-foreground-muted">Strategies</label>
            <button type="button" onClick={selectAllStrategies} className="text-xs text-primary hover:underline">
              Select all
            </button>
            <button type="button" onClick={() => setSelectedStrategies([])} className="text-xs text-foreground-muted hover:underline">
              Clear
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {strategies?.map((s: any) => (
              <button
                key={s.id}
                type="button"
                onClick={() => toggleStrategy(s.id)}
                className={`px-2 py-1 rounded text-xs border ${
                  selectedStrategies.includes(s.id)
                    ? "bg-primary text-white border-primary"
                    : "bg-background border-gray-300 text-foreground"
                }`}
              >
                {s.name || s.id}
              </button>
            ))}
          </div>
        </div>

        {/* Instrument + Timeframe + Min Trades */}
        <div className="flex flex-wrap gap-3 items-end mb-3">
          <div>
            <label className="flex items-center gap-2 text-xs text-foreground-muted mb-1">
              Instrument
              <WatchlistPicker />
            </label>
            <GroupedInstrumentSelect
              instruments={instruments ?? []}
              value={selectedInstrument}
              onChange={setSelectedInstrument}
              className="input"
              showDataIndicator
              watchlistFilter={filterSet}
            />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Timeframes</label>
            <div className="flex gap-1">
              {TIMEFRAMES.map((tf) => {
                const instrumentTfs = selectedInstrument ? manifestTimeframes(selectedInstrument) : [];
                const tfAvailable = instrumentTfs.length === 0 || instrumentTfs.includes(tf);
                return (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => toggleTimeframe(tf)}
                    disabled={!tfAvailable}
                    className={`px-2 py-1 rounded text-xs border transition ${
                      selectedTimeframes.includes(tf)
                        ? "bg-primary text-white border-primary"
                        : tfAvailable
                          ? "bg-background border-gray-300"
                          : "bg-background border-gray-200 text-foreground-muted/50 cursor-not-allowed"
                    }`}
                    title={!tfAvailable ? `No data for ${selectedInstrument}/${tf}` : undefined}
                  >
                    {tf}
                  </button>
                );
              })}
            </div>
            {selectedInstrument && selectedTimeframes.some((tf) => !hasData(selectedInstrument, tf)) && (
              <p className="text-amber-600 text-xs mt-1">
                Some selected timeframes have no data for {selectedInstrument}
              </p>
            )}
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1">Min Trades</label>
            <input
              type="number"
              value={minTrades}
              onChange={(e) => setMinTrades(Number(e.target.value))}
              min={1}
              max={1000}
              className="input w-20"
            />
          </div>
        </div>

        {/* Scoring Weights Toggle */}
        <div className="mb-3">
          <button
            type="button"
            onClick={() => setShowWeights(!showWeights)}
            className="text-xs text-primary hover:underline"
          >
            {showWeights ? "Hide scoring weights" : "Customise scoring weights"}
          </button>
          {showWeights && (
            <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-2">
              {(Object.keys(DEFAULT_WEIGHTS) as (keyof ScoringWeights)[]).map((key) => (
                <div key={key}>
                  <label className="block text-xs text-foreground-muted mb-0.5">
                    {key.replace("weight_", "").replace(/_/g, " ")}
                  </label>
                  <input
                    type="number"
                    value={weights[key]}
                    onChange={(e) => updateWeight(key, Number(e.target.value))}
                    min={0}
                    max={1}
                    step={0.05}
                    className="border border-gray-300 rounded px-2 py-1 text-xs bg-background w-full"
                  />
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Presets */}
        <div className="mb-3 flex flex-wrap items-end gap-3">
          {presets && presets.length > 0 && (
            <div>
              <label className="block text-xs text-foreground-muted mb-1">Load Preset</label>
              <select
                className="input"
                value=""
                onChange={(e) => {
                  const p = presets.find((pr) => pr.id === Number(e.target.value));
                  if (p) loadPreset(p);
                }}
              >
                <option value="">Select preset...</option>
                {presets.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          )}
          <div className="flex items-end gap-2">
            <div>
              <label className="block text-xs text-foreground-muted mb-1">Save as Preset</label>
              <input
                type="text"
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="Preset name"
                className="input w-40"
              />
            </div>
            <button
              type="button"
              onClick={handleSavePreset}
              disabled={savingPreset || !presetName.trim()}
              className="btn btn-secondary text-xs"
            >
              {savingPreset ? "Saving..." : "Save"}
            </button>
          </div>
          {presets && presets.length > 0 && (
            <div className="text-xs text-foreground-muted">
              {presets.map((p) => (
                <span key={p.id} className="inline-flex items-center gap-1 mr-2">
                  {p.name}
                  <button onClick={() => handleDeletePreset(p.id)} className="text-danger hover:underline">&times;</button>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Submit */}
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={running || selectedStrategies.length === 0 || !selectedInstrument || selectedTimeframes.length === 0}
            className="btn btn-primary"
          >
            {running ? "Running..." : "Run Research"}
          </button>
          {running && (
            <span className="text-xs text-foreground-muted">
              Running... {runProgress}%
            </span>
          )}
          {!running && lastSummary && (
            <span className="text-xs text-foreground-muted">
              Run {lastSummary.run_id}: {lastSummary.completed}/{lastSummary.total_combinations} completed, {lastSummary.qualified} qualified
              <InfoTip text="Completed = combos backtested. Qualified = combos that met the minimum trade threshold (default 80 trades) and are ranked." />
              {lastSummary.qualified === 0 && lastSummary.completed > 0 && (
                <span className="text-amber-600 ml-1">
                  (no combinations met min_trades={lastSummary.min_trades})
                </span>
              )}
            </span>
          )}
        </div>
      </form>

      {/* Heatmap */}
      {strategyIds.length > 0 && instrumentIds.length > 0 && (
        <div className="card">
          <Heatmap
            z={z}
            x={instrumentIds}
            y={strategyIds.map((s) => `${s} ${strategyShortName(s)}`)}
            title="Composite Score by Strategy x Instrument"
          />
        </div>
      )}

      {/* Saved Shortlist */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-medium text-foreground">
              Saved Shortlist<InfoTip text="Your curated list of promising strategy/instrument/timeframe combos. Persists independently of run results — safe from clear operations." />
            </h3>
            <p className="text-xs text-foreground-muted mt-0.5">
              Durable operator-curated list of promising combos. Persists independently of run results.
            </p>
          </div>
          {shortlist.length > 0 && (
            <span className="text-xs text-foreground-muted">{shortlist.length} saved</span>
          )}
        </div>
        {shortlist.length === 0 ? (
          <p className="text-sm text-foreground-muted py-4 text-center">
            No saved combos yet. Use the &quot;Save&quot; button in the rankings table below to add promising combos.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 bg-background-muted">
                  <th className="text-left px-3 py-2 text-xs text-foreground-muted">Strategy</th>
                  <th className="text-left px-3 py-2 text-xs text-foreground-muted">Instrument</th>
                  <th className="text-left px-3 py-2 text-xs text-foreground-muted">TF</th>
                  <th className="text-right px-3 py-2 text-xs text-foreground-muted">Score</th>
                  <th className="text-left px-3 py-2 text-xs text-foreground-muted">Status</th>
                  <th className="text-left px-3 py-2 text-xs text-foreground-muted">Note</th>
                  <th className="text-right px-3 py-2 text-xs text-foreground-muted">Actions</th>
                </tr>
              </thead>
              <tbody>
                {shortlist.map((entry) => (
                  <tr key={entry.id} className="border-b border-gray-100 hover:bg-background-muted/50">
                    <td className="px-3 py-2">
                      <span className="font-medium">{entry.strategy_id}</span>
                      <span className="block text-[10px] text-foreground-muted">{strategyShortName(entry.strategy_id)}</span>
                    </td>
                    <td className="px-3 py-2">{entry.instrument}</td>
                    <td className="px-3 py-2">{entry.timeframe}</td>
                    <td className="px-3 py-2 text-right font-medium">
                      <span className={entry.score >= PROMOTION_THRESHOLD ? "text-green-600" : ""}>{entry.score.toFixed(3)}</span>
                    </td>
                    <td className="px-3 py-2">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                        entry.status === "active" ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"
                      }`}>
                        {entry.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 max-w-[200px]">
                      {shortlistNote[entry.id] !== undefined ? (
                        <div className="flex items-center gap-1">
                          <input
                            type="text"
                            value={shortlistNote[entry.id]}
                            onChange={(e) => setShortlistNote((prev) => ({ ...prev, [entry.id]: e.target.value }))}
                            className="input text-xs w-full"
                            placeholder="Add note..."
                            onKeyDown={async (e) => {
                              if (e.key === "Enter") {
                                await updateShortlistEntry(entry.id, { note: shortlistNote[entry.id] });
                                setShortlistNote((prev) => { const n = { ...prev }; delete n[entry.id]; return n; });
                              }
                              if (e.key === "Escape") {
                                setShortlistNote((prev) => { const n = { ...prev }; delete n[entry.id]; return n; });
                              }
                            }}
                          />
                        </div>
                      ) : (
                        <button
                          onClick={() => setShortlistNote((prev) => ({ ...prev, [entry.id]: entry.note ?? "" }))}
                          className="text-xs text-foreground-muted hover:text-foreground truncate block max-w-[200px]"
                          title={entry.note || "Click to add a note"}
                        >
                          {entry.note || "—"}
                        </button>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right space-x-2">
                      {entry.status === "active" ? (
                        <button
                          onClick={() => updateShortlistEntry(entry.id, { status: "archived" })}
                          className="text-xs text-foreground-muted hover:underline"
                        >
                          Archive
                        </button>
                      ) : (
                        <button
                          onClick={() => updateShortlistEntry(entry.id, { status: "active" })}
                          className="text-xs text-primary hover:underline"
                        >
                          Restore
                        </button>
                      )}
                      <button
                        onClick={() => removeFromShortlist(entry.id)}
                        className="text-xs text-danger hover:underline"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Run Results */}
      <div className="table-container">
        <div className="px-4 py-3 border-b border-gray-200 bg-background-muted space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-foreground">Run Results</span>
              <span className="text-xs text-foreground-muted">
                {isAllRunsMode
                  ? "Best score per combo across all runs"
                  : effectiveRunId
                    ? `Run ${effectiveRunId}`
                    : "No run selected"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {rankings && rankings.length > 0 && (
                <button
                  onClick={handleValidateTop}
                  disabled={validatingLoading}
                  className="text-xs text-primary hover:underline disabled:opacity-50"
                >
                  {validatingLoading ? "Validating..." : "Validate Top 10"}
                </button>
              )}
              <button
                onClick={() => setShowBookmarked(!showBookmarked)}
                className={`text-xs px-2 py-1 rounded border ${showBookmarked ? "bg-amber-50 border-amber-300 text-amber-700" : "border-gray-200"}`}
              >
                {showBookmarked ? "Bookmarked" : "Bookmarks"}
              </button>
            </div>
          </div>
          {/* Run selector + Clear controls */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <label className="text-xs text-foreground-muted">Scope:</label>
              <select
                value={runScope}
                onChange={(e) => setRunScope(e.target.value)}
                className="text-xs border border-gray-300 rounded px-2 py-1 bg-background"
              >
                {currentRunId && <option value="current">Latest run ({currentRunId})</option>}
                <option value="all">All runs (best per combo)</option>
                {researchRuns
                  .filter((r) => r.run_id !== currentRunId)
                  .map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      Run {r.run_id} — {r.result_count} results, top {r.top_score.toFixed(3)}
                      {r.created_at ? ` (${new Date(r.created_at).toLocaleDateString()})` : ""}
                    </option>
                  ))}
              </select>
            </div>
            {rankings && rankings.length > 0 && (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setConfirmAction({
                    label: isAllRunsMode ? "Clear All Run Results" : `Clear Run ${effectiveRunId}`,
                    description: isAllRunsMode
                      ? `This will permanently delete all ${rankings.length} research result rows across every run. Your Saved Shortlist is NOT affected.`
                      : `This will permanently delete all results from run ${effectiveRunId}. Your Saved Shortlist is NOT affected.`,
                    onConfirm: async () => {
                      await api.deleteResultsBulk(isAllRunsMode ? undefined : effectiveRunId ?? undefined);
                      await mutate();
                      await mutateRuns();
                      if (!isAllRunsMode && effectiveRunId === currentRunId) setCurrentRunId(null);
                    },
                  })}
                  className="text-xs text-danger hover:underline"
                >
                  {isAllRunsMode ? "Clear All" : "Clear This Run"}
                </button>
                <button
                  onClick={() => setConfirmAction({
                    label: "Clear Non-Saved Results",
                    description: "This will delete all research results whose combos are NOT in your Saved Shortlist. Results matching saved combos will be kept. Your Saved Shortlist is NOT affected.",
                    onConfirm: async () => {
                      await api.deleteNonSavedResults(isAllRunsMode ? undefined : effectiveRunId ?? undefined);
                      await mutate();
                      await mutateRuns();
                    },
                  })}
                  className="text-xs text-amber-600 hover:underline"
                  title="Deletes results whose combos are NOT in your Saved Shortlist. Saved combos are kept."
                >
                  Clear Non-Saved
                </button>
              </div>
            )}
          </div>
        </div>
        {/* Bulk actions bar */}
        {selectedResults.size > 0 && (
          <div className="flex items-center gap-3 px-4 py-2 bg-primary/5 border border-primary/20 rounded-lg mb-2">
            <span className="text-xs font-medium">{selectedResults.size} selected</span>
            <button onClick={handleBulkCreateBots} disabled={bulkDeploying} className="btn btn-primary text-xs">
              {bulkDeploying ? <><Loader2 size={12} className="animate-spin" /> Deploying...</> : `Create ${selectedResults.size} Bot${selectedResults.size !== 1 ? "s" : ""}`}
            </button>
            <button onClick={deselectAllResults} className="text-xs text-foreground-muted hover:underline">Clear</button>
            {bulkResult && <span className="text-xs text-green-600">{bulkResult}</span>}
          </div>
        )}
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-background-muted">
              <th className="w-8 px-2">
                <input
                  type="checkbox"
                  checked={rankings != null && selectedResults.size === rankings.length && rankings.length > 0}
                  onChange={() => selectedResults.size === (rankings?.length ?? 0) ? deselectAllResults() : selectAllResults()}
                  className="rounded border-gray-300"
                  title="Select all for bulk bot creation"
                />
              </th>
              <th className="w-8 px-2"></th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Rank</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Strategy</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">Instrument</th>
              <th className="text-left px-4 py-3 font-medium text-foreground-muted">TF</th>
              {isAllRunsMode && <th className="text-left px-4 py-3 font-medium text-foreground-muted">Run</th>}
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">
                Score<InfoTip text="Composite Score: weighted blend of Sharpe ratio, profit factor, return, drawdown, sample size, and stability. Higher = better risk-adjusted performance." />
              </th>
              <th className="text-right px-4 py-3 font-medium text-foreground-muted">Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={isAllRunsMode ? 9 : 8} className="px-4 py-8 text-center text-foreground-muted">Loading...</td>
              </tr>
            )}
            {!isLoading && (!rankings || rankings.length === 0) && (
              <tr>
                <td colSpan={isAllRunsMode ? 9 : 8} className="px-4 py-8 text-center text-foreground-muted">
                  No research results yet. Run a full sweep above or configure a custom research run.
                </td>
              </tr>
            )}
            {rankings
              ?.filter((r) => !showBookmarked || isBookmarked("research_result", r.id))
              .map((r) => (
              <tr key={r.id} className={`border-b border-gray-100 hover:bg-background-muted/50 ${selectedResults.has(r.id) ? "bg-primary/5" : ""}`}>
                <td className="px-2">
                  <input
                    type="checkbox"
                    checked={selectedResults.has(r.id)}
                    onChange={() => toggleResultSelection(r.id)}
                    className="rounded border-gray-300"
                  />
                </td>
                <td className="px-2">
                  <BookmarkButton
                    isBookmarked={isBookmarked("research_result", r.id)}
                    onToggle={() => toggleBookmark("research_result", r.id)}
                  />
                </td>
                <td className="px-4 py-3 font-medium">{r.rank}</td>
                <td className="px-4 py-3">
                  <span className="font-medium">{r.strategy_id}</span>
                  <span className="block text-[10px] text-foreground-muted truncate max-w-[160px]">{strategyShortName(r.strategy_id)}</span>
                </td>
                <td className="px-4 py-3">{r.instrument}</td>
                <td className="px-4 py-3">{r.timeframe}</td>
                {isAllRunsMode && <td className="px-4 py-3 text-xs text-foreground-muted">{r.run_id}</td>}
                <td className="px-4 py-3 text-right font-medium">
                  <span className={r.composite_score >= PROMOTION_THRESHOLD ? "text-green-600" : "text-foreground"}>
                    {r.composite_score.toFixed(3)}
                  </span>
                </td>
                <td className="px-4 py-3 text-right space-x-2">
                  <button
                    onClick={() => saveToShortlist({
                      strategy_id: r.strategy_id,
                      instrument: r.instrument,
                      timeframe: r.timeframe,
                      score: r.composite_score,
                      source_run_id: r.run_id,
                      metrics_snapshot: r.metrics_json ?? undefined,
                    })}
                    className={`text-xs hover:underline ${
                      isShortlisted(r.strategy_id, r.instrument, r.timeframe)
                        ? "text-amber-600"
                        : "text-foreground-muted"
                    }`}
                  >
                    {isShortlisted(r.strategy_id, r.instrument, r.timeframe) ? "Saved" : "Save"}
                  </button>
                  <a
                    href={`/backtests?strategy=${r.strategy_id}&instrument=${r.instrument}&timeframe=${r.timeframe}`}
                    className="text-xs text-foreground-muted hover:text-primary hover:underline"
                    title="Open in Backtests with this combo pre-filled"
                  >
                    Backtest
                  </a>
                  <button
                    onClick={() => handleAdvancedResearch(r.strategy_id, r.instrument, r.timeframe)}
                    disabled={advancedLoading}
                    className="text-xs text-primary hover:underline disabled:opacity-50"
                  >
                    Analyse
                  </button>
                  <button
                    onClick={() => setPromoteTarget({
                      strategy_id: r.strategy_id,
                      instrument: r.instrument,
                      timeframe: r.timeframe,
                      composite_score: r.composite_score,
                    })}
                    className="text-xs text-green-600 hover:underline"
                  >
                    Create Bot
                  </button>
                  <button
                    onClick={async () => {
                      await api.deleteSingleResult(r.id);
                      await mutate();
                      await mutateRuns();
                    }}
                    className="text-xs text-foreground-muted hover:text-danger hover:underline"
                    title="Remove this result row"
                  >
                    &times;
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Validation Results */}
      {validationResult && (
        <div className="card">
          <h3 className="text-sm font-medium mb-3">
            Validation Results — {validationResult.total_passed}/{validationResult.total_validated} passed ({(validationResult.pass_rate * 100).toFixed(0)}%)
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left px-3 py-2 text-xs text-foreground-muted">Strategy</th>
                  <th className="text-left px-3 py-2 text-xs text-foreground-muted">Instrument</th>
                  <th className="text-left px-3 py-2 text-xs text-foreground-muted">TF</th>
                  <th className="text-right px-3 py-2 text-xs text-foreground-muted">Original</th>
                  <th className="text-right px-3 py-2 text-xs text-foreground-muted">Validation</th>
                  <th className="text-right px-3 py-2 text-xs text-foreground-muted">Divergence</th>
                  <th className="text-center px-3 py-2 text-xs text-foreground-muted">Status</th>
                </tr>
              </thead>
              <tbody>
                {validationResult.results.map((v) => (
                  <tr key={`${v.strategy_id}-${v.instrument}-${v.timeframe}`} className="border-b border-gray-100">
                    <td className="px-3 py-2" title={strategyShortName(v.strategy_id)}>{v.strategy_id}</td>
                    <td className="px-3 py-2">{v.instrument}</td>
                    <td className="px-3 py-2">{v.timeframe}</td>
                    <td className="px-3 py-2 text-right">{v.original_score.toFixed(3)}</td>
                    <td className="px-3 py-2 text-right">{v.validation_score.toFixed(3)}</td>
                    <td className="px-3 py-2 text-right">{v.score_divergence.toFixed(3)}</td>
                    <td className="px-3 py-2 text-center">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                        v.passed ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
                      }`}>
                        {v.passed ? "Pass" : "Fail"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Advanced Research Results */}
      {advancedLoading && (
        <div className="card text-center">
          <p className="text-foreground-muted text-sm">Running advanced analysis...</p>
        </div>
      )}

      {advancedResult && (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">Advanced Analysis</h3>

          {/* Walk-Forward */}
          {advancedResult.walk_forward && (
            <div className="card">
              <h4 className="text-sm font-medium mb-2">Walk-Forward Analysis</h4>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                <div>
                  <p className="text-xs text-foreground-muted">Windows</p>
                  <p className="text-lg font-semibold">{advancedResult.walk_forward.total_windows}</p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Avg Test Score</p>
                  <p className="text-lg font-semibold">{advancedResult.walk_forward.avg_test_score.toFixed(4)}</p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Avg Test Sharpe</p>
                  <p className="text-lg font-semibold">{advancedResult.walk_forward.avg_test_sharpe.toFixed(4)}</p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Score Degradation</p>
                  <p className={`text-lg font-semibold ${advancedResult.walk_forward.score_degradation > 0.1 ? "text-danger" : "text-primary"}`}>
                    {advancedResult.walk_forward.score_degradation.toFixed(4)}
                  </p>
                </div>
              </div>
              {advancedResult.walk_forward.windows.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-gray-200">
                        <th className="text-left px-2 py-1 text-foreground-muted">Window</th>
                        <th className="text-right px-2 py-1 text-foreground-muted">Train Score</th>
                        <th className="text-right px-2 py-1 text-foreground-muted">Test Score</th>
                        <th className="text-right px-2 py-1 text-foreground-muted">Train Trades</th>
                        <th className="text-right px-2 py-1 text-foreground-muted">Test Trades</th>
                        <th className="text-right px-2 py-1 text-foreground-muted">Test Profit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {advancedResult.walk_forward.windows.map((w) => (
                        <tr key={w.window_index} className="border-b border-gray-50">
                          <td className="px-2 py-1">{w.window_index}</td>
                          <td className="px-2 py-1 text-right">{w.train_score.toFixed(4)}</td>
                          <td className="px-2 py-1 text-right">{w.test_score.toFixed(4)}</td>
                          <td className="px-2 py-1 text-right">{w.train_trades}</td>
                          <td className="px-2 py-1 text-right">{w.test_trades}</td>
                          <td className={`px-2 py-1 text-right ${w.test_net_profit >= 0 ? "text-primary" : "text-danger"}`}>
                            {formatCurrency(w.test_net_profit)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Out-of-Sample */}
          {advancedResult.oos && (
            <div className="card">
              <h4 className="text-sm font-medium mb-2">Out-of-Sample Test</h4>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <p className="text-xs text-foreground-muted">IS Score</p>
                  <p className="text-lg font-semibold">{advancedResult.oos.is_score.toFixed(4)}</p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">OOS Score</p>
                  <p className="text-lg font-semibold">{advancedResult.oos.oos_score.toFixed(4)}</p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Score Degradation</p>
                  <p className={`text-lg font-semibold ${advancedResult.oos.score_degradation > 0.1 ? "text-danger" : "text-primary"}`}>
                    {advancedResult.oos.score_degradation.toFixed(4)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Robust</p>
                  <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                    advancedResult.oos.robust ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
                  }`}>
                    {advancedResult.oos.robust ? "Yes" : "No"}
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-3 text-sm">
                <div className="space-y-1">
                  <p className="text-xs text-foreground-muted font-medium">In-Sample</p>
                  <p>Trades: {advancedResult.oos.is_trades} | Sharpe: {advancedResult.oos.is_sharpe.toFixed(2)} | Profit: {formatCurrency(advancedResult.oos.is_net_profit)}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-foreground-muted font-medium">Out-of-Sample</p>
                  <p>Trades: {advancedResult.oos.oos_trades} | Sharpe: {advancedResult.oos.oos_sharpe.toFixed(2)} | Profit: {formatCurrency(advancedResult.oos.oos_net_profit)}</p>
                </div>
              </div>
            </div>
          )}

          {/* Monte Carlo */}
          {advancedResult.monte_carlo && (
            <div className="card">
              <h4 className="text-sm font-medium mb-2">Monte Carlo Robustness</h4>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <p className="text-xs text-foreground-muted">Profit Probability</p>
                  <p className="text-lg font-semibold">{(advancedResult.monte_carlo.profit_probability * 100).toFixed(1)}%</p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Ruin Probability</p>
                  <p className={`text-lg font-semibold ${advancedResult.monte_carlo.ruin_probability > 0.1 ? "text-danger" : "text-primary"}`}>
                    {(advancedResult.monte_carlo.ruin_probability * 100).toFixed(1)}%
                  </p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Mean Profit</p>
                  <p className={`text-lg font-semibold ${advancedResult.monte_carlo.mean_net_profit >= 0 ? "text-primary" : "text-danger"}`}>
                    {formatCurrency(advancedResult.monte_carlo.mean_net_profit)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Robust</p>
                  <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                    advancedResult.monte_carlo.robust ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
                  }`}>
                    {advancedResult.monte_carlo.robust ? "Yes" : "No"}
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3 mt-3 text-sm">
                <div>
                  <p className="text-xs text-foreground-muted">P5 (worst case)</p>
                  <p>{formatCurrency(advancedResult.monte_carlo.p5_net_profit)}</p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">Median</p>
                  <p>{formatCurrency(advancedResult.monte_carlo.median_net_profit)}</p>
                </div>
                <div>
                  <p className="text-xs text-foreground-muted">P95 (best case)</p>
                  <p>{formatCurrency(advancedResult.monte_carlo.p95_net_profit)}</p>
                </div>
              </div>
            </div>
          )}

          {/* Sensitivity */}
          {advancedResult.sensitivity && advancedResult.sensitivity.length > 0 && (
            <div className="card">
              <h4 className="text-sm font-medium mb-2">Parameter Sensitivity</h4>
              {advancedResult.sensitivity.map((sens, idx) => (
                <div key={idx} className="mb-4 last:mb-0">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-sm font-medium">{sens.param_name}</span>
                    <span className="text-xs text-foreground-muted">baseline: {sens.baseline_value}</span>
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      sens.robust ? "bg-green-100 text-green-800" : "bg-yellow-100 text-yellow-800"
                    }`}>
                      {sens.robust ? "Stable" : "Sensitive"}
                    </span>
                    <span className="text-xs text-foreground-muted">range: {sens.score_range.toFixed(4)}</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-gray-200">
                          <th className="text-left px-2 py-1 text-foreground-muted">Value</th>
                          <th className="text-right px-2 py-1 text-foreground-muted">Trades</th>
                          <th className="text-right px-2 py-1 text-foreground-muted">Profit</th>
                          <th className="text-right px-2 py-1 text-foreground-muted">Sharpe</th>
                          <th className="text-right px-2 py-1 text-foreground-muted">Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sens.variations.map((v, vi) => (
                          <tr key={vi} className="border-b border-gray-50">
                            <td className="px-2 py-1">{v.param_value}</td>
                            <td className="px-2 py-1 text-right">{v.total_trades}</td>
                            <td className={`px-2 py-1 text-right ${v.net_profit >= 0 ? "text-primary" : "text-danger"}`}>
                              {formatCurrency(v.net_profit)}
                            </td>
                            <td className="px-2 py-1 text-right">{v.sharpe_ratio.toFixed(4)}</td>
                            <td className="px-2 py-1 text-right font-medium">{v.composite_score.toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Promotion success/error feedback */}
      {promoteSuccess && (
        <div className="card bg-green-50 border-green-200 text-green-800 text-sm flex items-center justify-between">
          <span>{promoteSuccess}</span>
          <button onClick={() => setPromoteSuccess(null)} className="text-green-600 hover:underline text-xs">Dismiss</button>
        </div>
      )}

      {/* Destructive action confirmation dialog */}
      {confirmAction && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-background rounded-lg shadow-xl p-6 max-w-md w-full mx-4 space-y-4">
            <h3 className="text-lg font-semibold text-danger">{confirmAction.label}</h3>
            <p className="text-sm text-foreground-muted">{confirmAction.description}</p>
            <div className="flex gap-3 justify-end pt-2">
              <button
                onClick={() => setConfirmAction(null)}
                className="btn btn-secondary text-sm"
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  await confirmAction.onConfirm();
                  setConfirmAction(null);
                }}
                className="btn text-sm bg-danger text-white hover:bg-red-700"
              >
                Confirm Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Promotion confirmation dialog */}
      {promoteTarget && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-background rounded-lg shadow-xl p-6 max-w-md w-full mx-4 space-y-4">
            <h3 className="text-lg font-semibold">Promote to Paper Trading</h3>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-foreground-muted">Strategy</span>
                <span className="font-medium">{promoteTarget.strategy_id} — {strategyShortName(promoteTarget.strategy_id)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-foreground-muted">Instrument</span>
                <span className="font-medium">{promoteTarget.instrument}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-foreground-muted">Timeframe</span>
                <span className="font-medium">{promoteTarget.timeframe}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-foreground-muted">Composite Score</span>
                <span className="font-medium text-green-600">{promoteTarget.composite_score.toFixed(3)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-foreground-muted">Threshold</span>
                <span className="font-medium">{PROMOTION_THRESHOLD.toFixed(3)}</span>
              </div>
            </div>
            {promoteError && (
              <p className="text-sm text-red-600">{promoteError}</p>
            )}
            <div className="flex gap-3 justify-end pt-2">
              <button
                onClick={() => { setPromoteTarget(null); setPromoteError(null); }}
                className="btn btn-secondary text-sm"
                disabled={promoteLoading}
              >
                Cancel
              </button>
              <button
                onClick={handlePromote}
                className="btn btn-primary text-sm"
                disabled={promoteLoading}
              >
                {promoteLoading ? "Creating..." : "Create Bot"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
