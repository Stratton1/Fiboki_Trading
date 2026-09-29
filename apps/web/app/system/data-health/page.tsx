"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import type { ServiceRow } from "@/lib/types";

/** SYSTEM · Data Health: which data sources are real, seeded, or absent. */
export default function DataHealthPage() {
  const columns: Column<ServiceRow>[] = [
    { key: "name", header: "Source", cell: (row) => <strong className="mono">{row.name}</strong> },
    {
      key: "kind",
      header: "Kind",
      cell: (row) => (
        <span
          className={`badge badge--${row.kind === "live" ? "ok" : row.kind === "seed" ? "degraded" : "down"}`}
          data-testid="data-kind"
        >
          {row.kind.toUpperCase()}
        </span>
      ),
    },
    { key: "latency", numeric: true, header: "Latency", cell: (row) => <FigureValue figure={row.latency} showChip={false} /> },
    { key: "detail", header: "Detail", cell: (row) => row.detail, wrap: true },
  ];
  return (
    <ListPage<ServiceRow>
      title="Data Health"
      intro="Whether each source is a real measurement, a deterministic fixture, or absent. A fixture is labelled SEED everywhere it surfaces."
      path="/api/system/data-health"
      label="data sources"
      columns={columns}
      rowKey={(row) => row.name}
    />
  );
}
