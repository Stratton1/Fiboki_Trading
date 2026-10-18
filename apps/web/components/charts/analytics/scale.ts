/**
 * Drawing geometry for the analytics charts: value to pixel, tick placement,
 * tick labels. Presentation only; no statistic is computed here (every value
 * drawn arrives computed from the API).
 */

export type Scale = ((v: number) => number) & { domain: [number, number]; range: [number, number] };

export function linear(domain: [number, number], range: [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  const fn = ((v: number) => r0 + ((v - d0) / span) * (r1 - r0)) as Scale;
  fn.domain = domain;
  fn.range = range;
  return fn;
}

/** Round tick values covering [min, max], about `count` of them. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  const raw = (max - min) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    out.push(Number(v.toFixed(10)));
  }
  return out;
}

/** A padded [min, max] for the values, optionally forced to include `include`. */
export function extent(values: (number | null | undefined)[], include?: number): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (v === null || v === undefined || !Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (include !== undefined) {
    lo = Math.min(lo, include);
    hi = Math.max(hi, include);
  }
  if (!Number.isFinite(lo)) return [0, 1];
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.05 || 1;
    return [lo - pad, hi + pad];
  }
  const pad = (hi - lo) * 0.04;
  return [lo - pad, hi + pad];
}

const DAY = 86_400_000;
const fmtDay = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
const fmtMonth = new Intl.DateTimeFormat("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });
const fmtYear = new Intl.DateTimeFormat("en-GB", { year: "numeric", timeZone: "UTC" });
const fmtTime = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

/** Evenly spaced UTC time ticks with labels at a granularity that suits the span. */
export function timeTicks(from: number, to: number, count: number): { ms: number; label: string }[] {
  if (!(to > from)) return [{ ms: from, label: fmtDay.format(from) }];
  const span = to - from;
  const fmt = span > 3 * 365 * DAY ? fmtYear : span > 120 * DAY ? fmtMonth : span > 2 * DAY ? fmtDay : fmtTime;
  const out: { ms: number; label: string }[] = [];
  for (let i = 0; i <= count; i += 1) {
    const ms = from + (span * i) / count;
    out.push({ ms, label: fmt.format(ms) });
  }
  return out;
}

export const fmtDate = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** Index of the point whose time is nearest `ms` (times ascending). */
export function nearestIndex(times: number[], ms: number): number {
  if (times.length === 0) return -1;
  let lo = 0;
  let hi = times.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid]! < ms) lo = mid;
    else hi = mid;
  }
  return Math.abs(times[lo]! - ms) <= Math.abs(times[hi]! - ms) ? lo : hi;
}

/** A path through points, breaking at nulls (a gap is never drawn as data). */
export function linePath(xs: number[], ys: (number | null)[]): string {
  let d = "";
  let pen = false;
  for (let i = 0; i < xs.length; i += 1) {
    const y = ys[i];
    const x = xs[i];
    if (y === null || y === undefined || x === undefined || !Number.isFinite(y)) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  }
  return d;
}

/** A closed area between a line and a baseline y, breaking at nulls. */
export function areaPath(xs: number[], ys: (number | null)[], baseY: number): string {
  let d = "";
  let run: [number, number][] = [];
  const flush = () => {
    if (run.length > 1) {
      const first = run[0]!;
      const last = run[run.length - 1]!;
      d += `M${first[0].toFixed(1)},${baseY.toFixed(1)}`;
      for (const [x, y] of run) d += `L${x.toFixed(1)},${y.toFixed(1)}`;
      d += `L${last[0].toFixed(1)},${baseY.toFixed(1)}Z`;
    }
    run = [];
  };
  for (let i = 0; i < xs.length; i += 1) {
    const y = ys[i];
    const x = xs[i];
    if (y === null || y === undefined || x === undefined || !Number.isFinite(y)) flush();
    else run.push([x, y]);
  }
  flush();
  return d;
}

/** −5 … +5 for a value against a symmetric scale `span` (presentation only). */
export function divergingBin(value: number, span: number): number {
  if (!(span > 0) || !Number.isFinite(value)) return 0;
  const t = Math.max(-1, Math.min(1, value / span));
  return Math.round(t * 5);
}
