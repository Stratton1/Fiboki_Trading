/**
 * Geometry for operator drawings on the price chart: pure functions, no DOM.
 *
 * A drawing is an ANNOTATION. Its anchors are points an operator placed, in
 * (UTC seconds, price); everything here maps those points to and from the
 * chart's bar axis, measures the distance between two of them, or lays out the
 * standard Fibonacci ratios between two of them. None of it is an indicator,
 * a signal, a stop or a target, and nothing a strategy, the risk engine or a
 * backtest reads comes from here (the API's store is isolated by an AST test,
 * tests/unit/test_drawings_never_reach_a_decision.py).
 *
 * The bar axis is LOGICAL: bar i sits at i whatever the clock says, so a
 * weekend with no FX bars takes no room and 24/7 crypto is drawn the same
 * way. A time between two bars is interpolated between their indices; a time
 * beyond the last bar (a ray drawn into the future, a note to the right of the
 * last candle) is extrapolated at the last bar interval, so the drawing keeps
 * its place when the next bar arrives, when the operator zooms, and when the
 * timeframe changes.
 */

/** The standard retracement ratios, drawn between the two anchors. */
export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1] as const;
/** Optional extensions beyond the first anchor. */
export const FIB_EXTENSIONS = [1.272, 1.618] as const;

/** The spacing of the last two (or first two) bars, in seconds; 0 when unknown. */
function edgeInterval(times: readonly number[], end: "first" | "last"): number {
  const n = times.length;
  if (n < 2) return 0;
  const step =
    end === "last"
      ? (times[n - 1] as number) - (times[n - 2] as number)
      : (times[1] as number) - (times[0] as number);
  return step > 0 ? step : 0;
}

/**
 * A UTC time (seconds) as a fractional bar index. `times` ascending.
 * Exact bar times map to their index; times between bars interpolate; times
 * outside the window extrapolate at the edge interval. Null with no bars.
 */
export function timeToLogical(times: readonly number[], t: number): number | null {
  const n = times.length;
  if (n === 0 || !Number.isFinite(t)) return null;
  const first = times[0] as number;
  const last = times[n - 1] as number;
  if (t >= last) {
    const step = edgeInterval(times, "last");
    return step > 0 ? n - 1 + (t - last) / step : n - 1;
  }
  if (t <= first) {
    const step = edgeInterval(times, "first");
    return step > 0 ? (t - first) / step : 0;
  }
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if ((times[mid] as number) <= t) lo = mid;
    else hi = mid;
  }
  const a = times[lo] as number;
  const b = times[hi] as number;
  return b > a ? lo + (t - a) / (b - a) : lo;
}

/** The inverse of {@link timeToLogical}: a fractional bar index as UTC seconds. */
export function logicalToTime(times: readonly number[], logical: number): number | null {
  const n = times.length;
  if (n === 0 || !Number.isFinite(logical)) return null;
  if (logical >= n - 1) {
    return (times[n - 1] as number) + (logical - (n - 1)) * edgeInterval(times, "last");
  }
  if (logical <= 0) return (times[0] as number) + logical * edgeInterval(times, "first");
  const i = Math.floor(logical);
  const a = times[i] as number;
  const b = times[i + 1] as number;
  return a + (logical - i) * (b - a);
}

/** Snap a time to the nearest bar's open, when it is inside the window. */
export function snapTime(times: readonly number[], t: number): number {
  const logical = timeToLogical(times, t);
  if (logical === null || logical < 0 || logical > times.length - 1) return t;
  return times[Math.round(logical)] as number;
}

export interface FibLevel {
  ratio: number;
  price: number;
  extension: boolean;
}

/**
 * Retracement levels between the first anchor (ratio 1) and the second
 * (ratio 0): `price(r) = p2 + (p1 - p2) * r`. Extensions continue past the
 * first anchor. Display geometry only.
 */
export function fibLevels(p1: number, p2: number, extensions: boolean): FibLevel[] {
  const span = p1 - p2;
  const out: FibLevel[] = FIB_RATIOS.map((ratio) => ({
    ratio,
    price: p2 + span * ratio,
    extension: false,
  }));
  if (extensions) {
    for (const ratio of FIB_EXTENSIONS) out.push({ ratio, price: p2 + span * ratio, extension: true });
  }
  return out;
}

export interface Measurement {
  priceDelta: number;
  /** Percent of the first point's price; null when that price is 0. */
  percent: number | null;
  /** Whole bars between the two points on this chart's axis (signed). */
  bars: number;
  seconds: number;
}

/** The measure tool's read-out: price, percent, bars and time between two points. */
export function measure(
  times: readonly number[],
  a: { t: number; price: number },
  b: { t: number; price: number },
): Measurement {
  const la = timeToLogical(times, a.t);
  const lb = timeToLogical(times, b.t);
  const priceDelta = b.price - a.price;
  return {
    priceDelta,
    percent: a.price === 0 ? null : (priceDelta / a.price) * 100,
    bars: la === null || lb === null ? 0 : Math.round(lb - la),
    seconds: b.t - a.t,
  };
}

/** "3d 4h", "45m", "2h": a duration for the measure label. */
export function formatSpan(seconds: number): string {
  const sign = seconds < 0 ? "−" : "";
  let s = Math.abs(Math.round(seconds));
  const d = Math.floor(s / 86_400);
  s -= d * 86_400;
  const h = Math.floor(s / 3_600);
  s -= h * 3_600;
  const m = Math.floor(s / 60);
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h) parts.push(`${h}h`);
  if (m && !d) parts.push(`${m}m`);
  return sign + (parts.length ? parts.join(" ") : "0m");
}

// ------------------------------------------------------------ hit testing

export interface Pt {
  x: number;
  y: number;
}

/** Distance from p to the segment ab (pixels). */
export function distToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  const k = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
  return Math.hypot(p.x - (a.x + k * dx), p.y - (a.y + k * dy));
}

/** The far end of a ray from a through b, clipped to a box of `width` × `height`. */
export function rayEnd(a: Pt, b: Pt, width: number, height: number): Pt {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0 && dy === 0) return b;
  let k = Number.POSITIVE_INFINITY;
  if (dx > 0) k = Math.min(k, (width - a.x) / dx);
  if (dx < 0) k = Math.min(k, -a.x / dx);
  if (dy > 0) k = Math.min(k, (height - a.y) / dy);
  if (dy < 0) k = Math.min(k, -a.y / dy);
  if (!Number.isFinite(k) || k < 1) k = 1;
  return { x: a.x + dx * k, y: a.y + dy * k };
}

/** A fresh drawing id: short, URL-safe, unique enough within one operator's set. */
export function newDrawingId(): string {
  const bytes = new Uint8Array(8);
  globalThis.crypto.getRandomValues(bytes);
  return `d_${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}
