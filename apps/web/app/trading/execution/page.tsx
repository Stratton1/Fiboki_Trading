"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { useMemo } from "react";
import { GridPage } from "@/components/GridPage";
import { UrlState } from "@/components/UrlState";
import { ProvenanceChip } from "@/components/ProvenanceChip";
import { Distribution } from "@/components/charts";
import type { GridColumn } from "@/components/grid";
import { useApi } from "@/lib/query";
import { PROVENANCES, type InstrumentRow, type Page, type Provenance, type TradeRow } from "@/lib/types";
import { formatTimestamp, priceDecimals } from "@/lib/format";
import { deriveProvenance } from "@/lib/provenance";

/**
 * TRADING · Execution (the Journal's trade list).
 *
 * This replaces V1's `/trades`, which was titled "Paper / Backtest" and
 * contained zero paper trades, and which the dashboard re-rendered under
 * "Recent Execution" below a tile called "Fleet PnL (live)".
 *
 * Provenance is a COLUMN and a FILTER. The page title makes no claim about
 * where these trades came from, because the rows say it themselves. The
 * filter is URL state (`?provenance=paper`), so a filtered view can be linked,
 * and it sits OUTSIDE the async boundary, so changing it never unmounts it or
 * loses focus (report G W-18).
 *
 * Wave 3: the rows are a DataGrid (sort, filter, columns, keyboard, CSV with
 * provenance). Prices use the instrument's pip size from the platform when the
 * instrument list is available; otherwise the unitless default.
 */
const FILTERS = ["all", ...PROVENANCES] as const;
const filterParser = parseAsStringLiteral(FILTERS)
  .withDefault("all")
  .withOptions({ history: "replace" });

/** Closed trades change as the worker closes them: re-read every 30 s. */
const TRADES_REFRESH_MS = 30_000;

export default function ExecutionPage() {
  return (
    <UrlState fallback={<ExecutionView filter="all" onFilter={() => undefined} />}>
      <ExecutionFromUrl />
    </UrlState>
  );
}

function ExecutionFromUrl() {
  const [filter, setFilter] = useQueryState("provenance", filterParser);
  return <ExecutionView filter={filter} onFilter={(next) => void setFilter(next)} />;
}

function ExecutionView({
  filter,
  onFilter,
}: {
  filter: Provenance | "all";
  onFilter: (next: Provenance | "all") => void;
}) {
  const query = filter === "all" ? "" : `&provenance=${filter}`;
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments");
  const pips = useMemo(() => {
    const map = new Map<string, number | null>();
    if (instruments.status === "success") {
      for (const row of instruments.data.items) {
        map.set(row.symbol, priceDecimals(row.pip_size.value));
      }
    }
    return map;
  }, [instruments]);

  const columns: GridColumn<TradeRow>[] = [
    {
      id: "provenance",
      header: "Source",
      value: (row) => row.provenance,
      cell: (row) => <ProvenanceChip provenance={row.provenance} />,
      width: 96,
      pin: true,
    },
    { id: "trade_id", header: "Trade", value: (row) => row.trade_id, width: 120 },
    {
      id: "exit_time",
      header: "Closed",
      kind: "time",
      value: (row) => row.exit_time,
      cell: (row) => formatTimestamp(row.exit_time),
    },
    { id: "instrument", header: "Instrument", value: (row) => row.instrument, width: 100 },
    { id: "strategy", header: "Strategy", value: (row) => row.strategy_id, width: 180 },
    { id: "direction", header: "Side", value: (row) => row.direction.toUpperCase(), width: 72 },
    { id: "size", header: "Size", figure: (row) => row.size },
    {
      id: "entry_price",
      header: "Entry",
      figure: (row) => row.entry_price,
      decimals: (row) => pips.get(row.instrument) ?? null,
      hidden: true,
    },
    {
      id: "exit_price",
      header: "Exit price",
      figure: (row) => row.exit_price,
      decimals: (row) => pips.get(row.instrument) ?? null,
      hidden: true,
    },
    { id: "gross", header: "Gross P&L", figure: (row) => row.gross_pnl, signed: true, hidden: true },
    { id: "costs", header: "Costs", figure: (row) => row.costs, hidden: true },
    { id: "net", header: "Net P&L", figure: (row) => row.net_pnl, signed: true, width: 124 },
    { id: "r", header: "R", figure: (row) => row.r_multiple, signed: true, width: 88 },
    { id: "reason", header: "Exit", value: (row) => row.exit_reason, width: 120 },
    {
      id: "entry_time",
      header: "Opened",
      kind: "time",
      value: (row) => row.entry_time,
      cell: (row) => formatTimestamp(row.entry_time),
      hidden: true,
    },
  ];

  return (
    <GridPage<TradeRow>
      title="Execution"
      intro="Every closed trade the platform holds, from every source. The Source column is the truth; this heading makes no claim about it."
      path={`/api/trading/trades?limit=200${query}`}
      label="trades"
      gridId="trades"
      columns={columns}
      rowKey={(row) => row.trade_id}
      refreshMs={TRADES_REFRESH_MS}
      before={
        <div className="row mb-3">
          <label className="m-0" htmlFor="prov-filter">
            Filter by provenance
          </label>
          <select
            id="prov-filter"
            data-testid="provenance-filter"
            value={filter}
            onChange={(e) => onFilter(e.target.value as Provenance | "all")}
          >
            <option value="all">All sources (mixed)</option>
            {PROVENANCES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
      }
    >
      {(page) => {
        // Label the distribution from the values that are IN it: rows whose
        // R is null contribute nothing and so claim nothing. It is drawn over
        // the rows the platform returned, and says so when that is a page.
        const plotted = page.items.filter((t) => t.r_multiple.value !== null);
        return (
          <div className="card">
            <Distribution
              title={
                page.total > page.items.length
                  ? `R-multiple distribution (first ${page.items.length} of ${page.total})`
                  : "R-multiple distribution"
              }
              provenance={deriveProvenance(
                plotted.map((t) => t.r_multiple.provenance),
                "No trade in this result has an R-multiple, so nothing is plotted and nothing is labelled.",
              )}
              unit="R"
              values={plotted
                .map((t) => t.r_multiple.value)
                .filter((v): v is number => v !== null)}
            />
          </div>
        );
      }}
    </GridPage>
  );
}
