"use client";

import { useState, useCallback, useRef, useMemo } from "react";
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

const INSTRUMENTS = [
  // FX Majors
  "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
  // FX Crosses
  "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "EURAUD",
  // Metals
  "XAUUSD", "XAGUSD",
  // Oil
  "BCOUSD", "WTIUSD",
  // Indices
  "US500", "US100", "UK100", "DE40", "JP225",
  // Crypto
  "BTCUSD", "ETHUSD",
];
const TIMEFRAMES = ["M15", "M30", "H1", "H4", "D1"];

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
        >
          {INSTRUMENTS.map((i) => (
            <option key={i} value={i}>{i.slice(0, 3)}/{i.slice(3)}</option>
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
            onClick={() => liveAvailable && setChartMode("live")}
            disabled={!liveAvailable}
            title={!liveAvailable ? "IG demo credentials not configured" : "Live data from IG"}
            className={`px-1.5 py-0.5 text-[10px] rounded transition ${
              chartMode === "live"
                ? "bg-green-600 text-white font-medium"
                : liveAvailable
                  ? "text-foreground-muted hover:text-foreground"
                  : "text-foreground-muted/40 cursor-not-allowed"
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
          className="p-1 rounded text-foreground-muted hover:text-foreground hover:bg-background-muted transition"
          data-testid="chart-reset"
        >
          <RotateCcw size={12} />
        </button>
        <button
          onClick={() => tradingChartRef.current?.fitToData()}
          title="Fit to data — show all available bars"
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
              href="/bots"
              className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] text-foreground-muted hover:text-primary rounded hover:bg-background-muted transition"
              title="Bots"
              data-testid="link-bots"
            >
              <Bot size={10} />
              Bots
            </a>
          </div>
        )}

        {/* Help */}
        <div className="relative">
          <button
            onClick={() => setShowHelp(!showHelp)}
            className="p-1 rounded text-foreground-muted hover:text-foreground hover:bg-background-muted transition"
            title="Chart help"
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
