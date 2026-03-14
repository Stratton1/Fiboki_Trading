"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { formatPnl, formatCurrency, currencySymbol } from "@/lib/format-currency";
import { useAccount } from "@/lib/hooks/use-bots";
import { useShortlist } from "@/lib/hooks/use-shortlist";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { InfoTip } from "@/components/InfoTip";
import {
  Loader2,
  Play,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  Layers,
  Star,
  ChevronDown,
  ChevronUp,
  Info,
  Search,
  X,
  Check,
} from "lucide-react";

/* ── Valid platform timeframes (must match backend Timeframe enum) ── */
const VALID_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"];

/* ── Asset class display ── */
const ASSET_CLASS_LABELS: Record<string, string> = {
  forex_major: "Forex Major",
  forex_cross: "Forex Cross",
  forex_g10_cross: "Forex G10 Cross",
  forex_scandinavian: "Forex Scandinavian",
  forex_em: "Forex EM",
  commodity_metal: "Metals",
  commodity_energy: "Energy",
  index: "Indices",
  crypto: "Crypto",
};
const ASSET_CLASS_ORDER = [
  "forex_major",
  "forex_cross",
  "forex_g10_cross",
  "forex_scandinavian",
  "forex_em",
  "commodity_metal",
  "commodity_energy",
  "index",
  "crypto",
];

/* ── Types ── */
interface InstrumentItem {
  symbol: string;
  name: string;
  asset_class: string;
  has_canonical_data: boolean;
}

interface BotResult {
  strategy_id: string;
  instrument: string;
  timeframe: string;
  total_trades?: number;
  net_profit?: number;
  win_rate?: number;
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
  aggregate_sharpe: number | null;
  aggregate_win_rate: number | null;
  capital?: number;
  is_mixed_timeframe?: boolean;
}

type SortField = "strategy_id" | "instrument" | "timeframe" | "total_trades" | "net_profit" | "sharpe_ratio" | "max_drawdown_pct" | "win_rate";

