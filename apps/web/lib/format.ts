import type { Figure, Provenance } from "./types";

/**
 * The only place a Figure becomes text.
 *
 * A null value returns `null` from this function, so a caller must handle the
 * absent case explicitly. There is deliberately no `formatFigure(f) ?? "0"`
 * convenience; that shortcut IS the V1 bug.
 */
export function formatFigure(
  figure: Figure,
  options: { signed?: boolean; decimals?: number | null; bare?: boolean } = {},
): string | null {
  if (figure.value === null) return null;
  const opts = { decimals: options.decimals, bare: options.bare };
  return options.signed
    ? formatSigned(figure.value, figure.unit, opts)
    : formatNumber(figure.value, figure.unit, opts);
}

/** U+2212, the typographic minus. A hyphen is the wrong width in tabular figures. */
export const MINUS = "−";

/**
 * The number rendering spec (report G §2.5).
 *
 *  - Fixed decimals per unit. The unit is the server's (`Figure.unit`), and so
 *    is any finer precision it can supply (an instrument's `pip_size`, passed
 *    in as `decimals`). A column of one unit therefore always lines up.
 *  - Thousands separators on every magnitude (en-GB grouping).
 *  - A real minus sign (U+2212), never a hyphen.
 *  - Sign AFTER rounding: a value that rounds to zero at its displayed
 *    precision renders with no sign and no direction. `-0.001%` is `0.00%`,
 *    not `−0.00%`, because the latter asserts a loss the display cannot show.
 *
 * Units the platform does not declare a precision for (the empty unit, a new
 * one) keep up to five decimals with trailing zeros removed unless the caller
 * supplies `decimals` (DataGrid supplies the column's widest, so it aligns).
 */
export const UNIT_DECIMALS: Readonly<Record<string, number>> = {
  GBP: 2,
  USD: 2,
  EUR: 2,
  JPY: 0,
  pct: 2,
  R: 2,
  ratio: 2,
  bps: 0,
  ms: 0,
  s: 0,
  count: 0,
  lots: 2,
  pips: 1,
  x: 0,
};

const CURRENCY = /^[A-Z]{3}$/;

/** The fixed decimals for a unit, or null when the platform declares none. */
export function unitDecimals(unit: string): number | null {
  const fixed = UNIT_DECIMALS[unit];
  if (fixed !== undefined) return fixed;
  if (CURRENCY.test(unit)) return 2;
  return null;
}

/**
 * Display decimals for a price, from the instrument's pip size as the API
 * supplies it: one digit finer than a pip (fractional pips), so EURUSD
 * (pip 0.0001) shows 5 and USDJPY (pip 0.01) shows 3. Null when unknown.
 */
export function priceDecimals(pipSize: number | null | undefined): number | null {
  if (pipSize === null || pipSize === undefined || !(pipSize > 0)) return null;
  return Math.min(8, Math.max(0, Math.round(-Math.log10(pipSize)) + 1));
}

/** The decimals actually used to render `value` in `unit`. */
export function displayDecimals(value: number, unit: string, decimals?: number | null): number {
  if (decimals !== null && decimals !== undefined) return decimals;
  const fixed = unitDecimals(unit);
  if (fixed !== null) return fixed;
  // Undeclared precision: up to five decimals, trailing zeros removed.
  const text = Math.abs(value).toFixed(5).replace(/0+$/, "");
  const dot = text.indexOf(".");
  return dot === -1 || dot === text.length - 1 ? 0 : text.length - dot - 1;
}

/**
 * The sign of a value AT ITS DISPLAYED PRECISION: 0 when it rounds to zero.
 * Direction colour, the ▲/▼ glyph and the explicit sign all read this, never
 * the raw value, so they cannot disagree with the digits on screen.
 */
export function roundedSign(value: number, unit: string, decimals?: number | null): -1 | 0 | 1 {
  const d = displayDecimals(value, unit, decimals);
  const rounded = Number(Math.abs(value).toFixed(d));
  if (rounded === 0 || Number.isNaN(rounded)) return 0;
  return value < 0 ? -1 : 1;
}

const grouping = new Map<number, Intl.NumberFormat>();
function grouped(decimals: number): Intl.NumberFormat {
  let nf = grouping.get(decimals);
  if (nf === undefined) {
    nf = new Intl.NumberFormat("en-GB", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
      useGrouping: true,
    });
    grouping.set(decimals, nf);
  }
  return nf;
}

const CURRENCY_SYMBOL: Record<string, string> = { GBP: "£", USD: "$", EUR: "€", JPY: "¥" };

/**
 * Unit prefix and suffix around the magnitude. `bare` drops both (grid cells:
 * the unit is in the column header).
 */
