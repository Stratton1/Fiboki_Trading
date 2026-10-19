"use client";

import {
  BellPlus,
  Eye,
  EyeOff,
  Lock,
  LockOpen,
  MousePointer2,
  PanelRight,
  Redo2,
  Table2,
  Ruler,
  Trash2,
  Undo2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

const PHONE = "(max-width: 639px)";
function isPhone(): boolean {
  return window.matchMedia(PHONE).matches;
}
function subscribePhone(listener: () => void): () => void {
  const query = window.matchMedia(PHONE);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
import { useOperator } from "@/components/shell/platform";
import { LazyPriceChart } from "@/components/LazyChart";
import { ProvenanceLabelChip } from "@/components/ProvenanceChip";
import { useToast } from "@/components/ui/Toast";
import {
  placeBacktestRows,
  placeBacktestTrades,
  placePaperTrades,
  type ChartModel,
  type PlacedTrade,
} from "@/lib/chart-model";
import { newDrawingId } from "@/lib/drawing-geometry";
import { formatFigure, formatNumber, signClass } from "@/lib/format";
import { useApi } from "@/lib/query";
import type {
  BacktestRunRow,
  BacktestTradeRow,
  BacktestTradesView,
  Drawing,
  Envelope,
  OverlayView,
  Page,
} from "@/lib/types";
import { DataWindow } from "./DataWindow";
import { TradeDetail } from "./TradeDetail";
import { ALL_LAYERS_ON, type ChartLayers, type ChartSlots, type TradeSource } from "./layers";
import type { DrawingTool } from "./drawing-primitive";
import type { ChartDrawings, PriceChartProps } from "./PriceChart";
import { formatBarTime, TIME_ZONES, type TimeZoneId } from "./time";
import { useDrawings, type SaveStatus } from "./useDrawings";
import { ApiError, apiFetch } from "@/lib/api";
import type { PriceAlertsView } from "@/lib/chart-contract";
import "./workbench.css";

/**
 * The chart workstation's tools, loaded with the chart engine and never in a
 * route's first-load JavaScript (ChartWorkstation imports this lazily):
 *
 *  - the trades source toggle (paper, backtest, both) and the backtest RUN
 *    picker (GET /api/markets/backtests/{symbol}), whose trades are read from
 *    GET /api/markets/backtests/{symbol}/{run}/trades for the chart's window;
 *  - the drawing tool rail with keyboard shortcuts; drawings persist per
 *    operator and symbol (useDrawings) and are annotations only;
 *  - the trades table, synced both ways with the chart: a row brings its
 *    trade into view and selects it; a click on a trade selects its row.
 *
 * Every trade figure is the API's. The browser places them on bars; it never
 * computes a P&L, a stop or a target.
 */

export type { TradeSource };

type ChartBase = Omit<
  PriceChartProps,
  "trades" | "tradeLayers" | "selectedTrade" | "focusTrade" | "onTradeClick" | "drawings"
>;

export interface ChartWorkbenchProps {
  symbol: string;
  tf: string;
  /** Trading hours from the instrument registry ("24/7" for spot crypto), or null before the read. */
  hours: string | null;
  chart: ChartBase;
  run: string | null;
  onRun: (run: string | null) => void;
  /**
   * Replay: the run's trades as they stood at the replay cursor (the API's
   * answer, exits withheld while open). When set, the stored trades are not read.
   */
  replay?: { rows: BacktestTradeRow[]; runId: string | null } | null;
  /** The overlay payload (the data window reads it); null while loading. */
  overlays?: OverlayView | null;
  /** The bar the crosshair or keyboard cursor is on, or null (the latest). */
  readoutIndex?: number | null;
  timeZone?: TimeZoneId;
  /** The chart's display layers (drawings, trades, stops, events, …); all on by default. */
  chartLayers?: ChartLayers;
  onChartLayers?: (update: (layers: ChartLayers) => ChartLayers) => void;
  /** Which trades are drawn; owned by the workstation so a template can set it. */
  tradeSource?: TradeSource;
  onTradeSource?: (source: TradeSource) => void;
  /** The workstation's tool row and stamp line; the bar renders inline without them. */
  slots?: ChartSlots;
}

type PanelTab = "trades" | "data";

interface ToolSpec {
  tool: DrawingTool;
  label: string;
  /** KeyboardEvent.code with Alt. */
  code: string;
  keys: string;
  icon: ReactNode;
}

const ICON = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.4, "aria-hidden": true } as const;

export const TOOLS: ToolSpec[] = [
  {
    tool: "select",
    label: "Select and move",
    code: "KeyS",
    keys: "Alt S · Esc",
    icon: <MousePointer2 size={16} aria-hidden="true" />,
  },
  {
    tool: "trend",
    label: "Trend line",
    code: "KeyT",
    keys: "Alt T",
    icon: (
      <svg {...ICON}>
        <path d="M2.5 13.5 13.5 2.5" />
        <circle cx="2.5" cy="13.5" r="1.4" fill="currentColor" />
        <circle cx="13.5" cy="2.5" r="1.4" fill="currentColor" />
      </svg>
    ),
  },
  {
    tool: "ray",
    label: "Ray",
    code: "KeyR",
    keys: "Alt R",
    icon: (
      <svg {...ICON}>
        <path d="M2.5 12.5 14.5 3.5" />
        <circle cx="2.5" cy="12.5" r="1.4" fill="currentColor" />
        <path d="M11.5 3.2 14.5 3.5 13.6 6.3" />
      </svg>
    ),
  },
  {
    tool: "hline",
    label: "Horizontal level",
    code: "KeyH",
    keys: "Alt H",
    icon: (
      <svg {...ICON}>
        <path d="M1 8h14" />
        <circle cx="8" cy="8" r="1.4" fill="currentColor" />
      </svg>
    ),
  },
  {
    tool: "vline",
    label: "Vertical line",
    code: "KeyV",
    keys: "Alt V",
    icon: (
      <svg {...ICON}>
        <path d="M8 1v14" />
        <circle cx="8" cy="8" r="1.4" fill="currentColor" />
      </svg>
    ),
  },
  {
    tool: "rect",
    label: "Rectangle zone",
    code: "KeyB",
    keys: "Alt B",
    icon: (
      <svg {...ICON}>
        <rect x="2.5" y="4" width="11" height="8" />
      </svg>
    ),
  },
  {
    tool: "fib",
    label: "Fibonacci retracement (drawing)",
    code: "KeyF",
    keys: "Alt F",
    icon: (
      <svg {...ICON}>
        <path d="M2 3h12M2 6h12M2 8.5h12M2 11h12M2 13.5h12" strokeWidth="1" />
        <path d="M3 13.5 13 3" strokeDasharray="1.5 1.5" />
      </svg>
    ),
  },
  {
    tool: "text",
    label: "Text note",
    code: "KeyN",
    keys: "Alt N",
    icon: (
      <svg {...ICON}>
        <path d="M3.5 3.5h9M8 3.5v9" />
      </svg>
    ),
  },
  {
    tool: "measure",
    label: "Measure (price, time, percent; not saved)",
    code: "KeyM",
    keys: "Alt M",
    icon: <Ruler size={16} aria-hidden="true" />,
  },
];

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (target as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "submit", "reset"].includes(type);
  }
  return false;
}

