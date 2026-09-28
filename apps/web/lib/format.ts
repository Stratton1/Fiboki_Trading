import type { Figure, Provenance } from "./types";

/**
 * The only place a Figure becomes text.
 *
 * A null value returns `null` from this function, so a caller must handle the
 * absent case explicitly. There is deliberately no `formatFigure(f) ?? "0"`
 * convenience; that shortcut IS the V1 bug.
 */
export function formatFigure(figure: Figure): string | null {
  if (figure.value === null) return null;
  return formatNumber(figure.value, figure.unit);
}

export function formatNumber(value: number, unit: string): string {
  switch (unit) {
    case "GBP":
      return new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency: "GBP",
        maximumFractionDigits: 2,
      }).format(value);
    case "pct":
      return `${value.toFixed(2)}%`;
    case "R":
      return `${value.toFixed(2)}R`;
    case "ratio":
      return value.toFixed(2);
    case "bps":
      return `${value.toFixed(0)} bps`;
    case "ms":
      return `${value.toFixed(0)} ms`;
    case "s":
      return `${value.toFixed(0)}s`;
    case "count":
      return new Intl.NumberFormat("en-GB").format(value);
    case "lots":
      return `${value.toFixed(2)} lots`;
    case "pips":
      return `${value.toFixed(1)} pips`;
    case "x":
      return `${value.toFixed(0)}x`;
    default:
      return Math.abs(value) >= 1000
        ? new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 }).format(value)
        : value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  }
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

export function signClass(value: number | null): string {
  if (value === null) return "";
  if (value > 0) return "pos";
  if (value < 0) return "neg";
  return "";
}
