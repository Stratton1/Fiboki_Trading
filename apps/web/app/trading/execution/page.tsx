"use client";

import { useState } from "react";
import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import { ProvenanceChip } from "@/components/ProvenanceChip";
import { Distribution } from "@/components/charts";
import { PROVENANCES, type Provenance, type TradeRow } from "@/lib/types";
import { formatTimestamp } from "@/lib/format";
import { deriveProvenance } from "@/lib/provenance";

/**
 * TRADING · Execution.
 *
 * This replaces V1's `/trades`, which was titled "Paper / Backtest" and
 * contained zero paper trades, and which the dashboard re-rendered under
 * "Recent Execution" below a tile called "Fleet PnL (live)".
 *
 * Provenance is a COLUMN and a FILTER. The page title makes no claim about
 * where these trades came from, because the rows say it themselves.
 */
export default function ExecutionPage() {
  const [filter, setFilter] = useState<Provenance | "all">("all");
  const query = filter === "all" ? "" : `&provenance=${filter}`;

  const columns: Column<TradeRow>[] = [
    {
      key: "provenance",
      header: "Source",
      cell: (row) => <ProvenanceChip provenance={row.provenance} />,
    },
    { key: "exit_time", header: "Closed", cell: (row) => formatTimestamp(row.exit_time) },
    { key: "instrument", header: "Instrument", cell: (row) => row.instrument },
    { key: "strategy", header: "Strategy", cell: (row) => row.strategy_id },
    { key: "direction", header: "Side", cell: (row) => row.direction.toUpperCase() },
    { key: "size", header: "Size", cell: (row) => <FigureValue figure={row.size} showChip={false} /> },
    {
      key: "net",
      header: "Net P&L",
      cell: (row) => <FigureValue figure={row.net_pnl} showChip={false} colourSign />,
    },
    {
      key: "r",
      header: "R",
      cell: (row) => <FigureValue figure={row.r_multiple} showChip={false} colourSign />,
    },
    { key: "reason", header: "Exit", cell: (row) => row.exit_reason },
  ];

  return (
    <ListPage<TradeRow>
      title="Execution"
      intro="Every closed trade the platform holds, from every source. The Source column is the truth; this heading makes no claim about it."
      path={`/api/trading/trades?limit=200${query}`}
      label="trades"
      columns={columns}
      rowKey={(row) => row.trade_id}
    >
      {(page) => (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <label style={{ margin: 0 }} htmlFor="prov-filter">
              Filter by provenance
            </label>
            <select
              id="prov-filter"
              data-testid="provenance-filter"
              style={{ width: "auto" }}
              value={filter}
              onChange={(e) => setFilter(e.target.value as Provenance | "all")}
            >
              <option value="all">All sources (mixed)</option>
              {PROVENANCES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="card">
            {(() => {
              // Label the distribution from the values that are IN it: rows
              // whose R is null contribute nothing and so claim nothing.
              const plotted = page.items.filter((t) => t.r_multiple.value !== null);
              return (
                <Distribution
                  title="R-multiple distribution"
                  provenance={deriveProvenance(
                    plotted.map((t) => t.r_multiple.provenance),
                    "No trade in this result has an R-multiple, so nothing is plotted and nothing is labelled.",
                  )}
                  unit="R"
                  values={plotted
                    .map((t) => t.r_multiple.value)
                    .filter((v): v is number => v !== null)}
                />
              );
            })()}
          </div>
        </>
      )}
    </ListPage>
  );
}
