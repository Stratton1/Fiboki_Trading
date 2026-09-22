"use client";

import { ListPage, type Column } from "@/components/ListPage";

interface RunRow {
  [key: string]: unknown;
}

/** INTELLIGENCE · Runs: no orchestrator attached is a stated fact, not silence. */
export default function RunsPage() {
  const columns: Column<RunRow>[] = [
    { key: "run", header: "Run", cell: (row) => String(row.run_id ?? "—") },
    { key: "status", header: "Status", cell: (row) => String(row.status ?? "—") },
  ];
  return (
    <ListPage<RunRow>
      title="Runs"
      intro="Agent workflow runs and their outcomes."
      path="/api/intelligence/runs"
      label="agent runs"
      columns={columns}
      rowKey={(row) => String(row.run_id ?? Math.random())}
      emptyTitle="No agent runs recorded"
      emptyBody="Read the source badge: if it says ABSENT, no orchestrator is attached to this deployment. No run recorded is not the same as every run succeeding."
    />
  );
}
