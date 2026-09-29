"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import { ToneBadge } from "@/components/primitives";
import { verdictTone } from "@/lib/tones";
import type { ValidationRow } from "@/lib/types";

/** RESEARCH · Validation: the ladder's verdict, and what was binding. */
export default function ValidationPage() {
  const columns: Column<ValidationRow>[] = [
    { key: "strategy", header: "Strategy", cell: (row) => <span className="mono">{row.strategy_id}</span> },
    {
      key: "verdict",
      header: "Verdict",
      // The tone is the verdict's own (lib/tones.ts): a REJECT is never green.
      cell: (row) => (
        <ToneBadge tone={verdictTone(row.verdict)} testId="verdict-badge" value={row.verdict} />
      ),
    },
    { key: "passed", header: "Rungs passed", numeric: true, cell: (row) => <FigureValue figure={row.rungs_passed} showChip={false} /> },
    { key: "total", header: "Rungs run", numeric: true, cell: (row) => <FigureValue figure={row.rungs_total} showChip={false} /> },
    { key: "gates", header: "Gate set", cell: (row) => <span className="mono">{row.gate_set_version || "—"}</span> },
    { key: "binding", header: "Binding constraint", cell: (row) => row.binding_constraint || "—", wrap: true },
    { key: "detail", header: "Detail", cell: (row) => row.detail, wrap: true },
  ];
  return (
    <ListPage<ValidationRow>
      title="Validation"
      intro="The promotion ladder's record. A strategy with no report is shown as not validated — that is not a pass, and it never renders as one."
      path="/api/research/validation"
      label="validation reports"
      columns={columns}
      rowKey={(row) => row.strategy_id}
    />
  );
}
