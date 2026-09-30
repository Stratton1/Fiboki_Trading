"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { RegimeGrid } from "@/components/markets/RegimeGrid";
import type { RegimeRow } from "@/lib/types";

/**
 * MARKETS · Regimes: unknown is rendered as unknown, never as "ranging".
 * Above the table, the same rows as a grid of heat cells, unknown hatched
 * (components/markets/RegimeGrid.tsx); the table now shows persistence too,
 * which the API always sent.
 */
export default function RegimesPage() {
  const columns: Column<RegimeRow>[] = [
    { key: "instrument", header: "Instrument", cell: (row) => <strong>{row.instrument}</strong> },
    {
      key: "available",
      header: "State",
      cell: (row) => (
        <span className={`badge badge--${row.available ? "ok" : "unknown"}`}>
          {row.available ? "CLASSIFIED" : "UNKNOWN"}
        </span>
      ),
    },
    { key: "vol", header: "Volatility", cell: (row) => row.volatility ?? "—" },
    { key: "dir", header: "Direction", cell: (row) => row.direction ?? "—" },
    { key: "liq", header: "Liquidity", cell: (row) => row.liquidity ?? "—" },
    { key: "stress", header: "Stress", cell: (row) => row.stress ?? "—" },
    { key: "persistence", header: "Persistence", cell: (row) => row.persistence ?? "—" },
    { key: "detail", header: "Detail", cell: (row) => row.detail, wrap: true },
  ];
  return (
    <ListPage<RegimeRow>
      title="Regimes"
      intro="The regime vector per instrument. Without bar history a regime is UNKNOWN, and any strategy gated on regime cannot be evaluated."
      path="/api/markets/regimes"
      label="regimes"
      columns={columns}
      rowKey={(row) => row.instrument}
    >
      {(page) => <RegimeGrid rows={page.items} />}
    </ListPage>
  );
}
