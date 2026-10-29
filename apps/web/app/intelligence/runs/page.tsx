"use client";

import { ListPage, type Column } from "@/components/ListPage";
import type { AgentRunView } from "@/lib/types";

/** INTELLIGENCE · Runs: a missing ledger is a stated fact, not silence. */
export default function RunsPage() {
  const columns: Column<AgentRunView>[] = [
    { key: "when", header: "When", cell: (row) => row.recorded_at },
    { key: "workflow", header: "Workflow", cell: (row) => row.workflow ?? row.workflow_reason ?? "—" },
    { key: "role", header: "Role", cell: (row) => row.role },
    { key: "outcome", header: "Outcome", cell: (row) => row.outcome },
    { key: "model", header: "Model", cell: (row) => row.model_id ?? row.model_id_reason ?? "—" },
    { key: "tier", header: "Tier", cell: (row) => row.tier },
  ];
  return (
    <ListPage<AgentRunView>
      title="Runs"
      intro="Agent workflow runs and their outcomes. The 20-run acceptance protocol is still open."
      path="/api/intelligence/runs"
      label="agent runs"
      columns={columns}
      rowKey={(row, index) =>
        row.workflow ? `${row.workflow}:${row.recorded_at}` : `row:${index}`
      }
      emptyTitle="No agent runs recorded"
      emptyBody="Read the source badge. ABSENT means the agent audit ledger is not on disk. No run recorded is not the same as every run succeeding."
    />
  );
}
