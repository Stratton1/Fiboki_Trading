"use client";

import { PulseMonitor } from "@/components/monitors/PulseMonitor";
import { QUOTE_WINDOWS, type QuoteWindow } from "@/lib/monitors";
import { settingString, type PanelProps } from "../types";

/** PULSE in a tile: a mover publishes its symbol to the link group. */
export default function PulsePanel({ panel, onSymbol, onSettings, maximised }: PanelProps) {
  const raw = settingString(panel, "window", "24h");
  const window: QuoteWindow = (QUOTE_WINDOWS as readonly string[]).includes(raw) ? (raw as QuoteWindow) : "24h";
  return <PulseMonitor window={window} onWindow={(w) => onSettings({ window: w })} onSymbol={onSymbol} compact={!maximised} />;
}
