"use client";

import type { ReactNode } from "react";
import { Popover } from "@/components/ui/Popover";
import type { DefinitionView } from "@/lib/research";

/**
 * A metric's label with its definition and formula one click (or Enter) away
 * (feature 8). The text comes from the API (research/tearsheet.py
 * DEFINITIONS), so the page and the number it labels cannot drift apart.
 */
export function MetricLabel({
  label,
  definition,
  source,
  testId,
}: {
  label: ReactNode;
  definition: DefinitionView | { definition: string; formula: string; label?: string } | null | undefined;
  /** Where this particular number came from (engine metrics, gate report…). */
  source?: string;
  testId?: string;
}) {
  if (!definition) return <span className="rs-metric">{label}</span>;
  return (
    <Popover
      title={typeof label === "string" ? label : (definition as DefinitionView).label}
      testId={testId ? `${testId}-definition` : "metric-definition"}
      trigger={
        <button type="button" className="rs-metric rs-metric--def" data-testid={testId}>
          {label}
          <span className="rs-metric__i" aria-hidden="true">
            ?
          </span>
          <span className="sr-only">: definition</span>
        </button>
      }
    >
      <div className="rs-def">
        <p>{definition.definition}</p>
        <code className="rs-def__formula">{definition.formula}</code>
        {source ? <p className="rs-def__source">Source: {source}</p> : null}
      </div>
    </Popover>
  );
}
