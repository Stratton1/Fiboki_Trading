"use client";

import type { ChartTemplateBody } from "@/lib/chart-contract";
import type { TimeZoneId } from "../time";
import type { ChartLayers, LayerName } from "../layers";
import { GoToDate, TimeZoneSelect } from "./GoToDate";
import { LayerToolbar } from "./LayerToolbar";
import { SnapshotButton } from "./SnapshotButton";
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
  layers: ChartLayers;
  onLayers: (update: (layers: ChartLayers) => ChartLayers) => void;
  counts: Partial<Record<LayerName, number | null>>;
  symbol: string;
  tf: string;
  datasetVersion: string | null;
  capture: () => HTMLCanvasElement | null;
}

export default function ChartToolbar({
  template,
  onApplyTemplate,
  tz,
  onTz,
  at,
  onGoTo,
  onLatest,
  layers,
  onLayers,
  counts,
  symbol,
  tf,
  datasetVersion,
  capture,
}: ChartToolbarProps) {
  return (
    <div className="cw-tools" role="group" aria-label="Chart tools" data-testid="chart-tools">
      <LayerToolbar layers={layers} onLayers={onLayers} counts={counts} />
      <GoToDate tz={tz} at={at} onGoTo={onGoTo} onLatest={onLatest} />
      <TimeZoneSelect tz={tz} onTz={onTz} />
      <TemplateMenu current={template} onApply={onApplyTemplate} />
      <SnapshotButton symbol={symbol} tf={tf} datasetVersion={datasetVersion} capture={capture} />
    </div>
  );
}
