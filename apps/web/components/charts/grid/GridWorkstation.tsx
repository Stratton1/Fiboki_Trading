"use client";

import { ExternalLink, Link2, Link2Off } from "lucide-react";
import Link from "next/link";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useCallback, useMemo, useState } from "react";
import { LazyPriceChart } from "@/components/LazyChart";
import { UrlState } from "@/components/UrlState";
import type { CompareView } from "@/lib/chart-contract";
import { alignByTime, buildChartModel } from "@/lib/chart-model";
import { isStale } from "@/lib/freshness";
import { formatNumber, formatSigned, formatUtcTime, priceDecimals } from "@/lib/format";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { BarsView, Envelope, InstrumentRow, OverlayView, Page } from "@/lib/types";
import { formatBarTime } from "../time";
import { useTimeZone } from "../useTimeZone";

/**
 * MARKETS · multi-chart grid, `/markets/grid` (Wave 2B, programme features
 * 18 and 19): 1x2, 2x2 or 3x3 price charts, each with its own symbol,
 * timeframe and one compare symbol, with four links between them:
 *
 *   symbol     every chart shows the first chart's symbol (multi-timeframe);
 *   timeframe  every chart shows the first chart's timeframe (multi-symbol);
 *   crosshair  the pointer's bar time is mirrored on every other chart;
 *   time axis  scrolling or zooming one chart moves the others to the same span.
 *
 * Every candle, marker and compare value is the API's: bars from
 * /api/markets/bars, markers from /api/markets/overlays (no indicators here,
 * to keep nine charts cheap), and the compare line from
 * /api/markets/compare, which rebases it to percent on the server. The grid
 * only places those values on bars. The layout, symbols, timeframes,
 * compares and links live in the URL, so a grid is a link.
 */

export const LAYOUTS = ["1x2", "2x2", "3x3"] as const;
export type Layout = (typeof LAYOUTS)[number];
const CELLS: Record<Layout, number> = { "1x2": 2, "2x2": 4, "3x3": 9 };
const TIMEFRAMES = ["H1", "H4", "D1"] as const;
type Tf = (typeof TIMEFRAMES)[number];
const DEFAULT_TFS: Tf[] = ["H1", "H4", "D1"];
const SYNCS = ["symbol", "tf", "crosshair", "time"] as const;
type Sync = (typeof SYNCS)[number];
const SYNC_LABEL: Record<Sync, string> = {
  symbol: "Symbol",
  tf: "Timeframe",
  crosshair: "Crosshair",
  time: "Time axis",
};
/** Bars per grid chart: fewer than the workstation's 500, nine charts share the API. */
const GRID_BARS = 300;
const REFRESH_MS = 60_000;

const layoutParser = parseAsStringLiteral(LAYOUTS).withDefault("2x2").withOptions({ history: "replace" });
const listParser = parseAsString.withOptions({ history: "replace" });

function csv(value: string | null): string[] {
  return (value ?? "").split(",").map((s) => s.trim());
}

export interface GridState {
  layout: Layout;
  symbols: string[];
  tfs: string[];
  compares: string[];
  sync: Set<Sync>;
}

/** The cells as shown: links applied, gaps filled from the first cell. */
export function resolveCells(state: GridState): { symbol: string; tf: Tf; compare: string | null }[] {
  const n = CELLS[state.layout];
  const first = (state.symbols[0] || "EURUSD").toUpperCase();
  const firstTf = (TIMEFRAMES as readonly string[]).includes(state.tfs[0] ?? "") ? (state.tfs[0] as Tf) : "H1";
  return Array.from({ length: n }, (_, i) => {
    const symbol = state.sync.has("symbol") ? first : (state.symbols[i] || first).toUpperCase();
    const ownTf = state.tfs[i];
    const tf: Tf = state.sync.has("tf")
      ? firstTf
      : (TIMEFRAMES as readonly string[]).includes(ownTf ?? "")
        ? (ownTf as Tf)
        : (DEFAULT_TFS[i % DEFAULT_TFS.length] as Tf);
    const compare = (state.compares[i] || "").toUpperCase();
    return { symbol, tf, compare: compare && compare !== symbol ? compare : null };
  });
}

