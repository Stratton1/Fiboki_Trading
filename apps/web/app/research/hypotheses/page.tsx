"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import type { HypothesisRow } from "@/lib/types";

/** RESEARCH · Hypotheses: what each strategy claims, before any number. */
export default function HypothesesPage() {
  const columns: Column<HypothesisRow>[] = [
    { key: "id", header: "Strategy", cell: (row) => <span className="mono">{row.strategy_id}</span> },
    { key: "family", header: "Family", cell: (row) => row.family },
    { key: "statement", header: "Hypothesis", cell: (row) => row.statement, wrap: true },
    { key: "keywords", header: "Structure", cell: (row) => row.structural_keywords.join(" · "), wrap: true },
    {
      key: "support",
      header: "Supporting experiments",
      cell: (row) => <FigureValue figure={row.supporting_experiments} />,
    },
  ];
  return (
    <ListPage<HypothesisRow>
      title="Hypotheses"
      intro="The claim each strategy makes about the market, and how much recorded evidence stands behind it."
      path="/api/research/hypotheses"
      label="hypotheses"
      columns={columns}
      rowKey={(row) => row.strategy_id}
    />
  );
}
