"use client";

import { ListPage, type Column } from "@/components/ListPage";
import type { DatasetRow } from "@/lib/types";

/** RESEARCH · Datasets: content-addressed versions and their lineage. */
export default function DatasetsPage() {
  const columns: Column<DatasetRow>[] = [
    { key: "short", header: "Version", cell: (row) => <span className="mono">{row.short_id}</span> },
    { key: "id", header: "Full id", cell: (row) => <span className="mono">{row.version_id}</span> },
    { key: "describe", header: "Description", cell: (row) => row.describe, wrap: true },
  ];
  return (
    <ListPage<DatasetRow>
      title="Datasets"
      intro="Every stored result references a dataset version id, so the exact bytes behind a number can be re-resolved."
      path="/api/research/datasets"
      label="dataset versions"
      columns={columns}
      rowKey={(row) => row.version_id}
      emptyTitle="No dataset versions"
      emptyBody="If the source badge reads ABSENT, no market-data root is mounted and research run against this deployment has no data lineage."
    />
  );
}
