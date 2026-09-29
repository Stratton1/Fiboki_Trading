"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import type { ServiceRow } from "@/lib/types";

/**
 * SYSTEM · Services.
 *
 * Every badge here is derived from a probe run at request time. V1 rendered
 * hardcoded "Online" and "Connected" badges, so an outage looked identical to a
 * healthy idle platform.
 */
export default function ServicesPage() {
  const columns: Column<ServiceRow>[] = [
    { key: "name", header: "Service", cell: (row) => <strong className="mono">{row.name}</strong> },
    {
      key: "health",
      header: "Health",
      cell: (row) => (
        <span
          className={`badge badge--${row.healthy ? "ok" : "down"}`}
          data-testid="service-health"
        >
          {row.healthy ? "HEALTHY" : "NOT HEALTHY"}
        </span>
      ),
    },
    {
      key: "kind",
      header: "Kind",
      cell: (row) => (
        <span className={`badge badge--${row.kind === "live" ? "ok" : row.kind === "seed" ? "degraded" : "down"}`}>
          {row.kind.toUpperCase()}
        </span>
      ),
    },
    { key: "latency", numeric: true, header: "Probe latency", cell: (row) => <FigureValue figure={row.latency} showChip={false} /> },
    { key: "detail", header: "Detail", cell: (row) => row.detail, wrap: true },
  ];
  return (
    <ListPage<ServiceRow>
      title="Services"
      intro="Probed when you loaded this page. Nothing here is a static badge — every row is the result of reaching for the thing it names."
      path="/api/system/services"
      label="services"
      columns={columns}
      rowKey={(row) => row.name}
    />
  );
}
