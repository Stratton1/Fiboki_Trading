"use client";

import { AlertsMonitor } from "@/components/monitors/AlertsMonitor";
import type { PanelProps } from "../types";

/** NTF in a tile: notifications (rules too when maximised); an instrument publishes to the link group. */
export default function AlertsPanel({ onSymbol, maximised, readOnly }: PanelProps) {
  return <AlertsMonitor onSymbol={onSymbol} compact={!maximised} readOnly={readOnly} />;
}
