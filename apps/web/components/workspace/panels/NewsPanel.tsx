"use client";

import { NewsMonitor } from "@/components/monitors/NewsMonitor";
import { settingString, type PanelProps } from "../types";

/** NWS in a tile: follows the link group's symbol; a chart link publishes to the group. */
export default function NewsPanel({ panel, symbol, onSymbol, onSettings, maximised }: PanelProps) {
  const currencies = settingString(panel, "currencies", "").split(",").filter(Boolean);
  const source = settingString(panel, "source", "");
  const hours = Number(settingString(panel, "hours", "24")) || 24;
  return (
    <NewsMonitor
      filter={{ instrument: symbol, currencies, source: source || null, hours }}
      onFilter={(patch) => {
        if (patch.currencies !== undefined) onSettings({ currencies: patch.currencies.join(",") });
        if (patch.source !== undefined) onSettings({ source: patch.source ?? "" });
        if (patch.hours !== undefined) onSettings({ hours: String(patch.hours) });
      }}
      onSymbol={onSymbol}
      compact={!maximised}
    />
  );
}
