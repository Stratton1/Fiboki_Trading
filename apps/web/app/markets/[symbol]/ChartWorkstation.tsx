"use client";

import { BarChart3, ExternalLink, Layers, Table2 } from "lucide-react";
import Link from "next/link";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { lazy, Suspense, useEffect, useId, useMemo, useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList } from "@/components/CaveatList";
import { CrosshairReadout, type ReadoutSource } from "@/components/charts/CrosshairReadout";
import { LAYER_SECTIONS, OverlayLegend, type LayerKey } from "@/components/charts/OverlayLegend";
import { preloadGrid } from "@/components/grid";
import { LazyPriceChart, preloadPriceChart } from "@/components/LazyChart";
import { ProvenanceLabelChip } from "@/components/ProvenanceChip";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import { Segmented } from "@/components/ui/Segmented";
import { StaleBadge } from "@/components/ui/StaleBadge";
import { ViewStateTag } from "@/components/ui/ViewStateTag";
import { UrlState } from "@/components/UrlState";
import { buildChartModel, visiblePanes, type ChartModel } from "@/lib/chart-model";
import { isStale } from "@/lib/freshness";
import { displayDecimals, formatUtcTime, priceDecimals } from "@/lib/format";
import { deriveProvenance } from "@/lib/provenance";
import { useApi, type ApiHandle } from "@/lib/query";
import type { BarsView, Envelope, InstrumentRow, OverlayView, Page } from "@/lib/types";

/**
 * The chart workstation (plan §6, report G §2.4 C1–C18; this build covers the
 * 4c-1 core): candles from GET /api/markets/bars/{symbol}, and every series,
 * signal, fill, level and regime from GET /api/markets/overlays/{symbol}. The
 * browser computes nothing: lib/chart-model.ts only places the API's items on
 * the bar axis.
 *
 * Timeframes: the API does not say which timeframes have a dataset for a
 * symbol (no per-symbol catalogue route), so the control offers the three the
 * workstation is built for. A timeframe with no dataset is answered by the API
 * (503 `bars_unavailable`) and shown as that error, not guessed.
 *
 * States: loading, empty (the dataset has no bars in the window), absent (no
 * market-data root is mounted: the API's `market_data_not_mounted`), error,
 * and stale (last good data kept, marked). Never a blank canvas.
 */

/**
 * The resizable split and the data tables load when "View data" is first
 * opened: an operator who never opens them never downloads them.
 */
const loadDataPanel = () => import("@/components/charts/ChartDataPanel");
const loadSplitPane = () => import("@/components/ui/SplitPane");
const ChartDataPanel = lazy(() => loadDataPanel().then((m) => ({ default: m.ChartDataPanel })));
const SplitPane = lazy(() => loadSplitPane().then((m) => ({ default: m.SplitPane })));

const TIMEFRAMES = ["H1", "H4", "D1"] as const;
type Timeframe = (typeof TIMEFRAMES)[number];
const tfParser = parseAsStringLiteral(TIMEFRAMES).withDefault("H1").withOptions({ history: "replace" });

/** Bars per request: the API's own default window (routers/markets.py, limit=500). */
const BAR_LIMIT = 500;
/** An H1 bar closes once an hour; re-read every minute so a new one appears promptly. */
const REFRESH_MS = 60_000;

type Layers = Record<LayerKey, boolean>;
const ALL_LAYERS: Layers = { regimes: true, signals: true, fills: true, levels: true };

export function ChartWorkstation({ symbol }: { symbol: string }) {
  return (
    <UrlState fallback={<Workstation symbol={symbol} tf="H1" onTf={() => undefined} />}>
      <WorkstationFromUrl symbol={symbol} />
    </UrlState>
  );
}

function WorkstationFromUrl({ symbol }: { symbol: string }) {
  const [tf, setTf] = useQueryState("tf", tfParser);
  return <Workstation symbol={symbol} tf={tf} onTf={(next) => void setTf(next)} />;
}

