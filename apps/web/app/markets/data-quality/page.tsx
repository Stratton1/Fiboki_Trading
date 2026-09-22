"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import type { DataQualityRow } from "@/lib/types";

/** MARKETS · Data Quality: defects are detected, never silently repaired. */
export default function DataQualityPage() {
  const columns: Column<DataQualityRow>[] = [
    { key: "instrument", header: "Instrument", cell: (row) => <strong>{row.instrument}</strong> },
    {
      key: "quality",
      header: "Quality",
      cell: (row) => (
        <span className={`badge badge--${row.available ? "ok" : "unknown"}`}>
          {row.quality.toUpperCase()}
        </span>
      ),
    },
    { key: "bars", header: "Bars", cell: (row) => <FigureValue figure={row.bars} showChip={false} /> },
    { key: "gaps", header: "Gaps", cell: (row) => <FigureValue figure={row.gaps} showChip={false} /> },
    { key: "stale", header: "Stale runs", cell: (row) => <FigureValue figure={row.stale_runs} showChip={false} /> },
    { key: "detail", header: "Detail", cell: (row) => row.detail, wrap: true },
  ];
  return (
    <ListPage<DataQualityRow>
      title="Data Quality"
      intro="Per-instrument integrity. An unvalidated dataset makes every figure derived from it unverified, and that is stated rather than assumed away."
      path="/api/markets/data-quality"
      label="data quality rows"
      columns={columns}
      rowKey={(row) => row.instrument}
    />
  );
}