function inDialog(): boolean {
  return (
    document.querySelector(
      '[role="dialog"][data-open]:not([data-ending-style]), [role="alertdialog"][data-open]:not([data-ending-style])',
    ) !== null
  );
}

function statusText(status: SaveStatus, count: number): string {
  switch (status.kind) {
    case "loading":
      return "reading drawings…";
    case "saving":
      return "saving…";
    case "saved":
      return `${count} drawing${count === 1 ? "" : "s"} · saved v${status.version}`;
    default:
      return status.message;
  }
}

function runLabel(run: BacktestRunRow): string {
  const date = run.created_at ? run.created_at.slice(0, 10) : "undated";
  const n = run.trades_on_symbol.value;
  const flags = [
    run.kind === "walkforward_fold" ? run.label.replace("walkforward_fold_", "WF fold ") : null,
    !run.engine_current || run.superseded ? "stale engine" : null,
  ].filter(Boolean);
  return `${run.strategy_id} · ${date} · gate ${run.gate_verdict.replace("_", " ")} · ${
    n === null ? "?" : n.toLocaleString("en-GB")
  } trades${flags.length ? ` · ${flags.join(", ")}` : ""}`;
}

export default function ChartWorkbench({
  symbol,
  tf,
  hours,
  chart,
  run,
  onRun,
  replay = null,
  overlays = null,
  readoutIndex = null,
  timeZone = "UTC",
  chartLayers = ALL_LAYERS_ON,
  onChartLayers,
  tradeSource,
  onTradeSource,
  slots: givenSlots,
}: ChartWorkbenchProps) {
  // On a phone the trades bar stays under the chart (the chart comes first
  // there); from 640 px up it joins the workstation's single tool row.
  const phone = useSyncExternalStore(subscribePhone, isPhone, () => false);
  const slots = phone ? undefined : givenSlots;
  const notify = useToast();
  const operator = useOperator();
  const model: ChartModel = chart.model;
  const enc = encodeURIComponent(symbol);
  const [ownSource, setOwnSource] = useState<TradeSource>("both");
  const source = tradeSource ?? ownSource;
  const setSource = onTradeSource ?? setOwnSource;
  // Selections belong to one symbol and timeframe: a switch drops them.
  const scopeKey = `${symbol}|${tf}`;
  const [tradePick, setTradePick] = useState<{ scope: string; key: string | null }>({ scope: "", key: null });
  const selectedTrade = tradePick.scope === scopeKey ? tradePick.key : null;
  const setSelectedTrade = useCallback(
    (key: string | null) => setTradePick({ scope: scopeKey, key }),
    [scopeKey],
  );
  const [focusTrade, setFocusTrade] = useState<{ key: string; nonce: number } | null>(null);
  const nonce = useRef(0);
  const [panel, setPanel] = useState<PanelTab | null>("trades");
  const panelOpen = panel !== null;

  // ---------------------------------------------------------- backtest runs
  const runs = useApi<Page<BacktestRunRow>>(`/api/markets/backtests/${enc}?timeframe=${tf}`);
  const first = model.bars[0]?.t ?? null;
  const last = model.bars[model.bars.length - 1]?.t ?? null;
  const tradesPath =
    run && first && last && replay === null
      ? `/api/markets/backtests/${enc}/${encodeURIComponent(run)}/trades?timeframe=${tf}&from=${encodeURIComponent(first)}&to=${encodeURIComponent(last)}`
      : null;
  const btTrades = useApi<Envelope<BacktestTradesView>>(tradesPath);
  const btView = btTrades.status === "success" ? btTrades.data.data : null;

  const placedBt = useMemo(
    () =>
      replay
        ? replay.runId
          ? placeBacktestRows(model.times, replay.runId, replay.rows)
          : { trades: [], unplaced: 0 }
        : placeBacktestTrades(model.times, run ? btView : null),
    [model.times, btView, run, replay],
  );
  const placedPaper = useMemo(() => placePaperTrades(model), [model]);
  // The legend's Levels toggle governs every stop and target, paper and backtest.
  const showLevels = chartLayers.stops;
  const allTrades = useMemo(
    () =>
      [...placedPaper, ...placedBt.trades]
        .map((t) => (showLevels ? t : { ...t, segments: [] }))
        .sort((a, b) => a.entryIndex - b.entryIndex || a.key.localeCompare(b.key)),
    [placedPaper, placedBt.trades, showLevels],
  );
  const showPaper = source !== "backtest";
  const showBacktest = source !== "paper";
  // The Trades layer hides trade markers and connectors on the chart; the
  // table keeps listing them (hiding a layer never hides a figure).
  const drawPaper = showPaper && chartLayers.trades;
  const drawBacktest = showBacktest && chartLayers.trades;
  const tableTrades = allTrades.filter((t) => (t.source === "paper" ? showPaper : showBacktest));

  const selectTrade = useCallback(
    (key: string, scroll: boolean) => {
      setSelectedTrade(key);
      if (scroll) {
        nonce.current += 1;
        setFocusTrade({ key, nonce: nonce.current });
      }
    },
    [setSelectedTrade],
  );

  // ----------------------------------------------------------------- drawings
  const drawings = useDrawings(symbol, notify);
  // ------------------------------------------------------------ price alerts
  const alertsPath = `/api/alerts/price?symbol=${enc}`;
  const alertsRead = useApi<Envelope<PriceAlertsView>>(alertsPath);
  const alertData = alertsRead.status === "success" ? alertsRead.data.data : null;
  const alertLines = useMemo(
    () =>
      (alertData?.alerts ?? []).map((a) => ({
        id: a.alert_id,
        price: a.price,
        label: `ALERT ${a.condition === "cross_above" ? "↑" : "↓"}`,
      })),
    [alertData],
  );
  const [alertBusy, setAlertBusy] = useState(false);
  const createAlert = useCallback(
    async (price: number, drawingId: string, origin: "level" | "zone_top" | "zone_bottom") => {
      setAlertBusy(true);
      try {
        const env = await apiFetch<Envelope<PriceAlertsView>>("/api/alerts/price", {
          method: "POST",
          body: JSON.stringify({ symbol, price, timeframe: tf, drawing_id: drawingId, origin }),
        });
        const made = env.data.alerts[env.data.alerts.length - 1];
        notify(
          made
            ? `Alert stored: ${symbol} ${made.condition === "cross_above" ? "crosses above" : "crosses below"} ${formatNumber(
                made.price,
                "",
                { decimals: chart.priceDecimals },
              )} (decided against the close ${formatNumber(made.reference_close, "", {
                decimals: chart.priceDecimals,
              })}). ${env.data.evaluated ? "" : "Not watched yet: no evaluator is wired."}`
            : "Alert stored.",
        );
      } catch (error) {
        notify(error instanceof ApiError ? `Alert not created: ${error.message}` : "Alert not created.");
      } finally {
        setAlertBusy(false);
        alertsRead.reload();
      }
    },
    [symbol, tf, notify, chart.priceDecimals, alertsRead],
  );
  const [tool, setTool] = useState<DrawingTool>("select");
  const [drawingPick, setDrawingPick] = useState<{ scope: string; id: string | null }>({ scope: "", id: null });
  const selectedId = drawingPick.scope === scopeKey ? drawingPick.id : null;
  const setSelectedId = useCallback(
    (id: string | null) => setDrawingPick({ scope: scopeKey, id }),
    [scopeKey],
  );
  const [lockedAll, setLockedAll] = useState(false);
  // The Drawings layer: the rail's eye button and Alt Shift H toggle it.
  const hiddenAll = !chartLayers.drawings;
  const setLayers = useCallback(
    (update: (layers: ChartLayers) => ChartLayers) => onChartLayers?.(update),
    [onChartLayers],
  );
  const setHiddenAll = useCallback(
    (update: (hidden: boolean) => boolean) => setLayers((l) => ({ ...l, drawings: !update(!l.drawings) })),
    [setLayers],
  );
  const [scopeToTf, setScopeToTf] = useState(false);
  const [measured, setMeasured] = useState<string | null>(null);
  const [note, setNote] = useState<{
    at: { t: string; price: number };
    x: number;
    y: number;
    editId: string | null;
    text: string;
  } | null>(null);
  const roleAllows = operator === null ? true : operator.can_draw === true;
  const canEdit = drawings.canEdit && roleAllows;
  const visible = useMemo(
    () => drawings.all.filter((d) => d.timeframe === null || d.timeframe === undefined || d.timeframe === tf),
    [drawings.all, tf],
  );
  const others = useMemo(
    () =>
      drawings.others.filter(
        (o) => o.drawing.timeframe === null || o.drawing.timeframe === undefined || o.drawing.timeframe === tf,
      ),
    [drawings.others, tf],
  );
  const selected = drawings.all.find((d) => d.id === selectedId) ?? null;

  const chooseTool = useCallback(
    (next: DrawingTool) => {
      if (next !== "select" && next !== "measure" && !canEdit) return;
      setTool(next);
    },
    [canEdit],
  );

  const deleteSelected = useCallback(() => {
    if (!selected || !canEdit || selected.locked || lockedAll) return;
    drawings.commit(drawings.all.filter((d) => d.id !== selected.id));
    setSelectedId(null);
  }, [selected, canEdit, lockedAll, drawings, setSelectedId]);

  const patchSelected = useCallback(
    (patch: Partial<Drawing>) => {
      if (!selected || !canEdit) return;
      drawings.commit(drawings.all.map((d) => (d.id === selected.id ? { ...d, ...patch } : d)));
    },
    [selected, canEdit, drawings],
  );

  const toggleLock = useCallback(() => {
    if (selected && canEdit) patchSelected({ locked: !selected.locked });
    else setLockedAll((v) => !v);
  }, [selected, canEdit, patchSelected]);

  const chartDrawings: ChartDrawings = useMemo(
    () => ({
      visible,
      all: drawings.all,
      others,
      selectedId,
      hiddenAll,
      controls: { tool, canEdit, lockedAll, scope: scopeToTf ? tf : null },
      onCommit: (next, id) => {
        drawings.commit(next);
        setSelectedId(id);
      },
      onSelect: setSelectedId,
      onRequestText: (at, screen, editId) => {
        const existing = editId ? drawings.all.find((d) => d.id === editId) : null;
        setNote({ at, x: screen.x, y: screen.y, editId, text: existing?.text ?? "" });
      },
      onToolDone: () => setTool("select"),
      onMeasure: setMeasured,
    }),
    [visible, drawings, others, selectedId, setSelectedId, hiddenAll, tool, canEdit, lockedAll, scopeToTf, tf],
  );

  const commitNote = useCallback(() => {
    if (!note) return;
    const text = note.text.trim().slice(0, 280);
    setNote(null);
    if (!text || !canEdit) return;
    if (note.editId) {
      drawings.commit(drawings.all.map((d) => (d.id === note.editId ? { ...d, text } : d)));
      return;
    }
    const id = newDrawingId();
    drawings.commit([
      ...drawings.all,
      {
        id,
        kind: "text",
        points: [note.at],
        timeframe: scopeToTf ? tf : null,
        text,
        locked: false,
        hidden: false,
        fib_extensions: false,
      },
    ]);
    setSelectedId(id);
  }, [note, canEdit, drawings, scopeToTf, tf, setSelectedId]);

  // ---------------------------------------------------------------- keyboard
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (typing(event.target) || inDialog()) return;
      const mod = event.metaKey || event.ctrlKey;
      if (mod && !event.altKey && (event.key.toLowerCase() === "z" || event.key.toLowerCase() === "y")) {
        const redo = event.key.toLowerCase() === "y" || event.shiftKey;
        event.preventDefault();
        if (redo) drawings.redo();
        else drawings.undo();
        return;
      }
      if (event.altKey && !mod) {
        if (!event.shiftKey && event.code === "KeyW") {
          event.preventDefault();
          setPanel((v) => (v === "data" ? null : "data"));
          return;
        }
        if (event.shiftKey && event.code === "KeyL") {
          event.preventDefault();
          toggleLock();
          return;
        }
        if (event.shiftKey && event.code === "KeyH") {
          event.preventDefault();
          setHiddenAll((v) => !v);
          return;
        }
        if (event.shiftKey) return;
        const spec = TOOLS.find((t) => t.code === event.code);
        if (spec) {
          event.preventDefault();
          chooseTool(spec.tool);
        }
        return;
      }
      if (mod || event.altKey) return;
      if (event.key === "Escape") {
        if (tool !== "select") setTool("select");
        else if (selectedId) setSelectedId(null);
        return;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && selectedId) {
        event.preventDefault();
        deleteSelected();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawings, toggleLock, chooseTool, tool, selectedId, setSelectedId, deleteSelected, setHiddenAll]);

  const runsList = runs.status === "success" ? runs.data.items : [];
  const runRow = btView?.run ?? runsList.find((r) => r.run_id === run) ?? null;
  const roleReason =
    operator && !roleAllows
      ? `Signed in as ${operator.display_name} (${operator.role}). Drawing needs the operator role; the platform would refuse it.`
      : null;

  const overlayLayers = { ...chart.layers, fills: chart.layers.fills && showPaper };
  const paperCount = placedPaper.length;
  const btCount = placedBt.trades.length;

  return (
    <div
      className="cw-bench"
      data-testid="chart-workbench"
      data-panel={panelOpen ? "open" : "closed"}
      data-bar={slots?.bench ? "slot" : "inline"}
      data-tool={tool}
    >
      {slots?.bench
        ? createPortal(
            <span className="cw-bench__bar cw-bench__bar--slot" role="toolbar" aria-label="Trades on the chart">
        <fieldset className="cw-seg" data-testid="trade-source">
          <legend className="sr-only">Trades shown</legend>
          {(
            [
              ["paper", "Paper"],
              ["backtest", "Backtest"],
              ["both", "Both"],
            ] as const
          ).map(([value, text]) => (
            <label key={value} className="cw-seg__item" data-checked={source === value}>
              <input
                type="radio"
                name={`trade-source-${symbol}`}
                value={value}
                checked={source === value}
                onChange={() => setSource(value)}
              />
              {text}
            </label>
          ))}
        </fieldset>
        <label className="cw-run">
          <span className="cw-run__label">Run</span>
          <select
            className="cw-run__select"
            aria-label="Backtest run"
            data-testid="run-picker"
            value={run ?? ""}
            disabled={runs.status !== "success" || runsList.length === 0}
            onChange={(e) => {
              onRun(e.target.value === "" ? null : e.target.value);
              setSelectedTrade(null);
            }}
          >
            <option value="">
              {runs.status === "loading"
                ? "reading runs…"
                : runs.status === "error"
                  ? "runs could not be read"
                  : runsList.length === 0
                    ? runs.data.source.kind === "absent"
                      ? "research ledger not configured"
                      : `no stored runs for ${symbol} ${tf}`
                    : `None (${runsList.length} run${runsList.length === 1 ? "" : "s"})`}
            </option>
            {runsList.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {runLabel(r)}
              </option>
            ))}
            {run && !runsList.some((r) => r.run_id === run) ? (
              <option value={run}>{run} (not listed for {tf})</option>
            ) : null}
          </select>
        </label>
        {run && btTrades.status === "loading" ? (
          <span className="cw__chip" role="status" data-testid="bt-loading">
            reading trades…
          </span>
        ) : null}
        {run && btTrades.status === "error" ? (
          <span className="cw__chip cw__chip--error" role="alert" data-testid="bt-error">
            trades not loaded: {btTrades.error.code}
          </span>
        ) : null}
        <button
          type="button"
          className="cw-bench__toggle"
          aria-pressed={panel === "trades"}
          aria-controls={panel === "trades" ? "cw-trades" : undefined}
          data-testid="trades-panel-toggle"
          onClick={() => setPanel((v) => (v === "trades" ? null : "trades"))}
        >
          <PanelRight size={14} aria-hidden="true" /> Trades ({tableTrades.length})
        </button>
        <button
          type="button"
          className="cw-bench__toggle"
          aria-pressed={panel === "data"}
          aria-controls={panel === "data" ? "cw-data-window" : undefined}
          data-testid="data-window-toggle"
          title="Data window: every value at the crosshair, from the platform · Alt W"
          onClick={() => setPanel((v) => (v === "data" ? null : "data"))}
        >
          <Table2 size={14} aria-hidden="true" /> Data
        </button>
            </span>,
            slots.bench,
          )
        : null}
      {slots?.stamp
        ? createPortal(
            <span className="cw-bench__status">
        {runRow ? <RunProvenance run={runRow} view={btView} /> : null}
        <span
          className="cw-bench__counts num"
          data-testid="trade-counts"
          data-paper={paperCount}
          data-backtest={btCount}
          data-backtest-unplaced={placedBt.unplaced}
          data-segments={allTrades.reduce((n, t) => n + t.segments.length, 0)}
        >
          {paperCount} paper · {run ? `${btCount} backtest` : "no run"}
          {placedBt.unplaced ? ` · ${placedBt.unplaced} outside bars` : ""}
        </span>
        {hours ? (
          <span className="cw__chip" data-testid="instrument-hours" title={`${symbol} trading hours: ${hours}`}>
            {hours}
          </span>
        ) : null}            </span>,
            slots.stamp,
          )
        : null}
      {slots?.bench ? null : (
        <div className="cw-bench__bar" role="toolbar" aria-label="Trades on the chart">
        <fieldset className="cw-seg" data-testid="trade-source">
          <legend className="sr-only">Trades shown</legend>
          {(
            [
              ["paper", "Paper"],
              ["backtest", "Backtest"],
              ["both", "Both"],
            ] as const
          ).map(([value, text]) => (
            <label key={value} className="cw-seg__item" data-checked={source === value}>
              <input
                type="radio"
                name={`trade-source-${symbol}`}
                value={value}
                checked={source === value}
                onChange={() => setSource(value)}
              />
              {text}
            </label>
          ))}
        </fieldset>
        <label className="cw-run">
          <span className="cw-run__label">Run</span>
          <select
            className="cw-run__select"
            aria-label="Backtest run"
            data-testid="run-picker"
            value={run ?? ""}
            disabled={runs.status !== "success" || runsList.length === 0}
            onChange={(e) => {
              onRun(e.target.value === "" ? null : e.target.value);
              setSelectedTrade(null);
            }}
          >
            <option value="">
              {runs.status === "loading"
                ? "reading runs…"
                : runs.status === "error"
                  ? "runs could not be read"
                  : runsList.length === 0
                    ? runs.data.source.kind === "absent"
                      ? "research ledger not configured"
                      : `no stored runs for ${symbol} ${tf}`
                    : `None (${runsList.length} run${runsList.length === 1 ? "" : "s"})`}
            </option>
            {runsList.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {runLabel(r)}
              </option>
            ))}
            {run && !runsList.some((r) => r.run_id === run) ? (
              <option value={run}>{run} (not listed for {tf})</option>
            ) : null}
          </select>
        </label>
        {run && btTrades.status === "loading" ? (
          <span className="cw__chip" role="status" data-testid="bt-loading">
            reading trades…
          </span>
        ) : null}
        {run && btTrades.status === "error" ? (
          <span className="cw__chip cw__chip--error" role="alert" data-testid="bt-error">
            trades not loaded: {btTrades.error.code}
          </span>
        ) : null}
        <button
          type="button"
          className="cw-bench__toggle"
          aria-pressed={panel === "trades"}
          aria-controls={panel === "trades" ? "cw-trades" : undefined}
          data-testid="trades-panel-toggle"
          onClick={() => setPanel((v) => (v === "trades" ? null : "trades"))}
        >
          <PanelRight size={14} aria-hidden="true" /> Trades ({tableTrades.length})
        </button>
        <button
          type="button"
          className="cw-bench__toggle"
          aria-pressed={panel === "data"}
          aria-controls={panel === "data" ? "cw-data-window" : undefined}
          data-testid="data-window-toggle"
          title="Data window: every value at the crosshair, from the platform · Alt W"
          onClick={() => setPanel((v) => (v === "data" ? null : "data"))}
        >
          <Table2 size={14} aria-hidden="true" /> Data
        </button>
        {runRow ? <RunProvenance run={runRow} view={btView} /> : null}
        <span
          className="cw-bench__counts num"
          data-testid="trade-counts"
          data-paper={paperCount}
          data-backtest={btCount}
          data-backtest-unplaced={placedBt.unplaced}
          data-segments={allTrades.reduce((n, t) => n + t.segments.length, 0)}
        >
          {paperCount} paper · {run ? `${btCount} backtest` : "no run"}
          {placedBt.unplaced ? ` · ${placedBt.unplaced} outside bars` : ""}
        </span>
        {hours ? (
          <span className="cw__chip" data-testid="instrument-hours" title={`${symbol} trading hours: ${hours}`}>
            {hours}
          </span>
        ) : null}        </div>
      )}

      <div className="cw-bench__main">
        <DrawingRail
          tool={tool}
          onTool={chooseTool}
          canEdit={canEdit}
          roleReason={roleReason}
          selected={selected}
          lockedAll={lockedAll}
          hiddenAll={hiddenAll}
          onToggleLock={toggleLock}
          onToggleHidden={() => setHiddenAll((v) => !v)}
        />
        <div className="cw-bench__plot">
          <LazyPriceChart
            {...chart}
            layers={overlayLayers}
            trades={allTrades}
            tradeLayers={{ backtest: drawBacktest, paper: drawPaper }}
            selectedTrade={selectedTrade}
            focusTrade={focusTrade}
            onTradeClick={(key) => selectTrade(key, false)}
            drawings={chartDrawings}
            alerts={chartLayers.stops ? alertLines : []}
          />
          {note ? (
            <NoteEditor
              x={note.x}
              y={note.y}
              text={note.text}
              onText={(text) => setNote((n) => (n ? { ...n, text } : n))}
              onDone={commitNote}
              onCancel={() => setNote(null)}
            />
          ) : null}
          <p className="sr-only" role="status" data-testid="measure-readout">
            {measured ?? ""}
          </p>
          <DrawingActions
            canEdit={canEdit}
            drawings={drawings}
            selected={selected}
            lockedAll={lockedAll}
            scopeToTf={scopeToTf}
            tf={tf}
            onToggleScope={() => setScopeToTf((v) => !v)}
            onDelete={deleteSelected}
            onPatch={patchSelected}
            onAlert={(price, origin) => (selected ? void createAlert(price, selected.id, origin) : undefined)}
            alertBusy={alertBusy}
            canAlert={roleAllows}
            onClear={() => {
              drawings.clear();
              setSelectedId(null);
            }}
          />
        </div>
        {panel === "trades" ? (
          <TradesTable
            trades={tableTrades}
            selected={selectedTrade}
            onPick={(key) => selectTrade(key, true)}
            priceDecimals={chart.priceDecimals}
            stopsDetail={run ? (btView?.stops_detail ?? null) : null}
            timeZone={timeZone}
          />
        ) : panel === "data" ? (
          <div className="cw-trades" id="cw-data-window">
            <DataWindow
              symbol={symbol}
              tf={tf}
              model={model}
              overlays={overlays}
              index={readoutIndex}
              hidden={chart.hidden}
              priceDecimals={chart.priceDecimals}
              paneDecimals={chart.paneDecimals}
              trades={allTrades}
              tz={timeZone}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

function RunProvenance({ run, view }: { run: BacktestRunRow; view: BacktestTradesView | null }) {
  const dataset = run.dataset_version_ids.join(", ") || "not recorded";
  return (
    <span className="cw-run__prov" data-testid="run-provenance" data-run={run.run_id} data-verdict={run.gate_verdict}>
      <ProvenanceLabelChip label={{ kind: "single", provenance: run.provenance, count: 1 }} />
      <span className="cw__chip num" title={`Backtest run ${run.run_id}`}>
        run {run.run_id}
      </span>
      <span className="cw__chip num cw__chip--id" tabIndex={0} title={`Dataset ${dataset}`}>
        data {dataset.length > 22 ? `${dataset.slice(0, 14)}…${dataset.slice(-6)}` : dataset}
      </span>
      <span className="cw__chip" data-gate={run.gate_verdict}>
        gate {run.gate_verdict.replace("_", " ")}
      </span>
      {!run.engine_current || run.superseded ? (
        <span className="badge badge--degraded" data-testid="run-stale">
          stale engine ({run.engine_version || "unstamped"})
        </span>
      ) : null}
      {view &&
      view.run.trades_with_stops.value === 0 &&
      view.total_on_symbol.value !== null &&
      view.total_on_symbol.value > 0 ? (
        <span className="badge badge--degraded" data-testid="run-no-stops">
          no stops recorded
        </span>
      ) : null}
    </span>
  );
}

function DrawingRail({
  tool,
  onTool,
  canEdit,
  roleReason,
  selected,
  lockedAll,
  hiddenAll,
  onToggleLock,
  onToggleHidden,
}: {
  tool: DrawingTool;
  onTool: (tool: DrawingTool) => void;
  canEdit: boolean;
  roleReason: string | null;
  selected: Drawing | null;
  lockedAll: boolean;
  hiddenAll: boolean;
  onToggleLock: () => void;
  onToggleHidden: () => void;
}) {
  const noEdit = !canEdit;
  const why = roleReason ?? (noEdit ? "Drawings cannot be saved here right now." : null);
  return (
    <div
      className="cw-rail"
      role="toolbar"
      aria-label="Drawing tools"
      aria-orientation="vertical"
      data-testid="drawing-toolbar"
      data-can-edit={canEdit}
    >
      {TOOLS.map((spec) => {
        const disabled = spec.tool !== "select" && spec.tool !== "measure" && noEdit;
        return (
          <button
            key={spec.tool}
            type="button"
            className="cw-rail__btn"
            data-testid={`tool-${spec.tool}`}
            data-active={tool === spec.tool}
            aria-pressed={tool === spec.tool}
            aria-label={`${spec.label} (${spec.keys})`}
            title={disabled && why ? `${spec.label}: ${why}` : `${spec.label} · ${spec.keys}`}
            aria-disabled={disabled}
            onClick={() => (disabled ? undefined : onTool(spec.tool))}
          >
            {spec.icon}
          </button>
        );
      })}
      <span className="cw-rail__sep" aria-hidden="true" />
      <button
        type="button"
        className="cw-rail__btn"
        data-testid="drawing-lock"
        aria-pressed={selected ? selected.locked : lockedAll}
        aria-label={
          selected ? `${selected.locked ? "Unlock" : "Lock"} the selected drawing (Alt Shift L)` : `${lockedAll ? "Unlock" : "Lock"} all drawings (Alt Shift L)`
        }
        title={selected ? "Lock the selected drawing · Alt Shift L" : "Lock all drawings · Alt Shift L"}
        onClick={onToggleLock}
      >
        {(selected ? selected.locked : lockedAll) ? <Lock size={16} aria-hidden="true" /> : <LockOpen size={16} aria-hidden="true" />}
      </button>
      <button
        type="button"
        className="cw-rail__btn"
        data-testid="drawing-hide"
        aria-pressed={hiddenAll}
        aria-label={`${hiddenAll ? "Show" : "Hide"} all drawings (Alt Shift H)`}
        title="Hide all drawings · Alt Shift H"
        onClick={onToggleHidden}
      >
        {hiddenAll ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
      </button>
    </div>
  );
}

/**
 * The drawing edit bar, floating at the plot's top-left: undo, redo, delete,
 * the timeframe scope for new drawings, Fibonacci extensions for a selected
 * Fibonacci drawing, clear, and the save state (version) of the set.
 */
function DrawingActions({
  canEdit,
  drawings,
  selected,
  lockedAll,
  scopeToTf,
  tf,
  onToggleScope,
  onDelete,
  onPatch,
  onClear,
  onAlert,
  alertBusy,
  canAlert,
}: {
  canEdit: boolean;
  drawings: ReturnType<typeof useDrawings>;
  selected: Drawing | null;
  lockedAll: boolean;
  scopeToTf: boolean;
  tf: string;
  onToggleScope: () => void;
  onDelete: () => void;
  onPatch: (patch: Partial<Drawing>) => void;
  onClear: () => void;
  onAlert: (price: number, origin: "level" | "zone_top" | "zone_bottom") => void;
  alertBusy: boolean;
  canAlert: boolean;
}) {
  const noEdit = !canEdit;
  // A level alerts at its price; a zone at its top or bottom edge. The
  // direction (cross above or below) is decided by the platform.
  const levelPrice = selected && selected.kind === "hline" ? (selected.points[0]?.price ?? null) : null;
  const corners = selected && selected.kind === "rect" ? selected.points : null;
  const a = corners?.[0];
  const b = corners?.[1];
  // The zone's edges are its two stored anchor prices, higher and lower.
  const zone = a && b ? { top: Math.max(a.price, b.price), bottom: Math.min(a.price, b.price) } : null;
  return (
    <div className="cw-dbar" role="toolbar" aria-label="Drawing edits" data-testid="drawing-actions">
      {canEdit ? (
        <>
          <button
            type="button"
            className="cw-dbar__btn"
            data-testid="drawing-undo"
            aria-label="Undo (Ctrl Z)"
            title="Undo · Ctrl Z"
            aria-disabled={!drawings.canUndo}
            onClick={drawings.undo}
          >
            <Undo2 size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="cw-dbar__btn"
            data-testid="drawing-redo"
            aria-label="Redo (Ctrl Shift Z)"
            title="Redo · Ctrl Shift Z"
            aria-disabled={!drawings.canRedo}
            onClick={drawings.redo}
          >
            <Redo2 size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="cw-dbar__btn"
            data-testid="drawing-delete"
            aria-label="Delete the selected drawing (Delete)"
            title="Delete the selected drawing · Delete"
            aria-disabled={!selected || selected.locked || lockedAll}
            onClick={onDelete}
          >
            <Trash2 size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="cw-dbar__btn cw-dbar__btn--text"
            data-testid="drawing-scope"
            aria-pressed={scopeToTf}
            aria-label={scopeToTf ? `New drawings show on ${tf} only` : "New drawings show on every timeframe"}
            title="Timeframe scope for new drawings: every timeframe, or this one only"
            onClick={onToggleScope}
          >
            {scopeToTf ? `${tf} only` : "all TF"}
          </button>
          {selected && selected.kind === "fib" ? (
            <button
              type="button"
              className="cw-dbar__btn cw-dbar__btn--text"
              data-testid="fib-extensions"
              aria-pressed={selected.fib_extensions}
              aria-label="Show the 1.272 and 1.618 extensions"
              title="Fibonacci extensions 1.272 and 1.618"
              onClick={() => onPatch({ fib_extensions: !selected.fib_extensions })}
            >
              ext
            </button>
          ) : null}
          {canAlert && levelPrice !== null ? (
            <button
              type="button"
              className="cw-dbar__btn cw-dbar__btn--text"
              data-testid="alert-from-level"
              aria-disabled={alertBusy}
              title="Create a price alert at this level (the platform decides cross above or below)"
              onClick={() => (alertBusy ? undefined : onAlert(levelPrice, "level"))}
            >
              <BellPlus size={12} aria-hidden="true" /> alert
            </button>
          ) : null}
          {canAlert && zone ? (
            <>
              <button
                type="button"
                className="cw-dbar__btn cw-dbar__btn--text"
                data-testid="alert-from-zone-top"
                aria-disabled={alertBusy}
                title="Create a price alert at the zone's top edge"
                onClick={() => (alertBusy ? undefined : onAlert(zone.top, "zone_top"))}
              >
                <BellPlus size={12} aria-hidden="true" /> alert top
              </button>
              <button
                type="button"
                className="cw-dbar__btn cw-dbar__btn--text"
                data-testid="alert-from-zone-bottom"
                aria-disabled={alertBusy}
                title="Create a price alert at the zone's bottom edge"
                onClick={() => (alertBusy ? undefined : onAlert(zone.bottom, "zone_bottom"))}
              >
                <BellPlus size={12} aria-hidden="true" /> alert bottom
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="cw-dbar__btn cw-dbar__btn--text"
            data-testid="drawing-clear"
            aria-label="Clear all my drawings on this symbol (undo restores them)"
            title="Clear all my drawings on this symbol · undo restores them"
            aria-disabled={noEdit || drawings.all.length === 0}
            onClick={() => (drawings.all.length === 0 ? undefined : onClear())}
          >
            clear
          </button>
        </>
      ) : null}
      <span
        className="cw-dbar__status"
        data-testid="drawing-status"
        data-status={drawings.status.kind}
        role="status"
      >
        {statusText(drawings.status, drawings.all.length)}
        {canEdit || drawings.status.kind === "loading" ? "" : " · view only"}
      </span>
      {drawings.status.kind === "error" ? (
        <button type="button" className="cw-dbar__btn cw-dbar__btn--text" onClick={drawings.retry}>
          retry
        </button>
      ) : null}
    </div>
  );
}

function NoteEditor({
  x,
  y,
  text,
  onText,
  onDone,
  onCancel,
}: {
  x: number;
  y: number;
  text: string;
  onText: (text: string) => void;
  onDone: () => void;
  onCancel: () => void;
}) {
  // Enter places the note and Escape drops it; the blur that follows either
  // must not act a second time (an Escape must never save).
  const settled = useRef(false);
  const settle = (fn: () => void) => {
    if (settled.current) return;
    settled.current = true;
    fn();
  };
  return (
    <form
      className="cw-note"
      data-testid="note-editor"
      ref={(el) => {
        if (!el) return;
        el.style.setProperty("--note-x", `${x}px`);
        el.style.setProperty("--note-y", `${y}px`);
      }}
      onSubmit={(e) => {
        e.preventDefault();
        settle(onDone);
      }}
    >
      <label className="sr-only" htmlFor="cw-note-input">
        Note text
      </label>
      <input
        id="cw-note-input"
        data-testid="note-input"
        autoFocus
        maxLength={280}
        value={text}
        placeholder="Note… Enter to place, Esc to cancel"
        onChange={(e) => onText(e.target.value)}
        onKeyDown={(e: ReactKeyboardEvent<HTMLInputElement>) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            settle(onCancel);
          }
        }}
        onBlur={() => settle(onDone)}
      />
    </form>
  );
}

/** "22 Sep 06:00": the trades table's compact entry time in the display zone (the header names it). */
function shortTime(iso: string, tz: TimeZoneId): string {
  const full = formatBarTime(Math.floor(Date.parse(iso) / 1_000), tz);
  // "Tue 22 Sep 2026 06:00 UTC" -> "22 Sep 06:00"
  const parts = full.split(" ");
  return parts.length >= 5 ? `${parts[1]} ${parts[2]} ${parts[4]}` : full;
}

/**
 * The trades table's exit reason, short (programme P1 #9: the TP and EXIT
 * columns were cut off at 1440 px). The full reason is the cell's title and
 * the detail panel's text. An unknown reason is shown as the API sent it.
 */
const REASON_SHORT: Record<string, string> = {
  stop_loss: "SL",
  take_profit: "TP",
  trailing_stop: "TRAIL",
  breakeven: "BE",
  time_stop: "TIME",
  opposite_signal: "OPP",
  invalidation: "INVAL",
  session_close: "SESS",
  risk_halt: "HALT",
  end_of_data: "EOD",
  margin_call: "MARGIN",
  "open at cursor": "OPEN",
};

function reasonShort(reason: string): string {
  return REASON_SHORT[reason] ?? reason.replaceAll("_", " ");
}

function zoneLabel(tz: TimeZoneId): string {
  return TIME_ZONES.find((z) => z.id === tz)?.label ?? tz;
}

function TradesTable({
  timeZone,
  trades,
  selected,
  onPick,
  priceDecimals,
  stopsDetail,
}: {
  trades: PlacedTrade[];
  selected: string | null;
  onPick: (key: string) => void;
  priceDecimals: number | null;
  stopsDetail: string | null;
  timeZone: TimeZoneId;
}) {
  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const selectedRow = selected ? (trades.find((t) => t.key === selected) ?? null) : null;
  // Keep the selected row in view when the chart selects it.
  useEffect(() => {
    if (!selected) return;
    rowRefs.current.get(selected)?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  const onKey = (event: ReactKeyboardEvent<HTMLTableRowElement>, index: number) => {
    const move = (to: number) => {
      const key = trades[Math.max(0, Math.min(trades.length - 1, to))]?.key;
      if (key) rowRefs.current.get(key)?.focus();
    };
    if (event.key === "ArrowDown" || event.key === "j") {
      event.preventDefault();
      move(index + 1);
    } else if (event.key === "ArrowUp" || event.key === "k") {
      event.preventDefault();
      move(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      move(0);
    } else if (event.key === "End") {
      event.preventDefault();
      move(trades.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const key = trades[index]?.key;
      if (key) onPick(key);
    }
  };
  const price = (v: number | null) =>
    v === null ? (
      <span className="muted">—</span>
    ) : (
      <span className="num">{formatNumber(v, "", { decimals: priceDecimals })}</span>
    );
  return (
    <section className="cw-trades" id="cw-trades" aria-label="Trades on this chart" data-testid="trades-panel">
      {trades.length === 0 ? (
        <p className="cw-trades__empty muted" data-testid="trades-empty">
          No trade in this window for the selected source. A paper session or a backtest run with no
          trades here is an empty result, not a failure to load.
        </p>
      ) : (
        <div className="cw-trades__scroll">
          <table className="cw-trades__table">
            <thead>
              <tr>
                <th scope="col">Trade</th>
                <th scope="col">Entry {timeZone === "UTC" ? "UTC" : zoneLabel(timeZone)}</th>
                <th scope="col" className="r">Entry</th>
                <th scope="col" className="r">Exit</th>
                <th scope="col" className="r">Net P&amp;L</th>
                <th scope="col" className="r">
                  SL<span className="cw-trades__sep">/</span>TP
                </th>
                <th scope="col">Exit</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => {
                const pnl = t.row ? t.row.net_pnl : (t.fills?.exit.net_pnl ?? null);
                const entryIso = t.row ? t.row.entry_time : (t.fills?.entry.t ?? null);
                const reason = t.row ? t.row.exit_reason : (t.fills?.exit.exit_reason ?? null);
                const sl = t.segments.find((s) => s.role === "stop")?.price ?? null;
                const tp = t.segments.find((s) => s.role === "target")?.price ?? null;
                const pnlText = pnl ? formatFigure(pnl, { signed: true }) : null;
                return (
                  <tr
                    key={t.key}
                    ref={(el) => {
                      if (el) rowRefs.current.set(t.key, el);
                      else rowRefs.current.delete(t.key);
                    }}
                    tabIndex={i === 0 || t.key === selected ? 0 : -1}
                    aria-selected={t.key === selected}
                    data-testid="trade-row"
                    data-key={t.key}
                    data-source={t.source}
                    data-entry-index={t.entryIndex}
                    data-exit-index={t.exitIndex}
                    onClick={() => onPick(t.key)}
                    onKeyDown={(e) => onKey(e, i)}
                  >
                    <td>
                      <span className="chart-ink cw-trades__src" data-ink={t.provenance} data-source={t.source}>
                        {t.source === "backtest" ? "BT" : "PAPER"}
                      </span>{" "}
                      <span className="cw-trades__side" data-direction={t.direction ?? "unknown"}>
                        {t.direction === "short" ? "▼S" : t.direction === "long" ? "▲L" : "?"}
                      </span>
                    </td>
                    <td className="num">{entryIso ? shortTime(entryIso, timeZone) : "—"}</td>
                    <td className="r">{price(t.entryPrice)}</td>
                    <td className="r">{price(t.exitPrice)}</td>
                    <td className={`r num ${pnl && pnl.value !== null ? signClass(pnl.value, pnl.unit) : ""}`}>
                      {pnlText ?? <span className="muted">no data</span>}
                    </td>
                    <td className="r cw-trades__stack">
                      {price(sl)}
                      {price(tp)}
                    </td>
                    <td title={reason ? reason.replaceAll("_", " ") : undefined}>
                      {reason ? (
                        <abbr className="cw-trades__reason" title={reason.replaceAll("_", " ")}>
                          {reasonShort(reason)}
                        </abbr>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {selectedRow ? <TradeDetail trade={selectedRow} priceDecimals={priceDecimals} tz={timeZone} /> : null}
      {stopsDetail ? <p className="cw-trades__note muted">{stopsDetail}</p> : null}
      <p className="cw-trades__note muted">
        Entry times are {timeZone === "UTC" ? "UTC" : `shown in ${zoneLabel(timeZone)} (stored UTC)`}. Every figure is the platform&apos;s; the chart computes none. Click a row
        (or Enter) to bring the trade into view.
      </p>
    </section>
  );
}