/** Decimals for a non-price pane: the widest a value needs, by lib/format.ts's undeclared-precision rule. */
function paneDecimalsOf(model: ChartModel): Map<string, number> {
  const out = new Map<string, number>();
  for (const s of model.series) {
    if (s.pane === "price") continue;
    let widest = out.get(s.pane);
    for (const v of s.values) {
      if (v === null) continue;
      const d = displayDecimals(v, "");
      if (widest === undefined || d > widest) widest = d;
    }
    if (widest !== undefined) out.set(s.pane, widest);
  }
  return out;
}

function Workstation({
  symbol,
  tf,
  onTf,
}: {
  symbol: string;
  tf: Timeframe;
  onTf: (tf: Timeframe) => void;
}) {
  const enc = encodeURIComponent(symbol);
  const barsPath = `/api/markets/bars/${enc}?timeframe=${tf}&limit=${BAR_LIMIT}`;
  const overlaysPath = `/api/markets/overlays/${enc}?timeframe=${tf}&limit=${BAR_LIMIT}`;
  const bars = useApi<Envelope<BarsView>>(barsPath, { refreshMs: REFRESH_MS });
  const overlays = useApi<Envelope<OverlayView>>(overlaysPath, { refreshMs: REFRESH_MS });
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments");
  const [dataOpen, setDataOpen] = useState(false);
  // Layer and series toggles live here, so they survive a timeframe switch.
  const [layers, setLayers] = useState<Layers>(ALL_LAYERS);
  // Explicit operator choices; every other group follows its default.
  const [toggled, setToggled] = useState<ReadonlyMap<string, boolean>>(new Map());

  useEffect(() => {
    // Fetch the chart engine while the bars are still on their way.
    void preloadPriceChart();
  }, []);
  useEffect(() => {
    if (!dataOpen) return;
    void preloadGrid();
    void loadDataPanel();
    void loadSplitPane();
  }, [dataOpen]);

  const pip =
    instruments.status === "success"
      ? instruments.data.items.find((row) => row.symbol === symbol)?.pip_size.value
      : undefined;

  const barsEnv = bars.status === "success" ? bars.data : null;
  const overlayEnv = overlays.status === "success" ? overlays.data : null;
  const datasetMismatch =
    barsEnv !== null &&
    overlayEnv !== null &&
    overlayEnv.data.bars_dataset_version_id !== null &&
    overlayEnv.data.bars_dataset_version_id !== barsEnv.data.dataset_version_id;

  const journal = overlayEnv
    ? [
        ...overlayEnv.data.signals.map((s) => s.provenance),
        ...overlayEnv.data.fills.map((f) => f.provenance),
        ...overlayEnv.data.levels.map((l) => l.provenance),
      ]
    : [];

  return (
    <section className="cw" data-testid="chart-workstation" data-symbol={symbol} data-timeframe={tf}>
      <header className="cw__head">
        <div className="cw__title">
          <h1 data-testid="chart-symbol">{symbol}</h1>
          <Link href="/markets" className="cw__back">
            All instruments
          </Link>
        </div>
        <Segmented<Timeframe>
          label="Timeframe"
          value={tf}
          onValueChange={onTf}
          options={TIMEFRAMES.map((value) => ({ value, label: value }))}
          testId="chart-timeframe"
        />
        <div className="cw__meta">
          <Freshness bars={bars} overlays={overlays} />
          {barsEnv ? (
            <span
              className="cw__chip num"
              data-testid="dataset-version"
              data-version={barsEnv.data.dataset_version_id}
              title={`Bar dataset version ${barsEnv.data.dataset_version_id}`}
            >
              <Layers size={12} aria-hidden="true" />
              dataset {barsEnv.data.dataset_version_id ? barsEnv.data.dataset_version_id.slice(0, 12) : "not reported"}
            </span>
          ) : null}
          {datasetMismatch && overlayEnv ? (
            <span className="badge badge--degraded" data-testid="dataset-mismatch" role="status">
              overlays from dataset {overlayEnv.data.bars_dataset_version_id?.slice(0, 12)}
            </span>
          ) : null}
          {barsEnv ? (
            <span className="cw__legend-item" data-testid="bars-provenance">
              <span className="muted">Bars</span>
              <ProvenanceLabelChip label={{ kind: "source", source: barsEnv.source }} />
            </span>
          ) : null}
          {overlayEnv ? (
            <span className="cw__legend-item" data-testid="journal-provenance">
              <span className="muted">Journal</span>
              {journal.length > 0 ? (
                <ProvenanceLabelChip label={deriveProvenance(journal)} />
              ) : (
                <span className="muted">no items</span>
              )}
            </span>
          ) : null}
          <SourcesPopover bars={barsEnv} overlays={overlayEnv} />
          <Button
            size="sm"
            onClick={() => setDataOpen((open) => !open)}
            aria-expanded={dataOpen}
            aria-controls="chart-data"
            data-testid="chart-view-data"
            disabled={barsEnv === null || barsEnv.data.bars.length === 0}
          >
            <Table2 size={13} aria-hidden="true" />
            {dataOpen ? "Hide data" : "View data"}
          </Button>
        </div>
      </header>

      {barsEnv && barsEnv.caveats.length > 0 ? <CaveatList caveats={barsEnv.caveats} /> : null}
      {overlayEnv && overlayEnv.caveats.length > 0 ? <CaveatList caveats={overlayEnv.caveats} /> : null}

      <div className="cw__body">
        {bars.status === "error" && bars.error.code === "market_data_not_mounted" ? (
          <div className="state state--empty cw__state" data-testid="state-absent" role="status">
            <div className="state__title">
              <span>No market data is mounted</span>
              <ViewStateTag state="absent" />
            </div>
            <div className="state__body">
              {bars.error.message}
              <p>
                No bars can be drawn for {symbol} {tf} on this deployment. This is a missing data
                source, not a quiet market.
              </p>
            </div>
            <div className="state__code">code: {bars.error.code}</div>
          </div>
        ) : (
          <AsyncBoundary
            state={bars}
            label={`${symbol} ${tf} bars`}
            onRetry={bars.reload}
            isEmpty={(env) => env.data.bars.length === 0}
            emptyTitle={`No bars for ${symbol} ${tf}`}
            emptyBody={`The platform answered successfully with no bars for ${symbol} at ${tf} in dataset ${
              barsEnv?.data.dataset_version_id ?? "(not reported)"
            }. That is a real, empty result, not a failure to load. Nothing is drawn.`}
          >
            {(env) => (
              <ChartArea
                symbol={symbol}
                tf={tf}
                bars={env}
                barsPath={barsPath}
                overlays={overlays}
                overlaysPath={overlaysPath}
                pipSize={pip}
                dataOpen={dataOpen}
                layers={layers}
                setLayers={setLayers}
                toggled={toggled}
                setToggled={setToggled}
              />
            )}
          </AsyncBoundary>
        )}
      </div>
    </section>
  );
}

