"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { formatTimestamp } from "@/lib/format";
import type { ExperimentRow } from "@/lib/types";

/** RESEARCH · Experiments: the append-only ledger, including the failures. */
export default function ExperimentsPage() {
  const columns: Column<ExperimentRow>[] = [
    { key: "id", header: "Experiment", cell: (row) => <span className="mono">{row.experiment_id}</span> },
    { key: "strategy", header: "Strategy", cell: (row) => row.strategy_id },
    { key: "actor", header: "Actor", cell: (row) => `${row.actor} (${row.actor_kind})` },
    { key: "outcome", header: "Outcome", cell: (row) => row.outcome },
    { key: "dataset", header: "Dataset", cell: (row) => <span className="mono">{row.dataset_version_id}</span> },
    { key: "created", header: "Created", cell: (row) => formatTimestamp(row.created_at) },
  ];
  return (
    <ListPage<ExperimentRow>
      title="Experiments"
      intro="Every experiment ever run, successful or not. An empty table here means the ledger is not attached — read the source badge before concluding nothing has been tried."
      path="/api/research/experiments"
      label="experiments"
      columns={columns}
      rowKey={(row) => row.experiment_id}
      emptyTitle="No experiments recorded"
      emptyBody="Check the source badge above: if it reads ABSENT, the ledger is not attached to this deployment and nothing can be concluded from the absence of rows."
    />
  );
}
