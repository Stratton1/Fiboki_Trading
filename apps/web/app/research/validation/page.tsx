"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import { ToneBadge } from "@/components/primitives";
import { RungMeter } from "@/components/research/RungMeter";
import { verdictTone } from "@/lib/tones";
import type { ValidationRow } from "@/lib/types";

/**
 * RESEARCH · Validation: the ladder's verdict, and what was binding. The
 * Ladder column is a rung meter (components/research/RungMeter.tsx): the
 * rungs passed, the rung it stopped at and the binding constraint, from the
 * summary's own fields.
 */
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
    { key: "ladder", header: "Ladder", cell: (row) => <RungMeter row={row} />, wrap: true },
    { key: "passed", header: "Rungs passed", numeric: true, cell: (row) => <FigureValue figure={row.rungs_passed} showChip={false} /> },
    // Every rung the report records, reached or not (validation/ladder.py
    // appends `not_reached` after the ladder stops): the ladder's length.
    { key: "total", header: "Rungs in report", numeric: true, cell: (row) => <FigureValue figure={row.rungs_total} showChip={false} /> },
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
