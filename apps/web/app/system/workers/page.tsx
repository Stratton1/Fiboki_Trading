"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import type { WorkerRow } from "@/lib/types";

/** SYSTEM · Workers: "never started" and "running quietly" are different rows. */
export default function WorkersPage() {
  const columns: Column<WorkerRow>[] = [
    { key: "name", header: "Worker", cell: (row) => <strong className="mono">{row.name}</strong> },
    {
      key: "state",
      header: "State",
      cell: (row) => (
        <span
          className={`badge badge--${row.state === "running" ? "ok" : row.state === "stale" ? "degraded" : "down"}`}
          data-testid="worker-state"
        >
          {row.state.replace("_", " ").toUpperCase()}
        </span>
      ),
    },
    { key: "age", header: "Heartbeat age", cell: (row) => <FigureValue figure={row.heartbeat_age} /> },
    { key: "detail", header: "Detail", cell: (row) => row.detail, wrap: true },
  ];
  return (
    <ListPage<WorkerRow>
      title="Workers"
      intro="Heartbeat age per worker. A worker that has never started shows 'no data', not zero seconds."
      path="/api/system/workers"
      label="workers"
      columns={columns}
      rowKey={(row) => row.name}
    />
  );
}
