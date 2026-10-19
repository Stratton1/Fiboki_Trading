/**
 * The chart's display layers (Wave 2B layer toolbar and templates). Each is
 * show/hide only: hiding a layer changes what is drawn, never what the API
 * sent or what any table counts. The names are the API's template layers
 * (src/fiboki/api/chart_store.py LAYER_NAMES).
 */
export const LAYER_NAMES = [
  "drawings",
  "trades",
  "stops",
  "events",
  "indicators",
  "signals",
  "fills",
  "regimes",
] as const;
export type LayerName = (typeof LAYER_NAMES)[number];
export type ChartLayers = Record<LayerName, boolean>;

export const LAYER_LABEL: Record<LayerName, string> = {
  drawings: "Drawings",
  trades: "Trades",
  stops: "Stops / targets",
  events: "Events",
  indicators: "Indicators",
  signals: "Signals",
  fills: "Fills",
  regimes: "Regimes",
};

export const ALL_LAYERS_ON: ChartLayers = {
  drawings: true,
  trades: true,
  stops: true,
  events: true,
  indicators: true,
  signals: true,
  fills: true,
  regimes: true,
};

export const ALL_LAYERS_OFF: ChartLayers = {
  drawings: false,
  trades: false,
  stops: false,
  events: false,
  indicators: false,
  signals: false,
  fills: false,
  regimes: false,
};

export type { ChartTemplateBody, ChartTemplateRow, ChartTemplatesView, TradeSource } from "@/lib/chart-contract";
