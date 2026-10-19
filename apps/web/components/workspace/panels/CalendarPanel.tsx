"use client";

import { CalendarMonitor } from "@/components/monitors/CalendarMonitor";
import { settingString, type PanelProps } from "../types";

/** ECO in a tile: follows the link group's instrument; an event's chart link publishes to the group. */
export default function CalendarPanel({ panel, symbol, onSymbol, onSettings, maximised }: PanelProps) {
  const impacts = settingString(panel, "impact", "high").split(",").filter(Boolean);
  const days = Number(settingString(panel, "days", "7")) || 7;
  return (
    <CalendarMonitor
      filter={{ impacts, currencies: [], instrument: symbol, days }}
      onFilter={(patch) => {
        if (patch.impacts !== undefined) onSettings({ impact: patch.impacts.join(",") });
        if (patch.days !== undefined) onSettings({ days: String(patch.days) });
      }}
      onSymbol={onSymbol}
      compact={!maximised}
    />
  );
}
