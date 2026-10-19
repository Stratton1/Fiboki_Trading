import { readChartTheme } from "@/lib/chart-theme";

/**
 * Compose a chart snapshot: the chart as the library drew it (panes, axes,
 * overlays, drawings) above a band carrying the API-attested provenance stamp
 * (symbol, timeframe, dataset version, build sha, UTC time, mode), so a PNG
 * that leaves the workstation still says what it shows and where from.
 * Loaded only when an operator takes a snapshot. Colours are the chart's
 * tokens at the moment of the snapshot; nothing here picks a colour.
 */
export async function composeSnapshot(
  chart: HTMLCanvasElement,
  stamp: string,
): Promise<{ blob: Blob; width: number; height: number }> {
  const theme = readChartTheme();
  const scale = window.devicePixelRatio || 1;
  const band = Math.round(22 * scale);
  const out = document.createElement("canvas");
  out.width = chart.width;
  out.height = chart.height + band;
  const ctx = out.getContext("2d");
  if (!ctx) throw new Error("No 2D canvas context for the snapshot.");
  ctx.fillStyle = theme.background;
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(chart, 0, 0);
  ctx.fillStyle = theme.grid;
  ctx.fillRect(0, chart.height, out.width, Math.max(1, Math.round(scale)));
  ctx.fillStyle = theme.text;
  ctx.font = `${Math.round(11 * scale)}px ${theme.fontCode}`;
  ctx.textBaseline = "middle";
  ctx.fillText(stamp, Math.round(6 * scale), chart.height + band / 2, out.width - 12 * scale);
  const blob = await new Promise<Blob>((resolve, reject) =>
    out.toBlob((b) => (b ? resolve(b) : reject(new Error("The snapshot could not be encoded."))), "image/png"),
  );
  return { blob, width: out.width, height: out.height };
}

/** `fiboki_EURUSD_H1_20261019T023000Z.png` from the stamp's UTC time. */
export function snapshotName(symbol: string, tf: string, generatedAt: string): string {
  const compact = generatedAt.replace(/[-:]/g, "").replace(/\.\d+/, "").replace("+0000", "Z");
  return `fiboki_${symbol}_${tf}_${compact.endsWith("Z") ? compact : `${compact}Z`}.png`;
}
