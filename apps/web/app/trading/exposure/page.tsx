"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import { BarChart } from "@/components/charts";
import { deriveProvenance } from "@/lib/provenance";
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
      {(page) => {
        const charted = page.items.slice(0, 16);
        return (
          <div className="card">
            <BarChart
              title="Utilisation against limit"
              provenance={deriveProvenance(
                charted.flatMap((row) => [
                  row.exposure_pct.provenance,
                  row.limit_pct.provenance,
                ]),
              )}
              unit="pct"
              bars={charted.map((row) => ({
                label: row.label,
                value: row.exposure_pct.value,
                limit: row.limit_pct.value,
              }))}
            />
          </div>
        );
      }}
    </ListPage>
  );
}