function affixes(unit: string, bare: boolean): [string, string] {
  if (bare) return ["", ""];
  switch (unit) {
    case "pct":
      return ["", "%"];
    case "R":
      return ["", "R"];
    case "bps":
      return ["", " bps"];
    case "ms":
      return ["", " ms"];
    case "s":
      return ["", "s"];
    case "lots":
      return ["", " lots"];
    case "pips":
      return ["", " pips"];
    case "x":
      return ["", "x"];
    default: {
      if (CURRENCY.test(unit)) {
        const symbol = CURRENCY_SYMBOL[unit];
        return symbol ? [symbol, ""] : ["", ` ${unit}`];
      }
      return ["", ""];
    }
  }
}

/**
 * A number in its unit, with a real minus sign for negatives. A value that
 * rounds to zero renders as zero: "−£0.00" would claim a loss the data does
 * not contain at this precision.
 */
export function formatNumber(
  value: number,
  unit: string,
  options: { decimals?: number | null; bare?: boolean } = {},
): string {
  const d = displayDecimals(value, unit, options.decimals);
  const [prefix, suffix] = affixes(unit, options.bare === true);
  const magnitude = grouped(d).format(Math.abs(value));
  const minus = roundedSign(value, unit, d) < 0 ? MINUS : "";
  return `${minus}${prefix}${magnitude}${suffix}`;
}

/**
 * A signed quantity such as P&L: an explicit "+" on gains, a real minus on
 * losses, and no sign on a value that is zero at its displayed precision
 * (an exact zero renders only when the API returned 0; null never reaches
 * here).
 */
export function formatSigned(
  value: number,
  unit: string,
  options: { decimals?: number | null; bare?: boolean } = {},
): string {
  const text = formatNumber(value, unit, options);
  return roundedSign(value, unit, options.decimals) > 0 ? `+${text}` : text;
}

export const PROVENANCE_LABEL: Record<Provenance, string> = {
  backtest: "BACKTEST",
  walkforward: "WALKFORWARD",
  out_of_sample: "OOS",
  holdout: "HOLDOUT",
  paper: "PAPER",
  shadow: "SHADOW",
  broker_demo: "DEMO",
  broker_live: "LIVE",
};

/** Chip text (report E §4.4). The long label stays in titles and popovers. */
export const PROVENANCE_SHORT: Record<Provenance, string> = {
  backtest: "BT",
  walkforward: "WF",
  out_of_sample: "OOS",
  holdout: "HOLD",
  paper: "PAPER",
  shadow: "SHADOW",
  broker_demo: "DEMO",
  broker_live: "LIVE",
};

/** Whether a provenance records an execution (filled chip) or a simulation (hollow). */
export const PROVENANCE_EXECUTED: Record<Provenance, boolean> = {
  backtest: false,
  walkforward: false,
  out_of_sample: false,
  holdout: false,
  paper: true,
  shadow: true,
  broker_demo: true,
  broker_live: true,
};

export const PROVENANCE_HELP: Record<Provenance, string> = {
  backtest:
    "Simulated over data the strategy was fitted on. The weakest evidence there is.",
  walkforward:
    "Simulated over rolling out-of-sample windows, refitted as it advanced.",
  out_of_sample: "Simulated over data held back from fitting.",
  holdout:
    "Simulated over the final reserved segment, which may only be looked at once.",
  paper: "Executed by the paper engine against recorded executable prices.",
  shadow: "Mirrored against a live venue's pricing with no order submitted.",
  broker_demo: "Executed at a broker demo account. Real path, no real money.",
  broker_live: "Executed at a real-money account.",
};

/**
 * Parse an API timestamp as UTC.
 *
 * Every timestamp the platform stores is UTC. An ISO string with no zone
 * designator would be read by `Date` as the BROWSER's local time, silently
 * shifting it by the operator's offset, so a missing designator is taken to
 * mean UTC rather than local.
 */
export function parseUtc(iso: string): Date {
  const zoned = /(Z|[+-]\d{2}:?\d{2})$/i.test(iso) || !iso.includes("T");
  return new Date(zoned ? iso : `${iso}Z`);
}

/**
 * A timestamp, always labelled with its zone.
 *
 * Rendered in UTC and suffixed "UTC", so an operator in London in summer (UTC+1)
 * does not read a 14:00 fill as 14:00 local. An unlabelled time on a trading
 * screen is an ambiguous time.
 */
export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const date = parseUtc(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const text = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date);
  return `${text} UTC`;
}

export function formatAge(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

/** The P&L colour class for a value, by its sign at the displayed precision. */
export function signClass(value: number | null, unit = "", decimals?: number | null): string {
  if (value === null) return "";
  const sign = roundedSign(value, unit, decimals);
  if (sign > 0) return "pos";
  if (sign < 0) return "neg";
  return "flat";
}

/** HH:MM:SS in UTC, labelled, for the status bar clock and as-of stamps. */
export function formatUtcTime(iso: string | number | Date): string {
  const date = typeof iso === "string" ? parseUtc(iso) : new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const text = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(date);
  return `${text} UTC`;
}
