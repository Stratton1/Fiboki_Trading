"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import type { StrategyRow } from "@/lib/types";

/** RESEARCH · Strategies: the registry, as declarative documents. */
export default function StrategiesPage() {
  const columns: Column<StrategyRow>[] = [
    { key: "name", header: "Name", cell: (row) => row.name },
    { key: "id", header: "Id", cell: (row) => <span className="mono">{row.strategy_id}</span> },
    { key: "family", header: "Family", cell: (row) => row.family },
    { key: "tf", header: "Timeframes", cell: (row) => row.timeframes.join(", ") },
    { key: "universe", header: "Universe", cell: (row) => `${row.universe.length} instrument(s)` },
    { key: "rules", numeric: true, header: "Rules", cell: (row) => <FigureValue figure={row.rule_count} showChip={false} /> },
    { key: "params", numeric: true, header: "Parameters", cell: (row) => <FigureValue figure={row.parameter_count} showChip={false} /> },
    { key: "complexity", numeric: true, header: "Complexity", cell: (row) => <FigureValue figure={row.complexity} showChip={false} /> },
    { key: "hash", header: "Content hash", cell: (row) => <span className="mono">{row.content_hash}</span> },
  ];
  return (
    <ListPage<StrategyRow>
      title="Strategies"
      intro="Every registered strategy document, with the content hash that ties a result back to the exact declaration that produced it."
      path="/api/research/strategies"
      label="strategies"
      columns={columns}
      rowKey={(row) => row.strategy_id}
    />
  );
}
