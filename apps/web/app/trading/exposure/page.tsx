"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import { BarChart } from "@/components/charts";
import type { ExposureRow } from "@/lib/types";

/** TRADING · Exposure: notional against the versioned limit set. */
export default function ExposurePage() {
  const columns: Column<ExposureRow>[] = [
    { key: "label", header: "Bucket", cell: (row) => row.label },
    { key: "kind", header: "Kind", cell: (row) => row.key.split(":")[0] ?? "" },
    {
      key: "exposure",
      header: "Exposure",
      cell: (row) => <FigureValue figure={row.exposure_pct} showChip={false} />,
    },
    {
      key: "limit",
      header: "Limit",
      cell: (row) => <FigureValue figure={row.limit_pct} showChip={false} />,
    },
    {
      key: "util",
      header: "Utilisation",
      cell: (row) => <FigureValue figure={row.utilisation_pct} showChip={false} />,
    },
    {
      key: "breached",
      header: "State",
      cell: (row) => (
        <span className={`badge badge--${row.breached ? "down" : "ok"}`}>
          {row.breached ? "BREACHED" : "WITHIN"}
        </span>
      ),
    },
  ];

  return (
    <ListPage<ExposureRow>
      title="Exposure"
      intro="Notional exposure per instrument, strategy and currency, against the limit set actually in force."
      path="/api/trading/exposure"
      label="exposure buckets"
      columns={columns}
      rowKey={(row) => row.key}
    >
      {(page) => (
        <div className="card">
          <BarChart
            title="Utilisation against limit"
            provenance={page.items[0]?.exposure_pct.provenance ?? "paper"}
            unit="pct"
            bars={page.items.slice(0, 16).map((row) => ({
              label: row.label,
              value: row.exposure_pct.value,
              limit: row.limit_pct.value,
            }))}
          />
        </div>
      )}
    </ListPage>
  );
}
