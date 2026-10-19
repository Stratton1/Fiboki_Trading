"use client";

import type { ChartTemplateBody } from "@/lib/chart-contract";
import type { TimeZoneId } from "../time";
import { GoToDate, TimeZoneSelect } from "./GoToDate";
import { TemplateMenu } from "./TemplateMenu";

/**
 * The chart workstation's tool row (Wave 2B), loaded after first paint as one
 * chunk: templates, the layer toolbar, go-to-date and the display time zone,
 * and the snapshot. None of it is in the chart route's first-load JavaScript.
 */
export interface ChartToolbarProps {
  template: ChartTemplateBody;
  onApplyTemplate: (name: string, template: ChartTemplateBody) => void;
  tz: TimeZoneId;
  onTz: (tz: TimeZoneId) => void;
  at: string | null;
  onGoTo: (iso: string) => void;
  onLatest: () => void;
}

export default function ChartToolbar({
  template,
  onApplyTemplate,
  tz,
  onTz,
  at,
  onGoTo,
  onLatest,
}: ChartToolbarProps) {
  return (
    <div className="cw-tools" role="group" aria-label="Chart tools" data-testid="chart-tools">
      <GoToDate tz={tz} at={at} onGoTo={onGoTo} onLatest={onLatest} />
      <TimeZoneSelect tz={tz} onTz={onTz} />
      <TemplateMenu current={template} onApply={onApplyTemplate} />
    </div>
  );
}