export function GridWorkstation() {
  return (
    <UrlState
      fallback={
        <Grid
          state={{ layout: "2x2", symbols: ["EURUSD"], tfs: [], compares: [], sync: new Set(["crosshair", "time"]) }}
          onState={() => undefined}
        />
      }
    >
      <GridFromUrl />
    </UrlState>
  );
}

function GridFromUrl() {
  const [layout, setLayout] = useQueryState("layout", layoutParser);
  const [symbols, setSymbols] = useQueryState("symbols", listParser);
  const [tfs, setTfs] = useQueryState("tfs", listParser);
  const [cmp, setCmp] = useQueryState("cmp", listParser);
  const [sync, setSync] = useQueryState("sync", listParser);
  const state: GridState = {
    layout,
    symbols: csv(symbols),
    tfs: csv(tfs),
    compares: csv(cmp),
    // Unset: crosshair and time axis linked, symbol and timeframe free.
    sync: new Set(
      (sync === null ? ["crosshair", "time"] : csv(sync)).filter((s): s is Sync =>
        (SYNCS as readonly string[]).includes(s),
      ),
    ),
  };
  const onState = (next: GridState) => {
    void setLayout(next.layout);
    void setSymbols(next.symbols.join(",") || null);
    void setTfs(next.tfs.some(Boolean) ? next.tfs.join(",") : null);
    void setCmp(next.compares.some(Boolean) ? next.compares.join(",") : null);
    void setSync([...next.sync].join(",") || "none");
  };
  return <Grid state={state} onState={onState} />;
}

function Grid({ state, onState }: { state: GridState; onState: (next: GridState) => void }) {
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments");
  const list = instruments.status === "success" ? instruments.data.items : [];
  const cells = resolveCells(state);
  const [cross, setCross] = useState<{ t: number | null; from: number } | null>(null);
  const [range, setRange] = useState<{ from: number; to: number; source: number } | null>(null);
  const { tz } = useTimeZone();

  const edit = (i: number, field: "symbols" | "tfs" | "compares", value: string) => {
    const n = CELLS[state.layout];
    const current = cells.map((c) => (field === "symbols" ? c.symbol : field === "tfs" ? c.tf : (c.compare ?? "")));
    const next = Array.from({ length: n }, (_, k) => current[k] ?? "");
    next[i] = value;
    onState({ ...state, [field]: next });
  };
  const toggle = (s: Sync) => {
    const next = new Set(state.sync);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    onState({ ...state, sync: next });
  };

  const onCross = useCallback((from: number, t: number | null) => setCross({ t, from }), []);
  const onRange = useCallback(
    (source: number, r: { from: number; to: number }) => setRange({ ...r, source }),
    [],
  );

  return (
    <section
      className="cg"
      data-testid="chart-grid"
      data-layout={state.layout}
      data-sync={[...state.sync].sort().join(",")}
      aria-label="Multi-chart grid"
    >
      <header className="cg__bar" role="toolbar" aria-label="Grid layout and links">
        <h1 className="cg__title">Chart grid</h1>
        <fieldset className="cw-seg" data-testid="grid-layout">
          <legend className="sr-only">Layout</legend>
          {LAYOUTS.map((l) => (
            <label key={l} className="cw-seg__item" data-checked={state.layout === l}>
              <input
                type="radio"
                name="grid-layout"
                value={l}
                checked={state.layout === l}
                onChange={() => onState({ ...state, layout: l })}
              />
              {l}
            </label>
          ))}
        </fieldset>
        <span className="cg__label muted">Link</span>
        {SYNCS.map((s) => (
          <button
            key={s}
            type="button"
            className="cg__sync"
            aria-pressed={state.sync.has(s)}
            data-testid={`grid-sync-${s}`}
            onClick={() => toggle(s)}
            title={`${state.sync.has(s) ? "Unlink" : "Link"} ${SYNC_LABEL[s].toLowerCase()} across charts`}
          >
            {state.sync.has(s) ? <Link2 size={12} aria-hidden="true" /> : <Link2Off size={12} aria-hidden="true" />}
            {SYNC_LABEL[s]}
          </button>
        ))}
        <span className="cg__note muted">
          Times {tz === "UTC" ? "UTC" : `shown in ${tz}`}; every value from the platform.
        </span>
      </header>
      <div className="cg__cells" data-count={cells.length}>
        {cells.map((cell, i) => (
          <GridCell
            key={i}
            index={i}
            cell={cell}
            instruments={list}
            symbolLocked={state.sync.has("symbol") && i > 0}
            tfLocked={state.sync.has("tf") && i > 0}
            onSymbol={(v) => edit(i, "symbols", v)}
            onTf={(v) => edit(i, "tfs", v)}
            onCompare={(v) => edit(i, "compares", v)}
            syncTime={state.sync.has("crosshair") && cross && cross.from !== i ? cross.t : null}
            onCrosshairTime={state.sync.has("crosshair") ? onCross : undefined}
            syncRange={state.sync.has("time") && range && range.source !== i ? range : null}
            onTimeRange={state.sync.has("time") ? onRange : undefined}
            tz={tz}
          />
        ))}
      </div>
    </section>
  );
}

