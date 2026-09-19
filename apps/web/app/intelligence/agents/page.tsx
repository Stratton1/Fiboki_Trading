"use client";

import { ListPage, type Column } from "@/components/ListPage";
import type { AgentRoleRow } from "@/lib/types";

/**
 * INTELLIGENCE · Agents.
 *
 * The "Can execute" column is always NO, for every role, and that is structural
 * rather than a policy this page applies: no execution capability exists in
 * fiboki.agents.capabilities to be granted.
 */
export default function AgentsPage() {
  const columns: Column<AgentRoleRow>[] = [
    { key: "role", header: "Role", cell: (row) => <strong>{row.role}</strong> },
    { key: "purpose", header: "Purpose", cell: (row) => row.purpose, wrap: true },
    {
      key: "caps",
      header: "Capabilities",
      cell: (row) => <span className="mono">{row.capabilities.join(", ") || "—"}</span>,
      wrap: true,
    },
    {
      key: "exec",
      header: "Can execute",
      cell: () => <span className="badge badge--ok">NO</span>,
    },
  ];
  return (
    <ListPage<AgentRoleRow>
      title="Agents"
      intro="The specialist roles and exactly what each may do. No role holds an execution capability — the capability does not exist to be granted."
      path="/api/intelligence/agents"
      label="agent roles"
      columns={columns}
      rowKey={(row) => row.role}
    />
  );
}
