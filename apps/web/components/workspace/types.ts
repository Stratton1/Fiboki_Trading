import type { PanelConfig } from "@/lib/workspace";

/**
 * What every panel body receives from its tile. A panel reads its own API and
 * renders; it decides nothing about the market. `symbol` is the instrument the
 * panel shows (its own, or the one its link group retargeted it to);
 * `onSymbol` publishes a selection to the panel's link group, and does
 * nothing for a panel that is not a source.
 */
export interface PanelProps {
  panel: PanelConfig;
  symbol: string | null;
  onSymbol: (symbol: string) => void;
  /** Persist small display settings on the panel (timeframe, filter, note text). */
  onSettings: (patch: Record<string, string | boolean | number>) => void;
  /** True while the panel is maximised (it may show more rows). */
  maximised: boolean;
  readOnly: boolean;
}

export function settingString(panel: PanelConfig, key: string, fallback: string): string {
  const value = panel.settings?.[key];
  return typeof value === "string" && value !== "" ? value : fallback;
}
