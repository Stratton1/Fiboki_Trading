"use client";

import { useState, useCallback, useRef, useMemo, useEffect } from "react";
import TradingChart from "./TradingChart";
import type { TradingChartHandle } from "./TradingChart";
import { useMarketData, useLiveStatus } from "@/lib/hooks/use-market-data";
import type { ChartMode } from "@/lib/hooks/use-market-data";
import { useDrawings } from "@/lib/hooks/use-drawings";
import {
  AlertTriangle,
  Database,
  Loader2,
  Clock,
  MousePointer,
  TrendingUp,
  Minus,
  MoveRight,
  GitBranch,
  Columns3,
  Trash2,
  Layers,
  ArrowUpDown,
  Zap,
  HelpCircle,
  ExternalLink,
  Maximize2,
  RotateCcw,
  Bot,
  SeparatorVertical,
} from "lucide-react";
import { MARKET_SESSIONS, getSessionForTimestamp } from "@/lib/sessions";
import { InfoTip } from "@/components/InfoTip";

/** Instruments grouped by asset class so the <select> renders <optgroup>s
 * instead of a flat 22-item list. Order within each group is intentional. */
const INSTRUMENT_GROUPS: Array<{ label: string; items: string[] }> = [
  { label: "FX Majors",  items: ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"] },
  { label: "FX Crosses", items: ["EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "EURAUD"] },
  { label: "Metals",     items: ["XAUUSD", "XAGUSD"] },
  { label: "Oil",        items: ["BCOUSD", "WTIUSD"] },
  { label: "Indices",    items: ["US500", "US100", "UK100", "DE40", "JP225"] },
  { label: "Crypto",     items: ["BTCUSD", "ETHUSD"] },
];

const TIMEFRAMES = ["M15", "M30", "H1", "H4", "D1"];

/** Price precision for the y-axis formatter — JPY pairs quote to 3dp, all
 * other FX/indices/metals to 5dp, crypto to 2dp. */
function pricePrecisionFor(instrument: string): number {
  if (instrument.includes("JPY")) return 3;
  if (instrument === "BTCUSD" || instrument === "ETHUSD") return 2;
  return 5;
}

const DRAWING_TOOLS = [
  { id: null, label: "Select", icon: MousePointer, shortcut: "V" },
  { id: "straightLine", label: "Trend", icon: TrendingUp, shortcut: "T" },
  { id: "horizontalStraightLine", label: "H-Line", icon: Minus, shortcut: "H" },
  { id: "verticalStraightLine", label: "V-Line", icon: SeparatorVertical, shortcut: "X" },
  { id: "rayLine", label: "Ray", icon: MoveRight, shortcut: "R" },
  { id: "fibonacciLine", label: "Fib", icon: GitBranch, shortcut: "F" },
  { id: "parallelStraightLine", label: "Channel", icon: Columns3, shortcut: "C" },
] as const;

interface ChartCellProps {
  defaultInstrument?: string;
  defaultTimeframe?: string;
  compact?: boolean;
  onConfigChange?: (instrument: string, timeframe: string) => void;
}

export default function ChartCell({
  defaultInstrument = "EURUSD",
  defaultTimeframe = "H1",
  compact = false,
  onConfigChange,
}: ChartCellProps) {
  const [instrument, setInstrument] = useState(defaultInstrument);
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  const [ichimokuEnabled, setIchimokuEnabled] = useState(false);
  const [sessionsVisible, setSessionsVisible] = useState(false);
  const [activeDrawingTool, setActiveDrawingTool] = useState<string | null>(null);
  const [chartMode, setChartMode] = useState<ChartMode>("historical");
  const [showHelp, setShowHelp] = useState(false);

  // Chart instance ref for reset/fit operations
  const tradingChartRef = useRef<TradingChartHandle>(null);

  const { data, error, isLoading } = useMarketData(instrument, timeframe, chartMode);
  const { available: liveAvailable } = useLiveStatus();
  const {
    drawings,
    createDrawing,
    updateDrawing,
    deleteDrawing,
    clearDrawings,
  } = useDrawings(instrument, timeframe);

  // Price summary from latest candle
  const priceSummary = useMemo(() => {
    if (!data?.candles?.length) return null;
    const last = data.candles[data.candles.length - 1];
    const prev = data.candles.length > 1 ? data.candles[data.candles.length - 2] : null;
    const change = prev ? last.close - prev.close : 0;
    const changePct = prev ? (change / prev.close) * 100 : 0;
    return { last, change, changePct };
  }, [data]);

  const currentSession = useMemo(() => {
    if (!data?.candles?.length) return null;
    return getSessionForTimestamp(data.candles[data.candles.length - 1].timestamp);
  }, [data]);

  const lookupDrawingId = useCallback(
    (overlayId: string): number | null => {
      const match = overlayId.match(/^saved_(\d+)$/);
      if (match) return parseInt(match[1], 10);
      return null;
    },
    []
  );

  const handleDrawingCreated = useCallback(
    async (drawing: {
      tool_type: string;
      points: Array<{ timestamp: number; value: number }>;
    }) => {
      await createDrawing({
        instrument,
        timeframe,
        tool_type: drawing.tool_type,
        points: drawing.points,
      });
      setActiveDrawingTool(null);
    },
    [createDrawing, instrument, timeframe]
  );

  const updateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleDrawingUpdated = useCallback(
    (overlayId: string, points: Array<{ timestamp: number; value: number }>) => {
      if (updateTimerRef.current) clearTimeout(updateTimerRef.current);
      updateTimerRef.current = setTimeout(async () => {
        const drawingId = lookupDrawingId(overlayId);
        if (drawingId !== null) {
          await updateDrawing(drawingId, { points });
        }
      }, 300);
    },
    [lookupDrawingId, updateDrawing]
  );

  const handleDrawingRemoved = useCallback(
    async (overlayId: string) => {
      const drawingId = lookupDrawingId(overlayId);
      if (drawingId !== null) {
        await deleteDrawing(drawingId);
      }
    },
    [lookupDrawingId, deleteDrawing]
  );

  const handleInstrumentChange = (v: string) => {
    setInstrument(v);
    onConfigChange?.(v, timeframe);
  };
  const handleTimeframeChange = (v: string) => {
    setTimeframe(v);
    onConfigChange?.(instrument, v);
  };

  // ── Keyboard shortcuts for drawing tools ───────────────────
  // Bind V/T/H/X/R/F/C globally for the active chart. We previously surfaced
  // these in tooltips but never bound them, which made the hint misleading.
  // Skip when the user is typing in an input, select, or contentEditable
  // surface so we don't hijack typing in the instrument <select>.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable
      )) return;
      const key = e.key.toUpperCase();
      const match = DRAWING_TOOLS.find((t) => t.shortcut === key);
      if (!match) return;
      e.preventDefault();
      setActiveDrawingTool((cur) => (cur === match.id ? null : match.id));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Close the help popup on Escape or click-outside.
  const helpRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!showHelp) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setShowHelp(false);
    }
    function onClickOutside(e: MouseEvent) {
      if (helpRef.current && !helpRef.current.contains(e.target as Node)) {
        setShowHelp(false);
      }
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [showHelp]);

  // Inline message when an operator clicks Live with live unavailable.
  const [liveDisabledHint, setLiveDisabledHint] = useState(false);
  useEffect(() => {
    if (!liveDisabledHint) return;
    const id = window.setTimeout(() => setLiveDisabledHint(false), 3000);
    return () => window.clearTimeout(id);
  }, [liveDisabledHint]);

  const precision = pricePrecisionFor(instrument);

  return (
    <div className="flex flex-col h-full border border-border rounded-lg overflow-hidden bg-background-card" data-testid="chart-cell">
      {/* ── Header / Context Bar ─────────────────────────────── */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border bg-background" data-testid="chart-header">
        {/* Instrument + Timeframe */}
        <select
          value={instrument}
          onChange={(e) => handleInstrumentChange(e.target.value)}
          className="bg-background border border-border rounded px-1.5 py-0.5 text-xs font-medium"
          data-testid="instrument-select"
          aria-label="Select instrument"
        >
          {INSTRUMENT_GROUPS.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.items.map((i) => (
                <option key={i} value={i}>{i.slice(0, 3)}/{i.slice(3)}</option>
              ))}
            </optgroup>
          ))}
        </select>

        {/* Timeframe buttons */}
        <div className="flex gap-0.5" data-testid="timeframe-buttons">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => handleTimeframeChange(tf)}
              className={`px-1.5 py-0.5 text-[11px] rounded transition ${
                timeframe === tf
                  ? "bg-primary text-white font-medium"
                  : "text-foreground-muted hover:text-foreground hover:bg-background-muted"
              }`}
              data-testid={`tf-${tf}`}
            >
              {tf}
            </button>
          ))}
        </div>

        {/* Separator */}
        <div className="w-px h-4 bg-border" />

        {/* Price readout */}
        {priceSummary && (
          <div className="flex items-center gap-1.5 text-xs" data-testid="price-readout">
            <span className="font-mono font-semibold text-foreground">
              {priceSummary.last.close.toFixed(instrument.includes("JPY") ? 3 : 5)}
            </span>
            <span className={`font-mono text-[11px] ${priceSummary.change >= 0 ? "text-green-600" : "text-red-500"}`}>
              {priceSummary.change >= 0 ? "+" : ""}
              {priceSummary.changePct.toFixed(2)}%
            </span>
          </div>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* OHLC of latest bar */}
        {priceSummary && !compact && (
          <div className="hidden sm:flex items-center gap-2 text-[10px] text-foreground-muted font-mono" data-testid="ohlc-readout">
            <span>O {priceSummary.last.open.toFixed(instrument.includes("JPY") ? 3 : 5)}</span>
            <span>H {priceSummary.last.high.toFixed(instrument.includes("JPY") ? 3 : 5)}</span>
            <span>L {priceSummary.last.low.toFixed(instrument.includes("JPY") ? 3 : 5)}</span>
            <span>C {priceSummary.last.close.toFixed(instrument.includes("JPY") ? 3 : 5)}</span>
          </div>
        )}

        {/* Bar count + session */}
        {data && (
          <div className="flex items-center gap-1.5 text-[10px] text-foreground-muted">
            <Database size={9} />
            <span>{data.total_bars?.toLocaleString() ?? "?"}</span>
            {currentSession && (
              <>
                <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: currentSession.color.replace("0.06", "0.5") }} />
                <span>{currentSession.name}</span>
              </>
            )}
          </div>
        )}

        {/* Mode toggle */}
        <div className="flex gap-0.5 bg-background border border-border rounded p-0.5" data-testid="mode-toggle">
          <button
            onClick={() => setChartMode("historical")}
            className={`px-1.5 py-0.5 text-[10px] rounded transition ${
              chartMode === "historical"
                ? "bg-primary text-white font-medium"
                : "text-foreground-muted hover:text-foreground"
            }`}
          >
            Hist
          </button>
          <button
            onClick={() => {
              if (liveAvailable) setChartMode("live");
              else setLiveDisabledHint(true);
            }}
            aria-disabled={!liveAvailable}
            aria-label={liveAvailable ? "Switch to live data" : "Live data unavailable — IG demo credentials not configured"}
            title={!liveAvailable ? "IG demo credentials not configured" : "Live data from IG"}
            className={`px-1.5 py-0.5 text-[10px] rounded transition ${
              chartMode === "live"
                ? "bg-green-600 text-white font-medium"
                : liveAvailable
                  ? "text-foreground-muted hover:text-foreground"
                  : "text-foreground-muted/60 hover:text-foreground-muted cursor-help"
            }`}
          >
            <span className="flex items-center gap-0.5">
              {chartMode === "live" && <Zap size={8} />}
              Live
            </span>
          </button>
        </div>
      </div>

      {/* ── Drawing Tools + Overlay Controls ──────────────────── */}
      <div className="flex items-center gap-1 px-2 py-1 border-b border-border bg-background text-xs" data-testid="drawing-toolbar">
        {/* Drawing tools */}
        {DRAWING_TOOLS.map((tool) => {
          const Icon = tool.icon;
          const isActive = activeDrawingTool === tool.id;
          return (
            <button
              key={tool.id ?? "pointer"}
              onClick={() => setActiveDrawingTool(isActive && tool.id !== null ? null : tool.id)}
              title={`${tool.label} (${tool.shortcut})`}
              aria-label={`${tool.label} drawing tool, keyboard shortcut ${tool.shortcut}`}
              aria-pressed={isActive}
              className={`p-1 rounded transition ${
                isActive
                  ? "bg-primary text-white"
                  : "text-foreground-muted hover:text-foreground hover:bg-background-muted"
              }`}
              data-testid={`draw-${tool.id ?? "pointer"}`}
            >
              <Icon size={13} />
            </button>
          );
        })}

        {drawings.length > 0 && (
          <>
            <div className="w-px h-4 bg-border mx-0.5" />
            <button
              onClick={() => clearDrawings()}
              title="Clear all drawings"
              aria-label={`Clear all ${drawings.length} drawing${drawings.length === 1 ? "" : "s"}`}
              className="p-1 rounded text-foreground-muted hover:text-red-500 hover:bg-background-muted transition"
              data-testid="draw-clear"
            >
              <Trash2 size={12} />
            </button>
            <span className="text-[10px] text-foreground-muted">{drawings.length}</span>
          </>
        )}

        {/* Separator */}
        <div className="w-px h-4 bg-border mx-1" />

        {/* Overlay toggles */}
        <button
          onClick={() => setIchimokuEnabled(!ichimokuEnabled)}
          className={`flex items-center gap-1 px-1.5 py-0.5 text-[11px] rounded transition ${
            ichimokuEnabled
              ? "bg-blue-100 text-blue-700 font-medium"
              : "text-foreground-muted hover:text-foreground hover:bg-background-muted"
          }`}
          data-testid="toggle-ichimoku"
        >
          <Layers size={11} />
          Ichimoku
          <InfoTip text="Ichimoku Cloud: Tenkan (blue), Kijun (orange), Senkou A/B (green/red cloud), Chikou (purple). Core indicator for all Fiboki strategies." />
        </button>

        <button
          onClick={() => setSessionsVisible(!sessionsVisible)}
          className={`flex items-center gap-1 px-1.5 py-0.5 text-[11px] rounded transition ${
            sessionsVisible
              ? "bg-amber-100 text-amber-700 font-medium"
              : "text-foreground-muted hover:text-foreground hover:bg-background-muted"
          }`}
          data-testid="toggle-sessions"
        >
          <Clock size={11} />
          Sessions
          <InfoTip text="Market sessions: Asian (00-08 UTC), London (08-12), London-NY overlap (12-16), New York (16-21). Best setups often occur during London-NY overlap." />
        </button>

        {/* Separator */}
        <div className="w-px h-4 bg-border mx-0.5" />

        {/* Reset / Fit controls */}
        <button
          onClick={() => tradingChartRef.current?.resetView()}
          title="Reset view — restore default zoom and pan"
          aria-label="Reset chart view"
          className="p-1 rounded text-foreground-muted hover:text-foreground hover:bg-background-muted transition"
          data-testid="chart-reset"
        >
          <RotateCcw size={12} />
        </button>
        <button
          onClick={() => tradingChartRef.current?.fitToData()}
          title="Fit to data — show all available bars"
          aria-label="Fit chart to all available bars"
          className="p-1 rounded text-foreground-muted hover:text-foreground hover:bg-background-muted transition"
          data-testid="chart-fit"
        >
          <Maximize2 size={12} />
        </button>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Workflow links */}
        {!compact && (
          <div className="flex items-center gap-1">
            <a
              href={`/backtests?instrument=${instrument}`}
              className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] text-foreground-muted hover:text-primary rounded hover:bg-background-muted transition"
              title={`Backtest ${instrument}`}
              data-testid="link-backtest"
            >
              <ArrowUpDown size={10} />
              Backtest
            </a>
            <a
              href="/research"
              className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] text-foreground-muted hover:text-primary rounded hover:bg-background-muted transition"
              title="Open Research"
              data-testid="link-research"
            >
              <ExternalLink size={10} />
              Research
            </a>
            <a
              href={`/bots?instrument=${instrument}`}
              className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] text-foreground-muted hover:text-primary rounded hover:bg-background-muted transition"
              title={`Bots filtered to ${instrument}`}
              data-testid="link-bots"
            >
              <Bot size={10} />
              Bots
            </a>
          </div>
        )}

        {/* Help */}
        <div className="relative" ref={helpRef}>
          <button
            onClick={() => setShowHelp(!showHelp)}
            className="p-1 rounded text-foreground-muted hover:text-foreground hover:bg-background-muted transition"
            title="Chart help"
            aria-label="Chart help"
            aria-expanded={showHelp}
            data-testid="chart-help-btn"
          >
            <HelpCircle size={12} />
          </button>
          {showHelp && (
            <div className="absolute right-0 top-full mt-1 z-50 w-64 bg-background-card border border-border rounded-lg shadow-lg p-3 text-xs text-foreground" data-testid="chart-help-panel">
              <h4 className="font-medium mb-2">Chart Controls</h4>
              <ul className="space-y-1 text-foreground-muted">
                <li><strong>Scroll</strong> — pan chart left/right</li>
                <li><strong>Pinch/Ctrl+Scroll</strong> — zoom in/out</li>
                <li><strong>Drawing tools</strong> — click tool, then click on chart to draw</li>
                <li><strong>Ichimoku</strong> — toggles Tenkan, Kijun, Senkou A/B, Chikou lines</li>
                <li><strong>Sessions</strong> — shows Asian/London/NY session bands</li>
                <li><strong>Hist/Live</strong> — historical data or IG live feed</li>
              </ul>
              <div className="mt-2 pt-2 border-t border-border">
                <p className="text-foreground-muted">Drawings are auto-saved per instrument/timeframe pair.</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Session legend (when visible) ─────────────────────── */}
      {sessionsVisible && (
        <div className="flex items-center gap-3 px-2 py-1 border-b border-border text-[10px]" data-testid="session-legend">
          {MARKET_SESSIONS.map((s) => {
            const isCurrent = currentSession?.name === s.name;
            return (
              <span key={s.name} className={`flex items-center gap-1 ${isCurrent ? "font-bold text-foreground" : "text-foreground-muted"}`}>
                <span className="w-2 h-2 rounded-sm inline-block" style={{ backgroundColor: s.color.replace("0.06", "0.4") }} />
                {s.name}
                {isCurrent && " *"}
              </span>
            );
          })}
        </div>
      )}

      {/* ── Inline status banner ──────────────────────────────── */}
      {liveDisabledHint && (
        <div
          role="status"
          className="px-3 py-1 text-[11px] bg-amber-50 text-amber-800 border-b border-amber-200"
          data-testid="live-disabled-hint"
        >
          Live data is unavailable — set IG demo credentials in Settings to enable.
        </div>
      )}

      {/* ── Chart Area ────────────────────────────────────────── */}
      <div className="flex-1 min-h-0">
        {error ? (
          <div className="flex flex-col items-center justify-center h-full gap-2" data-testid="chart-error">
            <AlertTriangle size={20} className="text-danger" />
            <p className="text-xs text-danger font-medium">Failed to load chart data</p>
            <p className="text-[10px] text-foreground-muted max-w-xs text-center">
              Check that the backend is running and {instrument} data is available for {timeframe}.
            </p>
          </div>
        ) : isLoading ? (
          <div className="flex flex-col items-center justify-center h-full gap-2" data-testid="chart-loading">
            <Loader2 size={20} className="text-primary animate-spin" />
            <p className="text-xs text-foreground-muted">Loading {instrument} {timeframe}...</p>
          </div>
        ) : !data?.candles?.length ? (
          <div className="flex flex-col items-center justify-center h-full gap-2" data-testid="chart-empty">
            <Database size={20} className="text-foreground-muted/40" />
            <p className="text-xs text-foreground-muted font-medium">No data available</p>
            <p className="text-[10px] text-foreground-muted">
              No candles found for {instrument} on {timeframe}. Try a different instrument or timeframe.
            </p>
          </div>
        ) : (
          <TradingChart
            ref={tradingChartRef}
            data={data}
            timeframe={timeframe}
            precision={precision}
            ichimokuEnabled={ichimokuEnabled}
            activeDrawingTool={activeDrawingTool}
            savedDrawings={drawings}
            onDrawingCreated={handleDrawingCreated}
            onDrawingUpdated={handleDrawingUpdated}
            onDrawingRemoved={handleDrawingRemoved}
          />
        )}
      </div>
    </div>
  );
}