function GridCell({
  index,
  cell,
  instruments,
  symbolLocked,
  tfLocked,
  onSymbol,
  onTf,
  onCompare,
  syncTime,
  onCrosshairTime,
  syncRange,
  onTimeRange,
  tz,
}: {
  index: number;
  cell: { symbol: string; tf: Tf; compare: string | null };
  instruments: InstrumentRow[];
  symbolLocked: boolean;
  tfLocked: boolean;
  onSymbol: (v: string) => void;
  onTf: (v: string) => void;
  onCompare: (v: string) => void;
  syncTime: number | null;
  onCrosshairTime?: (from: number, t: number | null) => void;
  syncRange: { from: number; to: number } | null;
  onTimeRange?: (source: number, r: { from: number; to: number }) => void;
  tz: ReturnType<typeof useTimeZone>["tz"];
}) {
  const { symbol, tf, compare } = cell;
  const enc = encodeURIComponent(symbol);
  const bars = useApi<Envelope<BarsView>>(`/api/markets/bars/${enc}?timeframe=${tf}&limit=${GRID_BARS}`, {
    refreshMs: REFRESH_MS,
  });
  const overlays = useApi<Envelope<OverlayView>>(
    `/api/markets/overlays/${enc}?timeframe=${tf}&limit=${GRID_BARS}&include_indicators=false`,
    { refreshMs: REFRESH_MS },
  );
  const barsEnv = bars.status === "success" ? bars.data : null;
  const first = barsEnv?.data.bars[0]?.t ?? null;
  const last = barsEnv?.data.bars[barsEnv.data.bars.length - 1]?.t ?? null;
  const comparePath =
    compare && first && last
      ? `/api/markets/compare/${enc}?with=${encodeURIComponent(compare)}&timeframe=${tf}&from=${encodeURIComponent(
          first,
        )}&to=${encodeURIComponent(last)}&limit=${GRID_BARS}`
      : null;
  const cmp = useApi<Envelope<CompareView>>(comparePath);
  const overlayData = overlays.status === "success" ? overlays.data.data : null;
  const model = useMemo(
    () => (barsEnv ? buildChartModel(barsEnv.data.bars, overlayData, barsEnv.data.volume_kind) : null),
    [barsEnv, overlayData],
  );
  const cmpData = cmp.status === "success" ? cmp.data.data : null;
  const compareSeries = useMemo(
    () =>
      model && cmpData && compare
        ? { label: `${compare} %`, values: alignByTime(model.times, cmpData.points) }
        : null,
    [model, cmpData, compare],
  );
  const pip = instruments.find((r) => r.symbol === symbol)?.pip_size.value;
  const decimals = priceDecimals(pip);
  const [hover, setHover] = useState<number | null>(null);
  const shown = model ? (hover ?? model.times.length - 1) : null;
  const bar = model && shown !== null ? model.bars[shown] : undefined;
  const time = model && shown !== null ? model.times[shown] : undefined;
  const options = instruments.length > 0 ? instruments.map((r) => r.symbol) : [symbol];
  const stale = bars.status === "success" && (isStale(bars.freshness) || bars.freshness === "lagging");
  const emptyHidden = useMemo(() => new Set<string>(), []);
  const emptyDecimals = useMemo(() => new Map<string, number>(), []);

  return (
    <article
      className="cg-cell"
      data-testid="grid-cell"
      data-index={index}
      data-symbol={symbol}
      data-timeframe={tf}
      data-compare={compare ?? ""}
      data-stale={stale || undefined}
    >
      <div className="cg-cell__head">
        <label className="cg-cell__field">
          <span className="sr-only">Chart {index + 1} symbol</span>
          <select
            value={symbol}
            disabled={symbolLocked}
            data-testid="grid-symbol"
            onChange={(e) => onSymbol(e.target.value)}
          >
            {(options.includes(symbol) ? options : [symbol, ...options]).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="cg-cell__field">
          <span className="sr-only">Chart {index + 1} timeframe</span>
          <select value={tf} disabled={tfLocked} data-testid="grid-tf" onChange={(e) => onTf(e.target.value)}>
            {TIMEFRAMES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="cg-cell__field">
          <span className="sr-only">Chart {index + 1} compare symbol</span>
          <select value={compare ?? ""} data-testid="grid-compare" onChange={(e) => onCompare(e.target.value)}>
            <option value="">no compare</option>
            {options
              .filter((s) => s !== symbol)
              .map((s) => (
                <option key={s} value={s}>
                  vs {s}
                </option>
              ))}
          </select>
        </label>
        <span className="cg-cell__read num" data-testid="grid-readout">
          {bar && time !== undefined ? (
            <>
              {formatBarTime(time, tz)} C {formatNumber(bar.c, "", { decimals })}
              {compareSeries && shown !== null ? (
                <>
                  {" · "}
                  {compare}{" "}
                  {compareSeries.values[shown] === null || compareSeries.values[shown] === undefined
                    ? "no bar"
                    : formatSigned(compareSeries.values[shown] as number, "%", { decimals: 2 })}
                </>
              ) : null}
            </>
          ) : null}
        </span>
        <Link
          className="cg-cell__open"
          href={route.instrument(symbol, { tf })}
          aria-label={`Open ${symbol} ${tf} in the chart workstation`}
        >
          <ExternalLink size={12} aria-hidden="true" />
        </Link>
      </div>
      <div className="cg-cell__plot">
        {bars.status === "loading" ? (
          <div className="price-chart price-chart--pending" role="status" data-testid="grid-cell-loading">
            Reading {symbol} {tf} bars…
          </div>
        ) : bars.status === "error" ? (
          <div className="state state--error cg-cell__state" role="alert" data-testid="grid-cell-error">
            <div className="state__title">
              {bars.error.code === "market_data_not_mounted" ? "No market data is mounted" : "Bars not loaded"}
            </div>
            <div className="state__body">{bars.error.message}</div>
            <div className="state__code">code: {bars.error.code}</div>
          </div>
        ) : model && model.times.length === 0 ? (
          <div className="state state--empty cg-cell__state" role="status" data-testid="grid-cell-empty">
            No bars for {symbol} {tf}: an empty result, not a failure.
          </div>
        ) : model ? (
          <LazyPriceChart
            model={model}
            hidden={emptyHidden}
            layers={{ regimes: true, signals: true, fills: true, levels: true }}
            priceDecimals={decimals}
            paneDecimals={emptyDecimals}
            label={`${symbol} ${tf} price chart, grid cell ${index + 1}`}
            cursor={null}
            onCursor={(i, from) => (from === "pointer" ? setHover(i) : undefined)}
            compare={compareSeries}
            syncTime={syncTime}
            onCrosshairTime={onCrosshairTime ? (t) => onCrosshairTime(index, t) : undefined}
            syncRange={syncRange}
            onTimeRange={onTimeRange ? (r) => onTimeRange(index, r) : undefined}
            timeZone={tz}
            compact
          />
        ) : null}
      </div>
      <footer className="cg-cell__foot muted num">
        {barsEnv ? (
          <>
            <span data-testid="grid-dataset">dataset {barsEnv.data.dataset_version_id || "not reported"}</span>
            {" · "}
            {barsEnv.source.as_of ? `as of ${formatUtcTime(barsEnv.source.as_of)}` : "as-of not supplied"}
            {stale ? <span className="badge badge--degraded"> stale</span> : null}
            {cmp.status === "error" ? (
              <span className="badge badge--degraded" data-testid="grid-compare-error">
                {" "}
                compare not loaded: {cmp.error.code}
              </span>
            ) : null}
            {cmpData && cmpData.anchor_time ? ` · ${compare} rebased at ${formatUtcTime(cmpData.anchor_time)}` : null}
          </>
        ) : null}
      </footer>
    </article>
  );
}