function Freshness({
  bars,
  overlays,
}: {
  bars: ApiHandle<Envelope<BarsView>>;
  overlays: ApiHandle<Envelope<OverlayView>>;
}) {
  if (bars.status !== "success") {
    return (
      <span className="cw__chip" data-testid="chart-freshness" data-freshness={bars.status}>
        {bars.status === "loading" ? "reading bars…" : "no bars"}
      </span>
    );
  }
  // The worse of the two reads decides: overlays drawn over fresh bars are
  // only as current as the overlays.
  const worst =
    overlays.status === "success" &&
    (isStale(overlays.freshness) || overlays.freshness === "lagging") &&
    !isStale(bars.freshness)
      ? overlays
      : bars;
  if (isStale(worst.freshness) || worst.freshness === "lagging") {
    return (
      <span data-testid="chart-freshness" data-freshness={worst.freshness}>
        <StaleBadge
          info={{
            freshness: worst.freshness,
            asOf: worst.asOf,
            sourceAsOf: worst.sourceAsOf,
            refreshError: worst.refreshError,
          }}
          polling={worst.refreshMs !== undefined}
          onRetry={worst.reload}
        />
      </span>
    );
  }
  const asOf = bars.data.source.as_of;
  return (
    <span className="cw__chip num" data-testid="chart-freshness" data-freshness={bars.freshness}>
      <BarChart3 size={12} aria-hidden="true" />
      {asOf ? `bars as of ${formatUtcTime(asOf)}` : "bars: as-of not supplied"}
    </span>
  );
}