/* ── Multi-select instrument picker (grouped by asset class) ── */
function GroupedInstrumentMultiSelect({
  instruments,
  selected,
  onToggle,
  onSelectAll,
  onClear,
}: {
  instruments: InstrumentItem[];
  selected: string[];
  onToggle: (symbol: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
}) {
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const canonicalOnly = instruments.filter((i) => i.has_canonical_data);
  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return canonicalOnly;
    return canonicalOnly.filter(
      (i) =>
        i.symbol.toLowerCase().includes(q) ||
        i.name.toLowerCase().includes(q) ||
        (ASSET_CLASS_LABELS[i.asset_class] ?? "").toLowerCase().includes(q)
    );
  }, [canonicalOnly, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, InstrumentItem[]>();
    for (const inst of filtered) {
      const list = map.get(inst.asset_class) ?? [];
      list.push(inst);
      map.set(inst.asset_class, list);
    }
    return map;
  }, [filtered]);

  const nonCanonicalCount = instruments.length - canonicalOnly.length;

  return (
    <div className="bg-background-card rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-2">
        <label className="text-sm font-medium">Instruments</label>
        <InfoTip text="Only instruments with canonical historical data are shown. Instruments without data (e.g. crypto) are excluded as they would fail during simulation." />
        <div className="flex-1" />
        <span className="text-xs text-foreground-muted tabular-nums">
          {selected.length} selected
        </span>
        <button
          type="button"
          onClick={onSelectAll}
          className="text-xs text-primary hover:underline"
        >
          Select all
        </button>
        <button
          type="button"
          onClick={onClear}
          className="text-xs text-foreground-muted hover:underline"
        >
          Clear
        </button>
      </div>

      {/* Search */}
      <div className="flex items-center gap-2 bg-background-muted rounded-md px-2.5 py-1.5 mb-3">
        <Search size={14} className="text-foreground-muted shrink-0" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search instruments..."
          className="bg-transparent text-sm flex-1 outline-none placeholder:text-foreground-muted"
        />
        {search && (
          <button onClick={() => setSearch("")} className="text-foreground-muted hover:text-foreground">
            <X size={12} />
          </button>
        )}
      </div>

      {/* Grouped list */}
      <div className="max-h-56 overflow-y-auto space-y-1">
        {ASSET_CLASS_ORDER.filter((ac) => grouped.has(ac)).map((ac) => {
          const items = grouped.get(ac)!;
          const isCollapsed = collapsed[ac];
          const selectedInGroup = items.filter((i) => selected.includes(i.symbol)).length;
          return (
            <div key={ac}>
              <button
                type="button"
                onClick={() => setCollapsed({ ...collapsed, [ac]: !isCollapsed })}
                className="w-full flex items-center justify-between text-[10px] uppercase font-semibold text-foreground-muted px-2 py-1 bg-background-muted rounded hover:bg-background-muted/80"
              >
                <span>
                  {ASSET_CLASS_LABELS[ac]} ({items.length})
                  {selectedInGroup > 0 && (
                    <span className="ml-1 text-primary">{selectedInGroup} selected</span>
                  )}
                </span>
                {isCollapsed ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
              </button>
              {!isCollapsed && (
                <div className="flex flex-wrap gap-1.5 px-1 py-1.5">
                  {items.map((inst) => (
                    <button
                      key={inst.symbol}
                      type="button"
                      onClick={() => onToggle(inst.symbol)}
                      className={`px-2 py-1 rounded text-xs border transition-colors ${
                        selected.includes(inst.symbol)
                          ? "bg-primary text-white border-primary"
                          : "bg-background border-gray-300 text-foreground hover:border-primary/40"
                      }`}
                      title={inst.name}
                    >
                      {inst.symbol}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {nonCanonicalCount > 0 && (
        <p className="text-[10px] text-foreground-muted/60 mt-2 px-1">
          {nonCanonicalCount} instrument{nonCanonicalCount > 1 ? "s" : ""} without canonical data hidden
        </p>
      )}
    </div>
  );
}

/* ── Mini equity sparkline for per-bot rows ── */
function MiniSparkline({ data, positive }: { data: number[]; positive: boolean }) {
  if (data.length < 2) return <span className="text-xs text-foreground-muted">—</span>;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = Math.max(1, Math.floor(data.length / 40));
  const sampled = data.filter((_, i) => i % step === 0);

  const w = 80;
  const h = 20;
  const points = sampled.map((v, i) => {
    const x = (i / (sampled.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x},${y}`;
  });

  return (
    <svg width={w} height={h} className="inline-block">
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={positive ? "#16A34A" : "#DC2626"}
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* ── SVG equity curve chart ── */
function EquityCurveChart({ data, currency }: { data: number[]; currency: string }) {
  const W = 800;
  const H = 160;
  const PAD = { top: 10, right: 10, bottom: 24, left: 60 };
  const chartW = W - PAD.left - PAD.right;
  const chartH = H - PAD.top - PAD.bottom;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  // Sample to ~400 points for performance
  const step = Math.max(1, Math.floor(data.length / 400));
  const sampled = data.filter((_, i) => i % step === 0 || i === data.length - 1);

  const points = sampled.map((v, i) => {
    const x = PAD.left + (i / (sampled.length - 1)) * chartW;
    const y = PAD.top + chartH - ((v - min) / range) * chartH;
    return { x, y, v };
  });

  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  const areaD = pathD + ` L ${points[points.length - 1].x} ${PAD.top + chartH} L ${points[0].x} ${PAD.top + chartH} Z`;

  const startVal = data[0];
  const endVal = data[data.length - 1];
  const positive = endVal >= startVal;
  const color = positive ? "#16A34A" : "#DC2626";

  // Y-axis labels
  const yLabels = [min, min + range * 0.5, max];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
      {/* Grid lines */}
      {yLabels.map((v, i) => {
        const y = PAD.top + chartH - ((v - min) / range) * chartH;
        return (
          <g key={i}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} stroke="#E5E5E5" strokeWidth={0.5} />
            <text x={PAD.left - 6} y={y + 3} textAnchor="end" className="text-[9px] fill-gray-400">
              {formatCurrency(v, currency, 0)}
            </text>
          </g>
        );
      })}

      {/* Area fill */}
      <path d={areaD} fill={color} opacity={0.08} />

      {/* Line */}
      <path d={pathD} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />

      {/* Start/end labels */}
      <text x={PAD.left} y={H - 4} className="text-[10px] fill-gray-500">
        {formatCurrency(startVal, currency, 0)}
      </text>
      <text x={W - PAD.right} y={H - 4} textAnchor="end" className={`text-[10px] ${positive ? "fill-green-600" : "fill-red-600"}`}>
        {formatCurrency(endVal, currency, 0)}
      </text>
    </svg>
  );
}

/* ── Main page ── */
export default function ScenariosPage() {
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { data: instrumentsData } = useSWR("instruments", () => api.instruments());
  const instruments = (instrumentsData ?? []) as InstrumentItem[];
  const { data: account } = useAccount();
  const { shortlist } = useShortlist();

  const currency = account?.currency ?? "GBP";

  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [selectedInstruments, setSelectedInstruments] = useState<string[]>([]);
  const [selectedTimeframes, setSelectedTimeframes] = useState<string[]>(["H1"]);
  const [capital, setCapital] = useState(10000);

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [resultJobId, setResultJobId] = useState<string | null>(null);

  // Sort state for per-bot table
  const [sortField, setSortField] = useState<SortField>("net_profit");
  const [sortAsc, setSortAsc] = useState(false);

  // Try to restore result from a completed scenario job on mount
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const jobId = params.get("job");
    if (jobId && !result) {
      api.getJob(jobId).then((job) => {
        if (job.state === "completed" && job.result) {
          setResult(job.result as unknown as ScenarioResult);
          setResultJobId(jobId);
        }
      }).catch(() => {
        // Job not found or error — ignore
      });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function toggleItem(list: string[], setList: (v: string[]) => void, item: string) {
    setList(list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);
  }

  const canonicalInstruments = useMemo(
    () => instruments.filter((i) => i.has_canonical_data),
    [instruments]
  );

  const comboCount = selectedStrategies.length * selectedInstruments.length * selectedTimeframes.length;

  // Mixed timeframe warning (pre-run)
  const isMixedTimeframe = selectedTimeframes.length > 1;

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (comboCount === 0) return;

    setRunning(true);
    setProgress(0);
    setError(null);
    setResult(null);
    setResultJobId(null);

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
            setResultJobId(jobId);
            setRunning(false);
            // Update URL so result can be restored on page revisit
            window.history.replaceState(null, "", `?job=${jobId}`);
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

  /** Pre-fill builder from saved shortlist */
  const handleLoadShortlist = useCallback(() => {
    if (shortlist.length === 0) return;
    const strats = [...new Set(shortlist.map((s) => s.strategy_id))];
    const insts = [...new Set(shortlist.map((s) => s.instrument))];
    const tfs = [...new Set(shortlist.map((s) => s.timeframe))];
    setSelectedStrategies(strats);
    setSelectedInstruments(insts);
    setSelectedTimeframes(tfs.filter((tf) => VALID_TIMEFRAMES.includes(tf)));
  }, [shortlist]);

  // Sort per-bot results
  const sortedBots = useMemo(() => {
    if (!result) return [];
    const bots = [...result.per_bot];
    bots.sort((a, b) => {
      // Errors always at bottom
      if (a.error && !b.error) return 1;
      if (!a.error && b.error) return -1;
      if (a.error && b.error) return 0;

      let aVal: number | string = 0;
      let bVal: number | string = 0;

      switch (sortField) {
        case "strategy_id":
        case "instrument":
        case "timeframe":
          aVal = a[sortField] ?? "";
          bVal = b[sortField] ?? "";
          return sortAsc
            ? String(aVal).localeCompare(String(bVal))
            : String(bVal).localeCompare(String(aVal));
        case "total_trades":
          aVal = a.total_trades ?? 0;
          bVal = b.total_trades ?? 0;
          break;
        case "net_profit":
          aVal = a.net_profit ?? 0;
          bVal = b.net_profit ?? 0;
          break;
        case "win_rate":
          aVal = a.win_rate ?? 0;
          bVal = b.win_rate ?? 0;
          break;
        case "sharpe_ratio":
          aVal = a.sharpe_ratio ?? -999;
          bVal = b.sharpe_ratio ?? -999;
          break;
        case "max_drawdown_pct":
          aVal = a.max_drawdown_pct ?? 0;
          bVal = b.max_drawdown_pct ?? 0;
          break;
      }
      return sortAsc ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number);
    });
    return bots;
  }, [result, sortField, sortAsc]);

  function handleSort(field: SortField) {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(false);
    }
  }

  function SortHeader({ field, label, align }: { field: SortField; label: string; align?: string }) {
    const active = sortField === field;
    return (
      <th
        className={`pb-2 pr-3 cursor-pointer select-none hover:text-foreground transition ${align ?? "text-right"}`}
        onClick={() => handleSort(field)}
      >
        <span className="inline-flex items-center gap-0.5">
          {label}
          {active && (sortAsc ? <ChevronUp size={10} /> : <ChevronDown size={10} />)}
        </span>
      </th>
    );
  }

  // Fetch recent scenario jobs for history
  const { data: jobsData } = useSWR("scenario-jobs", () => api.listJobs("job_type=scenario"));
  const pastScenarios = useMemo(() => {
    if (!jobsData?.items) return [];
    return jobsData.items
      .filter((j) => j.state === "completed" && j.result)
      .slice(0, 5);
  }, [jobsData]);

  const strategiesLoading = !strategies;
  const instrumentsLoading = !instrumentsData;

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Scenario Sandbox"
        subtitle="Simulate portfolio combinations across strategies, instruments, and timeframes"
        actions={
          shortlist.length > 0 ? (
            <button
              type="button"
              onClick={handleLoadShortlist}
              className="btn btn-secondary"
            >
              <Star size={14} />
              Load from Shortlist ({shortlist.length})
            </button>
          ) : undefined
        }
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
            <div className="flex-1" />
            <span className="text-xs text-foreground-muted tabular-nums">
              {selectedStrategies.length} selected
            </span>
          </div>
          {strategiesLoading ? (
            <div className="flex items-center gap-2 py-4 text-foreground-muted">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-sm">Loading strategies...</span>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {strategies?.map((s: Record<string, unknown>) => (
                <button
                  key={s.id as string}
                  type="button"
                  onClick={() => toggleItem(selectedStrategies, setSelectedStrategies, s.id as string)}
                  className={`px-2 py-1 rounded text-xs border transition-colors ${
                    selectedStrategies.includes(s.id as string)
                      ? "bg-primary text-white border-primary"
                      : "bg-background border-gray-300 text-foreground hover:border-primary/40"
                  }`}
                >
                  {(s.name as string) || (s.id as string)}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Instruments — grouped, canonical-only, searchable */}
        {instrumentsLoading ? (
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <div className="flex items-center gap-2 py-4 text-foreground-muted">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-sm">Loading instruments...</span>
            </div>
          </div>
        ) : (
          <GroupedInstrumentMultiSelect
            instruments={instruments}
            selected={selectedInstruments}
            onToggle={(sym) => toggleItem(selectedInstruments, setSelectedInstruments, sym)}
            onSelectAll={() =>
              setSelectedInstruments(canonicalInstruments.map((i) => i.symbol))
            }
            onClear={() => setSelectedInstruments([])}
          />
        )}

        {/* Timeframes */}
        <div className="bg-background-card rounded-lg border border-gray-200 p-4">
          <div className="flex items-center gap-2 mb-2">
            <label className="text-sm font-medium">Timeframes</label>
            <InfoTip text="Selecting multiple timeframes produces a mixed-timeframe scenario. The aggregate equity curve is bar-indexed (not time-aligned), so treat it as an overall shape rather than a precise timeline." />
          </div>
          <div className="flex flex-wrap gap-2">
            {VALID_TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                type="button"
                onClick={() => toggleItem(selectedTimeframes, setSelectedTimeframes, tf)}
                className={`px-2 py-1 rounded text-xs border transition-colors ${
                  selectedTimeframes.includes(tf)
                    ? "bg-primary text-white border-primary"
                    : "bg-background border-gray-300 text-foreground hover:border-primary/40"
                }`}
              >
                {tf}
              </button>
            ))}
          </div>
          {isMixedTimeframe && (
            <p className="text-[11px] text-amber-600 mt-2 flex items-center gap-1">
              <Info size={12} />
              Mixed timeframes selected — aggregate equity curve will be bar-indexed, not time-aligned
            </p>
          )}
        </div>

        {/* Capital + Run */}
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="text-xs text-foreground-muted block mb-1">
              Starting Capital ({currencySymbol(currency)})
            </label>
            <input
              type="number"
              min={100}
              step={100}
              value={capital}
              onChange={(e) => setCapital(parseInt(e.target.value, 10) || 10000)}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm w-32 bg-background"
            />
          </div>
          <div className="text-xs text-foreground-muted py-1.5">
            = {formatCurrency(comboCount > 0 ? capital / comboCount : capital, currency, 0)} per bot
          </div>
          <button
            type="submit"
            disabled={running || comboCount === 0}
            className="flex items-center gap-2 bg-primary text-white px-4 py-1.5 rounded text-sm font-medium disabled:opacity-50 transition-opacity"
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

      {/* Empty state — no result and not running */}
      {!result && !running && !error && (
        <>
          <EmptyState
            icon={<Layers size={40} strokeWidth={1.5} />}
            title="No scenario results yet"
            description="Select strategies, instruments, and timeframes above, then click Run to simulate a portfolio."
            action={
              shortlist.length > 0 ? (
                <button
                  type="button"
                  onClick={handleLoadShortlist}
                  className="btn btn-secondary text-sm"
                >
                  <Star size={14} />
                  Load from Shortlist
                </button>
              ) : undefined
            }
          />

          {/* Recent scenario history */}
          {pastScenarios.length > 0 && (
            <div className="bg-background-card rounded-lg border border-gray-200 p-4 mt-4">
              <h3 className="text-sm font-medium mb-3">Recent Scenarios</h3>
              <div className="space-y-2">
                {pastScenarios.map((j) => {
                  const r = j.result as Record<string, unknown>;
                  const bots = (r.per_bot as unknown[])?.length ?? 0;
                  const pnl = r.aggregate_pnl as number | undefined;
                  return (
                    <button
                      key={j.job_id}
                      onClick={() => {
                        api.getJob(j.job_id).then((job) => {
                          if (job.result) {
                            setResult(job.result as unknown as ScenarioResult);
                            setResultJobId(j.job_id);
                            window.history.replaceState(null, "", `?job=${j.job_id}`);
                          }
                        }).catch(() => {});
                      }}
                      className="w-full flex items-center justify-between px-3 py-2 rounded-md text-sm bg-background-muted hover:bg-background-muted/70 transition-colors text-left"
                    >
                      <div>
                        <span className="font-medium">{j.label}</span>
                        <span className="text-xs text-foreground-muted ml-2">
                          {bots} bots, {r.total_trades as number} trades
                        </span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className={`text-sm font-medium tabular-nums ${(pnl ?? 0) >= 0 ? "text-primary" : "text-danger"}`}>
                          {pnl != null ? formatPnl(pnl, currency) : "—"}
                        </span>
                        <span className="text-xs text-foreground-muted">
                          {new Date(j.completed_at ?? j.created_at).toLocaleDateString()}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-4">
          {/* Mixed timeframe warning */}
          {result.is_mixed_timeframe && (
            <div className="flex items-start gap-2 text-amber-700 text-xs bg-amber-50 border border-amber-200 rounded-lg px-4 py-2.5">
              <Info size={14} className="mt-0.5 shrink-0" />
              <div>
                <p className="font-medium">Mixed-timeframe scenario</p>
                <p className="text-amber-600 mt-0.5">
                  This scenario combines bots on different timeframes. The aggregate equity curve
                  is bar-indexed (not time-aligned), so it shows the overall portfolio shape but
                  the x-axis does not represent uniform time. Individual per-bot metrics remain accurate.
                </p>
              </div>
            </div>
          )}

          {/* Aggregate summary */}
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <h3 className="text-sm font-medium mb-3">Portfolio Summary</h3>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4">
              <div>
                <p className="text-xs text-foreground-muted">Bots</p>
                <p className="text-lg font-semibold">{result.per_bot.length}</p>
              </div>
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
                  {formatPnl(result.aggregate_pnl, currency)}
                </p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted">Win Rate</p>
                <p className="text-lg font-semibold">
                  {result.aggregate_win_rate != null
                    ? `${(result.aggregate_win_rate * 100).toFixed(0)}%`
                    : "—"}
                </p>
              </div>
              <div>
                <p className="text-xs text-foreground-muted flex items-center gap-1">
                  Sharpe
                  <InfoTip text="Annualized Sharpe ratio computed from the combined equity curve returns across all bots in the scenario." />
                </p>
                <p className={`text-lg font-semibold ${(result.aggregate_sharpe ?? 0) >= 1 ? "text-primary" : (result.aggregate_sharpe ?? 0) < 0 ? "text-danger" : ""}`}>
                  {result.aggregate_sharpe != null
                    ? result.aggregate_sharpe.toFixed(2)
                    : "—"}
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
                <p className="text-xs text-foreground-muted">Capital</p>
                <p className="text-lg font-semibold">
                  {formatCurrency(result.capital ?? capital, currency, 0)}
                </p>
              </div>
            </div>

            {/* Result provenance */}
            {resultJobId && (
              <p className="text-[10px] text-foreground-muted mt-3 border-t border-border pt-2">
                <Check size={10} className="inline mr-1" />
                Result from job {resultJobId.slice(0, 8)}... — revisitable via Jobs page
              </p>
            )}
          </div>

          {/* Aggregate equity curve (SVG line chart) */}
          {result.aggregate_equity.length > 1 && (
            <div className="bg-background-card rounded-lg border border-gray-200 p-4">
              <div className="flex items-center gap-2 mb-2">
                <h3 className="text-sm font-medium">Aggregate Equity Curve</h3>
                {result.is_mixed_timeframe && (
                  <InfoTip text="Bar-indexed, not time-aligned. Each point represents one bar from the longest equity curve. Shorter curves are extended at their final value." />
                )}
              </div>
              <EquityCurveChart
                data={result.aggregate_equity}
                currency={currency}
              />
            </div>
          )}

          {/* Per-bot breakdown */}
          <div className="bg-background-card rounded-lg border border-gray-200 p-4">
            <h3 className="text-sm font-medium mb-3">
              Per-Bot Breakdown
              <span className="text-xs text-foreground-muted font-normal ml-2">
                ({result.per_bot.filter((b) => !b.error).length} successful, {result.per_bot.filter((b) => b.error).length} failed)
              </span>
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-foreground-muted border-b border-border">
                    <SortHeader field="strategy_id" label="Strategy" align="text-left" />
                    <SortHeader field="instrument" label="Instrument" align="text-left" />
                    <SortHeader field="timeframe" label="TF" align="text-left" />
                    <SortHeader field="total_trades" label="Trades" />
                    <SortHeader field="net_profit" label="Net Profit" />
                    <SortHeader field="win_rate" label="Win %" />
                    <SortHeader field="sharpe_ratio" label="Sharpe" />
                    <SortHeader field="max_drawdown_pct" label="Max DD" />
                    <th className="pb-2 text-right">Equity</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedBots.map((bot, i) => (
                    <tr key={i} className="border-b border-border/50 last:border-0">
                      {bot.error ? (
                        <>
                          <td className="py-1.5 pr-3 font-mono text-xs">{bot.strategy_id}</td>
                          <td className="py-1.5 pr-3">{bot.instrument}</td>
                          <td className="py-1.5 pr-3">{bot.timeframe}</td>
                          <td colSpan={6} className="py-1.5 text-danger text-xs">
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
                            {formatPnl(bot.net_profit ?? 0, currency)}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">
                            {bot.win_rate != null ? `${(bot.win_rate * 100).toFixed(0)}%` : "—"}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">
                            {bot.sharpe_ratio != null ? bot.sharpe_ratio.toFixed(2) : "—"}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums text-danger">
                            {bot.max_drawdown_pct != null
                              ? `${bot.max_drawdown_pct.toFixed(1)}%`
                              : "—"}
                          </td>
                          <td className="py-1.5 text-right">
                            {bot.equity_curve && bot.equity_curve.length > 1 ? (
                              <MiniSparkline
                                data={bot.equity_curve}
                                positive={(bot.net_profit ?? 0) >= 0}
                              />
                            ) : (
                              <span className="text-xs text-foreground-muted">—</span>
                            )}
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
