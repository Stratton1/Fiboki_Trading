import type { Provenance } from "./types";
import { PROVENANCE_EXECUTED } from "./format";

/**
 * The price chart's colours, read from the design tokens in app/globals.css.
 *
 * The canvas cannot read a CSS custom property, and Lightweight Charts parses
 * colours itself (hex, rgb/rgba, named), not OKLCH. So each token is read
 * from the computed style of <html> (custom properties compute with their
 * var() references substituted), and turned into an sRGB string by the
 * browser's own colour engine: the opaque part is painted onto a 1×1 canvas
 * and read back, the alpha is taken from the token. The browser therefore
 * decides gamut mapping exactly as it does for the rest of the page.
 *
 * This module lives in lib/ rather than components/ because it necessarily
 * WRITES `rgba(...)` strings, which the source rule banning hard-coded colours
 * in components and pages would (rightly) flag. Nothing here is a colour
 * choice: every value is a token's value at the moment it is read. Re-read on
 * every change of `data-theme` or `data-pnl` (PriceChart does).
 */

export const SERIES_ROLES = [
  "series-1",
  "series-2",
  "series-3",
  "series-4",
  "series-5",
  "series-6",
] as const;
export type SeriesRole = (typeof SERIES_ROLES)[number];

export type RegimeLabel = "trend" | "range" | "stress" | "unknown";

export interface ChartTheme {
  background: string;
  text: string;
  textMuted: string;
  grid: string;
  axis: string;
  crosshair: string;
  crosshairLabel: string;
  candle: string;
  volume: string;
  series: Record<SeriesRole, string>;
  /** Marker ink per provenance: the neutral simulated hue, or the mode hue. */
  provenance: Record<Provenance, string>;
  pnlUp: string;
  pnlDown: string;
  /** A trade whose P&L the platform did not supply. */
  neutral: string;
  regimeTint: Record<RegimeLabel, string>;
  /** The same hue, near-opaque, for the 3 px ribbon under the price pane. */
  regimeRibbon: Record<RegimeLabel, string>;
  fontUi: string;
  fontCode: string;
}

/** The token each provenance's marker ink is read from (mirrors `.chart-ink` in globals.css). */
export const PROVENANCE_INK: Record<Provenance, string> = {
  backtest: "--prov-sim",
  walkforward: "--prov-sim",
  out_of_sample: "--prov-sim",
  holdout: "--prov-sim",
  paper: "--prov-paper",
  shadow: "--prov-shadow",
  broker_demo: "--prov-demo",
  broker_live: "--mode-live",
};

/** Hollow for simulated evidence, filled for executed (report E §4.4). */
export function markerFill(provenance: Provenance): "filled" | "hollow" {
  return PROVENANCE_EXECUTED[provenance] ? "filled" : "hollow";
}

let probe: CanvasRenderingContext2D | null = null;

function probeContext(): CanvasRenderingContext2D | null {
  if (probe) return probe;
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  probe = canvas.getContext("2d", { willReadFrequently: true });
  return probe;
}

/** Split `oklch(L C H / A)` (or any `fn(... / A)`) into its opaque form and alpha. */
export function splitAlpha(css: string): { opaque: string; alpha: number } {
  const match = /^([a-z-]+\()(.*?)\s*\/\s*([0-9.]+)(%?)\s*\)$/i.exec(css.trim());
  if (!match) return { opaque: css.trim(), alpha: 1 };
  const [, fn, body, raw, pct] = match;
  const value = Number(raw);
  const alpha = Number.isFinite(value) ? (pct ? value / 100 : value) : 1;
  return { opaque: `${fn}${body})`, alpha: Math.min(1, Math.max(0, alpha)) };
}

/** A CSS colour as an sRGB `rgba()` string the chart library can parse. */
export function toRgba(css: string, alphaOverride?: number): string {
  const { opaque, alpha } = splitAlpha(css);
  const a = alphaOverride ?? alpha;
  const ctx = probeContext();
  if (!ctx || opaque === "") return `rgba(0, 0, 0, ${a})`;
  ctx.clearRect(0, 0, 1, 1);
  // An unparseable value leaves the previous fillStyle in place; reset first
  // so a bad token paints transparent rather than the last good colour.
  ctx.fillStyle = "rgba(0, 0, 0, 0)";
  ctx.fillStyle = opaque;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b, painted] = ctx.getImageData(0, 0, 1, 1).data;
  const alphaOut = painted === 0 ? 0 : a;
  return `rgba(${r}, ${g}, ${b}, ${Number(alphaOut.toFixed(3))})`;
}

/** Re-apply a different alpha to an `rgba()` string produced by `toRgba`. */
export function withAlpha(rgba: string, alpha: number): string {
  const match = /^rgba\((\d+),\s*(\d+),\s*(\d+),\s*[0-9.]+\)$/.exec(rgba);
  if (!match) return rgba;
  return `rgba(${match[1]}, ${match[2]}, ${match[3]}, ${alpha})`;
}

function token(style: CSSStyleDeclaration, name: string): string {
  return style.getPropertyValue(name).trim();
}

/** Read every chart colour from the tokens in force on <html> right now. */
export function readChartTheme(root: HTMLElement = document.documentElement): ChartTheme {
  const style = getComputedStyle(root);
  const colour = (name: string, alpha?: number) => toRgba(token(style, name), alpha);
  const series = Object.fromEntries(
    SERIES_ROLES.map((role) => [role, colour(`--${role}`)]),
  ) as Record<SeriesRole, string>;
  const provenance = Object.fromEntries(
    (Object.keys(PROVENANCE_INK) as Provenance[]).map((p) => [p, colour(PROVENANCE_INK[p])]),
  ) as Record<Provenance, string>;
  const tint = (label: RegimeLabel) => colour(`--regime-${label}`);
  const regimeTint: Record<RegimeLabel, string> = {
    trend: tint("trend"),
    range: tint("range"),
    stress: tint("stress"),
    unknown: tint("unknown"),
  };
  const regimeRibbon = Object.fromEntries(
    (Object.keys(regimeTint) as RegimeLabel[]).map((label) => [
      label,
      withAlpha(regimeTint[label], 0.85),
    ]),
  ) as Record<RegimeLabel, string>;
  return {
    background: colour("--chart-bg"),
    text: colour("--fg"),
    textMuted: colour("--fg-muted"),
    grid: colour("--chart-grid"),
    axis: colour("--chart-axis"),
    crosshair: colour("--chart-crosshair"),
    crosshairLabel: colour("--bg-overlay"),
    candle: colour("--chart-candle"),
    volume: colour("--chart-volume"),
    series,
    provenance,
    pnlUp: colour("--pnl-up"),
    pnlDown: colour("--pnl-down"),
    neutral: colour("--fg-subtle"),
    regimeTint,
    regimeRibbon,
    fontUi: token(style, "--font-ui") || "system-ui, sans-serif",
    fontCode: token(style, "--font-code") || "ui-monospace, monospace",
  };
}