function SourcesPopover({
  bars,
  overlays,
}: {
  bars: Envelope<BarsView> | null;
  overlays: Envelope<OverlayView> | null;
}) {
  // Every distinct OverlaySource the API attached, with the items it covers.
  const rows = new Map<string, { kind: string; detail: string; items: Map<string, number> }>();
  const add = (section: string, source: { kind: string; detail: string }) => {
    const key = `${source.kind}\u0000${source.detail}`;
    let row = rows.get(key);
    if (!row) {
      row = { kind: source.kind, detail: source.detail, items: new Map() };
      rows.set(key, row);
    }
    const seen = row.items.get(section);
    row.items.set(section, seen === undefined ? 1 : seen + 1);
  };
  if (overlays) {
    const d = overlays.data;
    for (const [section, list] of [
      ["signals", d.signals],
      ["fills", d.fills],
      ["levels", d.levels],
      ["regimes", d.regimes],
      ["series", d.series],
      ["events", d.events],
      ["headlines", d.headlines],
    ] as const) {
      for (const item of list) add(section, item.source);
    }
  }
  const fingerprints = overlays ? [...new Set(overlays.data.regimes.map((r) => r.classifier_fingerprint))] : [];
  return (
    <Popover
      title="Sources"
      testId="chart-sources-popover"
      align="end"
      trigger={
        <Button size="sm" data-testid="chart-sources">
          Sources{overlays ? ` (${rows.size})` : ""}
        </Button>
      }
    >
      <div className="cw-sources">
        {bars ? (
          <p data-testid="sources-bars">
            <strong>Bars</strong>: {bars.source.kind.toUpperCase()} · {bars.source.detail} Dataset{" "}
            <code>{bars.data.dataset_version_id || "not reported"}</code>
            {bars.data.volume_kind ? `, with ${bars.data.volume_kind.replace("_", " ")}` : ", no volume"}.
          </p>
        ) : (
          <p className="muted">Bars not loaded.</p>
        )}
        {overlays ? (
          <>
            <p>
              <strong>Overlays</strong>: {overlays.source.kind.toUpperCase()} · {overlays.source.detail}
              {overlays.data.bars_dataset_version_id ? (
                <>
                  {" "}
                  Computed on dataset <code>{overlays.data.bars_dataset_version_id}</code>.
                </>
              ) : (
                " No bar dataset version was reported for the overlays."
              )}
            </p>
            {rows.size === 0 ? (
              <p className="muted" data-testid="sources-empty">
                No overlay item was returned, so no item-level source is listed.
              </p>
            ) : (
              <ul className="cw-sources__list">
                {[...rows.values()].map((row) => (
                  <li key={`${row.kind}|${row.detail}`} data-testid="source-row" data-kind={row.kind}>
                    <span className="cw__chip">{row.kind}</span> {row.detail}{" "}
                    <span className="muted">
                      (
                      {[...row.items.entries()]
                        .map(([section, n]) => `${n.toLocaleString("en-GB")} ${section}`)
                        .join(", ")}
                      )
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {fingerprints.length > 0 ? (
              <p>
                Regime classifier <code>{fingerprints.join(", ")}</code>.
              </p>
            ) : null}
          </>
        ) : (
          <p className="muted">Overlays not loaded.</p>
        )}
        <p className="muted">
          Price chart drawn with{" "}
          <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
            TradingView Lightweight Charts <ExternalLink size={11} aria-hidden="true" />
          </a>{" "}
          (Apache-2.0). It computes nothing; every value above came from the platform.
        </p>
      </div>
    </Popover>
  );
}

function ChartArea({
  symbol,
  tf,
  bars,
  barsPath,
  overlays,
  overlaysPath,
  pipSize,
  dataOpen,
  layers,
  setLayers,
  toggled,
  setToggled,
}: {
  symbol: string;
  tf: Timeframe;
  bars: Envelope<BarsView>;
  barsPath: string;
  overlays: ApiHandle<Envelope<OverlayView>>;
  overlaysPath: string;
  pipSize: number | null | undefined;
  dataOpen: boolean;
  layers: Layers;
  setLayers: (update: (layers: Layers) => Layers) => void;
  toggled: ReadonlyMap<string, boolean>;
  setToggled: (update: (prev: ReadonlyMap<string, boolean>) => ReadonlyMap<string, boolean>) => void;
}) {
  const overlayData = overlays.status === "success" ? overlays.data.data : null;
  const model = useMemo(
    () => buildChartModel(bars.data.bars, overlayData, bars.data.volume_kind),
    [bars.data.bars, bars.data.volume_kind, overlayData],
  );
  const paneDecimals = useMemo(() => paneDecimalsOf(model), [model]);
  // Price precision from the instrument's pip size when the platform supplied
  // it; otherwise the undeclared-precision rule over the closes (lib/format.ts).
  const fromPip = priceDecimals(pipSize);
  const decimals = useMemo(() => {
    if (fromPip !== null) return fromPip;
    let widest: number | null = null;
    for (const b of bars.data.bars) {
      const d = displayDecimals(b.c, "");
      if (widest === null || d > widest) widest = d;
    }
    return widest;
  }, [fromPip, bars.data.bars]);

  const hidden = useMemo(() => {
    const out = new Set<string>();
    for (const g of model.groups) {
      const shown = toggled.get(g.id);
      if (shown === undefined ? !g.defaultVisible : !shown) out.add(g.id);
    }
    return out;
  }, [model.groups, toggled]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const readoutId = useId();

  const n = model.times.length;
  const keyboard = cursor !== null && cursor < n ? cursor : null;
  const pointer = hover !== null && hover < n ? hover : null;
  const shown = keyboard !== null ? keyboard : pointer;
  const source: ReadoutSource = keyboard !== null ? "keyboard" : pointer !== null ? "pointer" : "latest";

  const panes = visiblePanes(model, hidden);
  const visibleSeries = model.series.filter((s) => !hidden.has(s.group)).length;
  const layerCount = (layer: LayerKey, count: number) =>
    overlays.status === "success" &&
    !LAYER_SECTIONS[layer].some((s) => overlays.data.data.sections[s]?.available === false)
      ? count
      : null;
  const count = (v: number | null) => (v === null ? "n/a" : v.toLocaleString("en-GB"));
  const summary = {
    signals: layerCount("signals", model.signals.length),
    fills: layerCount("fills", model.fills.length),
    levels: layerCount("levels", model.levels.length),
    regimes: layerCount("regimes", model.regimes.length),
  };

  const chart = (
    <div className="cw__chart">
      <LazyPriceChart
        model={model}
        hidden={hidden}
        layers={layers}
        priceDecimals={decimals}
        paneDecimals={paneDecimals}
        label={`${symbol} ${tf} price chart, ${n.toLocaleString("en-GB")} bars. Arrow keys move the cursor bar by bar, Shift moves ten, Home and End jump, Escape releases.`}
        describedBy={readoutId}
        cursor={keyboard}
        onCursor={(index, from) => {
          if (from === "keyboard") {
            setCursor(index);
            return;
          }
          if (index !== null) setCursor(null);
          setHover(index);
        }}
      />
    </div>
  );

  return (
    <div className="cw__area">
      <OverlayLegend
        model={model}
        overlays={overlayData}
        overlaysPending={overlays.status !== "success"}
        layers={layers}
        onLayer={(layer) => setLayers((l) => ({ ...l, [layer]: !l[layer] }))}
        hidden={hidden}
        onGroup={(group) =>
          setToggled((prev) => {
            const next = new Map(prev);
            next.set(group, hidden.has(group));
            return next;
          })
        }
      />
      {overlays.status === "loading" ? (
        <p className="cw__note" data-testid="overlays-loading" role="status">
          Reading overlays for {symbol} {tf}… the candles are drawn; signals, fills, levels, regimes
          and series follow.
        </p>
      ) : null}
      {overlays.status === "error" ? (
        <div className="cw__note cw__note--error" data-testid="overlays-error" role="alert">
          <strong>Could not load the overlays.</strong> {overlays.error.message} The candles are
          current; nothing else is drawn, and an empty layer here means “not loaded”, not “none”.{" "}
          <span className="state__code">
            code: {overlays.error.code}
            {overlays.error.status ? ` · http ${overlays.error.status}` : ""}
            {overlays.error.correlationId ? ` · correlation ${overlays.error.correlationId}` : ""}
          </span>{" "}
          <Button size="sm" onClick={overlays.reload} data-testid="overlays-retry">
            Retry
          </Button>
        </div>
      ) : null}

      {dataOpen ? (
        <Suspense
          fallback={
            <>
              {chart}
              <p className="cw__note" data-testid="chart-data-pending" role="status">
                Preparing the data tables…
              </p>
            </>
          }
        >
          <SplitPane
            id="chart-workstation-data"
            orientation="vertical"
            className="cw__split"
            padded={false}
            firstLabel="Price chart"
            secondLabel="Chart data"
            defaultFirst={60}
            minFirst={30}
            minSecond={20}
            first={chart}
            second={
              <div id="chart-data" className="cw__data">
                <ChartDataPanel
                  model={model}
                  hidden={hidden}
                  priceDecimals={decimals}
                  paneDecimals={paneDecimals}
                  barsSource={bars.source}
                  overlaysSource={overlays.status === "success" ? overlays.data.source : null}
                  barsPath={barsPath}
                  overlaysPath={overlaysPath}
                  volumeKind={bars.data.volume_kind}
                />
              </div>
            }
          />
        </Suspense>
      ) : (
        chart
      )}

      <CrosshairReadout
        id={readoutId}
        model={model}
        index={shown}
        source={source}
        hidden={hidden}
        priceDecimals={decimals}
        paneDecimals={paneDecimals}
        volumeKind={bars.data.volume_kind}
      />
      <p
        className="cw__summary num"
        data-testid="chart-summary"
        data-bars={n}
        data-signals={summary.signals ?? ""}
        data-fills={summary.fills ?? ""}
        data-levels={summary.levels ?? ""}
        data-regimes={summary.regimes ?? ""}
        data-series={visibleSeries}
        data-series-total={model.series.length}
        data-panes={panes.length}
        data-volume={model.hasVolume}
        data-overlays={overlays.status}
      >
        {n.toLocaleString("en-GB")} bars · {count(summary.signals)} signals · {count(summary.fills)} fills ·{" "}
        {count(summary.levels)} levels · {count(summary.regimes)} regime runs · {visibleSeries} of{" "}
        {model.series.length} series · {panes.length} pane{panes.length === 1 ? "" : "s"}
        {model.hasVolume ? ` (with ${bars.data.volume_kind === "tick_volume" ? "tick volume" : "volume"})` : ""}
      </p>
    </div>
  );
}
