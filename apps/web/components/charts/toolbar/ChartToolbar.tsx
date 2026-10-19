"use client";

import type { ChartTemplateBody } from "@/lib/chart-contract";
import { TemplateMenu } from "./TemplateMenu";

/**
 * The chart workstation's tool row (Wave 2B), loaded after first paint as one
 * chunk: templates, the layer toolbar, go-to-date and the display time zone,
 * and the snapshot. None of it is in the chart route's first-load JavaScript.
 */
export interface ChartToolbarProps {
  template: ChartTemplateBody;
  onApplyTemplate: (name: string, template: ChartTemplateBody) => void;
}

export default function ChartToolbar({ template, onApplyTemplate }: ChartToolbarProps) {
  return (
    <div className="cw-tools" role="group" aria-label="Chart tools" data-testid="chart-tools">
      <TemplateMenu current={template} onApply={onApplyTemplate} />
    </div>
  );
}
