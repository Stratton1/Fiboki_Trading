"use client";

import { useState } from "react";
import type { ChartModel, PlacedFill, PlacedRegime, PlacedSignal } from "@/lib/chart-model";
import { formatNumber, formatTimestamp } from "@/lib/format";
import type { Bar, LevelOverlay, SourceNote } from "@/lib/types";
import { FigureValue } from "../FigureValue";
import { DataGrid, type GridColumn } from "../grid";
import { Tab, TabsList, TabsPanel, TabsRoot } from "../ui/Tabs";
import { formatBarTime } from "./time";

/**
 * "View data" for the chart workstation: every number the chart draws, as a
 * table (report G C15; the same rule as every other chart in the app). The
 * rows are the API's rows, not re-derived: bars from /bars, and signals,
 * fills, levels, regimes and series from /overlays, each with its provenance
 * chip where the API sent a provenance. Keyboard-operable through DataGrid.
 */

type Tabs = "bars" | "signals" | "fills" | "levels" | "regimes" | "series";

interface BarRow {
  index: number;
  time: number;
  bar: Bar;
}

function price(value: number, decimals: number | null) {
  return <span className="num">{formatNumber(value, "", { decimals })}</span>;
}

export function ChartDataPanel({
  model,
  hidden,
  priceDecimals,
  paneDecimals,
  barsSource,
  overlaysSource,
  barsPath,
  overlaysPath,
  volumeKind,
}: {
  model: ChartModel;
  hidden: ReadonlySet<string>;
  priceDecimals: number | null;
  paneDecimals: ReadonlyMap<string, number>;
  barsSource: SourceNote;
  overlaysSource: SourceNote | null;
  barsPath: string;
  overlaysPath: string;
  volumeKind: string | null;
}) {
  const [tab, setTab] = useState<Tabs>("bars");
  const barRows: BarRow[] = model.bars.map((bar, index) => ({
    index,
    time: model.times[index] as number,
    bar,
  }));
  const barColumns: GridColumn<BarRow>[] = [
    {
      id: "time",
      header: "Open (UTC)",
      kind: "time",
      value: (r) => r.bar.t,
      cell: (r) => formatBarTime(r.time),
      width: 190,
      pin: true,
    },
    ...(["o", "h", "l", "c"] as const).map(
      (k): GridColumn<BarRow> => ({
        id: k,
        header: k.toUpperCase(),
        kind: "number",
        value: (r) => r.bar[k],
        cell: (r) => price(r.bar[k], priceDecimals),
      }),
    ),
    ...(model.hasVolume
      ? [
          {
            id: "v",
            header: volumeKind === "tick_volume" ? "Tick volume" : "Volume",
            kind: "number",
            value: (r: BarRow) => (typeof r.bar.v === "number" ? r.bar.v : null),
            cell: (r: BarRow) =>
              typeof r.bar.v === "number" ? (
                price(r.bar.v, 0)
              ) : (
                <span className="figure__missing">no data</span>
              ),
          } satisfies GridColumn<BarRow>,
        ]
      : []),
  ];

  const signalColumns: GridColumn<PlacedSignal>[] = [
    { id: "t", header: "Decided (UTC)", kind: "time", value: (r) => r.item.t, cell: (r) => formatTimestamp(r.item.t), width: 170, pin: true },
    { id: "side", header: "Side", value: (r) => r.item.side, width: 90 },
    { id: "outcome", header: "Outcome", value: (r) => r.item.outcome, width: 100 },
    { id: "strategy", header: "Strategy", value: (r) => r.item.strategy_id, width: 180 },
    { id: "price", header: "Requested price", figure: (r) => r.item.requested_price, chip: true, decimals: () => priceDecimals },
    { id: "reason", header: "Reason", value: (r) => r.item.reason, wrap: true, width: 320 },
    { id: "session", header: "Session", value: (r) => r.item.session_id, hidden: true },
    { id: "signal", header: "Signal id", value: (r) => r.item.signal_id, hidden: true },
  ];

  const fillColumns: GridColumn<PlacedFill>[] = [
    { id: "t", header: "Time (UTC)", kind: "time", value: (r) => r.item.t, cell: (r) => formatTimestamp(r.item.t), width: 170, pin: true },
    { id: "role", header: "Role", value: (r) => r.item.role, width: 80 },
    { id: "side", header: "Side", value: (r) => r.item.side, width: 80 },
    { id: "price", header: "Price", figure: (r) => r.item.price, chip: true, decimals: () => priceDecimals },
    {
      // Only exits carry the platform's net P&L; an entry has none to show.
      id: "pnl",
      header: "Net P&L",
      kind: "number",
      value: (r) => (r.item.net_pnl === null ? null : r.item.net_pnl.value),
      cell: (r) =>
        r.item.net_pnl === null ? (
          <span className="figure__missing">{r.item.role === "entry" ? "n/a (entry)" : "no data"}</span>
        ) : (
          <FigureValue figure={r.item.net_pnl} colourSign />
        ),
      width: 170,
    },
    { id: "trade", header: "Trade", value: (r) => r.item.trade_id, width: 150 },
    { id: "strategy", header: "Strategy", value: (r) => r.item.strategy_id, width: 180 },
    { id: "exit_reason", header: "Exit reason", value: (r) => r.item.exit_reason, width: 130 },
    { id: "session", header: "Session", value: (r) => r.item.session_id, hidden: true },
  ];

  const levelColumns: GridColumn<LevelOverlay>[] = [
    { id: "role", header: "Role", value: (r) => r.role, width: 90, pin: true },
    { id: "price", header: "Price", figure: (r) => r.price, chip: true, decimals: () => priceDecimals },
    { id: "from", header: "From (UTC)", kind: "time", value: (r) => r.from, cell: (r) => formatTimestamp(r.from), width: 170 },
    { id: "to", header: "To (UTC)", kind: "time", value: (r) => r.to, cell: (r) => (r.to ? formatTimestamp(r.to) : "open"), width: 170 },
    { id: "position", header: "Position", value: (r) => r.position_id, width: 160 },
    { id: "strategy", header: "Strategy", value: (r) => r.strategy_id, width: 180 },
  ];

  const regimeColumns: GridColumn<PlacedRegime>[] = [
    { id: "from", header: "From (UTC)", kind: "time", value: (r) => r.item.from, cell: (r) => formatTimestamp(r.item.from), width: 170, pin: true },
    { id: "to", header: "To (UTC)", kind: "time", value: (r) => r.item.to, cell: (r) => formatTimestamp(r.item.to), width: 170 },
    { id: "label", header: "Label", value: (r) => r.item.label, width: 90 },
    { id: "bars", header: "Bars", kind: "number", value: (r) => r.end - r.start + 1, width: 80 },
    { id: "key", header: "Regime key", value: (r) => r.item.regime_key, width: 260 },
    {
      id: "axes",
      header: "Axes",
      value: (r) =>
        Object.entries(r.item.axes)
          .map(([k, v]) => `${k}: ${v}`)
          .join(", "),
      wrap: true,
      width: 320,
    },
    { id: "classifier", header: "Classifier", value: (r) => r.item.classifier_fingerprint, hidden: true },
  ];

  const visibleSeries = model.series.filter((s) => !hidden.has(s.group));
  const seriesColumns: GridColumn<BarRow>[] = [
    barColumns[0] as GridColumn<BarRow>,
    ...visibleSeries.map(
      (s): GridColumn<BarRow> => ({
        id: s.id,
        header: s.name,
        kind: "number",
        value: (r) => s.values[r.index] ?? null,
        cell: (r) => {
          const v = s.values[r.index];
          return v === null || v === undefined ? (
            <span className="figure__missing">no data</span>
          ) : (
            price(v, s.pane === "price" ? priceDecimals : (paneDecimals.get(s.pane) ?? null))
          );
        },
        width: 150,
      }),
    ),
  ];

  const barsGridSource = { source: barsSource, path: barsPath };
  const overlaysGridSource = { source: overlaysSource, path: overlaysPath };
  const count = (n: number) => n.toLocaleString("en-GB");

  return (
    <section className="chart-data" data-testid="chart-data-panel" aria-label="Chart data">
      <TabsRoot value={tab} onValueChange={(v) => setTab(v as Tabs)}>
        <TabsList aria-label="Chart data tables">
          <Tab value="bars" data-testid="chart-data-tab-bars">
            Bars ({count(barRows.length)})
          </Tab>
          <Tab value="signals" data-testid="chart-data-tab-signals">
            Signals ({count(model.signals.length)})
          </Tab>
          <Tab value="fills" data-testid="chart-data-tab-fills">
            Fills ({count(model.fills.length)})
          </Tab>
          <Tab value="levels" data-testid="chart-data-tab-levels">
            Levels ({count(model.levels.length)})
          </Tab>
          <Tab value="regimes" data-testid="chart-data-tab-regimes">
            Regimes ({count(model.regimes.length)})
          </Tab>
          <Tab value="series" data-testid="chart-data-tab-series">
            Series ({count(visibleSeries.length)})
          </Tab>
        </TabsList>
        <TabsPanel value="bars">
          <DataGrid<BarRow>
            id="chart-bars"
            label="bars"
            rows={barRows}
            columns={barColumns}
            rowKey={(r) => r.bar.t}
            source={barsGridSource}
            maxHeight="100%"
            testId="chart-data-bars"
          />
        </TabsPanel>
        <TabsPanel value="signals">
          <DataGrid<PlacedSignal>
            id="chart-signals"
            label="signals"
            rows={model.signals}
            columns={signalColumns}
            rowKey={(r) => `${r.item.session_id}|${r.item.signal_id}|${r.item.t}`}
            source={overlaysGridSource}
            maxHeight="100%"
            testId="chart-data-signals"
          />
        </TabsPanel>
        <TabsPanel value="fills">
          <DataGrid<PlacedFill>
            id="chart-fills"
            label="fills"
            rows={model.fills}
            columns={fillColumns}
            rowKey={(r) => `${r.item.session_id}|${r.item.trade_id}|${r.item.role}`}
            source={overlaysGridSource}
            maxHeight="100%"
            testId="chart-data-fills"
          />
        </TabsPanel>
        <TabsPanel value="levels">
          <DataGrid<LevelOverlay>
            id="chart-levels"
            label="levels"
            rows={model.levels}
            columns={levelColumns}
            rowKey={(r) => `${r.position_id}|${r.role}`}
            source={overlaysGridSource}
            maxHeight="100%"
            testId="chart-data-levels"
          />
        </TabsPanel>
        <TabsPanel value="regimes">
          <DataGrid<PlacedRegime>
            id="chart-regimes"
            label="regime runs"
            rows={model.regimes}
            columns={regimeColumns}
            rowKey={(r) => r.item.from}
            source={overlaysGridSource}
            maxHeight="100%"
            testId="chart-data-regimes"
          />
        </TabsPanel>
        <TabsPanel value="series">
          <DataGrid<BarRow>
            id="chart-series"
            label="series values"
            rows={barRows}
            columns={seriesColumns}
            rowKey={(r) => r.bar.t}
            source={overlaysGridSource}
            maxHeight="100%"
            testId="chart-data-series"
          />
        </TabsPanel>
      </TabsRoot>
    </section>
  );
}
