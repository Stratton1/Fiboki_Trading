"use client";

import { WatchlistMonitor } from "@/components/monitors/WatchlistMonitor";
import { QUOTE_WINDOWS, type QuoteWindow } from "@/lib/monitors";
import { settingString, type PanelProps } from "../types";

/** WL in a tile: selecting a row publishes its symbol to the panel's link group. */
export default function WatchlistPanel({ panel, onSymbol, onSettings, maximised, readOnly }: PanelProps) {
  const list = settingString(panel, "watchlist", "Majors");
  const raw = settingString(panel, "window", "24h");
  const window: QuoteWindow = (QUOTE_WINDOWS as readonly string[]).includes(raw) ? (raw as QuoteWindow) : "24h";
  return (
    <WatchlistMonitor
      list={list}
      onList={(n) => onSettings({ watchlist: n })}
      window={window}
      onWindow={(w) => onSettings({ window: w })}
      onSymbol={onSymbol}
      selected={panel.symbol}
      compact={!maximised}
      readOnly={readOnly}
    />
  );
}
